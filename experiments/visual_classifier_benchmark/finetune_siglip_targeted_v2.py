"""Dev-loss monitored targeted tuning; preserve the completed AP-only v1 runner."""
from pathlib import Path
import argparse
import fcntl
import math
import os
import shutil
import time
import traceback

import finetune_siglip_targeted as v1
from benchlib import LABELS, SEED, contained, csv_rows, digest, log, metrics, read, save, sha, targets

HERE, SOURCE = v1.HERE, v1.SOURCE
require = v1.require


def sources():
    hashes = v1.sources()
    for name in ('finetune_siglip_targeted_v2.py', 'run_targeted_finetune_v2.sh'):
        require((HERE / name).is_file(), 'Missing new source: ' + name)
        hashes[name] = sha(HERE / name)
    return hashes


def development_losses(rows, probabilities, specialist):
    import numpy as np
    require(np.isfinite(probabilities).all(), 'Nonfinite development probabilities')
    y = targets(rows)
    known = np.isfinite(y)
    safe = np.nan_to_num(y, nan=0.).astype(np.float64)
    p = np.clip(probabilities.astype(np.float64), 1e-7, 1 - 1e-7)
    losses = -(safe * np.log(p) + (1 - safe) * np.log1p(-p))
    require(known.any() and known[:, v1.INSTRUMENT].any(), 'Known development labels required')
    overall = float(losses[known].mean())
    instrument = float(losses[:, v1.INSTRUMENT][known[:, v1.INSTRUMENT]].mean())
    return dict(dev_BCE=instrument if specialist else overall,
                dev_overall_BCE=overall, dev_instrument_BCE=instrument)


