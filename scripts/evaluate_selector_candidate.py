"""Independent, fixed-rule replay on already-inspected historical periods.

Does not import the production selector. Missing months remain unknown.
Outputs are evaluation artifacts, not a claim of unseen holdout performance.
"""
import hashlib
import json
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def full_inputs(h, target, method):
    required = [target - 12] if method == 'seasonal_naive' else [
        target - distance for distance in (1, 2, 3, 4, 5, 6, 12, 13, 14, 15)]
    return all(month in h for month in required)


def prediction(h, target, method):
    h = {month: value for month, value in h.items() if month < target}
    recent_values = [h[month] for month in range(target - 3, target) if month in h]
    if not recent_values:
        recent_values = [h[month] for month in sorted(h)[-3:]]
    recent = sum(recent_values) / len(recent_values)
    if method == 'mean3':
        return recent
    prior_year = h.get(target - 12, recent)
    if method == 'seasonal_naive':
        return prior_year
    older = [h[month] for month in range(target - 6, target - 3) if month in h]
    previous = [h[month] for month in range(target - 15, target - 12) if month in h]
    older = sum(older) / len(older) if older else 0.
    previous = sum(previous) / len(previous) if previous else 0.
    trend = min(1.5, max(.5, recent / older)) if older > 0 else 1.
    growth = min(1.5, max(.5, recent / previous)) if previous > 0 else 1.
    seasonal = prior_year * growth if target - 12 in h else recent
    return max(0., .5 * recent * trend + .5 * seasonal)


def choose(h, target):
    h = {month: value for month, value in h.items() if month < target}
    eligible = []
    for method in ('seasonal_naive', 'adaptive'):
        if not full_inputs(h, target, method):
            continue
        folds = []
        for cutoff in range(target - 12, target):
            if cutoff not in h or sum(month < cutoff for month in h) < 6:
                continue
            if not full_inputs(h, cutoff, method):
                continue
            actual = h[cutoff]
            baseline_error = abs(prediction(h, cutoff, 'mean3') - actual)
            candidate_error = abs(prediction(h, cutoff, method) - actual)
            folds.append((baseline_error, candidate_error))
        if len(folds) < 6:
            continue
        baseline = sum(pair[0] for pair in folds)
        error = sum(pair[1] for pair in folds)
        wins = sum(candidate < base for base, candidate in folds)
        if (baseline > 0 and error <= .8 * baseline
                and wins * 3 >= len(folds) * 2
                and sum(pair[1] for pair in folds[-3:]) <= sum(pair[0] for pair in folds[-3:])):
            eligible.append((error / len(folds), method))
    return min(eligible)[1] if eligible else 'mean3'


def simulate(histories, paired):
    """Same hypothetical policy, initial stock, and complete-year subsets."""
    records = []
    products = paired[['supplier', 'sku', 'unit']].drop_duplicates()
    for product in products.to_dict('records'):
        history = histories[(product['supplier'], product['sku'])]
        for year, first_month, last_month in ((2024, 7, 12), (2025, 1, 12), (2026, 1, 8)):
            targets = list(range(year * 12 + first_month, year * 12 + last_month + 1))
            if any(month not in history for month in targets):
                continue
            training = {month: value for month, value in history.items() if month < targets[0]}
            if len(training) < 6:
                continue
            stock = 2 * prediction(training, targets[0], 'mean3')
            pending = lost = total = inventory = 0.
            for target in targets:
                stock += pending
                past = {month: value for month, value in history.items() if month < target}
                now = prediction(past, target, choose(past, target))
                next_month = prediction(past, target + 1, choose(past, target + 1))
                pending = max(0., now + next_month - stock)
                actual = history[target]
                lost += max(0., actual - stock)
                stock = max(0., stock - actual)
                total += actual
                inventory += stock
            records.append({**product, 'year': year, 'demand': total, 'lost': lost,
                            'mean_inventory': inventory / len(targets)})
    result = []
    for (year, unit), group in pd.DataFrame(records).groupby(['year', 'unit']):
        demand = float(group.demand.sum())
        result.append({'year': int(year), 'unit': unit, 'method': 'candidate',
            'sku_count': len(group), 'demand': demand,
            'fill_rate': 1 - float(group.lost.sum()) / demand if demand else None,
            'lost_units': float(group.lost.sum()),
            'mean_inventory': float(group.mean_inventory.sum())})
    return result


