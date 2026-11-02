"""Full DINOv2 S/B tuning with the existing frozen run, split and CUDA runtime."""
from pathlib import Path
import argparse
import copy
import fcntl
import hashlib
import math
import os
import random
import time
import traceback

import dinov2_compare as dino
from dinov2_compare import common, HERE, SPECS, CONTROL, require
from benchlib import (LABELS, SEED, contained, csv_rows, digest, load_test, log,
                      metrics, read, save, sha, targets, thresholds)

OUTPUTS = ('comparison.csv', 'per_label_comparison.csv', 'error_changes.json', 'comparison_summary.json')


def frozen_name(kind):
    return kind + '_frozen'


def tuned_name(kind):
    return kind + '_full_finetune'


def model_names():
    return [CONTROL] + [name for kind in SPECS for name in (frozen_name(kind), tuned_name(kind))]


def input_protocol(args, environment):
    """Verify old artifacts by hash; no old predictions enter fitting or selection."""
    baseline_head, control = dino.control_inputs(args)
    protocol = read(args.frozen_run / 'protocol.json')
    status = read(args.frozen_run / 'status.json')
    config = protocol['config']
    require(status['status'] == 'complete' and protocol['signature'] == digest(config)
            and status['protocol_signature'] == protocol['signature'], 'Completed DINOv2 frozen comparison required')
    require(environment == config['environment'] == read(args.baseline / 'environment.json'),
            'Reuse the same GPU/runtime as the completed DINOv2 and SigLIP2 runs')
    require(config['baseline_protocol_sha256'] == sha(args.baseline / 'protocol.json')
            and config['prior_protocol_sha256'] == sha(args.prior_run / 'protocol.json')
            and config['control_selection_sha256'] == sha(args.prior_run / 'selection_frozen.json')
            and config['control_checkpoint_sha256'] == sha(contained(args.prior_run, control['checkpoint']))
            and config['bundle_sha256'] == sha(args.bundle / 'bundle_manifest.json'), 'Frozen comparison inputs changed')
    require(config['input']['dino_size'] == args.size, 'Keep the frozen comparison input resolution')
    for name, checksum in config['script_hashes'].items():
        require(sha(HERE / name) == checksum, 'Frozen comparison source changed: ' + name)
    require(sha(common.SOURCE / 'benchlib.py') == config['benchlib_sha256'], 'Original benchlib changed')
    packets = {kind: dino.model_packet(args.models_root, kind) for kind in SPECS}
    require(packets == config['models'], 'DINOv2 pretrained weights differ from the frozen comparison')
    view = copy.copy(args)
    view.run_dir = args.frozen_run
    frozen = dino.verify_selection(view, protocol['signature'])
    selection_sha = sha(args.frozen_run / 'selection_frozen.json')
    for name in (CONTROL, *SPECS):
        folder = args.frozen_run / name
        done = read(folder / 'complete.json')
        require(done['selection_sha256'] == selection_sha, 'Original evaluation selection mismatch')
        dino.verify_files(folder, done['artifact_hashes'])
    dino.verify_files(args.frozen_run, status['output_hashes'])
    return baseline_head, control, frozen, packets


def group_hashes(encoder, head):
    groups = {}
    params = list(encoder.backbone.named_parameters()) + [('classifier.' + n, p) for n, p in head.named_parameters()]
    for name, parameter in params:
        group = '.'.join(name.split('.')[:3]) if name.startswith('encoder.layer.') else name.split('.')[0]
        checksum = groups.setdefault(group, hashlib.sha256())
        checksum.update(name.encode())
        checksum.update(parameter.detach().cpu().float().contiguous().numpy().tobytes())
    return {name: value.hexdigest() for name, value in groups.items()}


def build_models(args, kind, selection):
    from encoders import make_head
    encoder = dino.load_dino(args.models_root / SPECS[kind]['folder'], kind, args.device, args.size, selection['pooling'])
    head = make_head(selection, args.device)
    encoder.backbone.requires_grad_(True)
    head.requires_grad_(True)
    return encoder, head


