"""Portable result contracts. Standard library only; never imports training code."""
from pathlib import Path, PurePosixPath
import ast
import csv
import hashlib
import json
import math
import struct
import tarfile
import tempfile

LABELS = ['person', 'food_drink', 'animal', 'plant_flower', 'vehicle',
          'building_structure', 'musical_instrument', 'fireworks']
LABEL_NAMES = ['인물', '음식·음료', '동물', '식물·꽃', '탈것', '건물·구조물', '악기', '불꽃놀이']
RUNS = ('heads-fp32', 'siglip-full-fp32', 'siglip-focused-fp32')
STAGES = ('decode_preprocess_ms', 'transfer_model_head_ms', 'total_ms')
SCHEMA = 'visual-results-export-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON: ' + value)
    path = Path(path)
    require(path.stat().st_size <= 64 * 1024 ** 2, 'Oversized JSON evidence: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=invalid)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(2 ** 20), b''):
            h.update(chunk)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def safe_name(name):
    require(isinstance(name, str) and name and '\\' not in name and '\x00' not in name, 'Invalid relative path')
    p = PurePosixPath(name)
    require(not p.is_absolute() and all(v not in ('', '.', '..') for v in name.split('/')),
            'Unsafe relative path: ' + name)
    return p


def inside(root, name):
    safe_name(name)
    root = Path(root).resolve()
    candidate = root.joinpath(*PurePosixPath(name).parts)
    require(candidate.resolve().is_relative_to(root), 'Path escapes evidence: ' + name)
    require(not any(p.is_symlink() for p in (candidate, *candidate.parents) if p != root.parent),
            'Symlink not allowed in result evidence: ' + name)
    return candidate


def write_csv(path, rows, fields=None):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def percentile(values, q):
    values = sorted(values)
    require(bool(values), 'Empty timing series')
    x = (len(values) - 1) * q
    a, b = math.floor(x), math.ceil(x)
    return values[a] + (values[b] - values[a]) * (x - a)


def close(actual, expected, name):
    if expected is None:
        require(actual is None, name + ': expected null')
    else:
        require(type(actual) in (float, int) and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8),
                f'{name}: {actual!r} != {expected!r}')


def average_precision(y, probabilities):
    if len(set(y)) != 2:
        return None
    positive = sum(y)
    groups = {}
    for target, p in zip(y, probabilities):
        count, tp = groups.get(p, (0, 0))
        groups[p] = (count + 1, tp + target)
    total, tp, ap = 0, 0, 0.
    for p in sorted(groups, reverse=True):
        count, added = groups[p]
        total += count; tp += added
        ap += added / positive * tp / total
    return ap


