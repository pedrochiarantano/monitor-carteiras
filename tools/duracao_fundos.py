# -*- coding: utf-8 -*-
"""Duration estimada de fundos a partir da carteira aberta na CVM (CDA).

Uso: python duracao_fundos.py <pasta_cda> <ms_anbima.txt> <deb_anbima.txt> <data_ref AAAA-MM-DD> <cnpjs.json> <saida.json>

Regras (duration de Macaulay, em anos de 252 dias úteis):
- Títulos públicos: LTN (zero), NTN-F (10% a.a.), NTN-B/NTN-C (6% a.a.) a partir da taxa indicativa ANBIMA.
  LFT e operações compromissadas = 0.
- Debêntures: duration ANBIMA (dias úteis / 252) pelo código do ativo.
- Crédito privado sem código ANBIMA (CRI, CRA, CCB, debênture sem preço):
  IPCA ou prefixado -> estimado pelo prazo, amortização constante semestral, taxa 8% a.a.;
  CDI/Selic -> 0 (pós-fixado não tem risco de taxa relevante).
- CDB/LF/LCA: pós-fixado = 0; pré ou IPCA = prazo até o vencimento (bullet).
- Cotas de outros fundos: duration do fundo investido (recursivo, até 4 níveis).
- Caixa = 0. Valores a pagar/receber, derivativos, ações e exterior ficam fora da conta.
A cobertura informa quanto do patrimônio positivo entrou no cálculo.
"""
import csv, json, sys, os, re, datetime as dt
from collections import defaultdict
import numpy as np

csv.field_size_limit(10**9)

def easter(y):
    a=y%19;b=y//100;c=y%100;d=b//4;e=b%4;f=(b+8)//25;g=(b-f+1)//3;h=(19*a+b-d-g+15)%30
    i=c//4;k=c%4;l=(32+2*e+2*i-h-k)%7;m=(a+11*h+22*l)//451;mo=(h+l-7*m+114)//31;da=((h+l-7*m+114)%31)+1
    return dt.date(y,mo,da)

HOL=[]
for y in range(2020,2081):
    e=easter(y)
    HOL += [dt.date(y,1,1),dt.date(y,4,21),dt.date(y,5,1),dt.date(y,9,7),dt.date(y,10,12),dt.date(y,11,2),
            dt.date(y,11,15),dt.date(y,12,25),e-dt.timedelta(48),e-dt.timedelta(47),e-dt.timedelta(2),e+dt.timedelta(60)]
    if y>=2024: HOL.append(dt.date(y,11,20))
HOL=np.array(sorted(set(HOL)),dtype='datetime64[D]')

def du(a,b):
    return int(np.busday_count(np.datetime64(a), np.datetime64(b), holidays=HOL))

def pdate(s):
    s=(s or "").strip()
    if not s: return None
    for f in ("%Y-%m-%d","%d/%m/%Y","%Y%m%d"):
        try: return dt.datetime.strptime(s[:10] if f!="%Y%m%d" else s[:8], f).date()
        except ValueError: pass
    return None

def add_months(d,m):
    y=d.year+(d.month-1+m)//12; mo=(d.month-1+m)%12+1
    return dt.date(y,mo,min(d.day,28) if d.day>28 else d.day)

def macaulay(ref, venc, cupom_aa, y_aa, amort=False):
    """Fluxos semestrais retroativos a partir do vencimento."""
    if venc<=ref: return 0.0
    datas=[venc]; d=venc
    while True:
        d=add_months(d,-6)
        if d<=ref: break
        datas.append(d)
    datas=sorted(datas); n=len(datas)
    c=(1+cupom_aa)**0.5-1
    saldo=1.0; fl=[]
    for i,d in enumerate(datas):
        if amort:
            am=1.0/n; f=saldo*c+am; saldo-=am
        else:
            f=c+(1.0 if i==n-1 else 0.0)
        fl.append((du(ref,d)/252.0,f))
    pv=[f/(1+y_aa)**t for t,f in fl]; s=sum(pv)
    return sum(t*p for (t,_),p in zip(fl,pv))/s if s>0 else 0.0

def carregar_anbima(ms, deb):
    tit={}
    with open(ms,encoding='latin1') as fh:
        for ln in fh:
            p=ln.strip().split('@')
            if len(p)<9 or not re.match(r'^\d{8}$',p[1]): continue
            try: tit[(p[0].strip(), pdate(p[4]))]=float(p[7].replace(',','.'))/100
            except ValueError: pass
    debs={}
    with open(deb,encoding='latin1') as fh:
        for ln in fh:
            p=ln.strip().split('@')
            if len(p)<13: continue
            try: debs[p[0].strip().upper()]=float(p[12].replace('.','').replace(',','.'))/252.0
            except ValueError: pass
    return tit, debs

