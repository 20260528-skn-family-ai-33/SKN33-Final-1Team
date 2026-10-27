"""Inventory and evaluate the selected SigLIP2 model on available RunPod image inputs.

Input datasets and previous runs are read-only. Metrics are per labelled source/split;
the discovered images in dataset/logo folders have no verified labels and are prediction-only.
"""
import argparse
import fcntl
import json
import math
import os
import time
import traceback
import uuid
from collections import defaultdict
from pathlib import Path

import run_experiment as common
from benchlib import LABELS, contained, digest, load_test, read, save, sha
from prepare_partial_data import validate_packet
from resultlib import percentile, require, write_csv

HERE = Path(__file__).resolve().parent
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff', '.gif'}
UNLABELLED = (Path('/workspace/datasets'), Path('/workspace/query_logos'), Path('/workspace/video_logos'))


def model_identity(args):
    selected_path = args.model_run / 'selection_frozen.json'
    selected = read(selected_path)
    protocol = read(args.model_run / 'protocol.json')
    status = read(args.model_run / 'status.json')
    require(status['status'] == 'complete' and status['protocol_signature'] ==
            selected['protocol_signature'] == protocol['signature'] == digest(protocol['config']),
            'Selected run is not complete or protocol differs')
    selection_sha = sha(selected_path)
    require(read(args.model_run / 'selection_commit.json')['sha256'] == selection_sha,
            'Frozen selection commit differs')
    checkpoint = contained(args.model_run, selected['checkpoint'])
    expected = selected['artifact_hashes'].get(checkpoint.name)
    require(expected and checkpoint.is_file() and sha(checkpoint) == expected,
            'Selected checkpoint is missing or has changed')
    thresholds = selected['thresholds']
    require(len(thresholds) == len(LABELS) and all(type(v) in (int, float) and
            math.isfinite(v) and 0 <= v <= 1 for v in thresholds), 'Invalid frozen thresholds')
    model_packet = common.model_packet(args.model_dir, 'siglip2')
    require(model_packet == protocol['config']['model_packet'], 'Downloaded SigLIP2 model differs from selected run')
    bundle_sha = sha(args.bundle / 'bundle_manifest.json')
    require(bundle_sha == protocol['config']['base_manifest_sha256'], 'Selected run used a different base bundle')
    return dict(selection_sha256=selection_sha, selection_commit_sha256=sha(args.model_run / 'selection_commit.json'),
        selected_protocol=protocol['signature'], checkpoint=str(checkpoint), checkpoint_sha256=expected,
        model_packet=model_packet, thresholds=thresholds, selected_candidate=selected.get('id'),
        selected_source=selected.get('source'), trained_packet_manifest_sha256=protocol['config']['packet_manifest_sha256'],
        bundle_sha256=bundle_sha)


def file_record(path, source, split, row=None):
    return dict(path=str(path), source=source, split=split, image_id=row['image_id'] if row else None,
                labels=row['labels'] if row else None, sha256=sha(path), bytes=path.stat().st_size)