def movement(probabilities, initial):
    import numpy as np
    delta = np.abs(probabilities - initial)
    return dict(max_score_change=float(delta.max()), mean_score_change=float(delta.mean()),
                vehicle_max_score_change=float(delta[:, LABELS.index('vehicle')].max()),
                instrument_max_score_change=float(delta[:, v1.INSTRUMENT].max()))


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
        require(cached['protocol_signature'] == args.signature and cached['training_rows_sha256'] == digest(rows)
                and all(cached.get(k) == value for k, value in spec.items()), 'Cached v2 candidate differs')
        focused.verify_artifacts(args.run_dir, cached)
        log(args.run_dir, 'targeted_v2_candidate_reused', candidate=spec['id'])
        return cached
    folder.mkdir(parents=True, exist_ok=True)
    train, dev = [r for r in rows if r['split'] == 'train'], [r for r in rows if r['split'] == 'dev']
    require(train and dev and len(train) + len(dev) == len(rows), 'Train/dev-only packet required')
    v1.seed()
    base, original_head = load_candidate(args, reference, baseline_head)
    before = ft.state(base, original_head)
    initial_groups = ft.group_hashes(base, original_head)
    teacher = load_candidate(args, reference, baseline_head) if spec['preserve_weight'] else None
    v1.seed()
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
    require(np.allclose(initial, expected, rtol=1e-5, atol=1e-6), 'V2 initialization does not reproduce the reference')
    ft.common.save_array(folder / 'initial_dev_probabilities.npy', initial)
    initial_losses = development_losses(dev, initial, specialist)
    initial_metrics = metrics(targets(dev), initial, [.5] * 8)
    initial_ap = initial_metrics['per_label']['musical_instrument']['average_precision'] if specialist else initial_metrics['macro_average_precision']
    save(folder / 'initial_dev_metrics.json', dict(**initial_losses, metrics=initial_metrics, epoch=0))
    checkpoint_path = folder / ('instrument.safetensors' if specialist else 'adapter_best.safetensors')

    def save_checkpoint():
        ft.save_weights(checkpoint_path, encoder.extra_state() if specialist else ft.state(encoder, head))

    # Epoch zero is an explicit fallback, not an implicitly trained result.
    save_checkpoint()
    best_loss, best_ap, best_epoch = initial_losses['dev_BCE'], initial_ap, 0
    watch_loss, bad_epochs, history, gradient_audit = best_loss, 0, [], None
    stop_reason = 'max_epochs'
    trainable_names = [prefix + '.' + name for prefix, module in (('encoder', encoder), ('head', head))
                       for name, parameter in module.named_parameters() if parameter.requires_grad]
    v1.seed()
    for epoch in range(1, args.epochs + 1):
        started = time.perf_counter()
        trained = v1.train_epoch(args, spec, encoder, head, optimizer, train, epoch, teacher)
        audit = trained.pop('gradient_audit')
        gradient_audit = gradient_audit or audit
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        losses = development_losses(dev, probabilities, specialist)
        scored = metrics(targets(dev), probabilities, [.5] * 8)
        ap = scored['per_label']['musical_instrument']['average_precision'] if specialist else scored['macro_average_precision']
        improved = losses['dev_BCE'] < best_loss - args.checkpoint_min_delta
        tied_better_ap = abs(losses['dev_BCE'] - best_loss) <= args.checkpoint_min_delta and ap > best_ap + 1e-12
        checkpoint_updated = improved or tied_better_ap
        if checkpoint_updated:
            best_loss, best_ap, best_epoch = losses['dev_BCE'], ap, epoch
            save_checkpoint()
        if losses['dev_BCE'] < watch_loss - args.min_delta:
            watch_loss, bad_epochs = losses['dev_BCE'], 0
        else:
            bad_epochs += 1
        early_stop = epoch >= args.min_epochs and bad_epochs >= args.patience
        history.append(dict(epoch=epoch, best_epoch=best_epoch, **trained, **losses,
            train_weighted_preserve_kl=spec['preserve_weight'] * trained['train_preserve_kl'],
            dev_macro_AP=scored['macro_average_precision'], dev_instrument_AP=scored['per_label']['musical_instrument']['average_precision'],
            checkpoint_updated=checkpoint_updated,
            checkpoint_reason='lower_dev_BCE' if improved else ('tied_BCE_higher_AP' if tied_better_ap else 'not_better'),
            best_dev_BCE=best_loss, monitor_best_dev_BCE=watch_loss, bad_epochs=bad_epochs,
            early_stop_allowed=epoch >= args.min_epochs, early_stop=early_stop,
            **movement(probabilities, initial), elapsed_seconds=time.perf_counter() - started))
        save(folder / 'history.json', history)
        log(args.run_dir, 'targeted_v2_epoch', candidate=spec['id'], **history[-1])
        if early_stop:
            stop_reason = 'dev_BCE_plateau_after_min_epochs'
            break
    require(len(history) >= args.min_epochs, 'Minimum epoch budget was not reached')
    ft.common.save_array(folder / 'last_dev_probabilities.npy', probabilities)
    last_movement = movement(probabilities, initial)
    last_lora_B_norm = sum(float(p.detach().norm().cpu()) for name, p in encoder.named_parameters() if name.endswith('lora_B'))
    selected_source = 'initialization' if best_epoch == 0 else 'trained'
    merge_audit = None
    if specialist:
        last_probe_changed = not torch.equal(encoder.pooler.probe.detach().cpu(), base.backbone.head.probe.detach().cpu())
        last_classifier_changed = not torch.equal(encoder.classifier.weight.detach().cpu(), original_head.weight[v1.INSTRUMENT:v1.INSTRUMENT + 1].detach().cpu())
        require(last_probe_changed or last_classifier_changed, 'Instrument training never updated its parameters')
        encoder.load_extra(load_file(str(checkpoint_path)))
        require(ft.group_hashes(base, original_head) == initial_groups, 'Instrument training modified the frozen original model')
        frozen_pooler = base.backbone.head.state_dict()
        require(all(torch.equal(p.detach().cpu(), frozen_pooler[name].detach().cpu()) for name, p in encoder.pooler.state_dict().items() if name != 'probe'),
                'A frozen instrument-pooler parameter changed')
        shutil.copyfile(contained(args.run_dir, reference['checkpoint']), folder / 'best.safetensors.tmp')
        (folder / 'best.safetensors.tmp').replace(folder / 'best.safetensors')
    else:
        require(last_lora_B_norm > 0., 'LoRA training never updated B')
        ft.load_state(encoder, head, load_file(str(checkpoint_path)))
        unmerged = ft.predict_dev(encoder, head, args.packet, dev)
        merged = merge_lora(encoder)
        require(set(merged) == set(lora_paths), 'Unexpected LoRA merge population')
        merged_probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        require(np.allclose(merged_probabilities, unmerged, rtol=1e-4, atol=1e-5), 'Merged LoRA probabilities differ materially')
        after = ft.state(encoder, head)
        allowed = {'backbone.' + path + '.weight' for path in lora_paths} | {'classifier.weight', 'classifier.bias'}
        require(set(after) == set(before), 'Merged checkpoint is incompatible with the native model')
        changed = [name for name in before if not torch.equal(before[name], after[name])]
        require(set(changed) <= allowed, 'LoRA updated an unexpected native parameter')
        require(best_epoch == 0 or any(name.startswith('backbone.') for name in changed), 'Selected trained LoRA has no backbone update')
        ft.save_weights(folder / 'best.safetensors', after)
        ft.common.save_array(folder / 'dev_probabilities_unmerged.npy', unmerged)
        merge_audit = dict(merged_paths=merged, changed_native_parameters=changed,
            max_probability_difference=float(np.max(np.abs(merged_probabilities - unmerged))), adapters_remaining=False)
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    if specialist:
        require(np.allclose(probabilities[:, v1.OTHERS], expected[:, v1.OTHERS], rtol=1e-5, atol=1e-6), 'Another dev label score changed')
    cutoffs, threshold_audit = v1.calibrate(dev, probabilities, reference, specialist)
    if specialist:
        require(np.array_equal(probabilities[:, v1.OTHERS] >= np.asarray(cutoffs)[v1.OTHERS],
                               expected[:, v1.OTHERS] >= np.asarray(reference['thresholds'])[v1.OTHERS]), 'Another dev label decision changed')
    score = metrics(targets(dev), probabilities, cutoffs)
    selected_losses = development_losses(dev, probabilities, specialist)
    ft.common.save_array(folder / 'dev_probabilities.npy', probabilities)
    csv_rows(folder / 'threshold_audit.csv', threshold_audit)
    save(folder / 'checkpoint_selection.json', dict(best_epoch=best_epoch, trained_epochs=len(history),
        selected_checkpoint_source=selected_source, checkpoint_monitor='dev_BCE_then_AP',
        initial_dev_BCE=initial_losses['dev_BCE'], selected_dev_BCE=selected_losses['dev_BCE'],
        selected_monitor_AP=best_ap, min_epochs=args.min_epochs, max_epochs=args.epochs,
        patience=args.patience, min_delta=args.min_delta, checkpoint_min_delta=args.checkpoint_min_delta,
        stop_reason=stop_reason, bad_epochs=bad_epochs,
        selected_probability_change=movement(probabilities, initial), last_probability_change=last_movement))
    save(folder / 'parameter_audit.json', dict(initial_native_groups=initial_groups, trainable_parameters=trainable_names,
        gradient_audit=gradient_audit, original_native_parameters_unchanged=specialist, merge=merge_audit,
        last_lora_B_norm=last_lora_B_norm, selected_checkpoint_source=selected_source,
        classifier_in_preservation_loss=bool(spec['preserve_weight']),
        teacher_target_labels=v1.OTHERS if spec['preserve_weight'] else [], unknown_teacher_cells_excluded=True,
        source_checkpoint_sha256=sha(contained(args.run_dir, reference['checkpoint']))))
    artifacts = ['best.safetensors', 'initial_dev_probabilities.npy', 'initial_dev_metrics.json', 'dev_probabilities.npy',
                 'last_dev_probabilities.npy', 'history.json', 'threshold_audit.csv', 'checkpoint_selection.json', 'parameter_audit.json']
    artifacts += ['instrument.safetensors'] if specialist else ['adapter_best.safetensors', 'dev_probabilities_unmerged.npy']
    result = dict(**spec, protocol_signature=args.signature, source=selected_source, training_rows_sha256=digest(rows),
        best_epoch=best_epoch, trained_epochs=len(history), selected_checkpoint_source=selected_source,
        selected_dev_BCE=selected_losses['dev_BCE'], stop_reason=stop_reason,
        thresholds=cutoffs, dev_metrics=score, dev_confusion=focused.confusion(targets(dev), probabilities, cutoffs),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        extra_checkpoint=str((folder / 'instrument.safetensors').relative_to(args.run_dir)) if specialist else None,
        backbone_training_executed=not specialist, lora_merged=not specialist,
        threshold_policy='unchanged v1 dev-only vehicle/instrument error-budget calibration',
        artifact_hashes={name: sha(folder / name) for name in artifacts})
    save(done, result)
    log(args.run_dir, 'targeted_v2_candidate_complete', candidate=spec['id'], best_epoch=best_epoch,
        trained_epochs=len(history), selected_checkpoint_source=selected_source, stop_reason=stop_reason,
        initial_dev_BCE=initial_losses['dev_BCE'], selected_dev_BCE=selected_losses['dev_BCE'])
    del base, original_head, encoder, head, optimizer, teacher, before
    ft.common.release()
    return result


