"""Instrument-only loss/selection ablation; completed v1/v2 sources stay intact."""
from pathlib import Path
import argparse
import copy
import fcntl
import math
import os
import random
import shutil
import traceback

import finetune_siglip_targeted as targeted
import finetune_siglip_targeted_v2 as v2
from benchlib import LABELS, SEED, contained, csv_rows, digest, load_test, log, metrics, read, save, sha, targets

HERE, SOURCE = targeted.HERE, targeted.SOURCE
require = targeted.require
INSTRUMENT, OTHERS = targeted.INSTRUMENT, targeted.OTHERS
LABEL = LABELS[INSTRUMENT]
CONTROL = targeted.CONTROL
SOURCE_MODEL = 'siglip2_instrument_attention'
PRIOR_ATTENTION = 'siglip2_instrument_attention_v2'
AP_CONTROL = 'siglip2_instrument_ap_control'
BALANCED = 'siglip2_instrument_balanced'
MODELS = (CONTROL, PRIOR_ATTENTION, AP_CONTROL, BALANCED)
NEW_MODELS = (AP_CONTROL, BALANCED)


def sources():
    result = v2.sources()
    for name in ('finetune_siglip_instrument_balanced.py', 'run_instrument_balanced.sh'):
        require((HERE / name).is_file(), 'Missing new source: ' + name)
        result[name] = sha(HERE / name)
    return result


def support(rows):
    require(rows and all(row['split'] == 'train' for row in rows), 'Weights require TRAIN rows only')
    positive = sum(row['labels'][LABEL] == 1 for row in rows)
    negative = sum(row['labels'][LABEL] == 0 for row in rows)
    require(positive > 0 and negative > 0, 'Known train instrument positives and negatives required')
    total = positive + negative
    return dict(positive=positive, negative=negative, unknown=len(rows) - total,
                positive_weight=total / (2 * positive), negative_weight=total / (2 * negative),
                equivalent_pos_weight=negative / positive)


def development_losses(rows, probabilities):
    import numpy as np
    y = targets(rows)[:, INSTRUMENT]
    p = np.clip(probabilities[:, INSTRUMENT].astype(np.float64), 1e-7, 1 - 1e-7)
    require(np.isfinite(probabilities).all(), 'Nonfinite development scores')
    positive, negative = y == 1, y == 0
    require(positive.any() and negative.any(), 'Known dev instrument positives and negatives required')
    positive_bce = float(-np.log(p[positive]).mean())
    negative_bce = float(-np.log1p(-p[negative]).mean())
    known = positive | negative
    truth = y[known]
    unweighted = float((-(truth * np.log(p[known]) + (1 - truth) * np.log1p(-p[known]))).mean())
    return dict(dev_instrument_BCE=unweighted,
                dev_positive_BCE=positive_bce, dev_negative_BCE=negative_bce,
                dev_balanced_BCE=(positive_bce + negative_bce) / 2,
                dev_positive_mean_score=float(p[positive].mean()),
                dev_negative_mean_score=float(p[negative].mean()),
                dev_positive=int(positive.sum()), dev_negative=int(negative.sum()))


def score_dev(rows, probabilities, reference):
    import finetune_siglip_focused as focused
    cutoffs, audit = targeted.calibrate(rows, probabilities, reference, True)
    return dict(thresholds=cutoffs,
                dev_metrics=metrics(targets(rows), probabilities, cutoffs),
                dev_confusion=focused.confusion(targets(rows), probabilities, cutoffs)), audit


def development_key(scored, losses, reference):
    """No test information: pass original guards, then reduce instrument misses."""
    counts = scored['dev_confusion'][LABEL]
    return (not targeted.guard_reasons(scored, reference), -counts['fn'], -counts['fp'],
            scored['dev_metrics']['per_label'][LABEL]['average_precision'],
            -losses['dev_balanced_BCE'])


def input_hashes(args):
    import finetune_siglip_corrected as corrected
    result = corrected.input_hashes(args)
    for name in ('protocol.json', 'status.json', 'selection_frozen.json', 'selection_commit.json'):
        result['source_run/' + name] = sha(args.source_run / name)
    return result


