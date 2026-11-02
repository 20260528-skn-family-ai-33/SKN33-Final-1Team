"""DINOv2 S/B frozen-feature heads versus the completed SigLIP2 control.

No installers, remote inference, backbone training, or test-based selection.
"""
from pathlib import Path
import argparse
import fcntl
import math
import os
import sys
import traceback

import run_experiment as common
from benchlib import (LABELS, SEED, contained, csv_rows, digest, load_test, log,
                      read, save, sha, targets)

HERE = Path(__file__).resolve().parent
SPECS = {
    'dinov2_small': dict(repo='facebook/dinov2-small', folder='dinov2-small', dimension=384, heads=6),
    'dinov2_base': dict(repo='facebook/dinov2-base', folder='dinov2-base', dimension=768, heads=12),
}
MODEL_FILES = ('config.json', 'preprocessor_config.json', 'model.safetensors')
EVAL_FILES = ('test_predictions.json', 'test_metrics.json', 'timing_raw.csv', 'timing_summary.json')
CONTROL = 'siglip2_full_finetune'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_packet(folder, names):
    return {name: dict(bytes=(folder / name).stat().st_size, sha256=sha(folder / name)) for name in names}


def download(models_root):
    """Reuse completed files; interrupted downloads keep their first resolved revision."""
    from huggingface_hub import HfApi, snapshot_download
    for kind, spec in SPECS.items():
        folder = models_root / spec['folder']
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / '.dinov2-download.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest_path = folder / 'download_manifest.json'
            if manifest_path.exists():
                manifest = read(manifest_path)
                require(manifest['repo'] == spec['repo'] and manifest['files'] == file_packet(folder, MODEL_FILES),
                        'Downloaded model changed: ' + str(folder))
                print(kind + ': already downloaded and verified', flush=True)
                continue
            plan_path = folder / 'download_plan.json'
            if plan_path.exists():
                plan = read(plan_path)
                require(plan['repo'] == spec['repo'], 'Download plan repository mismatch')
            else:
                revision = HfApi().model_info(spec['repo']).sha
                require(isinstance(revision, str) and len(revision) == 40, 'Expected immutable Hub commit SHA')
                plan = dict(repo=spec['repo'], revision=revision)
                save(plan_path, plan)
            snapshot_download(repo_id=spec['repo'], revision=plan['revision'], local_dir=str(folder),
                              allow_patterns=list(MODEL_FILES) + ['README.md', 'LICENSE*'])
            save(manifest_path, dict(**plan, files=file_packet(folder, MODEL_FILES)))
            print(kind + ': downloaded ' + plan['revision'], flush=True)


def model_packet(models_root, kind):
    spec = SPECS[kind]
    folder = models_root / spec['folder']
    manifest = read(folder / 'download_manifest.json')
    require(manifest['repo'] == spec['repo'], 'Wrong DINOv2 repository')
    require(manifest['files'] == file_packet(folder, MODEL_FILES), 'DINOv2 file hash mismatch')
    config, processor = read(folder / 'config.json'), read(folder / 'preprocessor_config.json')
    require(config.get('model_type') == 'dinov2' and config.get('hidden_size') == spec['dimension']
            and config.get('patch_size') == 14 and config.get('num_hidden_layers') == 12
            and config.get('num_attention_heads') == spec['heads'], 'Unexpected DINOv2 architecture')
    require(processor.get('image_mean') == [.485, .456, .406]
            and processor.get('image_std') == [.229, .224, .225], 'Unexpected DINOv2 normalization')
    return dict(local_dir=str(folder), **manifest)


