"""Portable data contracts and metrics. No GPU/model import at module import."""
from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import random

LABELS = ['person', 'food_drink', 'animal', 'plant_flower', 'vehicle',
          'building_structure', 'musical_instrument', 'fireworks']
ARMS = ['resnet18_full_finetune', 'eg2_frozen_linear', 'eg2_full_finetune']
REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
MODEL_ID = 'google/embeddinggemma-2'
SEED = 20261022


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def log(out, event, **values):
    row = dict(event=event, at_utc=datetime.now(timezone.utc).isoformat(), **values)
    line = json.dumps(row, ensure_ascii=False, allow_nan=False)
    print(line, flush=True)
    with (Path(out) / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')


def contained(root, relative):
    p = (Path(root) / relative).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError('Path escapes bundle: ' + str(relative))
    return p


def validate_rows(rows, split, ids):
    if [x['image_id'] for x in rows] != [x['image_id'] for x in ids if x['split'] == split]:
        raise ValueError('Row population/order changed: ' + split)
    mapping = {x['image_id']: x for x in ids}
    for x in rows:
        if x['split'] != split or x['group_id'] != mapping[x['image_id']]['group_id']:
            raise ValueError('Split/group mismatch')
        if list(x['labels']) != LABELS or any(type(v) not in (int, type(None)) or v not in (0, 1, None) for v in x['labels'].values()):
            raise ValueError('Labels must be ordered 0/1/null, never bool/string')


def verify_bundle(root):
    """Hash test bytes without parsing test labels or opening test images."""
    root = Path(root)
    manifest = read(root / 'bundle_manifest.json')
    if manifest['schema'] != 'runpod-poster-bundle-v1':
        raise ValueError('Unknown bundle schema')
    for relative, expected in manifest['files'].items():
        p = contained(root, relative)
        if not p.is_file() or p.stat().st_size != expected['bytes'] or sha(p) != expected['sha256']:
            raise ValueError('Missing/modified bundle file: ' + relative)
    info = read(root / 'data/dataset_info.json')
    ids = read(root / 'data/split_ids.json')
    if info['labels'] != LABELS or info['approved_images'] != 187 or info['approval_method'] != 'explicit_user_chat_approval':
        raise ValueError('Original approved label packet required')
    if len(ids) != 187 or len({x['image_id'] for x in ids}) != 187:
        raise ValueError('Expected 187 unique posters')
    if len({x['pixel_sha256'] for x in ids}) != 187:
        raise ValueError('Expected187 unique RGB images')
    if {s: sum(x['split'] == s for x in ids) for s in ('train', 'dev', 'test')} != {'train': 113, 'dev': 37, 'test': 37}:
        raise ValueError('Original 113/37/37 split required')
    for key in ('group_id', 'pixel_sha256'):
        owners = {}
        for x in ids:
            if owners.setdefault(x[key], x['split']) != x['split']:
                raise ValueError('Cross-split leakage: ' + key)
    td = read(root / 'data/train_dev.json')
    if len(td)!=150 or any(x['split'] not in ('train','dev') for x in td):
        raise ValueError('Train/dev-only150 rows required')
    for stage in ('train', 'dev'):
        rows = [x for x in td if x['split'] == stage]
        validate_rows(rows, stage, ids)
        known = sum(v is not None for x in rows for v in x['labels'].values())
        if known != {'train': 861, 'dev': 283}[stage]:
            raise ValueError('Known-label mask changed: ' + stage)
    return info, ids, td


def load_test(root, selection_path, expected_selection_hash):
    if sha(selection_path) != expected_selection_hash:
        raise ValueError('Selection changed before test access')
    rows = read(Path(root) / 'data/test.json')
    validate_rows(rows, 'test', read(Path(root) / 'data/split_ids.json'))
    if sum(v is not None for x in rows for v in x['labels'].values()) != 282:
        raise ValueError('Test mask changed')
    return rows


def targets(rows):
    import numpy as np
    return np.asarray([[float('nan') if x['labels'][k] is None else x['labels'][k] for k in LABELS] for x in rows], dtype=np.float32)


def metrics(y, p, cutoffs):
    import numpy as np
    from sklearn.metrics import precision_recall_fscore_support, average_precision_score
    y, p = np.asarray(y), np.asarray(p)
    if y.shape != p.shape or y.shape[1] != 8 or not np.isfinite(p).all():
        raise ValueError('Invalid probability/label shape or nonfinite output')
    pred = p >= np.asarray(cutoffs)
    per = {}
    for j, k in enumerate(LABELS):
        mask = np.isfinite(y[:, j]); yy = y[mask, j].astype(int)
        pr, re, f1, _ = precision_recall_fscore_support(yy, pred[mask, j], average='binary', zero_division=0)
        per[k] = dict(precision=float(pr), recall=float(re), f1=float(f1), accuracy=float(np.mean(yy == pred[mask, j])),
                      average_precision=float(average_precision_score(yy, p[mask, j])) if len(np.unique(yy)) == 2 else None,
                      positive=int((yy == 1).sum()), negative=int((yy == 0).sum()), unknown=int((~mask).sum()))
    mask = np.isfinite(y); full = mask.all(axis=1)
    return dict(label_wise_accuracy=float(np.mean(y[mask] == pred[mask])),
                macro_f1=float(np.mean([x['f1'] for x in per.values()])),
                micro_f1=float(precision_recall_fscore_support(y[mask].astype(int), pred[mask], average='binary', zero_division=0)[2]),
                macro_average_precision=float(np.mean([x['average_precision'] for x in per.values() if x['average_precision'] is not None])),
                known_evaluated_cells=int(mask.sum()), masked_unknown_cells=int((~mask).sum()),
                exact_match_accuracy=float(np.mean(np.all(y[full] == pred[full], axis=1))) if full.any() else None,
                exact_match_fully_known_rows=int(full.sum()), per_label=per)


def thresholds(y, p):
    import numpy as np
    from sklearn.metrics import f1_score
    selected = []
    for j in range(8):
        mask = np.isfinite(y[:, j])
        selected.append(max((float(f1_score(y[mask, j], p[mask, j] >= i/20, zero_division=0)), -abs(i/20-.5), i/20)
                            for i in range(21))[-1])
    return selected


def timing_orders(n, repeats):
    rng = random.Random(SEED)
    result = []
    for repeat in range(repeats):
        order = list(range(n)); rng.shuffle(order)
        result.extend((repeat, i) for i in order)
    return result


def csv_rows(path, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
