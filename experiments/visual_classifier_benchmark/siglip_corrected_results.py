"""Evaluate frozen corrected-label SigLIP2 choices on the exposed test posters.

This module never fits a head, chooses a model using test scores, or installs
packages. All compared checkpoints have the same 384px FP32 inference graph.
"""
from pathlib import Path
import math

import finetune_siglip as ft
import finetune_siglip_focused as focused
from finetune_siglip import common
from benchlib import LABELS, contained, csv_rows, digest, load_test, log, read, save, sha


ORIGINAL = 'siglip2_full_finetune'
PREVIOUS = 'siglip2_previous_partial'
NEW_MODELS = ('siglip2_corrected_refit', 'siglip2_corrected_pretrained_head',
              'siglip2_corrected_full', 'siglip2_corrected_l2sp')
MODELS = (ORIGINAL, PREVIOUS, *NEW_MODELS)
ARTIFACTS = ('test_predictions.json', 'test_metrics.json', 'timing_raw.csv', 'timing_summary.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checkpoint_path(args, candidate):
    relative = candidate['checkpoint']
    require(isinstance(relative, str) and not Path(relative).is_absolute(),
            'Compared checkpoints must be relative to the new run directory')
    path = contained(args.run_dir, relative)
    require(path.name in candidate['artifact_hashes'], 'Checkpoint checksum is missing: ' + relative)
    focused.verify_artifacts(args.run_dir, candidate)
    require(sha(path) == candidate['artifact_hashes'][path.name], 'Selected checkpoint changed: ' + relative)
    return path


def frozen_selection(args, selections, selection_sha):
    selection_path = contained(args.run_dir, 'selection_frozen.json')
    selected = read(selection_path)
    protocol = read(contained(args.run_dir, 'protocol.json'))
    require(protocol['signature'] == digest(protocol['config']), 'Evaluation protocol config changed')
    require(sha(selection_path) == selection_sha
            and read(contained(args.run_dir, 'selection_commit.json'))['sha256'] == selection_sha,
            'Frozen evaluation selection changed')
    require(selected['protocol_signature'] == protocol['signature'], 'Frozen selection protocol mismatch')
    require(selected.get('test_used_for_selection') is False, 'Test must not be used for model selection')
    require(set(selections) == set(MODELS) and selected['models'] == selections,
            'Freeze all six control/corrected model choices before test access')
    require(selected['selected_model'] in selections, 'Unknown dev-selected model')
    for candidate in selections.values():
        require(len(candidate['thresholds']) == len(LABELS)
                and all(type(t) in (int, float) and math.isfinite(t) and 0 <= t <= 1
                        for t in candidate['thresholds']), 'Invalid selected thresholds')
        checkpoint_path(args, candidate)
    return selected, protocol['signature']


def validate_predictions(predictions, rows, cutoffs, context):
    require(len(predictions) == len(rows), context + ': prediction population differs')
    for prediction, row in zip(predictions, rows):
        require(prediction['image_id'] == row['image_id'] and prediction['labels'] == row['labels'],
                context + ': image IDs/labels/order differ')
        probabilities, decisions = prediction['probabilities'], prediction['predictions']
        require(len(probabilities) == len(decisions) == len(LABELS), context + ': prediction width differs')
        require(all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities),
                context + ': nonfinite/invalid probabilities')
        require(all(type(d) is int and d in (0, 1) and d == int(p >= t)
                    for p, t, d in zip(probabilities, cutoffs, decisions)),
                context + ': frozen threshold decisions differ')


def label_counts(predictions):
    counts = {label: dict(tp=0, fp=0, fn=0, tn=0) for label in LABELS}
    for row in predictions:
        for j, label in enumerate(LABELS):
            truth = row['labels'][label]
            if truth is not None:
                decision = row['predictions'][j]
                key = ('tp' if decision else 'fn') if truth else ('fp' if decision else 'tn')
                counts[label][key] += 1
    return counts


def goal(result):
    accuracy = result['metrics']['label_wise_accuracy']
    p95 = result['timing']['total_ms']['p95_ms']
    require(math.isfinite(accuracy) and math.isfinite(p95), 'Nonfinite comparison score/timing')
    return dict(accuracy_90=accuracy >= .9, p95_50=p95 <= 50, all_met=accuracy >= .9 and p95 <= 50)