def load_dino(folder, kind, device, size, pooling):
    import torch
    from torch.nn import functional as F
    from transformers import AutoModel
    from encoders import Encoder

    class DinoEncoder(Encoder):
        def forward(self, x):
            tokens = self.backbone(pixel_values=x).last_hidden_state.float()
            cls = tokens[:, 0]
            feature = cls if self.pooling == 'cls' else torch.cat((cls, tokens[:, 1:].mean(dim=1)), dim=1)
            require(feature.ndim == 2 and feature.shape[1] == self.dimension, 'DINOv2 feature shape mismatch')
            return F.normalize(feature, p=2, dim=-1)

    require(size > 0 and size % 14 == 0, 'DINOv2 size must be a multiple of patch size 14')
    require(pooling in ('cls', 'cls_mean'), 'Unknown pooling')
    model, loading = AutoModel.from_pretrained(
        str(folder), local_files_only=True, trust_remote_code=False, use_safetensors=True,
        dtype=torch.float32, attn_implementation='sdpa', output_loading_info=True)
    require(not any(loading.get(k) for k in ('missing_keys', 'unexpected_keys', 'mismatched_keys', 'error_msgs')),
            'DINOv2 checkpoint keys do not match: ' + str(loading))
    require(model.config.model_type == 'dinov2' and model.config.hidden_size == SPECS[kind]['dimension'],
            'Wrong DINOv2 model config')
    processor = read(folder / 'preprocessor_config.json')
    dimension = model.config.hidden_size * (2 if pooling == 'cls_mean' else 1)
    encoder = DinoEncoder(model, dimension, processor['image_mean'], processor['image_std'],
                          size, device, 'fp32', kind)
    encoder.pooling = pooling
    return encoder.eval().requires_grad_(False)


def control_inputs(args):
    import finetune_siglip as ft
    baseline, head = ft.assert_baseline(args.baseline)
    prior = read(args.prior_run / 'protocol.json')
    status = read(args.prior_run / 'status.json')
    require(status['status'] == 'complete' and prior['signature'] == digest(prior['config'])
            and status['protocol_signature'] == prior['signature'], 'Completed unchanged SigLIP2 control required')
    selected = read(args.prior_run / 'selection_frozen.json')
    ft.validate_checkpoint(args.prior_run, selected)
    require(selected['protocol_signature'] == prior['signature'], 'Control selection protocol mismatch')
    for name, checksum in prior['config']['script_hashes'].items():
        require(sha(HERE / name) == checksum, 'Original SigLIP2 source changed: ' + name)
    require(prior['config']['baseline_protocol_sha256'] == sha(args.baseline / 'protocol.json')
            and prior['config']['baseline_selection_sha256'] == sha(args.baseline / 'selection_frozen.json')
            and prior['config']['model_packet'] == baseline['config']['models']['siglip2'],
            'Control and frozen baseline do not match')
    require(common.model_packet(args.model_dir, 'siglip2') == prior['config']['model_packet'],
            'SigLIP2 downloaded weights changed')
    return head, selected


def verify_files(folder, hashes):
    for name, checksum in hashes.items():
        require(sha(contained(folder, name)) == checksum, 'Artifact changed: ' + str(folder / name))


def fit_model(args, kind, rows, signature):
    import numpy as np
    train = [r for r in rows if r['split'] == 'train']
    dev = [r for r in rows if r['split'] == 'dev']
    folder = args.run_dir / kind
    folder.mkdir(exist_ok=True)
    done = folder / 'head_selection.json'
    if done.exists():
        selected = read(done)
        require(selected['protocol_signature'] == signature, 'Cached head protocol changed')
        verify_files(folder, selected['artifact_hashes'])
        return dict(**selected, selection_sha256=sha(done))
    encoder = load_dino(args.models_root / SPECS[kind]['folder'], kind, args.device, args.size, 'cls_mean')
    # CLS and CLS+mean share one backbone forward; normalize each candidate separately.
    train_joint = common.extract(encoder, args.bundle, train, args.run_dir, kind + '_train')
    dev_joint = common.extract(encoder, args.bundle, dev, args.run_dir, kind + '_dev')
    del encoder
    common.release()
    options, trials = [], []
    for pooling in ('cls', 'cls_mean'):
        width = SPECS[kind]['dimension'] * (2 if pooling == 'cls_mean' else 1)
        tx, dx = train_joint[:, :width].copy(), dev_joint[:, :width].copy()
        tx /= np.maximum(np.linalg.norm(tx, axis=1, keepdims=True), 1e-12)
        dx /= np.maximum(np.linalg.norm(dx, axis=1, keepdims=True), 1e-12)
        best, candidates = common.fit_heads(tx, targets(train), dx, targets(dev), args.cs)
        options.append(dict(**best, pooling=pooling))
        trials.extend(dict(**c, pooling=pooling) for c in candidates)
    selected = max(options, key=lambda c: (c['dev_metrics']['macro_f1'],
        c['dev_metrics']['macro_average_precision'], -c['C'], c['pooling'] == 'cls'))
    common.save_array(folder / 'train_features_cls_mean.npy', train_joint)
    common.save_array(folder / 'dev_features_cls_mean.npy', dev_joint)
    save(folder / 'candidates.json', trials)
    artifacts = ('train_features_cls_mean.npy', 'dev_features_cls_mean.npy', 'candidates.json')
    selected = dict(**selected, protocol_signature=signature, backbone_trained=False,
                    training_images=len(train), dev_images=len(dev),
                    artifact_hashes={name: sha(folder / name) for name in artifacts})
    save(done, selected)
    log(args.run_dir, 'dino_head_selected', model=kind, pooling=selected['pooling'],
        C=selected['C'], dev_accuracy=selected['dev_metrics']['label_wise_accuracy'],
        dev_macro_f1=selected['dev_metrics']['macro_f1'])
    return dict(**selected, selection_sha256=sha(done))