def source_selection(args, environment, model_packet):
    protocol = read(args.source_run / 'protocol.json')
    status = read(args.source_run / 'status.json')
    selected = read(args.source_run / 'selection_frozen.json')
    require(status['status'] == 'complete' and
            protocol['signature'] == digest(protocol['config']) == status['protocol_signature'] == selected['protocol_signature'],
            'Complete, unchanged source v2 run required')
    require(read(args.source_run / 'selection_commit.json')['sha256'] == sha(args.source_run / 'selection_frozen.json'),
            'Source v2 selection changed')
    config = protocol['config']
    require(config['environment'] == environment and config['model_packet'] == model_packet,
            'Keep the same source GPU/runtime/model bytes')
    require(config['input_hashes']['base_manifest'] == sha(args.bundle / 'bundle_manifest.json'),
            'Source v2 used another base split')
    for filename, checksum in config['script_hashes'].items():
        require(sha(HERE / filename) == checksum, 'Completed source v2 code changed: ' + filename)
    candidate = selected['models'][SOURCE_MODEL]
    record = str(Path(candidate['checkpoint']).parent / 'candidate_complete.json')
    require(selected['candidate_records'][record] == sha(contained(args.source_run, record)),
            'Source attention candidate record changed')
    require(candidate['family'] == 'instrument_attention', 'Expected the existing attention branch')
    import finetune_siglip_focused as focused
    focused.verify_artifacts(args.source_run, candidate)
    return candidate