def score_rows(rows, cutoffs):
    require(len(cutoffs) == 8 and all(type(t) in (int, float) and math.isfinite(t) and 0 <= t <= 1 for t in cutoffs),
            'Invalid thresholds')
    per, counts = {}, {}
    for row in rows:
        require(list(row['labels']) == LABELS, 'Label order differs')
        require(len(row['probabilities']) == len(row['predictions']) == 8, 'Prediction width differs')
        for j, label in enumerate(LABELS):
            y, p, pred = row['labels'][label], row['probabilities'][j], row['predictions'][j]
            require(y is None or type(y) is int and y in (0, 1), 'Invalid truth label')
            require(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1, 'Invalid probability')
            require(type(pred) is int and pred in (0, 1) and pred == int(p >= cutoffs[j]), 'Threshold decision mismatch')
    for j, label in enumerate(LABELS):
        known = [(r['labels'][label], r['probabilities'][j], r['predictions'][j]) for r in rows if r['labels'][label] is not None]
        require(bool(known), 'No known test labels: ' + label)
        tp = sum(y == 1 and pred == 1 for y, _, pred in known)
        fp = sum(y == 0 and pred == 1 for y, _, pred in known)
        fn = sum(y == 1 and pred == 0 for y, _, pred in known)
        tn = sum(y == 0 and pred == 0 for y, _, pred in known)
        counts[label] = dict(tp=tp, fp=fp, fn=fn, tn=tn)
        per[label] = dict(precision=tp / (tp + fp) if tp + fp else 0.,
            recall=tp / (tp + fn) if tp + fn else 0., f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.,
            accuracy=(tp + tn) / len(known), average_precision=average_precision([v[0] for v in known], [v[1] for v in known]),
            positive=tp + fn, negative=tn + fp, unknown=len(rows) - len(known))
    total = {k: sum(c[k] for c in counts.values()) for k in ('tp', 'fp', 'fn', 'tn')}
    known = sum(total.values())
    fully = [r for r in rows if all(r['labels'][k] is not None for k in LABELS)]
    ap = [v['average_precision'] for v in per.values() if v['average_precision'] is not None]
    score = dict(label_wise_accuracy=(total['tp'] + total['tn']) / known,
        macro_f1=sum(v['f1'] for v in per.values()) / 8,
        micro_f1=2 * total['tp'] / (2 * total['tp'] + total['fp'] + total['fn']) if total['tp'] else 0.,
        macro_average_precision=sum(ap) / len(ap) if ap else None, known_evaluated_cells=known,
        masked_unknown_cells=len(rows) * 8 - known, exact_match_fully_known_rows=len(fully),
        exact_match_accuracy=sum(all(r['labels'][k] == r['predictions'][j] for j, k in enumerate(LABELS)) for r in fully) / len(fully) if fully else None,
        per_label=per)
    return score, counts


def compare_metrics(reported, calculated, context):
    for name, value in calculated.items():
        if name == 'per_label':
            for label in LABELS:
                for metric, expected in value[label].items():
                    close(reported[name][label][metric], expected, context + '/' + label + '/' + metric)
        else:
            close(reported[name], value, context + '/' + name)


def load_npy(path):
    """Read numeric Nx8 numpy arrays without numpy, pickle or model libraries."""
    with Path(path).open('rb') as stream:
        require(stream.read(6) == b'\x93NUMPY', 'Invalid npy header')
        version = stream.read(2)
        require(version in (b'\x01\x00', b'\x02\x00'), 'Unsupported npy version')
        size = struct.unpack('<H' if version[0] == 1 else '<I', stream.read(2 if version[0] == 1 else 4))[0]
        require(size < 65536, 'Oversized npy header')
        header = ast.literal_eval(stream.read(size).decode('latin1'))
        shape, dtype = header['shape'], header['descr']
        require(not header['fortran_order'] and len(shape) == 2 and shape[1] == 8 and 0 < shape[0] < 10000,
                'Expected row-major Nx8 probabilities')
        require(dtype in ('<f4', '<f8', '>f4', '>f8'), 'Expected float32/float64 probabilities')
        width = int(dtype[-1]); data = stream.read(shape[0] * 8 * width + 1)
        require(len(data) == shape[0] * 8 * width, 'npy data size mismatch')
        values = [v[0] for v in struct.iter_unpack(dtype[0] + ('f' if width == 4 else 'd'), data)]
        require(all(math.isfinite(v) and 0 <= v <= 1 for v in values), 'Invalid dev probabilities')
        return [values[i:i + 8] for i in range(0, len(values), 8)]


def rows_with_probabilities(labels, probabilities, cutoffs):
    require(len(labels) == len(probabilities), 'Probability population differs')
    return [dict(image_id=r['image_id'], labels=r['labels'], probabilities=p,
                 predictions=[int(v >= t) for v, t in zip(p, cutoffs)]) for r, p in zip(labels, probabilities)]


