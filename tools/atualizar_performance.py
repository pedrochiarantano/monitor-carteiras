# -*- coding: utf-8 -*-
"""Atualiza o Monitor de Performance (carteiras recomendadas por perfil de risco).

Consulta o Histórico de Cotação na Comdinheiro (desde 30/12/2022), recalcula tudo e grava
<pasta>/performance.enc (cifrado). Não guarda histórico próprio: a Comdinheiro devolve a série
inteira a cada consulta.

Uso: python tools/atualizar_performance.py _base [--se-necessario]
Variáveis de ambiente: COMDINHEIRO_USER, COMDINHEIRO_PASS, DATA_KEY.
  --se-necessario  não consulta se o arquivo já tem a cota do último dia útil.
"""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cripto
import performance as P
from atualizar_monitor import dia_util_anterior

def limpo(o):
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: limpo(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [limpo(v) for v in o]
    return o

def main():
    sys.stdout.reconfigure(line_buffering=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else "_base"
    os.makedirs(pasta, exist_ok=True)
    arq = os.path.join(pasta, "performance.enc")
    key = cripto.chave()
    agora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    alvo = dia_util_anterior(agora.date())
    if "--se-necessario" in sys.argv and os.path.exists(arq):
        ant = cripto.ler(arq, key, {})
        if (ant.get("ref") or "") >= alvo.isoformat():
            print("Performance já tem a cota de %s; nada a fazer." % ant["ref"])
            return
    j = P.consultar(os.environ["COMDINHEIRO_USER"], os.environ["COMDINHEIRO_PASS"])
    datas, cols = P.ler_series(j)
    if len(datas) < 500:
        # diagnóstico sem dados sensíveis: tamanho da resposta e cobertura de cada série
        tabs = j.get("tables", {})
        for k, t in tabs.items():
            ls = [t[l] for l in sorted(t, key=lambda l: int(l[3:]))]
            print("tabela %s: %d linhas; primeira coluna de %r a %r" % (k, len(ls), ls[1].get("col0") if len(ls) > 1 else None, ls[-1].get("col0")))
            if len(ls) > 2 and str(ls[0].get("col0", "")).strip().lower() == "data":
                for i in range(1, len(ls[0])):
                    ok = sum(1 for x in ls[1:] if (x.get("col%d" % i) or "nd").strip() not in ("nd", "", "-"))
                    print("   %-22s %4d valores" % (ls[0]["col%d" % i], ok))
            elif len(ls) <= 4:
                for x in ls: print("   ", {kk: x[kk] for kk in list(x)[:6]})
        print("meta:", {k: v for k, v in (j.get("meta") or {}).items() if k not in ("username", "password")})
        raise SystemExit("Série curta demais (%d datas); arquivo anterior mantido." % len(datas))
    res = P.calcular(datas, cols)
    res["gerado_em"] = agora.strftime("%Y-%m-%dT%H:%M")
    res["fonte"] = "Comdinheiro · Histórico de Cotação (número-índice)"
    cripto.gravar(arq, limpo(res), key)
    print("Performance: %d datas, de %s a %s" % (len(res["datas"]), res["datas"][0], res["ref"]))
    for p in res["perfis"]:
        t = p["total"]
        print("  %-12s mês %+.2f%%  ano %+.2f%%  início %+.2f%%" % (p["perfil"], 100 * (t["mes"] or 0), 100 * (t["ano"] or 0), 100 * (t["inicio"] or 0)))

if __name__ == "__main__":
    main()
