"""Train frozen-encoder multilabel heads and measure real batch-one CUDA latency."""
from pathlib import Path
from datetime import datetime, timezone
from importlib import metadata
import argparse
import fcntl
import gc
import json
import math
import os
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'poster_benchmark'
sys.path.insert(0, str(SOURCE))
from benchlib import (LABELS, SEED, contained, csv_rows, digest, load_test, log, metrics,
                      read, save, sha, targets, thresholds, timing_orders, verify_bundle)

MODEL_FOLDERS = {'siglip2': 'siglip2-base-384', 'convnextv2': 'convnextv2-tiny-384'}
PACKAGES = ('torch', 'torchvision', 'transformers', 'timm', 'numpy', 'scipy', 'scikit-learn', 'Pillow', 'safetensors')


def bundle_data(root):
    if read(root / 'bundle_manifest.json') != read(SOURCE / 'bundle_manifest.json'):
        raise ValueError('Use the original approved bundle; manifest differs from the reference')
    return verify_bundle(root)


def model_packet(folder, kind):
    names = ['config.json', 'model.safetensors']
    if kind == 'siglip2':
        names.append('preprocessor_config.json')
    files = {name: {'bytes': (folder / name).stat().st_size, 'sha256': sha(folder / name)} for name in names}
    # This pins downloaded bytes, not a claim of remote hash verification.
    revision = None
    meta = folder / '.cache/huggingface/download/model.safetensors.metadata'
    if meta.is_file():
        revision = meta.read_text().splitlines()[0]
    return dict(local_dir=str(folder), downloaded_revision=revision, files=files)


def cuda_environment(precision):
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA required for smoke/run; validate and unit tests work locally')
    if precision == 'bf16' and not torch.cuda.is_bf16_supported():
        raise RuntimeError('BF16 is not supported on this GPU; choose fp32 or fp16 explicitly')
    torch.set_num_threads(4)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    torch.cuda.set_device(0)
    prop = torch.cuda.get_device_properties(0)
    return dict(device='cuda:0', GPU=prop.name, GPU_total_bytes=prop.total_memory,
                compute_capability=list(torch.cuda.get_device_capability()),
                CUDA_runtime=torch.version.cuda, python=sys.version,
                precision=precision, head_precision='fp32', TF32=False,
                cudnn_benchmark=False, torch_threads=4,
                versions={p: metadata.version(p) for p in PACKAGES})


def synchronize(device):
    import torch
    if str(device).startswith('cuda'):
        torch.cuda.synchronize(device)


def release():
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def save_array(path, array):
    import numpy as np
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('wb') as stream:
        np.save(stream, array, allow_pickle=False)
    tmp.replace(path)


def extract(encoder, root, rows, out, stage):
    import numpy as np
    import torch
    features = []
    for index, row in enumerate(rows, 1):
        with torch.inference_mode():
            x = encoder.prepare(contained(root, row['image'])).to(encoder.device)
            feature = encoder(x).cpu().numpy()[0]
        if not np.isfinite(feature).all():
            raise ValueError('Nonfinite feature: ' + row['image_id'])
        features.append(feature)
        if index % 20 == 0 or index == len(rows):
            log(out, 'feature_progress', split=stage, completed=index, total=len(rows))
    return np.asarray(features, dtype=np.float32)


def fit_heads(train_x, train_y, dev_x, dev_y, cs):
    import numpy as np
    from scipy.special import expit
    from sklearn.linear_model import LogisticRegression
    candidates = []
    best = None
    for c in cs:
        for balanced in (False, True):
            coefs, intercepts = [], []
            for j in range(len(LABELS)):
                mask = np.isfinite(train_y[:, j])
                if len(np.unique(train_y[mask, j])) != 2:
                    raise ValueError('Each label needs known positive/negative training examples: ' + LABELS[j])
                clf = LogisticRegression(C=c, class_weight='balanced' if balanced else None,
                                         solver='liblinear', max_iter=2000, random_state=SEED)
                clf.fit(train_x[mask], train_y[mask, j].astype(int))
                if int(clf.n_iter_.max()) >= 2000:
                    raise RuntimeError('Head did not converge; do not silently score this candidate')
                coefs.append(clf.coef_[0].tolist())
                intercepts.append(float(clf.intercept_[0]))
            prob = expit(dev_x @ np.asarray(coefs).T + np.asarray(intercepts))
            cutoffs = thresholds(dev_y, prob)
            score = metrics(dev_y, prob, cutoffs)
            candidate = dict(C=c, class_weight='balanced' if balanced else None,
                             thresholds=cutoffs, dev_metrics=score)
            candidates.append(candidate)
            key = (score['macro_f1'], score['macro_average_precision'], -c, not balanced)
            if best is None or key > best[0]:
                best = (key, dict(**candidate, coef=coefs, intercept=intercepts))
    return best[1], candidates


