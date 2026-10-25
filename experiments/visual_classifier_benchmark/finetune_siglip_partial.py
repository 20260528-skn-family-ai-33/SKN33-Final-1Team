"""Tune only the SigLIP2 head or last vision blocks on approved training packets."""
from pathlib import Path
import argparse
import fcntl
import math
import os
import random
import time
import traceback

import numpy as np
import torch
from torch import nn

import finetune_siglip as ft
import finetune_siglip_focused as focused
from prepare_partial_data import validate_packet, require
from finetune_siglip import common, HERE
from benchlib import LABELS, SEED, contained, csv_rows, digest, load_test, log, metrics, read, save, sha, targets, thresholds


def group_name(name):
    return '.'.join(name.split('.')[:3]) if name.startswith('encoder.layers.') else name.split('.')[0]


class PartialEncoder(nn.Module):
    def __init__(self, encoder, head, tail_blocks):
        super().__init__()
        self.base_encoder = encoder
        blocks = self.backbone.encoder.layers
        require(0 <= tail_blocks <= len(blocks), 'Requested tail blocks exceed this vision tower')
        self.tail_blocks = tail_blocks
        self.backbone.requires_grad_(False)
        if tail_blocks:
            for block in list(blocks)[-tail_blocks:]:
                block.requires_grad_(True)
            self.backbone.post_layernorm.requires_grad_(True)
            self.backbone.head.requires_grad_(True)
        head.requires_grad_(True)

    @property
    def backbone(self):
        return self.base_encoder.backbone

    @property
    def device(self):
        return self.base_encoder.device

    @property
    def size(self):
        return self.base_encoder.size

    @property
    def mean(self):
        return self.base_encoder.mean

    @property
    def std(self):
        return self.base_encoder.std

    def prepare(self, path):
        return self.base_encoder.prepare(path)

    def forward(self, x):
        return self.base_encoder(x)

    def train(self, mode=True):
        super().train(mode)
        # Frozen blocks stay in eval mode, even when the train loop calls train().
        self.backbone.eval()
        if mode and self.tail_blocks:
            for block in list(self.backbone.encoder.layers)[-self.tail_blocks:]:
                block.train()
            self.backbone.post_layernorm.train(); self.backbone.head.train()
        return self


def training_epoch(encoder, head, optimizer, root, rows, microbatch, epoch):
    require(bool(rows) and all(r['split'] == 'train' for r in rows), 'Only TRAIN rows may enter optimization')
    encoder.train(); head.train()
    order = list(range(len(rows))); random.Random(SEED + epoch).shuffle(order)
    total, total_known, steps, audit = 0., 0, 0, None
    params = [p for p in (*encoder.parameters(), *head.parameters()) if p.requires_grad]
    for offset in range(0, len(rows), 8):
        group = [rows[i] for i in order[offset:offset + 8]]
        known = int(np.isfinite(targets(group)).sum())
        require(known > 0, 'Accumulation group has no known labels')
        optimizer.zero_grad(set_to_none=True)
        for start in range(0, len(group), microbatch):
            batch = group[start:start + microbatch]
            x = torch.cat([ft.prepare_train(encoder, root, r, True) for r in batch]).to(encoder.device)
            y = torch.tensor(targets(batch), device=encoder.device)
            numerator, n = ft.masked_loss(head(encoder(x)), y, torch.ones(len(LABELS), device=encoder.device))
            loss = numerator / known
            require(bool(torch.isfinite(loss)), 'Nonfinite partial training loss')
            loss.backward(); total += float(numerator.detach().cpu()); total_known += n
        if audit is None:
            audit = {}
            for name, parameter in list(encoder.backbone.named_parameters()) + [('classifier.' + k, p) for k, p in head.named_parameters()]:
                if not parameter.requires_grad:
                    require(parameter.grad is None, 'Frozen parameter received gradient: ' + name)
                    continue
                require(parameter.grad is not None and bool(torch.isfinite(parameter.grad).all()), 'Missing/nonfinite trainable gradient: ' + name)
                key = group_name(name)
                audit[key] = audit.get(key, 0.) + float(parameter.grad.float().norm().cpu())
            require(bool(audit) and all(v > 0 and math.isfinite(v) for v in audit.values()), 'Partial gradient audit failed')
        torch.nn.utils.clip_grad_norm_(params, 1., error_if_nonfinite=True)
        optimizer.step(); steps += 1
    return dict(loss=total / total_known, known_cells=total_known, optimizer_steps=steps, gradient_audit=audit)