def training_epoch(args, encoder, head, optimizer, train, positive_weight, epoch):
    import numpy as np
    import torch
    import finetune_siglip as ft
    require(train and all(row['split'] == 'train' for row in train), 'Only train rows may enter the optimizer')
    encoder.train()
    head.train()
    order = list(range(len(train)))
    random.Random(SEED + epoch).shuffle(order)
    total, count, steps, audit, unused = 0., 0, 0, None, None
    for offset in range(0, len(order), 8):
        group = [train[i] for i in order[offset:offset + 8]]
        known = int(np.isfinite(targets(group)).sum())
        require(known > 0, 'Training accumulation group has no known labels')
        optimizer.zero_grad(set_to_none=True)
        for start in range(0, len(group), args.microbatch):
            batch = group[start:start + args.microbatch]
            x = torch.cat([ft.prepare_train(encoder, args.bundle, row, True) for row in batch]).to(args.device)
            y = torch.tensor(targets(batch), device=args.device)
            numerator, n = ft.masked_loss(head(encoder(x)), y, positive_weight)
            loss = numerator / known
            require(bool(torch.isfinite(loss)), 'Nonfinite DINOv2 training loss')
            loss.backward()
            total += float(numerator.detach().cpu())
            count += n
        if epoch == 1 and audit is None:
            last = encoder.backbone.config.num_hidden_layers - 1
            names = ('embeddings.patch_embeddings.projection.weight', 'encoder.layer.0.attention.q_proj.weight',
                     f'encoder.layer.{last}.attention.q_proj.weight', 'layernorm.weight')
            parameters = dict(encoder.backbone.named_parameters())
            require(all(name in parameters for name in names), 'Unexpected DINOv2 parameter names for gradient audit')
            audit = {name: None if parameters[name].grad is None else float(parameters[name].grad.float().norm().cpu())
                     for name in names}
            audit['classifier.weight'] = None if head.weight.grad is None else float(head.weight.grad.float().norm().cpu())
            require(all(value is not None and math.isfinite(value) and value > 0 for value in audit.values()),
                    'DINOv2 backbone gradient audit failed: ' + str(audit))
            # The pretrained mask token is normally unused without masked-image inputs.
            unused = [name for name, parameter in parameters.items() if parameter.grad is None]
        torch.nn.utils.clip_grad_norm_(list(encoder.parameters()) + list(head.parameters()), 1., error_if_nonfinite=True)
        optimizer.step()
        steps += 1
    require(count == int(np.isfinite(targets(train)).sum()), 'Known training-label count changed')
    return dict(loss=total / count, known_cells=count, optimizer_steps=steps,
                gradient_audit=audit, parameters_without_gradient=unused)


def verify_candidate(args, candidate, signature):
    require(candidate['protocol_signature'] == signature, 'Candidate protocol changed')
    folder = contained(args.run_dir, candidate['checkpoint']).parent
    dino.verify_files(folder, candidate['artifact_hashes'])
    if 'completion_sha256' in candidate:
        require(sha(folder / 'candidate_complete.json') == candidate['completion_sha256'], 'Candidate completion changed')