def import_attention(args, source, reference, rows, baseline_head):
    import numpy as np
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    from siglip_targeted_models import load_candidate
    folder = args.run_dir / 'candidates' / 'reference-augment-attention'
    done = folder / 'candidate_complete.json'
    identity = dict(source_selection_sha256=sha(args.source_run / 'selection_frozen.json'),
                    source_checkpoint_sha256=sha(contained(args.source_run, source['checkpoint'])),
                    source_extra_sha256=sha(contained(args.source_run, source['extra_checkpoint'])))
    if done.exists():
        candidate = read(done)
        require(candidate['protocol_signature'] == args.signature and candidate['reference_identity'] == identity,
                'Imported attention reference changed')
        focused.verify_artifacts(args.run_dir, candidate)
        return candidate
    folder.mkdir(parents=True, exist_ok=True)
    for field, name in (('checkpoint', 'best.safetensors'), ('extra_checkpoint', 'instrument.safetensors')):
        shutil.copyfile(contained(args.source_run, source[field]), folder / name)
    candidate = copy.deepcopy(source)
    candidate.update(id='reference-augment-attention', source='prior', protocol_signature=args.signature,
                     selected_checkpoint_source='reference', training_executed_current_run=False,
                     checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
                     extra_checkpoint=str((folder / 'instrument.safetensors').relative_to(args.run_dir)),
                     reference_identity=identity)
    encoder, head = load_candidate(args, candidate, baseline_head)
    try:
        dev = [row for row in rows if row['split'] == 'dev']
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        expected = np.load(contained(args.source_run, source['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
        require(np.allclose(probabilities, expected, rtol=1e-5, atol=1e-6), 'Imported v2 attention no longer reproduces dev scores')
        candidate['dev_metrics'] = metrics(targets(dev), probabilities, candidate['thresholds'])
        candidate['dev_confusion'] = focused.confusion(targets(dev), probabilities, candidate['thresholds'])
        candidate['development_losses'] = development_losses(dev, probabilities)
        original = np.load(contained(args.run_dir, reference['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
        require(np.allclose(probabilities[:, OTHERS], original[:, OTHERS], rtol=1e-5, atol=1e-6),
                'Source attention changed another label score')
        ft.common.save_array(folder / 'dev_probabilities.npy', probabilities)
    finally:
        encoder = head = None
        ft.common.release()
    candidate['artifact_hashes'] = {name: sha(folder / name) for name in
                                  ('best.safetensors', 'instrument.safetensors', 'dev_probabilities.npy')}
    save(done, candidate)
    return candidate


def train_epoch(args, spec, encoder, head, optimizer, train, epoch, weights):
    import torch
    from torch.nn import functional as F
    import finetune_siglip as ft
    active = [row for row in train if row['labels'][LABEL] is not None]
    require(active and all(row['split'] == 'train' for row in active), 'Known TRAIN labels required')
    order = list(range(len(active)))
    random.Random(SEED + epoch).shuffle(order)
    positive_weight = weights['positive_weight'] if spec['balanced'] else 1.
    negative_weight = weights['negative_weight'] if spec['balanced'] else 1.
    encoder.train(); head.train()
    trainable = [parameter for parameter in encoder.parameters() if parameter.requires_grad]
    losses, weighted_losses, class_losses, class_scores = 0., 0., [0., 0.], [0., 0.]
    counts, steps, gradient_audit = [0, 0], 0, None
    for offset in range(0, len(order), 8):
        group = [active[index] for index in order[offset:offset + 8]]
        optimizer.zero_grad(set_to_none=True)
        for start in range(0, len(group), args.microbatch):
            batch = group[start:start + args.microbatch]
            x = torch.cat([ft.prepare_train(encoder, args.packet, row, True) for row in batch]).to(args.device)
            y = torch.tensor([row['labels'][LABEL] for row in batch], device=args.device, dtype=torch.float32)
            logits = head(encoder(x))[:, INSTRUMENT]
            elementwise = F.binary_cross_entropy_with_logits(logits, y, reduction='none')
            class_weight = torch.where(y == 1, logits.new_tensor(positive_weight), logits.new_tensor(negative_weight))
            weighted = (elementwise * class_weight).sum()
            loss = weighted / len(group)
            require(bool(torch.isfinite(loss)), 'Nonfinite instrument training loss')
            loss.backward()
            losses += float(elementwise.detach().sum().cpu())
            weighted_losses += float(weighted.detach().cpu())
            scores = logits.detach().sigmoid()
            for value in (0, 1):
                mask = y == value
                counts[value] += int(mask.sum())
                class_losses[value] += float(elementwise.detach()[mask].sum().cpu())
                class_scores[value] += float(scores[mask].sum().cpu())
        finite = all(parameter.grad is None or bool(torch.isfinite(parameter.grad).all()) for parameter in trainable)
        require(finite, 'Nonfinite instrument gradient')
        if gradient_audit is None:
            gradients = {name: float(parameter.grad.detach().norm().cpu()) for name, parameter in encoder.named_parameters()
                         if parameter.requires_grad and parameter.grad is not None}
            require(any(value > 0 for value in gradients.values()), 'No trainable instrument gradient')
            require(all(parameter.grad is None for parameter in encoder.base.parameters()), 'Frozen backbone received a gradient')
            gradient_audit = dict(trainable_gradient_norms=gradients, frozen_backbone_gradient=False)
        torch.nn.utils.clip_grad_norm_(trainable, 1.)
        optimizer.step(); steps += 1
    require(counts == [weights['negative'], weights['positive']], 'Training image population changed')
    return dict(train_BCE=losses / len(active), train_objective=weighted_losses / len(active),
                train_positive_BCE=class_losses[1] / counts[1], train_negative_BCE=class_losses[0] / counts[0],
                train_positive_online_mean_score=class_scores[1] / counts[1],
                train_negative_online_mean_score=class_scores[0] / counts[0],
                active_train_images=len(active), optimizer_steps=steps, gradient_audit=gradient_audit)


def train_candidate(args, spec, rows, reference, baseline_head, weights):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    from siglip_targeted_models import InstrumentEncoder, InstrumentOutput, load_candidate
    folder = args.run_dir / 'candidates' / spec['id']
    done = folder / 'candidate_complete.json'
    if done.exists():
        cached = read(done)
        require(cached['protocol_signature'] == args.signature and cached['training_rows_sha256'] == digest(rows)
                and all(cached[key] == value for key, value in spec.items()), 'Cached instrument candidate differs')
        focused.verify_artifacts(args.run_dir, cached)
        log(args.run_dir, 'instrument_candidate_reused', candidate=spec['id'])
        return cached
    folder.mkdir(parents=True, exist_ok=True)
    train, dev = ([row for row in rows if row['split'] == split] for split in ('train', 'dev'))
    targeted.seed()
    base, original_head = load_candidate(args, reference, baseline_head)
    initial_groups = ft.group_hashes(base, original_head)
    targeted.seed()
    encoder, head = InstrumentEncoder(base, original_head), InstrumentOutput(original_head)
    optimizer = torch.optim.AdamW([
        dict(params=[encoder.pooler.probe], lr=args.probe_lr),
        dict(params=encoder.classifier.parameters(), lr=args.probe_lr)], weight_decay=1e-4, foreach=False)
    initial = ft.predict_dev(encoder, head, args.packet, dev)
    expected = np.load(contained(args.run_dir, reference['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
    require(np.allclose(initial, expected, rtol=1e-5, atol=1e-6), 'Initialization does not reproduce the previous partial model')
    ft.common.save_array(folder / 'initial_dev_probabilities.npy', initial)
    initial_losses = development_losses(dev, initial)
    initial_scored, _ = score_dev(dev, initial, reference)
    best_key, best_epoch = development_key(initial_scored, initial_losses, reference), 0
    checkpoint = folder / 'instrument.safetensors'
    ft.save_weights(checkpoint, encoder.extra_state())
    save(folder / 'initial_dev_metrics.json', dict(epoch=0, **initial_losses, **initial_scored))
    history, first_gradient = [], None
    targeted.seed()
    # Both loss variants receive the same complete epoch budget; no AP-only early stop.
    for epoch in range(1, args.epochs + 1):
        trained = train_epoch(args, spec, encoder, head, optimizer, train, epoch, weights)
        gradient = trained.pop('gradient_audit')
        first_gradient = first_gradient or gradient
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        losses = development_losses(dev, probabilities)
        scored, _ = score_dev(dev, probabilities, reference)
        key = development_key(scored, losses, reference)
        updated = key > best_key
        if updated:
            best_key, best_epoch = key, epoch
            ft.save_weights(checkpoint, encoder.extra_state())
        confusion = scored['dev_confusion'][LABEL]
        row = dict(epoch=epoch, best_epoch=best_epoch, **trained, **losses,
                   dev_instrument_AP=scored['dev_metrics']['per_label'][LABEL]['average_precision'],
                   dev_accuracy=scored['dev_metrics']['label_wise_accuracy'],
                   dev_macro_f1=scored['dev_metrics']['macro_f1'],
                   dev_instrument_fp=confusion['fp'], dev_instrument_fn=confusion['fn'],
                   dev_instrument_threshold=scored['thresholds'][INSTRUMENT],
                   eligible=not targeted.guard_reasons(scored, reference), checkpoint_updated=updated,
                   **v2.movement(probabilities, initial))
        history.append(row)
        save(folder / 'history.json', history)
        log(args.run_dir, 'instrument_balanced_epoch', candidate=spec['id'], **row)
    trained_change = (not torch.equal(encoder.pooler.probe.detach().cpu(), base.backbone.head.probe.detach().cpu()) or
                      not torch.equal(encoder.classifier.weight.detach().cpu(), original_head.weight[INSTRUMENT:INSTRUMENT + 1].detach().cpu()) or
                      not torch.equal(encoder.classifier.bias.detach().cpu(), original_head.bias[INSTRUMENT:INSTRUMENT + 1].detach().cpu()))
    require(trained_change, 'Instrument parameters never changed during training')
    encoder.load_extra(load_file(str(checkpoint)))
    require(ft.group_hashes(base, original_head) == initial_groups, 'Original frozen model changed')
    frozen_pooler = base.backbone.head.state_dict()
    require(all(torch.equal(value.detach().cpu(), frozen_pooler[name].detach().cpu())
                for name, value in encoder.pooler.state_dict().items() if name != 'probe'), 'Frozen pooler parameter changed')
    shutil.copyfile(contained(args.run_dir, reference['checkpoint']), folder / 'best.safetensors.tmp')
    (folder / 'best.safetensors.tmp').replace(folder / 'best.safetensors')
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    require(np.allclose(probabilities[:, OTHERS], expected[:, OTHERS], rtol=1e-5, atol=1e-6), 'Another dev label score changed')
    scored, audit = score_dev(dev, probabilities, reference)
    require(np.array_equal(probabilities[:, OTHERS] >= np.asarray(scored['thresholds'])[OTHERS],
                           expected[:, OTHERS] >= np.asarray(reference['thresholds'])[OTHERS]), 'Another dev label decision changed')
    losses = development_losses(dev, probabilities)
    ft.common.save_array(folder / 'dev_probabilities.npy', probabilities)
    csv_rows(folder / 'threshold_audit.csv', audit)
    save(folder / 'checkpoint_selection.json', dict(best_epoch=best_epoch, trained_epochs=len(history),
         selected_checkpoint_source='trained' if best_epoch else 'initialization', stop_reason='fixed_epoch_budget',
         checkpoint_monitor='original dev guards; instrument FN, FP, AP, balanced BCE; epoch0 fallback',
         initial_development_losses=initial_losses, selected_development_losses=losses,
         selected_probability_change=v2.movement(probabilities, initial)))
    save(folder / 'parameter_audit.json', dict(original_native_parameters_unchanged=True,
         initial_native_groups=initial_groups, gradient_audit=first_gradient,
         trainable_parameters=[name for name, parameter in encoder.named_parameters() if parameter.requires_grad],
         source_checkpoint_sha256=sha(contained(args.run_dir, reference['checkpoint'])),
         loss_weights=weights if spec['balanced'] else dict(positive_weight=1., negative_weight=1.)))
    artifacts = ('best.safetensors', 'instrument.safetensors', 'dev_probabilities.npy', 'initial_dev_probabilities.npy',
                 'initial_dev_metrics.json', 'history.json', 'threshold_audit.csv', 'checkpoint_selection.json', 'parameter_audit.json')
    candidate = dict(**spec, protocol_signature=args.signature, training_rows_sha256=digest(rows),
         source='trained' if best_epoch else 'initialization', selected_checkpoint_source='trained' if best_epoch else 'initialization',
         trained_epochs=len(history), best_epoch=best_epoch, stop_reason='fixed_epoch_budget',
         selected_dev_BCE=losses['dev_instrument_BCE'], development_losses=losses, **scored,
         checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
         extra_checkpoint=str(checkpoint.relative_to(args.run_dir)),
         backbone_training_executed=False, head_training_executed=True, instrument_probe_training_executed=True,
         threshold_policy='unchanged dev-only instrument FP budget; other thresholds copied from original partial',
         artifact_hashes={name: sha(folder / name) for name in artifacts})
    save(done, candidate)
    del encoder, head, base, original_head, optimizer
    ft.common.release()
    return candidate


def freeze(args, models):
    reference = models[CONTROL]
    eligible = [(name, candidate) for name, candidate in models.items() if not targeted.guard_reasons(candidate, reference)]
    selected_name, selected = max(eligible, key=lambda item: (
        item[1]['dev_metrics']['label_wise_accuracy'], -item[1]['dev_confusion'][LABEL]['fn'],
        item[1]['dev_metrics']['macro_f1'], item[1]['dev_metrics']['per_label'][LABEL]['average_precision'], item[0] == CONTROL))
    reports, label_reports = [], []
    for name, candidate in models.items():
        reasons = targeted.guard_reasons(candidate, reference)
        losses = candidate.get('development_losses', {})
        counts = candidate['dev_confusion'][LABEL]
        reports.append(dict(model=name, candidate=candidate['id'], family=candidate['family'],
            balanced=candidate.get('balanced', ''), trained_epochs=candidate.get('trained_epochs', 0),
            best_epoch=candidate.get('best_epoch'), current_run_training_executed=name in NEW_MODELS,
            dev_accuracy=candidate['dev_metrics']['label_wise_accuracy'], dev_macro_f1=candidate['dev_metrics']['macro_f1'],
            dev_macro_AP=candidate['dev_metrics']['macro_average_precision'], dev_instrument_AP=candidate['dev_metrics']['per_label'][LABEL]['average_precision'],
            dev_instrument_BCE=losses.get('dev_instrument_BCE'), dev_balanced_BCE=losses.get('dev_balanced_BCE'),
            dev_positive_BCE=losses.get('dev_positive_BCE'), dev_negative_BCE=losses.get('dev_negative_BCE'),
            dev_positive_mean_score=losses.get('dev_positive_mean_score'), dev_negative_mean_score=losses.get('dev_negative_mean_score'),
            instrument_fp=counts['fp'], instrument_fn=counts['fn'], threshold=candidate['thresholds'][INSTRUMENT],
            eligible=not reasons, rejection_reason=';'.join(reasons), selected=name == selected_name))
        for j, label in enumerate(LABELS):
            c = candidate['dev_confusion'][label]
            original = reference['dev_confusion'][label]
            label_reports.append(dict(model=name, label=label, **c, threshold=candidate['thresholds'][j],
                AP=candidate['dev_metrics']['per_label'][label]['average_precision'], fp_delta=c['fp'] - original['fp'], fn_delta=c['fn'] - original['fn']))
    csv_rows(args.run_dir / 'dev_candidates.csv', reports)
    csv_rows(args.run_dir / 'dev_per_label.csv', label_reports)
    records = {str(Path(candidate['checkpoint']).parent / 'candidate_complete.json'):
               sha(args.run_dir / Path(candidate['checkpoint']).parent / 'candidate_complete.json') for candidate in models.values()}
    save(args.run_dir / 'selection_frozen.json', dict(protocol_signature=args.signature, models=models,
         selected_model=selected_name, selected_candidate=selected['id'], candidate_records=records,
         report_hashes={name: sha(args.run_dir / name) for name in ('dev_candidates.csv', 'dev_per_label.csv')},
         test_used_for_selection=False, previously_exposed_test=True))
    save(args.run_dir / 'selection_commit.json', dict(sha256=sha(args.run_dir / 'selection_frozen.json')))
    log(args.run_dir, 'instrument_balanced_selection_frozen_before_test', selected_model=selected_name)


def frozen(args):
    import finetune_siglip_focused as focused
    selected = read(args.run_dir / 'selection_frozen.json')
    require(read(args.run_dir / 'selection_commit.json')['sha256'] == sha(args.run_dir / 'selection_frozen.json') and
            selected['protocol_signature'] == args.signature and set(selected['models']) == set(MODELS) and
            selected['test_used_for_selection'] is False, 'Frozen balanced experiment selection changed')
    for candidate in selected['models'].values():
        require(candidate['protocol_signature'] == args.signature, 'Candidate signature differs')
        focused.verify_artifacts(args.run_dir, candidate)
    for name, checksum in {**selected['candidate_records'], **selected['report_hashes']}.items():
        require(sha(contained(args.run_dir, name)) == checksum, 'Frozen report changed: ' + name)
    return selected


def expected_predictions(args, name):
    import siglip_corrected_results as results
    if name == CONTROL:
        return results.reference_predictions(args, results.PREVIOUS)
    if name == PRIOR_ATTENTION:
        folder = args.source_run / SOURCE_MODEL
        complete = read(folder / 'complete.json')
        require(sha(folder / 'test_predictions.json') == complete['artifact_hashes']['test_predictions.json'], 'Source attention test file changed')
        return read(folder / 'test_predictions.json')
    return None


def evaluate(args, baseline_head):
    import numpy as np
    import finetune_siglip as ft
    import siglip_corrected_results as results
    from siglip_targeted_models import load_candidate
    selection = frozen(args)
    selection_sha = sha(args.run_dir / 'selection_frozen.json')
    rows = load_test(args.bundle, args.run_dir / 'selection_frozen.json', selection_sha)
    evaluated, predictions, counts = {}, {}, {}
    for name in MODELS:
        candidate = selection['models'][name]
        folder = args.run_dir / name
        folder.mkdir(exist_ok=True)
        expected = expected_predictions(args, name)
        result = results.cached_evaluation(args, name, candidate, args.signature, selection_sha)
        if result is None:
            encoder = head = None
            try:
                encoder, head = load_candidate(args, candidate, baseline_head)
                result = ft.common.benchmark(encoder, head, args.bundle, rows, np.asarray(candidate['thresholds']), folder, args.repeats, args.warmups)
                result['goal'] = results.goal(result)
                current = read(folder / 'test_predictions.json')
                results.validate_predictions(current, rows, candidate['thresholds'], name)
                results.reproduce_control(name, current, expected)
                if name != CONTROL:
                    require(all(all(a['predictions'][j] == b['predictions'][j] for j in OTHERS)
                                for a, b in zip(predictions[CONTROL], current)), 'Another test label decision changed')
                    require(np.allclose(np.asarray([r['probabilities'] for r in predictions[CONTROL]])[:, OTHERS],
                                        np.asarray([r['probabilities'] for r in current])[:, OTHERS], rtol=1e-5, atol=1e-6), 'Another test label score changed')
                save(folder / 'complete.json', dict(protocol_signature=args.signature, selection_sha256=selection_sha,
                     checkpoint_sha256=sha(contained(args.run_dir, candidate['checkpoint'])), result=result,
                     artifact_hashes={artifact: sha(folder / artifact) for artifact in results.ARTIFACTS}))
            finally:
                encoder = head = None
                ft.common.release()
        current = read(folder / 'test_predictions.json')
        results.validate_predictions(current, rows, candidate['thresholds'], name)
        results.reproduce_control(name, current, expected)
        if name != CONTROL:
            require(all(all(a['predictions'][j] == b['predictions'][j] for j in OTHERS)
                        for a, b in zip(predictions[CONTROL], current)), 'Cached candidate changed another test label')
        evaluated[name], predictions[name], counts[name] = result, current, results.label_counts(current)
        log(args.run_dir, 'instrument_model_evaluated', model=name, accuracy=result['metrics']['label_wise_accuracy'],
            instrument_fn=counts[name][LABEL]['fn'], p95_ms=result['timing']['total_ms']['p95_ms'])
    csv_rows(args.run_dir / 'comparison.csv', [dict(model=name, accuracy=r['metrics']['label_wise_accuracy'],
        macro_f1=r['metrics']['macro_f1'], macro_AP=r['metrics']['macro_average_precision'],
        mean_ms=r['timing']['total_ms']['mean_ms'], median_ms=r['timing']['total_ms']['median_ms'], p95_ms=r['timing']['total_ms']['p95_ms'],
        fp=sum(c['fp'] for c in counts[name].values()), fn=sum(c['fn'] for c in counts[name].values()),
        instrument_fp=counts[name][LABEL]['fp'], instrument_fn=counts[name][LABEL]['fn'],
        accuracy_90=r['goal']['accuracy_90'], p95_50=r['goal']['p95_50']) for name, r in evaluated.items()])
    label_rows, changes = [], []
    pairs = [(CONTROL, PRIOR_ATTENTION)] + [(reference, name) for reference in (CONTROL, PRIOR_ATTENTION) for name in NEW_MODELS]
    for reference, name in pairs:
        for j, label in enumerate(LABELS):
            old, new = counts[reference][label], counts[name][label]
            label_rows.append(dict(reference_model=reference, candidate_model=name, label=label,
                **{'prior_' + k: value for k, value in old.items()}, **{'candidate_' + k: value for k, value in new.items()},
                fp_delta=new['fp'] - old['fp'], fn_delta=new['fn'] - old['fn']))
            for a, b in zip(predictions[reference], predictions[name]):
                truth = a['labels'][label]
                if truth is not None and a['predictions'][j] != b['predictions'][j]:
                    changes.append(dict(reference_model=reference, candidate_model=name, image_id=a['image_id'], label=label, truth=truth,
                        prior_prediction=a['predictions'][j], candidate_prediction=b['predictions'][j],
                        prior_probability=a['probabilities'][j], candidate_probability=b['probabilities'][j],
                        prior_threshold=selection['models'][reference]['thresholds'][j], candidate_threshold=selection['models'][name]['thresholds'][j],
                        change='corrected' if b['predictions'][j] == truth else 'regressed'))
    csv_rows(args.run_dir / 'per_label_comparison.csv', label_rows)
    save(args.run_dir / 'error_changes.json', changes)
    positive_rows = []
    for name in MODELS:
        threshold = selection['models'][name]['thresholds'][INSTRUMENT]
        for row in predictions[name]:
            if row['labels'][LABEL] == 1:
                probability = row['probabilities'][INSTRUMENT]
                positive_rows.append(dict(model=name, image_id=row['image_id'], probability=probability, threshold=threshold,
                                          margin=probability - threshold, result='TP' if row['predictions'][INSTRUMENT] else 'FN'))
    csv_rows(args.run_dir / 'instrument_positive_scores.csv', positive_rows)
    chosen = selection['selected_model']
    summary = dict(results=evaluated, selected_model=chosen, selected_candidate=selection['selected_candidate'],
        goal_met=evaluated[chosen]['goal']['all_met'], protocol_signature=args.signature,
        selection_sha256=selection_sha, test_used_for_selection=False, previously_exposed_test=True,
        model_deployed=False, test_images=len(rows), latency_remeasured=True,
        note='Instrument-only probe/classifier updates; same one-pass vision tower and exact other-label outputs. '
             'Same exposed37 test; not an independent generalization estimate. No OCR/network/DB/loading time.')
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
    for root in (args.bundle, args.baseline, args.prior_run, args.previous_run, args.source_run, args.packet, args.model_dir):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir), 'Use a separate new run directory')
    args.device = 'cuda:0'
    manifest, rows = validate_packet(args.packet, args.bundle)
    require(manifest['added_images'] + manifest['changed_train_cells'] > 0, 'Approved corrected/augmented packet required')
    environment = ft.common.cuda_environment('fp32')
    baseline, baseline_head, _ = focused.input_protocol(args)
    require(environment == read(args.baseline / 'environment.json') == read(args.prior_run / 'environment.json'), 'Keep the existing GPU/runtime')
    previous = corrected.previous_inputs(args, baseline, environment)
    model_packet = ft.common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == baseline['config']['models']['siglip2'], 'Downloaded source model bytes changed')
    imported = source_selection(args, environment, model_packet)
    fingerprints = input_hashes(args)
    weights = support([row for row in rows if row['split'] == 'train'])
    specs = [dict(id='instrument-ap-control', family='instrument_attention', balanced=False),
             dict(id='instrument-balanced', family='instrument_attention', balanced=True)]
    config = dict(schema='siglip-instrument-balanced-v1', environment=environment, model_packet=model_packet,
        script_hashes=source_hashes, input_hashes=fingerprints, candidates=specs, train_instrument_support=weights,
        seed=SEED, precision='fp32', image_size=384, epochs=args.epochs, microbatch=args.microbatch,
        effective_batch=8, probe_lr=args.probe_lr, weight_decay=1e-4, gradient_clip=1.,
        training='Frozen original tower/head; train existing extra MAP probe and instrument classifier only. Same initialization and all known train rows once per epoch.',
        balanced_loss='Train-only w_pos=N/(2*Npos), w_neg=N/(2*Nneg); sum per-example weighted BCE divided by effective batch population.',
        checkpoint='Original dev guards first; instrument FN, FP, AP, balanced dev BCE; epoch0 fallback. Fixed epoch budget for both new loss variants.',
        calibration='Existing dev-only instrument FP budget, other seven thresholds copied from reference.',
        selection='Original dev eligibility guards; accuracy, instrument FN, macro F1, instrument AP; reference tie preference.',
        source_model=SOURCE_MODEL, warmups=args.warmups, repeats=args.repeats, target_accuracy=.9, target_p95_ms=50., test_used_for_selection=False)
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            old = read(protocol_path)
            require(args.resume and old['signature'] == args.signature == digest(old['config']), 'Resume requires identical code/data/settings')
        else:
            require(all(path.name == '.lock' for path in args.run_dir.iterdir()), 'Choose an empty new run directory')
            save(protocol_path, dict(signature=args.signature, config=config))
            save(args.run_dir / 'environment.json', environment)
            save(args.run_dir / 'training_packet_manifest.json', manifest)
            save(args.run_dir / 'training_packet_approvals.json', read(args.packet / 'approvals.json'))
            save(args.run_dir / 'training_data_summary.json', dict(added_images=manifest['added_images'],
                 changed_train_cells=manifest['changed_train_cells'], instrument_support=weights,
                 train_images=sum(row['split'] == 'train' for row in rows), dev_images=sum(row['split'] == 'dev' for row in rows),
                 test_images_used_in_training=0, approval_note='Existing AI-assisted approval provenance retained.'))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            frozen(args)
            require(status['protocol_signature'] == args.signature and all(sha(contained(args.run_dir, name)) == value
                    for name, value in status['artifact_hashes'].items()), 'Completed balanced experiment changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature))
        try:
            if not (args.run_dir / 'selection_frozen.json').exists():
                reference = corrected.reference(args, 'reference-partial', args.previous_run, previous, baseline_head, rows)
                prior_attention = import_attention(args, imported, reference, rows, baseline_head)
                candidates = [train_candidate(args, spec, rows, reference, baseline_head, weights) for spec in specs]
                freeze(args, dict(zip(MODELS, [reference, prior_attention, *candidates])))
            summary = evaluate(args, baseline_head)
            validate_packet(args.packet, args.bundle)
            require(input_hashes(args) == fingerprints and sources() == source_hashes and
                    ft.common.model_packet(args.model_dir, 'siglip2') == model_packet, 'Inputs or source changed during execution')
            excluded = {'.lock', 'status.json', 'failure.json', 'events.jsonl'}
            hashes = {path.relative_to(args.run_dir).as_posix(): sha(path) for path in args.run_dir.rglob('*')
                      if path.is_file() and path.name not in excluded and not path.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature, CUDA_executed=True,
                 backbone_training_executed=False, head_training_executed=True, instrument_probe_training_executed=True,
                 OCR_executed=False, model_deployed=False, artifact_hashes=hashes))
            log(args.run_dir, 'instrument_balanced_complete', selected_model=summary['selected_model'], goal_met=summary['goal_met'], run=str(args.run_dir))
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
    parser.add_argument('--source-run', type=Path, default=HERE / 'runs/siglip-augment-v1-fp32')
    parser.add_argument('--packet', type=Path, default=HERE / 'training_packets/siglip-augment-v1')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-instrument-balanced-v1-fp32')
    parser.add_argument('--epochs', type=int, default=16)
    parser.add_argument('--microbatch', type=int, choices=(1, 2, 4, 8), default=2)
    parser.add_argument('--probe-lr', type=float, default=1e-3)
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('bundle', 'baseline', 'prior_run', 'previous_run', 'source_run', 'packet', 'model_dir', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.command == 'show-results':
        import finetune_siglip_corrected as corrected
        corrected.show_results(args)
        for name in ('dev_per_label.csv', 'instrument_positive_scores.csv'):
            path = args.run_dir / name
            if path.is_file():
                print('\n' + name)
                print(path.read_text(), end='')
        return
    require(min(args.epochs, args.warmups, args.repeats) > 0 and math.isfinite(args.probe_lr) and args.probe_lr > 0,
            'Use positive finite counts and learning rate')
    run(args)


if __name__ == '__main__':
    main()