def candidate_train(args, spec, head_selection, reference, rows, loader=ft.build_models):
    from safetensors.torch import load_file
    train = [r for r in rows if r['split'] == 'train']; dev = [r for r in rows if r['split'] == 'dev']
    require(len(train) > 0 and len(dev) > 0 and len(train) + len(dev) == len(rows), 'Train/dev-only packet required')
    folder = args.run_dir / 'candidates' / spec['id']; folder.mkdir(parents=True, exist_ok=True)
    random.seed(SEED); torch.manual_seed(SEED)
    if str(args.device).startswith('cuda'):
        torch.cuda.manual_seed_all(SEED)
    base, head = loader(args.model_dir, args.device, head_selection)
    ft.load_state(base, head, load_file(str(contained(args.run_dir, reference['checkpoint']))))
    encoder = PartialEncoder(base, head, spec['tail_blocks'])
    initial = ft.group_hashes(encoder, head)
    trainable = {group_name(n) for n, p in encoder.backbone.named_parameters() if p.requires_grad} | {'classifier'}
    groups = [dict(params=head.parameters(), lr=1e-4)]
    body = [p for p in encoder.parameters() if p.requires_grad]
    if body:
        groups.insert(0, dict(params=body, lr=spec['backbone_lr']))
    optimizer = torch.optim.AdamW(groups, weight_decay=1e-4, foreach=False)
    best_ap, best_epoch, stale, history, audit = -1., None, 0, [], None
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = training_epoch(encoder, head, optimizer, args.packet, train, args.microbatch, epoch)
        audit = audit or trained['gradient_audit']
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        ap = metrics(targets(dev), probabilities, [.5] * 8)['macro_average_precision']
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            ft.save_weights(folder / 'best.safetensors', ft.state(encoder, head))
        else:
            stale += 1
        history.append(dict(epoch=epoch, train_loss=trained['loss'], known_cells=trained['known_cells'],
                            optimizer_steps=trained['optimizer_steps'], dev_macro_AP=ap, elapsed_seconds=time.perf_counter() - started))
        save(folder / 'history.json', history)
        log(args.run_dir, 'partial_epoch', candidate=spec['id'], best_epoch=best_epoch, **history[-1])
        if stale >= args.patience:
            break
    ft.load_state(encoder, head, load_file(str(folder / 'best.safetensors')))
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    cutoffs = thresholds(targets(dev), probabilities)
    score = metrics(targets(dev), probabilities, cutoffs)
    final = ft.group_hashes(encoder, head)
    changed = {k: initial[k] != final[k] for k in initial}
    require(all(changed[k] == (k in trainable) for k in changed), 'Frozen/trainable group update invariant failed')
    common.save_array(folder / 'dev_probabilities.npy', probabilities)
    result = dict(**spec, source='trained', threshold_policy='dev_F1', thresholds=cutoffs, best_epoch=best_epoch,
        dev_metrics=score, dev_confusion=focused.confusion(targets(dev), probabilities, cutoffs),
        trained_epochs=len(history), gradient_audit=audit, trainable_groups=sorted(trainable), parameter_groups_changed=changed,
        trainable_backbone_parameters=sum(p.numel() for p in encoder.parameters() if p.requires_grad),
        frozen_backbone_parameters=sum(p.numel() for p in encoder.parameters() if not p.requires_grad),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        artifact_hashes={n: sha(folder / n) for n in ('best.safetensors', 'history.json', 'dev_probabilities.npy')})
    save(folder / 'candidate_complete.json', result)
    del base, encoder, head, optimizer
    common.release()
    return result


