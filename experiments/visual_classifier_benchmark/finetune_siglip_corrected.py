"""Corrected-label head refits and paired full/L2-SP SigLIP2 experiments."""
from pathlib import Path
import argparse
import fcntl
import json
import math
import os
import shutil
import sys
import traceback

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'poster_benchmark'
if not (SOURCE / 'benchlib.py').is_file():
    raise SystemExit(f'Missing project file: {SOURCE / "benchlib.py"}. Restore the existing benchmark sources; do not reinstall packages.')
sys.path.insert(0, str(SOURCE))
from benchlib import LABELS, SEED, contained, csv_rows, digest, log, read, save, sha, targets

CODE = (
    'finetune_siglip_corrected.py', 'siglip_corrected_training.py',
    'siglip_corrected_results.py', 'run_corrected_finetune.sh',
    'prepare_partial_data.py', 'finetune_siglip.py', 'finetune_siglip_focused.py',
    'run_experiment.py', 'encoders.py',
)
MODEL_NAMES = (
    'siglip2_full_finetune', 'siglip2_previous_partial', 'siglip2_corrected_refit',
    'siglip2_corrected_pretrained_head', 'siglip2_corrected_full', 'siglip2_corrected_l2sp',
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_hashes():
    files = {name: HERE / name for name in CODE}
    files['../poster_benchmark/benchlib.py'] = SOURCE / 'benchlib.py'
    missing = [str(p) for p in files.values() if not p.is_file()]
    require(not missing, 'Missing project sources (no installation required): ' + ', '.join(missing))
    return {name: sha(p) for name, p in files.items()}


def previous_inputs(args, baseline, environment):
    """Read selection metadata and hash artifacts; do not parse test predictions."""
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    protocol = read(args.previous_run / 'protocol.json')
    status = read(args.previous_run / 'status.json')
    selection = read(args.previous_run / 'selection_frozen.json')
    require(status['status'] == 'complete', 'Finish the existing corrected-data partial run first')
    require(protocol['signature'] == digest(protocol['config']) == status['protocol_signature'] == selection['protocol_signature'],
            'Previous partial protocol/selection mismatch')
    contained(args.previous_run, selection['checkpoint'])
    ft.validate_checkpoint(args.previous_run, selection)
    focused.verify_artifacts(args.previous_run, selection)
    config = protocol['config']
    require(config['model_packet'] == baseline['config']['models']['siglip2'], 'Previous partial used different source weights')
    require(config['base_manifest_sha256'] == sha(args.bundle / 'bundle_manifest.json'), 'Previous partial used a different base split')
    require(config['baseline_protocol_sha256'] == sha(args.baseline / 'protocol.json') and
            config['prior_protocol_sha256'] == sha(args.prior_run / 'protocol.json') and
            config['prior_selection_sha256'] == sha(args.prior_run / 'selection_frozen.json'),
            'Previous partial reference chain changed')
    require(read(args.previous_run / 'environment.json') == environment == config['environment'],
            'Keep the same GPU and existing benchmark environment')
    for name, checksum in config['script_hashes'].items():
        require(sha(HERE / name) == checksum, 'Previous partial source changed: ' + name)
    require(bool(status.get('artifact_hashes')), 'Previous partial completion hashes missing')
    for name, checksum in status['artifact_hashes'].items():
        require(sha(contained(args.previous_run, name)) == checksum, 'Previous partial artifact changed: ' + name)
    return selection


def input_hashes(args):
    paths = {
        'packet_manifest': args.packet / 'packet_manifest.json',
        'base_manifest': args.bundle / 'bundle_manifest.json',
    }
    for name in ('baseline', 'prior_run', 'previous_run'):
        root = getattr(args, name)
        for filename in ('protocol.json', 'selection_frozen.json', 'selection_commit.json', 'status.json'):
            paths[name + '/' + filename] = root / filename
    return {name: sha(path) for name, path in paths.items()}


def reference(args, name, root, selected, baseline_head, rows):
    import numpy as np
    from safetensors.torch import load_file
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    from benchlib import metrics
    folder = args.run_dir / 'candidates' / name
    done = folder / 'candidate_complete.json'
    identity = dict(source_selection_sha256=sha(root / 'selection_frozen.json'),
                    source_checkpoint_sha256=sha(contained(root, selected['checkpoint'])))
    if done.exists():
        candidate = read(done)
        require(candidate['protocol_signature'] == args.signature and candidate['reference_identity'] == identity,
                'Cached reference differs: ' + name)
        focused.verify_artifacts(args.run_dir, candidate)
        return candidate
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / 'best.safetensors.tmp'
    shutil.copyfile(contained(root, selected['checkpoint']), temporary)
    temporary.replace(folder / 'best.safetensors')
    encoder, head = ft.build_models(args.model_dir, args.device, baseline_head)
    ft.load_state(encoder, head, load_file(str(folder / 'best.safetensors')))
    dev = [r for r in rows if r['split'] == 'dev']
    probabilities = ft.predict_dev(encoder, head, args.packet, dev)
    saved = np.load(contained(root, selected['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
    require(np.allclose(probabilities, saved, rtol=1e-5, atol=1e-6), 'Reference dev probabilities no longer reproduce: ' + name)
    ft.common.save_array(folder / 'dev_probabilities.npy', probabilities)
    candidate = dict(id=name, family='reference', source='prior', backbone_lr=0., l2sp=0.,
        protocol_signature=args.signature, threshold_policy='original', thresholds=selected['thresholds'],
        best_epoch=selected.get('best_epoch'), backbone_training_executed=False, head_training_executed=False,
        dev_metrics=metrics(targets(dev), probabilities, selected['thresholds']),
        dev_confusion=focused.confusion(targets(dev), probabilities, selected['thresholds']),
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)),
        reference_identity=identity,
        artifact_hashes={n: sha(folder / n) for n in ('best.safetensors', 'dev_probabilities.npy')})
    save(done, candidate)
    del encoder, head
    ft.common.release()
    return candidate


def candidate_key(candidate):
    import finetune_siglip_focused as focused
    score = candidate['dev_metrics']
    return (score['label_wise_accuracy'], -focused.focused_errors(candidate['dev_confusion']),
            score['macro_f1'], score['macro_average_precision'],
            candidate['source'] == 'prior', -candidate.get('l2sp', 0.))


def guard_reasons(candidate, reference_candidate):
    import finetune_siglip_focused as focused
    score, before = candidate['dev_metrics'], reference_candidate['dev_metrics']
    reasons = []
    if score['label_wise_accuracy'] + 1e-12 < before['label_wise_accuracy']:
        reasons.append('dev_accuracy_below_previous_partial')
    if score['macro_f1'] + 1e-12 < before['macro_f1']:
        reasons.append('dev_macro_f1_below_previous_partial')
    if focused.focused_errors(candidate['dev_confusion']) > focused.focused_errors(reference_candidate['dev_confusion']):
        reasons.append('dev_target_errors_above_previous_partial')
    return reasons


def freeze(args, candidates, models):
    import finetune_siglip_focused as focused
    reference_candidate = models['siglip2_previous_partial']
    # A method can be reported even if it fails promotion guards; no hidden fallback.
    eligible = [(name, c) for name, c in models.items() if not guard_reasons(c, reference_candidate)]
    selected_model, selected = max(eligible, key=lambda item: (
        candidate_key(item[1]), item[0] == 'siglip2_previous_partial'))
    rows, per_label = [], []
    reported_ids = {c['id'] for c in models.values()}
    for candidate in candidates:
        score, counts = candidate['dev_metrics'], candidate['dev_confusion']
        reasons = guard_reasons(candidate, reference_candidate)
        rows.append(dict(candidate=candidate['id'], family=candidate['family'], source=candidate['source'],
            l2sp=candidate.get('l2sp', 0.), best_epoch=candidate.get('best_epoch'),
            accuracy=score['label_wise_accuracy'], macro_f1=score['macro_f1'], macro_AP=score['macro_average_precision'],
            fp=sum(v['fp'] for v in counts.values()), fn=sum(v['fn'] for v in counts.values()),
            instrument_fn=counts['musical_instrument']['fn'], food_fp=counts['food_drink']['fp'],
            animal_fp=counts['animal']['fp'], plant_fp=counts['plant_flower']['fp'],
            target_errors=focused.focused_errors(counts), eligible=not reasons,
            rejection_reason=';'.join(reasons), reported_on_test=candidate['id'] in reported_ids,
            selected=candidate['id'] == selected['id']))
        for i, label in enumerate(LABELS):
            prior = reference_candidate['dev_confusion'][label]
            per_label.append(dict(candidate=candidate['id'], label=label,
                threshold=candidate['thresholds'][i], **counts[label],
                fp_delta=counts[label]['fp'] - prior['fp'], fn_delta=counts[label]['fn'] - prior['fn'],
                AP=score['per_label'][label]['average_precision'],
                precision=score['per_label'][label]['precision'], recall=score['per_label'][label]['recall']))
    csv_rows(args.run_dir / 'dev_candidates.csv', rows)
    csv_rows(args.run_dir / 'dev_per_label.csv', per_label)
    frozen = dict(protocol_signature=args.signature, models=models, selected_model=selected_model,
        selected_candidate=selected['id'], test_used_for_selection=False, previously_exposed_test=True,
        candidate_records={str(Path(c['checkpoint']).parent / 'candidate_complete.json'):
                           sha(args.run_dir / Path(c['checkpoint']).parent / 'candidate_complete.json')
                           for c in candidates},
        report_hashes={name: sha(args.run_dir / name) for name in ('dev_candidates.csv', 'dev_per_label.csv')})
    save(args.run_dir / 'selection_frozen.json', frozen)
    save(args.run_dir / 'selection_commit.json', dict(sha256=sha(args.run_dir / 'selection_frozen.json')))
    log(args.run_dir, 'corrected_selection_frozen_before_test', model=selected_model, candidate=selected['id'])
    return frozen


def verify_frozen(args):
    import finetune_siglip_focused as focused
    path = args.run_dir / 'selection_frozen.json'
    frozen = read(path)
    require(read(args.run_dir / 'selection_commit.json')['sha256'] == sha(path), 'Selection changed')
    require(frozen['protocol_signature'] == args.signature and set(frozen['models']) == set(MODEL_NAMES) and
            frozen['selected_model'] in frozen['models'] and frozen['test_used_for_selection'] is False,
            'Frozen experiment population/protocol changed')
    for candidate in frozen['models'].values():
        require(candidate['protocol_signature'] == args.signature, 'Candidate protocol mismatch')
        focused.verify_artifacts(args.run_dir, candidate)
    for name, checksum in frozen['candidate_records'].items():
        record = contained(args.run_dir, name)
        require(sha(record) == checksum, 'Frozen candidate metadata changed: ' + name)
        candidate = read(record)
        require(candidate['protocol_signature'] == args.signature, 'Candidate record protocol mismatch')
        focused.verify_artifacts(args.run_dir, candidate)
    for name, checksum in frozen['report_hashes'].items():
        require(sha(contained(args.run_dir, name)) == checksum, 'Development report changed')
    return frozen


def execute(args, rows, baseline_head, prior, previous):
    from siglip_corrected_training import fit_refit, train_full
    from siglip_corrected_results import evaluate
    if not (args.run_dir / 'selection_frozen.json').exists():
        original = reference(args, 'reference-full', args.prior_run, prior, baseline_head, rows)
        current = reference(args, 'reference-partial', args.previous_run, previous, baseline_head, rows)
        refit = fit_refit(args, dict(id='corrected-refit', family='refit'), rows, baseline_head,
                          source_checkpoint=contained(args.run_dir, original['checkpoint']))
        pretrained_head = fit_refit(args, dict(id='corrected-pretrained-head', family='pretrained_head'), rows, baseline_head)
        trained = [train_full(args, spec, rows, pretrained_head) for spec in args.candidates]
        full = next(c for c in trained if c['l2sp'] == 0.)
        anchored = max((c for c in trained if c['l2sp'] > 0.), key=candidate_key)
        models = dict(zip(MODEL_NAMES, (original, current, refit, pretrained_head, full, anchored)))
        freeze(args, [original, current, refit, pretrained_head, *trained], models)
    frozen = verify_frozen(args)
    return evaluate(args, frozen['models'], baseline_head, sha(args.run_dir / 'selection_frozen.json'))


def run(args):
    # Resolve source completeness before importing torch/sklearn; nothing is installed.
    sources = source_hashes()
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    from prepare_partial_data import validate_packet
    common = ft.common
    for root in (args.bundle, args.baseline, args.prior_run, args.previous_run, args.model_dir, args.packet):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir),
                'Choose a separate new run directory outside all input folders')
    require(args.previous_run != args.prior_run, 'Provide the completed corrected-data partial run separately')
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    args.device, args.seed = 'cuda:0', SEED
    manifest, rows = validate_packet(args.packet, args.bundle)
    require(manifest['added_images'] + manifest['changed_train_cells'] > 0,
            'The approved corrected training packet is required; do not use an unchanged-data bypass')
    environment = common.cuda_environment('fp32')
    baseline, baseline_head, prior = focused.input_protocol(args)
    require(environment == read(args.baseline / 'environment.json') == read(args.prior_run / 'environment.json'),
            'Use the unchanged baseline GPU/runtime; no reinstall is needed')
    model_packet = common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == baseline['config']['models']['siglip2'], 'Downloaded source model bytes changed')
    previous = previous_inputs(args, baseline, environment)
    args.candidates = [dict(id='corrected-full', family='full', backbone_lr=args.backbone_lr,
                            positive_weight='train_balanced', l2sp=0.)]
    args.candidates += [dict(id=f'corrected-l2sp-{value:g}', family='l2sp', backbone_lr=args.backbone_lr,
                             positive_weight='train_balanced', l2sp=value) for value in args.l2sp_strengths]
    fingerprints = input_hashes(args)
    config = dict(schema='siglip-corrected-comparison-v1', environment=environment, model_packet=model_packet,
        input_hashes=fingerprints, script_hashes=sources, candidates=args.candidates, cs=args.cs,
        seed=SEED, precision='fp32', image_size=384, microbatch=args.microbatch, effective_batch=8,
        max_epochs=args.epochs, patience=args.patience, head_lr=.001, weight_decay=.0001, gradient_clip=1.,
        learning_rate_schedule='constant, matching original full fine-tuning',
        augmentation='whole-poster letterbox384 and original mild ColorJitter; no crops/oversampling',
        head_refit='train-only logistic regression, unknown masked; C/class_weight by dev F1 then AP; FP32 replay',
        loss='masked BCE with negative/positive counts from corrected TRAIN; same weights in full and L2-SP',
        anchor='lambda/2 * sum of squared distance to original pretrained backbone; classifier excluded; once per optimizer step',
        checkpoint_selection='dev Macro AP; earliest tie; per-label dev F1 thresholds after epoch selection',
        l2sp_selection='dev accuracy, fewer target errors, macro F1, macro AP, lower lambda',
        overall_selection='dev accuracy/F1/target-error guards against previous partial; dev ranking; previous fallback',
        target_errors='instrument FN + food/animal/plant FP; per-label changes also reported',
        warmups=args.warmups, repeats=args.repeats, previously_exposed_test=True,
        test_used_for_selection=False, target_accuracy=.9, target_p95_ms=50.)
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            saved = read(protocol_path)
            require(args.resume and saved['signature'] == args.signature == digest(saved['config']),
                    'Existing run requires --resume with identical sources, inputs and settings')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'New run directory must be empty')
            save(protocol_path, dict(signature=args.signature, config=config))
            save(args.run_dir / 'environment.json', environment)
            save(args.run_dir / 'training_packet_manifest.json', manifest)
            save(args.run_dir / 'training_packet_approvals.json', read(args.packet / 'approvals.json'))
            save(args.run_dir / 'training_data_summary.json', dict(added_images=manifest['added_images'],
                changed_train_cells=manifest['changed_train_cells'], known_cells=manifest['known_cells'],
                train_images=sum(r['split'] == 'train' for r in rows), dev_images=sum(r['split'] == 'dev' for r in rows),
                test_images_used_in_training=0, independent_human_reannotation_claimed=False,
                approval_note='Approval provenance is copied verbatim; approvals do not imply independent human reannotation.'))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            done = read(status_path)
            verify_frozen(args)
            require(done['protocol_signature'] == args.signature and all(
                sha(contained(args.run_dir, name)) == checksum for name, checksum in done['artifact_hashes'].items()),
                'Completed experiment artifact changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature))
        try:
            log(args.run_dir, 'corrected_comparison_started', candidates=len(args.candidates),
                corrected_cells=manifest['changed_train_cells'], added_images=manifest['added_images'])
            summary = execute(args, rows, baseline_head, prior, previous)
            validate_packet(args.packet, args.bundle)
            require(input_hashes(args) == fingerprints and source_hashes() == sources and
                    common.model_packet(args.model_dir, 'siglip2') == model_packet, 'Inputs/source changed during execution')
            verify_frozen(args)
            excluded = {'status.json', 'failure.json', 'events.jsonl', '.lock'}
            hashes = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                      if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature,
                CUDA_executed=True, backbone_training_executed=True, head_training_executed=True,
                OCR_executed=False, model_deployed=False, artifact_hashes=hashes))
            log(args.run_dir, 'corrected_comparison_complete', selected=summary['selected_model'],
                goal_met=summary['goal_met'], run=str(args.run_dir))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=args.signature))
            raise


