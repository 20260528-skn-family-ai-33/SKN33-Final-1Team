"""Train only two backbone LoRA candidates; reuse the completed Open Images head run."""
from pathlib import Path
import argparse
import copy
import csv
import fcntl
import math
import os
import shutil
import traceback

import finetune_siglip_targeted as targeted
import finetune_siglip_targeted_v2 as v2
from benchlib import LABELS, SEED, contained, csv_rows, digest, log, metrics, read, save, sha, targets

HERE, SOURCE = targeted.HERE, targeted.SOURCE
require = targeted.require
CONTROL, HEAD_CONTROL, LORA, PRESERVE = targeted.MODEL_NAMES
NEW_MODELS = (LORA, PRESERVE)


def sources():
    result = v2.sources()
    for name in ('finetune_siglip_openimages_lora.py', 'run_openimages_lora.sh'):
        require((HERE / name).is_file(), 'Missing new source: ' + name)
        result[name] = sha(HERE / name)
    return result


def input_hashes(args):
    import finetune_siglip_corrected as corrected
    result = corrected.input_hashes(args)
    for name in ('protocol.json', 'status.json', 'selection_frozen.json', 'selection_commit.json'):
        result['head_run/' + name] = sha(args.head_run / name)
    return result


def head_selection(args, environment, model_packet):
    """Read the source's development selection, never choose its best test row."""
    protocol = read(args.head_run / 'protocol.json')
    status = read(args.head_run / 'status.json')
    selected = read(args.head_run / 'selection_frozen.json')
    require(status['status'] == 'complete' and
            protocol['signature'] == digest(protocol['config']) == status['protocol_signature'] == selected['protocol_signature'],
            'A complete, unchanged Open Images head run is required')
    require(read(args.head_run / 'selection_commit.json')['sha256'] == sha(args.head_run / 'selection_frozen.json') and
            selected['test_used_for_selection'] is False, 'Source development selection changed')
    config = protocol['config']
    require(config['environment'] == environment and config['model_packet'] == model_packet,
            'Keep the existing GPU/runtime/model weights')
    require(config['input_hashes']['packet_manifest'] == sha(args.packet / 'packet_manifest.json') and
            config['input_hashes']['base_manifest'] == sha(args.bundle / 'bundle_manifest.json'),
            'The completed head control must use this same training packet and base split')
    for name, checksum in config['script_hashes'].items():
        require(sha(HERE / name) == checksum, 'Completed head-run source changed: ' + name)
    for name, checksum in status['artifact_hashes'].items():
        require(sha(contained(args.head_run, name)) == checksum, 'Head-run artifact changed: ' + name)
    model_name = selected['selected_model']
    candidate = selected['models'][model_name]
    require(candidate['family'] in ('reference', 'instrument_attention'), 'Expected an existing head/probe control')
    record = str(Path(candidate['checkpoint']).parent / 'candidate_complete.json')
    require(selected['candidate_records'][record] == sha(contained(args.head_run, record)),
            'Head control candidate record changed')
    import finetune_siglip_focused as focused
    focused.verify_artifacts(args.head_run, candidate)
    return model_name, candidate


