"""Read-only SigLIP evidence and abstention-aware reports; standard library only."""
from pathlib import Path
import json

from resultlib import (LABELS, compare_metrics, digest, inside, load_npy, percentile,
                      read, require, rows_with_probabilities, save, score_rows, sha,
                      write_csv)


def reference(root):
    root = Path(root)
    status = read(root / 'status.json')
    require(status['status'] == 'complete', 'Finish the existing SigLIP run first')
    hashes = status['artifact_hashes']

    def proof(name):
        path = inside(root, name)
        require(name in hashes and sha(path) == hashes[name], 'Changed SigLIP evidence: ' + name)
        return path

    protocol = read(proof('protocol.json'))
    frozen = read(proof('selection_frozen.json'))
    require(protocol['signature'] == digest(protocol['config']) == status['protocol_signature'] ==
            frozen['protocol_signature'], 'Reference protocol differs')
    require(read(proof('selection_commit.json'))['sha256'] == sha(root / 'selection_frozen.json') and
            frozen['test_used_for_selection'] is False, 'Reference selection was not frozen before test')
    name = frozen['selected_model']
    require(name.startswith('siglip2_'), 'Expected a development-selected SigLIP2 reference')
    model = frozen['models'][name]
    candidate_path = str(Path(model['checkpoint']).parent / 'candidate_complete.json')
    require(candidate_path in frozen['candidate_records'] and
            sha(proof(candidate_path)) == frozen['candidate_records'][candidate_path], 'Reference candidate changed')
    candidate = read(root / candidate_path)
    require(candidate['thresholds'] == model['thresholds'] and candidate['checkpoint'] == model['checkpoint'],
            'Reference candidate identity differs')
    dev_path = str(Path(model['checkpoint']).parent / 'dev_probabilities.npy')
    proof(dev_path)
    for leaf in ('test_predictions.json', 'test_metrics.json', 'timing_summary.json'):
        proof(name + '/' + leaf)
    environment = read(proof('environment.json'))
    files = ['protocol.json', 'selection_frozen.json', 'selection_commit.json', 'environment.json',
             candidate_path, dev_path, *[name + '/' + leaf for leaf in
             ('test_predictions.json', 'test_metrics.json', 'timing_summary.json')]]
    return dict(model=name, thresholds=model['thresholds'], dev_path=dev_path, environment=environment,
                files={p: hashes[p] for p in files}, signature=status['protocol_signature'],
                checkpoint_sha256=hashes.get(model['checkpoint']), latency_remeasured=False)


def reference_rows(root, info, split, truth):
    if split == 'dev':
        probabilities = load_npy(inside(root, info['dev_path']))
        return rows_with_probabilities(truth, probabilities, info['thresholds'])
    result = read(inside(root, info['model'] + '/test_predictions.json'))
    require(len(result) == len(truth) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
            for a, b in zip(result, truth)), 'Reference and benchmark test populations differ')
    calculated, _ = score_rows(result, info['thresholds'])
    compare_metrics(read(inside(root, info['model'] + '/test_metrics.json')), calculated, 'SigLIP test')
    return result


