"""New poster packet plus source-equalized training; reuse the existing v2 model/loss code."""
from pathlib import Path
from contextlib import contextmanager
import argparse
import fcntl
import math
import os
import random
import traceback

import finetune_siglip_targeted as targeted
import finetune_siglip_targeted_v2 as v2
from prepare_poster_augment import ASSETS, verify_assets
from benchlib import LABELS, SEED, contained, csv_rows, digest, log, metrics, read, save, sha, targets

HERE, SOURCE = targeted.HERE, targeted.SOURCE
require = targeted.require


def sources():
    result = v2.sources()
    for name in ('finetune_siglip_poster_mix.py', 'prepare_poster_augment.py', 'run_poster_augment.sh',
                 'poster_augment_v2/package_manifest.json'):
        require((HERE / name).is_file(), 'Missing poster source: ' + name)
        result[name] = sha(HERE / name)
    return result


def source_groups(args, rows):
    additions = {a['image_id']: a for a in read(args.packet / 'approvals.json')['additions']}
    groups = {'poster': [], 'openimages': []}
    for row in rows:
        if row['split'] != 'train':
            continue
        source = additions.get(row['image_id'], {}).get('source_id', '')
        if not source:
            require(row['image_id'].startswith('P'), 'Unknown base training identity')
        group = 'openimages' if source.startswith('openimages:') else 'poster'
        groups[group].append(row)
    require(all(groups.values()), 'Both original/added posters and Open Images training rows are required')
    return groups


@contextmanager
def source_sampling(args, rows):
    """Scope the sampler override to this process and restore the unchanged v1 epoch function."""
    original = targeted.train_epoch
    groups = source_groups(args, rows)
    membership = {r['image_id']: name for name, items in groups.items() for r in items}

    def epoch(epoch_args, spec, encoder, head, optimizer, train, number, teacher=None):
        active = [r for r in train if spec['family'] != 'instrument_attention' or
                  r['labels']['musical_instrument'] is not None]
        pools = {name: [r for r in active if membership[r['image_id']] == name] for name in groups}
        require(all(pools.values()), 'No active training rows in a source group')
        if args.sampling == 'equal_sources':
            rng = random.Random(SEED + 1_000_003 * number)
            count = max(map(len, pools.values()))
            sampled = []
            for pool in pools.values():
                # Every eligible original is visited once; only the smaller source pool is oversampled.
                sampled.extend(pool)
                sampled.extend(rng.choices(pool, k=count - len(pool)))
        else:
            sampled = active
        result = original(epoch_args, spec, encoder, head, optimizer, sampled, number, teacher)
        result.update(sampling=args.sampling, unique_training_images=len(active),
            unique_poster_images=len(pools['poster']), unique_openimages_images=len(pools['openimages']),
            sampled_poster_views=sum(membership[r['image_id']] == 'poster' for r in sampled),
            sampled_openimages_views=sum(membership[r['image_id']] == 'openimages' for r in sampled),
            sampled_training_rows_sha256=digest([r['image_id'] for r in sampled]))
        return result

    targeted.train_epoch = epoch
    try:
        yield groups
    finally:
        targeted.train_epoch = original


def instrument_scores(args):
    frozen = targeted.frozen(args)
    rows = []
    for name, candidate in frozen['models'].items():
        cutoff = candidate['thresholds'][LABELS.index('musical_instrument')]
        for row in read(args.run_dir / name / 'test_predictions.json'):
            if row['labels']['musical_instrument'] == 1:
                probability = row['probabilities'][LABELS.index('musical_instrument')]
                rows.append(dict(model=name, image_id=row['image_id'], probability=probability,
                    threshold=cutoff, margin=probability-cutoff,
                    result='TP' if row['predictions'][LABELS.index('musical_instrument')] else 'FN'))
    csv_rows(args.run_dir / 'instrument_positive_scores.csv', rows)


