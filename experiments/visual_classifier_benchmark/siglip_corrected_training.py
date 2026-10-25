"""Corrected-label head refits and clean-start SigLIP2 full tuning.

Only the caller's approved train/dev packet enters fitting. Evaluation and
selection across candidates belong to the separate experiment runner.
"""
from pathlib import Path
import math
import random
import time

import finetune_siglip as ft
import finetune_siglip_focused as focused
from finetune_siglip import common
from benchlib import LABELS, SEED, contained, digest, log, metrics, read, save, sha, targets, thresholds


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _seed(args):
    import torch
    require(args.seed == SEED, 'Use the fixed benchmark seed')
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if str(args.device).startswith('cuda'):
        torch.cuda.manual_seed_all(args.seed)


def _splits(rows):
    require(rows and all(row['split'] in ('train', 'dev') for row in rows), 'Train/dev-only corrected packet required')
    train = [row for row in rows if row['split'] == 'train']
    dev = [row for row in rows if row['split'] == 'dev']
    require(train and dev, 'Both training and development rows required')
    require(len({row['image_id'] for row in rows}) == len(rows), 'Duplicate packet image IDs')
    require(not ({row['group_id'] for row in train} & {row['group_id'] for row in dev}), 'Train/dev group overlap')
    return train, dev


def _folder(args, spec):
    name = spec['id']
    require(isinstance(name, str) and name and name not in ('.', '..') and Path(name).name == name,
            'Candidate ID must be a simple directory name')
    return contained(args.run_dir, 'candidates/' + name)


def _verify(args, candidate):
    require(candidate['protocol_signature'] == args.signature, 'Candidate protocol differs')
    folder = contained(args.run_dir, candidate['checkpoint']).parent
    for name, checksum in candidate['artifact_hashes'].items():
        require(sha(contained(folder, name)) == checksum, 'Candidate artifact changed: ' + name)
    return folder


def _cached(args, spec, rows):
    done = _folder(args, spec) / 'candidate_complete.json'
    if not done.exists():
        return None
    candidate = read(done)
    require(all(candidate.get(key) == value for key, value in spec.items()), 'Cached candidate specification differs')
    require(candidate['training_rows_sha256'] == digest(rows), 'Cached candidate training/development rows changed')
    _verify(args, candidate)
    log(args.run_dir, 'corrected_candidate_reused', candidate=spec['id'])
    return candidate


def _head_parameters(head):
    return dict(coef=head.weight.detach().cpu().float().tolist(), intercept=head.bias.detach().cpu().float().tolist())


def _body_hashes(encoder, head):
    return {name: value for name, value in ft.group_hashes(encoder, head).items() if name != 'classifier'}