def verify_selection(args, signature):
    selected = common.verify_selection(args.run_dir, signature)
    require(set(selected['models']) == {CONTROL, *SPECS}, 'All three models must freeze before test access')
    for kind in SPECS:
        require(sha(args.run_dir / kind / 'head_selection.json') == selected['models'][kind]['selection_sha256'],
                'Selected DINOv2 head changed')
    require(selected['control_selection_sha256'] == sha(args.prior_run / 'selection_frozen.json'),
            'Original control selection changed')
    return selected


def evaluation_cache(args, kind, selection_sha):
    folder = args.run_dir / kind
    done = folder / 'complete.json'
    if not done.exists():
        return None
    cached = read(done)
    require(cached['selection_sha256'] == selection_sha, 'Cached evaluation selection changed')
    verify_files(folder, cached['artifact_hashes'])
    return cached['result']


def compare_labels(args, predictions):
    reference = predictions[CONTROL]
    records, changes = [], []
    for kind in SPECS:
        current = predictions[kind]
        require(len(reference) == len(current) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
                for a, b in zip(reference, current)), 'Comparison image/label order differs')
        for j, label in enumerate(LABELS):
            row = dict(model=kind, label=label)
            for prefix, items in (('prior', reference), ('dino', current)):
                counts = dict(tp=0, fp=0, fn=0, tn=0)
                for item in items:
                    y, pred = item['labels'][label], item['predictions'][j]
                    if y is not None:
                        counts[('tp' if pred else 'fn') if y else ('fp' if pred else 'tn')] += 1
                row.update({prefix + '_' + k: v for k, v in counts.items()})
            row.update(fp_delta=row['dino_fp'] - row['prior_fp'], fn_delta=row['dino_fn'] - row['prior_fn'])
            records.append(row)
            for a, b in zip(reference, current):
                y = a['labels'][label]
                if y is not None and a['predictions'][j] != b['predictions'][j]:
                    changes.append(dict(model=kind, image_id=a['image_id'], label=label, truth=y,
                        prior_prediction=a['predictions'][j], dino_prediction=b['predictions'][j],
                        change='recovered' if b['predictions'][j] == y else 'regressed'))
    csv_rows(args.run_dir / 'per_label_comparison.csv', records)
    save(args.run_dir / 'error_changes.json', changes)