def reference_predictions(args, name):
    if name == ORIGINAL:
        folder = contained(args.prior_run, ORIGINAL)
    elif name == PREVIOUS:
        folder = contained(args.previous_run, 'siglip2_partial')
    else:
        return None
    # Reading old test predictions is allowed only after the new choices freeze.
    complete = read(contained(folder, 'complete.json'))
    expected = complete['artifact_hashes']['test_predictions.json']
    path = contained(folder, 'test_predictions.json')
    require(sha(path) == expected, 'Previous control predictions changed: ' + name)
    return read(path)


def reproduce_control(name, current, expected):
    if expected is None:
        return
    require(len(current) == len(expected) and all(
        a['image_id'] == b['image_id'] and a['labels'] == b['labels']
        and a['predictions'] == b['predictions'] for a, b in zip(current, expected)),
        'Previous control image/label/decisions did not reproduce: ' + name)


def cached_evaluation(args, name, candidate, signature, selection_sha):
    folder = contained(args.run_dir, name)
    done = contained(folder, 'complete.json')
    if not done.exists():
        return None
    cached = read(done)
    require(cached['protocol_signature'] == signature and cached['selection_sha256'] == selection_sha,
            'Cached comparison protocol/selection changed: ' + name)
    require(cached['checkpoint_sha256'] == candidate['artifact_hashes'][Path(candidate['checkpoint']).name],
            'Cached comparison checkpoint changed: ' + name)
    require(set(cached['artifact_hashes']) == set(ARTIFACTS), 'Cached comparison artifact set differs: ' + name)
    for artifact, checksum in cached['artifact_hashes'].items():
        require(sha(contained(folder, artifact)) == checksum, 'Cached comparison artifact changed: ' + name + '/' + artifact)
    result = cached['result']
    require(result['metrics'] == read(contained(folder, 'test_metrics.json'))
            and result['timing'] == read(contained(folder, 'timing_summary.json')),
            'Cached result and saved metrics/timing disagree: ' + name)
    require(result['goal'] == goal(result), 'Cached goal definition changed: ' + name)
    return result


def comparison_details(args, selections, predictions, counts):
    records, changes = [], []
    for reference in (ORIGINAL, PREVIOUS):
        for candidate in NEW_MODELS:
            old, new = predictions[reference], predictions[candidate]
            require(len(old) == len(new) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
                    for a, b in zip(old, new)), 'Compared prediction populations differ')
            for j, label in enumerate(LABELS):
                prior, current = counts[reference][label], counts[candidate][label]
                records.append(dict(reference_model=reference, candidate_model=candidate, label=label,
                    **{'prior_' + k: v for k, v in prior.items()},
                    **{'candidate_' + k: v for k, v in current.items()},
                    fp_delta=current['fp'] - prior['fp'], fn_delta=current['fn'] - prior['fn']))
                for a, b in zip(old, new):
                    truth = a['labels'][label]
                    if truth is not None and a['predictions'][j] != b['predictions'][j]:
                        changes.append(dict(reference_model=reference, candidate_model=candidate,
                            image_id=a['image_id'], label=label, truth=truth,
                            prior_prediction=a['predictions'][j], candidate_prediction=b['predictions'][j],
                            prior_probability=a['probabilities'][j], candidate_probability=b['probabilities'][j],
                            prior_threshold=selections[reference]['thresholds'][j],
                            candidate_threshold=selections[candidate]['thresholds'][j],
                            change='corrected' if b['predictions'][j] == truth else 'regressed'))
    csv_rows(contained(args.run_dir, 'per_label_comparison.csv'), records)
    save(contained(args.run_dir, 'error_changes.json'), changes)