def dev_thresholds(rows, probabilities, focused=False):
    result = []
    for j, label in enumerate(LABELS):
        known = [(r['labels'][label], p[j]) for r, p in zip(rows, probabilities) if r['labels'][label] is not None]
        require({y for y, _ in known} == {0, 1}, 'Both dev classes required: ' + label)
        beta = 2. if focused and label == 'musical_instrument' else (.5 if focused and label in ('food_drink', 'animal', 'plant_flower') else 1.)
        candidates = []
        for i in (range(1, 20) if focused else range(21)):
            cutoff = i / 20
            tp = sum(y == 1 and p >= cutoff for y, p in known)
            fp = sum(y == 0 and p >= cutoff for y, p in known)
            fn = sum(y == 1 and p < cutoff for y, p in known)
            denom = (1 + beta * beta) * tp + beta * beta * fn + fp
            value = (1 + beta * beta) * tp / denom if denom else 0.
            candidates.append((value, -abs(cutoff - .5), cutoff))
        result.append(max(candidates)[-1])
    return result


def focus_errors(counts):
    return counts['musical_instrument']['fn'] + sum(counts[k]['fp'] for k in ('food_drink', 'animal', 'plant_flower'))


def extract_archive(archive, destination, max_bytes=16 * 1024 ** 3):
    archive, destination = Path(archive), Path(destination)
    require(not destination.exists(), 'Extraction destination already exists; choose a new directory')
    sidecar = archive.with_name(archive.name + '.sha256')
    if sidecar.exists():
        require(sidecar.read_text().split()[0] == sha(archive), 'Archive SHA256 mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.visual-extract-', dir=destination.parent) as temporary:
        root = Path(temporary) / 'evidence'; root.mkdir()
        total, names = 0, set()
        with tarfile.open(archive, 'r:gz') as stream:
            for member in stream:
                safe_name(member.name)
                require(member.isfile() and member.name not in names, 'Nonregular or duplicate archive member')
                names.add(member.name); total += member.size
                require(len(names) <= 10000 and total <= max_bytes and member.size >= 0, 'Archive exceeds extraction limits')
                target = inside(root, member.name); target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member)
                require(source is not None, 'Unreadable archive member')
                with source, target.open('xb') as output:
                    for chunk in iter(lambda: source.read(2 ** 20), b''):
                        output.write(chunk)
        verify_manifest(root)
        root.rename(destination)
    return destination


def verify_manifest(root):
    root = Path(root)
    manifest = read(root / 'export_manifest.json')
    require(manifest['schema'] == SCHEMA and manifest['labels'] == LABELS and manifest['runs'] == list(RUNS), 'Unknown export contract')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if not p.is_dir()}
    require(actual == set(manifest['files']) | {'export_manifest.json'}, 'Missing or unexpected evidence files')
    for name, expected in manifest['files'].items():
        p = inside(root, name)
        require(p.is_file() and p.stat().st_size == expected['bytes'] and sha(p) == expected['sha256'], 'Evidence hash mismatch: ' + name)
    return manifest


def artifact_check(root, manifest, folder, hashes):
    omitted = manifest.get('omitted_checkpoints', {})
    for name, checksum in hashes.items():
        safe_name(name)
        relative = (PurePosixPath(folder) / name).as_posix()
        if relative in manifest['files']:
            require(manifest['files'][relative]['sha256'] == checksum, 'Recorded artifact hash differs: ' + relative)
        else:
            require(relative.endswith('.safetensors') and omitted.get(relative) == checksum,
                    'Required artifact absent: ' + relative)


def timing_check(folder, rows, config, reported):
    with (folder / 'timing_raw.csv').open(encoding='utf-8-sig', newline='') as stream:
        records = list(csv.DictReader(stream))
    expected = {(r['image_id'], n) for r in rows for n in range(config['repeats'])}
    observed = [(r['image_id'], int(r['repeat'])) for r in records]
    require(len(observed) == len(expected) and set(observed) == expected, 'Timing population/repeats differ')
    for row in records:
        values = [float(row[k]) for k in STAGES]
        require(all(math.isfinite(v) and v >= 0 for v in values), 'Invalid timing value')
        require(math.isclose(values[0] + values[1], values[2], rel_tol=1e-6, abs_tol=.001), 'Timing stages do not sum')
    calculated = {}
    for stage in STAGES:
        values = [float(r[stage]) for r in records]
        result = dict(n=len(values), mean_ms=sum(values) / len(values), median_ms=percentile(values, .5), p95_ms=percentile(values, .95))
        for k, value in result.items():
            close(reported[stage][k], value, 'timing/' + stage + '/' + k)
        calculated[stage] = result
    return calculated


