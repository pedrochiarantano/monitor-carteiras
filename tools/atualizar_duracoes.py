# -*- coding: utf-8 -*-
"""Atualiza as durations estimadas dos fundos (data/duracoes.enc).

Para cada ativo de Pré Fixado ou Inflação sem duration da Comdinheiro, abre a
carteira do fundo na CVM (CDA, últimos 4 meses publicados) e calcula a duration
com as taxas ANBIMA do dia de análise. Para cada fundo usa o mês mais recente com
pelo menos 90% do patrimônio aberto (ou o de maior cobertura).

As estimativas anteriores são mantidas para ativos que não forem recalculados,
e as durations manuais (config.enc) nunca são alteradas.

Variáveis de ambiente: DATA_KEY. Uso: python tools/atualizar_duracoes.py data
"""
import os, sys, re, json, io, zipfile, subprocess, tempfile, urllib.request, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cripto

CVM = "https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/"
TICKERS = {"JURO11": "42.730.834/0001-00", "KDIF11": "26.324.298/0001-89", "BDIF11": "40.502.607/0001-94",
           "IFRA11": "34.633.510/0001-18", "CPTI11": "38.065.012/0001-77"}
SEM_DURATION = {"PICE11", "ENDD11"}   # FIPs
CNPJ = re.compile(r"^(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})")

def baixar(url, timeout=300):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()

def anbima(prefixo, d):
    for _ in range(7):
        url = "https://www.anbima.com.br/informacoes/%s/arqs/%s%s.txt" % (
            "merc-sec" if prefixo == "ms" else "merc-sec-debentures", prefixo, d.strftime("%y%m%d"))
        try:
            b = baixar(url, 60)
            if b.count(b"@") > 50:
                return b, d
        except Exception:
            pass
        d -= dt.timedelta(days=1)
    raise SystemExit("Arquivo ANBIMA %s indisponível" % prefixo)

def main():
    pasta = sys.argv[1]
    key = cripto.chave()
    pos = cripto.ler(os.path.join(pasta, "posicoes.enc"), key)
    meta = cripto.ler(os.path.join(pasta, "meta.enc"), key)
    cfg = cripto.ler(os.path.join(pasta, "config.enc"), key)
    antigas = cripto.ler(os.path.join(pasta, "duracoes.enc"), key, []) or []
    on = cfg["onshore"]; clsmap = on["clsmap"]; ac = on.get("assetClass", {})
    def classe(p):
        return ac.get(p["a"]) or clsmap.get((p.get("mc") or "nd").lower()) or p.get("mc")
    alvo = {}
    for p in pos:
        if p.get("off") or p.get("du") is not None or classe(p) not in ("Pré Fixado", "Inflação"):
            continue
        a = p["a"]
        if a in SEM_DURATION:
            continue
        m = CNPJ.match(p.get("al") or "") or CNPJ.match(a)
        if m: alvo[a] = m.group(1)
        elif a in TICKERS: alvo[a] = TICKERS[a]
    print("%d ativos sem duration da Comdinheiro" % len(alvo))
    ref = dt.date.fromisoformat(meta["data_analise"])
    tmp = tempfile.mkdtemp()
    ms, d_ms = anbima("ms", ref); db, _ = anbima("db", ref)
    open(os.path.join(tmp, "ms.txt"), "wb").write(ms); open(os.path.join(tmp, "db.txt"), "wb").write(db)
    json.dump(alvo, open(os.path.join(tmp, "alvo.json"), "w"), ensure_ascii=False)
    lista = baixar(CVM, 60).decode("latin1")
    meses = sorted(set(re.findall(r"cda_fi_(\d{6})\.zip", lista)))[-4:][::-1]
    res = {}
    for m in meses:
        dm = os.path.join(tmp, m); os.makedirs(dm)
        zipfile.ZipFile(io.BytesIO(baixar(CVM + "cda_fi_%s.zip" % m, 600))).extractall(dm)
        out = os.path.join(tmp, "out_%s.json" % m)
        subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "duracao_fundos.py"), dm,
                        os.path.join(tmp, "ms.txt"), os.path.join(tmp, "db.txt"), d_ms.isoformat(),
                        os.path.join(tmp, "alvo.json"), out], check=True, stdout=subprocess.DEVNULL)
        res[m] = json.load(open(out))
        for f in os.listdir(dm): os.remove(os.path.join(dm, f))
    novas = {}
    for a, c in alvo.items():
        esc = None
        for m in meses:
            r = res[m].get(a)
            if r and r["du"] is not None and r["cobertura"] >= 0.9: esc = (m, r); break
        if not esc:
            cands = [(res[m][a]["cobertura"], m) for m in meses if res[m].get(a) and res[m][a]["du"] is not None and res[m][a]["cobertura"] > 0.15]
            if cands:
                _, m = max(cands); esc = (m, res[m][a])
        if not esc:
            continue
        m, r = esc
        conf = "alta" if r["cobertura"] >= 0.9 else "média" if r["cobertura"] >= 0.5 else "baixa"
        novas[a] = {"a": a, "cnpj": c, "du": r["du"], "conf": conf, "base": "%s-%s" % (m[:4], m[4:]), "cob": round(r["cobertura"], 3),
                    "metodo": "Carteira aberta na CVM (CDA %s/%s), %d%% do patrimônio calculado" % (m[4:], m[:4], round(r["cobertura"] * 100))}
    final = {x["a"]: x for x in antigas}
    final.update(novas)
    cripto.gravar(os.path.join(pasta, "duracoes.enc"), list(final.values()), key)
    print("Durations: %d recalculadas, %d mantidas de antes (meses CDA: %s; ANBIMA %s)" % (len(novas), len(final) - len(novas), ", ".join(meses), d_ms))

if __name__ == "__main__":
    main()