def show_results(args):
    status = read(args.run_dir / 'status.json')
    # Avoid printing hundreds of checkpoint hashes in an interactive terminal.
    compact = {key: value for key, value in status.items() if key != 'artifact_hashes'}
    compact['artifact_count'] = len(status.get('artifact_hashes', {}))
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    summary_path = args.run_dir / 'comparison_summary.json'
    if summary_path.is_file() and status['status'] == 'complete':
        summary = read(summary_path)
        print(json.dumps({key: summary[key] for key in ('selected_model', 'selected_candidate', 'goal_met')},
                         ensure_ascii=False, indent=2))
    for name in ('dev_candidates.csv', 'comparison.csv', 'per_label_comparison.csv'):
        path = args.run_dir / name
        if path.is_file():
            print('\n' + name)
            print(path.read_text(encoding='utf-8'), end='')
    if status['status'] != 'complete':
        print('\nRun is not complete; existing output may be partial.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('train', 'show-results'), nargs='?', default='train')
    parser.add_argument('--bundle', type=Path, default=SOURCE / 'assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--previous-run', type=Path, default=HERE / 'runs/siglip-ai-reviewed-v1-fp32')
    parser.add_argument('--packet', type=Path, default=HERE / 'training_packets/siglip-ai-reviewed-v1')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-corrected-v2-fp32')
    parser.add_argument('--cs', type=float, nargs='+', default=[.1, 1., 10.])
    parser.add_argument('--backbone-lr', type=float, default=3e-5)
    parser.add_argument('--l2sp-strengths', type=float, nargs='+', default=[.01, .1])
    parser.add_argument('--epochs', type=int, default=8)
    parser.add_argument('--patience', type=int, default=2)
    parser.add_argument('--microbatch', type=int, choices=(1, 2, 4, 8), default=2)
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('bundle', 'baseline', 'prior_run', 'previous_run', 'packet', 'model_dir', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.command == 'show-results':
        show_results(args)
        return
    require(min(args.epochs, args.patience, args.warmups, args.repeats) > 0 and
            all(math.isfinite(v) and v > 0 for v in [args.backbone_lr, *args.cs, *args.l2sp_strengths]),
            'Counts, learning rates, C values and L2-SP strengths must be positive and finite')
    require(len(args.cs) == len(set(args.cs)) and len(args.l2sp_strengths) == len(set(args.l2sp_strengths)) and
            len({f'{v:g}' for v in args.l2sp_strengths}) == len(args.l2sp_strengths), 'Duplicate candidate settings')
    run(args)


if __name__ == '__main__':
    main()
