# -*- coding: utf-8 -*-
"""Monitor de Fundos · Etti Partners (versão Python da planilha Monitor_Fundos_Etti.xlsm).

Base histórica (cotas/índices, captação, informações, proventos) + atualizadores por fonte
+ cálculo das métricas por ativo, ranking e quartil dentro da classe.

Fontes: CVM (Informe Diário, Informe Mensal FIDC e FII), Yahoo (FIIs, ETFs, FI-Infra e
ativos externos), Banco Central (CDI e PTAX), ANBIMA (IMA), B3 (IBOV, IFIX, SMLL, IDIV).
"""
import base64, calendar, csv, datetime as dt, io, json, math, os, re, time, unicodedata, urllib.parse, urllib.request, zipfile
import numpy as np
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
URL_INF = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_"
URL_FIDC = "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/inf_mensal_fidc_"
URL_FIDC_HIST = "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/HIST/inf_mensal_fidc_"
URL_FII = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_"
URL_YH = "https://query1.finance.yahoo.com/v8/finance/chart/"
URL_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs."
URL_ANB = "https://www.anbima.com.br/informacoes/ima/ima-sh-down.asp"
URL_B3 = "https://sistemaswebb3-listados.b3.com.br/indexStatisticsProxy/IndexCall/GetPortfolioDay/"

def hoje_br():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3))

def http(url, data=None, timeout=120, tentativas=3, headers=None):
    h = dict(UA); h.update(headers or {})
    err = None
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            err = e
            time.sleep(2 + 3 * t)
        except Exception as e:
            err = e
            time.sleep(2 + 3 * t)
    raise RuntimeError("%s: %s" % (url[:90], err))

def digits(s):
    return re.sub(r"\D", "", str(s or ""))

def fmt_cnpj(s):
    d = digits(s).zfill(14)
    return "%s.%s.%s/%s-%s" % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])

def num(s):
    if isinstance(s, (int, float)):
        return None if s != s else float(s)
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        try:
            return float(s.replace(".", "").replace(",", "."))
        except ValueError:
            return None

def meses_atras(d, m):
    y = d.year; mo = d.month - m
    while mo <= 0:
        mo += 12; y -= 1
    while mo > 12:
        mo -= 12; y += 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))

def fim_mes_anterior(d):
    return d.replace(day=1) - dt.timedelta(days=1)

# =============================================================================== base
class Base:
    """cot: DataFrame (datas x chaves); fl: DataFrame (1º dia do mês x chaves, R$);
    info: {chave: {...}}; prov: {ticker|AAAA-MM-DD: valor}."""
    def __init__(self, cot, fl, info, prov):
        self.cot = cot.sort_index(); self.fl = fl.sort_index(); self.info = info; self.prov = prov

    # ---------------- séries
    def serie(self, k, cap=None):
        if k not in self.cot.columns:
            return np.array([], dtype="datetime64[D]"), np.array([])
        s = self.cot[k].dropna()
        s = s[s > 0]
        if cap is not None:
            s = s[s.index <= pd.Timestamp(cap)]
        return s.index.values.astype("datetime64[D]"), s.values.astype(float)

    def ultima_data(self, k):
        if k not in self.cot.columns:
            return None
        s = self.cot[k].dropna()
        return s.index[-1].date() if len(s) else None

    def _garantir(self, k, datas):
        novas = pd.DatetimeIndex(datas).difference(self.cot.index)
        if len(novas):
            self.cot = pd.concat([self.cot, pd.DataFrame(index=novas, columns=self.cot.columns, dtype=float)]).sort_index()
        if k not in self.cot.columns:
            self.cot[k] = np.nan

    def aplicar(self, k, datas, vals, escala=True):
        """Grava a série; com escala, ajusta o nível para emendar no valor já existente."""
        if not len(datas):
            return
        idx = pd.DatetimeIndex(pd.to_datetime(list(datas)))
        v = np.asarray(vals, dtype=float)
        self._garantir(k, idx)
        f = 1.0
        if escala:
            atual = self.cot[k]
            for d, x in zip(idx, v):
                y = atual.get(d)
                if y is not None and not pd.isna(y) and y > 0 and x > 0:
                    f = y / x; break
        ok = v > 0
        self.cot.loc[idx[ok], k] = np.round(v[ok] * f, 8)

    def valor_antes(self, k, d, inclusivo=False):
        if k not in self.cot.columns:
            return None, None
        s = self.cot[k].dropna()
        s = s[s.index <= pd.Timestamp(d)] if inclusivo else s[s.index < pd.Timestamp(d)]
        if not len(s):
            return None, None
        return float(s.iloc[-1]), s.index[-1].date()

    def set_fluxo(self, k, mes, v):
        m = pd.Timestamp(mes.replace(day=1))
        if m not in self.fl.index:
            self.fl = pd.concat([self.fl, pd.DataFrame(index=[m], columns=self.fl.columns, dtype=float)]).sort_index()
        if k not in self.fl.columns:
            self.fl[k] = np.nan
        self.fl.loc[m, k] = v

    def fluxo_soma(self, k, d1, d2):
        if k not in self.fl.columns:
            return None
        s = self.fl[k]
        s = s[(s.index >= pd.Timestamp(d1)) & (s.index <= pd.Timestamp(d2))].dropna()
        return float(s.sum()) if len(s) else None

    def podar(self):
        h = hoje_br().date()
        lim = pd.Timestamp(meses_atras(h.replace(day=1), 63))
        self.cot = self.cot[self.cot.index >= lim]
        self.cot = self.cot.dropna(how="all")
        self.fl = self.fl[self.fl.index >= lim]
        lp = meses_atras(h.replace(day=1), 24).isoformat()
        self.prov = {k: v for k, v in self.prov.items() if k.split("|")[1] >= lp}

    # ---------------- persistência (JSON gzip, dentro do cripto)
    def para_obj(self):
        def df_obj(df):
            return {"datas": [d.strftime("%Y-%m-%d") for d in df.index], "chaves": list(df.columns),
                    "v": [[None if pd.isna(x) else round(float(x), 8) for x in df[c].values] for c in df.columns]}
        return {"cot": df_obj(self.cot), "fl": df_obj(self.fl), "info": self.info, "prov": self.prov}

    @staticmethod
    def de_obj(o):
        def obj_df(x):
            idx = pd.to_datetime(x["datas"])
            return pd.DataFrame({c: pd.array(v, dtype="Float64").astype(float) for c, v in zip(x["chaves"], x["v"])}, index=idx)
        return Base(obj_df(o["cot"]), obj_df(o["fl"]), o["info"], o["prov"])

