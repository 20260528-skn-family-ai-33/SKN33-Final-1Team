"""Target instrument misses and food/animal/plant false positives, offline FP32."""
from pathlib import Path
import argparse
import fcntl
import math
import os
import random
import shutil
import sys
import time
import traceback

import numpy as np
import torch

import finetune_siglip as ft
from finetune_siglip import common, HERE
from benchlib import (LABELS, SEED, contained, csv_rows, digest, load_test, log,
                      metrics, read, save, sha, targets, thresholds)

INSTRUMENT = LABELS.index('musical_instrument')
FP_LABELS = ('food_drink', 'animal', 'plant_flower')
FP_INDICES = tuple(LABELS.index(k) for k in FP_LABELS)
ARTIFACTS = ('test_predictions.json', 'test_metrics.json', 'timing_raw.csv', 'timing_summary.json')


def focused_loss(logits, labels, positive_boost, negative_boost):
    """BCE plus bounded hard-example weights; unknown cells have zero gradient."""
    from torch.nn import functional as F
    if not all(math.isfinite(v) and v >= 0 for v in (positive_boost, negative_boost)):
        raise ValueError('Boosts must be finite and nonnegative')
    mask = torch.isfinite(labels)
    if not mask.any():
        raise ValueError('Batch has no known labels')
    safe = torch.nan_to_num(labels, nan=0.).float()
    # Detach difficulty: weighting must not introduce a gradient through p itself.
    p = torch.sigmoid(logits.detach().float())
    weight = torch.ones_like(p)
    weight[:, INSTRUMENT] += positive_boost * safe[:, INSTRUMENT] * (1 - p[:, INSTRUMENT]).square()
    for j in FP_INDICES:
        weight[:, j] += negative_boost * (1 - safe[:, j]) * p[:, j].square()
    loss = F.binary_cross_entropy_with_logits(logits.float(), safe, reduction='none')
    return (loss * weight * mask).sum(), int(mask.sum().item())


def confusion(y, p, cutoffs):
    y, p = np.asarray(y), np.asarray(p)
    pred = p >= np.asarray(cutoffs)
    return {label: dict(
        tp=int(((y[:, j] == 1) & pred[:, j]).sum()),
        fp=int(((y[:, j] == 0) & pred[:, j]).sum()),
        fn=int(((y[:, j] == 1) & ~pred[:, j]).sum()),
        tn=int(((y[:, j] == 0) & ~pred[:, j]).sum()))
        for j, label in enumerate(LABELS)}


def focused_errors(counts):
    return counts['musical_instrument']['fn'] + sum(counts[k]['fp'] for k in FP_LABELS)


def focused_thresholds(y, p):
    """Dev only: F2 for instruments, F0.5 for FP targets, F1 for others."""
    y, p = np.asarray(y), np.asarray(p)
    if y.shape != p.shape or y.ndim != 2 or y.shape[1] != len(LABELS) or not np.isfinite(p).all():
        raise ValueError('Invalid dev probabilities/labels')
    selected = []
    for j in range(len(LABELS)):
        mask = np.isfinite(y[:, j])
        yy, pp = y[mask, j], p[mask, j]
        if set(np.unique(yy)) != {0, 1}:
            raise ValueError('Both known dev classes required: ' + LABELS[j])
        beta = 2. if j == INSTRUMENT else (.5 if j in FP_INDICES else 1.)
        candidates = []
        for index in range(1, 20):
            cutoff = index / 20
            pred = pp >= cutoff
            tp = int(((yy == 1) & pred).sum())
            fp = int(((yy == 0) & pred).sum())
            fn = int(((yy == 1) & ~pred).sum())
            denominator = (1 + beta * beta) * tp + beta * beta * fn + fp
            score = (1 + beta * beta) * tp / denominator if denominator else 0.
            candidates.append((score, -abs(cutoff - .5), cutoff))
        selected.append(max(candidates)[-1])
    return selected