def fit_refit(args, spec, rows, baseline_head, source_checkpoint=None):
    """Refit all eight linear classifiers on corrected labels, never old head targets."""
    import numpy as np
    from encoders import make_head
    from safetensors.torch import load_file

    train, dev = _splits(rows)
    source_checkpoint = Path(source_checkpoint).resolve() if source_checkpoint is not None else None
    source_sha = sha(source_checkpoint) if source_checkpoint is not None else None
    cached = _cached(args, spec, rows)
    if cached is not None:
        require(cached['source_checkpoint_sha256'] == source_sha, 'Refit source checkpoint changed')
        return cached
    require(all(math.isfinite(c) and c > 0 for c in args.cs), 'Positive finite refit C values required')
    folder = _folder(args, spec)
    folder.mkdir(parents=True, exist_ok=True)
    _seed(args)
    encoder, previous_head = ft.build_models(args.model_dir, args.device, baseline_head)
    if source_checkpoint is not None:
        ft.load_state(encoder, previous_head, load_file(str(source_checkpoint)))
    encoder.eval().requires_grad_(False)
    previous_head.eval().requires_grad_(False)
    initial_body = _body_hashes(encoder, previous_head)
    initial_head = ft.group_hashes(encoder, previous_head)['classifier']
    train_x = common.extract(encoder, args.packet, train, args.run_dir, spec['id'] + '_train')
    dev_x = common.extract(encoder, args.packet, dev, args.run_dir, spec['id'] + '_dev')
    fitted, tried = common.fit_heads(train_x, targets(train), dev_x, targets(dev), args.cs)
    # The source classifier is deliberately discarded. Fit coefficients are cast
    # by make_head to the actual FP32 inference representation before calibration.
    del previous_head
    head = make_head(fitted, args.device)
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    require(np.isfinite(probabilities).all(), 'Nonfinite FP32 refit development probabilities')
    cutoffs = thresholds(targets(dev), probabilities)
    score = metrics(targets(dev), probabilities, cutoffs)
    final_groups = ft.group_hashes(encoder, head)
    require(initial_body == _body_hashes(encoder, head), 'Head refit modified the backbone')
    learned_head = _head_parameters(head)
    head_selection = dict(**learned_head, C=fitted['C'], class_weight=fitted['class_weight'], thresholds=cutoffs,
        dev_metrics=score, fit_dev_metrics=fitted['dev_metrics'], fit_thresholds=fitted['thresholds'],
        protocol_signature=args.signature, precision='fp32', feature_normalization='L2',
        training_images=len(train), dev_images=len(dev), source_classifier_reused=False)
    common.save_array(folder / 'train_features.npy', train_x)
    common.save_array(folder / 'dev_features.npy', dev_x)
    common.save_array(folder / 'dev_probabilities.npy', probabilities)
    save(folder / 'head_candidates.json', tried)
    save(folder / 'head_selection.json', head_selection)
    ft.save_weights(folder / 'best.safetensors', ft.state(encoder, head))
    save(folder / 'parameter_audit.json', dict(initial_backbone=initial_body,
        final_backbone=_body_hashes(encoder, head), backbone_unchanged=True,
        source_classifier_sha256=initial_head, refit_classifier_sha256=final_groups['classifier'],
        classifier_changed=initial_head != final_groups['classifier'], classifier_fitted_to_corrected_labels=True))
    result = dict(spec)
    result.update(source='trained', backbone_lr=0., threshold_policy='dev_F1', thresholds=cutoffs,
        dev_metrics=score, dev_confusion=focused.confusion(targets(dev), probabilities, cutoffs),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        protocol_signature=args.signature, training_rows_sha256=digest(rows),
        backbone_initialization='original_pretrained' if source_checkpoint is None else 'prior_finetuned',
        source_checkpoint=str(source_checkpoint) if source_checkpoint is not None else None,
        source_checkpoint_sha256=source_sha, backbone_trained=False, head_refit_executed=True,
        source_classifier_reused=False, backbone_unchanged=True, trained_epochs=0, best_epoch=0,
        coef=learned_head['coef'], intercept=learned_head['intercept'],
        head_selection='head_selection.json', C=fitted['C'], class_weight=fitted['class_weight'],
        trainable_backbone_parameters=0, frozen_backbone_parameters=sum(p.numel() for p in encoder.parameters()),
        artifact_hashes={name: sha(folder / name) for name in
            ('best.safetensors', 'train_features.npy', 'dev_features.npy', 'dev_probabilities.npy',
             'head_candidates.json', 'head_selection.json', 'parameter_audit.json')})
    if source_checkpoint is not None:
        require(sha(source_checkpoint) == source_sha, 'Refit source checkpoint changed during extraction')
    save(folder / 'candidate_complete.json', result)
    log(args.run_dir, 'corrected_refit_complete', candidate=spec['id'], C=fitted['C'],
        class_weight=fitted['class_weight'], dev_accuracy=score['label_wise_accuracy'],
        dev_macro_f1=score['macro_f1'], dev_macro_AP=score['macro_average_precision'], backbone_trained=False)
    del encoder, head, train_x, dev_x, probabilities
    common.release()
    return result


def _group_name(name):
    if name.startswith('encoder.layers.'):
        return '.'.join(name.split('.')[:3])
    return name.split('.')[0]


def _bce_gradient_audit(encoder, head):
    """Measure before the anchor backward, proving the data loss reaches all groups."""
    import torch
    norms, unused = {}, []
    for name, parameter in list(encoder.backbone.named_parameters()) + [('classifier.' + n, p) for n, p in head.named_parameters()]:
        group = _group_name(name)
        norms.setdefault(group, 0.)
        if parameter.grad is None:
            unused.append(name)
            continue
        require(bool(torch.isfinite(parameter.grad).all()), 'Nonfinite BCE gradient: ' + name)
        norms[group] += float(parameter.grad.float().norm().cpu())
    require(norms and all(math.isfinite(v) and v > 0 for v in norms.values()),
            'Full-tuning BCE must reach every vision group and classifier: ' + str(norms))
    return norms, unused


def _anchor_penalty(encoder, anchor):
    """Raw L2-SP penalty: 1/2 sum over backbone parameters; no head, no averaging."""
    import torch
    terms = []
    for name, parameter in encoder.backbone.named_parameters():
        terms.append((parameter.float() - anchor[name]).square().sum())
    return .5 * torch.stack(terms).sum()