def candidate_train(args, spec, selection, rows, signature):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    import finetune_siglip as ft
    folder = args.run_dir / 'candidates' / spec['id']
    folder.mkdir(parents=True, exist_ok=True)
    done = folder / 'candidate_complete.json'
    if done.exists():
        result = read(done)
        require(all(result[k] == v for k, v in spec.items()), 'Cached candidate specification changed')
        verify_candidate(args, result, signature)
        log(args.run_dir, 'candidate_reused', candidate=spec['id'])
        return dict(**result, completion_sha256=sha(done))
    train = [row for row in rows if row['split'] == 'train']
    dev = [row for row in rows if row['split'] == 'dev']
    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    encoder, head = build_models(args, spec['kind'], selection)
    initial = group_hashes(encoder, head)
    initial_prob = ft.predict_dev(encoder, head, args.bundle, dev)
    common.save_array(folder / 'initial_dev_probabilities.npy', initial_prob)
    initial_metrics = metrics(targets(dev), initial_prob, selection['thresholds'])
    require(abs(initial_metrics['label_wise_accuracy'] - selection['dev_metrics']['label_wise_accuracy']) < 1e-12,
            'Frozen DINOv2 dev decisions do not reproduce; stop before training')
    positive = torch.tensor(ft.positive_weights(targets(train), spec['positive_weight'] == 'train_balanced'), device=args.device)
    optimizer = torch.optim.AdamW([dict(params=encoder.parameters(), lr=spec['backbone_lr']),
                                  dict(params=head.parameters(), lr=.001)], weight_decay=.0001, foreach=False)
    best_ap, best_epoch, stale = -1., None, 0
    history, gradient_audit, unused = [], None, None
    torch.cuda.reset_peak_memory_stats()
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = training_epoch(args, encoder, head, optimizer, train, positive, epoch)
        if epoch == 1:
            gradient_audit, unused = trained['gradient_audit'], trained['parameters_without_gradient']
        probabilities = ft.predict_dev(encoder, head, args.bundle, dev)
        scored = metrics(targets(dev), probabilities, [.5] * len(LABELS))
        ap = scored['macro_average_precision']
        history.append(dict(epoch=epoch, train_loss=trained['loss'], known_cells=trained['known_cells'],
            optimizer_steps=trained['optimizer_steps'], dev_macro_AP=ap,
            dev_accuracy_at_half=scored['label_wise_accuracy'], elapsed_seconds=time.perf_counter() - started))
        # Epoch zero is a separate frozen control, never a hidden fallback.
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            ft.save_weights(folder / 'best.safetensors', ft.state(encoder, head))
        else:
            stale += 1
        save(folder / 'history.json', history)
        log(args.run_dir, 'dinov2_finetune_epoch', candidate=spec['id'], epoch=epoch,
            train_loss=trained['loss'], dev_macro_AP=ap, best_epoch=best_epoch,
            elapsed_seconds=history[-1]['elapsed_seconds'])
        if stale >= args.patience:
            break
    ft.load_state(encoder, head, load_file(str(folder / 'best.safetensors')))
    probabilities = ft.predict_dev(encoder, head, args.bundle, dev)
    cutoffs = thresholds(targets(dev), probabilities)
    score = metrics(targets(dev), probabilities, cutoffs)
    final = group_hashes(encoder, head)
    changed = {name: initial[name] != checksum for name, checksum in final.items()}
    require(set(changed) == set(initial) and changed and all(changed.values()),
            'All DINOv2 block groups, embeddings, final norm and head must change: ' + str(changed))
    common.save_array(folder / 'dev_probabilities.npy', probabilities)
    save(folder / 'parameter_audit.json', dict(initial=initial, selected_checkpoint=final, changed=changed,
                                              gradient_audit=gradient_audit, parameters_without_gradient=unused))
    result = dict(**spec, protocol_signature=signature, pooling=selection['pooling'],
        best_epoch=best_epoch, trained_epochs=len(history), thresholds=cutoffs, dev_metrics=score,
        initial_dev_metrics=initial_metrics, backbone_trained=True, parameter_groups_changed=changed,
        gradient_audit=gradient_audit, parameters_without_gradient=unused,
        trainable_backbone_parameters=sum(p.numel() for p in encoder.parameters() if p.requires_grad),
        positive_weights=positive.detach().cpu().tolist(), peak_training_cuda_bytes=torch.cuda.max_memory_allocated(),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        artifact_hashes={name: sha(folder / name) for name in
            ('best.safetensors', 'history.json', 'initial_dev_probabilities.npy', 'dev_probabilities.npy', 'parameter_audit.json')})
    save(done, result)
    log(args.run_dir, 'dinov2_candidate_complete', candidate=spec['id'], best_epoch=best_epoch,
        dev_accuracy=score['label_wise_accuracy'], dev_macro_f1=score['macro_f1'])
    del encoder, head, optimizer
    common.release()
    return dict(**result, completion_sha256=sha(done))


def choose(candidates):
    return max(candidates, key=lambda c: (c['dev_metrics']['macro_f1'], c['dev_metrics']['label_wise_accuracy'],
        c['dev_metrics']['macro_average_precision'], -c['backbone_lr'], c['positive_weight'] == 'none'))


def verify_selection(args, signature):
    path = args.run_dir / 'selection_frozen.json'
    selected = read(path)
    require(read(args.run_dir / 'selection_commit.json')['sha256'] == sha(path)
            and selected['protocol_signature'] == signature, 'Frozen selection hash/protocol mismatch')
    require(set(selected['models']) == set(SPECS), 'Both S/B selections must freeze before test access')
    require(selected['frozen_selection_sha256'] == sha(args.frozen_run / 'selection_frozen.json'), 'Frozen control selection changed')
    for candidate in selected['models'].values():
        verify_candidate(args, candidate, signature)
    return selected