def variants(result, y, p):
    options = []
    for policy, cutoffs in (('dev_F1', thresholds(y, p)), ('dev_target_Fbeta', focused_thresholds(y, p))):
        options.append(dict(**result, threshold_policy=policy, thresholds=cutoffs,
                            dev_metrics=metrics(y, p, cutoffs), dev_confusion=confusion(y, p, cutoffs)))
    return options


def choose(candidates, reference):
    """A dev-only fallback prevents winning by sacrificing overall F1/accuracy."""
    ref = reference['dev_metrics']
    eligible = [c for c in [reference] + candidates
                if c['dev_metrics']['label_wise_accuracy'] + 1e-12 >= ref['label_wise_accuracy']
                and c['dev_metrics']['macro_f1'] + 1e-12 >= ref['macro_f1']
                and focused_errors(c['dev_confusion']) <= focused_errors(reference['dev_confusion'])]
    return max(eligible, key=lambda c: (
        c['dev_metrics']['label_wise_accuracy'], -focused_errors(c['dev_confusion']),
        c['dev_metrics']['macro_f1'], c['dev_metrics']['macro_average_precision'],
        c['source'] == 'prior', c['threshold_policy'] == 'original', -c['backbone_lr']))


def training_epoch(encoder, head, optimizer, bundle, rows, candidate, microbatch, epoch):
    if not rows or any(r['split'] != 'train' for r in rows):
        raise ValueError('Only training rows may enter focused loss')
    encoder.train(); head.train()
    order = list(range(len(rows)))
    random.Random(SEED + epoch).shuffle(order)
    total, count, steps, audit = 0., 0, 0, None
    for offset in range(0, len(order), 8):
        group = [rows[i] for i in order[offset:offset + 8]]
        known = int(np.isfinite(targets(group)).sum())
        if not known:
            raise ValueError('Accumulation group has no known labels')
        optimizer.zero_grad(set_to_none=True)
        for start in range(0, len(group), microbatch):
            batch = group[start:start + microbatch]
            x = torch.cat([ft.prepare_train(encoder, bundle, row, True) for row in batch]).to(encoder.device)
            y = torch.tensor(targets(batch), device=encoder.device)
            numerator, n = focused_loss(head(encoder(x)), y, candidate['positive_boost'], candidate['negative_boost'])
            loss = numerator / known
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite training loss')
            loss.backward()
            total += float(numerator.detach().cpu()); count += n
        if audit is None:
            audit = {name: None if p.grad is None else float(p.grad.norm().cpu())
                     for name, p in encoder.backbone.named_parameters()
                     if name.endswith(('patch_embedding.weight', 'self_attn.q_proj.weight', 'post_layernorm.weight'))}
            audit['classifier.weight'] = None if head.weight.grad is None else float(head.weight.grad.norm().cpu())
            if any(v is None or not math.isfinite(v) or v <= 0 for v in audit.values()):
                raise ValueError('Backbone/classifier gradient audit failed')
        torch.nn.utils.clip_grad_norm_(list(encoder.parameters()) + list(head.parameters()), 1., error_if_nonfinite=True)
        optimizer.step(); steps += 1
    return dict(loss=total / count, known_cells=count, optimizer_steps=steps, gradient_audit=audit)


def validate_rows(td):
    if any(r['split'] not in ('train', 'dev') for r in td):
        raise ValueError('Train/dev only; test must remain unopened until selection')
    train, dev = ([r for r in td if r['split'] == split] for split in ('train', 'dev'))
    if not train or not dev or {r['image_id'] for r in train} & {r['image_id'] for r in dev}:
        raise ValueError('Nonempty disjoint train/dev rows required')
    return train, dev