def evaluate(args, selected, baseline_head):
    import numpy as np
    import finetune_siglip as ft
    from encoders import make_head
    from safetensors.torch import load_file
    selection_path = args.run_dir / 'selection_frozen.json'
    selection_sha = sha(selection_path)
    rows = load_test(args.bundle, selection_path, selection_sha)
    results, predictions = {}, {}
    for kind in (CONTROL, *SPECS):
        candidate = selected['models'][kind]
        folder = args.run_dir / kind
        folder.mkdir(exist_ok=True)
        result = evaluation_cache(args, kind, selection_sha)
        if result is None:
            if kind == CONTROL:
                encoder, head = ft.build_models(args.model_dir, args.device, baseline_head)
                ft.load_state(encoder, head, load_file(str(contained(args.prior_run, candidate['checkpoint']))))
                encoder.eval().requires_grad_(False)
                head.eval().requires_grad_(False)
            else:
                encoder = load_dino(args.models_root / SPECS[kind]['folder'], kind, args.device,
                                    args.size, candidate['pooling'])
                head = make_head(candidate, args.device)
            result = common.benchmark(encoder, head, args.bundle, rows, np.asarray(candidate['thresholds']),
                                      folder, args.repeats, args.warmups)
            result['goal'] = dict(accuracy_90=result['metrics']['label_wise_accuracy'] >= .9,
                p95_50=result['timing']['total_ms']['p95_ms'] <= 50,
                all_met=result['metrics']['label_wise_accuracy'] >= .9 and result['timing']['total_ms']['p95_ms'] <= 50)
            if kind == CONTROL:
                old = read(args.prior_run / CONTROL / 'test_predictions.json')
                now = read(folder / 'test_predictions.json')
                require(len(old) == len(now) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
                        and a['predictions'] == b['predictions'] for a, b in zip(old, now)),
                        'Previous SigLIP2 control decisions did not reproduce')
            save(folder / 'complete.json', dict(selection_sha256=selection_sha, result=result,
                artifact_hashes={name: sha(folder / name) for name in EVAL_FILES}))
            del encoder, head
            common.release()
        results[kind] = result
        predictions[kind] = read(folder / 'test_predictions.json')
    csv_rows(args.run_dir / 'comparison.csv', [dict(model=kind,
        accuracy=value['metrics']['label_wise_accuracy'], macro_f1=value['metrics']['macro_f1'],
        mean_ms=value['timing']['total_ms']['mean_ms'], median_ms=value['timing']['total_ms']['median_ms'],
        p95_ms=value['timing']['total_ms']['p95_ms'], accuracy_90=value['goal']['accuracy_90'],
        p95_50=value['goal']['p95_50']) for kind, value in results.items()])
    compare_labels(args, predictions)
    summary = dict(results=results, dev_selected_dino=selected['dev_selected_dino'],
        goal_met=results[selected['dev_selected_dino']]['goal']['all_met'],
        goal_by_model={k: v['goal']['all_met'] for k, v in results.items()},
        test_used_for_selection=False, previously_exposed_test=True,
        backbone_training_executed=False, head_training_executed=True,
        note='Same exposed37 test; not an independent generalization estimate. DINOv2 frozen backbone + new heads; '
             'SigLIP2 previously fine-tuned control. DINOv2 input392, SigLIP2 input384. Decode/preprocess/transfer/'
             'vision/head included; OCR/network/DB/model loading/TensorRT excluded.')
    save(args.run_dir / 'comparison_summary.json', summary)
    return summary


