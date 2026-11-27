# -*- coding: utf-8 -*-
"""Rentabilidade das carteiras dos clientes desde o início da gestão.

Consulta na Comdinheiro o Histórico de Cotação das cotas de cada carteira (Ext_<carteira>_Cota,
número-índice) e do CDI, até a cota mais recente (D-1); o mês corrente aparece como parcial. Para cada carteira calcula, a partir da data de início da gestão:
  - retorno de cada mês e de cada ano, benchmark do período e benchmark+ (retorno − benchmark);
  - retorno acumulado diário (para o gráfico), contra o benchmark.
Benchmark: CDI nas carteiras em reais; T-Bill nas offshore (US:BIL, ETF SPDR Bloomberg 1-3 Month
T-Bill, com proventos reinvestidos).

Entradas: data/cotas_carteiras.enc {"carteiras": [...], "inicio": {carteira: [datas]}}
          (usa a data de início mais antiga; sem data, usa o início da cota na Comdinheiro)
          data/posicoes.enc: carteiras novas nas posições entram sozinhas na consulta
Saída:    <pasta>/cotas.enc

Uso: python tools/atualizar_cotas.py data _base [--se-necessario]
Variáveis de ambiente: COMDINHEIRO_COTAS_USER e COMDINHEIRO_COTAS_PASS (se ausentes,
COMDINHEIRO_USER e COMDINHEIRO_PASS), DATA_KEY.
"""
import os, sys, json, re, time, datetime as dt, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cripto

URL = "https://api.comdinheiro.com.br/v1/ep1/import-data"
DATA_INI = dt.date(2022, 12, 30)
BENCH = {"CDI": ("CDI", "CDI"), "TBILL": ("US:BIL", "T-Bill")}   # chave -> (código na Comdinheiro, nome exibido)
TBILL_DESC = "ETF SPDR Bloomberg 1-3 Month T-Bill, com proventos"

def bench_de(carteira, moeda):
    return "TBILL" if ("offshore" in carteira.lower() or (moeda or "").upper() == "USD") else "CDI"
PARAMS = ("&data_ini=%s&data_fim=dmenos1&pagina=1&d=MOEDA_ORIGINAL&g=1&m=0&info_desejada=numero_indice"
          "&retorno=discreto&tipo_data=du_br&tipo_ajuste=todosajustes&num_casas=2&enviar_email=0&ordem_legenda=1"
          "&cabecalho_excel=modo1&classes_ativos=1qae8ggyq4ngn3&ordem_data=0&rent_acum=rent_acum&minY=&maxY=&deltaY="
          "&preco_nd_ant=0&base_num_indice=100&flag_num_indice=0&eixo_x=Data&startX=0&max_list_size=20&line_width=2"
          "&titulo_grafico=&legenda_eixoy=&tipo_grafico=line&tooltip=unica&pageviews=1")