def selection_bundle(models, selections, signature):
    if set(selections) != set(models):
        raise ValueError('All requested models must be selected before test access')
    return dict(protocol_signature=signature, models=selections,
                test_used_for_selection=False, previously_exposed_test=True,
                training_scope='frozen_backbones_per_label_logistic_heads', labels=LABELS)


def verify_selection(out, signature):
    selection_path = out / 'selection_frozen.json'
    selection = read(selection_path)
    commit = read(out / 'selection_commit.json')
    if commit['sha256'] != sha(selection_path) or selection['protocol_signature'] != signature:
        raise ValueError('Selection hash/signature mismatch')
    for kind in selection['models']:
        for name, checksum in selection['models'][kind]['artifact_hashes'].items():
            if sha(out / kind / name) != checksum:
                raise ValueError('Training artifact changed: ' + kind + '/' + name)
    return selection


def timed_prediction(encoder, head, root, row):
    import torch
    synchronize(encoder.device)
    started = time.perf_counter()
    x = encoder.prepare(contained(root, row['image']))
    preprocessed = time.perf_counter()
    with torch.inference_mode():
        x = x.to(encoder.device)
        feature = encoder(x)
        probability = torch.sigmoid(head(feature)).cpu().numpy()[0]
    synchronize(encoder.device)
    ended = time.perf_counter()
    return probability, dict(decode_preprocess_ms=(preprocessed - started) * 1000,
                             transfer_model_head_ms=(ended - preprocessed) * 1000,
                             total_ms=(ended - started) * 1000)


def benchmark(encoder, head, root, rows, cutoffs, out, repeats, warmups):
    import numpy as np
    # All per-image work is executed every request; only model/normalizers stay loaded.
    for _ in range(warmups):
        timed_prediction(encoder, head, root, rows[0])
    records, probabilities = [], [None] * len(rows)
    for repeat, index in timing_orders(len(rows), repeats):
        row = rows[index]
        probability, timing = timed_prediction(encoder, head, root, row)
        if not np.isfinite(probability).all():
            raise ValueError('Nonfinite prediction')
        if probabilities[index] is None:
            probabilities[index] = probability
        elif not np.array_equal(probability >= cutoffs, probabilities[index] >= cutoffs):
            raise ValueError('Repeated inference changed a label decision')
        records.append(dict(image_id=row['image_id'], repeat=repeat, **timing))
        if len(records) % len(rows) == 0:
            csv_rows(out / 'timing_raw.csv', records)
            log(out.parent, 'timing_progress', model=out.name,
                completed=len(records), total=len(rows) * repeats)
    prob = np.asarray(probabilities)
    score = metrics(targets(rows), prob, cutoffs)
    summary = {key: dict(n=len(records), mean_ms=float(np.mean([r[key] for r in records])),
                        median_ms=float(np.median([r[key] for r in records])),
                        p95_ms=float(np.percentile([r[key] for r in records], 95)))
               for key in ('decode_preprocess_ms', 'transfer_model_head_ms', 'total_ms')}
    predictions = [dict(image_id=row['image_id'], labels=row['labels'],
                        probabilities=prob[i].tolist(), predictions=(prob[i] >= cutoffs).astype(int).tolist())
                   for i, row in enumerate(rows)]
    save(out / 'test_predictions.json', predictions)
    save(out / 'test_metrics.json', score)
    save(out / 'timing_summary.json', summary)
    return dict(metrics=score, timing=summary,
                goal=dict(label_accuracy_90=score['label_wise_accuracy'] >= .9,
                          median_30ms=summary['total_ms']['median_ms'] <= 30,
                          p95_50ms=summary['total_ms']['p95_ms'] <= 50,
                          all_met=score['label_wise_accuracy'] >= .9 and
                          summary['total_ms']['median_ms'] <= 30 and summary['total_ms']['p95_ms'] <= 50))