def collect(args, identity):
    _, _, original = common.bundle_data(args.bundle)
    test = load_test(args.bundle, args.model_run / 'selection_frozen.json', identity['selection_sha256'])
    records, problems, packet_summary, roots = [], [], [], []
    for row in original + test:
        path = contained(args.bundle, row['image'])
        try:
            records.append(file_record(path, 'original_bundle', row['split'], row))
        except (OSError, ValueError) as exc:
            problems.append(dict(kind='image_access', source='original_bundle', path=str(path), error=str(exc)))
    if not args.packets_root.is_dir():
        problems.append(dict(kind='missing_root', source='training_packets', path=str(args.packets_root), error='not a directory'))
    else:
        for packet in sorted(args.packets_root.iterdir()):
            if packet.is_symlink() or not packet.is_dir():
                continue
            try:
                manifest, rows = validate_packet(packet, args.bundle)
                packet_hash = sha(packet / 'packet_manifest.json')
                relation = 'selected_training_packet' if packet_hash == identity['trained_packet_manifest_sha256'] else 'training_membership_unknown'
                packet_records = []
                for row in rows:
                    path = contained(packet, row['image'])
                    packet_records.append(dict(**file_record(path, 'packet:' + packet.name, row['split'], row), training_relation=relation))
                records.extend(packet_records)
                packet_summary.append(dict(packet=packet.name, valid=True, rows=len(rows), manifest_sha256=packet_hash,
                    added_images=manifest['added_images'], changed_train_cells=manifest['changed_train_cells'], training_relation=relation))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                packet_summary.append(dict(packet=packet.name, valid=False, error=str(exc)))
                problems.append(dict(kind='invalid_packet', source='packet:' + packet.name, path=str(packet), error=str(exc)))
    for root in args.unlabelled_roots:
        root_info = dict(path=str(root), found_images=0, skipped_symlinks=0, accessible=False)
        roots.append(root_info)
        if root.is_symlink() or not root.is_dir():
            problems.append(dict(kind='missing_or_symlink_root', source='unlabelled', path=str(root), error='not an accessible real directory'))
            continue
        root_info['accessible'] = True
        def onerror(exc):
            problems.append(dict(kind='directory_access', source='unlabelled:' + str(root),
                                 path=str(exc.filename), error=str(exc)))
        for directory, dirs, names in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
            dpath = Path(directory)
            root_info['skipped_symlinks'] += sum((dpath / name).is_symlink() for name in dirs)
            dirs[:] = sorted(name for name in dirs if not (dpath / name).is_symlink())
            for name in sorted(names):
                path = dpath / name
                if path.is_symlink():
                    root_info['skipped_symlinks'] += 1
                    continue
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                root_info['found_images'] += 1
                try:
                    require(path.is_file(), 'not a regular file')
                    records.append(file_record(path, 'unlabelled:' + str(root), 'unlabelled'))
                except (OSError, ValueError) as exc:
                    problems.append(dict(kind='image_access', source='unlabelled:' + str(root), path=str(path), error=str(exc)))
    require(records, 'No readable image inputs found')
    return records, problems, packet_summary, roots


def paths(args):
    for key in ('bundle', 'model_dir', 'model_run', 'packets_root', 'run_dir'):
        setattr(args, key, getattr(args, key).expanduser().resolve())
    args.unlabelled_roots = [p.expanduser().absolute() for p in args.unlabelled_roots]
    for source in (args.bundle, args.model_dir, args.model_run, args.packets_root, *args.unlabelled_roots):
        require(not args.run_dir.is_relative_to(source) and not source.is_relative_to(args.run_dir),
                'Run output must not overlap any input path: ' + str(source))


def preflight(args):
    identity = model_identity(args)
    records, problems, packets, roots = collect(args, identity)
    result = dict(status='inventory_complete', model=identity, records=len(records),
        unique_file_sha256=len({r['sha256'] for r in records}), labels=['verified original bundle and packet records'],
        packets=packets, roots=roots, problems=problems, training_executed=False, inference_executed=False,
        note='Some training packet approvals can be AI-assisted; verify label provenance. No labels inferred for unlabelled sources.')
    print(json.dumps(result, ensure_ascii=False, indent=2))


def metrics(group, probabilities, thresholds):
    # Deduplicate within one source/split. Conflicting labels for identical bytes are excluded and reported.
    by_hash = defaultdict(list)
    for row in group:
        if row['sha256'] in probabilities:
            by_hash[row['sha256']].append(row)
    cells, conflict_cells = [], 0
    for file_hash, rows in by_hash.items():
        pred = probabilities[file_hash]['probabilities']
        for index, label in enumerate(LABELS):
            truths = {r['labels'][label] for r in rows if r['labels'][label] is not None}
            if len(truths) > 1:
                conflict_cells += 1
            elif truths:
                cells.append((label, next(iter(truths)), int(pred[index] >= thresholds[index])))
    per = []
    for label in LABELS:
        selected = [(y, p) for k, y, p in cells if k == label]
        tp = sum(y == 1 and p == 1 for y, p in selected)
        fp = sum(y == 0 and p == 1 for y, p in selected)
        fn = sum(y == 1 and p == 0 for y, p in selected)
        tn = sum(y == 0 and p == 0 for y, p in selected)
        pos = tp + fn
        denominator = 2 * tp + fp + fn
        f1 = (2 * tp / denominator if denominator else 0.) if pos else None
        per.append(dict(label=label, known=len(selected), positive=pos, negative=tn + fp,
            tp=tp, fp=fp, fn=fn, tn=tn, f1=f1))
    supported = [r['f1'] for r in per if r['f1'] is not None]
    correct = sum(r['tp'] + r['tn'] for r in per)
    total = sum(r['known'] for r in per)
    return dict(input_rows=len(group), unique_successful_images=len(by_hash), known_cells=total,
        conflicting_label_cells_excluded=conflict_cells, label_accuracy=correct / total if total else None,
        macro_f1_supported=sum(supported) / len(supported) if supported else None,
        macro_f1_supported_count=len(supported),
        macro_f1_all8_zero_for_no_positive=sum((r['f1'] or 0.) for r in per) / len(LABELS)), per