def consultar(usuario, senha, carteiras, tentativas=3):
    x = "+".join(["Ext_%s_Cota" % c for c in carteiras] + [b[0] for b in BENCH.values()])
    url_interna = "HistoricoCotacao002.php?&x=" + x + PARAMS % DATA_INI.strftime("%d/%m/%Y")
    corpo = urllib.parse.urlencode([("username", usuario), ("password", senha), ("URL", url_interna), ("format", "json3")]).encode()
    err = None
    for t in range(tentativas):
        try:
            req = urllib.request.Request(URL, data=corpo, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            with urllib.request.urlopen(req, timeout=300) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            if "tables" not in j:
                raise RuntimeError("API respondeu: %s" % (j.get("msg") or str(j)[:200]))
            return j
        except Exception as e:
            err = e
            time.sleep(10 * (t + 1))
    raise RuntimeError("Comdinheiro: %s" % err)

def _num(s):
    s = (s or "").strip()
    if s.lower() in ("", "nd", "-"):
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None

def _data(s):
    try:
        return dt.datetime.strptime((s or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None

def _matriz(t):
    ls = [t[l] for l in sorted(t, key=lambda l: int(re.sub(r"\D", "", l) or 0))]
    return [[x["col%d" % i] for i in range(len(x))] for x in ls]

def ler(j):
    tabs = j["tables"]
    info, serie = None, None
    for k in tabs:
        m = _matriz(tabs[k])
        if m and str(m[0][0]).strip().lower() == "data":
            serie = m
        else:
            info = m
    cab = [c.strip() for c in serie[0]]
    datas = [_data(r[0]) for r in serie[1:]]
    cols = {c: [_num(r[i]) for r in serie[1:]] for i, c in enumerate(cab) if i}
    meta = {}
    if info:
        nomes = info[0]
        linhas = {str(r[0]).strip().lower(): r for r in info[1:]}
        antiga = next((r for k, r in linhas.items() if "antiga" in k), None)
        recente = next((r for k, r in linhas.items() if "recente" in k), None)
        moeda = next((r for k, r in linhas.items() if "moeda" in k), None)
        for i, n in enumerate(nomes):
            if i:
                meta[n.strip()] = {"antiga": _data(antiga[i]) if antiga else None, "recente": _data(recente[i]) if recente else None,
                                   "moeda": (moeda[i] or "").strip() if moeda else ""}
    return datas, cols, meta

def fim_mes(d):
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)

def calcular(datas, cols, meta, carteiras, inicios):
    ultima = datas[-1]
    out = {}
    for c in carteiras:
        k = "Ext_%s_Cota" % c
        v, mt = cols.get(k), meta.get(k, {})
        reg = {"inicio_planilha": inicios.get(c, []), "moeda": mt.get("moeda") or ""}
        bk = bench_de(c, reg["moeda"]); cdi = cols.get(BENCH[bk][0])
        reg["bench"] = BENCH[bk][1]
        if bk == "TBILL": reg["bench_desc"] = TBILL_DESC
        if not v or not mt.get("recente"):
            reg["erro"] = "sem cotas na Comdinheiro"
            out[c] = reg; continue
        if inicios.get(c):
            ini = dt.date.fromisoformat(min(inicios[c])); reg["inicio_fonte"] = "planilha"
        else:
            ini = max(mt.get("antiga") or DATA_INI, DATA_INI); reg["inicio_fonte"] = "comdinheiro"
        fim = min(mt["recente"], ultima)
        # índice da base: última data <= início com cota; fim: última data <= fim com cota
        i0 = max([i for i, d in enumerate(datas) if d <= ini and v[i] is not None] or [-1])
        if i0 < 0:
            i0 = next((i for i, d in enumerate(datas) if d >= ini and v[i] is not None), -1)
        i1 = max([i for i, d in enumerate(datas) if d <= fim and v[i] is not None] or [-1])
        if i0 < 0 or i1 <= i0:
            msg = ("a cota na Comdinheiro vai até %s, antes do início da gestão (%s); aparece assim que a Comdinheiro calcular as cotas desde o início" % (fim.strftime("%d/%m/%Y"), ini.strftime("%d/%m/%Y"))
                   if ini >= fim else "cota sem histórico depois do início")
            reg.update({"inicio": ini.isoformat(), "fim": fim.isoformat(), "erro": msg})
            out[c] = reg; continue
        reg["inicio"] = datas[i0].isoformat(); reg["fim"] = datas[i1].isoformat()
        # série diária (completa os buracos com a cota anterior)
        def ult_ok(serie, j):
            while j >= 0 and serie[j] is None:
                j -= 1
            return serie[j] if j >= 0 else None
        b0 = ult_ok(cdi, i0) if cdi else None
        xs, ys, cs, ult_v, ult_c = [], [], [], v[i0], b0
        for i in range(i0, i1 + 1):
            if v[i] is not None: ult_v = v[i]
            if cdi and cdi[i] is not None: ult_c = cdi[i]
            xs.append((datas[i] - datas[i0]).days)
            ys.append(round(ult_v / v[i0] - 1, 6))
            cs.append(round(ult_c / b0 - 1, 6) if b0 and ult_c else None)
        reg["d0"] = datas[i0].isoformat(); reg["x"] = xs; reg["ret"] = ys; reg["bmk"] = cs
        # meses: base na última cota <= fim do mês anterior (ou na data de início, no 1º mês)
        def valor_ate(serie, d, piso):
            j = max([i for i in range(piso, i1 + 1) if datas[i] <= d and serie[i] is not None] or [piso])
            return serie[j], j
        meses, cdim, parciais = {}, {}, []
        m = dt.date(datas[i0].year, datas[i0].month, 1)
        while m <= datas[i1]:
            a_d = max(m - dt.timedelta(days=1), datas[i0])
            b_d = min(fim_mes(m), datas[i1])
            va, ja = valor_ate(v, a_d, i0); vb, jb = valor_ate(v, b_d, i0)
            if jb > ja and va:
                chave = "%d-%02d" % (m.year, m.month)
                meses[chave] = vb / va - 1
                ca, cb = (ult_ok(cdi, ja), ult_ok(cdi, jb)) if cdi else (None, None)
                if ca and cb:
                    cdim[chave] = cb / ca - 1
                if fim_mes(m) > datas[i1] and b_d < fim_mes(m) - dt.timedelta(days=3):
                    parciais.append(chave)
                if m.year == datas[i0].year and m.month == datas[i0].month and datas[i0] > m and chave not in parciais:
                    parciais.append(chave)
            m = fim_mes(m) + dt.timedelta(days=1)
        reg["meses"] = meses; reg["bmk_meses"] = cdim; reg["parciais"] = parciais
        anos, anos_cdi = {}, {}
        for y in sorted({int(k[:4]) for k in meses}):
            r = c_ = 1.0; ok_c = True
            for k in sorted(meses):
                if k.startswith(str(y)):
                    r *= 1 + meses[k]
                    if k in cdim: c_ *= 1 + cdim[k]
                    else: ok_c = False
            anos[str(y)] = r - 1
            if ok_c: anos_cdi[str(y)] = c_ - 1
        reg["anos"] = anos; reg["bmk_anos"] = anos_cdi
        reg["total"] = ys[-1]; reg["total_bmk"] = cs[-1]
        out[c] = reg
    return out

def main():
    sys.stdout.reconfigure(line_buffering=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else "data"
    saida = args[1] if len(args) > 1 else "_base"
    key = cripto.chave()
    arq = os.path.join(saida, "cotas.enc")
    agora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    if "--se-necessario" in sys.argv and os.path.exists(arq):
        ant = cripto.ler(arq, key, {})
        if (ant.get("gerado_em") or "")[:10] == agora.strftime("%Y-%m-%d"):
            print("Cotas das carteiras já atualizadas hoje (%s); nada a fazer." % ant["gerado_em"])
            return
    cfg = cripto.ler(os.path.join(pasta, "cotas_carteiras.enc"), key)
    # carteiras que já aparecem nas posições também entram (exceto as de teste)
    pos = cripto.ler(os.path.join(pasta, "posicoes.enc"), key, [])
    novas = sorted({p["c"] for p in pos if p.get("c") and not p["c"].startswith("carteira_teste")} - set(cfg["carteiras"]))
    if novas:
        print("Carteiras novas (das posições): " + ", ".join(novas))
        cfg["carteiras"] = cfg["carteiras"] + novas
    usuario = os.environ.get("COMDINHEIRO_COTAS_USER") or os.environ["COMDINHEIRO_USER"]
    senha = os.environ.get("COMDINHEIRO_COTAS_PASS") or os.environ["COMDINHEIRO_PASS"]
    j = consultar(usuario, senha, cfg["carteiras"])
    datas, cols, meta = ler(j)
    com_cota = sum(1 for c in cfg["carteiras"] if (meta.get("Ext_%s_Cota" % c) or {}).get("recente"))
    if com_cota < len(cfg["carteiras"]) // 2:
        raise SystemExit("Só %d de %d carteiras com cota: a conta da Comdinheiro usada não enxerga as cotas dos clientes "
                         "(Ext_..._Cota). Cadastre no GitHub os segredos COMDINHEIRO_COTAS_USER e COMDINHEIRO_COTAS_PASS "
                         "com o login dono dessas cotas. Arquivo anterior mantido." % (com_cota, len(cfg["carteiras"])))
    res = {"ref": datas[-1].isoformat(), "gerado_em": agora.strftime("%Y-%m-%dT%H:%M"),
           "fonte": "Comdinheiro · cotas das carteiras",
           "carteiras": calcular(datas, cols, meta, cfg["carteiras"], cfg.get("inicio", {}))}
    os.makedirs(saida, exist_ok=True)
    cripto.gravar(arq, res, key)
    ok = [c for c, r in res["carteiras"].items() if not r.get("erro")]
    print("Cotas das carteiras até %s: %d com rentabilidade, %d sem (%s)" % (res["ref"], len(ok), len(res["carteiras"]) - len(ok),
          ", ".join("%s: %s" % (c, r["erro"]) for c, r in res["carteiras"].items() if r.get("erro"))))

if __name__ == "__main__":
    main()