def train_review(rows, prob, cutoffs, path):
    """Training diagnostics only. This never changes labels or sampling."""
    if any(r['split'] != 'train' for r in rows):
        raise ValueError('Hard examples may only be listed from train')
    records = []
    for row, pp in zip(rows, prob):
        for j in (INSTRUMENT,) + FP_INDICES:
            truth = row['labels'][LABELS[j]]
            if truth is None:
                continue
            miss = j == INSTRUMENT and truth == 1 and pp[j] < cutoffs[j]
            false_positive = j in FP_INDICES and truth == 0 and pp[j] >= cutoffs[j]
            if miss or false_positive:
                records.append(dict(image_id=row['image_id'], image=row['image'], split='train',
                                    label=LABELS[j], truth=truth, probability=float(pp[j]),
                                    threshold=cutoffs[j], error='FN' if miss else 'FP'))
    # Always write a usable header even when there are no errors.
    import csv
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('image_id', 'image', 'split', 'label', 'truth', 'probability', 'threshold', 'error'))
        writer.writeheader(); writer.writerows(records)


def candidate_train(args, candidate, baseline_head, prior, td, loader=ft.build_models):
    from safetensors.torch import load_file
    train, dev = validate_rows(td)
    folder = args.run_dir / 'candidates' / candidate['id']
    folder.mkdir(parents=True, exist_ok=True)
    random.seed(SEED); torch.manual_seed(SEED)
    if str(args.device).startswith('cuda'):
        torch.cuda.manual_seed_all(SEED); torch.cuda.reset_peak_memory_stats()
    encoder, head = loader(args.model_dir, args.device, baseline_head)
    ft.load_state(encoder, head, load_file(str(args.run_dir / prior['checkpoint'])))
    encoder.requires_grad_(True); head.requires_grad_(True)
    initial = ft.group_hashes(encoder, head)
    optimizer = torch.optim.AdamW([
        dict(params=encoder.parameters(), lr=candidate['backbone_lr']),
        dict(params=head.parameters(), lr=1e-4)], weight_decay=1e-4, foreach=False)
    best_ap, best_epoch, stale, history, audit = -1., None, 0, [], None
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = training_epoch(encoder, head, optimizer, args.bundle, train, candidate, args.microbatch, epoch)
        audit = audit or trained['gradient_audit']
        prob = ft.predict_dev(encoder, head, args.bundle, dev)
        scored = metrics(targets(dev), prob, [.5] * len(LABELS))
        ap = scored['macro_average_precision']
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            ft.save_weights(folder / 'best.safetensors', ft.state(encoder, head))
        else:
            stale += 1
        history.append(dict(epoch=epoch, train_loss=trained['loss'], known_cells=trained['known_cells'],
                            optimizer_steps=trained['optimizer_steps'], dev_macro_AP=ap,
                            elapsed_seconds=time.perf_counter() - started))
        save(folder / 'history.json', history)
        log(args.run_dir, 'focused_epoch', candidate=candidate['id'], **history[-1], best_epoch=best_epoch)
        if stale >= args.patience:
            break
    ft.load_state(encoder, head, load_file(str(folder / 'best.safetensors')))
    prob = ft.predict_dev(encoder, head, args.bundle, dev)
    common.save_array(folder / 'dev_probabilities.npy', prob)
    changed = {k: initial[k] != v for k, v in ft.group_hashes(encoder, head).items()}
    if not changed or not all(changed.values()):
        raise ValueError('Every vision group/classifier must change: ' + str(changed))
    result = dict(**candidate, source='trained', best_epoch=best_epoch, trained_epochs=len(history),
                  gradient_audit=audit, parameter_groups_changed=changed,
                  checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
                  artifact_hashes={n: sha(folder / n) for n in ('best.safetensors', 'history.json', 'dev_probabilities.npy')})
    result['variants'] = variants(result, targets(dev), prob)
    save(folder / 'candidate_complete.json', result)
    del encoder, head, optimizer
    common.release()
    return result


def verify_artifacts(out, candidate):
    folder = contained(out, candidate['checkpoint']).parent
    for name, checksum in candidate['artifact_hashes'].items():
        if sha(contained(folder, name)) != checksum:
            raise ValueError('Candidate artifact changed: ' + name)


