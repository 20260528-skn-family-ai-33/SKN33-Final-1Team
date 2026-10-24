"""Read-only post-run audit independent of the training/evaluation functions."""
from pathlib import Path
from collections import Counter
import argparse
import csv
import hashlib
import json
import math
import statistics

import numpy as np
from safetensors import safe_open


def read(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(2 ** 20), b''):
            h.update(chunk)
    return h.hexdigest()


def label_metrics(truth, pred):
    known = np.isfinite(truth)
    tp = ((truth == 1) & (pred == 1) & known).sum(axis=0)
    fp = ((truth == 0) & (pred == 1) & known).sum(axis=0)
    fn = ((truth == 1) & (pred == 0) & known).sum(axis=0)
    tn = ((truth == 0) & (pred == 0) & known).sum(axis=0)
    denominator = 2 * tp + fp + fn
    f1 = np.divide(2 * tp, denominator, out=np.zeros_like(tp, dtype=float), where=denominator > 0)
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, f1=f1, known=known)


def check_run(root, baseline):
    status = read(root / 'status.json')
    assert status['status'] == 'complete'
    protocol = read(root / 'protocol.json')
    config = protocol['config']
    assert hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest() == protocol['signature']
    assert status['protocol_signature'] == protocol['signature']
    assert sha(baseline / 'protocol.json') == config['baseline_protocol_sha256']
    assert sha(baseline / 'selection_frozen.json') == config['baseline_selection_sha256']
    assert read(root / 'environment.json') == read(baseline / 'environment.json')
    chosen = read(root / 'selection_frozen.json')
    assert chosen['protocol_signature'] == protocol['signature']
    assert read(root / 'selection_commit.json')['sha256'] == sha(root / 'selection_frozen.json')
    candidates = []
    for spec in config['candidates']:
        folder = root / 'candidates' / spec['id']
        result = read(folder / 'candidate_complete.json')
        for name, checksum in result['artifact_hashes'].items():
            assert sha(folder / name) == checksum
        history = read(folder / 'history.json')
        best_epoch = max(history, key=lambda row: row['dev_macro_AP'])['epoch']
        assert best_epoch == result['best_epoch']
        assert all(row['known_cells'] == 861 for row in history)
        assert all(result['parameter_groups_changed'].values())
        assert all(v > 0 and math.isfinite(v) for v in result['gradient_audit'].values())
        candidates.append(result)
    best = max(candidates, key=lambda x: (x['dev_metrics']['macro_f1'], x['dev_metrics']['label_wise_accuracy'],
        x['dev_metrics']['macro_average_precision'], -x['backbone_lr'], x['positive_weight'] == 'none'))
    assert best['id'] == chosen['id']
    checkpoint = root / chosen['checkpoint']
    assert sha(checkpoint) == chosen['artifact_hashes']['best.safetensors']
    groups = {}; parameter_values = 0
    model_path = Path(config['model_packet']['local_dir']) / 'model.safetensors'
    assert sha(model_path) == config['model_packet']['files']['model.safetensors']['sha256']
    with safe_open(str(model_path), framework='np') as original, safe_open(str(checkpoint), framework='np') as tuned:
        source_keys = set(original.keys())
        for name in tuned.keys():
            value = tuned.get_tensor(name)
            assert np.isfinite(value).all()
            parameter_values += value.size
            if name.startswith('backbone.'):
                source_name = 'vision_model.' + name[len('backbone.'):]
                assert source_name in source_keys
                before = original.get_tensor(source_name)
                group_name = '.'.join(name.split('.')[:4]) if '.encoder.layers.' in name else '.'.join(name.split('.')[:2])
                groups[group_name] = groups.get(group_name, False) or not np.array_equal(before, value)
        assert all(groups.values())
    summary = read(root / 'comparison_summary.json')
    labels = ['person','food_drink','animal','plant_flower','vehicle','building_structure','musical_instrument','fireworks']
    baseline_head = read(baseline / 'selection_frozen.json')['models']['siglip2']
    predictions = {}; audits = {}
    for kind in ('siglip2_frozen', 'siglip2_full_finetune'):
        folder = root / kind
        complete = read(folder / 'complete.json')
        assert complete['selection_sha256'] == sha(root / 'selection_frozen.json')
        for name, checksum in complete['artifact_hashes'].items():
            assert sha(folder / name) == checksum
        rows = read(folder / 'test_predictions.json')
        assert len(rows) == 37
        y = np.asarray([[np.nan if row['labels'][label] is None else row['labels'][label] for label in labels] for row in rows])
        p = np.asarray([row['predictions'] for row in rows])
        cuts = baseline_head['thresholds'] if kind == 'siglip2_frozen' else chosen['thresholds']
        assert np.array_equal(np.asarray([row['probabilities'] for row in rows]) >= cuts, p)
        m = label_metrics(y, p)
        score = summary['results'][kind]['metrics']
        assert int(m['known'].sum()) == 282
        assert math.isclose(float(np.mean(y[m['known']] == p[m['known']])), score['label_wise_accuracy'])
        assert math.isclose(float(m['f1'].mean()), score['macro_f1'])
        with (folder / 'timing_raw.csv').open(encoding='utf-8-sig') as stream:
            timing = list(csv.DictReader(stream))
        assert len(timing) == 370
        assert Counter(x['image_id'] for x in timing) == Counter({row['image_id']: 10 for row in rows})
        for stage in ('decode_preprocess_ms', 'transfer_model_head_ms', 'total_ms'):
            values = np.asarray([float(row[stage]) for row in timing])
            reported = summary['results'][kind]['timing'][stage]
            assert np.isclose(values.mean(), reported['mean_ms'])
            assert np.isclose(np.median(values), reported['median_ms'])
            assert np.isclose(np.percentile(values, 95), reported['p95_ms'])
        audits[kind] = dict(correct=int((m['tp'] + m['tn']).sum()), errors=int((m['fp'] + m['fn']).sum()),
            per_label={label: {key:int(m[key][i]) for key in ('tp','fp','fn','tn')} for i,label in enumerate(labels)})
        predictions[kind] = (rows, y, p)
    old_rows, old_y, old_p = predictions['siglip2_frozen']
    new_rows, new_y, new_p = predictions['siglip2_full_finetune']
    assert [x['image_id'] for x in old_rows] == [x['image_id'] for x in new_rows]
    assert np.array_equal(old_y, new_y, equal_nan=True)
    assert [x['predictions'] for x in old_rows] == [x['predictions'] for x in read(baseline / 'siglip2/test_predictions.json')]
    rng = np.random.default_rng(20261024)
    indices = rng.integers(0, 37, size=(5000, 37))
    mask = np.isfinite(old_y)
    deltas = []
    f1_deltas = []
    for index in indices:
        y = old_y[index]
        known = mask[index]
        deltas.append((np.mean(y[known] == new_p[index][known]) - np.mean(y[known] == old_p[index][known])) * 100)
        f1_deltas.append(float(label_metrics(y, new_p[index])['f1'].mean() - label_metrics(y, old_p[index])['f1'].mean()))
    result = dict(status='independently_verified', completed_candidates=len(candidates),
        selected_candidate=chosen['id'], selected_epoch=chosen['best_epoch'],
        all_selected_vision_groups_changed=groups, checkpoint_parameter_values=parameter_values,
        audit=audits, paired_poster_bootstrap=dict(repetitions=5000,
            accuracy_delta_pp_95CI=np.percentile(deltas, [2.5,97.5]).tolist(),
            macro_f1_delta_95CI=np.percentile(f1_deltas, [2.5,97.5]).tolist()),
        environment=read(root / 'environment.json'), summary=summary,
        candidates=[{k:v for k,v in x.items() if k not in ('gradient_audit','artifact_hashes')} for x in candidates],
        prior_exposed_test=True, packages_installed=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check_run(args.run, args.baseline), ensure_ascii=False, indent=2))