def _training_epoch(args, spec, encoder, head, optimizer, train, positive_weights, anchor, epoch):
    import numpy as np
    import torch
    require(train and all(row['split'] == 'train' for row in train), 'Only train rows may enter optimization')
    encoder.train()
    head.train()
    order = list(range(len(train)))
    random.Random(args.seed + epoch).shuffle(order)
    bce_total, known_total, steps = 0., 0, 0
    group_bce_total, penalty_total, weighted_total = 0., 0., 0.
    gradient_audit, unused, penalty_backward_calls = None, None, 0
    for offset in range(0, len(order), 8):
        group = [train[i] for i in order[offset:offset + 8]]
        known = int(np.isfinite(targets(group)).sum())
        require(known > 0, 'Accumulation group has no known training labels')
        optimizer.zero_grad(set_to_none=True)
        group_bce = 0.
        for start in range(0, len(group), args.microbatch):
            batch = group[start:start + args.microbatch]
            x = torch.cat([ft.prepare_train(encoder, args.packet, row, True) for row in batch]).to(args.device)
            y = torch.tensor(targets(batch), device=args.device)
            numerator, n = ft.masked_loss(head(encoder(x)), y, positive_weights)
            loss = numerator / known
            require(bool(torch.isfinite(loss)), 'Nonfinite corrected full-tuning BCE')
            loss.backward()
            value = float(numerator.detach().cpu())
            bce_total += value
            group_bce += value / known
            known_total += n
        if epoch == 1 and gradient_audit is None:
            gradient_audit, unused = _bce_gradient_audit(encoder, head)
        # Exactly one anchor evaluation/backward per effective optimizer group.
        # The zero-lambda control executes the same BCE gradients and optimizer
        # steps; drift is measured under no_grad, never added to its gradients.
        if spec['l2sp'] > 0:
            penalty = _anchor_penalty(encoder, anchor)
            weighted = spec['l2sp'] * penalty
            require(bool(torch.isfinite(weighted)), 'Nonfinite weighted L2-SP penalty')
            weighted.backward()
            penalty_backward_calls += 1
        else:
            with torch.no_grad():
                penalty = _anchor_penalty(encoder, anchor)
            weighted = penalty * 0.
        require(bool(torch.isfinite(penalty)), 'Nonfinite L2-SP drift measurement')
        penalty_total += float(penalty.detach().cpu())
        weighted_total += float(weighted.detach().cpu())
        group_bce_total += group_bce
        torch.nn.utils.clip_grad_norm_(list(encoder.parameters()) + list(head.parameters()), 1., error_if_nonfinite=True)
        optimizer.step()
        steps += 1
    require(known_total == int(np.isfinite(targets(train)).sum()), 'Known training-label population changed')
    require(penalty_backward_calls == (steps if spec['l2sp'] > 0 else 0), 'Incorrect anchor accumulation count')
    return dict(bce=bce_total / known_total, anchor_penalty=penalty_total / steps,
        weighted_penalty=weighted_total / steps, objective=(group_bce_total + weighted_total) / steps,
        known_cells=known_total, optimizer_steps=steps, anchor_evaluations=steps,
        penalty_backward_calls=penalty_backward_calls, gradient_audit=gradient_audit,
        parameters_without_bce_gradient=unused)