def evaluate(args, selected, reference, head_selection):
    from encoders import load_encoder, make_head
    from safetensors.torch import load_file
    ft.validate_checkpoint(args.run_dir, selected)
    selection_path = args.run_dir / 'selection_frozen.json'; selection_sha = sha(selection_path)
    rows = load_test(args.bundle, selection_path, selection_sha)
    results = {}
    for name, chosen in (('siglip2_full_finetune', reference), ('siglip2_partial', selected)):
        focused.verify_artifacts(args.run_dir, chosen)
        folder = args.run_dir / name; folder.mkdir(exist_ok=True)
        done = folder / 'complete.json'
        if done.exists():
            cached = read(done)
            require(cached['selection_sha256'] == selection_sha, 'Cached evaluation selection changed')
            require(all(sha(folder / n) == h for n, h in cached['artifact_hashes'].items()), 'Cached evaluation artifact changed')
            results[name] = cached['result']; continue
        encoder = load_encoder('siglip2', args.model_dir, args.device, 'fp32', 384)
        head = make_head(head_selection, args.device)
        ft.load_state(encoder, head, load_file(str(contained(args.run_dir, chosen['checkpoint']))))
        encoder.eval(); head.eval()
        result = common.benchmark(encoder, head, args.bundle, rows, np.asarray(chosen['thresholds']), folder, args.repeats, args.warmups)
        if name == 'siglip2_full_finetune':
            before = read(args.prior_run / 'siglip2_full_finetune/test_predictions.json')
            now = read(folder / 'test_predictions.json')
            require(len(now) == len(before) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels'] and a['predictions'] == b['predictions'] for a, b in zip(now, before)), 'Previous control decisions did not reproduce')
        save(done, dict(result=result, selection_sha256=selection_sha, artifact_hashes={n: sha(folder / n) for n in focused.ARTIFACTS}))
        results[name] = result
        del encoder, head
        common.release()
    csv_rows(args.run_dir / 'comparison.csv', [dict(model=k, accuracy=v['metrics']['label_wise_accuracy'],
        macro_f1=v['metrics']['macro_f1'], mean_ms=v['timing']['total_ms']['mean_ms'], median_ms=v['timing']['total_ms']['median_ms'],
        p95_ms=v['timing']['total_ms']['p95_ms'], accuracy_90=v['metrics']['label_wise_accuracy'] >= .9,
        p95_50=v['timing']['total_ms']['p95_ms'] <= 50) for k, v in results.items()])
    label_rows = []
    for label in LABELS:
        item = dict(label=label)
        for prefix, name in (('prior', 'siglip2_full_finetune'), ('partial', 'siglip2_partial')):
            predictions = read(args.run_dir / name / 'test_predictions.json')
            counts = focused.confusion(targets(predictions), np.asarray([r['probabilities'] for r in predictions]), reference['thresholds'] if prefix == 'prior' else selected['thresholds'])[label]
            item.update({prefix + '_' + k: v for k, v in counts.items()})
        item.update(fp_delta=item['partial_fp'] - item['prior_fp'], fn_delta=item['partial_fn'] - item['prior_fn'])
        label_rows.append(item)
    csv_rows(args.run_dir / 'per_label_comparison.csv', label_rows)
    new = results['siglip2_partial']
    summary = dict(results=results, selected_candidate=selected['id'], selected_source=selected['source'],
        selected_tail_blocks=selected.get('tail_blocks'), goal_met=new['metrics']['label_wise_accuracy'] >= .9 and new['timing']['total_ms']['p95_ms'] <= 50,
        previously_exposed_test=True, test_used_for_selection=False, model_deployed=False,
        note='Same exposed37 test; known282/unknown14. Decode/preprocess/transfer/vision/head only. No OCR/network/DB/model loading. Deployment model unchanged.')
    save(args.run_dir / 'comparison_summary.json', summary)
    return summary


def execute(args, rows, original_rows, head, prior, signature, trainer=candidate_train, evaluator=evaluate):
    reference = focused.prepare_reference(args, head, prior, original_rows)
    selection_path = args.run_dir / 'selection_frozen.json'
    if not selection_path.exists():
        completed = []
        for spec in args.candidates:
            done = args.run_dir / 'candidates' / spec['id'] / 'candidate_complete.json'
            if done.exists():
                result = read(done); focused.verify_artifacts(args.run_dir, result)
                require(all(result[k] == v for k, v in spec.items()), 'Candidate settings changed')
            else:
                result = trainer(args, spec, head, reference, rows)
            completed.append(result)
        chosen = focused.choose(completed, reference)
        save(selection_path, dict(**chosen, protocol_signature=signature, test_used_for_selection=False))
        save(args.run_dir / 'selection_commit.json', dict(sha256=sha(selection_path)))
        log(args.run_dir, 'partial_selection_frozen_before_test', candidate=chosen['id'], source=chosen['source'])
    selected = read(selection_path)
    require(selected['protocol_signature'] == signature, 'Selection protocol differs')
    ft.validate_checkpoint(args.run_dir, selected)
    return evaluator(args, selected, reference, head)


def run(args):
    for root in (args.bundle, args.baseline, args.prior_run, args.model_dir, args.packet):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir), 'Use a separate new run directory')
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    args.device = 'cuda:0'
    _, _, original_rows = common.bundle_data(args.bundle)
    packet_manifest, rows = validate_packet(args.packet, args.bundle)
    require(args.allow_unchanged_data or packet_manifest['added_images'] + packet_manifest['changed_train_cells'] > 0,
            'No approved additions/corrections. Review data first; explicit --allow-unchanged-data enables a controlled ablation.')
    environment = common.cuda_environment('fp32')
    baseline, head, prior = focused.input_protocol(args)
    require(environment == read(args.baseline / 'environment.json') == read(args.prior_run / 'environment.json'), 'Reuse the unchanged baseline GPU/runtime')
    model_packet = common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == baseline['config']['models']['siglip2'], 'Source model bytes differ')
    args.candidates = [dict(id='head-only', tail_blocks=0, backbone_lr=0.)] if 0 in args.tail_blocks else []
    args.candidates += [dict(id=f'tail{tail}-lr{lr:.0e}', tail_blocks=tail, backbone_lr=lr) for tail in args.tail_blocks if tail for lr in args.lrs]
    config = dict(environment=environment, model_packet=model_packet, candidates=args.candidates,
        packet_manifest_sha256=sha(args.packet / 'packet_manifest.json'), base_manifest_sha256=sha(args.bundle / 'bundle_manifest.json'),
        baseline_protocol_sha256=sha(args.baseline / 'protocol.json'), prior_protocol_sha256=sha(args.prior_run / 'protocol.json'),
        prior_selection_sha256=sha(args.prior_run / 'selection_frozen.json'), max_epochs=args.epochs, patience=args.patience,
        microbatch=args.microbatch, effective_batch=8, head_lr=1e-4, weight_decay=1e-4, precision='fp32', seed=SEED,
        augmentation='unchanged 384px whole-poster letterbox; mild ColorJitter; no crops/oversampling/pseudo-labels',
        loss='unweighted masked BCE; all train rows once per epoch', checkpoint_selection='dev Macro AP; earliest tie',
        thresholds='per-label dev F1, step .05', selection='dev accuracy/F1/target-error guard against unchanged prior, then focused.choose; prior fallback',
        warmups=args.warmups, repeats=args.repeats, allow_unchanged_data=args.allow_unchanged_data,
        script_hashes={n: sha(HERE / n) for n in ('finetune_siglip_partial.py', 'prepare_partial_data.py', 'finetune_siglip.py', 'finetune_siglip_focused.py', 'run_experiment.py', 'encoders.py')})
    signature = digest(config); args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            old = read(protocol_path)
            require(args.resume and old['signature'] == signature == digest(old['config']), 'Existing run requires --resume with identical code/data/settings')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'New run directory must be empty')
            save(protocol_path, dict(signature=signature, config=config)); save(args.run_dir / 'environment.json', environment)
            save(args.run_dir / 'training_packet_manifest.json', packet_manifest)
            save(args.run_dir / 'training_packet_approvals.json', read(args.packet / 'approvals.json'))
            save(args.run_dir / 'training_data_summary.json', dict(added_images=packet_manifest['added_images'],
                 changed_train_cells=packet_manifest['changed_train_cells'], known_cells=packet_manifest['known_cells'],
                 train_images=sum(r['split'] == 'train' for r in rows), dev_images=sum(r['split'] == 'dev' for r in rows),
                 test_images_used_in_training=0))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            done = read(status_path)
            ft.validate_checkpoint(args.run_dir, read(args.run_dir / 'selection_frozen.json'))
            require(done['protocol_signature'] == signature and all(sha(args.run_dir / n) == h for n, h in done['artifact_hashes'].items()), 'Completed run changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir)); return
        save(status_path, dict(status='running', protocol_signature=signature))
        try:
            summary = execute(args, rows, original_rows, head, prior, signature)
            validate_packet(args.packet, args.bundle); focused.input_protocol(args)
            require(sha(args.packet / 'packet_manifest.json') == config['packet_manifest_sha256'] and common.model_packet(args.model_dir, 'siglip2') == model_packet,
                    'Training inputs changed during run')
            for key, path in (('baseline_protocol_sha256', args.baseline / 'protocol.json'),
                              ('prior_protocol_sha256', args.prior_run / 'protocol.json'),
                              ('prior_selection_sha256', args.prior_run / 'selection_frozen.json')):
                require(sha(path) == config[key], 'Reference input changed during run')
            require(all(sha(HERE / n) == h for n, h in config['script_hashes'].items()), 'Source code changed during run')
            ft.validate_checkpoint(args.run_dir, read(args.run_dir / 'selection_frozen.json'))
            files = ['comparison.csv', 'comparison_summary.json', 'per_label_comparison.csv',
                     'training_packet_manifest.json', 'training_packet_approvals.json', 'training_data_summary.json']
            files += [f'{name}/{file}' for name in ('siglip2_full_finetune', 'siglip2_partial') for file in (*focused.ARTIFACTS, 'complete.json')]
            save(status_path, dict(status='complete', protocol_signature=signature, CUDA_executed=True,
                selected_source=summary['selected_source'], model_deployed=False, artifact_hashes={n: sha(args.run_dir / n) for n in files}))
            log(args.run_dir, 'partial_complete', selected=summary['selected_candidate'], goal_met=summary['goal_met'])
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=signature)); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-partial-fp32')
    parser.add_argument('--tail-blocks', type=int, nargs='+', default=[0, 2, 4])
    parser.add_argument('--lrs', type=float, nargs='+', default=[1e-6, 3e-6])
    parser.add_argument('--epochs', type=int, default=8)
    parser.add_argument('--patience', type=int, default=2)
    parser.add_argument('--microbatch', type=int, choices=(1, 2, 4, 8), default=2)
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--allow-unchanged-data', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    require(min(args.epochs, args.patience, args.warmups, args.repeats) > 0 and all(0 <= n <= 12 for n in args.tail_blocks)
            and len(set(args.tail_blocks)) == len(args.tail_blocks) and all(math.isfinite(v) and v > 0 for v in args.lrs), 'Invalid training settings')
    require(len({f'{v:.0e}' for v in args.lrs}) == len(args.lrs), 'Learning-rate identifiers must be unique')
    for name in ('bundle', 'baseline', 'prior_run', 'model_dir', 'packet', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    run(args)


if __name__ == '__main__':
    main()