def evaluate(args, selections, baseline_head, selection_sha):
    """Remeasure all frozen choices; the dev-selected model alone sets goal_met."""
    import numpy as np
    import torch
    from encoders import load_encoder, make_head
    from safetensors.torch import load_file

    frozen, signature = frozen_selection(args, selections, selection_sha)
    selection_path = contained(args.run_dir, 'selection_frozen.json')
    rows = load_test(args.bundle, selection_path, selection_sha)
    require(len(rows) == 37, 'Expected the original exposed37 test population')
    require(str(args.device).startswith('cuda') and torch.cuda.is_available(), 'CUDA is required for latency comparison')
    results, predictions, counts = {}, {}, {}
    for name, candidate in selections.items():
        # Re-check checkpoint bytes even when a previous evaluation is reusable.
        checkpoint = checkpoint_path(args, candidate)
        folder = contained(args.run_dir, name)
        folder.mkdir(parents=True, exist_ok=True)
        expected = reference_predictions(args, name)
        result = cached_evaluation(args, name, candidate, signature, selection_sha)
        if result is None:
            encoder = head = weights = None
            try:
                encoder = load_encoder('siglip2', args.model_dir, args.device, 'fp32', 384)
                head = make_head(baseline_head, args.device)
                weights = load_file(str(checkpoint))
                ft.load_state(encoder, head, weights)
                weights = None
                encoder.eval().requires_grad_(False)
                head.eval().requires_grad_(False)
                torch.cuda.reset_peak_memory_stats(args.device)
                result = common.benchmark(encoder, head, args.bundle, rows,
                    np.asarray(candidate['thresholds']), folder, args.repeats, args.warmups)
                result['peak_inference_cuda_bytes'] = torch.cuda.max_memory_allocated(args.device)
                # The median remains a reported timing statistic, never a goal condition.
                result['goal'] = goal(result)
                current = read(contained(folder, 'test_predictions.json'))
                validate_predictions(current, rows, candidate['thresholds'], name)
                reproduce_control(name, current, expected)
                if expected is not None:
                    result['original_control_decisions_reproduced'] = True
                save(contained(folder, 'complete.json'), dict(protocol_signature=signature,
                    selection_sha256=selection_sha, checkpoint_sha256=sha(checkpoint), result=result,
                    artifact_hashes={artifact: sha(contained(folder, artifact)) for artifact in ARTIFACTS}))
                log(args.run_dir, 'corrected_model_evaluated', model=name,
                    accuracy=result['metrics']['label_wise_accuracy'], p95_ms=result['timing']['total_ms']['p95_ms'])
            finally:
                encoder = head = weights = None
                common.release()
        else:
            log(args.run_dir, 'corrected_evaluation_reused', model=name)
        current = read(contained(folder, 'test_predictions.json'))
        validate_predictions(current, rows, candidate['thresholds'], name)
        reproduce_control(name, current, expected)
        results[name], predictions[name] = result, current
        counts[name] = label_counts(current)
        require(sum(sum(c.values()) for c in counts[name].values()) == 282, 'Known test-label mask changed')
    frozen_selection(args, selections, selection_sha)
    csv_rows(contained(args.run_dir, 'comparison.csv'), [dict(model=name,
        accuracy=r['metrics']['label_wise_accuracy'], macro_f1=r['metrics']['macro_f1'],
        macro_AP=r['metrics']['macro_average_precision'], mean_ms=r['timing']['total_ms']['mean_ms'],
        median_ms=r['timing']['total_ms']['median_ms'], p95_ms=r['timing']['total_ms']['p95_ms'],
        fp=sum(c['fp'] for c in counts[name].values()), fn=sum(c['fn'] for c in counts[name].values()),
        accuracy_90=r['goal']['accuracy_90'], p95_50=r['goal']['p95_50']) for name, r in results.items()])
    comparison_details(args, selections, predictions, counts)
    selected_name = frozen['selected_model']
    deltas = {}
    for name in NEW_MODELS:
        deltas[name] = {}
        for reference in (ORIGINAL, PREVIOUS):
            deltas[name][reference] = dict(
                accuracy_delta_pp=(results[name]['metrics']['label_wise_accuracy'] - results[reference]['metrics']['label_wise_accuracy']) * 100,
                macro_f1_delta=results[name]['metrics']['macro_f1'] - results[reference]['metrics']['macro_f1'],
                fp_delta=sum(counts[name][k]['fp'] - counts[reference][k]['fp'] for k in LABELS),
                fn_delta=sum(counts[name][k]['fn'] - counts[reference][k]['fn'] for k in LABELS))
    summary = dict(status='complete', results=results, selected_model=selected_name,
        selected_candidate=selections[selected_name].get('id'),
        selected_epoch=selections[selected_name].get('best_epoch'),
        goal_met=results[selected_name]['goal']['all_met'], goal_by_model={name: r['goal']['all_met'] for name, r in results.items()},
        deltas=deltas, test_images=37, known_cells=282, unknown_excluded=14,
        test_used_for_selection=False, previously_exposed_test=True, model_deployed=False,
        CUDA_executed=True, OCR_executed=False, inference_precision='fp32', input_size=384,
        latency_remeasured=True, selection_sha256=selection_sha, protocol_signature=signature,
        note='Same exposed37 posters; not a new independent generalization estimate. All compared models use one '
             'SigLIP2 384px vision tower plus an eight-label head, FP32 batch1. Decode/EXIF/letterbox/normalization/'
             'transfer/vision/head/output synchronization included; OCR/network/DB/model loading/training/file saving '
             'and TensorRT excluded. The median is descriptive only; goal is accuracy>=90% and p95<=50ms.')
    save(contained(args.run_dir, 'comparison_summary.json'), summary)
    return summary