def train_full(args, spec, rows, head_candidate):
    """All candidates share one corrected head and the unchanged pretrained anchor."""
    import numpy as np
    import torch
    from safetensors.torch import load_file

    train, dev = _splits(rows)
    require(math.isfinite(spec['backbone_lr']) and spec['backbone_lr'] > 0
            and math.isfinite(spec['l2sp']) and spec['l2sp'] >= 0, 'Invalid full-tuning learning rate/anchor coefficient')
    require(spec['positive_weight'] in ('none', 'train_balanced'), 'Unsupported BCE positive weighting')
    require(args.microbatch in (1, 2, 4, 8) and args.epochs >= 1 and args.patience >= 1, 'Invalid full-tuning schedule')
    require(head_candidate.get('backbone_initialization') == 'original_pretrained'
            and head_candidate.get('backbone_unchanged') is True, 'Full tuning must start from the original-pretrained refit')
    _verify(args, head_candidate)
    head_checkpoint = contained(args.run_dir, head_candidate['checkpoint'])
    head_checkpoint_sha = sha(head_checkpoint)
    cached = _cached(args, spec, rows)
    if cached is not None:
        require(cached['head_checkpoint_sha256'] == head_checkpoint_sha
                and cached['head_candidate_id'] == head_candidate['id'], 'Clean-start initialization changed')
        return cached
    folder = _folder(args, spec)
    folder.mkdir(parents=True, exist_ok=True)
    _seed(args)
    encoder, head = ft.build_models(args.model_dir, args.device, head_candidate)
    pretrained_groups = _body_hashes(encoder, head)
    anchor = {name: parameter.detach().float().clone() for name, parameter in encoder.backbone.named_parameters()}
    ft.load_state(encoder, head, load_file(str(head_checkpoint)))
    require(pretrained_groups == _body_hashes(encoder, head), 'Refit checkpoint backbone differs from the pretrained anchor')
    encoder.backbone.requires_grad_(True)
    head.requires_grad_(True)
    initial = ft.group_hashes(encoder, head)
    positive = torch.tensor(ft.positive_weights(targets(train), spec['positive_weight'] == 'train_balanced'), device=args.device)
    optimizer = torch.optim.AdamW([dict(params=encoder.parameters(), lr=spec['backbone_lr']),
                                  dict(params=head.parameters(), lr=.001)], weight_decay=1e-4, foreach=False)
    history, gradient_audit, unused = [], None, None
    best_ap, best_epoch, stale = -1., None, 0
    if str(args.device).startswith('cuda'):
        torch.cuda.reset_peak_memory_stats()
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = _training_epoch(args, spec, encoder, head, optimizer, train, positive, anchor, epoch)
        if epoch == 1:
            gradient_audit, unused = trained['gradient_audit'], trained['parameters_without_bce_gradient']
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        scored = metrics(targets(dev), probabilities, [.5] * len(LABELS))
        ap = scored['macro_average_precision']
        history.append(dict(epoch=epoch, train_bce=trained['bce'], train_anchor_penalty=trained['anchor_penalty'],
            train_weighted_penalty=trained['weighted_penalty'], train_objective=trained['objective'],
            known_cells=trained['known_cells'], optimizer_steps=trained['optimizer_steps'],
            anchor_evaluations=trained['anchor_evaluations'], penalty_backward_calls=trained['penalty_backward_calls'],
            dev_macro_AP=ap, dev_accuracy_at_half=scored['label_wise_accuracy'],
            elapsed_seconds=time.perf_counter() - started))
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            ft.save_weights(folder / 'best.safetensors', ft.state(encoder, head))
        else:
            stale += 1
        save(folder / 'history.json', history)
        log(args.run_dir, 'corrected_full_epoch', candidate=spec['id'], best_epoch=best_epoch,
            bce=trained['bce'], anchor_penalty=trained['anchor_penalty'], weighted_penalty=trained['weighted_penalty'],
            **history[-1])
        if stale >= args.patience:
            break
    ft.load_state(encoder, head, load_file(str(folder / 'best.safetensors')))
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    require(np.isfinite(probabilities).all(), 'Nonfinite full-tuning development probabilities')
    cutoffs = thresholds(targets(dev), probabilities)
    score = metrics(targets(dev), probabilities, cutoffs)
    final = ft.group_hashes(encoder, head)
    require(set(final) == set(initial), 'Parameter groups changed identity')
    changed = {name: initial[name] != final[name] for name in initial}
    require(changed and all(changed.values()), 'Every full-tuning vision group and classifier must change: ' + str(changed))
    common.save_array(folder / 'dev_probabilities.npy', probabilities)
    save(folder / 'parameter_audit.json', dict(initial=initial, selected_checkpoint=final, changed=changed,
        pretrained_anchor_backbone=pretrained_groups, anchor_matches_initial_backbone=True, classifier_in_anchor=False,
        gradient_audit=gradient_audit, parameters_without_bce_gradient=unused,
        l2sp_definition='lambda/2 * sum_backbone((theta-theta_pretrained)^2); once per optimizer group'))
    result = dict(spec)
    result.update(source='trained', threshold_policy='dev_F1', thresholds=cutoffs, dev_metrics=score,
        dev_confusion=focused.confusion(targets(dev), probabilities, cutoffs),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        protocol_signature=args.signature, training_rows_sha256=digest(rows), backbone_initialization='original_pretrained',
        head_candidate_id=head_candidate['id'], head_checkpoint_sha256=head_checkpoint_sha,
        best_epoch=best_epoch, trained_epochs=len(history), backbone_trained=True,
        gradient_audit=gradient_audit, parameters_without_bce_gradient=unused, parameter_groups_changed=changed,
        positive_weights=positive.detach().cpu().tolist(), classifier_in_anchor=False,
        trainable_backbone_parameters=sum(p.numel() for p in encoder.parameters() if p.requires_grad),
        peak_training_cuda_bytes=torch.cuda.max_memory_allocated() if str(args.device).startswith('cuda') else None,
        **_head_parameters(head), artifact_hashes={name: sha(folder / name) for name in
            ('best.safetensors', 'history.json', 'dev_probabilities.npy', 'parameter_audit.json')})
    require(sha(head_checkpoint) == head_checkpoint_sha, 'Head initialization changed during full tuning')
    save(folder / 'candidate_complete.json', result)
    log(args.run_dir, 'corrected_full_candidate_complete', candidate=spec['id'], best_epoch=best_epoch,
        dev_accuracy=score['label_wise_accuracy'], dev_macro_f1=score['macro_f1'], dev_macro_AP=score['macro_average_precision'])
    del encoder, head, optimizer, anchor, probabilities
    common.release()
    return result