def evaluation_cache(args, name, selection_sha):
    folder = args.run_dir / name
    done = folder / 'complete.json'
    if not done.exists():
        return None
    cached = read(done)
    require(cached['selection_sha256'] == selection_sha, 'Cached evaluation selection changed')
    dino.verify_files(folder, cached['artifact_hashes'])
    return cached['result']


def comparison_details(args, predictions):
    comparisons = [(frozen_name(k), tuned_name(k)) for k in SPECS] + [(CONTROL, tuned_name(k)) for k in SPECS]
    records, changes = [], []
    for reference, candidate in comparisons:
        old, new = predictions[reference], predictions[candidate]
        require(len(old) == len(new) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
                for a, b in zip(old, new)), 'Comparison image/label order differs')
        for j, label in enumerate(LABELS):
            row = dict(reference_model=reference, candidate_model=candidate, label=label)
            for prefix, items in (('prior', old), ('candidate', new)):
                counts = dict(tp=0, fp=0, fn=0, tn=0)
                for item in items:
                    y, pred = item['labels'][label], item['predictions'][j]
                    if y is not None:
                        counts[('tp' if pred else 'fn') if y else ('fp' if pred else 'tn')] += 1
                row.update({prefix + '_' + k: v for k, v in counts.items()})
            row.update(fp_delta=row['candidate_fp'] - row['prior_fp'], fn_delta=row['candidate_fn'] - row['prior_fn'])
            records.append(row)
            for a, b in zip(old, new):
                y = a['labels'][label]
                if y is not None and a['predictions'][j] != b['predictions'][j]:
                    changes.append(dict(reference_model=reference, candidate_model=candidate, image_id=a['image_id'],
                        label=label, truth=y, prior_prediction=a['predictions'][j], candidate_prediction=b['predictions'][j],
                        change='recovered' if b['predictions'][j] == y else 'regressed'))
    csv_rows(args.run_dir / 'per_label_comparison.csv', records)
    save(args.run_dir / 'error_changes.json', changes)


