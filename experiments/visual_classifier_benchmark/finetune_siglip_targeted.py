"""Instrument MAP-probe tuning and paired LoRA/prediction-preservation trials."""
from pathlib import Path
import argparse
import fcntl
import json
import math
import os
import random
import shutil
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'poster_benchmark'
if not (SOURCE / 'benchlib.py').is_file():
    raise SystemExit(f'Missing source: {SOURCE / "benchlib.py"}; restore the existing project file.')
sys.path.insert(0, str(SOURCE))
from benchlib import LABELS, SEED, contained, csv_rows, digest, load_test, log, metrics, read, save, sha, targets

INSTRUMENT = LABELS.index('musical_instrument')
OTHERS = [i for i in range(8) if i != INSTRUMENT]
CONTROL = 'siglip2_previous_partial'
MODEL_NAMES = (CONTROL, 'siglip2_instrument_attention', 'siglip2_lora', 'siglip2_lora_preserve')
CODE = ('finetune_siglip_targeted.py', 'siglip_targeted_models.py', 'run_targeted_finetune.sh',
        'finetune_siglip_corrected.py', 'siglip_corrected_results.py', 'finetune_siglip.py',
        'finetune_siglip_focused.py', 'prepare_partial_data.py', 'run_experiment.py', 'encoders.py')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def seed():
    import torch
    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)


def sources():
    paths = {name: HERE / name for name in CODE}
    paths['../poster_benchmark/benchlib.py'] = SOURCE / 'benchlib.py'
    require(all(p.is_file() for p in paths.values()), 'Missing project source: ' + ', '.join(str(p) for p in paths.values() if not p.is_file()))
    return {name: sha(p) for name, p in paths.items()}


def counts_at(y, p, cutoff):
    import numpy as np
    mask = np.isfinite(y)
    yy, pp = y[mask], p[mask] >= cutoff
    tp, fp = int(((yy == 1) & pp).sum()), int(((yy == 0) & pp).sum())
    fn, tn = int(((yy == 1) & ~pp).sum()), int(((yy == 0) & ~pp).sum())
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.
    total = tp + fp + fn + tn
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, f1=f1, correct=tp + tn,
                accuracy=(tp + tn) / total if total else 0.)


def calibrate(rows, probabilities, reference, instrument_only):
    """Dev-only cutoffs, with explicit vehicle and instrument false-positive limits."""
    import numpy as np
    y = targets(rows)
    selected, audit = [], []
    for j, label in enumerate(LABELS):
        old = reference['dev_confusion'][label]
        original = float(reference['thresholds'][j])
        if instrument_only and j != INSTRUMENT:
            selected.append(original)
            continue
        grid = sorted({i / 20 for i in range(21)} | {original})
        options = [(cutoff, counts_at(y[:, j], probabilities[:, j], cutoff)) for cutoff in grid]
        if j == INSTRUMENT:
            eligible = [(t, c) for t, c in options if c['fp'] <= old['fp']]
            # Reduce instrument misses without spending additional dev FP budget.
            chosen = max(eligible or options, key=lambda v: (-v[1]['fn'], -v[1]['fp'], v[1]['f1'], -abs(v[0] - original), v[0]))
        elif label == 'vehicle':
            eligible = [(t, c) for t, c in options if c['fp'] <= old['fp'] and c['fn'] <= old['fn']]
            chosen = max(eligible or options, key=lambda v: (v[1]['correct'], v[1]['f1'], -abs(v[0] - original), v[0]))
        else:
            eligible = options
            chosen = max(options, key=lambda v: (v[1]['correct'], v[1]['f1'], -abs(v[0] - original), v[0]))
        selected.append(chosen[0])
        for cutoff, values in options:
            audit.append(dict(label=label, threshold=cutoff, **values, selected=cutoff == chosen[0],
                within_reference_fp=values['fp'] <= old['fp'], within_reference_fn=values['fn'] <= old['fn']))
    require(np.isfinite(probabilities).all(), 'Nonfinite dev probabilities')
    return selected, audit