MAPTIT={'LETRAS DO TESOURO NACIONAL':'LTN','NOTAS DO TESOURO NACIONAL - SERIE F':'NTN-F',
        'NOTAS DO TESOURO NACIONAL SERIE B':'NTN-B','NOTAS DO TESOURO NACIONAL SERIE C':'NTN-C',
        'LETRAS FINANCEIRAS DO TESOURO':'LFT','NOTAS DO TESOURO NACIONAL SERIE I':'NTN-I'}

def dur_titulo(tipo, venc, ref, tit):
    if venc is None: return None
    if tipo in ('LFT','NTN-I'): return 0.0
    y=tit.get((tipo,venc))
    if y is None:
        cand=[(abs((v-venc).days),t) for (tp,v),t in tit.items() if tp==tipo]
        y=min(cand)[1] if cand else (0.14 if tipo in('LTN','NTN-F') else 0.075)
    if tipo=='LTN': return du(ref,venc)/252.0
    if tipo=='NTN-F': return macaulay(ref,venc,0.10,y)
    return macaulay(ref,venc,0.06,y)

def norm(c): return re.sub(r'\D','',c or '')

MES={'F':1,'G':2,'H':3,'J':4,'K':5,'M':6,'N':7,'Q':8,'U':9,'V':10,'X':11,'Z':12}
IPCA_INDICE=7600.0   # número-índice aproximado do IPCA, usado no valor do ponto do DAP

def taxa_curva(tit, tipo, venc):
    cand=[(abs((v-venc).days),t) for (tp,v),t in tit.items() if tp in tipo]
    return min(cand)[1] if cand else None

def futuro(cod, tp, qt, vendido, ref, tit):
    m=re.match(r'^(DI1|DAP)([FGHJKMNQUVXZ])(\d{2})$', cod)
    if not m: return None
    try: q=float(qt or 0)
    except ValueError: return None
    if q==0: return None
    ano=2000+int(m.group(3)); mes=MES[m.group(2)]
    if m.group(1)=='DI1':
        venc=dt.date(ano,mes,1); y=taxa_curva(tit,('LTN','NTN-F'),venc) or 0.14; ponto=1.0
    else:
        venc=dt.date(ano,mes,15); y=taxa_curva(tit,('NTN-B',),venc) or 0.075; ponto=0.00025*IPCA_INDICE
    n=du(ref,venc)
    if n<=0: return None
    pu=100000/(1+y)**(n/252)
    noc=abs(q)*pu*ponto
    # vendido em taxa = comprado em preço = duration positiva
    return ((noc if vendido else -noc), n/252)