def prepare_reference(args, baseline_head, selected, td):
    from safetensors.torch import load_file
    train, dev = validate_rows(td)
    folder = args.run_dir / 'candidates/prior'
    done = folder / 'candidate_complete.json'
    if done.exists():
        result = read(done); verify_artifacts(args.run_dir, result)
        return result
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / 'best.safetensors.tmp'
    shutil.copyfile(contained(args.prior_run, selected['checkpoint']), temporary)
    temporary.replace(folder / 'best.safetensors')
    encoder, head = ft.build_models(args.model_dir, args.device, baseline_head)
    ft.load_state(encoder, head, load_file(str(folder / 'best.safetensors')))
    prob = ft.predict_dev(encoder, head, args.bundle, dev)
    # Catch model/runtime/input drift without inspecting any previous test metrics.
    np.testing.assert_allclose(prob, np.load(contained(args.prior_run, selected['checkpoint']).parent / 'dev_probabilities.npy'), rtol=1e-5, atol=1e-6)
    common.save_array(folder / 'dev_probabilities.npy', prob)
    train_review(train, ft.predict_dev(encoder, head, args.bundle, train), selected['thresholds'], folder / 'train_hard_cases.csv')
    result = dict(id='prior', source='prior', backbone_lr=0., best_epoch=selected['best_epoch'],
                  threshold_policy='original', thresholds=selected['thresholds'],
                  dev_metrics=metrics(targets(dev), prob, selected['thresholds']),
                  dev_confusion=confusion(targets(dev), prob, selected['thresholds']),
                  checkpoint='candidates/prior/best.safetensors',
                  artifact_hashes={n: sha(folder / n) for n in ('best.safetensors', 'dev_probabilities.npy', 'train_hard_cases.csv')})
    save(done, result)
    del encoder, head
    common.release()
    return result


def input_protocol(args):
    baseline_protocol, head = ft.assert_baseline(args.baseline)
    protocol = read(args.prior_run / 'protocol.json')
    status = read(args.prior_run / 'status.json')
    if status['status'] != 'complete' or protocol['signature'] != digest(protocol['config']) or status['protocol_signature'] != protocol['signature']:
        raise ValueError('A completed, unchanged previous fine-tuning run is required')
    selected = read(args.prior_run / 'selection_frozen.json')
    ft.validate_checkpoint(args.prior_run, selected)
    if selected['protocol_signature'] != protocol['signature']:
        raise ValueError('Previous selection protocol mismatch')
    for name, checksum in protocol['config']['script_hashes'].items():
        if sha(HERE / name) != checksum:
            raise ValueError('Previous fine-tuning source changed: ' + name)
    if (protocol['config']['baseline_protocol_sha256'] != sha(args.baseline / 'protocol.json')
            or protocol['config']['baseline_selection_sha256'] != sha(args.baseline / 'selection_frozen.json')
            or protocol['config']['model_packet'] != baseline_protocol['config']['models']['siglip2']):
        raise ValueError('Previous run used a different baseline')
    return baseline_protocol, head, selected


def comparison_details(out):
    old = read(out / 'siglip2_full_finetune/test_predictions.json')
    new = read(out / 'siglip2_focused/test_predictions.json')
    if len(old) != len(new) or any(a['image_id'] != b['image_id'] or a['labels'] != b['labels'] for a, b in zip(old, new)):
        raise ValueError('Comparison rows/labels differ')
    records, changes = [], []
    for j, label in enumerate(LABELS):
        row = dict(label=label)
        for name, predictions in (('prior', old), ('focused', new)):
            counts = dict(tp=0, fp=0, fn=0, tn=0)
            for r in predictions:
                y, pred = r['labels'][label], r['predictions'][j]
                if y is not None:
                    counts[('tp' if pred else 'fn') if y else ('fp' if pred else 'tn')] += 1
            row.update({name + '_' + k: v for k, v in counts.items()})
        row['fp_delta'] = row['focused_fp'] - row['prior_fp']
        row['fn_delta'] = row['focused_fn'] - row['prior_fn']
        records.append(row)
        for a, b in zip(old, new):
            y = a['labels'][label]
            if y is not None and a['predictions'][j] != b['predictions'][j]:
                changes.append(dict(image_id=a['image_id'], label=label, truth=y,
                                    prior_prediction=a['predictions'][j], focused_prediction=b['predictions'][j],
                                    change='recovered' if b['predictions'][j] == y else 'regressed'))
    csv_rows(out / 'per_label_comparison.csv', records)
    save(out / 'error_changes.json', changes)


