# -*- coding: utf-8 -*-
"""Atualiza os dados do Monitor de Carteiras · Etti Partners.

Puxa o Relatório Gerencial de Carteiras (Comdinheiro) do último dia útil
e a PTAX de venda (Banco Central), e grava em data/ (criptografado):
  posicoes.enc   posições
  meta.enc       data de análise, PTAX, horário
  historico.enc  AUM por carteira por data (acumulado; o anterior é lido e mantido)

Não toca em config.enc (IPS, modelo, classificações, durations manuais) nem em
duracoes.enc (durations estimadas): essas informações sobrevivem a cada atualização.

Variáveis de ambiente: COMDINHEIRO_USER, COMDINHEIRO_PASS, DATA_KEY.
Uso: python tools/atualizar_monitor.py data [--data dd/mm/aaaa] [--raw resposta.json]
"""
import os, sys, json, re, datetime as dt, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cripto

API = "https://api.comdinheiro.com.br/v1/ep1/import-data"
VARS = ("nome_portfolio+ativo+desc+cv_alias+tipo_ativo+ticker_cmd+saldo_bruto+quant+mv(mvclasse)"
        "+liqdz_vcmto+instituicao_financeira+data_analise+data_mais_recente_ativo+data_vencimento"
        "+duration(a,a,u)+data_aplicacao+prazo_liquidez+saldo_liquido")

# Feriados nacionais (calendário ANBIMA) 2026-2027
FERIADOS = {
 "2026-01-01","2026-02-16","2026-02-17","2026-04-03","2026-04-21","2026-05-01","2026-06-04",
 "2026-09-07","2026-10-12","2026-11-02","2026-11-15","2026-11-20","2026-12-25",
 "2027-01-01","2027-02-08","2027-02-09","2027-03-26","2027-04-21","2027-05-01","2027-05-27",
 "2027-09-07","2027-10-12","2027-11-02","2027-11-15","2027-11-20","2027-12-25",
}

def du(d):
    return d.weekday() < 5 and d.isoformat() not in FERIADOS

def dia_util_anterior(d):
    d -= dt.timedelta(days=1)
    while not du(d):
        d -= dt.timedelta(days=1)
    return d

def num(s):
    if s is None: return None
    s = str(s).strip()
    if s in ("", "nd", "-"): return None
    try: return float(s.replace(".", "").replace(",", "."))
    except ValueError: return None

def iso(s):
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", (s or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None

def consultar(data_br):
    if "--raw" in sys.argv:
        raw = open(sys.argv[sys.argv.index("--raw") + 1], encoding="utf-8").read()
        tab = json.loads(raw)["tables"]["tab0"]
        ks = sorted([k for k in tab if k.startswith("lin")], key=lambda k: int(k[3:]))
        return [[tab[k][c] for c in sorted(tab[k], key=lambda c: int(c[3:]))] for k in ks]
    inner = ("RelatorioGerencialCarteiras001.php?&data_analise=" + data_br +
             "&data_ini=&nome_portfolio=&variaveis=" + VARS +
             "&filtro=todos&ativo=&filtro_IF=todos&relat_alias=&layout=0&layoutB=0"
             "&num_casas=&enviar_email=0&portfolio_editavel=&filtro_id=&orderBy=")
    body = urllib.parse.urlencode({
        "username": os.environ["COMDINHEIRO_USER"],
        "password": os.environ["COMDINHEIRO_PASS"],
        "URL": inner, "format": "json3"})
    req = urllib.request.Request(API, data=body.encode(), method="POST",
          headers={"Content-Type": "application/x-www-form-urlencoded"})
    raw = urllib.request.urlopen(req, timeout=300).read().decode("utf-8", "replace")
    tab = json.loads(raw)["tables"]["tab0"]
    ks = sorted([k for k in tab if k.startswith("lin")], key=lambda k: int(k[3:]))
    return [[tab[k][c] for c in sorted(tab[k], key=lambda c: int(c[3:]))] for k in ks]

def ptax(d):
    for _ in range(7):
        ds = d.strftime("%m-%d-%Y")
        url = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
               "CotacaoDolarDia(dataCotacao=@dataCotacao)?@dataCotacao='" + ds + "'&$format=json")
        try:
            v = json.loads(urllib.request.urlopen(url, timeout=30).read())["value"]
            if v: return v[-1]["cotacaoVenda"], d.isoformat()
        except Exception:
            pass
        d -= dt.timedelta(days=1)
    return None, None

def main():
    saida = sys.argv[1]
    key = cripto.chave()
    hoje = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3)
    if "--data" in sys.argv:
        alvo = dt.datetime.strptime(sys.argv[sys.argv.index("--data") + 1], "%d/%m/%Y").date()
    else:
        alvo = dia_util_anterior(hoje.date())
    for tentativa in range(3):
        linhas = consultar(alvo.strftime("%d/%m/%Y"))
        hdr, rows = linhas[0], linhas[1:]
        sem_saldo = sum(1 for r in rows if num(r[6]) is None)
        if rows and sem_saldo / len(rows) < 0.3:
            break
        print(f"{alvo}: {sem_saldo}/{len(rows)} sem saldo, recuando um dia útil")
        alvo = dia_util_anterior(alvo)
    else:
        raise SystemExit("Sem dados válidos nos últimos dias úteis")
    if "--data" in sys.argv and "--raw" in sys.argv:
        pass

    pos = []
    for r in rows:
        sb = num(r[6])
        if sb is None: continue
        pos.append({
            "c": r[0], "a": r[1], "ds": r[2].strip(), "al": (r[3] or "").strip()[:18], "t": r[4], "tk": r[5], "sb": round(sb, 2),
            "sl": round(num(r[17]) or 0, 2), "q": num(r[7]), "mc": r[8], "lq": r[9], "if": r[10],
            "dr": iso(r[12]), "vc": iso(r[13]), "du": num(r[14]), "ap": iso(r[15]),
            "pl": (int(num(r[16])) if num(r[16]) is not None else None),
            "off": r[0].endswith("_offshore")})
    px, px_d = ptax(alvo)
    meta = {"data_analise": alvo.isoformat(), "ptax": px, "ptax_data": px_d,
            "gerado_em": hoje.strftime("%Y-%m-%dT%H:%M"), "posicoes": len(pos),
            "carteiras": len({p["c"] for p in pos}),
            "fonte": "Comdinheiro (Relatório Gerencial de Carteiras) e Banco Central (PTAX venda)"}

    hist = [h for h in (cripto.ler(os.path.join(saida, "historico.enc"), key, []) or []) if h["d"] != meta["data_analise"]]
    aum = {}
    for p in pos:
        aum[p["c"]] = aum.get(p["c"], 0) + p["sb"]
    for c, v in sorted(aum.items()):
        hist.append({"d": meta["data_analise"], "c": c, "v": round(v, 2)})
    hist.sort(key=lambda h: (h["d"], h["c"]))

    if len(pos) < 500:
        raise SystemExit("Poucas posições (%d): nada foi gravado" % len(pos))
    os.makedirs(saida, exist_ok=True)
    cripto.gravar(os.path.join(saida, "posicoes.enc"), pos, key)
    cripto.gravar(os.path.join(saida, "historico.enc"), hist, key)
    cripto.gravar(os.path.join(saida, "meta.enc"), meta, key)
    print("Data de análise %s · %d posições · %d carteiras · PTAX %s" % (meta["data_analise"], meta["posicoes"], meta["carteiras"], meta["ptax"]))

if __name__ == "__main__":
    main()
