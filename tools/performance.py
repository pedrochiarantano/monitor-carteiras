# -*- coding: utf-8 -*-
"""Monitor de Performance · Etti Partners (versão Python da aba Resultado da planilha Carteiras Recomendadas).

Lê o Histórico de Cotação (número-índice, base 100 em 30/12/2022) das carteiras recomendadas e dos
índices na API da Comdinheiro e calcula:
  - retorno por janela (mês, ano, ano anterior, 12m, 24m, desde o início e os 5 meses anteriores);
  - composição do retorno de cada perfil: CDI + Estratégico + Tático + Selection = Total
      Estratégico = carteira estratégica − CDI
      Tático      = carteira tática − carteira estratégica
      Selection   = carteira recomendada − carteira tática
  - volatilidade anualizada (desvio-padrão populacional dos retornos diários × √252),
    média móvel de 6 meses (126 dias úteis) e de 12 meses (252 dias úteis);
  - retorno x volatilidade desde o início, drawdown, Sharpe e retornos mensais.
"""
import calendar, datetime as dt, json, math, os, re, time, urllib.parse, urllib.request

URL = "https://api.comdinheiro.com.br/v1/ep1/import-data"
INICIO = dt.date(2022, 12, 30)

# (código na Comdinheiro, nome exibido, papel)
ATIVOS = [
    ("CarteiraConsOn", "Conservador", "perfil"),
    ("CarteiraModeOn", "Moderado", "perfil"),
    ("CarteiraArroOn", "Arrojado", "perfil"),
    ("CDI", "CDI", "indice"),
    ("IBOV", "Ibovespa", "indice"),
    ("IFIX", "IFIX", "indice"),
    ("ANBIMA_IRFM1%BE", "IRF-M 1+", "indice"),
    ("ANBIMA_IMAB", "IMA-B", "indice"),
    ("JGP_IDEXCDI", "JGP Idex CDI", "indice"),
    ("GOLD11", "GOLD11", "indice"),
    ("CarteiraEstratCons", "Estratégico Conservador", "estrat"),
    ("CarteiraTaticCons", "Tático Conservador", "tatic"),
    ("CarteiraEstratMode", "Estratégico Moderado", "estrat"),
    ("CarteiraTaticMode", "Tático Moderado", "tatic"),
    ("CarteiraEstratArro", "Estratégico Arrojado", "estrat"),
    ("CarteiraTaticArro", "Tático Arrojado", "tatic"),
    ("ANBIMA_IMAB5", "IMA-B 5", "indice"),
    ("ANBIMA_IMAB5%BE", "IMA-B 5+", "indice"),
    ("ANBIMA_IDADI", "IDA-DI", "indice"),
    ("ANBIMA_IDAIPCA", "IDA-IPCA", "indice"),
    ("ANBIMA_IDAIPCAINFRA", "IDA-IPCA Infra", "indice"),
    ("JGP_IDEXINFRA_GERAL", "JGP Idex Infra", "indice"),
    ("ANBIMA_IHFA", "IHFA", "indice"),
    ("BITH11", "BITH11", "indice"),
]
PERFIS = [("Conservador", "Estratégico Conservador", "Tático Conservador"),
          ("Moderado", "Estratégico Moderado", "Tático Moderado"),
          ("Arrojado", "Estratégico Arrojado", "Tático Arrojado")]
# ordem da tabela de índices (como na planilha)
INDICES = ["CDI", "JGP Idex CDI", "IDA-DI", "IDA-IPCA", "IDA-IPCA Infra", "JGP Idex Infra", "IRF-M 1+", "IMA-B",
           "IMA-B 5", "IMA-B 5+", "Ibovespa", "IFIX", "IHFA", "GOLD11", "BITH11"]
MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