def evaluate(args, selected, frozen, baseline_head, control):
    import numpy as np
    import finetune_siglip as ft
    from encoders import make_head
    from safetensors.torch import load_file
    selection_path = args.run_dir / 'selection_frozen.json'
    selection_sha = sha(selection_path)
    rows = load_test(args.bundle, selection_path, selection_sha)
    results, predictions = {}, {}
    mapping = {name: kind for kind in SPECS for name in (frozen_name(kind), tuned_name(kind))}
    for name in model_names():
        folder = args.run_dir / name
        folder.mkdir(exist_ok=True)
        result = evaluation_cache(args, name, selection_sha)
        if result is None:
            if name == CONTROL:
                encoder, head = ft.build_models(args.model_dir, args.device, baseline_head)
                ft.load_state(encoder, head, load_file(str(contained(args.prior_run, control['checkpoint']))))
                cutoffs = control['thresholds']
                expected_path = args.frozen_run / CONTROL / 'test_predictions.json'
            else:
                kind = mapping[name]
                initial = frozen['models'][kind]
                encoder, head = build_models(args, kind, initial)
                cutoffs = initial['thresholds']
                expected_path = args.frozen_run / kind / 'test_predictions.json'
                if name == tuned_name(kind):
                    candidate = selected['models'][kind]
                    ft.load_state(encoder, head, load_file(str(contained(args.run_dir, candidate['checkpoint']))))
                    cutoffs, expected_path = candidate['thresholds'], None
            encoder.eval().requires_grad_(False)
            head.eval().requires_grad_(False)
            result = common.benchmark(encoder, head, args.bundle, rows, np.asarray(cutoffs), folder, args.repeats, args.warmups)
            result['goal'] = dict(accuracy_90=result['metrics']['label_wise_accuracy'] >= .9,
                p95_50=result['timing']['total_ms']['p95_ms'] <= 50,
                all_met=result['metrics']['label_wise_accuracy'] >= .9 and result['timing']['total_ms']['p95_ms'] <= 50)
            if expected_path is not None:
                expected, current = read(expected_path), read(folder / 'test_predictions.json')
                require(len(expected) == len(current) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
                        and a['predictions'] == b['predictions'] for a, b in zip(expected, current)),
                        'Original control decisions did not reproduce: ' + name)
                result['original_control_decisions_reproduced'] = True
            save(folder / 'complete.json', dict(selection_sha256=selection_sha, result=result,
                artifact_hashes={n: sha(folder / n) for n in dino.EVAL_FILES}))
            del encoder, head
            common.release()
        results[name] = result
        predictions[name] = read(folder / 'test_predictions.json')
    csv_rows(args.run_dir / 'comparison.csv', [dict(model=name,
        accuracy=r['metrics']['label_wise_accuracy'], macro_f1=r['metrics']['macro_f1'],
        mean_ms=r['timing']['total_ms']['mean_ms'], median_ms=r['timing']['total_ms']['median_ms'],
        p95_ms=r['timing']['total_ms']['p95_ms'], accuracy_90=r['goal']['accuracy_90'], p95_50=r['goal']['p95_50'])
        for name, r in results.items()])
    comparison_details(args, predictions)
    summary = dict(results=results, dev_selected_dino=selected['dev_selected_dino'],
        goal_met=results[tuned_name(selected['dev_selected_dino'])]['goal']['all_met'],
        goal_by_model={name: r['goal']['all_met'] for name, r in results.items()},
        selected_candidates={kind: c['id'] for kind, c in selected['models'].items()},
        selected_epochs={kind: c['best_epoch'] for kind, c in selected['models'].items()},
        backbone_training_executed=True, head_training_executed=True, OCR_executed=False,
        test_used_for_selection=False, previously_exposed_test=True,
        note='Same exposed37 test; not independent generalization. DINOv2 input392, SigLIP2 input384; FP32, batch1. '
             'All five models remeasured in this run; decode/preprocess/transfer/vision/head included; '
             'OCR/network/DB/model loading/training/TensorRT excluded.')
    save(args.run_dir / 'comparison_summary.json', summary)
    return summary