def evaluate(args, selected, reference, head):
    from encoders import load_encoder, make_head
    from safetensors.torch import load_file
    # The test is opened only after all model/threshold choices and hashes freeze.
    ft.validate_checkpoint(args.run_dir, selected)
    selection_hash = sha(args.run_dir / 'selection_frozen.json')
    rows = load_test(args.bundle, args.run_dir / 'selection_frozen.json', selection_hash)
    prior_test = read(args.prior_run / 'siglip2_full_finetune/test_predictions.json')
    results = {}
    for name, candidate in (('siglip2_full_finetune', reference), ('siglip2_focused', selected)):
        verify_artifacts(args.run_dir, candidate)
        folder = args.run_dir / name
        folder.mkdir(exist_ok=True)
        done = folder / 'complete.json'
        if done.exists():
            cached = read(done)
            if cached['selection_sha256'] != selection_hash:
                raise ValueError('Cached evaluation selection changed')
            for artifact, checksum in cached['artifact_hashes'].items():
                if sha(folder / artifact) != checksum:
                    raise ValueError('Cached evaluation changed')
            results[name] = cached['result']
            continue
        encoder = load_encoder('siglip2', args.model_dir, args.device, 'fp32', 384)
        classifier = make_head(head, args.device)
        ft.load_state(encoder, classifier, load_file(str(contained(args.run_dir, candidate['checkpoint']))))
        encoder.eval(); classifier.eval()
        result = common.benchmark(encoder, classifier, args.bundle, rows,
                                  np.asarray(candidate['thresholds']), folder, args.repeats, args.warmups)
        if name == 'siglip2_full_finetune':
            # Reproduce decisions; float serialization need not be byte-identical.
            current = read(folder / 'test_predictions.json')
            if len(current) != len(prior_test) or any(a['image_id'] != b['image_id'] or a['labels'] != b['labels'] or a['predictions'] != b['predictions'] for a, b in zip(current, prior_test)):
                raise ValueError('Previous fine-tuned baseline decisions did not reproduce')
        save(done, dict(selection_sha256=selection_hash, result=result,
                        artifact_hashes={n: sha(folder / n) for n in ARTIFACTS}))
        results[name] = result
        del encoder, classifier
        common.release()
    csv_rows(args.run_dir / 'comparison.csv', [dict(model=k,
        accuracy=v['metrics']['label_wise_accuracy'], macro_f1=v['metrics']['macro_f1'],
        mean_ms=v['timing']['total_ms']['mean_ms'], median_ms=v['timing']['total_ms']['median_ms'],
        p95_ms=v['timing']['total_ms']['p95_ms'], accuracy_90=v['metrics']['label_wise_accuracy'] >= .9,
        p95_50=v['timing']['total_ms']['p95_ms'] <= 50) for k, v in results.items()])
    comparison_details(args.run_dir)
    new = results['siglip2_focused']
    summary = dict(results=results, selected_candidate=selected['id'], threshold_policy=selected['threshold_policy'],
                   selected_source=selected['source'], selected_backbone_trained_in_this_run=selected['source'] == 'trained',
                   goal_met=new['metrics']['label_wise_accuracy'] >= .9 and new['timing']['total_ms']['p95_ms'] <= 50,
                   test_used_for_selection=False, previously_exposed_test=True,
                   note='Same exposed37 test; no new independent generalization estimate. Decode/preprocess/transfer/vision/head only; no OCR/network/DB/loading/TensorRT.')
    save(args.run_dir / 'comparison_summary.json', summary)
    return summary


