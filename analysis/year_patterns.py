import json
from pathlib import Path
import numpy as np
import pandas as pd

df=pd.read_pickle(Path(__file__).with_name('orders_cache.pkl'))
valid=(df['Документ'].astype(str).str.startswith('Расходная накладная ') & df.date.notna() & df['Количество'].gt(0) & df['Код'].notna() & df['Ед.'].notna() & df.date.dt.year.isin([2025,2026]))
d=df.loc[valid].copy()
d=d[(d.date.dt.month<9)|((d.date.dt.month==9)&(d.date.dt.day<=22))]
d['year']=d.date.dt.year
d['sku']=d['Код'].str.strip()
d['invoice']=d['Документ'].str.strip()
keys=['supplier','sku','Ед.']
x=d.groupby(keys+['year','invoice'],as_index=False).agg(qty=('Количество','sum'),date=('date','min'),name=('Номенклатура','first'))
counts=x.groupby(keys+['year']).size().unstack(fill_value=0)
common=counts[(counts[2025]>=20)&(counts[2026]>=20)].reset_index()[keys]
g=x.merge(common,on=keys,validate='many_to_one')
base=g.groupby(keys).qty.agg(median='median',q1=lambda a:a.quantile(.25),q3=lambda a:a.quantile(.75)).reset_index()
base['threshold']=np.maximum(3*base['median'],base.q3+3*(base.q3-base.q1))
g=g.merge(base,on=keys,validate='many_to_one')
g['large']=g.qty>g.threshold
g['month']=g.date.dt.month
out={'period':'Jan 1 through Sep 22, both years; monthly pattern comparisons use Jan-Aug only',
     'method':'Same pooled robust threshold per supplier/SKU/unit across both periods; at least 20 sales per SKU in EACH year',
     'data_grain':'invoice-SKU-unit; invoice numbers identify purchases, not buyers',
     'customer_identity_available':False,
     'limitation':'Repeated large lines show a recurring SKU-level order pattern only. They do not prove that one customer made the purchases.',
     'common_sku_unit_pairs':len(common),'years':{},'large_batch_repetition':{},'examples':[]}
for year in [2025,2026]:
    all_y=x[x.year==year]; a=g[g.year==year]
    inv=a.groupby(['supplier','invoice']).large.any()
    all_invoice_count=all_y[['supplier','invoice']].drop_duplicates().shape[0]
    out['years'][year]={'all_invoices':all_invoice_count,'assessable_invoices':len(inv),'flagged_invoices':int(inv.sum()),
      'flagged_pct_of_assessable':100*inv.mean(),'assessable_lines':len(a),'all_lines':len(all_y),
      'large_lines':int(a.large.sum()),'large_lines_pct':100*a.large.mean(),
      'by_unit':{u:{'total':float(h.qty.sum()),'large':float(h.loc[h.large,'qty'].sum()),'large_pct':100*h.loc[h.large,'qty'].sum()/h.qty.sum()} for u,h in a.groupby('Ед.')}}

lc=g.groupby(keys+['year']).large.sum().unstack(fill_value=0)
both=(lc[2025]>0)&(lc[2026]>0)
out['large_batch_repetition']={'large_skus_2025':int((lc[2025]>0).sum()),'large_skus_2026':int((lc[2026]>0).sum()),
 'large_in_both':int(both.sum()),'pct_2026_skus_with_previous_large':100*both.sum()/(lc[2026]>0).sum(),
 'at_least_5_large_each_year':int(((lc[2025]>=5)&(lc[2026]>=5)).sum())}
g=g.merge(both.rename('recurring_sku').reset_index(),on=keys,validate='many_to_one')
a=g[(g.year==2026)&g.large]
out['large_batch_repetition']['pct_2026_large_lines_from_skus_with_prior_large_lines']=100*a.recurring_sku.mean()

rank=lc[both].assign(score=lambda z:np.minimum(z[2025],z[2026])).sort_values('score',ascending=False)
for key,row in rank.head(6).iterrows():
    h=g[(g.supplier==key[0])&(g.sku==key[1])&(g['Ед.']==key[2])]
    item={'supplier':key[0],'sku':key[1],'unit':key[2],'name':h.name.iloc[0],
          'pooled_median':float(h['median'].iloc[0]),'threshold':float(h.threshold.iloc[0]),'years':{}}
    for year in [2025,2026]:
        y=h[h.year==year]; b=y[y.large]
        item['years'][year]={'sales':len(y),'median_qty':float(y.qty.median()),'large_orders':len(b),
            'large_median':float(b.qty.median()),'large_min':float(b.qty.min()),'large_max':float(b.qty.max()),
            'large_distinct_days':int(b.date.dt.normalize().nunique()),'large_distinct_months':int(b.month.nunique()),
            'large_monthly_counts':b.groupby('month').size().to_dict(),
            'top_large_sizes':{str(k):int(v) for k,v in b.qty.value_counts().head(5).items()}}
    out['examples'].append(item)

monthly=g[g.month<=8].groupby(keys+['year','month']).qty.sum().unstack('month',fill_value=0).reindex(columns=range(1,9),fill_value=0)
cors=[]
for key in common.itertuples(index=False,name=None):
    try:
        m=monthly.loc[key]; a=m.loc[2025].to_numpy(dtype=float); b=m.loc[2026].to_numpy(dtype=float)
    except KeyError: continue
    if (a>0).sum()<6 or (b>0).sum()<6 or a.std()==0 or b.std()==0: continue
    corr=float(np.corrcoef(a,b)[0,1]); cors.append({'supplier':key[0],'sku':key[1],'unit':key[2],'corr':corr,'2025':a.tolist(),'2026':b.tolist()})
out['monthly_similarity']={'eligible_skus':len(cors),'median_correlation':float(np.median([i['corr'] for i in cors])),
 'pct_corr_ge_07':100*sum(i['corr']>=.7 for i in cors)/len(cors),
 'examples':sorted(cors,key=lambda a:a['corr'],reverse=True)[:4]}
out['monthly_by_supplier_pieces']={}
for supplier,h in g[(g.month<=8)&g['Ед.'].eq('шт')].groupby('supplier'):
    p=h.groupby(['year','month']).qty.sum().unstack().reindex(columns=range(1,9),fill_value=0)
    out['monthly_by_supplier_pieces'][supplier]={'2025':p.loc[2025].tolist(),'2026':p.loc[2026].tolist(),'corr':float(p.loc[2025].corr(p.loc[2026]))}

assert all(v['flagged_invoices']<=v['assessable_invoices']<=v['all_invoices'] for v in out['years'].values())
print(json.dumps(out,ensure_ascii=False,indent=2))