PARAMS = [("data_ini", INICIO.strftime("%d%m%Y")), ("data_fim", "31129999"), ("pagina", "1"),
          ("d", "MOEDA_ORIGINAL"), ("g", "1"), ("m", "0"), ("info_desejada", "numero_indice"),
          ("retorno", "discreto"), ("tipo_data", "du_br"), ("tipo_ajuste", "todosajustes"), ("num_casas", "2"),
          ("enviar_email", "0"), ("ordem_legenda", "1"), ("cabecalho_excel", "modo1"),
          ("classes_ativos", "hio94mrrlk3j"), ("ordem_data", "0"), ("rent_acum", "rent_acum"),
          ("minY", ""), ("maxY", ""), ("deltaY", ""), ("preco_nd_ant", "0"), ("base_num_indice", "100"),
          ("flag_num_indice", "0"), ("eixo_x", "Data"), ("startX", "0"), ("max_list_size", "20"),
          ("line_width", "2"), ("titulo_grafico", ""), ("legenda_eixoy", ""), ("tipo_grafico", "line"),
          ("script", ""), ("tooltip", "unica")]

def consultar(usuario, senha, tentativas=3):
    url_interna = "HistoricoCotacao002.php?&x=" + "+".join(a[0] for a in ATIVOS)
    for k, v in PARAMS:
        url_interna += "&%s=%s" % (k, v)
    corpo = urllib.parse.urlencode([("username", usuario), ("password", senha), ("URL", url_interna), ("format", "json3")]).encode()
    err = None
    for t in range(tentativas):
        try:
            req = urllib.request.Request(URL, data=corpo, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            with urllib.request.urlopen(req, timeout=300) as r:
                txt = r.read().decode("utf-8", "replace")
            j = json.loads(txt)
            if "tables" not in j:
                raise RuntimeError("API respondeu: %s" % (j.get("msg") or txt[:200]))
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

def ler_series(j):
    """Devolve (datas, {nome: [valores ou None]}) até a última data em que as carteiras têm cota."""
    tabs = j["tables"]
    serie = None
    for k in sorted(tabs, key=lambda k: int(re.sub(r"\D", "", k) or 0)):
        t = tabs[k]
        linhas = [t[l] for l in sorted(t, key=lambda l: int(re.sub(r"\D", "", l) or 0))]
        linhas = [[ln["col%d" % i] for i in range(len(ln))] for ln in linhas]
        if linhas and str(linhas[0][0]).strip().lower() == "data":
            serie = linhas
    if not serie:
        raise RuntimeError("resposta sem a tabela de cotações")
    cab = [c.strip() for c in serie[0]]
    codigos = {a[0].replace("%BE", "+"): a[1] for a in ATIVOS}
    datas, cols = [], {a[1]: [] for a in ATIVOS}
    for ln in serie[1:]:
        try:
            d = dt.datetime.strptime(ln[0].strip(), "%d/%m/%Y").date()
        except ValueError:
            continue
        datas.append(d)
        for i, c in enumerate(cab[1:], 1):
            nome = codigos.get(c)
            if nome:
                cols[nome].append(_num(ln[i]))
    # corta as datas finais sem cota das carteiras (ex.: hoje, só com CDI)
    while datas and all(cols[p][-1] is None for p, _, _ in PERFIS):
        datas.pop()
        for v in cols.values():
            v.pop()
    # completa buracos com o valor anterior (preco_nd_ant=0 devolve "nd")
    for v in cols.values():
        ult = None
        for i, x in enumerate(v):
            if x is None:
                v[i] = ult
            else:
                ult = x
    return datas, cols

# ----------------------------------------------------------------------------- utilidades
def meses_atras(d, m):
    y, mo = d.year, d.month - m
    while mo <= 0:
        mo += 12; y -= 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))

def fim_mes(d):
    return dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])

