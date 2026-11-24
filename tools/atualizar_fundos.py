# -*- coding: utf-8 -*-
"""Atualiza o Monitor de Fundos.

Lê o cadastro (data/fundos_cadastro.enc, editável pela página) e a base histórica
(<dir_base>/fundos_base.enc, guardada no ramo base-fundos), baixa os dados novos de
cada fonte, recalcula as métricas e grava:
  <dir_base>/fundos_base.enc    base histórica (cotas, captação, informações, proventos)
  <dir_base>/fundos.enc         resultado para a página (métricas, rankings, status das fontes)
  <dir_base>/fundos_series.enc  séries semanais de 36 meses para os gráficos

Modos:
  diario  (padrão) todas as fontes, últimos meses
  novos   só traz o histórico dos ativos cadastrados que ainda não têm dados, e recalcula
  recalc  só recalcula, sem baixar nada

Uso: python tools/atualizar_fundos.py data _base [--modo diario|novos|recalc] [--se-necessario]
Variáveis de ambiente: DATA_KEY.
"""
import os, sys, json, time, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cripto
import fundos as F

def limpo(o):
    """Troca NaN/inf por None (o JSON do navegador não aceita NaN)."""
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: limpo(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [limpo(v) for v in o]
    if hasattr(o, "item"):
        return limpo(o.item())
    return o

def main():
    sys.stdout.reconfigure(line_buffering=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else "data"
    pasta_base = args[1] if len(args) > 1 else "_base"
    modo = "diario"
    if "--modo" in sys.argv:
        modo = sys.argv[sys.argv.index("--modo") + 1]
        args = [a for a in args if a != modo]
    key = cripto.chave()
    os.makedirs(pasta_base, exist_ok=True)
    arq_cad = os.path.join(pasta, "fundos_cadastro.enc")
    arq_base = os.path.join(pasta_base, "fundos_base.enc")
    arq_out = os.path.join(pasta_base, "fundos.enc")
    arq_ser = os.path.join(pasta_base, "fundos_series.enc")

    ant = cripto.ler(arq_out, key, {}) if os.path.exists(arq_out) else {}
    agora = F.hoje_br()
    if "--se-necessario" in sys.argv and modo == "diario" and (ant.get("gerado_em") or "")[:10] == agora.strftime("%Y-%m-%d"):
        print("Fundos já atualizados hoje (%s); nada a fazer." % ant["gerado_em"])
        return

    cad_obj = cripto.ler(arq_cad, key)
    cad = cad_obj["cad"]
    antes = json.dumps(cad, sort_keys=True, ensure_ascii=False)
    novos = F.completar_cadastro(cad)
    if novos:
        print("Cadastro: %d ativo(s) classificados automaticamente" % novos)

    if os.path.exists(arq_base):
        base = F.Base.de_obj(cripto.ler(arq_base, key))
    else:
        raise SystemExit("Base histórica não encontrada em %s" % arq_base)
    print("Base: %d datas x %d séries (%s a %s)" % (len(base.cot.index), len(base.cot.columns),
          base.cot.index.min().date(), base.cot.index.max().date()))

    status = {s["fonte"]: s for s in ant.get("status", [])}
    def reg(fonte, f, *a, **k):
        t = time.time()
        try:
            msg, ult, n = f(*a, **k)
        except Exception as e:
            msg, ult, n = "Erro · %s" % str(e)[:160], None, 0
        print("%-16s %s (%.0fs)" % (fonte, msg, time.time() - t))
        if modo == "novos" and n == 0 and fonte in status:
            return
        status[fonte] = {"fonte": fonte, "msg": msg, "ult": ult.isoformat() if ult else None,
                         "quando": F.hoje_br().strftime("%Y-%m-%dT%H:%M")}

    so_novos = modo == "novos"
    if modo == "diario":   # ativos cadastrados que ainda não têm histórico (rede de segurança)
        for nome, f in (("CVM", F.atualizar_cvm), ("FIDC", F.atualizar_fidc)):
            try:
                msg, _, n = f(base, cad, 2, True) if nome == "CVM" else f(base, cad, True)
                if n: print("Histórico novo %s: %s" % (nome, msg))
            except Exception as e:
                print("Histórico novo %s: erro %s" % (nome, e))
    if modo in ("diario", "novos"):
        reg("CVM diário", F.atualizar_cvm, base, cad, 2, so_novos)
        reg("CVM FIDC", F.atualizar_fidc, base, cad, so_novos)
        reg("Yahoo", F.atualizar_yahoo, base, cad, so_novos)
        if modo == "diario" or novos:
            reg("CVM FII mensal", F.atualizar_fii_mensal, base, cad)
        if modo == "diario":
            reg("Banco Central", F.atualizar_bcb, base)
            reg("ANBIMA", F.atualizar_anbima, base)
            reg("B3", F.atualizar_b3, base)
            reg("Exterior", F.atualizar_exterior, base)
            n = F.limpar_outliers(base)
            if n:
                print("Outliers removidos: %d" % n)
        base.podar()

    res = F.calcular(base, cad, cad_obj["classes"], cad_obj["bmks"])
    res["gerado_em"] = F.hoje_br().strftime("%Y-%m-%dT%H:%M")
    res["status"] = list(status.values())
    res["modo"] = modo
    cap = dt.date.fromisoformat(res["cap_cvm"]) if res.get("cap_cvm") else None
    ser = F.series_resumo(base, cad, cap)
    print("Resultado: %d ativos · referência %s · %d séries" % (len(res["linhas"]), res["ref"], len(ser)))

    res = limpo(res); ser = limpo(ser)
    if modo != "recalc":
        cripto.gravar(arq_base, base.para_obj(), key)
    cripto.gravar(arq_out, res, key)
    cripto.gravar(arq_ser, ser, key)
    if json.dumps(cad, sort_keys=True, ensure_ascii=False) != antes:
        cad_obj["cad"] = cad
        cripto.gravar(arq_cad, cad_obj, key)
        print("Cadastro atualizado (classificação/subclasse)")

if __name__ == "__main__":
    main()