# =============================================================================== utilidades de série
def idx_le(ds, t):
    """Índice do último ds <= t (ou -1)."""
    i = np.searchsorted(ds, np.datetime64(t, "D"), side="right") - 1
    return int(i)

def ret_win(ds, vs, d_end, d_start, tol):
    i1 = idx_le(ds, d_end); i0 = idx_le(ds, d_start)
    if i1 < 0 or i0 < 0:
        return None
    if (np.datetime64(d_start, "D") - ds[i0]).astype(int) > tol:
        return None
    if i1 == i0:
        return None
    return float(vs[i1] / vs[i0] - 1)

def vol_dd(ds, vs, d_end, d_start, mensal, tol):
    i1 = idx_le(ds, d_end); i0 = idx_le(ds, d_start)
    if i0 < 0 or i1 <= i0:
        return None, None
    if (np.datetime64(d_start, "D") - ds[i0]).astype(int) > tol:
        return None, None
    seg = vs[i0:i1 + 1]
    pk = np.maximum.accumulate(seg)
    dd = float(min(0.0, (seg / pk - 1).min()))
    if mensal:
        dser = pd.Series(seg, index=pd.DatetimeIndex(ds[i0:i1 + 1]))
        fims = dser.groupby(dser.index.to_period("M")).last()
        # primeiro ponto é o início; os demais são o último valor de cada mês seguinte
        vals = [seg[0]] + list(fims.values[1:]) if dser.index[0].to_period("M") == fims.index[0] else [seg[0]] + list(fims.values)
        r = np.diff(vals) / np.array(vals[:-1])
        ann, minobs = math.sqrt(12), 6
    else:
        r = seg[1:] / seg[:-1] - 1
        ann, minobs = math.sqrt(252), 20
    if len(r) < minobs:
        return None, None
    return float(np.std(r, ddof=1) * ann), dd

def outliers(v, lim=0.35):
    v = np.asarray(v, dtype=float); n = len(v)
    fora = np.zeros(n, dtype=bool)
    if n < 5:
        return fora
    for i in range(n):
        w = np.concatenate([v[max(0, i - 5):i], v[i + 1:i + 6]])
        med = np.median(w)
        if med > 0 and abs(v[i] / med - 1) > lim:
            fora[i] = True
    return fora

# =============================================================================== CVM informe diário
def _baixar_zip(url):
    st, b = http(url, timeout=600)
    if st == 404 or not b:
        return None
    return zipfile.ZipFile(io.BytesIO(b))

def atualizar_cvm(base, cad, n_meses=2, so_novos=False, log=print):
    want = {}
    for i, a in enumerate(cad):
        if a.get("ativo", "S") == "N" or (a.get("fonte") or "").upper() != "CVM" or not digits(a.get("cnpj")):
            continue
        k = "CVM:" + digits(a["cnpj"]).zfill(14)
        if so_novos and (base.ultima_data(k) or base.info.get(k, {}).get("sem_dados")):
            continue
        want.setdefault(fmt_cnpj(a["cnpj"]), []).append(i)
    if not want:
        return "OK · nenhum fundo para atualizar", None, 0
    h = hoje_br().date(); recs = {}; falhas = []
    if so_novos:
        n_meses = 64
    for m in range(n_meses - 1, -1, -1):
        mes = meses_atras(h.replace(day=1), m)
        url = URL_INF + mes.strftime("%Y%m") + ".zip"
        try:
            z = _baixar_zip(url)
        except Exception as e:
            falhas.append(mes.strftime("%m/%y")); continue
        if z is None:
            continue
        nome = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        df = pd.read_csv(z.open(nome), sep=";", encoding="latin1", dtype=str)
        col_cn = "CNPJ_FUNDO_CLASSE" if "CNPJ_FUNDO_CLASSE" in df.columns else "CNPJ_FUNDO"
        df = df[df[col_cn].isin(want.keys())]
        if "ID_SUBCLASSE" not in df.columns:
            df["ID_SUBCLASSE"] = ""
        for c in ("VL_QUOTA", "VL_PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA", "NR_COTST"):
            df[c] = pd.to_numeric(df.get(c), errors="coerce")
        df["DT_COMPTC"] = pd.to_datetime(df["DT_COMPTC"])
        df["ID_SUBCLASSE"] = df["ID_SUBCLASSE"].fillna("").str.strip()
        for cn, g in df.groupby(col_cn):
            recs.setdefault(cn, []).append(g[["ID_SUBCLASSE", "DT_COMPTC", "VL_QUOTA", "VL_PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA", "NR_COTST"]])
        log("  CVM %s: %d linhas" % (mes.strftime("%m/%Y"), len(df)))
    if so_novos:   # marca quem não tem dados na CVM, para não baixar o histórico de novo a cada cadastro
        for cn, idx in want.items():
            if cn not in recs:
                for i in idx:
                    base.info.setdefault("CVM:" + digits(cad[i]["cnpj"]).zfill(14), {})["sem_dados"] = hoje_br().date().isoformat()
    n_ok = 0; ult = None
    for cn, partes in recs.items():
        g = pd.concat(partes).sort_values("DT_COMPTC")
        for i in want[cn]:
            a = cad[i]; k = "CVM:" + digits(a["cnpj"]).zfill(14)
            last_d = g["DT_COMPTC"].max()
            sub = (a.get("subclasse") or "").strip()
            gl = g[g["DT_COMPTC"] == last_d]
            if not ((gl["ID_SUBCLASSE"] == sub) & (gl["VL_QUOTA"] > 0)).any():
                cand = gl[(gl["VL_QUOTA"] > 0)].sort_values("VL_PATRIM_LIQ", ascending=False)
                if len(cand):
                    best = cand.iloc[0]["ID_SUBCLASSE"]
                    if best != sub:
                        a["subclasse"] = best
                        a["obs"] = ((a.get("obs") or "") + " | Subclasse CVM alterada p/ '%s' em %s" % (best, last_d.strftime("%d/%m/%Y"))).strip(" |")
                        sub = best
            s = g[(g["ID_SUBCLASSE"] == sub) & (g["VL_QUOTA"] > 0)].drop_duplicates("DT_COMPTC", keep="last")
            if not len(s):
                continue
            base.aplicar(k, s["DT_COMPTC"].values, s["VL_QUOTA"].values, escala=True)
            d_ult = s["DT_COMPTC"].iloc[-1].date()
            ult = max(ult, d_ult) if ult else d_ult
            info = base.info.setdefault(k, {})
            info["pl"] = float(gl["VL_PATRIM_LIQ"].fillna(0).sum()); info["cot"] = float(gl["NR_COTST"].fillna(0).sum())
            info["dtpl"] = last_d.date().isoformat()
            fl = (g.assign(m=g["DT_COMPTC"].dt.to_period("M")).groupby("m").apply(lambda x: (x["CAPTC_DIA"].fillna(0) - x["RESG_DIA"].fillna(0)).sum()))
            for mm, v in fl.items():
                base.set_fluxo(k, mm.to_timestamp().date(), float(v))
            n_ok += 1
    msg = "OK · %d de %d fundos" % (n_ok, sum(len(v) for v in want.values()))
    if falhas:
        msg = "Atenção · falhou %s · %s" % (", ".join(falhas), msg)
    return msg, ult, n_ok