def run(args):
    import torch
    from encoders import load_encoder
    from safetensors.torch import load_file
    from finetune_siglip import load_state
    identity = model_identity(args)
    records, initial_problems, packets, roots = collect(args, identity)
    environment = common.cuda_environment('fp32')
    sources = {name: sha(HERE / name) for name in ('evaluate_all_images.py', 'run_all_images.sh',
        'run_experiment.py', 'encoders.py', 'finetune_siglip.py', 'prepare_partial_data.py', 'resultlib.py')}
    sources['../poster_benchmark/benchlib.py'] = sha(HERE.parent / 'poster_benchmark/benchlib.py')
    config = dict(schema='siglip-all-image-inference-v1', model=identity, data_digest=digest(records),
        inputs=[str(p) for p in (args.bundle, args.packets_root, *args.unlabelled_roots)],
        inventory_problems=initial_problems, packets=packets, roots=roots, environment=environment,
        sources=sources, thresholds=identity['thresholds'], precision='fp32', warmups=0,
        timing='one batch-one successful inference per unique file hash; decode, preprocess, transfer, model and head; no hash or file writes',
        selection_policy='unchanged prior frozen selection; no evaluation data used to choose model or thresholds')
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            old = read(protocol_path)
            require(args.resume and old['signature'] == signature == digest(old['config']),
                    'Resume requires the identical inputs, model, environment and code')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Choose a new empty run directory')
            save(protocol_path, dict(signature=signature, config=config))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] in ('complete', 'complete_with_errors'):
            done = read(status_path)
            require(done['protocol_signature'] == signature and all(sha(contained(args.run_dir, n)) == h
                for n, h in done['artifact_hashes'].items()), 'Completed output changed')
            print('already_complete: no inference repeated')
            return
        save(status_path, dict(status='running', protocol_signature=signature, training_executed=False))
        cache = args.run_dir / 'cache'
        cache.mkdir(exist_ok=True)
        probabilities, problems = {}, list(initial_problems)
        session = uuid.uuid4().hex
        first_paths = {}
        for row in records:
            first_paths.setdefault(row['sha256'], row['path'])
        try:
            encoder = load_encoder('siglip2', args.model_dir, 'cuda:0', 'fp32', 384)
            head = torch.nn.Linear(encoder.dimension, len(LABELS), device='cuda:0', dtype=torch.float32)
            load_state(encoder, head, load_file(identity['checkpoint']))
            encoder.eval(); head.eval().requires_grad_(False)
            for index, (file_hash, image_path) in enumerate(first_paths.items(), 1):
                path = cache / (file_hash + '.json')
                if path.exists():
                    require(args.resume, 'Existing cache requires --resume')
                    value = read(path)
                    require(value['sha256'] == file_hash and value['protocol_signature'] == signature and
                        value['record_sha256'] == digest({k: v for k, v in value.items() if k != 'record_sha256'}) and
                        len(value['probabilities']) == len(LABELS) and all(type(v) in (int, float) and math.isfinite(v)
                        and 0 <= v <= 1 for v in value['probabilities']) and math.isfinite(value['elapsed_ms'])
                        and value['elapsed_ms'] >= 0, 'Invalid cached prediction')
                else:
                    try:
                        require(sha(image_path) == file_hash, 'Image changed after inventory')
                        torch.cuda.synchronize()
                        started = time.perf_counter()
                        with torch.inference_mode():
                            x = encoder.prepare(image_path).to('cuda:0')
                            prob = torch.sigmoid(head(encoder(x))).cpu().tolist()[0]
                        torch.cuda.synchronize()
                        elapsed = (time.perf_counter() - started) * 1000
                        require(all(math.isfinite(v) and 0 <= v <= 1 for v in prob), 'Non-finite model output')
                        require(sha(image_path) == file_hash, 'Image changed during inference')
                        value = dict(sha256=file_hash, protocol_signature=signature, probabilities=prob,
                                     elapsed_ms=elapsed, timing_session=session, source_path=image_path)
                        value['record_sha256'] = digest(value)
                        save(path, value)
                    except (OSError, ValueError) as exc:
                        problems.append(dict(kind='inference_failed', source='unique_file', path=image_path,
                                             sha256=file_hash, error=str(exc)))
                        print(json.dumps(dict(event='image_failed', path=image_path, error=str(exc)), ensure_ascii=False), flush=True)
                        continue
                probabilities[file_hash] = value
                if index % 50 == 0 or index == len(first_paths):
                    print(json.dumps(dict(event='image_progress', completed=index, total=len(first_paths)), ensure_ascii=False), flush=True)
            require(model_identity(args) == identity and all(sha(HERE / n) == h for n, h in sources.items()),
                    'Model or source code changed during inference')
            output = []
            for row in records:
                value = probabilities.get(row['sha256'])
                output.append(dict(**row, prediction=[int(x >= t) for x, t in zip(value['probabilities'], identity['thresholds'])] if value else None,
                    probabilities=value['probabilities'] if value else None, error=None if value else 'unique file failed inference'))
            summary, label_rows = [], []
            groups = defaultdict(list)
            for row in records:
                if row['labels'] is not None:
                    groups[(row['source'], row['split'])].append(row)
            for (source, split), rows in sorted(groups.items()):
                total, per = metrics(rows, probabilities, identity['thresholds'])
                summary.append(dict(source=source, split=split, **total,
                    interpretation='train includes seen data; dev used for model/threshold selection; original test was previously exposed'))
                label_rows.extend(dict(source=source, split=split, **entry) for entry in per)
            write_csv(args.run_dir / 'metrics.csv', summary,
                fields=['source','split','input_rows','unique_successful_images','known_cells','conflicting_label_cells_excluded',
                        'label_accuracy','macro_f1_supported','macro_f1_supported_count','macro_f1_all8_zero_for_no_positive','interpretation'])
            write_csv(args.run_dir / 'per_label.csv', label_rows,
                fields=['source','split','label','known','positive','negative','tp','fp','fn','tn','f1'])
            save(args.run_dir / 'predictions.json', output)
            save(args.run_dir / 'inventory.json', dict(records=len(records), unique_sha256=len(first_paths), packets=packets,
                roots=roots, problems=initial_problems, images_not_present_in_selected_roots='not inventoried'))
            save(args.run_dir / 'errors.json', problems)
            timings = [v['elapsed_ms'] for v in probabilities.values()]
            sessions = sorted({v['timing_session'] for v in probabilities.values()})
            save(args.run_dir / 'summary.json', dict(model=identity, labelled_groups=summary,
                unique_images_discovered=len(first_paths), unique_images_succeeded=len(probabilities),
                failed_unique_images=len(first_paths)-len(probabilities), errors=len(problems),
                timing=dict(n=len(timings), mean_ms=sum(timings)/len(timings) if timings else None,
                    median_ms=percentile(timings,.5) if timings else None, p95_ms=percentile(timings,.95) if timings else None,
                    timing_sessions=len(sessions), warmups=0, scope=config['timing'],
                    note='Single observation per unique file; resumed sessions may have different system load; not the prior repeated-measure p95.'),
                training_executed=False, labels_for_unlabelled_sources=False, original_test_previously_exposed=True,
                caveat='Train and dev metrics are not independent; packet human review is not guaranteed; no blanket all-RunPod claim.'))
            names = ['protocol.json', 'metrics.csv', 'per_label.csv', 'predictions.json', 'inventory.json', 'errors.json', 'summary.json']
            save(status_path, dict(status='complete_with_errors' if problems else 'complete', protocol_signature=signature,
                CUDA_executed=True, training_executed=False, artifact_hashes={n: sha(args.run_dir / n) for n in names}))
            print(json.dumps(dict(event='all_images_complete', status='complete_with_errors' if problems else 'complete',
                                  output=str(args.run_dir), unique_success=len(probabilities), errors=len(problems))))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=signature, training_executed=False))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preflight', 'run', 'show-results'))
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--model-run', type=Path, default=HERE / 'runs/siglip-ai-reviewed-v1-fp32')
    parser.add_argument('--packets-root', type=Path, default=HERE / 'training_packets')
    parser.add_argument('--unlabelled-roots', nargs='+', type=Path, default=list(UNLABELLED))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/siglip-all-data-eval-v1')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    paths(args)
    if args.command == 'preflight':
        preflight(args)
    elif args.command == 'run':
        run(args)
    else:
        status = read(args.run_dir / 'status.json')
        require(status['status'] in ('complete', 'complete_with_errors') and
            all(sha(contained(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()), 'Incomplete or changed output')
        print(json.dumps({k: v for k, v in status.items() if k != 'artifact_hashes'}, indent=2))
        print(json.dumps(read(args.run_dir / 'summary.json'), ensure_ascii=False, indent=2))
        print((args.run_dir / 'per_label.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    main()
