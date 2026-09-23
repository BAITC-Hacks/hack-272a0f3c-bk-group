"""Combine source provenance, forecasts and date-aware purchasing gates."""
from datetime import datetime, timezone
from decimal import Decimal
import math
import numpy as np
import pandas as pd

from analysis.orders_analysis import build_invoice_lines, classify_invoice_line_patterns
from analysis.procurement_logic import round_purchase_quantity
from .forecast import evaluate_history, future_daily, safety_from_errors, selection_details, METHODS

DEFAULTS = {'lead_days': 30, 'review_days': 14, 'service': 0.9,
    'parameters_confirmed': False, 'blank_months_zero': False,
    'constraints_confirmed': False, 'incoming_confirmed': False,
    'reserve_policy_confirmed': False, 'scenario': 'full', 'max_snapshot_age': 2}


def validate_settings(values):
    settings = {**DEFAULTS, **values}
    for field, low, high in [('lead_days',1,120), ('review_days',1,60), ('max_snapshot_age',0,31)]:
        value = float(settings[field])
        if not math.isfinite(value) or value != int(value) or not low <= value <= high:
            raise ValueError(f'Некорректный параметр {field}: диапазон {low}–{high}')
        settings[field] = int(value)
    settings['service'] = float(settings['service'])
    if not 0.5 <= settings['service'] <= 0.99:
        raise ValueError('Уровень сервиса должен быть от 0.50 до 0.99')
    for key in ('parameters_confirmed','blank_months_zero','constraints_confirmed','incoming_confirmed','reserve_policy_confirmed'):
        if not isinstance(settings[key], bool):
            raise ValueError(f'{key}: требуется логическое значение')
    if settings['scenario'] not in ('full','robust'):
        raise ValueError('Неизвестный сценарий')
    return settings


def prepare_lines(data):
    _, lines = build_invoice_lines(data.sales[data.sales.date <= data.as_of + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)])
    return lines


def classify(data):
    return classify_invoice_line_patterns(prepare_lines(data))


def plan_inventory(daily, free_stock, shipments, safety, lead, pack, moq=0):
    """Project every date so late receipts cannot conceal an early shortage."""
    if daily.empty or not isinstance(daily.index, pd.DatetimeIndex):
        raise ValueError('Для расчёта требуется непустой ежедневный прогноз')
    expected = pd.date_range(daily.index[0].normalize(), periods=len(daily), freq='D')
    if not daily.index.equals(expected):
        raise ValueError('Даты прогноза должны идти подряд, без повторов и пропусков')
    if not np.isfinite(daily.to_numpy(dtype=float)).all() or daily.lt(0).any():
        raise ValueError('Ежедневный спрос должен быть конечным неотрицательным числом')
    if not math.isfinite(float(free_stock)) or not math.isfinite(float(safety)) or safety < 0:
        raise ValueError('Остаток и страховой запас должны быть конечными числами; запас неотрицательным')
    if not math.isfinite(float(lead)) or lead < 1 or int(lead) != lead:
        raise ValueError('Срок поставки должен быть целым положительным числом дней')
    # Validate purchasing constraints even when no order is necessary.
    round_purchase_quantity(0, pack, moq)
    as_of = daily.index[0] - pd.Timedelta(days=1)
    receipts = {}
    for shipment in shipments:
        arrival = pd.Timestamp(shipment['arrival'])
        quantity = float(shipment['quantity'])
        if pd.isna(arrival) or not math.isfinite(quantity) or quantity < 0:
            raise ValueError('У поставки должна быть известная дата и конечное неотрицательное количество')
        arrival = arrival.normalize()
        if arrival <= as_of:
            raise ValueError('Поставка на дату складского среза или раньше требует сверки')
        if arrival <= daily.index[-1]:
            receipts[arrival] = receipts.get(arrival, Decimal(0)) + Decimal(str(quantity))
    # Keep full decimal stock arithmetic. Rounded chart values must never
    # become inputs to purchasing (metres/kg may use fractions below .001).
    stock, safety_value = Decimal(str(float(free_stock))), Decimal(str(float(safety)))
    path, balances = [], []
    breach = as_of if stock < safety_value else None
    shortage = as_of if stock < 0 else None
    for day, demand in daily.items():
        stock += receipts.get(day, Decimal(0))
        stock -= Decimal(str(float(demand)))
        if stock < safety_value and breach is None:
            breach = day
        if stock < 0 and shortage is None:
            shortage = day
        balances.append((day, stock))
        path.append({'date': str(day.date()), 'without_order': float(stock)})
    deadline = breach - pd.Timedelta(days=lead) if breach is not None else None
    order_day = max(deadline, as_of) if deadline is not None else as_of
    arrival = order_day + pd.Timedelta(days=lead)
    after_arrival = [value for day, value in balances if day >= arrival]
    raw = float(max(Decimal(0), safety_value - min(after_arrival))) if after_arrival else 0
    quantity = round_purchase_quantity(raw, pack, moq)
    for row, (day, value) in zip(path, balances):
        row['with_order'] = float(value + (Decimal(str(quantity)) if day >= arrival else Decimal(0)))
    return {'raw_required': raw, 'recommended_quantity': quantity, 'trajectory': path,
        'order_date': str(max(deadline, as_of).date()) if deadline is not None and quantity>0 else None,
        'latest_safe_order_date': str(deadline.date()) if deadline is not None else None,
        'shortage_date': str(shortage.date()) if shortage is not None else None,
        'early_shortage': bool(shortage is not None and shortage < arrival),
        'arrival_date': str(arrival.date()) if quantity>0 else None,
        'incoming_in_horizon': float(sum(receipts.values(), Decimal(0)))}


