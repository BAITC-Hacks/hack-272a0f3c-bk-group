"""Fixed forecast rules and chronological out-of-sample evaluation."""
import numpy as np
import pandas as pd
from functools import lru_cache

METHODS = {'mean3': 'Среднее 3 месяца', 'seasonal_naive': 'Тот же месяц год назад',
           'adaptive': 'Сезонность + ограниченный тренд', 'selected':'Выбор по прошлым ошибкам'}


def predict(history, target, method='adaptive'):
    if method == 'selected':
        method=select_method(history,target)
    # Small monthly series: arithmetic on month ordinals avoids thousands of
    # pandas reindex/clip allocations during chronological validation.
    target_ordinal = target.year * 12 + target.month
    h = {stamp.year * 12 + stamp.month: max(0.,float(value))
         for stamp,value in zip(history.index,history.to_numpy())
         if stamp < target and pd.notna(value)}
    if not h:
        return None
    def mean_range(first,last):
        values=[h[k] for k in range(target_ordinal-first,target_ordinal-last+1) if k in h]
        return sum(values)/len(values) if values else None
    recent = mean_range(3,1)
    if recent is None:
        observed=[h[k] for k in sorted(h)[-3:]]
        recent=sum(observed)/len(observed)
    prior_year = h.get(target_ordinal-12)
    if method == 'mean3':
        return float(recent)
    if method == 'seasonal_naive':
        return float(prior_year if prior_year is not None else recent)
    older = mean_range(6,4)
    trend = min(1.5,max(0.5,recent / older)) if older is not None and older > 0 else 1.0
    last_year_recent = mean_range(15,13)
    growth = min(1.5,max(0.5,recent / last_year_recent)) if last_year_recent is not None and last_year_recent > 0 else 1.0
    seasonal = prior_year * growth if prior_year is not None else recent
    return float(max(0, 0.5 * recent * trend + 0.5 * seasonal))


def select_method(history,target):
    observed=history[(history.index<target)&history.notna()]
    scores={method:[] for method in ('mean3','seasonal_naive','adaptive')}
    for cutoff in observed.index[-4:]:
        prior=history[history.index<cutoff]
        if prior.notna().sum()<6:
            continue
        actual=max(0,float(observed.loc[cutoff]))
        for method in scores:
            scores[method].append(abs(predict(prior,cutoff,method)-actual))
    if not scores['mean3']:
        return 'mean3'
    return min(scores,key=lambda method:sum(scores[method])/len(scores[method]))


def future_daily(history, start, days, method='selected'):
    """Prorate monthly rates; never use the incomplete current month as training."""
    start = pd.Timestamp(start).normalize()
    cutoff = start.to_period('M').to_timestamp()
    train = history[history.index < cutoff]
    if method == 'selected':
        method=select_method(train,cutoff)
    dates = pd.date_range(start + pd.Timedelta(days=1), periods=days, freq='D')
    cache = {}
    result = []
    for day in dates:
        period = day.to_period('M').to_timestamp()
        if period not in cache:
            # Freeze the observed history at the calculation date for every future month.
            cache[period] = predict(train, period, method)
        value = cache[period]
        result.append(np.nan if value is None else value / day.days_in_month)
    return pd.Series(result, index=dates, dtype=float)


def backtest(history, folds=6):
    records = []
    for target in history.index[-folds:]:
        actual = history.loc[target]
        train = history[history.index < target]
        if pd.isna(actual) or train.notna().sum() < 6:
            continue
        for method in METHODS:
            forecast = predict(train, target, method)
            if forecast is not None:
                records.append({'month': str(target.date()), 'method': method,
                    'actual': max(0, float(actual)), 'forecast': forecast,
                    'error': forecast - max(0, float(actual))})
    return records


def safety_from_errors(records, service, horizon):
    errors = [max(0, -r['error']) for r in records if r['method'] == 'selected']
    if len(errors) < 3:
        return None
    # Conservative linear scaling; this is a small-sample empirical buffer,
    # not a calibrated probability guarantee for an arbitrary lead time.
    return float(np.quantile(errors, service) * horizon / 30.4375)


def simulate_policies(history, folds=6):
    """Counterfactual monthly policy replay, never presented as actual stockout.

    Shared initial stock = 2x prior three-month mean; lead and review are each
    one month; no reserves/real receipts/costs. Unknown actuals exclude the SKU.
    Orders at the start of t use history strictly before t and arrive at t+1.
    """
    targets=history.index[-folds:]
    if len(targets)!=folds or history.loc[targets].isna().any():
        return []
    training=history[history.index<targets[0]]
    if training.notna().sum()<6:
        return []
    base=predict(training,targets[0],'mean3')
    results=[]
    for method in ['minmax','mean3','seasonal_naive','adaptive','selected']:
        stock=2*base
        pending=0.
        total=lost=inventory=ordered=0.
        for month in targets:
            stock+=pending
            past=history[history.index<month]
            if method=='minmax':
                pending=max(0,2*base-stock) if stock<=base else 0.
            else:
                target=predict(past,month,method)+predict(past,month+pd.DateOffset(months=1),method)
                pending=max(0,target-stock)
            actual=max(0,float(history.loc[month]))
            lost+=max(0,actual-stock)
            stock=max(0,stock-actual)
            total+=actual
            inventory+=stock
            ordered+=pending
        results.append({'method':method,'demand':total,'lost':lost,
            'mean_inventory':inventory/folds,'ordered':ordered,'months':folds})
    return results


@lru_cache(maxsize=6000)
def _evaluate_cached(months, quantities):
    history=pd.Series(quantities,index=pd.DatetimeIndex(months),dtype=float)
    return backtest(history),simulate_policies(history)


def evaluate_history(history):
    # Changing procurement lead time does not invalidate monthly backtests.
    # Missing values are explicit None so cache keys are stable and not NaN.
    return _evaluate_cached(tuple(history.index),tuple(None if pd.isna(v) else float(v) for v in history))
