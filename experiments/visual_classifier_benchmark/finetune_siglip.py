"""Full vision-tower tuning using the existing runtime, frozen split and baseline."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_experiment as common
from benchlib import (LABELS, SEED, contained, csv_rows, digest, load_test, log, metrics,
                      read, save, sha, targets, thresholds, timing_orders)


def masked_loss(logits, labels, positive_weight):
    import torch
    from torch.nn import functional as F
    mask = torch.isfinite(labels)
    if not mask.any():
        raise ValueError('Batch has no known labels')
    safe = torch.nan_to_num(labels, nan=0.0)
    loss = F.binary_cross_entropy_with_logits(logits.float(), safe.float(),
                                              pos_weight=positive_weight, reduction='none')
    return (loss * mask).sum(), int(mask.sum().item())


def positive_weights(y, balanced):
    import numpy as np
    values = []
    for j in range(len(LABELS)):
        known = y[np.isfinite(y[:, j]), j]
        pos, neg = int((known == 1).sum()), int((known == 0).sum())
        if not pos or not neg:
            raise ValueError('Positive and negative train labels required: ' + LABELS[j])
        values.append(neg / pos if balanced else 1.0)
    return values


def group_hashes(encoder, head):
    groups = {}
    for name, parameter in list(encoder.backbone.named_parameters()) + [('classifier.' + n, p) for n, p in head.named_parameters()]:
        if name.startswith('encoder.layers.'):
            group = '.'.join(name.split('.')[:3])
        elif name.startswith('classifier.'):
            group = 'classifier'
        else:
            group = name.split('.')[0]
        h = groups.setdefault(group, hashlib.sha256())
        h.update(name.encode())
        h.update(parameter.detach().cpu().float().contiguous().numpy().tobytes())
    return {name: h.hexdigest() for name, h in groups.items()}


def state(encoder, head):
    return {**{'backbone.' + k: v.detach().cpu().contiguous().clone() for k, v in encoder.backbone.state_dict().items()},
            **{'classifier.' + k: v.detach().cpu().contiguous().clone() for k, v in head.state_dict().items()}}


def load_state(encoder, head, weights):
    encoder.backbone.load_state_dict({k[len('backbone.'):]: v for k, v in weights.items() if k.startswith('backbone.')}, strict=True)
    head.load_state_dict({k[len('classifier.'):]: v for k, v in weights.items() if k.startswith('classifier.')}, strict=True)


def save_weights(path, weights):
    from safetensors.torch import save_file
    temporary = path.with_name(path.name + '.tmp')
    save_file(weights, str(temporary))
    temporary.replace(path)


def prepare_train(encoder, bundle, row, augment):
    if not augment:
        return encoder.prepare(contained(bundle, row['image']))
    import numpy as np
    import torch
    from torchvision.transforms import ColorJitter
    from encoders import letterbox
    with letterbox(contained(bundle, row['image']), encoder.size) as original:
        im = ColorJitter(brightness=.08, contrast=.08, saturation=.05, hue=0)(original)
        array = np.asarray(im, dtype=np.float32).copy() / 255.0
        im.close()
    x = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).contiguous()
    return ((x - encoder.mean) / encoder.std).contiguous()


def predict_dev(encoder, head, bundle, rows):
    import numpy as np
    import torch
    encoder.eval(); head.eval()
    probabilities = []
    with torch.inference_mode():
        for row in rows:
            x = encoder.prepare(contained(bundle, row['image'])).to(encoder.device)
            probabilities.append(torch.sigmoid(head(encoder(x))).cpu().numpy()[0])
    return np.asarray(probabilities)


def training_epoch(encoder, head, optimizer, bundle, rows, positive_weight,
                   microbatch, effective_batch, epoch, augment=True, gradient_audit=False):
    import numpy as np
    import torch
    encoder.train(); head.train()
    order = list(range(len(rows)))
    random.Random(SEED + epoch).shuffle(order)
    total_numerator, total_known, steps = 0., 0, 0
    audit = None
    for group_start in range(0, len(order), effective_batch):
        group = [rows[i] for i in order[group_start:group_start + effective_batch]]
        known = sum(v is not None for row in group for v in row['labels'].values())
        if known == 0:
            raise ValueError('Accumulation group has no known labels')
        optimizer.zero_grad(set_to_none=True)
        for start in range(0, len(group), microbatch):
            batch = group[start:start + microbatch]
            x = torch.cat([prepare_train(encoder, bundle, row, augment) for row in batch]).to(encoder.device)
            y = torch.tensor(targets(batch), device=encoder.device)
            logits = head(encoder(x))
            numerator, n = masked_loss(logits, y, positive_weight)
            loss = numerator / known
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite training loss')
            loss.backward()
            total_numerator += float(numerator.detach().cpu())
            total_known += n
        if gradient_audit and audit is None:
            audit = {}
            for name, p in encoder.backbone.named_parameters():
                if name.endswith('patch_embedding.weight') or name.endswith('self_attn.q_proj.weight') or name.endswith('post_layernorm.weight'):
                    audit[name] = None if p.grad is None else float(p.grad.float().norm().cpu())
            audit['classifier.weight'] = None if head.weight.grad is None else float(head.weight.grad.float().norm().cpu())
            if not audit or any(v is None or not math.isfinite(v) or v <= 0 for v in audit.values()):
                raise ValueError('Backbone gradient audit failed: ' + str(audit))
        norm = torch.nn.utils.clip_grad_norm_(list(encoder.parameters()) + list(head.parameters()), 1.0, error_if_nonfinite=True)
        optimizer.step()
        steps += 1
    return dict(loss=total_numerator / total_known, known_cells=total_known,
                optimizer_steps=steps, gradient_audit=audit)


def build_models(folder, device, baseline_head):
    from encoders import load_encoder, make_head
    encoder = load_encoder('siglip2', folder, device, 'fp32', 384)
    head = make_head(baseline_head, device)
    encoder.backbone.requires_grad_(True)
    head.requires_grad_(True)
    return encoder, head


def candidate_train(args, candidate, baseline_head, td, loader=build_models):
    import torch
    from safetensors.torch import load_file
    train = [r for r in td if r['split'] == 'train']
    dev = [r for r in td if r['split'] == 'dev']
    folder = args.run_dir / 'candidates' / candidate['id']
    folder.mkdir(parents=True, exist_ok=True)
    random.seed(SEED); torch.manual_seed(SEED); torch.cuda.manual_seed_all(SEED)
    encoder, head = loader(args.model_dir, args.device, baseline_head)
    encoder.backbone.requires_grad_(True); head.requires_grad_(True)
    initial = group_hashes(encoder, head)
    pw = torch.tensor(positive_weights(targets(train), candidate['positive_weight'] == 'train_balanced'), device=args.device)
    optimizer = torch.optim.AdamW([
        {'params': encoder.parameters(), 'lr': candidate['backbone_lr']},
        {'params': head.parameters(), 'lr': .001}], weight_decay=.0001, foreach=False)
    best_ap, best_state, best_epoch, stale = -1., None, None, 0
    history, gradient_audit = [], None
    if str(args.device).startswith('cuda'):
        torch.cuda.reset_peak_memory_stats()
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = training_epoch(encoder, head, optimizer, args.bundle, train, pw,
                                  args.microbatch, 8, epoch, gradient_audit=epoch == 1)
        if epoch == 1:
            gradient_audit = trained['gradient_audit']
        prob = predict_dev(encoder, head, args.bundle, dev)
        # Checkpoint by threshold-independent AP. Thresholds are selected after it.
        scored = metrics(targets(dev), prob, [.5] * 8)
        ap = scored['macro_average_precision']
        history.append(dict(epoch=epoch, train_loss=trained['loss'],
                            optimizer_steps=trained['optimizer_steps'], known_cells=trained['known_cells'],
                            dev_macro_AP=ap, dev_accuracy_at_half=scored['label_wise_accuracy'],
                            elapsed_seconds=time.perf_counter() - started))
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            best_state = state(encoder, head)
            save_weights(folder / 'best.safetensors', best_state)
        else:
            stale += 1
        save(folder / 'history.json', history)
        log(args.run_dir, 'finetune_epoch', candidate=candidate['id'], epoch=epoch,
            train_loss=trained['loss'], dev_macro_AP=ap, best_epoch=best_epoch,
            elapsed_seconds=history[-1]['elapsed_seconds'])
        if stale >= args.patience:
            break
    load_state(encoder, head, best_state)
    prob = predict_dev(encoder, head, args.bundle, dev)
    cutoffs = thresholds(targets(dev), prob)
    score = metrics(targets(dev), prob, cutoffs)
    changed = {k: initial[k] != v for k, v in group_hashes(encoder, head).items()}
    if not changed or not all(changed.values()):
        raise ValueError('Every vision group and classifier must change: ' + str(changed))
    common.save_array(folder / 'dev_probabilities.npy', prob)
    result = dict(**candidate, best_epoch=best_epoch, thresholds=cutoffs, dev_metrics=score,
                  trainable_backbone_parameters=sum(p.numel() for p in encoder.parameters()),
                  gradient_audit=gradient_audit, parameter_groups_changed=changed,
                  positive_weights=pw.detach().cpu().tolist(), trained_epochs=len(history),
                  peak_training_cuda_bytes=torch.cuda.max_memory_allocated() if str(args.device).startswith('cuda') else None,
                  checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)))
    result['artifact_hashes'] = {n: sha(folder / n) for n in ('best.safetensors', 'history.json', 'dev_probabilities.npy')}
    save(folder / 'candidate_complete.json', result)
    log(args.run_dir, 'candidate_complete', candidate=candidate['id'], best_epoch=best_epoch,
        dev_accuracy=score['label_wise_accuracy'], dev_macro_f1=score['macro_f1'])
    del encoder, head, optimizer, best_state
    common.release()
    return result


def choose(candidates):
    return max(candidates, key=lambda x: (x['dev_metrics']['macro_f1'],
                                         x['dev_metrics']['label_wise_accuracy'],
                                         x['dev_metrics']['macro_average_precision'],
                                         -x['backbone_lr'], x['positive_weight'] == 'none'))


def assert_baseline(baseline):
    status = read(baseline / 'status.json')
    protocol = read(baseline / 'protocol.json')
    if status['status'] != 'complete' or protocol['signature'] != digest(protocol['config']):
        raise ValueError('Original completed baseline required')
    if status['protocol_signature'] != protocol['signature']:
        raise ValueError('Baseline protocol mismatch')
    selection = common.verify_selection(baseline, protocol['signature'])
    folder = baseline / 'siglip2'
    complete = read(folder / 'evaluation_complete.json')
    if complete['selection_sha256'] != sha(baseline / 'selection_frozen.json'):
        raise ValueError('Baseline selection mismatch')
    for name, checksum in complete['artifact_hashes'].items():
        if sha(folder / name) != checksum:
            raise ValueError('Baseline artifact changed: ' + name)
    for relative, checksum in protocol['config']['script_hashes'].items():
        if sha(HERE.parent / relative) != checksum:
            raise ValueError('Baseline model/preprocessing code changed: ' + relative)
    return protocol, selection['models']['siglip2']


def validate_checkpoint(out, selected):
    if read(out / 'selection_commit.json')['sha256'] != sha(out / 'selection_frozen.json'):
        raise ValueError('Frozen selection changed')
    folder = (out / selected['checkpoint']).parent
    for name, checksum in selected['artifact_hashes'].items():
        if sha(folder / name) != checksum:
            raise ValueError('Selected candidate changed: ' + name)


def evaluate_pair(args, selected, baseline_head):
    import torch
    from encoders import load_encoder, make_head
    from safetensors.torch import load_file
    out = args.run_dir
    selected_path = out / 'selection_frozen.json'
    validate_checkpoint(out, selected)
    # First access to the new run's test labels/images occurs after dev selection.
    rows = load_test(args.bundle, selected_path, sha(selected_path))
    results = {}
    baseline_rows = read(args.baseline / 'siglip2/test_predictions.json')
    for kind in ('siglip2_frozen', 'siglip2_full_finetune'):
        folder = out / kind
        folder.mkdir(exist_ok=True)
        encoder = load_encoder('siglip2', args.model_dir, args.device, 'fp32', 384)
        head = make_head(baseline_head, args.device)
        if kind == 'siglip2_full_finetune':
            load_state(encoder, head, load_file(str(out / selected['checkpoint'])))
        encoder.eval(); head.eval()
        cutoffs = baseline_head['thresholds'] if kind == 'siglip2_frozen' else selected['thresholds']
        torch.cuda.reset_peak_memory_stats()
        result = common.benchmark(encoder, head, args.bundle, rows,
                                  __import__('numpy').asarray(cutoffs), folder,
                                  args.repeats, args.warmups)
        result['peak_inference_cuda_bytes'] = torch.cuda.max_memory_allocated()
        if kind == 'siglip2_frozen':
            now = read(folder / 'test_predictions.json')
            if len(now) != len(baseline_rows) or any(
                a['image_id'] != b['image_id'] or a['predictions'] != b['predictions'] or a['labels'] != b['labels']
                for a, b in zip(now, baseline_rows)):
                raise ValueError('Re-evaluated frozen baseline decisions differ')
            result['original_baseline_decisions_reproduced'] = True
        save(folder / 'complete.json', dict(result=result, selection_sha256=sha(selected_path),
            artifact_hashes={n: sha(folder / n) for n in ('test_predictions.json', 'test_metrics.json', 'timing_raw.csv', 'timing_summary.json')}))
        results[kind] = result
        del encoder, head
        common.release()
    validate_checkpoint(out, selected)
    csv_rows(out / 'comparison.csv', [dict(model=k, accuracy=v['metrics']['label_wise_accuracy'],
        macro_f1=v['metrics']['macro_f1'], mean_ms=v['timing']['total_ms']['mean_ms'],
        median_ms=v['timing']['total_ms']['median_ms'], p95_ms=v['timing']['total_ms']['p95_ms'],
        accuracy_90=v['metrics']['label_wise_accuracy'] >= .9, p95_50=v['timing']['total_ms']['p95_ms'] <= 50)
        for k, v in results.items()])
    old, new = results['siglip2_frozen'], results['siglip2_full_finetune']
    return dict(status='complete', results=results, selected_candidate=selected['id'],
        selected_epoch=selected['best_epoch'], test_images=37, known_cells=282, unknown_excluded=14,
        accuracy_delta_pp=(new['metrics']['label_wise_accuracy'] - old['metrics']['label_wise_accuracy']) * 100,
        macro_f1_delta=new['metrics']['macro_f1'] - old['metrics']['macro_f1'],
        goal_met=new['metrics']['label_wise_accuracy'] >= .9 and new['timing']['total_ms']['p95_ms'] <= 50,
        backbone_training_executed=True, training_scope='full_SigLIP_vision_tower_and_8_label_head; text tower unused',
        OCR_executed=False, inference_precision='fp32', test_used_for_selection=False, previously_exposed_test=True)


def run(args):
    import torch
    args.device = 'cuda:0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    environment = common.cuda_environment('fp32')
    _, _, td = common.bundle_data(args.bundle)
    baseline_protocol, baseline_head = assert_baseline(args.baseline)
    if environment != read(args.baseline / 'environment.json'):
        raise ValueError('Reuse exactly the baseline GPU/runtime; environment changed')
    packet = common.model_packet(args.model_dir, 'siglip2')
    if packet != baseline_protocol['config']['models']['siglip2']:
        raise ValueError('Downloaded model bytes differ from the baseline')
    candidates = [dict(id=f'lr{lr:.0e}-{mode}', backbone_lr=lr, positive_weight=mode)
                  for lr in args.lrs for mode in ('none', 'train_balanced')]
    config = dict(environment=environment, model_packet=packet,
        baseline_protocol_sha256=sha(args.baseline / 'protocol.json'), baseline_selection_sha256=sha(args.baseline / 'selection_frozen.json'),
        bundle_sha256=sha(args.bundle / 'bundle_manifest.json'), candidates=candidates,
        max_epochs=args.epochs, patience=args.patience, microbatch=args.microbatch, effective_batch=8,
        precision='fp32', head_lr=.001, optimizer='AdamW', weight_decay=.0001, gradient_clip=1.,
        augmentation=dict(brightness=.08, contrast=.08, saturation=.05, hue=0, geometry=False),
        checkpoint_selection='dev_macro_AP; epoch>=1; earliest tie',
        candidate_selection='dev_macro_F1,accuracy,macro_AP,smaller_LR,unweighted',
        thresholds='per_label_dev_F1_max_0_to_1_step_.05', warmups=args.warmups, repeats=args.repeats,
        seed=SEED, script_hashes={n: sha(HERE / n) for n in ('finetune_siglip.py', 'encoders.py', 'run_experiment.py')})
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            if not args.resume or read(protocol_path)['signature'] != signature:
                raise ValueError('Existing run requires --resume with identical code/data/GPU/settings')
            if (args.run_dir / 'status.json').exists() and read(args.run_dir / 'status.json')['status'] == 'complete':
                log(args.run_dir, 'already_complete', run=str(args.run_dir))
                return
        else:
            save(protocol_path, dict(signature=signature, config=config))
            save(args.run_dir / 'environment.json', environment)
        save(args.run_dir / 'status.json', dict(status='running', protocol_signature=signature))
        log(args.run_dir, 'full_finetune_started', candidates=len(candidates), environment=environment,
            data=dict(train=113, dev=37, test=37, previously_exposed_test=True))
        try:
            if not (args.run_dir / 'selection_frozen.json').exists():
                completed = []
                for candidate in candidates:
                    path = args.run_dir / 'candidates' / candidate['id'] / 'candidate_complete.json'
                    if path.exists():
                        result = read(path)
                        for name, checksum in result['artifact_hashes'].items():
                            if sha(path.parent / name) != checksum:
                                raise ValueError('Completed candidate changed')
                    else:
                        result = candidate_train(args, candidate, baseline_head, td)
                    completed.append(result)
                best = choose(completed)
                selected = dict(**best, protocol_signature=signature, test_used_for_selection=False)
                save(args.run_dir / 'selection_frozen.json', selected)
                save(args.run_dir / 'selection_commit.json', dict(sha256=sha(args.run_dir / 'selection_frozen.json')))
                log(args.run_dir, 'selection_frozen_before_test', candidate=best['id'], epoch=best['best_epoch'],
                    dev_accuracy=best['dev_metrics']['label_wise_accuracy'], dev_macro_f1=best['dev_metrics']['macro_f1'])
            selected = read(args.run_dir / 'selection_frozen.json')
            if selected['protocol_signature'] != signature:
                raise ValueError('Selection protocol mismatch')
            summary = evaluate_pair(args, selected, baseline_head)
            common.bundle_data(args.bundle)
            assert_baseline(args.baseline)
            if common.model_packet(args.model_dir, 'siglip2') != packet:
                raise ValueError('Source weights changed during training')
            save(args.run_dir / 'comparison_summary.json', summary)
            save(args.run_dir / 'status.json', dict(status='complete', protocol_signature=signature,
                CUDA_executed=True, backbone_training_executed=True, OCR_executed=False))
            log(args.run_dir, 'full_finetune_complete', run=str(args.run_dir),
                accuracy=summary['results']['siglip2_full_finetune']['metrics']['label_wise_accuracy'],
                p95_ms=summary['results']['siglip2_full_finetune']['timing']['total_ms']['p95_ms'],
                goal_met=summary['goal_met'])
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(args.run_dir / 'status.json', dict(status='failed', protocol_signature=signature))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--lrs', nargs='+', type=float, default=[1e-5, 3e-5])
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--patience', type=int, default=3)
    parser.add_argument('--microbatch', type=int, default=2, choices=(1, 2, 4, 8))
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.epochs < 1 or args.patience < 1 or args.warmups < 1 or args.repeats < 1 or any(not math.isfinite(x) or x <= 0 for x in args.lrs):
        parser.error('Positive settings required')
    for name in ('bundle', 'baseline', 'model_dir', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    run(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
