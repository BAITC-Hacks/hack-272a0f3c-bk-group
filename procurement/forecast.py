"""Fixed forecast rules and chronological out-of-sample evaluation."""
import numpy as np
import pandas as pd
from functools import lru_cache

METHODS = {'mean3': 'Среднее 3 месяца', 'seasonal_naive': 'Тот же месяц год назад',
           'adaptive': 'Сезонность + ограниченный тренд',
           'selected': 'Выбор с проверкой устойчивости', 'legacy_selected': 'Предыдущий алгоритм выбора'}

SELECTION_WINDOW = 12
MIN_SELECTION_FOLDS = 6
MIN_ERROR_REDUCTION = 0.20


def _ordinal(stamp):
    return stamp.year * 12 + stamp.month


def _observations(history):
    return {_ordinal(stamp): max(0., float(value))
            for stamp, value in zip(history.index, history.to_numpy())
            if pd.notna(stamp) and pd.notna(value) and np.isfinite(value)}


def predict(history, target, method='adaptive'):
    return _predict(_observations(history), _ordinal(target), method)


def _predict(observed, target_ordinal, method):
    # Filtering here also protects validation folds inside model selection.
    h = {month: value for month, value in observed.items() if month < target_ordinal}
    if not h:
        return None
    if method == 'selected':
        method = _selection_details(h, target_ordinal)['method']
    elif method == 'legacy_selected':
        method = _legacy_method(h, target_ordinal)
    if method not in ('mean3', 'seasonal_naive', 'adaptive'):
        raise ValueError('Неизвестный метод прогноза: ' + method)
    def mean_range(first,last):
        values=[h[k] for k in range(target_ordinal-first,target_ordinal-last+1) if k in h]
        return sum(value / len(values) for value in values) if values else None
    recent = mean_range(3,1)
    if recent is None:
        observed=[h[k] for k in sorted(h)[-3:]]
        recent=sum(value / len(observed) for value in observed)
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


def _legacy_method(observed, target):
    """Frozen old rule, retained only as a before/after evaluation baseline."""
    observed = {month: value for month, value in observed.items() if month < target}
    scores={method:[] for method in ('mean3','seasonal_naive','adaptive')}
    for cutoff in sorted(observed)[-4:]:
        if sum(month < cutoff for month in observed) < 6:
            continue
        actual=observed[cutoff]
        for method in scores:
            scores[method].append(abs(_predict(observed,cutoff,method)-actual))
    if not scores['mean3']:
        return 'mean3'
    return min(scores,key=lambda method:sum(scores[method])/len(scores[method]))


def _full_inputs(observed, target, method):
    lags = (12,) if method == 'seasonal_naive' else (1,2,3,4,5,6,12,13,14,15)
    return all(target - lag in observed for lag in lags)


def _selection_details(observed, target):
    observed = {month: value for month, value in observed.items() if month < target}
    recent = [observed[month] for month in range(target - SELECTION_WINDOW, target) if month in observed]
    # Descriptive diagnostics only; missing observations are not zero demand.
    if len(recent) < 6:
        regime = 'Недостаточно наблюдений'
    elif sum(value == 0 for value in recent) / len(recent) >= .25:
        regime = 'Есть месяцы с неположительными нетто-продажами'
    else:
        # Scale first so large but finite source values cannot overflow the
        # variance. This diagnostic must not bring down an entire report.
        scale = max(recent)
        scaled = [value / scale for value in recent]
        mean = sum(scaled) / len(scaled)
        cv = (sum((value - mean) ** 2 for value in scaled) / len(scaled)) ** .5 / mean
        regime = 'Переменный объём продаж' if cv >= .75 else 'Относительно ровный объём продаж'
    candidates, eligible = [], []
    for method in ('seasonal_naive', 'adaptive'):
        folds = []
        target_available = _full_inputs(observed, target, method)
        for cutoff in range(target - SELECTION_WINDOW, target):
            if cutoff not in observed or sum(month < cutoff for month in observed) < 6:
                continue
            if not _full_inputs(observed, cutoff, method):
                continue
            actual = observed[cutoff]
            folds.append((abs(_predict(observed, cutoff, 'mean3') - actual),
                          abs(_predict(observed, cutoff, method) - actual)))
        baseline = sum(pair[0] for pair in folds)
        error = sum(pair[1] for pair in folds)
        wins = sum(candidate < base for base, candidate in folds)
        reduction = 1 - error / baseline if baseline > 0 else None
        recent_not_worse = bool(folds) and sum(pair[1] for pair in folds[-3:]) <= sum(pair[0] for pair in folds[-3:])
        passed = (target_available and len(folds) >= MIN_SELECTION_FOLDS
                  and baseline > 0 and error <= (1 - MIN_ERROR_REDUCTION) * baseline
                  and wins * 3 >= len(folds) * 2 and recent_not_worse)
        diagnostic = {'method': method, 'target_inputs_available': target_available,
            'folds': len(folds), 'wins': wins, 'error_reduction': reduction,
            'mae': error / len(folds) if folds else None,
            'baseline_mae': baseline / len(folds) if folds else None,
            'recent_not_worse': recent_not_worse, 'eligible': passed}
        candidates.append(diagnostic)
        if passed:
            eligible.append((diagnostic['mae'], method, diagnostic))
    if eligible:
        _, method, chosen = min(eligible, key=lambda item: (item[0], item[1]))
        reason = (f"На {chosen['folds']} сопоставимых месяцах ошибка ниже среднего за три месяца "
                  f"на {chosen['error_reduction']:.0%}; побед {chosen['wins']} из {chosen['folds']}. "
                  'На последних трёх сравнениях суммарная ошибка не выше базовой.')
    else:
        method = 'mean3'
        if not observed:
            reason = 'Нет известных завершённых месяцев для расчёта прогноза.'
        elif not any(c['target_inputs_available'] and c['folds'] >= MIN_SELECTION_FOLDS for c in candidates):
            reason = ('Недостаточно сопоставимых сезонных проверок или входов для целевого месяца. '
                      'Используется среднее за три месяца.')
        else:
            reason = ('Сезонные модели не показали устойчивого преимущества: требуется снижение ошибки '
                      'минимум на 20%, победа в двух третях сравнений и отсутствие ухудшения на последних трёх. '
                      'Используется среднее за три месяца.')
    recent_three = sum(month in observed for month in range(target - 3, target))
    if observed and recent_three < 3:
        reason += (f' Из последних трёх месяцев известны {recent_three}; '
                   'пропуски не заменены нулями. При полном пропуске используется последнее известное среднее.')
    return {'method': method, 'reason': reason, 'regime': regime,
            'observed_last12': len(recent), 'window_months': SELECTION_WINDOW,
            'minimum_folds': MIN_SELECTION_FOLDS, 'candidates': candidates}