# =============================================================================== FIDC (informe mensal)
def _ler_tab_fidc(z, prefixo):
    out = []
    for nome in z.namelist():
        if prefixo.lower() in nome.lower() and nome.lower().endswith(".csv"):
            txt = z.open(nome).read().decode("latin1")
            linhas = txt.splitlines()
            if not linhas:
                continue
            h = [x.strip().upper() for x in linhas[0].split(";")]
            hub = len(h) - 1
            iden = h.index("DENOM_SOCIAL") if "DENOM_SOCIAL" in h else 999
            def fld(parts, col):
                if col not in h: return ""
                j = h.index(col)
                if j > iden: j = len(parts) - 1 - (hub - j)
                return parts[j] if 0 <= j < len(parts) else ""
            for ln in linhas[1:]:
                p = ln.split(";")
                out.append((p, fld))
    return out

def atualizar_fidc(base, cad, so_novos=False, log=print):
    want = {}
    for i, a in enumerate(cad):
        if a.get("ativo", "S") == "N" or (a.get("fonte") or "").upper() != "CVM-FIDC" or not digits(a.get("cnpj")):
            continue
        k = "FIDC:" + digits(a["cnpj"]).zfill(14)
        if so_novos and (base.ultima_data(k) or base.info.get(k, {}).get("sem_dados")):
            continue
        want.setdefault(fmt_cnpj(a["cnpj"]), []).append(i)
    if not want:
        return "OK · nenhum FIDC para atualizar", None, 0
    h = hoje_br().date()
    n_hist = 62 if so_novos else 4
    meses = [meses_atras(h.replace(day=1), m) for m in range(n_hist, 0, -1)]
    zips = []
    for y in sorted({m.year for m in meses if m.year <= 2024}):
        z = _baixar_zip(URL_FIDC_HIST + "%d.zip" % y)
        if z: zips.append(z)
    for m in meses:
        if m.year >= 2025:
            z = _baixar_zip(URL_FIDC + m.strftime("%Y%m") + ".zip")
            if z: zips.append(z)
    dados = {}; pls = {}
    for z in zips:
        for tab, tipo in (("tab_X_2_", 2), ("tab_X_3_", 3), ("tab_IV_", 4)):
            for p, fld in _ler_tab_fidc(z, tab):
                cn = fld(p, "CNPJ_FUNDO_CLASSE") or fld(p, "CNPJ_FUNDO")
                if cn not in want:
                    continue
                try:
                    d = dt.date.fromisoformat(fld(p, "DT_COMPTC")[:10])
                except ValueError:
                    continue
                if tipo == 4:
                    pls.setdefault(cn, {})[d] = num(fld(p, "TAB_IV_A_VL_PL"))
                else:
                    ser = " ".join(fld(p, "TAB_X_CLASSE_SERIE").split())
                    a = dados.setdefault(cn, {}).setdefault(d, {}).setdefault(ser, [None, None, None])
                    if tipo == 2:
                        a[0] = num(fld(p, "TAB_X_VL_COTA")); a[1] = num(fld(p, "TAB_X_QT_COTA"))
                    else:
                        a[2] = num(fld(p, "TAB_X_VL_RENTAB_MES"))
    if so_novos:
        for cn, idx in want.items():
            if cn not in dados:
                for i in idx:
                    base.info.setdefault("FIDC:" + digits(cad[i]["cnpj"]).zfill(14), {})["sem_dados"] = hoje_br().date().isoformat()
    n_ok = 0; ult = None
    for cn, dm in dados.items():
        for i in want[cn]:
            a = cad[i]; k = "FIDC:" + digits(a["cnpj"]).zfill(14)
            ser = (a.get("serie") or "").strip()
            meses_ord = sorted(dm)
            gravou = False
            for j, d in enumerate(meses_ord):
                ds = dm[d]; best = ""
                if ser in ds and (ds[ser][0] or 0) > 0:
                    best = ser
                else:
                    bpl = 0
                    for s_, v in ds.items():
                        if v[0] and v[1] and v[0] * v[1] > bpl:
                            bpl = v[0] * v[1]; best = s_
                    if best and best != ser:
                        a["serie"] = best; ser = best
                if not best:
                    continue
                v = ds[best]
                v0, _ = base.valor_antes(k, d.replace(day=1))
                if v0 is None:
                    novo = v[0]
                else:
                    fat = 0
                    if j > 0 and best in dm[meses_ord[j - 1]] and (dm[meses_ord[j - 1]][best][0] or 0) > 0 and v[0]:
                        fat = v[0] / dm[meses_ord[j - 1]][best][0]
                    if not fat and v[2] is not None:
                        fat = 1 + v[2] / 100
                    if not fat:
                        continue
                    novo = v0 * fat
                if not novo:
                    continue
                # grava na última data da base <= competência (ou cria a data)
                datas = base.cot.index[base.cot.index <= pd.Timestamp(d)]
                dgrava = datas[-1] if len(datas) else pd.Timestamp(d)
                base._garantir(k, [dgrava])
                base.cot.loc[dgrava, k] = novo
                ult = max(ult, dgrava.date()) if ult else dgrava.date()
                if cn in pls and d in pls[cn]:
                    inf = base.info.setdefault(k, {})
                    inf["pl"] = pls[cn][d]; inf["dtpl"] = d.isoformat(); inf.pop("cot", None)
                gravou = True
            n_ok += gravou
    return "OK · %d de %d FIDCs" % (n_ok, sum(len(v) for v in want.values())), ult, n_ok