def execute(args, td, head, prior, signature, trainer=candidate_train, evaluator=evaluate):
    reference = prepare_reference(args, head, prior, td)
    path = args.run_dir / 'selection_frozen.json'
    if not path.exists():
        _, dev = validate_rows(td)
        prob = np.load(args.run_dir / 'candidates/prior/dev_probabilities.npy')
        base = {k: v for k, v in reference.items() if k not in ('threshold_policy', 'thresholds', 'dev_metrics', 'dev_confusion')}
        choices = variants(base, targets(dev), prob)
        for candidate in args.candidates:
            done = args.run_dir / 'candidates' / candidate['id'] / 'candidate_complete.json'
            if done.exists():
                result = read(done); verify_artifacts(args.run_dir, result)
            else:
                result = trainer(args, candidate, head, reference, td)
            choices.extend(result['variants'])
        best = choose(choices, reference)
        save(path, dict(**best, protocol_signature=signature, test_used_for_selection=False))
        save(args.run_dir / 'selection_commit.json', dict(sha256=sha(path)))
        log(args.run_dir, 'focused_selection_frozen_before_test', candidate=best['id'],
            threshold_policy=best['threshold_policy'], dev_accuracy=best['dev_metrics']['label_wise_accuracy'],
            dev_focused_errors=focused_errors(best['dev_confusion']))
    selected = read(path)
    if selected['protocol_signature'] != signature:
        raise ValueError('Selection protocol mismatch')
    ft.validate_checkpoint(args.run_dir, selected)
    return evaluator(args, selected, reference, head)


