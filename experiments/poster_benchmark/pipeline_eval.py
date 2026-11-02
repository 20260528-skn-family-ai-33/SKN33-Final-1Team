"""Evaluate existing checkpoints with real PaddleOCR; never train."""
import argparse
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time

from benchlib import (ARMS, LABELS, contained, csv_rows, digest, load_test, log,
                      metrics, read, save, sha, targets, timing_orders, verify_bundle)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def source_run(value, runs):
    """Latest train, not a historical bundled checkpoint or a silent older fallback."""
    if value != 'latest':
        return Path(value).expanduser().resolve(strict=True)
    candidates = [p for p in Path(runs).glob('*/protocol.json')
                  if read(p).get('config', {}).get('command') == 'train']
    if not candidates:
        raise ValueError('No existing train run. Pass --source-run /path/to/train-run, or explicitly choose --source-run bundled')
    return max(candidates, key=lambda p: p.stat().st_mtime_ns).parent.resolve()


def validate_source(source, bundle):
    """Validate frozen selection, checkpoint, predictions and immutable source artifacts."""
    protocol = read(source/'protocol.json')
    config = protocol['config']
    signature = protocol['signature']
    status = read(source/'status.json')
    if (config['command'] != 'train' or status.get('status') != 'complete'
            or not status.get('CUDA_executed') or signature != digest(config)
            or status.get('protocol_signature') != signature):
        raise ValueError('Source must be an intact, completed CUDA train run')
    if sha(bundle/'bundle_manifest.json') != config['bundle_manifest_sha256']:
        raise ValueError('Source dataset differs from supplied bundle')
    # New evaluator lives in separate files: preserve the original training protocol.
    for name in ('benchlib.py', 'models.py', 'run_benchmark.py'):
        if sha(HERE/name) != config['script_hashes'][name]:
            raise ValueError('Original model/data code changed; use the source run code: '+name)
    selection_path = source/'selection_frozen.json'
    selection_hash = sha(selection_path)
    selection = read(selection_path)
    if (read(source/'selection_commit.json')['sha256'] != selection_hash
            or selection['protocol_signature'] != signature
            or selection['kind'] != 'dev_selected_fresh_EG2_training'
            or selection.get('test_used_for_selection') is not False):
        raise ValueError('Source frozen dev selection changed')
    selected = selection['selected']
    checkpoint = contained(source, selected['checkpoint'])
    if sha(checkpoint) != selected['checkpoint_sha256']:
        raise ValueError('Source selected EG2 checkpoint changed')
    artifacts = {}
    for arm in ARMS:
        folder = source/'arms'/arm
        commit = read(folder/'arm_complete_commit.json')
        if commit['sha256'] != sha(folder/'arm_complete.json'):
            raise ValueError('Source arm commit changed: '+arm)
        done = read(folder/'arm_complete.json')
        if done['selection_sha256'] != selection_hash:
            raise ValueError('Source arm selection changed: '+arm)
        for name, checksum in done['file_hashes'].items():
            if sha(contained(folder, name)) != checksum:
                raise ValueError('Source arm output changed: '+arm+'/'+name)
        artifacts[arm] = sha(folder/'arm_complete.json')
    return dict(protocol=protocol, selection=selection, selection_sha256=selection_hash,
                checkpoint=str(checkpoint), arm_hashes=artifacts,
                environment=read(source/'environment.json'), source_kind='completed_cuda_train')


def validate_bundled(bundle):
    """Explicitly selected original local checkpoints; never masquerade as a CUDA train."""
    selected = read(bundle/'references/eg2_selection.json')
    checkpoint = bundle/'references/eg2_full_finetune.pt'
    if sha(checkpoint) != selected['checkpoint_sha256']:
        raise ValueError('Bundled EG2 checkpoint differs from the original selection')
    return dict(protocol={'config': {'precision': 'fp32'}},
                selection=dict(kind='existing_MPS_selected_checkpoint', selected=selected,
                               test_used_for_selection=False, previously_exposed_test=True),
                selection_sha256=None, checkpoint=str(checkpoint), environment=None,
                source_kind='bundled_local_checkpoint',
                arm_hashes={'bundle_manifest.json': sha(bundle/'bundle_manifest.json'),
                            'eg2_selection.json': sha(bundle/'references/eg2_selection.json')})