# =============================================================================== FII mensal (VP, PL, cotistas)
def atualizar_fii_mensal(base, cad, log=print):
    want = {}
    for a in cad:
        if (a.get("fonte") or "").upper() == "YAHOO" and digits(a.get("cnpj")) and a.get("ticker"):
            want[fmt_cnpj(a["cnpj"])] = "YH:" + a["ticker"].upper().strip()
    best = {}
    y = hoje_br().year
    for ano in (y - 1, y):
        z = _baixar_zip(URL_FII + "%d.zip" % ano)
        if not z:
            continue
        nome = [n for n in z.namelist() if "complemento" in n.lower()]
        if not nome:
            continue
        df = pd.read_csv(z.open(nome[0]), sep=";", encoding="latin1", dtype=str)
        cols = {c.upper(): c for c in df.columns}
        ccn = cols.get("CNPJ_FUNDO_CLASSE") or cols.get("CNPJ_FUNDO")
        df = df[df[ccn].isin(want.keys())]
        for _, r in df.iterrows():
            d = str(r[cols["DATA_REFERENCIA"]])[:10]; ver = int(num(r.get(cols.get("VERSAO"), "0")) or 0)
            a = (d, ver, num(r.get(cols.get("VALOR_PATRIMONIAL_COTAS"))), num(r.get(cols.get("PATRIMONIO_LIQUIDO"))), num(r.get(cols.get("TOTAL_NUMERO_COTISTAS"))))
            b = best.get(r[ccn])
            if b is None or (a[0] > b[0]) or (a[0] == b[0] and a[1] >= b[1]):
                best[r[ccn]] = a
    ult = None
    for cn, a in best.items():
        inf = base.info.setdefault(want[cn], {})
        if a[2] is not None: inf["vp"] = a[2]
        inf["dtvp"] = a[0]
        if a[3] is not None: inf["pl"] = a[3]
        if a[4] is not None: inf["cot"] = a[4]
        inf["dtpl"] = a[0]
        ult = max(ult, a[0]) if ult else a[0]
    return "OK · %d FIIs com VP/PL" % len(best), (dt.date.fromisoformat(ult) if ult else None), len(best)

# =============================================================================== Yahoo
def yahoo_chart(sym, rng):
    url = URL_YH + urllib.parse.quote(sym, safe="") + "?range=%s&interval=1d&events=div" % rng
    st, b = http(url, timeout=60, tentativas=4)
    if st != 200:
        return None
    j = json.loads(b)
    res = (j.get("chart") or {}).get("result")
    if not res:
        return None
    r = res[0]; gmt = r.get("meta", {}).get("gmtoffset", 0)
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    cl = q.get("close") or []; vo = q.get("volume") or []
    ds, cs, vs = [], [], []
    for i, t in enumerate(ts):
        c = cl[i] if i < len(cl) else None
        if c is None or c <= 0:
            continue
        d = dt.datetime.fromtimestamp(t + gmt, dt.timezone.utc).date()
        if ds and d <= ds[-1]:
            ds[-1], cs[-1], vs[-1] = d, c, (vo[i] or 0) if i < len(vo) else 0
            continue
        ds.append(d); cs.append(float(c)); vs.append(float((vo[i] or 0) if i < len(vo) else 0))
    agora = hoje_br()
    if ds and (ds[-1] > agora.date() or (ds[-1] == agora.date() and agora.time() < dt.time(18, 30))):
        ds.pop(); cs.pop(); vs.pop()
    divs = []
    for v in ((r.get("events") or {}).get("dividends") or {}).values():
        divs.append((dt.datetime.fromtimestamp(v["date"] + gmt, dt.timezone.utc).date(), float(v["amount"])))
    return ds, cs, vs, divs

def filtro_spike(ds, cl, vl):
    n = len(cl)
    if n < 3:
        return ds, cl, vl
    ok = ~outliers(cl, 0.35)
    for i in range(1, n - 1):
        r1 = cl[i] / cl[i - 1] - 1; r2 = cl[i + 1] / cl[i] - 1
        if abs(r1) > 0.12 and abs((1 + r1) * (1 + r2) - 1) < 0.03:
            ok[i] = False
    return [d for d, o in zip(ds, ok) if o], [c for c, o in zip(cl, ok) if o], [v for v, o in zip(vl, ok) if o]

def eh_desdobramento(r):
    if r <= 0: return False
    k = 1 / r if r < 1 else r
    kr = round(k)
    return 2 <= kr <= 200 and abs(k / kr - 1) < 0.03

def atualizar_yahoo(base, cad, so_novos=False, log=print):
    feitos = set(); n_ok = 0; falhas = []; ult = None
    h = hoje_br().date()
    for a in cad:
        t = (a.get("ticker") or "").upper().strip()
        if (a.get("fonte") or "").upper() != "YAHOO" or a.get("ativo", "S") == "N" or not t or t in feitos:
            continue
        feitos.add(t); k = "YH:" + t
        ld = base.ultima_data(k)
        if so_novos and ld:
            continue
        rng = "6y" if not ld else ("3mo" if ld >= h - dt.timedelta(days=80) else ("1y" if ld >= h - dt.timedelta(days=340) else "6y"))
        try:
            r = yahoo_chart(t + ".SA", rng)
        except Exception:
            r = None
        time.sleep(0.25)
        if not r or not r[0]:
            falhas.append(t); continue
        ds, cl, vl, divs = r
        ds, cl, vl = filtro_spike(ds, cl, vl)
        if not ds:
            falhas.append(t); continue
        dv = {}
        for d, v in divs:
            dv[d] = dv.get(d, 0) + v
        tri = [100.0]
        for j in range(1, len(cl)):
            if eh_desdobramento(cl[j] / cl[j - 1]):
                tri.append(tri[-1])
            else:
                tri.append(tri[-1] * (cl[j] + dv.get(ds[j], 0)) / cl[j - 1])
        base.aplicar(k, ds, tri, escala=True)
        inf = base.info.setdefault(k, {})
        inf["preco"] = cl[-1]; inf["dtpreco"] = ds[-1].isoformat()
        u = list(zip(cl, vl))[-21:]
        inf["liq"] = sum(c * v for c, v in u) / len(u)
        inf["hist"] = ";".join("%s:%s" % (d.strftime("%Y%m%d"), c) for d, c in list(zip(ds, cl))[-15:])
        for d, v in divs:
            base.prov["%s|%s" % (t, d.isoformat())] = v
        n_ok += 1
        ult = max(ult, ds[-1]) if ult else ds[-1]
    msg = "OK · %d tickers" % n_ok + (" · sem dados: %d (%s)" % (len(falhas), " ".join(falhas[:8]) + ("..." if len(falhas) > 8 else "")) if falhas else "")
    return msg, ult, n_ok