def grouped(frame, fields):
    return {key: value for key, value in frame.groupby(fields, sort=False)} if len(frame) else {}


def build_report(data, lines, values=None, overrides=None, progress=None):
    settings = validate_settings(values or {})
    overrides = overrides or {}
    as_of = data.as_of
    completed = as_of.to_period('M').to_timestamp() - pd.DateOffset(months=1)
    horizon = settings['lead_days'] + settings['review_days']
    monthly = grouped(data.monthly, ['supplier','sku'])
    balances = grouped(data.balances, ['supplier','sku'])
    constraints = grouped(data.constraints, ['supplier','sku'])
    snapshots = grouped(data.snapshots, ['supplier','sku'])
    shipments = grouped(data.shipments, ['supplier','sku'])
    sales = grouped(data.sales, ['supplier','Код','Ед.'])
    units = data.catalog.groupby(['supplier','sku']).size().to_dict()
    batch_rules = {}
    if settings['constraints_confirmed']:
        for product in data.catalog.to_dict('records'):
            key = (product['supplier'], product['sku'])
            if units[key] != 1 or not product['unit']:
                continue
            con = constraints.get(key, pd.DataFrame())
            pack = number_or_none(con.value.iloc[0]) if len(con) == 1 else None
            override = overrides.get(product['key'], {})
            if override.get('dataset') != data.fingerprint:
                override = {}
            pack = override.get('pack_multiple', pack)
            batch_rules[(*key, product['unit'])] = {
                'pack_multiple': pack if pack is not None and pack > 0 else 0,
                'minimum_order_quantity': override.get('minimum_order_quantity', 0)}
    if len(lines):
        if progress:
            progress('Проверяем повторяемость партий и условия упаковки…')
        lines = classify_invoice_line_patterns(lines, batch_rules)
    else:
        lines = lines.assign(pattern_class=pd.Series(dtype=str))
    patterns = grouped(lines, ['supplier','sku','Ед.'])
    result, all_tests, all_simulations = [], [], []
    for product_index,product in enumerate(data.catalog.to_dict('records')):
        if progress and product_index % 100 == 0:
            progress(f'Расчет SKU: {product_index} / {len(data.catalog)}')
        supplier, sku, unit = product['supplier'], product['sku'], product['unit']
        key = (supplier,sku)
        blocks, warnings, sources = [], [], {}
        if product.get('catalog_sources'):
            sources['catalog'] = '; '.join(product['catalog_sources'])
        override = overrides.get(product['key'], {})
        if override.get('dataset') != data.fingerprint:
            override = {}
        if units[key] > 1:
            blocks.append('Несколько единиц измерения для SKU: требуется конверсия')
        if not unit:
            blocks.append('Не указана единица измерения: добавьте её в исходные данные')
        m = monthly.get(key, pd.DataFrame())
        history = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        if len(m):
            if m.month.duplicated().any():
                blocks.append('Дубли SKU в помесячной книге')
            else:
                history = m.set_index('month').quantity.sort_index().astype(float)
                sources['monthly'] = m.source.iloc[0]
                if history.index.isna().any():
                    blocks.append('Неизвестная дата в помесячной истории')
                    history = history[history.index.notna()]
                if np.isinf(history.to_numpy()).any():
                    blocks.append('Некорректные бесконечные значения в помесячной истории')
                    history = history.replace([np.inf, -np.inf], np.nan)
        else:
            blocks.append('Нет помесячной истории')
        if len(history):
            history = history.reindex(pd.date_range(history.index.min(), completed, freq='MS'))
        missing_months = int(history.isna().sum())
        if settings['blank_months_zero']:
            history = history.fillna(0)
            warnings.append('Пустые месяцы приняты за 0 по настройке закупщика')
        elif history.tail(6).isna().any():
            blocks.append('Пропуски в последних 6 месяцах: подтвердите их смысл')
        if history.notna().sum() < 6:
            blocks.append('Менее 6 наблюдаемых завершенных месяцев')
        if history.lt(0).any():
            warnings.append('Отрицательные месячные нетто-продажи ограничены нулем только для прогноза')
        p = patterns.get((supplier,sku,unit), pd.DataFrame())
        s = sales.get((supplier,sku,unit), pd.DataFrame())
        if not len(p):
            warnings.append('Нет детализации накладных для анализа партий; используется месячная история')
        elif not settings['constraints_confirmed']:
            warnings.append('Анализ партий предварительный: подтвердите упаковку и минимальную партию')
        sale_identity = [column for column in s.columns if column != 'source']
        if len(s) and s.duplicated(subset=sale_identity).any():
            blocks.append('Полные дубли продаж: требуется сверка, автоматическое удаление отключено')
        negative_rows = int(s['Количество'].lt(0).sum()) if len(s) else 0
        if negative_rows:
            warnings.append(f'Возвраты/корректировки: {negative_rows} отрицательных строк; прогноз использует месячные нетто-продажи')
        adjusted = history.copy()
        scenario_available = True
        candidate_count = 0
        recurring_count = 0
        examples = []
        if len(p):
            candidate = p[p.pattern_class.eq('candidate_one_off')].copy()
            candidate_count = len(candidate)
            recurring_count = int(p.pattern_class.eq('repeating_wholesale_batch').sum())
            for period, rows in candidate.groupby(candidate.date.dt.to_period('M')):
                month = period.to_timestamp()
                if month not in history.index or pd.isna(history.loc[month]):
                    continue
                # Adjust only where transaction net total reconciles to the monthly source.
                net = s.loc[s.date.dt.to_period('M').eq(period), 'Количество'].sum()
                if not np.isclose(net, history.loc[month], atol=0.01, rtol=0):
                    scenario_available = False
                    continue
                adjusted.loc[month] = max(0, history.loc[month] - (rows.qty - rows.threshold).clip(lower=0).sum())
            display = p.sort_values(['large','qty'], ascending=[False,False]).head(12)
            examples = display[['invoice','date','qty','median','threshold','pattern_class','explanation']].to_dict('records')
        if not scenario_available:
            warnings.append('Часть кандидатов не согласуется с месячным нетто-итогом; устойчивый сценарий не применим')
            adjusted = history.copy()
        if settings['scenario'] == 'robust' and not scenario_available:
            blocks.append('Устойчивый сценарий требует сверки накладных с месячными продажами')
        selected_history = adjusted if settings['scenario'] == 'robust' else history
        tests, simulations_for_sku = evaluate_history(history)
        for sim in (simulations_for_sku if unit and units[key] == 1 else []):
            all_simulations.append({**sim,'unit':unit,'sku':sku,'supplier':supplier})
        for test in (tests if unit and units[key] == 1 else []):
            all_tests.append({**test, 'supplier':supplier, 'sku':sku, 'unit':unit})
        safety = safety_from_errors(tests, settings['service'], horizon)
        if safety is None:
            blocks.append('Недостаточно ошибок прогноза для страхового запаса')
        daily = future_daily(selected_history, as_of, horizon)
        full_daily = daily if settings['scenario']=='full' else future_daily(history, as_of, horizon)
        robust_daily = full_daily if adjusted.equals(history) else (daily if settings['scenario']=='robust' else future_daily(adjusted, as_of, horizon))
        if not np.isfinite(daily.to_numpy()).all():
            blocks.append('Нет надежного прогноза')
        snap = snapshots.get(key, pd.DataFrame())
        free, on_hand, reserved, snapshot_date = None, None, None, None
        if len(snap) == 1:
            row = snap.iloc[0]
            on_hand, reserved = number_or_none(row.on_hand), number_or_none(row.reserved)
            snapshot_date = row.snapshot_date
            sources['snapshot'] = row.source
            if on_hand is not None and reserved is not None:
                free = float(on_hand - reserved)
                if 'on_hand' not in override and pd.notna(row.free_stock) and not np.isclose(free, row.free_stock, atol=0.01, rtol=0):
                    blocks.append('Свободный остаток не равен остатку минус резерв')
        elif len(snap) > 1:
            blocks.append('Дубли SKU в текущем складском срезе')
        if override.get('on_hand') is not None and override.get('reserved') is not None:
            on_hand, reserved = number_or_none(override['on_hand']), number_or_none(override['reserved'])
            free = float(on_hand - reserved) if on_hand is not None and reserved is not None else None
            snapshot_date = pd.Timestamp(override['snapshot_date'])
            sources['snapshot'] = 'Ручной ввод: ' + override['reason']
        if free is None:
            blocks.append('Нет текущего остатка и резерва')
        if reserved is not None and reserved < 0:
            blocks.append('Отрицательный резерв: требуется сверка складского среза')
        if free is not None and free < 0:
            warnings.append('Резерв превышает остаток: уже есть нехватка для подтверждённых обязательств')
        if snapshot_date is None or pd.isna(snapshot_date):
            blocks.append('Неизвестна дата текущего остатка')
        elif not 0 <= (as_of - snapshot_date).days <= settings['max_snapshot_age']:
            blocks.append('Складской срез устарел или датирован будущим')
        b = balances.get(key, pd.DataFrame())
        opening = b[b.month.eq(as_of.to_period('M').to_timestamp())] if len(b) else b
        opening_value = number_or_none(opening.quantity.iloc[0]) if len(opening)==1 else None
        if opening_value is not None and opening_value <= 0:
            warnings.append('Сигнал дефицита: неположительный начальный остаток месяца; длительность stockout неизвестна')
        con = constraints.get(key, pd.DataFrame())
        pack = number_or_none(con.value.iloc[0]) if len(con)==1 else None
        if len(con)==1:
            sources['constraint'] = con.source.iloc[0] + ' / ' + con.field.iloc[0]
        if override.get('pack_multiple') is not None:
            pack = override['pack_multiple']
            sources['constraint'] = 'Ручной ввод: ' + override['reason']
        if pack is None or pack <= 0:
            blocks.append('Нет положительной кратности в единице продажи')
        if not settings['constraints_confirmed']:
            blocks.append('Подтвердите смысл кратности и единицы закупки')
        if not settings['parameters_confirmed']:
            blocks.append('Подтвердите срок поставки, период пересмотра и уровень сервиса')
        if not settings['reserve_policy_confirmed']:
            blocks.append('Подтвердите, что резерв — дополнительный спрос вне прогноза')
        ship = shipments.get(key, pd.DataFrame())
        shipment_identity = [field for field in ('supplier', 'sku', 'arrival', 'quantity', 'shipment', 'snapshot_date')
                             if field in ship.columns]
        if len(ship) and ship.duplicated(subset=shipment_identity).any():
            blocks.append('Повторяющиеся строки поставок: подтвердите, что это отдельные партии')
        incoming = []
        all_shipments = []
        for r in ship.to_dict('records'):
            all_shipments.append(r)
            arrival = pd.Timestamp(r['arrival'])
            quantity = number_or_none(r['quantity'])
            if quantity is None or quantity < 0:
                blocks.append('Некорректное количество в поставке: требуется сверка')
                continue
            if pd.notna(arrival):
                arrival = arrival.normalize()
            if pd.isna(arrival) or arrival <= as_of:
                blocks.append('Дата поставки неизвестна или не позже складского среза: требуется сверка')
            elif arrival <= as_of + pd.Timedelta(days=horizon):
                incoming.append({'arrival':arrival,'quantity':quantity})
        if not settings['incoming_confirmed']:
            blocks.append('Подтвердите полноту поставок, их даты и единицы')
        calculation = None
        if free is not None and safety is not None and np.isfinite(daily.to_numpy()).all() and pack is not None and pack > 0:
            try:
                calculation = plan_inventory(daily, free, incoming, safety, settings['lead_days'], pack,
                                             override.get('minimum_order_quantity',0) or 0)
            except (ValueError, OverflowError) as error:
                blocks.append('Невозможно рассчитать закупку: ' + str(error))
        if calculation and calculation['early_shortage']:
            warnings.append('Дефицит раньше новой поставки: нужны ускорение или перемещение')
        ready = not blocks and calculation is not None
        status = 'blocked' if not ready else ('urgent' if calculation['early_shortage'] else ('order' if calculation['recommended_quantity'] > 0 else 'covered'))
        forecast_schedule = []
        for period in daily.index.to_period('M').unique():
            details = selection_details(selected_history, period.to_timestamp())
            forecast_schedule.append({'month':str(period), **details, 'label':METHODS[details['method']]})
        result.append({**product, 'status':status, 'blocks':list(dict.fromkeys(blocks)), 'warnings':warnings,
            'forecast_method':forecast_schedule[0]['label'],
            'forecast_selection':forecast_schedule[0], 'forecast_schedule':forecast_schedule,
            'forecast_horizon':number_or_none(daily.sum(min_count=1)),
            'full_forecast':number_or_none(full_daily.sum(min_count=1)),
            'robust_forecast':number_or_none(robust_daily.sum(min_count=1)) if scenario_available else None,
            'candidate_count':candidate_count, 'recurring_count':recurring_count, 'negative_rows':negative_rows,
            'missing_months':missing_months, 'on_hand':number_or_none(on_hand), 'reserved':number_or_none(reserved),
            'free_stock':number_or_none(free), 'snapshot_date':snapshot_date, 'opening_balance':opening_value,
            'pack_multiple':pack, 'safety_stock':safety, 'ready':ready,
            'quantity':calculation['recommended_quantity'] if ready else None,
            'calculation':calculation, 'sources':sources, 'shipments':all_shipments, 'examples':examples,
            'history':[{'month':str(k.date()),'net':number_or_none(v),'robust':number_or_none(adjusted.loc[k]) if scenario_available else None} for k,v in history.items()],
            'backtest':tests, 'override':override or None})
    metrics = []
    t = pd.DataFrame(all_tests)
    if len(t):
        for (unit, method), group in t.groupby(['unit','method']):
            denominator = group.actual.abs().sum()
            metrics.append({'unit':unit, 'method':method, 'label':METHODS[method], 'n':len(group),
                'wape':float(group.error.abs().sum()/denominator) if denominator else None,
                'mae':float(group.error.abs().mean()), 'bias':float(group.error.mean())})
    simulations=[]
    for (unit,method),group in grouped(pd.DataFrame(all_simulations),['unit','method']).items():
        demand=group.demand.sum()
        simulations.append({'unit':unit,'method':method,'sku_count':len(group),
            'fill_rate':float(1-group.lost.sum()/demand) if demand else None,
            'lost_units':float(group.lost.sum()),'mean_inventory':float(group.mean_inventory.sum())})
    return {'created_at':datetime.now(timezone.utc).isoformat(), 'as_of':str(as_of.date()),
        'dataset':data.fingerprint, 'settings':settings, 'sources':data.sources, 'rows':result, 'metrics':metrics,
        'simulations':simulations,
        'summary':{'sku_count':len(result), 'ready':sum(r['ready'] for r in result),
            'blocked':sum(not r['ready'] for r in result), 'to_order':sum(r['ready'] and r['quantity']>0 for r in result),
            'candidate_lines':int(lines.pattern_class.eq('candidate_one_off').sum()),
            'recurring_lines':int(lines.pattern_class.eq('repeating_wholesale_batch').sum()),
            'invalid_sales_rows':int((data.sales.date.isna() | data.sales['Количество'].isna() | data.sales['Код'].eq('')).sum()),
            'duplicate_sales_rows':int(data.sales.duplicated(subset=[column for column in data.sales.columns if column != 'source']).sum())},
        'limitations':['Прогноз основан на отгрузках. Неудовлетворённый спрос в него не включён.',
            f'Незавершённый месяц ({as_of:%Y-%m}) исключён из обучения.',
            'Backtest: последние 6 завершенных месяцев, фиксированные модели, только прошлое в каждом прогнозе.',
            'Backtest проверяет полный ряд; устойчивый сценарий — текущий анализ чувствительности без доказанного преимущества.',
            'Страховой запас: квантиль недопрогноза с линейным масштабированием горизонта; уровень сервиса не гарантирован.',
            'День заказа рассчитан по равномерному спросу внутри месяца. Горизонт расчета ограничен.',
            'План закупки по компании; распределение между складами не моделируется.']}


def number_or_none(value):
    if value is None or pd.isna(value):
        return None
    return float(value) if np.isfinite(value) else None