def run(args):
    validate_output(args)
    args.device = 'cuda:0'
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    environment = common.cuda_environment('fp32')
    _, _, td = common.bundle_data(args.bundle)
    baseline_protocol, head, prior = input_protocol(args)
    if environment != read(args.baseline / 'environment.json') or environment != read(args.prior_run / 'environment.json'):
        raise ValueError('Reuse exactly the previous GPU/runtime; no automatic installation')
    packet = common.model_packet(args.model_dir, 'siglip2')
    if packet != baseline_protocol['config']['models']['siglip2']:
        raise ValueError('Downloaded model bytes changed')
    args.candidates = [dict(id=f'lr{lr:.0e}-{level}', backbone_lr=lr, positive_boost=boost, negative_boost=boost)
                       for lr in args.lrs for level, boost in (('mild', 2.), ('strong', 4.))]
    config = dict(environment=environment, model_packet=packet,
        prior_protocol_sha256=sha(args.prior_run / 'protocol.json'), prior_selection_sha256=sha(args.prior_run / 'selection_frozen.json'),
        baseline_protocol_sha256=sha(args.baseline / 'protocol.json'), bundle_sha256=sha(args.bundle / 'bundle_manifest.json'),
        candidates=args.candidates, max_epochs=args.epochs, patience=args.patience, microbatch=args.microbatch,
        effective_batch=8, head_lr=1e-4, weight_decay=1e-4, gradient_clip=1., precision='fp32', seed=SEED,
        warm_start='previous dev-selected full vision tower + classifier',
        loss='known-cell BCE; instrument positives: 1+boost*(1-stopgrad(p))^2; food/animal/plant negatives: 1+boost*stopgrad(p)^2; others: 1',
        sampling='all train images once per epoch; no oversampling/cropping/pseudo-labels',
        augmentation=dict(brightness=.08, contrast=.08, saturation=.05, hue=0, geometry=False),
        checkpoint_selection='dev_macro_AP; epoch>=1; earliest tie',
        threshold_selection='original prior; dev F1; dev F2 instrument/F0.5 FP targets/F1 others; grid .05..95 step .05',
        selection_guard='dev accuracy and macro_F1 >= prior; instrument_FN + target_FP <= prior; otherwise prior',
        candidate_selection='dev accuracy, fewer focused errors, macro_F1, macro_AP, prior, original_thresholds, smaller_LR',
        warmups=args.warmups, repeats=args.repeats,
        script_hashes={n: sha(HERE / n) for n in ('finetune_siglip_focused.py', 'finetune_siglip.py', 'encoders.py', 'run_experiment.py')})
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol = args.run_dir / 'protocol.json'
        if protocol.exists():
            saved = read(protocol)
            if not args.resume or saved['signature'] != signature or digest(saved['config']) != signature:
                raise ValueError('Existing run requires --resume with identical code/data/GPU/settings')
        else:
            if any(p.name != '.lock' for p in args.run_dir.iterdir()):
                raise ValueError('New run directory must be empty')
            save(protocol, dict(signature=signature, config=config))
            save(args.run_dir / 'environment.json', environment)
        status = args.run_dir / 'status.json'
        if status.exists() and read(status)['status'] == 'complete':
            selected = read(args.run_dir / 'selection_frozen.json')
            if read(status)['protocol_signature'] != signature or selected['protocol_signature'] != signature:
                raise ValueError('Completed selection/status protocol mismatch')
            ft.validate_checkpoint(args.run_dir, selected)
            for name, checksum in read(status)['artifact_hashes'].items():
                if sha(args.run_dir / name) != checksum:
                    raise ValueError('Completed output changed: ' + name)
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status, dict(status='running', protocol_signature=signature))
        try:
            summary = execute(args, td, head, prior, signature)
            common.bundle_data(args.bundle); input_protocol(args)
            for name, filename in (('prior_protocol_sha256', args.prior_run / 'protocol.json'),
                                   ('prior_selection_sha256', args.prior_run / 'selection_frozen.json'),
                                   ('baseline_protocol_sha256', args.baseline / 'protocol.json'),
                                   ('bundle_sha256', args.bundle / 'bundle_manifest.json')):
                if sha(filename) != config[name]:
                    raise ValueError('Input changed during run: ' + str(filename))
            if common.model_packet(args.model_dir, 'siglip2') != packet:
                raise ValueError('Source model changed during run')
            outputs = ['comparison.csv', 'comparison_summary.json', 'per_label_comparison.csv', 'error_changes.json']
            outputs += [f'{model}/{name}' for model in ('siglip2_full_finetune', 'siglip2_focused') for name in (*ARTIFACTS, 'complete.json')]
            save(status, dict(status='complete', protocol_signature=signature, CUDA_executed=True,
                              backbone_training_executed=True, OCR_executed=False,
                              artifact_hashes={n: sha(args.run_dir / n) for n in outputs}))
            log(args.run_dir, 'focused_complete', run=str(args.run_dir), selected=summary['selected_candidate'], goal_met=summary['goal_met'])
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status, dict(status='failed', protocol_signature=signature))
            raise


def validate_output(args):
    # A new run must never overwrite a baseline, its parents or its descendants.
    for original in (args.baseline, args.prior_run, args.bundle, args.model_dir):
        if args.run_dir.is_relative_to(original) or original.is_relative_to(args.run_dir):
            raise ValueError('Use a separate new run directory, outside inputs/baselines')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-focused-fp32')
    parser.add_argument('--lrs', nargs='+', type=float, default=[1e-6, 3e-6])
    parser.add_argument('--epochs', type=int, default=6)
    parser.add_argument('--patience', type=int, default=2)
    parser.add_argument('--microbatch', type=int, choices=(1, 2, 4, 8), default=2)
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if min(args.epochs, args.patience, args.warmups, args.repeats) < 1 or any(not math.isfinite(x) or x <= 0 for x in args.lrs):
        parser.error('Positive finite settings required')
    if len({f'{lr:.0e}' for lr in args.lrs}) != len(args.lrs):
        parser.error('Learning rate identifiers must be unique')
    for name in ('bundle', 'baseline', 'prior_run', 'model_dir', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    run(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