# =============================================================================== benchmarks
def sgs(serie, d1):
    for _ in range(4):
        try:
            st, b = http(URL_SGS + "%d/dados?formato=json&dataInicial=%s&dataFinal=%s" % (serie, d1.strftime("%d/%m/%Y"), hoje_br().strftime("%d/%m/%Y")), timeout=60)
            if st == 200 and b.strip().startswith(b"["):
                j = json.loads(b)
                return [dt.datetime.strptime(x["data"], "%d/%m/%Y").date() for x in j], [float(x["valor"]) for x in j]
        except Exception:
            pass
        time.sleep(3)
    return [], []

def atualizar_bcb(base, log=print):
    h = hoje_br().date(); msg = []
    ld = base.ultima_data("BMK:CDI")
    ds, vs = sgs(12, (ld - dt.timedelta(days=10)) if ld else h - dt.timedelta(days=400))
    if len(ds) > 1:
        idx = [1.0]
        for i in range(1, len(ds)):
            idx.append(idx[-1] * (1 + vs[i - 1] / 100))
        base.aplicar("BMK:CDI", ds, idx, escala=True)
        msg.append("CDI até %s" % ds[-1].strftime("%d/%m"))
    else:
        msg.append("CDI: falhou")
    ld = base.ultima_data("BMK:USD")
    ds2, vs2 = sgs(1, (ld - dt.timedelta(days=10)) if ld else h - dt.timedelta(days=400))
    if ds2:
        base.aplicar("BMK:USD", ds2, vs2, escala=False)
        msg.append("PTAX até %s" % ds2[-1].strftime("%d/%m"))
    else:
        msg.append("PTAX: falhou")
    m = " · ".join(msg)
    return ("Atenção · " if "falhou" in m else "OK · ") + m, base.ultima_data("BMK:CDI"), 2

IMAS = ["IMA-B", "IMA-B 5", "IMA-B 5+", "IRF-M", "IMA-S", "IMA-GERAL"]

def atualizar_anbima(base, log=print):
    h = hoje_br().date()
    ld = base.ultima_data("BMK:IMA-B") or (h - dt.timedelta(days=45))
    ld = max(ld, h - dt.timedelta(days=400))
    d = ld + dt.timedelta(days=1); ult = None; dur = {}
    while d < h:
        if d.weekday() < 5:
            corpo = ("Tipo=&DataRef=&Pai=ima&escolha=2&Idioma=PT&saida=csv&Dt_Ref_Ver=%s&Dt_Ref=%s" % (d.strftime("%Y%m%d"), d.strftime("%d/%m/%Y"))).encode()
            try:
                st, b = http(URL_ANB, data=corpo, timeout=60, headers={"Content-Type": "application/x-www-form-urlencoded"})
            except Exception:
                st, b = 0, b""
            if st == 200:
                for ln in b.decode("latin1").replace("\r", "").split("\n"):
                    p = ln.split(";")
                    if len(p) >= 3 and p[0].strip().upper() in IMAS:
                        try:
                            dd = dt.datetime.strptime(p[1].strip(), "%d/%m/%Y").date()
                        except ValueError:
                            continue
                        v = num(p[2])
                        if v and v > 0:
                            base.aplicar("BMK:" + p[0].strip().upper(), [dd], [v], escala=False)
                            ult = max(ult, dd) if ult else dd
                            if len(p) > 9: dur[p[0].strip().upper()] = num(p[9])
        d += dt.timedelta(days=1)
    if ult is None:
        ult = base.ultima_data("BMK:IMA-B")
    if dur:
        base.info["ANBIMA:duration"] = {k: v for k, v in dur.items() if v}
    ok = ult and ult >= h - dt.timedelta(days=5)
    return ("OK · " if ok else "Atenção · ") + "IMA até %s" % (ult.strftime("%d/%m") if ult else "-"), ult, 6

def atualizar_b3(base, log=print):
    h = hoje_br(); msg = []; ult = None
    for ix in ("IBOV", "IFIX", "SMLL", "IDIV"):
        ds, vs = [], []
        for y in range((h.date() - dt.timedelta(days=20)).year, h.year + 1):
            q = base64.b64encode(json.dumps({"index": ix, "language": "pt-br", "year": str(y)}, separators=(",", ":")).encode()).decode()
            try:
                st, b = http(URL_B3 + q, timeout=60)
                j = json.loads(b) if st == 200 else {}
            except Exception:
                j = {}
            for row in j.get("results") or []:
                dia = int(row.get("day") or 0)
                for mm in range(1, 13):
                    s = row.get("rateValue%d" % mm)
                    if not s or not dia:
                        continue
                    try:
                        d = dt.date(y, mm, dia)
                    except ValueError:
                        continue
                    if d < h.date() or (d == h.date() and h.time() > dt.time(18, 30)):
                        v = num(s)
                        if v: ds.append(d); vs.append(v)
        if ds:
            o = sorted(zip(ds, vs)); ds = [x[0] for x in o]; vs = [x[1] for x in o]
            base.aplicar("BMK:" + ix, ds, vs, escala=False)
            ult = max(ult, ds[-1]) if ult else ds[-1]; msg.append(ix)
        else:
            msg.append(ix + "(falhou)")
    m = "B3: " + " ".join(msg)
    return ("Atenção · " if "falhou" in m else "OK · ") + m, ult, 4

EXT = [("^GSPC", "S&P 500 (R$)"), ("GC=F", "Ouro (R$)"), ("SI=F", "Prata (R$)"), ("BTC-USD", "Bitcoin (R$)")]