def freeze(args, reference, candidates):
    models = dict(zip(v1.MODEL_NAMES, [reference, *candidates]))
    eligible = [(name, c) for name, c in models.items() if not v1.guard_reasons(c, reference)]
    selected_name, selected = max(eligible, key=lambda item: (
        item[1]['dev_metrics']['label_wise_accuracy'], -item[1]['dev_confusion']['musical_instrument']['fn'],
        item[1]['dev_metrics']['macro_f1'], item[1]['dev_metrics']['macro_average_precision'], item[0] == v1.CONTROL))
    reports, label_rows = [], []
    for name, candidate in models.items():
        reasons = v1.guard_reasons(candidate, reference)
        reports.append(dict(model=name, candidate=candidate['id'], family=candidate['family'],
            trained_epochs=candidate.get('trained_epochs', 0), best_epoch=candidate.get('best_epoch'),
            selected_checkpoint_source=candidate.get('selected_checkpoint_source', 'reference'),
            selected_dev_BCE=candidate.get('selected_dev_BCE'), stop_reason=candidate.get('stop_reason', 'reference'),
            dev_accuracy=candidate['dev_metrics']['label_wise_accuracy'], dev_macro_f1=candidate['dev_metrics']['macro_f1'],
            dev_macro_AP=candidate['dev_metrics']['macro_average_precision'], instrument_fp=candidate['dev_confusion']['musical_instrument']['fp'],
            instrument_fn=candidate['dev_confusion']['musical_instrument']['fn'], vehicle_fp=candidate['dev_confusion']['vehicle']['fp'],
            vehicle_fn=candidate['dev_confusion']['vehicle']['fn'], eligible=not reasons, rejection_reason=';'.join(reasons), selected=name == selected_name))
        for j, label in enumerate(LABELS):
            old, new = reference['dev_confusion'][label], candidate['dev_confusion'][label]
            label_rows.append(dict(model=name, label=label, threshold=candidate['thresholds'][j], **new,
                fp_delta=new['fp'] - old['fp'], fn_delta=new['fn'] - old['fn'], AP=candidate['dev_metrics']['per_label'][label]['average_precision']))
    csv_rows(args.run_dir / 'dev_candidates.csv', reports)
    csv_rows(args.run_dir / 'dev_per_label.csv', label_rows)
    metadata = {str(Path(c['checkpoint']).parent / 'candidate_complete.json'):
                sha(args.run_dir / Path(c['checkpoint']).parent / 'candidate_complete.json') for c in models.values()}
    save(args.run_dir / 'selection_frozen.json', dict(protocol_signature=args.signature, models=models, selected_model=selected_name,
        selected_candidate=selected['id'], test_used_for_selection=False, previously_exposed_test=True, candidate_records=metadata,
        report_hashes={name: sha(args.run_dir / name) for name in ('dev_candidates.csv', 'dev_per_label.csv')}))
    save(args.run_dir / 'selection_commit.json', dict(sha256=sha(args.run_dir / 'selection_frozen.json')))
    log(args.run_dir, 'targeted_v2_selection_frozen_before_test', selected_model=selected_name)