class Serie:
    def __init__(self, datas, cols):
        self.datas = datas; self.cols = cols
        self.idx = {d: i for i, d in enumerate(datas)}

    def i_ate(self, d):
        """Índice da última data <= d."""
        lo, hi, r = 0, len(self.datas) - 1, -1
        while lo <= hi:
            m = (lo + hi) // 2
            if self.datas[m] <= d:
                r = m; lo = m + 1
            else:
                hi = m - 1
        return r

    def i_desde(self, d):
        """Índice da primeira data >= d."""
        i = self.i_ate(d)
        if i >= 0 and self.datas[i] == d:
            return i
        return i + 1 if i + 1 < len(self.datas) else -1

    def ret(self, nome, i0, i1):
        v = self.cols.get(nome)
        if not v or i0 < 0 or i1 < 0 or i1 <= i0 or v[i0] is None or v[i1] is None or v[i0] <= 0:
            return None
        return v[i1] / v[i0] - 1

    def retornos_diarios(self, nome, i0, i1):
        v = self.cols[nome]
        return [v[i] / v[i - 1] - 1 for i in range(max(i0, 0) + 1, i1 + 1) if v[i] and v[i - 1]]

def desvio_pop(x):
    if len(x) < 2:
        return None
    m = sum(x) / len(x)
    return math.sqrt(sum((a - m) ** 2 for a in x) / len(x))

def janelas(S, ref):
    """Datas inicial/final de cada janela (mesmas regras da planilha, sobre as datas da Comdinheiro)."""
    i1 = S.i_ate(ref)
    J = [("mes", "Mês", S.i_ate(dt.date(ref.year, ref.month, 1) - dt.timedelta(days=1)), i1),
         ("ano", "Ano", S.i_ate(dt.date(ref.year - 1, 12, 31)), i1),
         ("ano_ant", "%d" % (ref.year - 1), S.i_ate(dt.date(ref.year - 2, 12, 31)), S.i_ate(dt.date(ref.year - 1, 12, 31))),
         ("r12", "12 meses", S.i_desde(meses_atras(ref, 12)), i1),
         ("r24", "24 meses", S.i_desde(meses_atras(ref, 24)), i1),
         ("inicio", "Desde o início", S.idx.get(INICIO, 0), i1)]
    m = dt.date(ref.year, ref.month, 1)
    for k in range(1, 6):
        ini = meses_atras(m, k)
        fim = fim_mes(ini)
        J.append(("m%d" % k, "%s %s" % (MESES[ini.month - 1], str(ini.year)[2:]), S.i_ate(ini - dt.timedelta(days=1)), S.i_ate(fim)))
    return [{"k": k, "rotulo": r, "ini": S.datas[a].isoformat() if a >= 0 else None,
             "fim": S.datas[b].isoformat() if b >= 0 else None, "_i0": a, "_i1": b} for k, r, a, b in J]