def atualizar_exterior(base, log=print):
    h = hoje_br().date(); n_ok = 0; ult = None
    for sym, nome in EXT:
        k = "USD:" + sym
        ld = base.ultima_data(k)
        rng = "3mo" if (ld and ld >= h - dt.timedelta(days=80)) else "6y"
        try:
            r = yahoo_chart(sym, rng)
        except Exception:
            r = None
        if not r or not r[0]:
            continue
        ds, cl, _, _ = r
        base.aplicar(k, ds, cl, escala=False)
        lim = pd.Timestamp(h - dt.timedelta(days=100)) if rng == "3mo" else pd.Timestamp("2000-01-01")
        u = base.cot[k].ffill(); p = base.cot["BMK:USD"].ffill() if "BMK:USD" in base.cot else None
        if p is None:
            continue
        datas = base.cot.index[(base.cot.index >= lim) & (base.cot.index.dayofweek < 5)]
        vals = (u.reindex(datas) * p.reindex(datas)).dropna()
        base.aplicar("BMK:" + nome, vals.index, vals.values, escala=False)
        n_ok += 1; ult = max(ult, ds[-1]) if ult else ds[-1]
        time.sleep(0.3)
    return ("OK · " if n_ok == 4 else "Atenção · ") + "Exterior: %d/4" % n_ok, ult, n_ok

def limpar_outliers(base):
    tot = 0
    for c in base.cot.columns:
        if c.startswith("YH:") or c.startswith("USD:"):
            s = base.cot[c].dropna(); s = s[s > 0]
            if len(s) >= 11:
                fora = outliers(s.values, 0.35)
                if fora.any():
                    base.cot.loc[s.index[fora], c] = np.nan; tot += int(fora.sum())
    return tot

# =============================================================================== classificação sugerida
def norm_txt(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9&+ ]", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "

def _tem(txt, *termos):
    for t in termos:
        if t.endswith("*"):
            if " " + t[:-1] in txt: return True
        elif " " + t + " " in txt:
            return True
    return False

def sugerir_classe(nome, anbima="", tk=""):
    n = norm_txt(nome); a = norm_txt(anbima); seg = ""; tipo = "Fundo"
    if tk and (" FII " in a or "FIAGRO" in a or "FUNDOS DE PARTICIPACOES" in a or not anbima.strip()) and "RENDA FIXA" not in a and "ETF" not in a:
        tipo = "FII"
        if _tem(n, "AGRO*", "CRA", "FIAGRO", "RURAL") or "FIAGRO" in a: seg, tipo = "Fiagro", "Fiagro"
        elif "PARTICIPACOES" in a: seg, tipo = "FIP", "FIP"
        elif _tem(n, "LOG*", "GALPOES", "INDUSTRIAL"): seg = "Logística"
        elif _tem(n, "SHOPPING", "MALLS", "MALL"): seg = "Shoppings"
        elif _tem(n, "FOF", "FUNDO DE FUNDOS", "FUNDOS") or "PAPEL FUNDOS" in a: seg = "FoF"
        elif _tem(n, "CRI", "RECEBIVEIS", "CREDITO", "PAPEL", "SECURITIES", "HIGH YIELD", "HIGH GRADE") or "PAPEL" in a: seg = "Recebíveis"
        elif _tem(n, "CORPORATE", "OFFICE", "OFFICES", "LAJES", "ESCRITORIOS", "CORPORATIVO", "CORPORATIVA"): seg = "Lajes Corporativas"
        elif _tem(n, "RENDA URBANA", "VAREJO", "AGENCIAS", "HOSPITAL", "EDUCACIONAL"): seg = "Renda Urbana/Varejo"
        elif _tem(n, "HEDGE FUND"): seg = "Hedge Fund FII"
        elif _tem(n, "RESIDENCIAL", "DESENVOLVIMENTO", "LIVING"): seg = "Desenvolvimento/Residencial"
        elif _tem(n, "HOTEL"): seg = "Hotel"
        else: seg = "Híbrido"
        return "FII", seg, tipo
    if tk:
        if _tem(n, "BITCOIN", "CRIPTO", "HASH", "ETH"): return "Cripto", "", "ETF"
        if _tem(n, "OURO", "GOLD", "PRATA"): return "Metais", "", "ETF"
        if _tem(n, "S&P", "SP500", "NASDAQ", "GLOBAL", "USA", "US"): return "AcExt", "", "ETF"
        if "RENDA FIXA" in a: return "InfraLst", "", "FI-Infra"
        return "ETFBR", "", "ETF"
    if _tem(n, "FIDC", "DIREITOS CREDITORIOS", "FICFIDC") or "FIDC" in a: return "FIDC", "", "FIDC"
    if _tem(n, "INCENTIVAD*", "INFRA*", "DEB INC"):
        return ("InfraCDI" if _tem(n, "CDI", "POS*", "HEDGE") else "InfraIPCA"), "", tipo
    if _tem(n, "BITCOIN", "CRIPTO", "HASHDEX"): return "Cripto", "", tipo
    if _tem(n, "OURO", "PRATA", "GOLD"): return "Metais", "", tipo
    if "CAMBIAL" in a or _tem(n, "CAMBIAL"): return "Cambial", "", tipo
    if _tem(n, "LONG BIAS", "LONG BIASED", "LONG-BIASED", "LB"): return "LB", "", tipo
    if _tem(n, "LONG SHORT", "EQUITY HEDGE", "LONG AND SHORT") or "LONG AND SHORT" in a: return "LS", "", tipo
    if _tem(n, "QUANT", "SISTEMATICO"): return "MMQuant", "", tipo
    if _tem(n, "S&P", "US TECH", "GLOBAL EQUITIES", "NASDAQ") or "ACOES INVESTIMENTO NO EXTERIOR" in a: return "AcExt", "", tipo
    if " ACOES" in a or _tem(n, "FIA", "ACOES", "FICFIA"): return "LO", "", tipo
    if "INVESTIMENTO NO EXTERIOR" in a: return "MMExt", "", tipo
    hy = _tem(n, "HY", "HIGH YIELD", "ESTRUTURADO", "OPORTUNIDADE*", "SPECIAL")
    if "MULTIMERCADO" in a or _tem(n, "MM", "MULTIMERCADO", "MACRO"):
        if _tem(n, "CRED*", "CP"): return ("CrdHY" if hy else "CrdHG"), "", tipo
        return "MM", "", tipo
    if "SIMPLES" in a or ("SOBERANO" in a and "BAIXA" in a) or _tem(n, "SELIC", "DI", "CASH", "REFERENCIADO", "TESOURO SELIC"): return "DI", "", tipo
    if "INDEXADOS" in a or _tem(n, "IPCA", "INFLACAO", "PRE FIXADO", "PREFIXADO", "IMA B", "IMAB"): return "RFIdx", "", tipo
    if "CREDITO LIVRE" in a: return ("CrdHY" if hy else "CrdHG"), "", tipo
    if _tem(n, "CRED*", "CP", "HIGH GRADE", "CORPORATE"): return "CrdHG", "", tipo
    return "RF", "", tipo