def verify_results(root):
    """Recompute evidence on CPU without inference or loading checkpoints."""
    root = Path(root)
    manifest = verify_manifest(root)
    bundle = root / 'bundle'
    original = read(bundle / 'bundle_manifest.json')
    for name in manifest['files']:
        if name.startswith('bundle/') and name != 'bundle/bundle_manifest.json':
            require(original['files'][name[7:]] == manifest['files'][name], 'Bundle provenance mismatch: ' + name)
    info = read(bundle / 'data/dataset_info.json')
    test = read(bundle / 'data/test.json'); td = read(bundle / 'data/train_dev.json')
    ids = read(bundle / 'data/split_ids.json')
    require(info['labels'] == LABELS and info['split_counts'] == dict(train=113, dev=37, test=37), 'Approved split differs')
    require(len(ids) == info['approved_images'] == 187 and len({r['image_id'] for r in ids}) == 187, 'Split IDs differ')
    for attribute in ('group_id', 'pixel_sha256'):
        owners = {}
        for r in ids:
            require(owners.setdefault(r[attribute], r['split']) == r['split'], 'Cross-split ' + attribute)
    for split in ('train', 'dev', 'test'):
        population = test if split == 'test' else [r for r in td if r['split'] == split]
        require([r['image_id'] for r in population] == [r['image_id'] for r in ids if r['split'] == split], 'Row order/split differs')
        require(len(population) == info['split_counts'][split], 'Split count differs')
        require(sum(r['labels'][k] is not None for r in population for k in LABELS) == info['known_cells'][split], 'Known label mask differs')
        mapping = {r['image_id']: r for r in ids}
        require(all(r['split'] == split and r['group_id'] == mapping[r['image_id']]['group_id']
                    and list(r['labels']) == LABELS and all(type(v) in (int, type(None)) and v in (0, 1, None) for v in r['labels'].values())
                    for r in population), 'Row group/label contract differs')
    require(len(td) == 150 and all(r['split'] in ('train', 'dev') for r in td), 'Train/dev population differs')
    require(info['approval_method'] == 'explicit_user_chat_approval' and info['known_cells'] == dict(train=861, dev=283, test=282), 'Approval/mask contract differs')
    dev = [r for r in td if r['split'] == 'dev']
    protocols, selections, candidates, models = {}, {}, {}, []
    for run in RUNS:
        folder = root / 'runs' / run
        status, protocol = read(folder / 'status.json'), read(folder / 'protocol.json')
        config = protocol['config']; selected = read(folder / 'selection_frozen.json')
        require(status['status'] == 'complete' and status['protocol_signature'] == protocol['signature'] == digest(config), 'Incomplete or altered run: ' + run)
        require(selected['protocol_signature'] == protocol['signature'] and selected['test_used_for_selection'] is False, 'Selection protocol differs')
        require(read(folder / 'selection_commit.json')['sha256'] == sha(folder / 'selection_frozen.json'), 'Frozen selection hash differs')
        require(read(folder / 'environment.json') == read(root / 'runs' / RUNS[0] / 'environment.json'), 'GPU/runtime differs')
        require(config['environment'] == read(folder / 'environment.json'), 'Protocol/runtime differs')
        require(config.get('precision', read(folder / 'environment.json')['precision']) == 'fp32', 'FP32 comparison required')
        for name, checksum in config['script_hashes'].items():
            relative = name if '/' in name else 'visual_classifier_benchmark/' + name
            require(sha(inside(root / 'sources', relative)) == checksum, 'Training source fingerprint differs: ' + name)
        artifact_check(root, manifest, 'runs/' + run, status.get('artifact_hashes', {}))
        protocols[run], selections[run] = protocol, selected
        if run != RUNS[0]:
            require(config['baseline_protocol_sha256'] == sha(root / 'runs' / RUNS[0] / 'protocol.json'), 'Baseline protocol link differs')
            key_run = RUNS[0] if run == RUNS[1] else RUNS[1]
            key = 'baseline_selection_sha256' if run == RUNS[1] else 'prior_selection_sha256'
            require(config[key] == sha(root / 'runs' / key_run / 'selection_frozen.json'), 'Parent selection link differs')
            if run == RUNS[2]:
                require(config['prior_protocol_sha256'] == sha(root / 'runs' / RUNS[1] / 'protocol.json'), 'Parent protocol link differs')
            require(config['bundle_sha256'] == sha(bundle / 'bundle_manifest.json'), 'Data manifest link differs')
            if 'model_packet' in config:
                require(config['model_packet'] == protocols[RUNS[0]]['config']['models']['siglip2'], 'Source model packet differs')
        else:
            require(config['bundle_manifest_sha256'] == sha(bundle / 'bundle_manifest.json'), 'Baseline data manifest differs')
        if run == RUNS[0]:
            specs = [(k, v) for k, v in selected['models'].items()]
            require('siglip2' in selected['models'], 'SigLIP frozen baseline missing')
            for kind, choice in specs:
                artifact_check(root, manifest, 'runs/' + run + '/' + kind, choice['artifact_hashes'])
        else:
            checkpoint = selected['checkpoint']; safe_name(checkpoint)
            artifact_check(root, manifest, 'runs/' + run + '/' + str(PurePosixPath(checkpoint).parent), selected['artifact_hashes'])
            specs = [('siglip2_frozen', selections[RUNS[0]]['models']['siglip2']), ('siglip2_full_finetune', selected)] if run == RUNS[1] else [
                ('siglip2_full_finetune', read(folder / 'candidates/prior/candidate_complete.json')), ('siglip2_focused', selected)]
        for kind, choice in specs:
            model_folder = folder / kind
            complete = read(model_folder / ('evaluation_complete.json' if run == RUNS[0] else 'complete.json'))
            require(complete['selection_sha256'] == sha(folder / 'selection_frozen.json'), 'Evaluation selection differs')
            artifact_check(root, manifest, f'runs/{run}/{kind}', complete['artifact_hashes'])
            rows = read(model_folder / 'test_predictions.json')
            require(len(rows) == len(test) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels'] for a, b in zip(rows, test)), 'Test IDs/labels differ')
            calculated, counts = score_rows(rows, choice['thresholds'])
            compare_metrics(read(model_folder / 'test_metrics.json'), calculated, run + '/' + kind)
            compare_metrics(complete['result']['metrics'], calculated, 'complete/' + kind)
            run_summary = read(folder / 'comparison_summary.json')
            compare_metrics(run_summary['results'][kind]['metrics'], calculated, 'summary/' + kind)
            timing = timing_check(model_folder, rows, config, read(model_folder / 'timing_summary.json'))
            for stage in STAGES:
                for k, v in timing[stage].items():
                    close(complete['result']['timing'][stage][k], v, 'complete/timing')
                    close(run_summary['results'][kind]['timing'][stage][k], v, 'summary/timing')
            models.append(dict(id=run + '/' + kind, run=run, model=kind, metrics=calculated, confusion=counts,
                               timing=timing, thresholds=choice['thresholds'], predictions=rows,
                               goal_met=calculated['label_wise_accuracy'] >= .9 and timing['total_ms']['p95_ms'] <= 50))
        candidates[run] = verify_candidates(root, manifest, run, config, selected, dev)
    by_id = {m['id']: m for m in models}
    for a, b in ((RUNS[0] + '/siglip2', RUNS[1] + '/siglip2_frozen'),
                 (RUNS[1] + '/siglip2_full_finetune', RUNS[2] + '/siglip2_full_finetune')):
        require([r['predictions'] for r in by_id[a]['predictions']] == [r['predictions'] for r in by_id[b]['predictions']], 'Control model decisions differ')
    focused = selections[RUNS[2]]
    reference = read(root / 'runs' / RUNS[2] / 'candidates/prior/candidate_complete.json')
    require(reference['thresholds'] == selections[RUNS[1]]['thresholds'], 'Reference thresholds differ from prior selection')
    for filename in ('best.safetensors', 'dev_probabilities.npy'):
        require(reference['artifact_hashes'][filename] == selections[RUNS[1]]['artifact_hashes'][filename], 'Reference checkpoint/dev probabilities differ')
    summary = read(root / 'runs' / RUNS[2] / 'comparison_summary.json')
    require(summary['selected_source'] == focused['source'] and summary['threshold_policy'] == focused['threshold_policy'], 'Selected source/policy differs')
    require(summary['goal_met'] == by_id[RUNS[2] + '/siglip2_focused']['goal_met'], 'Goal decision differs')
    changes = error_changes(by_id[RUNS[2] + '/siglip2_full_finetune']['predictions'], by_id[RUNS[2] + '/siglip2_focused']['predictions'])
    require(read(root / 'runs' / RUNS[2] / 'error_changes.json') == changes, 'Error change records differ')
    return dict(schema='visual-results-verified-v1', status='verified_report_evidence',
        archive_created_at=manifest['created_at_utc'], manifest_sha256=sha(root / 'export_manifest.json'),
        checkpoint_bytes_verified=any(k.endswith('.safetensors') for k in manifest['files']),
        omitted_checkpoints=len(manifest.get('omitted_checkpoints', {})),
        parameter_updates_independently_verified=False, inference_reexecuted=False,
        previously_exposed_test=True, goal=dict(accuracy_min=.9, p95_max_ms=50), dataset=info,
        environment=read(root / 'runs' / RUNS[0] / 'environment.json'), models=models,
        selected=focused, candidates=candidates, error_changes=changes,
        note='Saved evidence and metrics verified; checkpoint parameter changes and GPU inference were not independently reexecuted.')


def verify_candidates(root, manifest, run, config, selected, dev):
    if run == RUNS[0]:
        return []
    folder = root / 'runs' / run
    candidates, variants = [], []
    for spec in config['candidates']:
        safe_name(spec['id'])
        candidate_folder = folder / 'candidates' / spec['id']
        result = read(candidate_folder / 'candidate_complete.json')
        require(all(result[k] == v for k, v in spec.items()), 'Candidate settings differ')
        artifact_check(root, manifest, f'runs/{run}/candidates/{spec["id"]}', result['artifact_hashes'])
        history = read(candidate_folder / 'history.json')
        require(bool(history) and [r['epoch'] for r in history] == list(range(1, len(history) + 1)), 'Invalid epoch history')
        require(max(history, key=lambda r: r['dev_macro_AP'])['epoch'] == result['best_epoch'], 'Best epoch differs')
        require(all(r['known_cells'] == 861 for r in history), 'Training known cell population differs')
        require(all(result['parameter_groups_changed'].values()) and all(v is not None and math.isfinite(v) and v > 0 for v in result['gradient_audit'].values()), 'Reported training audit failed')
        probabilities = load_npy(candidate_folder / 'dev_probabilities.npy')
        if run == RUNS[1]:
            require(result['thresholds'] == dev_thresholds(dev, probabilities), 'Dev F1 thresholds differ')
            calculated, _ = score_rows(rows_with_probabilities(dev, probabilities, result['thresholds']), result['thresholds'])
            compare_metrics(result['dev_metrics'], calculated, 'candidate/' + spec['id'])
        else:
            for variant in result['variants']:
                require(variant['source'] == 'trained' and all(variant[k] == result[k] for k in
                        ('id', 'backbone_lr', 'checkpoint', 'best_epoch', 'artifact_hashes')), 'Invalid trained variant')
                validate_variant(variant, dev, probabilities)
            variants.extend(result['variants'])
        candidates.append(dict(id=spec['id'], settings=spec, best_epoch=result['best_epoch'], history=history))
    if run == RUNS[1]:
        completed = [read(folder / 'candidates' / s['id'] / 'candidate_complete.json') for s in config['candidates']]
        best = max(completed, key=lambda r: (r['dev_metrics']['macro_f1'], r['dev_metrics']['label_wise_accuracy'], r['dev_metrics']['macro_average_precision'], -r['backbone_lr'], r['positive_weight'] == 'none'))
        require(all(best[k] == selected[k] for k in ('id', 'checkpoint', 'best_epoch', 'thresholds')), 'Full fine-tuning dev selection differs')
        compare_metrics(selected['dev_metrics'], best['dev_metrics'], 'selected/full/dev')
    else:
        reference = read(folder / 'candidates/prior/candidate_complete.json')
        artifact_check(root, manifest, f'runs/{run}/candidates/prior', reference['artifact_hashes'])
        probabilities = load_npy(folder / 'candidates/prior/dev_probabilities.npy')
        reference_rows = rows_with_probabilities(dev, probabilities, reference['thresholds'])
        score, counts = score_rows(reference_rows, reference['thresholds'])
        compare_metrics(reference['dev_metrics'], score, 'prior/dev')
        require(reference['dev_confusion'] == counts, 'Prior dev confusion differs')
        for policy, focus in (('dev_F1', False), ('dev_target_Fbeta', True)):
            cuts = dev_thresholds(dev, probabilities, focus)
            metric, confusion = score_rows(rows_with_probabilities(dev, probabilities, cuts), cuts)
            variants.append(dict(**{k: v for k, v in reference.items() if k not in ('threshold_policy', 'thresholds', 'dev_metrics', 'dev_confusion')},
                                 threshold_policy=policy, thresholds=cuts, dev_metrics=metric, dev_confusion=confusion))
        eligible = [c for c in [reference] + variants
                    if c['dev_metrics']['label_wise_accuracy'] + 1e-12 >= score['label_wise_accuracy']
                    and c['dev_metrics']['macro_f1'] + 1e-12 >= score['macro_f1']
                    and focus_errors(c['dev_confusion']) <= focus_errors(counts)]
        best = max(eligible, key=lambda c: (c['dev_metrics']['label_wise_accuracy'], -focus_errors(c['dev_confusion']), c['dev_metrics']['macro_f1'], c['dev_metrics']['macro_average_precision'], c['source'] == 'prior', c['threshold_policy'] == 'original', -c['backbone_lr']))
        require(all(best[k] == selected[k] for k in ('id', 'source', 'threshold_policy', 'thresholds', 'checkpoint', 'best_epoch')), 'Focused dev selection differs')
        compare_metrics(selected['dev_metrics'], best['dev_metrics'], 'selected/focused/dev')
        require(selected['dev_confusion'] == best['dev_confusion'], 'Selected dev confusion differs')
    return candidates


def validate_variant(variant, dev, probabilities):
    policy = variant['threshold_policy']
    require(policy in ('dev_F1', 'dev_target_Fbeta'), 'Unknown threshold policy')
    cuts = dev_thresholds(dev, probabilities, policy == 'dev_target_Fbeta')
    require(cuts == variant['thresholds'], 'Focused dev thresholds differ')
    calculated, counts = score_rows(rows_with_probabilities(dev, probabilities, cuts), cuts)
    compare_metrics(variant['dev_metrics'], calculated, 'focused/dev')
    require(variant['dev_confusion'] == counts, 'Focused dev confusion differs')


def error_changes(old, new):
    changes = []
    for j, label in enumerate(LABELS):
        for a, b in zip(old, new):
            y = a['labels'][label]
            if y is not None and a['predictions'][j] != b['predictions'][j]:
                changes.append(dict(image_id=a['image_id'], label=label, truth=y,
                    prior_prediction=a['predictions'][j], focused_prediction=b['predictions'][j],
                    change='recovered' if b['predictions'][j] == y else 'regressed'))
    return changes