def run(args):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    import finetune_siglip_corrected as corrected
    from prepare_partial_data import validate_packet
    package, _ = verify_assets()
    source_hashes = sources()
    for root in (args.bundle, args.baseline, args.prior_run, args.previous_run, args.packet, args.model_dir, ASSETS):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir),
                'Use a separate new run directory')
    args.device = 'cuda:0'
    manifest, rows = validate_packet(args.packet, args.bundle)
    lineage_path = args.packet.with_name(args.packet.name + '.lineage.json')
    lineage = read(lineage_path)
    require(lineage['parent_manifest_sha256'] == package['parent_manifest_sha256'] and
            lineage['poster_package_sha256'] == sha(ASSETS / 'package_manifest.json') and
            lineage['packet_manifest_sha256'] == sha(args.packet / 'packet_manifest.json') and
            lineage['corrections_sha256'] == package['corrections_sha256'], 'Poster packet lineage differs')
    require(manifest['added_images'] == 327 + package['new_images'] and manifest['changed_train_cells'] == 32,
            'Keep all previous additions and 32 corrections')
    groups = source_groups(args, rows)
    require(len(groups['openimages']) == 315, 'Expected all existing 315 approved Open Images examples')
    environment = ft.common.cuda_environment('fp32')
    baseline, baseline_head, _ = focused.input_protocol(args)
    require(environment == read(args.baseline / 'environment.json') == read(args.prior_run / 'environment.json'),
            'Keep the existing GPU/runtime')
    previous = corrected.previous_inputs(args, baseline, environment)
    model_packet = ft.common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == baseline['config']['models']['siglip2'], 'Source model weights changed')
    fingerprints = corrected.input_hashes(args)
    fingerprints['poster_lineage'] = sha(lineage_path)
    specs = [dict(id='instrument-attention', family='instrument_attention', preserve_weight=0.),
             dict(id='lora', family='lora', preserve_weight=0.),
             dict(id='lora-preserve', family='lora_preserve', preserve_weight=args.preserve_weight)]
    sampling = dict(mode=args.sampling, unit='TRAIN image views per epoch; not loss-cell weights',
        unique_counts={name: len(items) for name, items in groups.items()},
        source_ids_sha256={name: digest([r['image_id'] for r in items]) for name, items in groups.items()},
        equal_sources='Visit every eligible original once and resample the smaller pool up to the larger count; v1 shuffles the resulting epoch.',
        unknowns='Known-instrument filter for specialist; unknown labels always masked for LoRA; no teacher loss on unknown cells.')
    config = dict(schema='siglip-poster-source-mix-v1', environment=environment, model_packet=model_packet,
        script_hashes=source_hashes, input_hashes=fingerprints, candidates=specs, seed=SEED,
        precision='fp32', image_size=384, sampling=sampling,
        max_epochs=args.epochs, min_epochs=args.min_epochs, patience=args.patience, min_delta=args.min_delta,
        checkpoint_min_delta=args.checkpoint_min_delta, microbatch=args.microbatch, effective_batch=8,
        lora_rank=args.lora_rank, lora_alpha=args.lora_alpha, tail_blocks=args.tail_blocks,
        lora_lr=args.lora_lr, head_lr=args.head_lr, probe_lr=args.probe_lr, preserve_weight=args.preserve_weight,
        weight_decay=1e-4, gradient_clip=1.,
        initialization='Same development-selected corrected partial native model; reference is not retrained.',
        training='Existing v1 model/loss/optimizer/weak-augmentation code with explicitly recorded source sampling.',
        checkpoint='Existing v2 minimum masked dev BCE with AP tie-break and epoch0 fallback.',
        stopping='Existing v2 dev BCE patience after min_epochs; default full 16 epochs.',
        calibration='Existing v1 development-only thresholds and instrument/vehicle FP/FN budgets.',
        selection='Existing development accuracy/F1 and per-label regression guards; reference fallback.',
        comparison_scope='This run changes the reviewed poster data and, by default, source sampling together; their separate causal contributions are not isolated.',
        warmups=args.warmups, repeats=args.repeats, target_accuracy=.9, target_p95_ms=50., test_used_for_selection=False)
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            protocol = read(protocol_path)
            require(args.resume and protocol['signature'] == args.signature == digest(protocol['config']),
                    'Resume requires identical code/data/settings')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Choose an empty new run directory')
            save(protocol_path, dict(signature=args.signature, config=config))
            save(args.run_dir / 'environment.json', environment)
            save(args.run_dir / 'training_packet_manifest.json', manifest)
            save(args.run_dir / 'training_packet_approvals.json', read(args.packet / 'approvals.json'))
            save(args.run_dir / 'training_data_summary.json', dict(added_images=manifest['added_images'],
                inherited_added_images=327, new_posters=package['new_images'], changed_train_cells=32,
                known_cells=manifest['known_cells'], train_images=sum(r['split'] == 'train' for r in rows),
                dev_images=sum(r['split'] == 'dev' for r in rows), test_images_used_in_training=0,
                sampling=sampling, approval_note='User-requested AI visual review; independent human verification is not claimed.'))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            targeted.frozen(args)
            require(status['protocol_signature'] == args.signature and
                    all(sha(contained(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                    'Completed poster experiment changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature))
        try:
            if not (args.run_dir / 'selection_frozen.json').exists():
                reference = corrected.reference(args, 'reference-partial', args.previous_run, previous, baseline_head, rows)
                with source_sampling(args, rows):
                    candidates = [v2.train_candidate(args, spec, rows, reference, baseline_head) for spec in specs]
                v2.freeze(args, reference, candidates)
            summary = targeted.evaluate(args, baseline_head)
            instrument_scores(args)
            validate_packet(args.packet, args.bundle)
            verify_assets()
            after = corrected.input_hashes(args)
            after['poster_lineage'] = sha(lineage_path)
            require(after == fingerprints and sources() == source_hashes and
                    ft.common.model_packet(args.model_dir, 'siglip2') == model_packet,
                    'Inputs or source changed during execution')
            excluded = {'.lock', 'status.json', 'failure.json', 'events.jsonl'}
            hashes = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                      if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature, CUDA_executed=True,
                backbone_training_executed=True, head_training_executed=True, OCR_executed=False,
                model_deployed=False, artifact_hashes=hashes))
            log(args.run_dir, 'poster_mix_complete', selected_model=summary['selected_model'],
                goal_met=summary['goal_met'], run=str(args.run_dir))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=args.signature))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('train', 'show-results'), nargs='?', default='train')
    for name, default in dict(bundle=SOURCE/'assets', baseline=HERE/'runs/heads-fp32',
        prior_run=HERE/'runs/siglip-full-fp32', previous_run=HERE/'runs/siglip-ai-reviewed-v1-fp32',
        packet=HERE/'training_packets/siglip-poster-v2', model_dir=Path('/workspace/models/siglip2-base-384'),
        run_dir=HERE/'runs/siglip-poster-v2-mix-fp32').items():
        parser.add_argument('--'+name.replace('_','-'), dest=name, type=Path, default=default)
    parser.add_argument('--sampling', choices=('equal_sources', 'uniform'), default='equal_sources')
    for name, default in dict(epochs=16, min_epochs=16, patience=4, lora_rank=8, tail_blocks=2,
                             warmups=20, repeats=10).items():
        parser.add_argument('--'+name.replace('_','-'), dest=name, type=int, default=default)
    parser.add_argument('--microbatch', type=int, choices=(1,2,4,8), default=2)
    for name, default in dict(min_delta=1e-5, checkpoint_min_delta=1e-7, lora_alpha=8.,
                             lora_lr=3e-5, head_lr=3e-5, probe_lr=1e-3, preserve_weight=1.).items():
        parser.add_argument('--'+name.replace('_','-'), dest=name, type=float, default=default)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('bundle','baseline','prior_run','previous_run','packet','model_dir','run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.command == 'show-results':
        import finetune_siglip_corrected as corrected
        corrected.show_results(args)
        for name in ('dev_per_label.csv','instrument_positive_scores.csv'):
            path = args.run_dir / name
            if path.is_file():
                print('\n'+name)
                print(path.read_text(), end='')
        return
    require(1 <= args.min_epochs <= args.epochs and min(args.patience,args.warmups,args.repeats,args.lora_rank)>0
            and 1 <= args.tail_blocks <= 12, 'Invalid positive epoch/count/rank settings')
    require(all(math.isfinite(v) and v>0 for v in (args.lora_alpha,args.lora_lr,args.head_lr,args.probe_lr,args.preserve_weight))
            and all(math.isfinite(v) and v>=0 for v in (args.min_delta,args.checkpoint_min_delta)),
            'Invalid learning rate, scale, preservation weight, or delta')
    run(args)


if __name__ == '__main__':
    main()