def run(args):
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    args.device = 'cuda:0'
    environment = common.cuda_environment('fp32')
    _, _, rows = common.bundle_data(args.bundle)
    baseline_head, control = control_inputs(args)
    require(environment == read(args.baseline / 'environment.json'), 'Reuse the original baseline GPU/runtime')
    packets = {kind: model_packet(args.models_root, kind) for kind in SPECS}
    sources = ('dinov2_compare.py', 'run_dinov2.sh', 'encoders.py', 'run_experiment.py', 'finetune_siglip.py')
    config = dict(environment=environment, models=packets,
        baseline_protocol_sha256=sha(args.baseline / 'protocol.json'),
        prior_protocol_sha256=sha(args.prior_run / 'protocol.json'),
        control_selection_sha256=sha(args.prior_run / 'selection_frozen.json'),
        control_checkpoint_sha256=sha(contained(args.prior_run, control['checkpoint'])),
        bundle_sha256=sha(args.bundle / 'bundle_manifest.json'),
        input=dict(dino_size=args.size, siglip2_size=384, geometry='RGB_EXIF_gray128_letterbox_bicubic'),
        pooling=['cls', 'cls_mean'], cs=args.cs, class_weights=[None, 'balanced'],
        thresholds='per_label_dev_F1_0_to_1_step_.05',
        head_pooling_selection='dev_macro_F1,macro_AP,smaller_C,CLS',
        dino_model_selection='dev_accuracy,macro_F1,macro_AP,small',
        warmups=args.warmups, repeats=args.repeats, precision='fp32', seed=SEED,
        script_hashes={name: sha(HERE / name) for name in sources},
        benchlib_sha256=sha(common.SOURCE / 'benchlib.py'))
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = args.run_dir / 'protocol.json'
        if path.exists():
            previous = read(path)
            require(args.resume and previous['signature'] == signature and digest(previous['config']) == signature,
                    'Existing run requires --resume with identical code/data/GPU/settings; use a new run directory for changes')
        else:
            require(not any(p.name != '.lock' for p in args.run_dir.iterdir()), 'Run directory must be empty')
            save(path, dict(signature=signature, config=config))
            save(args.run_dir / 'environment.json', environment)
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            verify_selection(args, signature)
            selection_sha = sha(args.run_dir / 'selection_frozen.json')
            for kind in (CONTROL, *SPECS):
                require(evaluation_cache(args, kind, selection_sha) is not None, 'Completed evaluation missing')
            verify_files(args.run_dir, read(status_path)['output_hashes'])
            print('Already complete; no training or inference repeated. ' + str(args.run_dir / 'comparison.csv'))
            return
        save(status_path, dict(status='running', protocol_signature=signature))
        try:
            selection_path = args.run_dir / 'selection_frozen.json'
            if not selection_path.exists():
                models = {CONTROL: dict(thresholds=control['thresholds'], checkpoint=control['checkpoint'], artifact_hashes={})}
                for kind in SPECS:
                    models[kind] = fit_model(args, kind, rows, signature)
                best = max(SPECS, key=lambda k: (models[k]['dev_metrics']['label_wise_accuracy'],
                    models[k]['dev_metrics']['macro_f1'], models[k]['dev_metrics']['macro_average_precision'],
                    k == 'dinov2_small'))
                save(selection_path, dict(protocol_signature=signature, models=models, dev_selected_dino=best,
                    control_selection_sha256=config['control_selection_sha256'], labels=LABELS,
                    test_used_for_selection=False, previously_exposed_test=True))
                save(args.run_dir / 'selection_commit.json', dict(sha256=sha(selection_path)))
                log(args.run_dir, 'all_selections_frozen_before_test', dev_selected_dino=best, sha256=sha(selection_path))
            selected = verify_selection(args, signature)
            summary = evaluate(args, selected, baseline_head)
            # Verify inputs once more before declaring completion.
            common.bundle_data(args.bundle)
            control_inputs(args)
            require(packets == {k: model_packet(args.models_root, k) for k in SPECS}, 'DINOv2 weights changed during run')
            require(all(sha(HERE / n) == h for n, h in config['script_hashes'].items()), 'Source changed during run')
            verify_selection(args, signature)
            names = ('comparison.csv', 'per_label_comparison.csv', 'error_changes.json', 'comparison_summary.json')
            save(status_path, dict(status='complete', protocol_signature=signature, CUDA_executed=True,
                backbone_training_executed=False, head_training_executed=True, OCR_executed=False,
                output_hashes={n: sha(args.run_dir / n) for n in names}))
            log(args.run_dir, 'dinov2_comparison_complete', run=str(args.run_dir), goal_met=summary['goal_met'])
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=signature))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('download', 'run', 'show-results'))
    parser.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--baseline', type=Path, default=HERE / 'runs/heads-fp32')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/dinov2-heads-fp32')
    parser.add_argument('--size', type=int, default=392)
    parser.add_argument('--cs', type=float, nargs='+', default=[.01, .1, 1., 10.])
    parser.add_argument('--warmups', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('models_root', 'model_dir', 'bundle', 'baseline', 'prior_run', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.size < 14 or args.size % 14 or args.warmups < 1 or args.repeats < 1 or any(not math.isfinite(c) or c <= 0 for c in args.cs):
        parser.error('Positive settings required; input size must be a multiple of 14')
    if args.command == 'download':
        download(args.models_root)
    elif args.command == 'show-results':
        for name in ('status.json', 'comparison.csv', 'per_label_comparison.csv'):
            path = args.run_dir / name
            print(path.read_text(encoding='utf-8') if path.is_file() else 'Not ready: ' + str(path))
    else:
        run(args)


if __name__ == '__main__':
    main()