def guard_reasons(candidate, reference):
    before, after = reference['dev_metrics'], candidate['dev_metrics']
    reasons = []
    if after['label_wise_accuracy'] + 1e-12 < before['label_wise_accuracy']:
        reasons.append('dev_accuracy_below_reference')
    if after['macro_f1'] + 1e-12 < before['macro_f1']:
        reasons.append('dev_macro_f1_below_reference')
    for label in ('vehicle', 'musical_instrument'):
        for error in ('fp', 'fn'):
            if candidate['dev_confusion'][label][error] > reference['dev_confusion'][label][error]:
                reasons.append(label + '_' + error + '_above_reference')
    for label in ('food_drink', 'animal', 'plant_flower'):
        if candidate['dev_confusion'][label]['fp'] > reference['dev_confusion'][label]['fp']:
            reasons.append(label + '_fp_above_reference')
    return reasons


def train_epoch(args, spec, encoder, head, optimizer, train, epoch, teacher=None):
    import numpy as np
    import torch
    from torch.nn import functional as F
    import finetune_siglip as ft
    require(train and all(r['split'] == 'train' for r in train), 'Optimization accepts TRAIN rows only')
    specialist = spec['family'] == 'instrument_attention'
    active = [r for r in train if r['labels']['musical_instrument'] is not None] if specialist else train
    require(active, 'No known training instrument labels')
    order = list(range(len(active)))
    random.Random(SEED + epoch).shuffle(order)
    encoder.train(); head.train()
    trainable = [p for p in (*encoder.parameters(), *head.parameters()) if p.requires_grad]
    bce_sum, known_total, kl_sum, kl_total, steps = 0., 0, 0., 0, 0
    first_audit = None
    for offset in range(0, len(order), 8):
        group = [active[i] for i in order[offset:offset + 8]]
        yy = targets(group)
        known = int(np.isfinite(yy[:, INSTRUMENT]).sum()) if specialist else int(np.isfinite(yy).sum())
        preserved = int(np.isfinite(yy[:, OTHERS]).sum())
        optimizer.zero_grad(set_to_none=True)
        for start in range(0, len(group), args.microbatch):
            batch = group[start:start + args.microbatch]
            x = torch.cat([ft.prepare_train(encoder, args.packet, r, True) for r in batch]).to(args.device)
            y = torch.tensor(targets(batch), device=args.device)
            logits = head(encoder(x))
            if specialist:
                numerator = F.binary_cross_entropy_with_logits(logits[:, INSTRUMENT], y[:, INSTRUMENT], reduction='sum')
                cells = len(batch)
            else:
                numerator, cells = ft.masked_loss(logits, y, torch.ones(8, device=args.device))
            loss = numerator / known
            if teacher is not None and preserved:
                teacher_encoder, teacher_head = teacher
                with torch.no_grad():
                    probability = torch.sigmoid(teacher_head(teacher_encoder(x))[:, OTHERS]).clamp(1e-6, 1 - 1e-6)
                mask = torch.isfinite(y[:, OTHERS])
                # Independent Bernoulli KL, not a softmax over unrelated labels.
                entropy_term = probability * probability.log() + (1 - probability) * torch.log1p(-probability)
                kl = F.binary_cross_entropy_with_logits(logits[:, OTHERS], probability, reduction='none') + entropy_term
                kl_numerator = (kl * mask).sum()
                loss = loss + spec['preserve_weight'] * kl_numerator / preserved
                kl_sum += float(kl_numerator.detach().cpu()); kl_total += int(mask.sum())
            require(bool(torch.isfinite(loss)), 'Nonfinite targeted training objective')
            loss.backward()
            bce_sum += float(numerator.detach().cpu()); known_total += cells
        if first_audit is None:
            first_audit = {}
            for prefix, module in (('encoder', encoder), ('head', head)):
                for name, parameter in module.named_parameters():
                    if not parameter.requires_grad:
                        require(parameter.grad is None, 'Frozen parameter received gradient: ' + prefix + '.' + name)
                    else:
                        require(parameter.grad is not None and bool(torch.isfinite(parameter.grad).all()),
                                'Missing/nonfinite trainable gradient: ' + prefix + '.' + name)
                        first_audit[prefix + '.' + name] = float(parameter.grad.norm().cpu())
            # LoRA A has a legitimate zero gradient initially because B starts at zero.
            require(first_audit and any(v > 0 for v in first_audit.values()), 'No trainable gradient')
        torch.nn.utils.clip_grad_norm_(trainable, 1., error_if_nonfinite=True)
        optimizer.step(); steps += 1
    return dict(train_bce=bce_sum / known_total, train_preserve_kl=kl_sum / kl_total if kl_total else 0.,
        known_training_cells=known_total, preserved_known_cells=kl_total, optimizer_steps=steps,
        active_train_images=len(active), gradient_audit=first_audit)