class PaddleClient:
    """One loaded OCR process, with strict request/response matching and a timeout."""
    def __init__(self, command, log_path, timeout=240):
        self.timeout = timeout
        self.trace = Path(log_path).open('w', encoding='utf-8')
        self.process = None
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.trace, text=True, encoding='utf-8', bufsize=1)
            self.identity = self.receive()
            if self.identity.get('event') != 'ready':
                raise ValueError('OCR worker did not become ready')
        except BaseException:
            self.close()
            raise

    def receive(self):
        ready, _, _ = select.select([self.process.stdout], [], [], self.timeout)
        if not ready:
            raise TimeoutError('PaddleOCR timeout; inspect paddle_worker.log')
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('PaddleOCR exited; inspect paddle_worker.log')
        value = json.loads(line)
        if not value.get('ok'):
            raise RuntimeError('PaddleOCR failed: '+str(value.get('error')))
        return value

    def predict(self, path, image_id):
        self.process.stdin.write(json.dumps(dict(path=str(path), image_id=image_id))+'\n')
        self.process.stdin.flush()
        value = self.receive()
        if value.get('image_id') != image_id:
            raise ValueError('OCR response image ID differs')
        return value

    def close(self):
        if self.process is not None:
            if self.process.stdin:
                with suppress(BrokenPipeError):
                    self.process.stdin.close()
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process.stdout.close()
        self.trace.close()


def measure_sample(ocr, visual, synchronize, clock=time.perf_counter):
    """Continuous wall measurement; OCR executes on EVERY call, including repeats."""
    synchronize()
    begin = clock()
    ocr_result = ocr()
    after_ocr = clock()
    visual_result = visual()
    synchronize()
    end = clock()
    return ocr_result, visual_result, dict(ocr_ms=(after_ocr-begin)*1000,
                                          model_ms=(end-after_ocr)*1000,
                                          total_ms=(end-begin)*1000)


def timing_summary(raw):
    import numpy as np
    return {key: dict(n=len(raw), mean_ms=float(np.mean([r[key] for r in raw])),
                      median_ms=float(np.median([r[key] for r in raw])),
                      p95_ms=float(np.percentile([r[key] for r in raw], 95)))
            for key in ('ocr_ms', 'model_ms', 'total_ms')}


def show_results(runs):
    paths = [p for p in Path(runs).glob('*/protocol.json')
             if read(p).get('config', {}).get('command') == 'evaluate_pipeline']
    if not paths:
        raise ValueError('No OCR pipeline evaluation results')
    folder = max(paths, key=lambda p: p.stat().st_mtime_ns).parent
    if not (folder/'status.json').is_file() or read(folder/'status.json').get('status') != 'complete':
        raise ValueError('Latest pipeline evaluation is incomplete; inspect '+str(folder))
    print('Results: '+str(folder))
    summary = read(folder/'pipeline_summary.json')
    print('Checkpoint source: '+summary.get('config', {}).get('source_kind', 'completed_cuda_train'))
    for arm, value in summary['results'].items():
        timing = value['timing']
        print(f"{arm}: accuracy={value['test_metrics']['label_wise_accuracy']:.2%}, "
              f"OCR={timing['ocr_ms']['mean_ms']:.2f} ms, "
              f"model={timing['model_ms']['mean_ms']:.2f} ms, "
              f"total={timing['total_ms']['mean_ms']:.2f} ms, "
              f"total p95={timing['total_ms']['p95_ms']:.2f} ms")