def main():
    folder = ROOT / 'outputs' / 'selector-evaluation'
    folder.mkdir(exist_ok=True, parents=True)
    baseline_folder = ROOT / 'outputs' / 'historical' / 'before-selector-fix'
    report_path = baseline_folder / 'report.json'
    old_report = json.loads(report_path.read_text(encoding='utf-8'))
    archives = [Path.home() / 'Downloads' / name for name in ('IEK.zip', 'Systeme electric.zip')]
    digest = hashlib.sha256()
    for archive in archives:
        digest.update(archive.read_bytes())
    signature = digest.hexdigest()
    assert signature == old_report['dataset'], 'Source archives differ from saved historical evaluation'
    caches = sorted((ROOT / 'data').glob(f'normalized-{signature}-*.pkl'), key=lambda p: p.stat().st_mtime)
    assert caches, 'No private normalized cache for verified source fingerprint'
    with caches[-1].open('rb') as handle:
        dataset, _ = pickle.load(handle)
    assert dataset.fingerprint == signature
    histories = {}
    for key, group in dataset.monthly.groupby(['supplier', 'sku']):
        assert not group.month.duplicated().any(), f'Duplicate month for {key}'
        histories[key] = {stamp.year * 12 + stamp.month: max(0., float(quantity))
                          for stamp, quantity in zip(group.month, group.quantity) if pd.notna(quantity)}
    original = pd.read_csv(baseline_folder / 'forecasts.csv', sep=';', dtype={'sku': str})
    keys = ['supplier', 'sku', 'unit', 'month', 'year', 'actual']
    paired = original.pivot(index=keys, columns='method', values='forecast').reset_index()
    records = []
    for row in paired.to_dict('records'):
        month = pd.Timestamp(row['month'])
        history = histories[(row['supplier'], row['sku'])]
        chosen = choose(history, month.year * 12 + month.month)
        candidate = row[chosen]
        independent = prediction(history, month.year * 12 + month.month, chosen)
        assert abs(candidate - independent) < 1e-7 * max(1., abs(candidate)), 'Replay disagrees with saved method forecast'
        records.append({**row, 'chosen': chosen, 'candidate': candidate})
    frame = pd.DataFrame(records)
    frame.to_csv(folder / 'forecasts.csv', sep=';', index=False, encoding='utf-8-sig')
    metrics = []
    for (year, unit), group in frame.groupby(['year', 'unit']):
        actual = float(group.actual.sum())
        metrics.append({'year': int(year), 'unit': unit, 'n': len(group),
            'mean3_wape': float((group.mean3 - group.actual).abs().sum()) / actual if actual else None,
            'old_selected_wape': float((group.selected - group.actual).abs().sum()) / actual if actual else None,
            'candidate_wape': float((group.candidate - group.actual).abs().sum()) / actual if actual else None,
            'chosen': {str(key): int(value) for key, value in group.chosen.value_counts().items()}})
    example = frame.loc[frame.sku.eq('030200193_') & frame.month.eq('2025-02-01')].to_dict('records')
    simulations = simulate(histories, paired)
    simulations += [record for record in old_report['simulations']
                    if record['method'] in ('mean3', 'selected')]
    for item in simulations:
        matches = [record for record in simulations if record['year'] == item['year'] and record['unit'] == item['unit']]
        assert all(record['sku_count'] == item['sku_count'] and abs(record['demand'] - item['demand']) < 1e-8 for record in matches)
    result = {'dataset': signature, 'source_verified': True, 'n': len(frame), 'metrics': metrics,
        'example': example, 'simulations': simulations,
        'limitation': 'Already-inspected periods; fixed rule, no threshold sweep. Forecast and hypothetical policy only, not actual procurement savings.'}
    (folder / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