def completar_cadastro(cad):
    """Preenche fonte, chave, CNPJ formatado e classe sugerida de linhas novas (como 'Classificar novos')."""
    novos = 0; prox_id = max([int(a.get("id") or 0) for a in cad] + [0]) + 1
    for a in cad:
        nome = (a.get("nome") or "").strip()
        if not nome:
            continue
        tk = (a.get("ticker") or "").upper().strip()
        if not tk and re.fullmatch(r"[A-Z][A-Z0-9]{3}\d{1,2}", nome.upper().strip()):
            tk = nome.upper(); a["ticker"] = tk
        if not a.get("id"):
            a["id"] = prox_id; prox_id += 1
        a.setdefault("ativo", "S")
        if not a.get("fonte"):
            if tk: a["fonte"] = "YAHOO"
            elif "FIDC" in norm_txt(nome + " " + (a.get("anbima") or "")) or "DIREITOS CRED" in norm_txt(nome): a["fonte"] = "CVM-FIDC"
            else: a["fonte"] = "CVM"
        if not a.get("chave"):
            f = a["fonte"].upper()
            a["chave"] = ("YH:" + tk) if f == "YAHOO" else (("FIDC:" if f == "CVM-FIDC" else "CVM:") + digits(a.get("cnpj")).zfill(14))
            if digits(a.get("cnpj")): a["cnpj"] = fmt_cnpj(a["cnpj"])
        if not a.get("classe"):
            cl, seg, tipo = sugerir_classe(nome, a.get("anbima") or "", tk)
            a["classe"] = cl
            if seg and not a.get("segmento"): a["segmento"] = seg
            if not a.get("tipo"): a["tipo"] = tipo
            a["origem"] = "Sugerida (revisar)"; novos += 1
        elif a["classe"] == "FII" and not a.get("segmento"):
            a["segmento"] = sugerir_classe(nome, a.get("anbima") or "", tk)[1]
    return novos

# =============================================================================== métricas
ALIAS_BMK = {"Dólar (PTAX)": "USD", "Dólar": "USD", "PTAX": "USD"}

class Calc:
    def __init__(self, base, cap=None):
        self.b = base; self.cap = cap
        self.cdi = base.serie("BMK:CDI")
        self._bm = {}

    def bm(self, nome):
        if nome not in self._bm:
            self._bm[nome] = self.b.serie("BMK:" + ALIAS_BMK.get(nome, nome))
        return self._bm[nome]

    def metricas(self, k, mensal=False, bench=""):
        ds, vs = self.b.serie(k, self.cap)
        if len(ds) < 2:
            return None
        d0 = pd.Timestamp(ds[-1]).date(); tol = 35 if mensal else 10
        o = {"data": d0.isoformat()}
        o["mes"] = ret_win(ds, vs, d0, fim_mes_anterior(d0), tol)
        o["ano"] = ret_win(ds, vs, d0, dt.date(d0.year - 1, 12, 31), tol)
        for m in (12, 24, 36, 48, 60):
            o["r%d" % m] = ret_win(ds, vs, d0, meses_atras(d0, m), tol)
        cd, cv = self.cdi
        if len(cd):
            c = ret_win(cd, cv, d0, dt.date(d0.year - 1, 12, 31), 10)
            if o["ano"] is not None and c and c > 0: o["pcdi_ano"] = o["ano"] / c
            c = ret_win(cd, cv, d0, meses_atras(d0, 12), 10)
            if o["r12"] is not None and c and c > 0: o["pcdi12"] = o["r12"] / c
        if bench:
            bd, bv = self.bm(bench)
            if len(bd) > 1:
                v = ret_win(bd, bv, d0, meses_atras(d0, 12), 10)
                o["bmk12"] = v
                if o["r12"] is not None and v is not None: o["vs_bmk"] = o["r12"] - v
        o["vol12"], o["dd12"] = vol_dd(ds, vs, d0, meses_atras(d0, 12), mensal, tol)
        o["vol36"], o["dd36"] = vol_dd(ds, vs, d0, meses_atras(d0, 36), mensal, tol)
        if o["r36"] is not None and o["vol36"] and len(cd):
            c36 = ret_win(cd, cv, d0, meses_atras(d0, 36), 10)
            if c36 is not None:
                o["sharpe36"] = ((1 + o["r36"]) ** (1 / 3) - (1 + c36) ** (1 / 3)) / o["vol36"]
        if not mensal and len(vs) >= 2:
            o["dia"] = float(vs[-1] / vs[-2] - 1)
        return o

    def bmk_na_data(self, nome, d0):
        ds, vs = self.bm(nome)
        if len(ds) < 2:
            return None
        i = idx_le(ds, d0)
        if i < 0:
            return None
        de = pd.Timestamp(ds[i]).date()
        o = {"data": de.isoformat(), "mes": ret_win(ds, vs, de, fim_mes_anterior(de), 10),
             "ano": ret_win(ds, vs, de, dt.date(de.year - 1, 12, 31), 10)}
        for m in (12, 24, 36, 48, 60):
            o["r%d" % m] = ret_win(ds, vs, de, meses_atras(de, m), 10)
        if i >= 1: o["dia"] = float(vs[i] / vs[i - 1] - 1)
        return o

def data_ref_cvm(base, cad):
    h = hoje_br().date(); cnt = {}
    for a in cad:
        if (a.get("fonte") or "").upper() == "CVM" and a.get("ativo", "S") != "N" and a.get("chave"):
            d = base.ultima_data(a["chave"])
            if d and d > h - dt.timedelta(days=20):
                cnt[d] = cnt.get(d, 0) + 1
    if not cnt:
        return None
    return max(cnt.items(), key=lambda x: (x[1], x[0]))[0]

def preco_na_data(base, k, cap):
    inf = base.info.get(k, {}); p = inf.get("preco")
    if not cap:
        return p
    hst = inf.get("hist")
    if not hst:
        ds, vs = base.serie(k)
        if len(ds) > 1 and p:
            i = idx_le(ds, cap)
            if 0 <= i < len(ds) - 1:
                p = p * vs[i] / vs[-1]
        return p
    best, v = None, None
    for x in hst.split(";"):
        if ":" not in x: continue
        d, c = x.split(":")
        d = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        if d <= cap and (best is None or d >= best):
            best, v = d, float(c)
    return v if best else p

