"""Check the live-run artifacts against the preserved baseline and independent replay."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    current_dir = ROOT / 'outputs' / 'historical'
    baseline_dir = current_dir / 'before-selector-fix'
    independent_dir = ROOT / 'outputs' / 'selector-evaluation'
    current = json.loads((current_dir / 'report.json').read_text(encoding='utf-8'))
    baseline = json.loads((baseline_dir / 'report.json').read_text(encoding='utf-8'))
    independent = json.loads((independent_dir / 'report.json').read_text(encoding='utf-8'))
    assert current['dataset'] == baseline['dataset'] == independent['dataset']
    assert (current['start'], current['end']) == (baseline['start'], baseline['end'])
    assert current['coverage'] == baseline['coverage']

    def read(folder):
        return pd.read_csv(folder / 'forecasts.csv', sep=';', dtype={'sku': str})

    new, old, replay = read(current_dir), read(baseline_dir), read(independent_dir)
    keys = ['supplier', 'sku', 'unit', 'month']
    old['method'] = old.method.replace({'selected': 'legacy_selected'})
    unchanged = old.merge(new, on=[*keys, 'method'], suffixes=('_old', '_new'), validate='one_to_one')
    assert len(unchanged) == len(old)
    for field in ('actual', 'forecast', 'error'):
        np.testing.assert_allclose(unchanged[field + '_old'], unchanged[field + '_new'], rtol=1e-12, atol=1e-8)
    compared = new[new.method.eq('selected')].merge(replay, on=keys, suffixes=('_new', '_replay'), validate='one_to_one')
    assert len(compared) == len(replay) == current['coverage']['evaluated']
    np.testing.assert_allclose(compared.forecast, compared.candidate, rtol=1e-12, atol=1e-8)
    np.testing.assert_allclose(compared.actual_new, compared.actual_replay, rtol=1e-12, atol=1e-8)

    simulations = 0
    for source in independent['simulations']:
        method = {'candidate': 'selected', 'selected': 'legacy_selected'}.get(source['method'], source['method'])
        target = next(row for row in current['simulations']
                      if (row['year'], row['unit'], row['method']) == (source['year'], source['unit'], method))
        assert source['sku_count'] == target['sku_count']
        for field in ('demand', 'fill_rate', 'lost_units', 'mean_inventory'):
            np.testing.assert_allclose(source[field], target[field], rtol=1e-12, atol=1e-8)
        simulations += 1
    result = {'dataset': current['dataset'], 'start': current['start'], 'end': current['end'],
        'paired_sku_months': len(compared), 'unchanged_baseline_forecasts': len(unchanged),
        'independent_simulation_groups': simulations, 'all_comparisons_passed': True}
    (independent_dir / 'production-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