@contextmanager
def exclusive_gpu(bundle):
    with (bundle/'.gpu_owner.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def evaluate(args, bundle, source, provenance, out):
    import numpy as np
    import torch
    from models import EG, ResNet, image, precision_context
    from run_benchmark import cuda_environment, get_model_dir, hashlib_image, release

    args.precision = provenance['protocol']['config']['precision']
    env = cuda_environment(args)
    if provenance['environment'] is not None and env != provenance['environment']:
        raise ValueError('GPU/runtime differs from source training evaluation; keep original visual venv/GPU')
    save(out/'environment.json', env)
    model_dir = get_model_dir(bundle, args.model_dir, args.cache_dir)
    rows = load_test(bundle, out/'source_selection_frozen.json', provenance['selection_sha256'])
    y = targets(rows)
    if len(rows) != 37 or int(np.isfinite(y).sum()) != 282:
        raise ValueError('Expected the original37 test posters /282 known decisions')
    if source is None and read(bundle/'references/local_results.json')['test_ids'] != [r['image_id'] for r in rows]:
        raise ValueError('Bundled reference test population/order changed')
    heads = read(bundle/'references/eg2_heads.json')['heads']
    resnet = read(bundle/'references/resnet_selection.json')
    client = PaddleClient([str(args.ocr_python), '-u', str(HERE/'paddle_service.py'),
                           '--device', args.ocr_device, '--models', str(args.ocr_models)],
                          out/'paddle_worker.log', args.ocr_timeout)
    try:
        if client.identity['device'] != args.ocr_device:
            raise ValueError('OCR worker device differs from requested device')
        save(out/'ocr_environment.json', client.identity)
        table = []
        results = {}
        for arm in ARMS:
            folder = out/'arms'/arm
            (folder/'ocr_raw').mkdir(parents=True)
            model = None
            load_started = time.perf_counter()
            try:
                if arm == ARMS[0]:
                    model = ResNet()
                    state = torch.load(bundle/'references/resnet18_full_finetune.pt', map_location='cpu', weights_only=True)
                    model.load_state_dict(state)
                    del state
                    model.to('cuda:0')
                    cutoffs = resnet['thresholds']
                else:
                    model = EG(model_dir, 'cuda:0', heads)
                    model.set_training(False)
                    if arm == ARMS[2]:
                        state = torch.load(provenance['checkpoint'], map_location='cpu', weights_only=True)
                        model.load_state_dict(state)
                        del state
                        cutoffs = provenance['selection']['selected']['thresholds']
                    else:
                        cutoffs = [heads[k]['threshold'] for k in LABELS]
                model.eval()
                for parameter in model.parameters():
                    parameter.requires_grad_(False)
                torch.cuda.synchronize()
                load_seconds = time.perf_counter()-load_started

                def visual(row):
                    im = image(bundle, row)
                    try:
                        with torch.inference_mode(), precision_context(args.precision, 'cuda:0'):
                            embedding = model.embed(im, 'cuda:0')
                            if arm == ARMS[1]:
                                features = embedding.float().cpu().numpy().astype(np.float64)
                                logits = np.stack([features @ np.asarray(heads[k]['coef']) + heads[k]['intercept']
                                                   for k in LABELS], axis=1)
                                probability = (1/(1+np.exp(-np.clip(logits, -60, 60))))[0]
                            else:
                                probability = torch.sigmoid(model.classifier(embedding).float()).cpu().numpy()[0]
                        return probability
                    finally:
                        im.close()

                def predict(row):
                    return measure_sample(
                        lambda: client.predict(contained(bundle, row['image']), row['image_id']),
                        lambda: visual(row), torch.cuda.synchronize)

                reference_path = (source/'arms'/arm/'test_probs.npy' if source is not None
                                  else bundle/'references'/f'{arm}_test_probs.npy')
                reference = np.load(reference_path, allow_pickle=False)
                source_fingerprints = (read(source/'arms'/arm/'shared_input_fingerprints.json')
                                       if source is not None else None)
                # Audit input equality outside the performance interval.
                for row in rows:
                    im = image(bundle, row)
                    try:
                        if source_fingerprints is not None and source_fingerprints[row['image_id']] != hashlib_image(im):
                            raise ValueError('Visual input differs from the source evaluation')
                    finally:
                        im.close()
                # OCR and the visual model are both loaded; warm up the full serial path.
                for row in rows[:3]:
                    predict(row)
                probabilities = np.empty((len(rows), len(LABELS)))
                raw = []
                saved_texts = []
                for repeat, index in timing_orders(len(rows), args.repeats):
                    row = rows[index]
                    ocr, probability, stages = predict(row)
                    decision = probability >= np.asarray(cutoffs)
                    difference = float(np.max(np.abs(probability-reference[index])))
                    if source is not None and (difference > 1e-5 or not np.array_equal(decision, reference[index] >= np.asarray(cutoffs))):
                        raise ValueError('Classification differs from source; no checkpoint/threshold replacement allowed')
                    if repeat == 0:
                        probabilities[index] = probability
                        save(folder/'ocr_raw'/f'{row["image_id"]}.json', ocr)
                        saved_texts.append(dict(image_id=row['image_id'], text_count=ocr['text_count']))
                    elif (float(np.max(np.abs(probability-probabilities[index]))) > 1e-5
                          or not np.array_equal(decision, probabilities[index] >= np.asarray(cutoffs))):
                        raise ValueError('Repeated classification changed')
                    record = dict(image_id=row['image_id'], repeat=repeat, **stages,
                                  ocr_engine_ms=ocr['engine_ms'], ocr_text_count=ocr['text_count'],
                                  probability_max_abs_difference=difference)
                    raw.append(record)
                    log(out, 'pipeline_sample', arm=arm, **record)
                csv_rows(folder/'timing_raw.csv', raw)
                score = metrics(y, probabilities, cutoffs)
                timing = timing_summary(raw)
                np.save(folder/'test_probs.npy', probabilities)
                save(folder/'test_metrics.json', score)
                save(folder/'test_predictions.json', [dict(image_id=row['image_id'], truth=row['labels'],
                     probabilities=probabilities[i].tolist(), predictions=(probabilities[i] >= np.asarray(cutoffs)).astype(int).tolist())
                     for i, row in enumerate(rows)])
                save(folder/'ocr_text_counts.json', saved_texts)
                save(folder/'timing_summary.json', timing)
                changed = (probabilities >= np.asarray(cutoffs)) != (reference >= np.asarray(cutoffs))
                results[arm] = dict(test_metrics=score, timing=timing, visual_load_seconds_excluded=load_seconds,
                                    source_probability_max_abs_difference=max(r['probability_max_abs_difference'] for r in raw),
                                    source_binary_predictions_equal=not bool(changed.any()),
                                    source_changed_binary_decisions=int(changed.sum()),
                                    source_changed_known_decisions=int((changed & np.isfinite(y)).sum()),
                                    cross_backend_reference=source is None)
                table.append(dict(model=arm, accuracy=score['label_wise_accuracy'], macro_f1=score['macro_f1'],
                    ocr_mean_ms=timing['ocr_ms']['mean_ms'], model_mean_ms=timing['model_ms']['mean_ms'],
                    total_mean_ms=timing['total_ms']['mean_ms'], ocr_p95_ms=timing['ocr_ms']['p95_ms'],
                    model_p95_ms=timing['model_ms']['p95_ms'], total_p95_ms=timing['total_ms']['p95_ms'],
                    samples=len(raw), known_cells=282, unknown_excluded=14,
                    ocr_device=args.ocr_device, visual_device='cuda:0', precision=args.precision))
                log(out, 'pipeline_arm_complete', arm=arm, **results[arm])
            finally:
                if model is not None:
                    del model
                release()
        return results, table
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runs = Path(os.environ.get('POSTER_RUN_DIR', str(HERE/'runs')))
    parser.add_argument('--source-run', default='latest', help='Completed train directory, latest, or bundled (original local checkpoints, no train required)')
    parser.add_argument('--bundle', type=Path, default=Path(os.environ.get('POSTER_BUNDLE_DIR', str(HERE/'assets'))))
    parser.add_argument('--run-dir', type=Path, default=runs/('pipeline-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())))
    parser.add_argument('--ocr-python', type=Path, default=Path(os.environ.get('POSTER_OCR_VENV', str(HERE/'.ocr-venv')))/'bin/python')
    parser.add_argument('--ocr-models', type=Path, default=REPO/'.runtime/paddle-models')
    parser.add_argument('--ocr-device', choices=['cpu', 'gpu:0'], default='gpu:0')
    parser.add_argument('--ocr-timeout', type=float, default=240)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--cache-dir', type=Path, default=Path(os.environ.get('HF_HOME', '/workspace/.cache/huggingface')))
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--check-source', action='store_true', help='Verify existing artifacts only; no OCR/model inference')
    parser.add_argument('--show-results', action='store_true', help='Print the latest OCR-inclusive results without inference')
    args = parser.parse_args()
    if args.repeats < 1 or not math.isfinite(args.ocr_timeout) or args.ocr_timeout <= 0:
        parser.error('Positive repeats and OCR timeout required')
    out = None
    try:
        if args.show_results:
            show_results(runs)
            return 0
        bundle = args.bundle.resolve(strict=True)
        verify_bundle(bundle)
        source = None if args.source_run == 'bundled' else source_run(args.source_run, runs)
        provenance = validate_bundled(bundle) if source is None else validate_source(source, bundle)
        if args.check_source:
            print(json.dumps(dict(status='source_verified', source_kind=provenance['source_kind'],
                  source_run=str(source) if source is not None else None,
                  checkpoint=provenance['checkpoint'], training_executed=False)))
            return 0
        destination = args.run_dir.resolve()
        if (source is not None and destination.is_relative_to(source)) or destination.is_relative_to(bundle):
            raise ValueError('Use a separate new output directory, outside source run and bundle')
        if not args.ocr_python.is_file():
            raise FileNotFoundError('Run bash setup_pipeline.sh first; OCR Python missing')
        with exclusive_gpu(bundle):
            destination.mkdir(parents=True, exist_ok=False)
            out = destination
            if source is None:
                save(out/'source_selection_frozen.json', provenance['selection'])
            else:
                shutil.copyfile(source/'selection_frozen.json', out/'source_selection_frozen.json')
            provenance['selection_sha256'] = sha(out/'source_selection_frozen.json')
            config = dict(command='evaluate_pipeline', source_run=str(source) if source is not None else None,
                          source_kind=provenance['source_kind'], training_executed=False,
                          source_protocol_sha256=sha(source/'protocol.json') if source is not None else None,
                          source_selection_sha256=provenance['selection_sha256'],
                          source_checkpoint_sha256=provenance['selection']['selected']['checkpoint_sha256'],
                          ocr_device=args.ocr_device, visual_device='cuda:0', repeats=args.repeats,
                          warmups=3, batch=1, sequential=True, cached_OCR_used=False,
                          timing=dict(ocr_ms='full-resolution decode + PaddleOCR + IPC/serialization',
                                      model_ms='visual decode +448 letterbox + processor/inference/head + sync',
                                      total_ms='continuous wall time from OCR request to classification completion'),
                          excluded_from_timing=['download', 'load', 'warmup', 'artifact disk writes', 'training'],
                          OCR_accuracy_measured=False, OCR_text_used_for_visual_classification=False,
                          scripts={name: sha(HERE/name) for name in ('pipeline_eval.py', 'paddle_service.py')},
                          source_artifacts=provenance['arm_hashes'])
            save(out/'protocol.json', dict(config=config, signature=digest(config)))
            log(out, 'pipeline_started', source_kind=provenance['source_kind'],
                source_run=config['source_run'], training_executed=False)
            results, table = evaluate(args, bundle, source, provenance, out)
            verify_bundle(bundle)
            if sha(out/'source_selection_frozen.json') != config['source_selection_sha256']:
                raise ValueError('Frozen selection changed during evaluation')
            if source is None:
                validate_bundled(bundle)
            else:
                validate_source(source, bundle)
            csv_rows(out/'comparison_pipeline.csv', table)
            save(out/'pipeline_summary.json', dict(results=results, config=config, test_images=37,
                 known_cells=282, unknown_excluded=14, previous_test_exposure=True,
                 PaddleOCR_included=True, OCR_accuracy_measured=False, training_executed=False))
            save(out/'status.json', dict(status='complete', CUDA_executed=True, PaddleOCR_executed=True,
                                        training_executed=False, protocol_signature=digest(config)))
            print('\nmodel | accuracy | OCR ms | model ms | total ms | total p95 ms')
            for row in table:
                print(f"{row['model']} | {row['accuracy']:.2%} | {row['ocr_mean_ms']:.2f} | {row['model_mean_ms']:.2f} | {row['total_mean_ms']:.2f} | {row['total_p95_ms']:.2f}")
            log(out, 'pipeline_complete', run=str(out), training_executed=False)
        return 0
    except Exception as exc:
        if out is not None:
            import traceback
            save(out/'failure.json', dict(status='failed', reason=repr(exc), traceback=traceback.format_exc()))
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