def selection_details(history, target):
    """Choose using past calendar months; fallback forecasts are not evidence."""
    return _selection_details(_observations(history), _ordinal(target))


def select_method(history, target):
    return selection_details(history, target)['method']


def future_daily(history, start, days, method='selected'):
    """Prorate monthly rates; never use the incomplete current month as training."""
    start = pd.Timestamp(start).normalize()
    cutoff = start.to_period('M').to_timestamp()
    train = history[history.index < cutoff]
    observed = _observations(train)
    dates = pd.date_range(start + pd.Timedelta(days=1), periods=days, freq='D')
    cache = {}
    result = []
    for day in dates:
        period = day.to_period('M').to_timestamp()
        if period not in cache:
            # Freeze the observed history at the calculation date for every future month.
            # Selection is checked for each target month against the SAME
            # frozen actuals, never against generated future predictions.
            cache[period] = _predict(observed, _ordinal(period), method)
        value = cache[period]
        result.append(np.nan if value is None else value / day.days_in_month)
    return pd.Series(result, index=dates, dtype=float)


def backtest(history, folds=6):
    records = []
    observed = _observations(history)
    for target in history.index[-folds:]:
        actual = history.loc[target]
        target_month = _ordinal(target)
        if pd.isna(actual) or not np.isfinite(actual) or sum(month < target_month for month in observed) < 6:
            continue
        for method in METHODS:
            forecast = _predict(observed, target_month, method)
            if forecast is not None and np.isfinite(forecast):
                records.append({'month': str(target.date()), 'method': method,
                    'actual': max(0, float(actual)), 'forecast': forecast,
                    'error': forecast - max(0, float(actual))})
    return records


def safety_from_errors(records, service, horizon):
    if not np.isfinite(service) or not 0 < service < 1 or not np.isfinite(horizon) or horizon <= 0:
        raise ValueError('Некорректный уровень сервиса или горизонт страхового запаса')
    errors = [max(0, -r['error']) for r in records
              if r['method'] == 'selected' and np.isfinite(r['error'])]
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
    if len(targets)!=folds or not np.isfinite(history.loc[targets].to_numpy(dtype=float)).all():
        return []
    observed = _observations(history)
    first = _ordinal(targets[0])
    if sum(month < first for month in observed)<6:
        return []
    base=_predict(observed,first,'mean3')
    results=[]
    for method in ['minmax', *METHODS]:
        stock=2*base
        pending=0.
        total=lost=inventory=ordered=0.
        for month in targets:
            stock+=pending
            target_month = _ordinal(month)
            past = {stamp: value for stamp, value in observed.items() if stamp < target_month}
            if method=='minmax':
                pending=max(0,2*base-stock) if stock<=base else 0.
            else:
                target=_predict(past,target_month,method)+_predict(past,target_month+1,method)
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
    return _evaluate_cached(tuple(history.index),tuple(None if pd.isna(v) or not np.isfinite(v) else float(v) for v in history))