def run(args):
    source_hashes = sources()
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    import finetune_siglip_corrected as corrected
    from prepare_partial_data import validate_packet
    for root in (args.bundle, args.baseline, args.prior_run, args.previous_run, args.packet, args.model_dir):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir), 'Use a separate new run directory')
    require(args.run_dir != (HERE / 'runs/siglip-targeted-v1-fp32').resolve(), 'Keep the completed v1 run; use a new v2 directory')
    args.device = 'cuda:0'
    manifest, rows = validate_packet(args.packet, args.bundle)
    require(manifest['added_images'] + manifest['changed_train_cells'] > 0, 'Use the approved corrected packet')
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
    config = dict(schema='siglip-targeted-v2-dev-loss-monitor', environment=environment, model_packet=model_packet,
        script_hashes=source_hashes, input_hashes=fingerprints, candidates=specs, seed=SEED,
        precision='fp32', image_size=384, max_epochs=args.epochs, min_epochs=args.min_epochs, patience=args.patience,
        min_delta=args.min_delta, checkpoint_min_delta=args.checkpoint_min_delta, microbatch=args.microbatch, effective_batch=8,
        lora_rank=args.lora_rank, lora_alpha=args.lora_alpha, tail_blocks=args.tail_blocks, lora_lr=args.lora_lr,
        head_lr=args.head_lr, probe_lr=args.probe_lr, preserve_weight=args.preserve_weight, weight_decay=1e-4, gradient_clip=1.,
        checkpoint='minimum masked dev BCE; instrument-only BCE for probe, all known dev cells for LoRA; AP breaks numeric BCE ties; epoch0 explicit fallback',
        stopping='dev BCE fails to improve by min_delta for patience epochs, only after min_epochs; AP does not control stopping',
        dev_BCE='FP64 log of clipped FP32 probabilities in [1e-7,1-1e-7]; unweighted known cells, no augmentation, no teacher loss',
        initialization='same previous dev-selected corrected partial as v1, never v1 test-selected candidate',
        training='unchanged v1 train_epoch, optimizers, losses, augmentation, LoRA/probe and teacher settings',
        calibration='unchanged v1 dev-only instrument/vehicle FP/FN budgets',
        selection='unchanged v1 dev accuracy/F1 and per-label error guards; reference fallback',
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
                test_images_used_in_training=0, approval_note='Original approval provenance copied; no independent human reannotation claim.'))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            v1.frozen(args)
            require(status['protocol_signature'] == args.signature and all(sha(contained(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                    'Completed v2 experiment changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature))
        try:
            if not (args.run_dir / 'selection_frozen.json').exists():
                reference = corrected.reference(args, 'reference-partial', args.previous_run, previous, baseline_head, rows)
                candidates = [train_candidate(args, spec, rows, reference, baseline_head) for spec in specs]
                freeze(args, reference, candidates)
            summary = v1.evaluate(args, baseline_head)
            validate_packet(args.packet, args.bundle)
            require(corrected.input_hashes(args) == fingerprints and sources() == source_hashes and
                    ft.common.model_packet(args.model_dir, 'siglip2') == model_packet, 'Inputs or source changed during execution')
            excluded = {'.lock', 'status.json', 'failure.json', 'events.jsonl'}
            hashes = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                      if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature, CUDA_executed=True,
                backbone_training_executed=True, head_training_executed=True, OCR_executed=False, model_deployed=False, artifact_hashes=hashes))
            log(args.run_dir, 'targeted_v2_complete', selected_model=summary['selected_model'], goal_met=summary['goal_met'], run=str(args.run_dir))
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
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-targeted-v2-fp32')
    parser.add_argument('--epochs', type=int, default=16)
    parser.add_argument('--min-epochs', type=int, default=8)
    parser.add_argument('--patience', type=int, default=4)
    parser.add_argument('--min-delta', type=float, default=1e-5)
    parser.add_argument('--checkpoint-min-delta', type=float, default=1e-7)
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
    require(args.epochs >= args.min_epochs >= 1 and min(args.patience, args.warmups, args.repeats, args.lora_rank) >= 1,
            'Use max_epochs >= min_epochs >= 1 and positive counts')
    require(all(math.isfinite(value) and value > 0 for value in
                (args.min_delta, args.checkpoint_min_delta, args.lora_alpha, args.lora_lr, args.head_lr, args.probe_lr, args.preserve_weight)),
            'Use positive finite deltas, learning rates and preservation weight')
    run(args)


if __name__ == '__main__':
    main()