def import_head(args, model_name, source, reference, rows, baseline_head):
    import numpy as np
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    from siglip_targeted_models import load_candidate
    folder = args.run_dir / 'candidates/reference-openimages-head'
    done = folder / 'candidate_complete.json'
    identity = dict(model=model_name, selection_sha256=sha(args.head_run / 'selection_frozen.json'),
                    checkpoint_sha256=sha(contained(args.head_run, source['checkpoint'])),
                    extra_sha256=sha(contained(args.head_run, source['extra_checkpoint']))
                        if source.get('extra_checkpoint') else None)
    if done.exists():
        cached = read(done)
        require(cached['protocol_signature'] == args.signature and cached['reference_identity'] == identity,
                'Imported head control differs')
        focused.verify_artifacts(args.run_dir, cached)
        return cached
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(contained(args.head_run, source['checkpoint']), folder / 'best.safetensors')
    artifact_names = ['best.safetensors', 'dev_probabilities.npy']
    extra = None
    if source.get('extra_checkpoint'):
        shutil.copyfile(contained(args.head_run, source['extra_checkpoint']), folder / 'instrument.safetensors')
        extra = str((folder / 'instrument.safetensors').relative_to(args.run_dir))
        artifact_names.append('instrument.safetensors')
    candidate = copy.deepcopy(source)
    candidate.update(id='reference-openimages-head', source='prior', protocol_signature=args.signature,
        source_model=model_name, source_trained_epochs=source.get('trained_epochs', 0), trained_epochs=0,
        selected_checkpoint_source='reference', stop_reason='completed_head_reused',
        current_run_training_executed=False, reference_identity=identity,
        checkpoint=str((folder / 'best.safetensors').relative_to(args.run_dir)), extra_checkpoint=extra)
    encoder = head = None
    try:
        encoder, head = load_candidate(args, candidate, baseline_head)
        dev = [row for row in rows if row['split'] == 'dev']
        probabilities = ft.predict_dev(encoder, head, args.packet, dev)
        old = np.load(contained(args.head_run, source['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
        expected = np.load(contained(args.run_dir, reference['checkpoint']).parent / 'dev_probabilities.npy', allow_pickle=False)
        require(np.allclose(probabilities, old, rtol=1e-5, atol=1e-6), 'Imported head no longer reproduces source dev scores')
        require(np.allclose(probabilities[:, targeted.OTHERS], expected[:, targeted.OTHERS], rtol=1e-5, atol=1e-6),
                'Head control changed a non-instrument dev score')
        candidate['dev_metrics'] = metrics(targets(dev), probabilities, candidate['thresholds'])
        candidate['dev_confusion'] = focused.confusion(targets(dev), probabilities, candidate['thresholds'])
        ft.common.save_array(folder / 'dev_probabilities.npy', probabilities)
    finally:
        encoder = head = None
        ft.common.release()
    candidate['artifact_hashes'] = {name: sha(folder / name) for name in artifact_names}
    save(done, candidate)
    return candidate


def freeze(args, models, rows):
    import numpy as np
    reference = models[CONTROL]
    eligible = [(name, candidate) for name, candidate in models.items()
                if not targeted.guard_reasons(candidate, reference)]
    chosen, selected = max(eligible, key=lambda item: (
        item[1]['dev_metrics']['label_wise_accuracy'],
        -item[1]['dev_confusion']['musical_instrument']['fn'],
        item[1]['dev_metrics']['macro_f1'], item[1]['dev_metrics']['macro_average_precision'],
        item[0] not in NEW_MODELS))
    dev = [row for row in rows if row['split'] == 'dev']
    reports, label_rows = [], []
    for name, candidate in models.items():
        reasons = targeted.guard_reasons(candidate, reference)
        folder = contained(args.run_dir, candidate['checkpoint']).parent
        probabilities = np.load(folder / 'dev_probabilities.npy', allow_pickle=False)
        losses = v2.development_losses(dev, probabilities, False)
        instrument = candidate['dev_confusion']['musical_instrument']
        vehicle = candidate['dev_confusion']['vehicle']
        reports.append(dict(model=name, candidate=candidate['id'], family=candidate['family'],
            current_run_training_executed=name in NEW_MODELS, trained_epochs=candidate.get('trained_epochs', 0),
            best_epoch=candidate.get('best_epoch'), selected_checkpoint_source=candidate.get('selected_checkpoint_source', 'reference'),
            stop_reason=candidate.get('stop_reason', 'reference'),
            dev_accuracy=candidate['dev_metrics']['label_wise_accuracy'],
            dev_macro_f1=candidate['dev_metrics']['macro_f1'], dev_macro_AP=candidate['dev_metrics']['macro_average_precision'],
            dev_BCE_scope='all_known_labels', dev_BCE=losses['dev_BCE'], dev_instrument_BCE=losses['dev_instrument_BCE'],
            dev_instrument_AP=candidate['dev_metrics']['per_label']['musical_instrument']['average_precision'],
            instrument_fp=instrument['fp'], instrument_fn=instrument['fn'], vehicle_fp=vehicle['fp'], vehicle_fn=vehicle['fn'],
            eligible=not reasons, rejection_reason=';'.join(reasons), selected=name == chosen))
        for index, label in enumerate(LABELS):
            count, old = candidate['dev_confusion'][label], reference['dev_confusion'][label]
            label_rows.append(dict(model=name, label=label, threshold=candidate['thresholds'][index], **count,
                AP=candidate['dev_metrics']['per_label'][label]['average_precision'],
                fp_delta=count['fp'] - old['fp'], fn_delta=count['fn'] - old['fn']))
    csv_rows(args.run_dir / 'dev_candidates.csv', reports)
    csv_rows(args.run_dir / 'dev_per_label.csv', label_rows)
    metadata = {str(Path(candidate['checkpoint']).parent / 'candidate_complete.json'):
                sha(contained(args.run_dir, candidate['checkpoint']).parent / 'candidate_complete.json')
                for candidate in models.values()}
    save(args.run_dir / 'selection_frozen.json', dict(protocol_signature=args.signature, models=models,
        selected_model=chosen, selected_candidate=selected['id'], test_used_for_selection=False,
        previously_exposed_test=True, candidate_records=metadata,
        report_hashes={name: sha(args.run_dir / name) for name in ('dev_candidates.csv', 'dev_per_label.csv')}))
    save(args.run_dir / 'selection_commit.json', dict(sha256=sha(args.run_dir / 'selection_frozen.json')))
    log(args.run_dir, 'openimages_lora_selection_frozen_before_test', selected_model=chosen)


def diagnostics(args, source_model):
    """Called only after development selection is frozen and evaluation is complete."""
    import numpy as np
    import siglip_corrected_results as results
    selected = targeted.frozen(args)
    predictions = {name: read(args.run_dir / name / 'test_predictions.json') for name in targeted.MODEL_NAMES}
    expected = read(args.head_run / source_model / 'test_predictions.json')
    current = predictions[HEAD_CONTROL]
    require([row['image_id'] for row in current] == [row['image_id'] for row in expected] and
            all(a['predictions'] == b['predictions'] for a, b in zip(current, expected)) and
            np.allclose([row['probabilities'] for row in current], [row['probabilities'] for row in expected], rtol=1e-5, atol=1e-6),
            'Imported head test predictions differ from the completed source')
    counts = {name: results.label_counts(rows) for name, rows in predictions.items()}
    with (args.run_dir / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        comparison = list(csv.DictReader(stream))
    for row in comparison:
        count = counts[row['model']]
        row.update(instrument_fp=count['musical_instrument']['fp'], instrument_fn=count['musical_instrument']['fn'],
                   vehicle_fp=count['vehicle']['fp'], vehicle_fn=count['vehicle']['fn'])
    csv_rows(args.run_dir / 'comparison.csv', comparison)
    positives = []
    for name, rows in predictions.items():
        threshold = selected['models'][name]['thresholds'][targeted.INSTRUMENT]
        for row in rows:
            if row['labels']['musical_instrument'] == 1:
                probability = row['probabilities'][targeted.INSTRUMENT]
                positives.append(dict(model=name, image_id=row['image_id'], probability=probability, threshold=threshold,
                    margin=probability-threshold, result='TP' if row['predictions'][targeted.INSTRUMENT] else 'FN'))
    csv_rows(args.run_dir / 'instrument_positive_scores.csv', positives)


def run(args):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import finetune_siglip as ft
    import finetune_siglip_focused as focused
    import finetune_siglip_corrected as corrected
    from prepare_partial_data import validate_packet
    script_hashes = sources()
    for root in (args.bundle, args.baseline, args.prior_run, args.previous_run, args.head_run, args.packet, args.model_dir):
        require(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir), 'Use a separate new run directory')
    args.device = 'cuda:0'
    manifest, rows = validate_packet(args.packet, args.bundle)
    require(manifest['added_images'] > 0, 'Approved additional images are required')
    environment = ft.common.cuda_environment('fp32')
    baseline, baseline_head, _ = focused.input_protocol(args)
    require(environment == read(args.baseline / 'environment.json') == read(args.prior_run / 'environment.json'),
            'Keep the existing GPU/runtime')
    previous = corrected.previous_inputs(args, baseline, environment)
    model_packet = ft.common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == baseline['config']['models']['siglip2'], 'Source model weights changed')
    source_model, source_head = head_selection(args, environment, model_packet)
    fingerprints = input_hashes(args)
    specs = [dict(id='openimages-lora', family='lora', preserve_weight=0.),
             dict(id='openimages-lora-preserve', family='lora_preserve', preserve_weight=args.preserve_weight)]
    config = dict(schema='siglip-openimages-lora-v1', environment=environment, model_packet=model_packet,
        script_hashes=script_hashes, input_hashes=fingerprints, candidates=specs, seed=SEED, precision='fp32',
        image_size=384, epochs=args.epochs, min_epochs=args.min_epochs, patience=args.patience,
        min_delta=args.min_delta, checkpoint_min_delta=args.checkpoint_min_delta,
        microbatch=args.microbatch, effective_batch=8, lora_rank=args.lora_rank, lora_alpha=args.lora_alpha,
        tail_blocks=args.tail_blocks, lora_lr=args.lora_lr, head_lr=args.head_lr, preserve_weight=args.preserve_weight,
        head_control_source_model=source_model,
        initialization='Both new LoRAs start from the same previous development-selected corrected partial native model.',
        training='Only last tail_blocks q_proj/v_proj LoRA adapters and 8-label classifier are optimized; completed head/probe controls reused.',
        loss='Existing targeted masked BCE over known labels; unknown labels remain excluded.',
        preservation='Frozen reference Bernoulli KL on known non-instrument TRAIN cells only; no hard labels or teacher loss on unknown cells.',
        checkpoint='Existing v2 minimum all-known dev BCE, AP tie-break, explicit epoch0 fallback.',
        calibration='Existing development-only instrument/vehicle FP/FN budgets; other labels development accuracy.',
        selection='Development accuracy/F1 and original per-label regression guards; reference preferred on metric ties.',
        resume='Completed candidates reused; interrupted candidate restarts from the reference. Same code/data/settings required.',
        warmups=args.warmups, repeats=args.repeats, target_accuracy=.9, target_p95_ms=50., test_used_for_selection=False)
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol = args.run_dir / 'protocol.json'
        if protocol.exists():
            saved = read(protocol)
            require(args.resume and saved['signature'] == args.signature == digest(saved['config']),
                    'Resume requires identical code/data/settings')
        else:
            require(all(path.name == '.lock' for path in args.run_dir.iterdir()), 'Choose an empty new run directory')
            save(protocol, dict(signature=args.signature, config=config))
            save(args.run_dir / 'environment.json', environment)
            save(args.run_dir / 'training_packet_manifest.json', manifest)
            save(args.run_dir / 'training_packet_approvals.json', read(args.packet / 'approvals.json'))
            save(args.run_dir / 'training_data_summary.json', dict(added_images=manifest['added_images'],
                changed_train_cells=manifest['changed_train_cells'], known_cells=manifest['known_cells'],
                train_images=sum(row['split']=='train' for row in rows), dev_images=sum(row['split']=='dev' for row in rows),
                test_images_used_in_training=0, head_control_retrained=False,
                approval_note='Existing AI review provenance retained; independent human annotation is not claimed.'))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status']=='complete':
            done = read(status_path)
            targeted.frozen(args)
            require(done['protocol_signature']==args.signature and
                    all(sha(contained(args.run_dir,name))==checksum for name,checksum in done['artifact_hashes'].items()),
                    'Completed experiment changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature))
        try:
            if not (args.run_dir / 'selection_frozen.json').exists():
                reference = corrected.reference(args, 'reference-partial', args.previous_run, previous, baseline_head, rows)
                head_control = import_head(args, source_model, source_head, reference, rows, baseline_head)
                trained = [v2.train_candidate(args,spec,rows,reference,baseline_head) for spec in specs]
                freeze(args, dict(zip(targeted.MODEL_NAMES,[reference,head_control,*trained])), rows)
            summary = targeted.evaluate(args, baseline_head)
            diagnostics(args, source_model)
            validate_packet(args.packet,args.bundle)
            head_selection(args,environment,model_packet)
            require(input_hashes(args)==fingerprints and sources()==script_hashes and
                    ft.common.model_packet(args.model_dir,'siglip2')==model_packet, 'Training inputs or code changed during execution')
            excluded = {'.lock','status.json','failure.json','events.jsonl'}
            hashes = {path.relative_to(args.run_dir).as_posix():sha(path) for path in args.run_dir.rglob('*')
                      if path.is_file() and path.name not in excluded and not path.name.endswith('.tmp')}
            save(status_path,dict(status='complete',protocol_signature=args.signature,CUDA_executed=True,
                backbone_training_executed=True,head_training_executed=True,head_control_retrained=False,
                OCR_executed=False,model_deployed=False,artifact_hashes=hashes))
            log(args.run_dir,'openimages_lora_complete',selected_model=summary['selected_model'],goal_met=summary['goal_met'])
        except Exception as exc:
            save(args.run_dir/'failure.json',dict(reason=repr(exc),traceback=traceback.format_exc()))
            save(status_path,dict(status='failed',protocol_signature=args.signature))
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('train','show-results'),nargs='?',default='train')
    parser.add_argument('--bundle',type=Path,default=SOURCE/'assets')
    parser.add_argument('--baseline',type=Path,default=HERE/'runs/heads-fp32')
    parser.add_argument('--prior-run',type=Path,default=HERE/'runs/siglip-full-fp32')
    parser.add_argument('--previous-run',type=Path,default=HERE/'runs/siglip-ai-reviewed-v1-fp32')
    parser.add_argument('--head-run',type=Path,default=HERE/'runs/siglip-openimages-instrument-v1-fp32')
    parser.add_argument('--packet',type=Path,default=HERE/'training_packets/siglip-openimages-v1')
    parser.add_argument('--model-dir',type=Path,default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--run-dir',type=Path,default=HERE/'runs/siglip-openimages-lora-v1-fp32')
    parser.add_argument('--epochs',type=int,default=16)
    parser.add_argument('--min-epochs',type=int,default=16)
    parser.add_argument('--patience',type=int,default=4)
    parser.add_argument('--min-delta',type=float,default=1e-5)
    parser.add_argument('--checkpoint-min-delta',type=float,default=1e-7)
    parser.add_argument('--microbatch',type=int,choices=(1,2,4,8),default=2)
    parser.add_argument('--lora-rank',type=int,default=8)
    parser.add_argument('--lora-alpha',type=float,default=8.)
    parser.add_argument('--tail-blocks',type=int,choices=range(1,13),default=2)
    parser.add_argument('--lora-lr',type=float,default=3e-5)
    parser.add_argument('--head-lr',type=float,default=3e-5)
    parser.add_argument('--preserve-weight',type=float,default=1.)
    parser.add_argument('--warmups',type=int,default=20)
    parser.add_argument('--repeats',type=int,default=10)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    for name in ('bundle','baseline','prior_run','previous_run','head_run','packet','model_dir','run_dir'):
        setattr(args,name,getattr(args,name).expanduser().resolve())
    if args.command=='show-results':
        import finetune_siglip_corrected as corrected
        corrected.show_results(args)
        for name in ('dev_per_label.csv','instrument_positive_scores.csv'):
            path=args.run_dir/name
            if path.is_file():
                print('\n'+name)
                print(path.read_text(),end='')
        return
    require(1<=args.min_epochs<=args.epochs and min(args.patience,args.warmups,args.repeats,args.lora_rank)>0,
            'Use valid positive epoch/count/rank settings')
    require(all(math.isfinite(value) and value>0 for value in
                (args.lora_alpha,args.lora_lr,args.head_lr,args.preserve_weight)) and
            all(math.isfinite(value) and value>=0 for value in (args.min_delta,args.checkpoint_min_delta)),
            'Invalid learning rate, LoRA scale, preservation weight, or delta')
    run(args)


if __name__=='__main__':
    main()