def execute(args, environment, signature, td, encoder_loader=None):
    import numpy as np
    import torch
    from encoders import load_encoder, make_head
    encoder_loader = encoder_loader or load_encoder
    train = [r for r in td if r['split'] == 'train']
    dev = [r for r in td if r['split'] == 'dev']
    trained_models = []
    if args.command == 'smoke':
        for kind in args.models:
            encoder = encoder_loader(kind, args.models_root / MODEL_FOLDERS[kind],
                                     environment['device'], args.precision, 384)
            features = extract(encoder, args.bundle, train[:2], args.run_dir, 'smoke_train_only')
            if features.shape != (2, encoder.dimension):
                raise ValueError('Smoke shape failed')
            log(args.run_dir, 'smoke_model_complete', model=kind, features=list(features.shape),
                backbone_parameters=sum(p.numel() for p in encoder.parameters()),
                pretrained_backbone_trained=False)
            del encoder
            release()
        return dict(status='smoke_complete', test_accessed=False, training_executed=False)
    selected_path = args.run_dir / 'selection_frozen.json'
    if not selected_path.exists():
        selected = {}
        for kind in args.models:
            folder = args.run_dir / kind
            folder.mkdir(exist_ok=True)
            checkpoint = folder / 'head_selection.json'
            if checkpoint.exists():
                head_selection = read(checkpoint)
                if head_selection['protocol_signature'] != signature:
                    raise ValueError('Cached head protocol changed')
                for name, checksum in head_selection['artifact_hashes'].items():
                    if sha(folder / name) != checksum:
                        raise ValueError('Cached head artifact changed')
            else:
                encoder = encoder_loader(kind, args.models_root / MODEL_FOLDERS[kind],
                                         environment['device'], args.precision, 384)
                train_x = extract(encoder, args.bundle, train, args.run_dir, 'train')
                dev_x = extract(encoder, args.bundle, dev, args.run_dir, 'dev')
                selection, candidates = fit_heads(train_x, targets(train), dev_x, targets(dev), args.cs)
                trained_models.append(kind)
                save_array(folder / 'train_features.npy', train_x)
                save_array(folder / 'dev_features.npy', dev_x)
                save(folder / 'candidates.json', candidates)
                head_selection = dict(**selection, protocol_signature=signature,
                                      training_images=len(train), dev_images=len(dev),
                                      backbone_trained=False,
                                      artifact_hashes={name: sha(folder / name) for name in
                                                      ('train_features.npy', 'dev_features.npy', 'candidates.json')})
                save(checkpoint, head_selection)
                del encoder
                release()
            selected[kind] = {**head_selection, 'artifact_hashes': {
                **head_selection['artifact_hashes'], 'head_selection.json': sha(checkpoint)}}
            log(args.run_dir, 'head_selected', model=kind, C=head_selection['C'],
                dev_macro_f1=head_selection['dev_metrics']['macro_f1'])
        save(selected_path, selection_bundle(args.models, selected, signature))
        save(args.run_dir / 'selection_commit.json', dict(sha256=sha(selected_path)))
        log(args.run_dir, 'all_selections_frozen_before_test', sha256=sha(selected_path))
    selection = verify_selection(args.run_dir, signature)
    # This is the only semantic access to held-out test labels/images.
    rows = load_test(args.bundle, selected_path, sha(selected_path))
    results = {}
    for kind in args.models:
        folder = args.run_dir / kind
        done = folder / 'evaluation_complete.json'
        if done.exists():
            cached = read(done)
            if cached['selection_sha256'] != sha(selected_path):
                raise ValueError('Cached evaluation selection changed')
            for name, checksum in cached['artifact_hashes'].items():
                if sha(folder / name) != checksum:
                    raise ValueError('Cached evaluation artifact changed')
            results[kind] = cached['result']
            continue
        encoder = encoder_loader(kind, args.models_root / MODEL_FOLDERS[kind],
                                 environment['device'], args.precision, 384)
        head = make_head(selection['models'][kind], environment['device'])
        if str(environment['device']).startswith('cuda'):
            torch.cuda.reset_peak_memory_stats()
        result = benchmark(encoder, head, args.bundle, rows,
                           np.asarray(selection['models'][kind]['thresholds']), folder,
                           args.repeats, args.warmups)
        result['cuda_peak_allocated_bytes'] = (torch.cuda.max_memory_allocated()
                                              if str(environment['device']).startswith('cuda') else None)
        save(done, dict(selection_sha256=sha(selected_path), result=result,
                        artifact_hashes={name: sha(folder / name) for name in
                                        ('test_predictions.json', 'test_metrics.json', 'timing_raw.csv', 'timing_summary.json')}))
        results[kind] = result
        log(args.run_dir, 'model_complete', model=kind, accuracy=result['metrics']['label_wise_accuracy'],
            p95_ms=result['timing']['total_ms']['p95_ms'])
        del encoder, head
        release()
    all_negative = metrics(targets(rows), np.zeros((len(rows), len(LABELS))), [.5] * len(LABELS))
    csv_rows(args.run_dir / 'comparison.csv', [dict(
        model=kind, label_accuracy=result['metrics']['label_wise_accuracy'],
        macro_f1=result['metrics']['macro_f1'],
        mean_ms=result['timing']['total_ms']['mean_ms'],
        median_ms=result['timing']['total_ms']['median_ms'],
        p95_ms=result['timing']['total_ms']['p95_ms'], goals_met=result['goal']['all_met'])
        for kind, result in results.items()])
    verify_selection(args.run_dir, signature)
    return dict(status='complete', results=results, all_negative_baseline=all_negative,
                training_executed=bool(trained_models), heads_fitted_this_invocation=trained_models,
                head_training_completed=True, backbone_training_executed=False,
                OCR_executed=False, OCR_in_timing=False, batch=1, test_images=len(rows),
                previously_exposed_test=True, test_used_for_selection=False,
                note='Same exposed37 test posters; not a new independent generalization estimate. '
                     'No OCR/network/DB/model-loading time. No TensorRT applied.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('validate', 'smoke', 'run', 'show-results'))
    parser.add_argument('--bundle', type=Path, default=Path(os.environ.get('POSTER_BUNDLE_DIR', str(SOURCE / 'assets'))))
    parser.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    parser.add_argument('--models', nargs='+', choices=tuple(MODEL_FOLDERS), default=list(MODEL_FOLDERS))
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    parser.add_argument('--precision', choices=('fp32', 'fp16', 'bf16'), default='fp32')
    parser.add_argument('--cs', type=float, nargs='+', default=[.01, .1, 1., 10.])
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if len(set(args.models)) != len(args.models) or args.repeats < 1 or args.warmups < 1 or any(not math.isfinite(c) or c <= 0 for c in args.cs):
        parser.error('Unique models, positive C/repeats/warmups required')
    args.bundle, args.models_root, args.run_dir = (p.expanduser().resolve() for p in (args.bundle, args.models_root, args.run_dir))
    if args.command == 'show-results':
        print(json.dumps(read(args.run_dir / 'comparison_summary.json'), ensure_ascii=False, indent=2))
        return 0
    _, _, td = bundle_data(args.bundle)
    packets = {kind: model_packet(args.models_root / MODEL_FOLDERS[kind], kind) for kind in args.models}
    if args.command == 'validate':
        print(json.dumps(dict(status='validated', models=packets, images=187, CUDA_executed=False), ensure_ascii=False))
        return 0
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    environment = cuda_environment(args.precision)
    protocol = dict(bundle_manifest_sha256=sha(args.bundle / 'bundle_manifest.json'),
                    models=packets, model_order=args.models, environment=environment,
                    cs=args.cs, image_size=384, interpolation='bicubic', preserve_full_image=True,
                    precision=args.precision, warmups=args.warmups, repeats=args.repeats, seed=SEED,
                    selection='dev_macro_f1_then_macro_AP_then_smaller_C_then_unbalanced',
                    script_hashes={str(p.relative_to(HERE.parent)): sha(p) for p in
                                   [HERE / 'encoders.py', HERE / 'run_experiment.py', SOURCE / 'benchlib.py']})
    signature = digest(protocol)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stored = args.run_dir / 'protocol.json'
        if stored.exists():
            if not args.resume:
                raise ValueError('Run directory already exists; use --resume with identical settings')
            if read(stored)['signature'] != signature:
                raise ValueError('Input/model/code/GPU/settings changed; use a new run directory')
        else:
            save(stored, dict(signature=signature, config=protocol))
            save(args.run_dir / 'environment.json', environment)
        log(args.run_dir, 'started', command=args.command, precision=args.precision)
        try:
            summary = execute(args, environment, signature, td)
            bundle_data(args.bundle)
            if {kind: model_packet(args.models_root / MODEL_FOLDERS[kind], kind) for kind in args.models} != packets:
                raise ValueError('Model files changed during execution')
            save(args.run_dir / 'comparison_summary.json', summary)
            save(args.run_dir / 'status.json', dict(status=summary['status'], protocol_signature=signature,
                                                    CUDA_executed=True, OCR_executed=False))
            log(args.run_dir, summary['status'], run=str(args.run_dir))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(args.run_dir / 'status.json', dict(status='failed', protocol_signature=signature))
            raise
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
