"""Chronological evaluation of production forecasts; no fabricated purchases."""
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from .forecast import backtest, simulate_policies, METHODS, SELECTION_WINDOW, MIN_SELECTION_FOLDS, MIN_ERROR_REDUCTION


def validate_period(start, end, as_of):
    start, end = pd.Period(start, freq='M'), pd.Period(end, freq='M')
    if start > end or end >= pd.Timestamp(as_of).to_period('M'):
        raise ValueError('Выберите завершённые месяцы в хронологическом порядке.')
    if end.ordinal - start.ordinal > 59:
        raise ValueError('Один прогон может охватывать до 60 месяцев.')
    return start.to_timestamp(), end.to_timestamp()


def aggregate(frame, fields):
    result = []
    if frame.empty:
        return result
    for key, group in frame.groupby(fields, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        actual = float(group.actual.sum())
        absolute = float(group.error.abs().sum())
        result.append({**dict(zip(fields,key)), 'n':len(group),
            'sku_count':len(group[['supplier','sku']].drop_duplicates()),
            'actual':actual, 'forecast':float(group.forecast.sum()),
            'wape':absolute/actual if actual else None,
            'mae':absolute/len(group),
            'bias_pct':float(group.error.sum())/actual if actual else None,
            'underforecast':float((-group.error).clip(lower=0).sum()),
            'overforecast':float(group.error.clip(lower=0).sum())})
    return result


def run_history(data, start, end, progress=None):
    start, end = validate_period(start,end,data.as_of)
    months = pd.date_range(start,end,freq='MS')
    unit_counts = data.catalog.groupby(['supplier','sku']).size().to_dict()
    catalog = {(p['supplier'],p['sku']):p for p in data.catalog.to_dict('records')
               if p['unit'] and unit_counts[(p['supplier'],p['sku'])] == 1}
    observations, simulations = [], []
    coverage = Counter(unknown_actual=0, insufficient_history=0, evaluated=0,
                       missing_unit_or_conflict=0, duplicate_month_skus=0,
                       negative_actual=0)
    groups = list(data.monthly.groupby(['supplier','sku'],sort=True))
    year_ranges = {int(year):dates for year,dates in pd.Series(months,index=months).groupby(months.year)}
    for index,(key,group) in enumerate(groups):
        if progress and index % 100 == 0:
            progress(f'Историческая проверка: {index} / {len(groups)} товаров')
        product = catalog.get(key)
        if product is None:
            coverage['missing_unit_or_conflict'] += 1
            continue
        group = group[group.month <= end]
        if group.month.duplicated().any():
            coverage['duplicate_month_skus'] += 1
            continue
        if group.empty:
            coverage['unknown_actual'] += len(months)
            continue
        # Retain unknown months; no zero-fill and no incomplete current month.
        first = min(group.month.min(),start)
        history = group.set_index('month').quantity.astype(float).reindex(pd.date_range(first,end,freq='MS'))
        for month in months:
            actual = history.loc[month]
            if pd.isna(actual):
                coverage['unknown_actual'] += 1
            elif history[history.index < month].notna().sum() < 6:
                coverage['insufficient_history'] += 1
            else:
                coverage['evaluated'] += 1
                coverage['negative_actual'] += int(actual < 0)
        for record in backtest(history,folds=len(months)):
            observations.append({**record,'year':int(record['month'][:4]),
                'supplier':key[0],'sku':key[1],'unit':product['unit'],'name':product['name']})
        for year,dates in year_ranges.items():
            replay = simulate_policies(history.loc[:dates.iloc[-1]],folds=len(dates))
            simulations.extend({**record,'year':year,'supplier':key[0],'sku':key[1],
                                'unit':product['unit']} for record in replay)
    frame = pd.DataFrame(observations)
    if frame.empty:
        raise ValueError('Недостаточно наблюдений: нужны известные продажи и минимум шесть предшествующих месяцев.')
    selected = frame[frame.method.eq('selected')]
    baseline = frame[frame.method.eq('mean3')][['supplier','sku','month','forecast','error']]
    paired = selected.merge(baseline,on=['supplier','sku','month'],suffixes=('','_mean3'),validate='one_to_one')
    sku_comparisons = []
    for (year,unit), group in paired.groupby(['year','unit']):
        errors = group.assign(selected_abs=group.error.abs(),baseline_abs=group.error_mean3.abs())
        errors = errors.groupby(['supplier','sku'])[['selected_abs','baseline_abs']].sum()
        difference = errors.selected_abs - errors.baseline_abs
        sku_comparisons.append({'year':int(year),'unit':unit,'sku_count':len(errors),
            'better':int((difference < -1e-8).sum()),'equal':int((difference.abs() <= 1e-8).sum()),
            'worse':int((difference > 1e-8).sum())})
    examples = []
    for (_, _),group in paired.groupby(['year','unit']):
        worst = group.assign(absolute_error=group.error.abs()).nlargest(5,'absolute_error')
        examples.extend(worst[['year','month','supplier','sku','unit','name','actual',
                              'forecast','forecast_mean3','absolute_error']].to_dict('records'))
    sim_frame = pd.DataFrame(simulations)
    sim_summary = []
    if not sim_frame.empty:
        for (year,unit,method),group in sim_frame.groupby(['year','unit','method']):
            demand = float(group.demand.sum())
            sim_summary.append({'year':int(year),'unit':unit,'method':method,
                'sku_count':len(group),'months':int(group.months.iloc[0]),'demand':demand,
                'fill_rate':1-float(group.lost.sum())/demand if demand else None,
                'lost_units':float(group.lost.sum()),'mean_inventory':float(group.mean_inventory.sum())})
    return {'created_at':datetime.now(timezone.utc).isoformat(),'dataset':data.fingerprint,
        'start':str(start.date())[:7],'end':str(end.date())[:7],
        'coverage':dict(coverage),'methods':METHODS,
        'selection_policy':{'window_months':SELECTION_WINDOW,'minimum_folds':MIN_SELECTION_FOLDS,
            'minimum_error_reduction':MIN_ERROR_REDUCTION,'minimum_win_share':2/3,
            'last_three_not_worse':True,'complete_method_inputs_required':True},
        'metrics':aggregate(frame,['year','unit','method']),
        'supplier_metrics':aggregate(frame,['year','supplier','unit','method']),
        'monthly_metrics':aggregate(frame,['month','unit','method']),
        'sku_comparisons':sku_comparisons,'examples':examples,'simulations':sim_summary,
        'historical_orders_evaluable':False,
        'limitations':[
            'Оценивается прогноз продаж, а не соответствие фактическим заказам поставщикам: полной истории закупок и исторических резервов/открытых поставок нет.',
            'В каждом месяце используются только предыдущие наблюдения. Для выбора метода проверяемый месяц недоступен.',
            'По умолчанию используется среднее за три месяца. Сезонная модель допускается после минимум шести настоящих сравнений за последние 12 календарных месяцев, при снижении ошибки минимум на 20%, победе в двух третях сравнений и без ухудшения на последних трёх. Подмена сезонного расчёта средним не считается подтверждением сезонности.',
            'Предыдущий алгоритм сохранён для сравнения: он выбирал модель по четырём прошлым наблюдениям без проверки полноты сезонных входов.',
            'Пустые месяцы остаются неизвестными. Отрицательные нетто-продажи ограничены нулём, как в рабочем прогнозе.',
            'Симуляция: начальный запас равен двум средним месяцам; срок поставки и пересмотр — по месяцу. Реальные поступления, резервы, упаковки, страховой запас и затраты не моделируются.',
            'Симуляция включает только товары с известными продажами во всех месяцах проверяемого периода года. Это отдельная, более полная выборка.',
            'Проверяются месячные модели полного ряда. Календарные даты заказа, ежедневная траектория и устойчивый сценарий этим прогоном не валидированы.',
            'Архивы получены позднее проверяемых периодов: даты исправления старых записей неизвестны. Проверка предполагает доступность этих исторических значений на соответствующую дату.'
        ]}, observations