def calcular(base, cad, classes, bmks, ref_modo="cvm"):
    cls = {c["codigo"]: c for c in classes}
    cap = data_ref_cvm(base, cad) if ref_modo == "cvm" else None
    C = Calc(base, cap)
    d_ref_dy = cap or hoje_br().date()
    prov_t = {}
    for kk, v in base.prov.items():
        t, d = kk.split("|"); prov_t.setdefault(t, []).append((dt.date.fromisoformat(d), v))
    linhas = []; ref_d = None
    for a in cad:
        if (a.get("ativo") or "S").upper() == "N" or not a.get("chave"):
            continue
        k = a["chave"]; classe = a.get("classe") or ""; t = (a.get("ticker") or "").upper().strip(); ix = a.get("indexador") or ""
        bk = (a.get("benchmark") or "").strip()
        if not bk:
            if classe == "FII": bk = "IFIX"
            elif classe == "InfraLst": bk = "IMA-B" if ix.upper() == "IPCA" else "CDI"
            elif classe in cls: bk = cls[classe].get("bench") or "CDI"
            else: bk = "CDI"
        mensal = (a.get("fonte") or "").upper() == "CVM-FIDC"
        L = {"id": a.get("id"), "chave": k, "nome": a.get("nome"), "classe": classe, "seg": a.get("segmento") or "",
             "ident": t or a.get("cnpj") or "", "tipo": a.get("tipo") or "", "idx": ix, "bmk": bk, "fonte": a.get("fonte"), "mensal": mensal}
        m = C.metricas(k, mensal, bk)
        if m:
            L.update(m)
            d0 = dt.date.fromisoformat(m["data"])
            ref_d = max(ref_d, d0) if ref_d else d0
            v = base.fluxo_soma(k, d0.replace(day=1), d0.replace(day=1))
            L["cap_mes"] = v / 1e6 if v is not None else None
            v = base.fluxo_soma(k, meses_atras(d0.replace(day=1), 11), d0.replace(day=1))
            L["cap12"] = v / 1e6 if v is not None else None
        inf = base.info.get(k, {})
        if inf.get("pl") is not None: L["pl"] = inf["pl"] / 1e6
        if (inf.get("cot") or 0) > 0: L["cot"] = inf["cot"]
        if inf.get("nomecvm"): L["nomecvm"] = inf["nomecvm"]
        if t:
            preco = preco_na_data(base, k, cap); vp = inf.get("vp")
            if preco: L["preco"] = preco
            if vp and vp > 0:
                L["vp"] = vp
                if preco: L["pvp"] = preco / vp
            if inf.get("liq") is not None: L["liq"] = inf["liq"] / 1000
            s12 = 0; ult_p = 0; ult_d = None
            for d, v in prov_t.get(t, []):
                if meses_atras(d_ref_dy, 12) < d <= d_ref_dy: s12 += v
                if d <= d_ref_dy and (ult_d is None or d > ult_d): ult_d, ult_p = d, v
            if preco and preco > 0:
                if s12 > 0: L["dy12"] = s12 / preco
                if ult_p > 0 and ult_d and ult_d > meses_atras(d_ref_dy, 2): L["dym"] = ult_p / preco
            if ult_d: L["ult_prov"] = ult_p; L["dt_ex"] = ult_d.isoformat()
        linhas.append(L)
    # ranking e quartil em 12m dentro da classe (FII: dentro do segmento)
    grupos = {}
    for i, L in enumerate(linhas):
        if L.get("r12") is not None:
            grupos.setdefault((L["classe"], L["seg"] if L["classe"] == "FII" else ""), []).append(i)
        if ref_d:
            lim = 70 if L["mensal"] else 7
            if not L.get("data") or (ref_d - dt.date.fromisoformat(L["data"])).days > lim:
                L["defasada"] = True
    for g in grupos.values():
        g.sort(key=lambda i: -linhas[i]["r12"])
        for pos, i in enumerate(g, 1):
            linhas[i]["rank"] = pos; linhas[i]["n"] = len(g); linhas[i]["quartil"] = int((pos - 1) * 4 / len(g)) + 1
    if cap: ref_d = cap
    # benchmarks
    bm_out = []
    for b in bmks:
        m = C.metricas("BMK:" + b["nome"])
        if m:
            m.update({"nome": b["nome"], "descricao": b.get("descricao"), "fonte": b.get("fonte")})
            bm_out.append(m)
    # linha de benchmark de cada classe/segmento, na data mais recente do grupo
    por_grupo = {}
    for L in linhas:
        g = L["classe"] if L["classe"] != "FII" else "FII|" + L["seg"]
        if L.get("data"):
            por_grupo[g] = max(por_grupo.get(g, ""), L["data"])
    bm_grupo = {}
    for g, d in por_grupo.items():
        if g.startswith("FII|"): bn = "IFIX"
        else: bn = (cls.get(g) or {}).get("bench") or "CDI"
        r = C.bmk_na_data(bn, dt.date.fromisoformat(d))
        if r: r["nome"] = bn; bm_grupo[g] = r
    return {"ref": ref_d.isoformat() if ref_d else None, "cap_cvm": cap.isoformat() if cap else None,
            "linhas": linhas, "benchmarks": bm_out, "bm_grupo": bm_grupo,
            "duration_ima": base.info.get("ANBIMA:duration", {})}

def series_resumo(base, cad, cap=None, meses=36):
    """Séries semanais (base 100) dos últimos 36 meses para os gráficos do detalhe."""
    out = {}
    fim = pd.Timestamp(cap) if cap else base.cot.index.max()
    ini = pd.Timestamp(meses_atras(fim.date(), meses))
    chaves = [a["chave"] for a in cad if a.get("chave") and (a.get("ativo") or "S") != "N"]
    chaves += [c for c in base.cot.columns if c.startswith("BMK:")]
    for k in chaves:
        if k not in base.cot.columns:
            continue
        s = base.cot[k].dropna()
        s = s[(s.index >= ini - pd.Timedelta(days=10)) & (s.index <= fim) & (s > 0)]
        if len(s) < 3:
            continue
        w = s.resample("W-FRI").last().dropna()
        if s.index[-1] > w.index[-1]: w.loc[s.index[-1]] = s.iloc[-1]
        out[k] = {"d0": w.index[0].strftime("%Y-%m-%d"), "d": [int((d - w.index[0]).days) for d in w.index], "v": [round(float(x / w.iloc[0] * 100), 3) for x in w.values]}
    return out