def parse_answer(text):
    """No repair/retry/default negatives; duplicate keys and non-JSON text are invalid."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        obj = json.loads(text.strip(), object_pairs_hook=pairs,
                         parse_constant=lambda x: require(False, 'Nonfinite value: ' + x))
        require(isinstance(obj, dict) and set(obj) == set(LABELS), 'Exactly the eight label keys are required')
        require(all(v is None or type(v) is int and v in (0, 1) for v in obj.values()),
                'Values must be JSON integers 0/1 or null; booleans/strings are invalid')
        return [obj[k] for k in LABELS], None
    except (ValueError, TypeError) as exc:
        return [None] * len(LABELS), str(exc)


def metrics(rows):
    per = {}
    for j, label in enumerate(LABELS):
        known = [(r['labels'][label], r['predictions'][j]) for r in rows if r['labels'][label] is not None]
        tp = sum(y == 1 and p == 1 for y, p in known)
        fp = sum(y == 0 and p == 1 for y, p in known)
        fn = sum(y == 1 and p == 0 for y, p in known)
        tn = sum(y == 0 and p == 0 for y, p in known)
        unanswered_positive = sum(y == 1 and p is None for y, p in known)
        unanswered_negative = sum(y == 0 and p is None for y, p in known)
        answered = tp + fp + fn + tn
        per[label] = dict(tp=tp, fp=fp, fn=fn, tn=tn, known=len(known), answered=answered,
            unanswered_positive=unanswered_positive, unanswered_negative=unanswered_negative,
            accuracy=(tp + tn) / len(known), coverage=answered / len(known),
            covered_precision=tp / (tp + fp) if tp + fp else 0.,
            covered_recall=tp / (tp + fn) if tp + fn else 0.,
            covered_f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.)
    total = {k: sum(c[k] for c in per.values()) for k in
             ('tp', 'fp', 'fn', 'tn', 'known', 'answered', 'unanswered_positive', 'unanswered_negative')}
    return dict(accuracy=(total['tp'] + total['tn']) / total['known'],
        covered_accuracy=(total['tp'] + total['tn']) / total['answered'] if total['answered'] else None,
        coverage=total['answered'] / total['known'],
        macro_f1_covered=sum(c['covered_f1'] for c in per.values()) / len(LABELS),
        macro_AP=None, **total, masked_unknown_cells=len(rows) * 8 - total['known'], per_label=per,
        policy='Accuracy counts every unanswered known cell as incorrect. FP/FN count explicit binary errors only. '
               'Covered precision/recall/F1 exclude unanswered cells; compare alongside coverage. '
               'No AP from generated binary/null decisions.')


def timing(records):
    keys = ('decode_preprocess_ms', 'transfer_generate_ms', 'decode_parse_ms', 'total_ms')
    return {k: dict(n=len(records), mean_ms=sum(r[k] for r in records) / len(records),
                   median_ms=percentile([r[k] for r in records], .5),
                   p95_ms=percentile([r[k] for r in records], .95)) for k in keys}


def split_report(out, split, truth, siglip, qwen, requests, reference_info, reference_root):
    out = Path(out)
    old, new = metrics(siglip), metrics(qwen)
    old_ap = score_rows(siglip, reference_info['thresholds'])[0]['macro_average_precision']
    qtime = timing(requests)
    stime = read(inside(reference_root, reference_info['model'] + '/timing_summary.json'))['total_ms'] if split == 'test' else None
    rows = []
    for name, score, latency, source, invalid in (
        (reference_info['model'], old, stime, 'historical_saved_run' if stime else 'not_measured', 0),
        ('qwen3_5_9b_zero_shot', new, qtime['total_ms'], 'current_run', sum(bool(r['parse_error']) for r in qwen))):
        rows.append(dict(split=split, model=name, accuracy=score['accuracy'], covered_accuracy=score['covered_accuracy'],
            coverage=score['coverage'], macro_f1_covered=score['macro_f1_covered'],
            macro_AP=old_ap if name == reference_info['model'] else None,
            fp=score['fp'], fn=score['fn'], unanswered=score['known'] - score['answered'], invalid_images=invalid,
            mean_ms=latency['mean_ms'] if latency else None, median_ms=latency['median_ms'] if latency else None,
            p95_ms=latency['p95_ms'] if latency else None, timing_n=latency['n'] if latency else 0,
            latency_source=source, accuracy_90=score['accuracy'] >= .9,
            p95_50=latency['p95_ms'] <= 50 if latency else None))
    label_rows, changes, errors, positive = [], [], [], []
    for j, label in enumerate(LABELS):
        a, b = old['per_label'][label], new['per_label'][label]
        label_rows.append(dict(split=split, label=label,
            **{'prior_' + k: a[k] for k in ('tp', 'fp', 'fn', 'tn')},
            **{'qwen_' + k: b[k] for k in ('tp', 'fp', 'fn', 'tn', 'unanswered_positive', 'unanswered_negative')},
            prior_accuracy=a['accuracy'], qwen_accuracy=b['accuracy'], coverage=b['coverage'],
            fp_delta=b['fp'] - a['fp'], fn_delta=b['fn'] - a['fn']))
        for target, before, after in zip(truth, siglip, qwen):
            y, p, q = target['labels'][label], before['predictions'][j], after['predictions'][j]
            if y is None:
                continue
            detail = dict(split=split, image_id=target['image_id'], image=target['image'], label=label, truth=y,
                siglip_prediction=p, siglip_probability=before['probabilities'][j],
                siglip_threshold=reference_info['thresholds'][j], qwen_prediction=q,
                parse_error=after['parse_error'])
            if p != q:
                changes.append(dict(**detail, change='unanswered' if q is None else ('corrected' if q == y else 'regressed')))
            if p != y or q != y:
                errors.append(dict(**detail, siglip_error=p != y, qwen_error=q != y))
            if label == 'musical_instrument' and y == 1:
                positive.append(dict(**detail, siglip_result='TP' if p == 1 else 'FN',
                                     qwen_result='unanswered' if q is None else ('TP' if q == 1 else 'FN')))
    prefix = out / split
    prefix.mkdir(exist_ok=True)
    save(prefix / 'siglip_predictions.json', siglip)
    save(prefix / 'qwen_predictions.json', qwen)
    save(prefix / 'qwen_metrics.json', new)
    save(prefix / 'qwen_timing_summary.json', qtime)
    save(prefix / 'error_changes.json', changes)
    write_csv(prefix / 'comparison.csv', rows)
    write_csv(prefix / 'per_label_comparison.csv', label_rows)
    fields = ['split', 'image_id', 'image', 'label', 'truth', 'siglip_prediction', 'siglip_probability',
              'siglip_threshold', 'qwen_prediction', 'parse_error']
    write_csv(prefix / 'disagreements.csv', changes, fields + ['change'])
    write_csv(prefix / 'errors.csv', errors, fields + ['siglip_error', 'qwen_error'])
    write_csv(prefix / 'instrument_positive_scores.csv', positive, fields + ['siglip_result', 'qwen_result'])
    write_csv(prefix / 'qwen_timing_raw.csv', requests)
    summary = dict(split=split, comparison=rows, accuracy_delta_pp=(new['accuracy'] - old['accuracy']) * 100,
        known_cells=new['known'], masked_unknown_cells=new['masked_unknown_cells'],
        invalid_images=sum(bool(r['parse_error']) for r in qwen),
        repeat_disagreement_images=sum(r['repeat_disagreement'] for r in qwen),
        baseline_latency_remeasured=False, paired_latency_measurement=False,
        AP_available=False, training_executed=False, model_deployed=False,
        note='Same 384px letterbox input; SigLIP FP32 and Qwen BF16 are different inference configurations. '
             'SigLIP timing is historical test-only evidence; Qwen timing includes preprocessing, transfer, '
             'full generation and JSON decoding/parsing. No OCR/network/DB/model loading/file writes. '
             'First repeat defines accuracy; other repeats measure latency and expose output disagreement. '
             'No prompt/threshold/model selection on either split. Previously exposed test is not independent.')
    save(prefix / 'comparison_summary.json', summary)
    return summary