def main():
    pasta, ms, deb, dref, alvo_f, saida = sys.argv[1:7]
    ref=dt.date.fromisoformat(dref)
    tit, debs = carregar_anbima(ms, deb)
    hold=defaultdict(list)   # cnpj -> [(valor, dur|None, tipo, ('fundo',cnpj)|None)]
    futs=defaultdict(list)   # cnpj -> [(nocional_com_sinal, dur)]
    pls={}
    def arq(nome):
        fs=[f for f in os.listdir(pasta) if f.startswith(nome) and f.endswith('.csv')]
        return os.path.join(pasta,fs[0]) if fs else None
    def rd(nome):
        f=arq(nome)
        if not f: return
        with open(f,encoding='latin1') as fh:
            yield from csv.DictReader(fh,delimiter=';')
    def val(r):
        try: return float(r.get('VL_MERC_POS_FINAL') or 0)
        except ValueError: return 0.0
    def idx(r):
        s=(r.get('DS_INDEXADOR_POSFX') or '').upper()
        if 'IPCA' in s or 'IGP' in s or 'PREÇO' in s: return 'ipca'
        if 'DI ' in s or s.startswith('DI') or 'SELIC' in s or 'CDI' in s: return 'pos'
        if 'PREFIX' in s: return 'pre'
        return '?'
    for r in rd('cda_fi_BLC_1'):
        c=norm(r['CNPJ_FUNDO_CLASSE']); v=val(r)
        if v<=0: continue
        if 'Compromissada' in r['TP_APLIC']: hold[c].append((v,0.0,'compromissada',None)); continue
        t=MAPTIT.get(r['TP_TITPUB'].strip(),'?')
        d=dur_titulo(t,pdate(r['DT_VENC']),ref,tit)
        hold[c].append((v,d,'titulo '+t,None))
    for r in rd('cda_fi_BLC_2'):
        c=norm(r['CNPJ_FUNDO_CLASSE']); v=val(r)
        if v>0: hold[c].append((v,None,'cotas',norm(r['CNPJ_FUNDO_CLASSE_COTA'])))
    for r in rd('cda_fi_BLC_4'):
        c=norm(r['CNPJ_FUNDO_CLASSE']); v=val(r)
        if r['TP_APLIC'].startswith('Mercado Futuro') and ('DI1' in r['TP_ATIVO'] or 'DAP' in r['TP_ATIVO']):
            fut=futuro((r['CD_ATIVO'] or '').strip().upper(), r['TP_ATIVO'], r['QT_POS_FINAL'], 'vendid' in r['TP_APLIC'].lower(), ref, tit)
            if fut: futs[c].append(fut)
            continue
        if v<=0: continue
        if r['TP_APLIC'].startswith('Debêntures'):
            d=debs.get((r['CD_ATIVO'] or '').strip().upper())
            hold[c].append((v,d,'debenture anbima' if d is not None else 'debenture sem preco',None))
        elif r['TP_APLIC'].startswith('Ações'):
            hold[c].append((v,None,'acoes',None))
    for r in rd('cda_fi_BLC_5'):
        c=norm(r['CNPJ_FUNDO_CLASSE']); v=val(r)
        if v<=0: continue
        venc=pdate(r['DT_VENC']); ix='pos' if (r.get('TITULO_POSFX')=='S' and idx(r)=='pos') else idx(r)
        if ix=='pos': d=0.0
        elif venc: d=max(0,du(ref,venc))/252.0
        else: d=None
        hold[c].append((v,d,'deposito '+ix,None))
    for r in rd('cda_fi_BLC_6'):
        c=norm(r['CNPJ_FUNDO_CLASSE']); v=val(r)
        if v<=0: continue
        venc=pdate(r['DT_VENC']); ix=idx(r)
        if ix=='pos': d=0.0
        elif venc and ix in('ipca','pre','?'): d=macaulay(ref,venc,0.08 if ix!='pre' else 0.13,0.08 if ix!='pre' else 0.14,amort=True)
        else: d=None
        hold[c].append((v,d,'credito '+ix+' estimado',None))
    for r in rd('cda_fi_BLC_8'):
        c=norm(r['CNPJ_FUNDO_CLASSE']); v=val(r)
        if v>0 and r['TP_APLIC'].startswith('Disponibilidades'): hold[c].append((v,0.0,'caixa',None))
    for r in rd('cda_fi_PL'):
        try: pls[norm(r['CNPJ_FUNDO_CLASSE'])]=float(r['VL_PATRIM_LIQ'] or 0)
        except ValueError: pass
    memo={}
    def dur_fundo(c, nivel=0, pilha=()):
        if c in memo: return memo[c]
        if nivel>4 or c in pilha or c not in hold: return None
        vis=sum(v for v,_,_,_ in hold[c]); tot=max(vis, pls.get(c,0) or 0); cob=0.0; soma=0.0; tipos=defaultdict(float)
        for v,d,tp,filho in hold[c]:
            if filho:
                r=dur_fundo(filho,nivel+1,pilha+(c,))
                if r and r['du'] is not None and r['cobertura']>0:
                    peso=v*r['cobertura']; soma+=peso*r['du']; cob+=peso
                    for k,x in r['tipos'].items(): tipos[k]+=x*v
                continue
            if d is None: tipos['fora: '+tp]+=v; continue
            soma+=v*d; cob+=v; tipos[tp]+=v
        nf=sum(n*d for n,d in futs.get(c,[]))
        if futs.get(c): tipos['futuros (nocional/PL)']+=sum(abs(n) for n,_ in futs[c])
        soma+=nf
        if cob<=0 and futs.get(c): cob=tot*0  # só futuros: sem base
        res={'du': (soma/cob if cob>0 else None), 'cobertura': (cob/tot if tot>0 else 0.0), 'pl_cda': tot,
             'tipos': {k:x/tot for k,x in tipos.items()} if tot>0 else {}}
        memo[c]=res; return res
    alvo=json.load(open(alvo_f))
    out={}
    for chave,cnpj in alvo.items():
        r=dur_fundo(norm(cnpj))
        out[chave]={'cnpj':cnpj,'du':(round(r['du'],2) if r and r['du'] is not None else None),
                    'cobertura':(round(r['cobertura'],3) if r else 0),
                    'composicao':({k:round(v,3) for k,v in sorted(r['tipos'].items(),key=lambda x:-x[1])[:6]} if r else {}),
                    'encontrado': bool(r)}
    json.dump(out,open(saida,'w'),ensure_ascii=False,indent=1)
    # verificação dos títulos
    for t,v in (('NTN-B','2035-05-15'),('NTN-F','2033-01-01'),('NTN-F','2031-01-01'),('NTN-B','2050-08-15'),('NTN-B','2029-05-15')):
        print('check',t,v,round(dur_titulo(t,dt.date.fromisoformat(v),ref,tit),2))

if __name__=='__main__':
    main()