def run(args):
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    args.device = 'cuda:0'
    environment = common.cuda_environment('fp32')
    _, _, rows = common.bundle_data(args.bundle)
    baseline_head, control, frozen, packets = input_protocol(args, environment)
    specs = [dict(id=f'{kind}-lr{lr:.0e}-{mode}', kind=kind, backbone_lr=lr, positive_weight=mode)
             for kind in SPECS for lr in args.lrs for mode in ('none', 'train_balanced')]
    require(len({s['id'] for s in specs}) == len(specs), 'Learning rates must produce distinct candidate IDs')
    sources = ('finetune_dinov2.py', 'run_dinov2_finetune.sh', 'dinov2_compare.py', 'run_dinov2.sh',
               'finetune_siglip.py', 'encoders.py', 'run_experiment.py')
    config = dict(environment=environment, models=packets,
        frozen_protocol_sha256=sha(args.frozen_run / 'protocol.json'),
        frozen_selection_sha256=sha(args.frozen_run / 'selection_frozen.json'),
        prior_protocol_sha256=sha(args.prior_run / 'protocol.json'),
        prior_selection_sha256=sha(args.prior_run / 'selection_frozen.json'),
        bundle_sha256=sha(args.bundle / 'bundle_manifest.json'), candidates=specs,
        input_size=args.size, precision='fp32', pooling='reuse_each_frozen_dev_selected_pooling',
        head_initialization='completed_DINOv2_frozen_logistic_head', head_lr=.001,
        optimizer='AdamW', weight_decay=.0001, gradient_clip=1., microbatch=args.microbatch, effective_batch=8,
        augmentation=dict(brightness=.08, contrast=.08, saturation=.05, geometry=False),
        max_epochs=args.epochs, patience=args.patience, checkpoint_selection='dev_macro_AP; epoch>=1; earliest tie',
        candidate_selection='dev_macro_F1,accuracy,macro_AP,smaller_LR,unweighted',
        dino_model_selection='dev_accuracy,macro_F1,macro_AP,small', thresholds='per_label_dev_F1_0_to_1_step_.05',
        warmups=args.warmups, repeats=args.repeats, seed=SEED,
        script_hashes={n: sha(HERE / n) for n in sources}, benchlib_sha256=sha(common.SOURCE / 'benchlib.py'))
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            previous = read(protocol_path)
            require(args.resume and previous['signature'] == signature and digest(previous['config']) == signature,
                    'Existing run requires --resume with identical code/data/GPU/settings; changes need a new run directory')
        else:
            require(not any(p.name != '.lock' for p in args.run_dir.iterdir()), 'Run directory must be empty')
            save(protocol_path, dict(signature=signature, config=config))
            save(args.run_dir / 'environment.json', environment)
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            verify_selection(args, signature)
            selection_sha = sha(args.run_dir / 'selection_frozen.json')
            for name in model_names():
                require(evaluation_cache(args, name, selection_sha) is not None, 'Completed evaluation missing')
            dino.verify_files(args.run_dir, read(status_path)['output_hashes'])
            print('Already complete; no training or inference repeated. ' + str(args.run_dir / 'comparison.csv'))
            return
        save(status_path, dict(status='running', protocol_signature=signature))
        log(args.run_dir, 'dinov2_full_finetune_started', candidates=len(specs), training_images=113, dev_images=37)
        try:
            selection_path = args.run_dir / 'selection_frozen.json'
            if not selection_path.exists():
                models = {}
                for kind in SPECS:
                    candidates = [candidate_train(args, spec, frozen['models'][kind], rows, signature)
                                  for spec in specs if spec['kind'] == kind]
                    models[kind] = choose(candidates)
                best = max(SPECS, key=lambda k: (models[k]['dev_metrics']['label_wise_accuracy'],
                    models[k]['dev_metrics']['macro_f1'], models[k]['dev_metrics']['macro_average_precision'],
                    k == 'dinov2_small'))
                save(selection_path, dict(protocol_signature=signature, models=models, dev_selected_dino=best,
                    frozen_selection_sha256=config['frozen_selection_sha256'], labels=LABELS,
                    test_used_for_selection=False, previously_exposed_test=True))
                save(args.run_dir / 'selection_commit.json', dict(sha256=sha(selection_path)))
                log(args.run_dir, 'all_finetune_selections_frozen_before_test', dev_selected_dino=best, sha256=sha(selection_path))
            selected = verify_selection(args, signature)
            summary = evaluate(args, selected, frozen, baseline_head, control)
            common.bundle_data(args.bundle)
            _, _, _, final_packets = input_protocol(args, environment)
            require(final_packets == packets and all(sha(HERE / n) == h for n, h in config['script_hashes'].items()),
                    'Input model/source changed during run')
            verify_selection(args, signature)
            save(status_path, dict(status='complete', protocol_signature=signature, CUDA_executed=True,
                backbone_training_executed=True, head_training_executed=True, OCR_executed=False,
                output_hashes={n: sha(args.run_dir / n) for n in OUTPUTS}))
            log(args.run_dir, 'dinov2_full_finetune_complete', run=str(args.run_dir), goal_met=summary['goal_met'])
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=signature))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'show-results'))
    parser.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--frozen-run', type=Path, default=HERE / 'runs/dinov2-heads-fp32')
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/dinov2-full-fp32')
    parser.add_argument('--size', type=int, default=392)
    parser.add_argument('--lrs', type=float, nargs='+', default=[1e-5, 3e-5])
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--patience', type=int, default=3)
    parser.add_argument('--microbatch', type=int, default=2, choices=(1, 2, 4, 8))
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('models_root', 'model_dir', 'bundle', 'baseline', 'prior_run', 'frozen_run', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.size < 14 or args.size % 14 or min(args.epochs, args.patience, args.warmups, args.repeats) < 1 or any(not math.isfinite(x) or x <= 0 for x in args.lrs):
        parser.error('Positive settings required; DINOv2 input size must be a multiple of 14')
    if args.command == 'show-results':
        for name in ('status.json', 'comparison.csv', 'per_label_comparison.csv'):
            path = args.run_dir / name
            print(path.read_text(encoding='utf-8') if path.is_file() else 'Not ready: ' + str(path))
    else:
        run(args)


if __name__ == '__main__':
    main()