def calcular(datas, cols, ref=None):
    S = Serie(datas, cols)
    ref = ref or datas[-1]
    i_ref = S.i_ate(ref)
    ref = datas[i_ref]
    J = janelas(S, ref)
    def linha(nome):
        return {j["k"]: S.ret(nome, j["_i0"], j["_i1"]) for j in J}
    def sub(a, b):
        return {k: (a[k] - b[k]) if a.get(k) is not None and b.get(k) is not None else None for k in a}

    cdi = linha("CDI")
    perfis = []
    for p, e, t in PERFIS:
        tot, est, tat = linha(p), linha(e), linha(t)
        perfis.append({"perfil": p, "total": tot, "cdi": cdi,
                       "estrategico": sub(est, cdi), "tatico": sub(tat, est), "selection": sub(tot, tat),
                       "ganho_cdi": sub(tot, cdi),
                       "estrat_bruto": est, "tatic_bruto": tat})
    indices = [{"nome": n, "ret": linha(n)} for n in INDICES if n in cols]

    # risco desde o início e retornos mensais
    i0 = S.idx.get(INICIO, 0)
    n_du = i_ref - i0
    risco = []
    for nome in [p for p, _, _ in PERFIS] + INDICES:
        if nome not in cols:
            continue
        r = S.retornos_diarios(nome, i0, i_ref)
        vol = desvio_pop(r) * math.sqrt(252) if len(r) > 20 else None
        tot = S.ret(nome, i0, i_ref)
        anual = (1 + tot) ** (252 / n_du) - 1 if tot is not None and n_du > 0 else None
        v = cols[nome][i0:i_ref + 1]
        pico, mdd, dd_atual = v[0], 0.0, 0.0
        for x in v:
            if x is None:
                continue
            pico = max(pico, x); dd = x / pico - 1; mdd = min(mdd, dd); dd_atual = dd
        risco.append({"nome": nome, "vol": vol, "ret_anual": anual, "ret_total": tot, "max_dd": mdd, "dd_atual": dd_atual})
    cdi_anual = next((x["ret_anual"] for x in risco if x["nome"] == "CDI"), None)
    for x in risco:
        if x["vol"] and x["ret_anual"] is not None and cdi_anual is not None and x["nome"] != "CDI":
            x["sharpe"] = (x["ret_anual"] - cdi_anual) / x["vol"]

    mensal = {}
    for nome in [p for p, _, _ in PERFIS] + ["CDI", "Ibovespa", "IMA-B", "IHFA"]:
        out = {}
        d = dt.date(INICIO.year + 1, 1, 1)
        while d <= ref:
            a = S.i_ate(d - dt.timedelta(days=1)); b = S.i_ate(min(fim_mes(d), ref))
            r = S.ret(nome, a, b)
            if r is not None:
                out["%d-%02d" % (d.year, d.month)] = r
            d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        anos = {}
        for y in range(INICIO.year + 1, ref.year + 1):
            a = S.i_ate(dt.date(y - 1, 12, 31)); b = S.i_ate(min(dt.date(y, 12, 31), ref))
            anos[str(y)] = S.ret(nome, a, b)
        mensal[nome] = {"meses": out, "anos": anos}
    for x in risco:
        if x["nome"] in mensal and x["nome"] != "CDI":
            ms = mensal[x["nome"]]["meses"]; mc = mensal["CDI"]["meses"]
            com = [k for k in ms if k in mc]
            if com:
                x["meses_acima_cdi"] = sum(1 for k in com if ms[k] > mc[k]) / len(com)
                x["melhor_mes"] = max(com, key=lambda k: ms[k]); x["melhor_mes_v"] = ms[x["melhor_mes"]]
                x["pior_mes"] = min(com, key=lambda k: ms[k]); x["pior_mes_v"] = ms[x["pior_mes"]]

    # volatilidade móvel (6m = 126 du; 12m = 252 du), como na planilha: janela de n+1 retornos
    vol_movel = {}
    for p, _, _ in PERFIS:
        v = cols[p]
        r = [None] + [v[i] / v[i - 1] - 1 if v[i] and v[i - 1] else None for i in range(1, len(v))]
        for n, chave in ((126, "6m"), (252, "12m")):
            s = []
            for i in range(i0, i_ref + 1):
                ini = i - n
                if ini < i0 + 1:
                    s.append(None); continue
                w = [x for x in r[ini:i + 1] if x is not None]
                s.append(round(desvio_pop(w) * math.sqrt(252), 6) if len(w) > n // 2 else None)
            vol_movel.setdefault(p, {})[chave] = s

    for j in J:
        j.pop("_i0"); j.pop("_i1")
    series = {n: [round(x, 6) if x is not None else None for x in cols[n][i0:i_ref + 1]] for n in cols}
    return {"ref": ref.isoformat(), "inicio": INICIO.isoformat(), "janelas": J, "perfis": perfis, "indices": indices,
            "risco": risco, "mensal": mensal, "dias_uteis": n_du,
            "datas": [d.isoformat() for d in datas[i0:i_ref + 1]], "series": series, "vol_movel": vol_movel}