def train_candidate(args, spec, rows, reference, baseline_head):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    from siglip_targeted_models import InstrumentEncoder, InstrumentOutput, install_lora, merge_lora, load_candidate
    folder = args.run_dir / 'candidates' / spec['id']
    done = folder / 'candidate_complete.json'
    if done.exists():
        cached = read(done)
        require(cached['protocol_signature'] == args.signature and cached['training_rows_sha256'] == digest(rows) and
                all(cached.get(k) == v for k, v in spec.items()), 'Cached targeted candidate differs')
        focused.verify_artifacts(args.run_dir, cached)
        log(args.run_dir, 'targeted_candidate_reused', candidate=spec['id'])
        return cached
    folder.mkdir(parents=True, exist_ok=True)
    train, dev = [r for r in rows if r['split'] == 'train'], [r for r in rows if r['split'] == 'dev']
    require(len(train) + len(dev) == len(rows), 'Train/dev-only packet required')
    seed()
    base, original_head = load_candidate(args, reference, baseline_head)
    before = ft.state(base, original_head)
    initial_groups = ft.group_hashes(base, original_head)
    teacher = load_candidate(args, reference, baseline_head) if spec['preserve_weight'] else None
    seed()
    specialist = spec['family'] == 'instrument_attention'
    if specialist:
        encoder, head = InstrumentEncoder(base, original_head), InstrumentOutput(original_head)
        optimizer = torch.optim.AdamW([
            dict(params=[encoder.pooler.probe], lr=args.probe_lr),
            dict(params=encoder.classifier.parameters(), lr=args.probe_lr)], weight_decay=1e-4, foreach=False)
        lora_paths = []
    else:
        encoder, head = base, original_head
        lora_paths = install_lora(encoder, args.lora_rank, args.lora_alpha, args.tail_blocks)
        head.requires_grad_(True)
        optimizer = torch.optim.AdamW([
            dict(params=[p for p in encoder.parameters() if p.requires_grad], lr=args.lora_lr),
            dict(params=head.parameters(), lr=args.head_lr)], weight_decay=1e-4, foreach=False)
    initial = ft.predict_dev(encoder, head, args.packet, dev)
    expected = np.load(contained(args.run_dir, reference['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
    require(np.allclose(initial, expected, rtol=1e-5, atol=1e-6), 'Candidate initialization no longer reproduces reference probabilities')
    common = ft.common
    common.save_array(folder / 'initial_dev_probabilities.npy', initial)
    history, best_ap, best_epoch, stale, gradient_audit = [], -1., None, 0, None
    trainable_names = [prefix + '.' + n for prefix, module in (('encoder', encoder), ('head', head))
                       for n, p in module.named_parameters() if p.requires_grad]
    seed()  # Loading a teacher must not change the paired LoRA initialization/augmentation stream.
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = train_epoch(args, spec, encoder, head, optimizer, train, epoch, teacher)
        gradient_audit = gradient_audit or trained.pop('gradient_audit')
        trained.pop('gradient_audit', None)
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        dev_metrics = metrics(targets(dev), probabilities, [.5] * 8)
        ap = dev_metrics['per_label']['musical_instrument']['average_precision'] if specialist else dev_metrics['macro_average_precision']
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            if specialist:
                ft.save_weights(folder / 'instrument.safetensors', encoder.extra_state())
            else:
                ft.save_weights(folder / 'adapter_best.safetensors', ft.state(encoder, head))
        else:
            stale += 1
        history.append(dict(epoch=epoch, best_epoch=best_epoch, **trained,
            dev_macro_AP=dev_metrics['macro_average_precision'], dev_instrument_AP=dev_metrics['per_label']['musical_instrument']['average_precision'],
            elapsed_seconds=time.perf_counter() - started))
        save(folder / 'history.json', history)
        log(args.run_dir, 'targeted_epoch', candidate=spec['id'], **history[-1])
        if stale >= args.patience:
            break
    merge_audit = None
    if specialist:
        encoder.load_extra(load_file(str(folder / 'instrument.safetensors')))
        require(ft.group_hashes(base, original_head) == initial_groups, 'Instrument training modified the original backbone/head')
        frozen_pooler = base.backbone.head.state_dict()
        require(all(torch.equal(v.detach().cpu(), frozen_pooler[k].detach().cpu()) for k, v in encoder.pooler.state_dict().items() if k != 'probe'),
                'Instrument tuning modified frozen MAP parameters')
        probe_changed = not torch.equal(encoder.pooler.probe.detach().cpu(), frozen_pooler['probe'].detach().cpu())
        classifier_changed = not torch.equal(encoder.classifier.weight.detach().cpu(), original_head.weight[INSTRUMENT:INSTRUMENT + 1].detach().cpu())
        require(probe_changed or classifier_changed, 'Instrument candidate did not update any trainable parameter')
        shutil.copyfile(contained(args.run_dir, reference['checkpoint']), folder / 'best.safetensors.tmp')
        (folder / 'best.safetensors.tmp').replace(folder / 'best.safetensors')
    else:
        ft.load_state(encoder, head, load_file(str(folder / 'adapter_best.safetensors')))
        unmerged = ft.predict_dev(encoder, head, args.packet, dev)
        merged = merge_lora(encoder)
        require(set(merged) == set(lora_paths), 'Unexpected LoRA merge population')
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        require(np.allclose(probabilities, unmerged, rtol=1e-4, atol=1e-5), 'Merged LoRA probabilities differ materially')
        after = ft.state(encoder, head)
        allowed = {'backbone.' + path + '.weight' for path in lora_paths} | {'classifier.weight', 'classifier.bias'}
        require(set(after) == set(before), 'Merged checkpoint is not compatible with the native encoder')
        changed = [name for name in before if not torch.equal(before[name], after[name])]
        require(changed and set(changed) <= allowed and any(n.startswith('backbone.') for n in changed),
                'LoRA updated unexpected native parameters')
        ft.save_weights(folder / 'best.safetensors', after)
        common.save_array(folder / 'dev_probabilities_unmerged.npy', unmerged)
        merge_audit = dict(merged_paths=merged, changed_native_parameters=changed,
                           max_probability_difference=float(np.max(np.abs(probabilities - unmerged))), adapters_remaining=False)
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    if specialist:
        require(np.allclose(probabilities[:, OTHERS], expected[:, OTHERS], rtol=1e-5, atol=1e-6), 'Instrument branch changed another dev label score')
    cutoffs, threshold_audit = calibrate(dev, probabilities, reference, specialist)
    score = metrics(targets(dev), probabilities, cutoffs)
    if specialist:
        require(np.array_equal(probabilities[:, OTHERS] >= np.asarray(cutoffs)[OTHERS],
                               expected[:, OTHERS] >= np.asarray(reference['thresholds'])[OTHERS]),
                'Instrument branch changed another dev label decision')
    common.save_array(folder / 'dev_probabilities.npy', probabilities)
    csv_rows(folder / 'threshold_audit.csv', threshold_audit)
    save(folder / 'parameter_audit.json', dict(initial_native_groups=initial_groups, trainable_parameters=trainable_names,
        gradient_audit=gradient_audit, original_native_parameters_unchanged=specialist,
        merge=merge_audit, classifier_in_preservation_loss=bool(spec['preserve_weight']),
        teacher_target_labels=OTHERS if spec['preserve_weight'] else [],
        unknown_teacher_cells_excluded=True, source_checkpoint_sha256=sha(contained(args.run_dir, reference['checkpoint']))))
    artifacts = ['best.safetensors', 'initial_dev_probabilities.npy', 'dev_probabilities.npy', 'history.json',
                 'threshold_audit.csv', 'parameter_audit.json']
    artifacts += ['instrument.safetensors'] if specialist else ['adapter_best.safetensors', 'dev_probabilities_unmerged.npy']
    result = dict(**spec, protocol_signature=args.signature, source='trained', training_rows_sha256=digest(rows),
        best_epoch=best_epoch, trained_epochs=len(history), thresholds=cutoffs, dev_metrics=score,
        dev_confusion=focused.confusion(targets(dev), probabilities, cutoffs),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        threshold_policy='dev_recall_under_FP_budget_instrument;dev_accuracy_vehicle_FP_FN_budget;dev_accuracy_others',
        extra_checkpoint=str((folder / 'instrument.safetensors').relative_to(args.run_dir)) if specialist else None,
        backbone_training_executed=not specialist, lora_merged=not specialist,
        artifact_hashes={name: sha(folder / name) for name in artifacts})
    save(done, result)
    del base, original_head, encoder, head, optimizer, teacher, before
    common.release()
    return result


def freeze(args, reference, candidates):
    models = dict(zip(MODEL_NAMES, [reference, *candidates]))
    eligible = [(name, c) for name, c in models.items() if not guard_reasons(c, reference)]
    selected_name, selected = max(eligible, key=lambda item: (
        item[1]['dev_metrics']['label_wise_accuracy'],
        -item[1]['dev_confusion']['musical_instrument']['fn'],
        item[1]['dev_metrics']['macro_f1'], item[1]['dev_metrics']['macro_average_precision'], item[0] == CONTROL))
    reports, labels = [], []
    for name, candidate in models.items():
        reasons = guard_reasons(candidate, reference)
        reports.append(dict(model=name, candidate=candidate['id'], family=candidate['family'], best_epoch=candidate.get('best_epoch'),
            dev_accuracy=candidate['dev_metrics']['label_wise_accuracy'], dev_macro_f1=candidate['dev_metrics']['macro_f1'],
            dev_macro_AP=candidate['dev_metrics']['macro_average_precision'], instrument_fp=candidate['dev_confusion']['musical_instrument']['fp'],
            instrument_fn=candidate['dev_confusion']['musical_instrument']['fn'], vehicle_fp=candidate['dev_confusion']['vehicle']['fp'],
            vehicle_fn=candidate['dev_confusion']['vehicle']['fn'], eligible=not reasons, rejection_reason=';'.join(reasons), selected=name == selected_name))
        for j, label in enumerate(LABELS):
            count, prior = candidate['dev_confusion'][label], reference['dev_confusion'][label]
            labels.append(dict(model=name, label=label, threshold=candidate['thresholds'][j], **count,
                fp_delta=count['fp'] - prior['fp'], fn_delta=count['fn'] - prior['fn'],
                AP=candidate['dev_metrics']['per_label'][label]['average_precision']))
    csv_rows(args.run_dir / 'dev_candidates.csv', reports)
    csv_rows(args.run_dir / 'dev_per_label.csv', labels)
    metadata = {str(Path(c['checkpoint']).parent / 'candidate_complete.json'):
                sha(args.run_dir / Path(c['checkpoint']).parent / 'candidate_complete.json') for c in models.values()}
    save(args.run_dir / 'selection_frozen.json', dict(protocol_signature=args.signature, models=models, selected_model=selected_name,
        selected_candidate=selected['id'], test_used_for_selection=False, previously_exposed_test=True, candidate_records=metadata,
        report_hashes={name: sha(args.run_dir / name) for name in ('dev_candidates.csv', 'dev_per_label.csv')}))
    save(args.run_dir / 'selection_commit.json', dict(sha256=sha(args.run_dir / 'selection_frozen.json')))
    log(args.run_dir, 'targeted_selection_frozen_before_test', selected_model=selected_name)


def frozen(args):
    import finetune_siglip_focused as focused
    path = args.run_dir / 'selection_frozen.json'
    selected = read(path)
    require(read(args.run_dir / 'selection_commit.json')['sha256'] == sha(path) and
            selected['protocol_signature'] == args.signature and set(selected['models']) == set(MODEL_NAMES) and
            selected['selected_model'] in MODEL_NAMES and selected['test_used_for_selection'] is False,
            'Frozen targeted selection changed')
    for candidate in selected['models'].values():
        require(candidate['protocol_signature'] == args.signature, 'Frozen candidate protocol differs')
        focused.verify_artifacts(args.run_dir, candidate)
    for name, checksum in {**selected['candidate_records'], **selected['report_hashes']}.items():
        require(sha(contained(args.run_dir, name)) == checksum, 'Frozen metadata changed: ' + name)
    return selected


def evaluate(args, baseline_head):
    import numpy as np
    import finetune_siglip as ft
    import siglip_corrected_results as results
    from siglip_targeted_models import load_candidate
    selection = frozen(args)
    selection_sha = sha(args.run_dir / 'selection_frozen.json')
    rows = load_test(args.bundle, args.run_dir / 'selection_frozen.json', selection_sha)
    results_by_model, predictions, counts = {}, {}, {}
    for name, candidate in selection['models'].items():
        folder = args.run_dir / name; folder.mkdir(exist_ok=True)
        result = results.cached_evaluation(args, name, candidate, args.signature, selection_sha)
        expected = results.reference_predictions(args, results.PREVIOUS) if name == CONTROL else None
        if result is None:
            encoder = head = None
            try:
                encoder, head = load_candidate(args, candidate, baseline_head)
                result = ft.common.benchmark(encoder, head, args.bundle, rows, np.asarray(candidate['thresholds']), folder, args.repeats, args.warmups)
                result['goal'] = results.goal(result)
                current = read(folder / 'test_predictions.json')
                results.validate_predictions(current, rows, candidate['thresholds'], name)
                results.reproduce_control(name, current, expected)
                if candidate['family'] == 'instrument_attention':
                    require(all(all(a['predictions'][j] == b['predictions'][j] for j in OTHERS)
                                for a, b in zip(predictions[CONTROL], current)),
                            'Instrument branch changed another test label decision')
                save(folder / 'complete.json', dict(protocol_signature=args.signature, selection_sha256=selection_sha,
                    checkpoint_sha256=sha(contained(args.run_dir, candidate['checkpoint'])), result=result,
                    artifact_hashes={n: sha(folder / n) for n in results.ARTIFACTS}))
            finally:
                encoder = head = None
                ft.common.release()
        current = read(folder / 'test_predictions.json')
        results.validate_predictions(current, rows, candidate['thresholds'], name)
        results.reproduce_control(name, current, expected)
        if candidate['family'] == 'instrument_attention':
            require(all(all(a['predictions'][j] == b['predictions'][j] for j in OTHERS)
                        for a, b in zip(predictions[CONTROL], current)), 'Instrument branch changed another test label decision')
        results_by_model[name], predictions[name], counts[name] = result, current, results.label_counts(current)
        log(args.run_dir, 'targeted_model_evaluated', model=name, accuracy=result['metrics']['label_wise_accuracy'], p95_ms=result['timing']['total_ms']['p95_ms'])
    csv_rows(args.run_dir / 'comparison.csv', [dict(model=name, accuracy=r['metrics']['label_wise_accuracy'],
        macro_f1=r['metrics']['macro_f1'], macro_AP=r['metrics']['macro_average_precision'],
        mean_ms=r['timing']['total_ms']['mean_ms'], median_ms=r['timing']['total_ms']['median_ms'], p95_ms=r['timing']['total_ms']['p95_ms'],
        fp=sum(c['fp'] for c in counts[name].values()), fn=sum(c['fn'] for c in counts[name].values()),
        accuracy_90=r['goal']['accuracy_90'], p95_50=r['goal']['p95_50']) for name, r in results_by_model.items()])
    label_rows, changes = [], []
    for name in MODEL_NAMES[1:]:
        for j, label in enumerate(LABELS):
            old, new = counts[CONTROL][label], counts[name][label]
            label_rows.append(dict(reference_model=CONTROL, candidate_model=name, label=label,
                **{'prior_' + k: v for k, v in old.items()}, **{'candidate_' + k: v for k, v in new.items()},
                fp_delta=new['fp'] - old['fp'], fn_delta=new['fn'] - old['fn']))
            for a, b in zip(predictions[CONTROL], predictions[name]):
                truth = a['labels'][label]
                if truth is not None and a['predictions'][j] != b['predictions'][j]:
                    changes.append(dict(reference_model=CONTROL, candidate_model=name, image_id=a['image_id'], label=label, truth=truth,
                        prior_prediction=a['predictions'][j], candidate_prediction=b['predictions'][j],
                        prior_probability=a['probabilities'][j], candidate_probability=b['probabilities'][j],
                        prior_threshold=selection['models'][CONTROL]['thresholds'][j], candidate_threshold=selection['models'][name]['thresholds'][j],
                        change='corrected' if b['predictions'][j] == truth else 'regressed'))
    csv_rows(args.run_dir / 'per_label_comparison.csv', label_rows)
    save(args.run_dir / 'error_changes.json', changes)
    selected_name = selection['selected_model']
    summary = dict(results=results_by_model, selected_model=selected_name,
        selected_candidate=selection['models'][selected_name]['id'],
        goal_met=results_by_model[selected_name]['goal']['all_met'],
        protocol_signature=args.signature, selection_sha256=selection_sha, test_used_for_selection=False, previously_exposed_test=True,
        test_images=len(rows), model_deployed=False, latency_remeasured=True,
        note='Same exposed37 posters. FP32 batch1; one vision tower plus original head, with an extra MAP probe for instrument_attention. '
             'LoRA weights are merged. Includes decode/preprocess/transfer/model/head; excludes OCR/network/DB/model loading/training. '
             'No independent generalization estimate; no deployment.')
    save(args.run_dir / 'comparison_summary.json', summary)
    frozen(args)
    return summary


def run(args):
    source_hashes = sources()
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    import finetune_siglip_corrected as corrected
    from prepare_partial_data import validate_packet
    for root in (args.bundle, args.baseline, args.prior_run, args.previous_run, args.packet, args.model_dir):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir), 'Use a separate new run directory')
    args.device = 'cuda:0'
    manifest, rows = validate_packet(args.packet, args.bundle)
    require(manifest['added_images'] + manifest['changed_train_cells'] > 0, 'Use the existing approved corrected packet')
    environment = ft.common.cuda_environment('fp32')
    baseline, baseline_head, _ = focused.input_protocol(args)
    require(environment == read(args.baseline / 'environment.json') == read(args.prior_run / 'environment.json'), 'Keep the existing GPU/runtime')
    previous = corrected.previous_inputs(args, baseline, environment)
    model_packet = ft.common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == baseline['config']['models']['siglip2'], 'Source model bytes changed')
    fingerprints = corrected.input_hashes(args)
    specs = [dict(id='instrument-attention', family='instrument_attention', preserve_weight=0.),
             dict(id='lora', family='lora', preserve_weight=0.),
             dict(id='lora-preserve', family='lora_preserve', preserve_weight=args.preserve_weight)]
    config = dict(schema='siglip-targeted-v1', environment=environment, model_packet=model_packet,
        script_hashes=source_hashes, input_hashes=fingerprints, candidates=specs, seed=SEED,
        precision='fp32', image_size=384, epochs=args.epochs, patience=args.patience, microbatch=args.microbatch, effective_batch=8,
        lora_rank=args.lora_rank, lora_alpha=args.lora_alpha, tail_blocks=args.tail_blocks, lora_lr=args.lora_lr,
        head_lr=args.head_lr, probe_lr=args.probe_lr, weight_decay=1e-4, clip_grad_norm=1.,
        initialization='previous dev-selected corrected partial for all candidates; LoRA B=0; probe/head cloned',
        specialist='frozen backbone/global8-head; only extra MAP query and instrument linear layer train; other7 cutoffs fixed',
        loss='unweighted masked BCE; specialist uses only known instrument TRAIN cells',
        preservation='mean independent Bernoulli KL to frozen previous model; known non-instrument TRAIN cells only; temperature1',
        checkpoint='instrument dev AP for specialist; dev Macro AP for LoRA; earliest tie',
        calibration='dev grid .05; instrument FN minimized subject to prior FP budget; vehicle accuracy under prior FP/FN budgets; other labels accuracy',
        selection='dev accuracy/F1 >= prior; vehicle/instrument FP/FN and food/animal/plant FP <= prior; prior fallback',
        augmentation='original 384px whole-poster letterbox plus mild ColorJitter; no crops or new labels',
        resume='completed candidates/evaluations reused; interrupted candidate restarts from initialization',
        warmups=args.warmups, repeats=args.repeats, target_accuracy=.9, target_p95_ms=50., test_used_for_selection=False)
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            protocol = read(protocol_path)
            require(args.resume and protocol['signature'] == args.signature == digest(protocol['config']), 'Resume requires identical code/data/settings')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Choose an empty new run directory')
            save(protocol_path, dict(signature=args.signature, config=config))
            save(args.run_dir / 'environment.json', environment)
            save(args.run_dir / 'training_packet_manifest.json', manifest)
            save(args.run_dir / 'training_packet_approvals.json', read(args.packet / 'approvals.json'))
            save(args.run_dir / 'training_data_summary.json', dict(added_images=manifest['added_images'], changed_train_cells=manifest['changed_train_cells'],
                known_cells=manifest['known_cells'], train_images=sum(r['split'] == 'train' for r in rows), dev_images=sum(r['split'] == 'dev' for r in rows),
                test_images_used_in_training=0, approval_note='Original approval provenance copied; independent human reannotation is not claimed.'))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            frozen(args)
            require(status['protocol_signature'] == args.signature and all(sha(contained(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                    'Completed experiment changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature))
        try:
            if not (args.run_dir / 'selection_frozen.json').exists():
                reference = corrected.reference(args, 'reference-partial', args.previous_run, previous, baseline_head, rows)
                candidates = [train_candidate(args, spec, rows, reference, baseline_head) for spec in specs]
                freeze(args, reference, candidates)
            summary = evaluate(args, baseline_head)
            validate_packet(args.packet, args.bundle)
            require(corrected.input_hashes(args) == fingerprints and sources() == source_hashes and
                    ft.common.model_packet(args.model_dir, 'siglip2') == model_packet, 'Inputs or code changed during execution')
            excluded = {'.lock', 'status.json', 'failure.json', 'events.jsonl'}
            hashes = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                      if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature, CUDA_executed=True,
                backbone_training_executed=True, head_training_executed=True, OCR_executed=False, model_deployed=False, artifact_hashes=hashes))
            log(args.run_dir, 'targeted_complete', selected_model=summary['selected_model'], goal_met=summary['goal_met'], run=str(args.run_dir))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=args.signature))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('train', 'show-results'), nargs='?', default='train')
    parser.add_argument('--bundle', type=Path, default=SOURCE / 'assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--previous-run', type=Path, default=HERE / 'runs/siglip-ai-reviewed-v1-fp32')
    parser.add_argument('--packet', type=Path, default=HERE / 'training_packets/siglip-ai-reviewed-v1')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-targeted-v1-fp32')
    parser.add_argument('--epochs', type=int, default=8)
    parser.add_argument('--patience', type=int, default=2)
    parser.add_argument('--microbatch', type=int, choices=(1, 2, 4, 8), default=2)
    parser.add_argument('--lora-rank', type=int, default=4)
    parser.add_argument('--lora-alpha', type=float, default=4.)
    parser.add_argument('--tail-blocks', type=int, choices=range(1, 13), default=4)
    parser.add_argument('--lora-lr', type=float, default=1e-4)
    parser.add_argument('--head-lr', type=float, default=1e-4)
    parser.add_argument('--probe-lr', type=float, default=1e-3)
    parser.add_argument('--preserve-weight', type=float, default=.1)
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('bundle', 'baseline', 'prior_run', 'previous_run', 'packet', 'model_dir', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.command == 'show-results':
        import finetune_siglip_corrected as corrected
        corrected.show_results(args)
        return
    require(min(args.epochs, args.patience, args.warmups, args.repeats, args.lora_rank) > 0 and
            all(math.isfinite(v) and v > 0 for v in (args.lora_alpha, args.lora_lr, args.head_lr, args.probe_lr, args.preserve_weight)),
            'Counts, ranks, learning rates and preservation weight must be positive and finite')
    run(args)


if __name__ == '__main__':
    main()
