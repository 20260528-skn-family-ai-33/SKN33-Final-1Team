"""Qwen3.5-9B zero-shot presence evaluation; never installs or trains anything."""
from pathlib import Path
from importlib import metadata
import argparse
import fcntl
import fnmatch
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'poster_benchmark'
sys.path.insert(0, str(SOURCE))
from benchlib import load_test, log, verify_bundle
from qwen_zero_shot_report import (LABELS, digest, inside, metrics, parse_answer, read,
    reference, reference_rows, require, save, sha, split_report, write_csv)

MODEL_ID = 'Qwen/Qwen3.5-9B'
MODEL_MANIFEST = 'zero_shot_download_manifest.json'
PROMPT = HERE / 'qwen_zero_shot_prompt.json'
SEED = 20261026
PACKAGES = ('torch', 'torchvision', 'transformers', 'timm', 'numpy', 'scipy',
            'scikit-learn', 'Pillow', 'safetensors', 'huggingface-hub')


def environment():
    try:
        import torch
        from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError('Existing BENCH_PYTHON cannot import Qwen3.5 support: ' + str(exc) +
                           '. No packages were changed; do not rerun setup/pip.') from exc
    require(torch.cuda.is_available(), 'CUDA is required; no CPU fallback')
    require(torch.cuda.is_bf16_supported(), 'BF16 is required; no automatic precision/quantization changes')
    torch.set_num_threads(4)
    torch.cuda.set_device(0)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    random.seed(SEED)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    return dict(python=sys.version, executable=sys.executable, GPU=torch.cuda.get_device_name(0),
        GPU_total_bytes=torch.cuda.get_device_properties(0).total_memory,
        compute_capability=list(torch.cuda.get_device_capability(0)), CUDA_runtime=torch.version.cuda,
        precision='bf16', device='cuda:0', TF32=False, torch_threads=4,
        versions={p: metadata.version(p) for p in PACKAGES})


def model_manifest(folder, verify=True):
    manifest = read(folder / MODEL_MANIFEST)
    require(manifest['schema'] == 'qwen-zero-shot-download-v1' and manifest['model_id'] == MODEL_ID,
            'Unknown downloaded model manifest')
    require(len(manifest['revision']) == 40 and manifest['files'], 'Model revision/files missing')
    for name, item in manifest['files'].items():
        path = inside(folder, name)
        require(path.is_file() and path.stat().st_size == item['bytes'] and
                (not verify or sha(path) == item['sha256']), 'Downloaded file changed/incomplete: ' + name)
    config = read(folder / 'config.json')
    require(config['model_type'] == 'qwen3_5' and
            config['architectures'] == ['Qwen3_5ForConditionalGeneration'], 'Wrong model architecture')
    require(any(n.endswith('.safetensors') for n in manifest['files']) and
            'chat_template.jinja' in manifest['files'] and 'preprocessor_config.json' in manifest['files'],
            'Missing vision/chat/weight files')
    return manifest


def download(args):
    from huggingface_hub import HfApi, snapshot_download
    args.model_dir.mkdir(parents=True, exist_ok=True)
    with (args.model_dir / '.zero-shot-download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (args.model_dir / MODEL_MANIFEST).exists():
            packet = model_manifest(args.model_dir)
            require(args.revision in ('main', packet['revision']), 'Use another directory for another revision')
            print(json.dumps(dict(download_reused=True, revision=packet['revision'],
                                  model_dir=str(args.model_dir), installation_executed=False)))
            return
        info = HfApi().model_info(MODEL_ID, revision=args.revision)
        patterns = ('*.json', '*.safetensors', '*.jinja', '*.txt', '*.model', 'README.md', 'LICENSE')
        files = sorted(s.rfilename for s in info.siblings if any(fnmatch.fnmatch(s.rfilename, p) for p in patterns))
        require(files and len(info.sha) == 40, 'No pinned repository file list')
        # Persist the resolved revision before downloading so an interrupted download keeps its identity.
        intent = args.model_dir / 'zero_shot_download_intent.json'
        wanted = dict(model_id=MODEL_ID, requested_revision=args.revision, revision=info.sha, files=files)
        if intent.exists():
            old = read(intent)
            require(old['model_id'] == MODEL_ID and old['requested_revision'] == args.revision,
                    'Download intent differs; choose another directory')
            wanted = old
        else:
            require(not any(p.suffix == '.safetensors' for p in args.model_dir.iterdir()),
                    'Do not adopt unrelated weights; use a new model directory')
            save(intent, wanted)
        snapshot_download(MODEL_ID, revision=wanted['revision'], local_dir=str(args.model_dir),
                          allow_patterns=wanted['files'])
        payload = {name: dict(bytes=inside(args.model_dir, name).stat().st_size,
                             sha256=sha(inside(args.model_dir, name))) for name in wanted['files']}
        save(args.model_dir / MODEL_MANIFEST, dict(schema='qwen-zero-shot-download-v1', model_id=MODEL_ID,
                                                  revision=wanted['revision'], files=payload))
        packet = model_manifest(args.model_dir, verify=False)
        print(json.dumps(dict(download_complete=True, revision=packet['revision'], files=len(payload),
                              model_dir=str(args.model_dir), installation_executed=False)))


def source_hashes():
    names = ('evaluate_qwen_zero_shot.py', 'qwen_zero_shot_report.py', 'qwen_zero_shot_prompt.json',
             'run_qwen_zero_shot.sh', 'resultlib.py', '../poster_benchmark/benchlib.py')
    return {name: sha(HERE / name) for name in names}


def letterbox(path, size):
    from PIL import Image, ImageOps
    # Same RGB, EXIF, bicubic, gray padding and rounding contract as the existing SigLIP encoder.
    with Image.open(path) as original:
        rgb = ImageOps.exif_transpose(original).convert('RGB')
    scale = min(size / rgb.width, size / rgb.height)
    shape = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
    canvas = Image.new('RGB', (size, size), (128, 128, 128))
    canvas.paste(rgb.resize(shape, Image.Resampling.BICUBIC),
                 ((size - shape[0]) // 2, (size - shape[1]) // 2))
    rgb.close()
    return canvas


def infer(path, processor, model, prompt, max_tokens):
    import torch
    torch.cuda.synchronize()
    started = time.perf_counter()
    with letterbox(path, 384) as image:
        rgb_sha = hashlib.sha256(image.tobytes()).hexdigest()
        user = prompt['task'] + '\n' + json.dumps(prompt['definitions'], ensure_ascii=False) + '\n' + prompt['output']
        messages = [dict(role='system', content=prompt['system']),
                    dict(role='user', content=[dict(type='image'), dict(type='text', text=user)])]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                             enable_thinking=False)
        require(text.endswith('<think>\n\n</think>\n\n'), 'Chat template did not disable thinking as expected')
        inputs = processor(text=[text], images=[image], return_tensors='pt')
    grid = inputs['image_grid_thw'][0].tolist()
    require(grid == [1, 24, 24], 'Processor changed the 384px input grid: ' + str(grid))
    input_tokens = int(inputs['input_ids'].shape[1])
    prepared = time.perf_counter()
    inputs = {key: value.to(device='cuda:0', dtype=torch.bfloat16) if value.is_floating_point()
              else value.to('cuda:0') for key, value in inputs.items()}
    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False, num_beams=1,
                                   use_cache=True, return_dict_in_generate=False, logits_to_keep=1)
    torch.cuda.synchronize()
    completed = time.perf_counter()
    tokens = generated[0, input_tokens:]
    raw = processor.decode(tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False)
    predicted, error = parse_answer(raw)
    ended = time.perf_counter()
    return dict(raw_text=raw, predictions=predicted, parse_error=error, input_rgb_sha256=rgb_sha,
        input_tokens=input_tokens, generated_tokens=int(tokens.numel()),
        reached_token_limit=int(tokens.numel()) >= max_tokens, image_grid_thw=grid,
        decode_preprocess_ms=(prepared - started) * 1000,
        transfer_generate_ms=(completed - prepared) * 1000,
        decode_parse_ms=(ended - completed) * 1000, total_ms=(ended - started) * 1000)


def evaluate_split(args, split, rows, info, processor, model, prompt):
    cached, predictions, raw_timings = args.run_dir / 'requests' / split, [], []
    cached.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows, 1):
        image_path = inside(args.bundle, row['image'])
        image_sha = sha(image_path)
        repetitions = []
        for repeat in range(args.repeats):
            path = cached / (row['image_id'] + '-r' + str(repeat) + '.json')
            identity = dict(protocol_signature=args.signature, split=split, image_id=row['image_id'],
                            image_sha256=image_sha, repeat=repeat)
            if path.exists():
                require(args.resume, 'Use --resume for cached requests')
                record = read(path)
                require(all(record.get(k) == v for k, v in identity.items()), 'Cached request identity changed')
                p, error = parse_answer(record['raw_text'])
                require(p == record['predictions'] and error == record['parse_error'] and
                        all(type(record[k]) in (int, float) and math.isfinite(record[k]) and record[k] >= 0
                            for k in ('decode_preprocess_ms', 'transfer_generate_ms', 'decode_parse_ms', 'total_ms')),
                        'Cached request changed')
            else:
                record = dict(**identity, **infer(image_path, processor, model, prompt, args.max_new_tokens))
                save(path, record)
            repetitions.append(record)
            raw_timings.append({k: record[k] for k in ('image_id', 'repeat', 'input_tokens', 'generated_tokens',
                'reached_token_limit', 'decode_preprocess_ms', 'transfer_generate_ms', 'decode_parse_ms', 'total_ms')})
        first = repetitions[0]
        predictions.append(dict(image_id=row['image_id'], labels=row['labels'], predictions=first['predictions'],
            parse_error=first['parse_error'], raw_text=first['raw_text'],
            repeat_disagreement=any(r['predictions'] != first['predictions'] for r in repetitions[1:])))
        log(args.run_dir, 'qwen_zero_shot_progress', split=split, completed=i, total=len(rows),
            invalid=bool(first['parse_error']), unanswered=sum(v is None for v in first['predictions']))
    old = reference_rows(args.reference_run, info, split, rows)
    return split_report(args.run_dir, split, rows, old, predictions, raw_timings, info, args.reference_run)


def run(args):
    import torch
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    env = environment()
    # Fail on missing/changed inputs before allocating 9B model weights.
    require(read(args.bundle / 'bundle_manifest.json') == read(SOURCE / 'bundle_manifest.json'),
            'Use the original approved benchmark bundle')
    _, _, td = verify_bundle(args.bundle)
    info = reference(args.reference_run)
    old_env = info['environment']
    require(all(env[k] == old_env[k] for k in ('python', 'GPU', 'GPU_total_bytes', 'compute_capability', 'CUDA_runtime')) and
            all(env['versions'].get(k) == v for k, v in old_env['versions'].items()),
            'Reuse the reference GPU/runtime/packages; no automatic environment changes')
    packet = model_manifest(args.model_dir)
    stats = {n: (inside(args.model_dir, n).stat().st_size, inside(args.model_dir, n).stat().st_mtime_ns)
             for n in packet['files']}
    prompt = read(PROMPT)
    require(list(prompt['definitions']) == LABELS, 'Prompt label order changed')
    hashes = source_hashes()
    config = dict(schema='qwen-zero-shot-comparison-v1', environment=env, model_id=MODEL_ID,
        model_dir=str(args.model_dir), model_revision=packet['revision'],
        model_manifest_sha256=sha(args.model_dir / MODEL_MANIFEST), prompt=prompt, source_hashes=hashes,
        bundle_sha256=sha(args.bundle / 'bundle_manifest.json'), reference_run=str(args.reference_run),
        reference=info, split=args.split, input='same 384px EXIF-corrected RGB bicubic gray letterbox as SigLIP',
        image_grid_thw=[1, 24, 24], precision='bf16', attn_implementation='sdpa', quantization=None,
        thinking=False, do_sample=False, num_beams=1, max_new_tokens=args.max_new_tokens,
        repeats=args.repeats, warmups=args.warmups, seed=SEED, few_shot_examples=0,
        training_executed=False, threshold_calibration_executed=False, prompt_selection_executed=False,
        labels_passed_to_model=False, AP_available=False, baseline_latency_remeasured=False,
        abstention_policy='Every unanswered known cell is incorrect for primary accuracy; never default to zero or drop.',
        population='Original development37/test37; no training images, corrections or additions are modified.')
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        if protocol_path.exists():
            require(args.resume and read(protocol_path) == dict(signature=args.signature, config=config),
                    'Resume requires identical code/data/prompt/model/GPU/settings')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Choose an empty new run directory')
            save(protocol_path, dict(signature=args.signature, config=config))
        frozen_path = args.run_dir / 'selection_frozen.json'
        frozen = dict(protocol_signature=args.signature, prompt_sha256=sha(PROMPT), model_revision=packet['revision'],
                      reference_model=info['model'], test_used_for_selection=False, selection='none; fixed zero-shot protocol')
        if frozen_path.exists():
            require(read(frozen_path) == frozen and read(args.run_dir / 'selection_commit.json')['sha256'] == sha(frozen_path),
                    'Frozen zero-shot protocol changed')
        else:
            save(frozen_path, frozen)
            save(args.run_dir / 'selection_commit.json', dict(sha256=sha(frozen_path)))
        frozen_sha = sha(frozen_path)
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            require(status['protocol_signature'] == args.signature and
                    all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                    'Completed zero-shot results changed')
            log(args.run_dir, 'already_complete', run=str(args.run_dir))
            return
        save(status_path, dict(status='running', protocol_signature=args.signature, training_executed=False))
        try:
            log(args.run_dir, 'zero_shot_protocol_frozen_before_test', reference=info['model'])
            processor = AutoProcessor.from_pretrained(str(args.model_dir), local_files_only=True, trust_remote_code=False)
            processor.tokenizer.padding_side = 'left'
            model = Qwen3_5ForConditionalGeneration.from_pretrained(str(args.model_dir),
                local_files_only=True, trust_remote_code=False, use_safetensors=True,
                dtype=torch.bfloat16, attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
            dev = [r for r in td if r['split'] == 'dev']
            for _ in range(args.warmups):
                infer(inside(args.bundle, dev[0]['image']), processor, model, prompt, args.max_new_tokens)
            summaries = []
            for split in (('dev', 'test') if args.split == 'all' else (args.split,)):
                rows = dev if split == 'dev' else load_test(args.bundle, frozen_path, frozen_sha)
                summaries.append(evaluate_split(args, split, rows, info, processor, model, prompt))
            require(sha(frozen_path) == frozen_sha and source_hashes() == hashes and
                    reference(args.reference_run) == info and sha(args.model_dir / MODEL_MANIFEST) == config['model_manifest_sha256'],
                    'Inputs changed during evaluation')
            verify_bundle(args.bundle)
            require(all((inside(args.model_dir, n).stat().st_size, inside(args.model_dir, n).stat().st_mtime_ns) == v
                        for n, v in stats.items()), 'Model files changed during inference')
            write_csv(args.run_dir / 'comparison.csv', [r for s in summaries for r in s['comparison']])
            save(args.run_dir / 'comparison_summary.json', dict(protocol_signature=args.signature,
                reference_model=info['model'], split_summaries=summaries, training_executed=False,
                model_deployed=False, baseline_latency_remeasured=False, test_used_for_selection=False,
                previously_exposed_test=True, AP_available=False))
            excluded = {'.lock', 'status.json', 'failure.json', 'events.jsonl'}
            artifacts = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                         if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature, artifact_hashes=artifacts,
                CUDA_executed=True, training_executed=False, OCR_executed=False, model_deployed=False))
            log(args.run_dir, 'qwen_zero_shot_complete', run=str(args.run_dir))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=args.signature, training_executed=False))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check-env', 'download', 'run', 'show-results'))
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/qwen3.5-9b'))
    parser.add_argument('--revision', default='main', help='Resolved to an immutable HF commit on first download')
    parser.add_argument('--bundle', type=Path, default=SOURCE / 'assets')
    parser.add_argument('--reference-run', type=Path, default=HERE / 'runs/siglip-poster-v2-mix-fp32')
    parser.add_argument('--run-dir', type=Path, default=HERE / 'runs/qwen3.5-9b-zero-shot-v1')
    parser.add_argument('--split', choices=('dev', 'test', 'all'), default='all')
    parser.add_argument('--max-new-tokens', type=int, default=192)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--warmups', type=int, default=3)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    require(1 <= args.repeats <= 20 and 1 <= args.warmups <= 20 and 96 <= args.max_new_tokens <= 512,
            'Require repeats/warmups 1..20 and max-new-tokens 96..512')
    for key in ('model_dir', 'bundle', 'reference_run', 'run_dir'):
        setattr(args, key, getattr(args, key).expanduser().resolve())
    for a, b in ((args.run_dir, args.bundle), (args.run_dir, args.reference_run),
                 (args.run_dir, args.model_dir), (args.model_dir, args.bundle), (args.model_dir, args.reference_run)):
        require(not a.is_relative_to(b) and not b.is_relative_to(a), 'Model/data/reference/output directories must be separate')
    if args.command == 'check-env':
        env = environment()
        import torch
        free, _ = torch.cuda.mem_get_info()
        print(json.dumps(dict(environment=env, GPU_free_bytes=free, installation_executed=False,
                              model_loaded=False, inference_executed=False), ensure_ascii=False, indent=2))
    elif args.command == 'download':
        download(args)
    elif args.command == 'show-results':
        status = read(args.run_dir / 'status.json')
        if status['status'] == 'complete':
            require(all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()), 'Result files changed')
        print(json.dumps({k: v for k, v in status.items() if k != 'artifact_hashes'}, indent=2))
        for name in ('comparison.csv', 'dev/per_label_comparison.csv', 'test/per_label_comparison.csv',
                     'test/instrument_positive_scores.csv'):
            path = args.run_dir / name
            if path.exists():
                print('\n' + name + '\n' + path.read_text(encoding='utf-8-sig'))
    else:
        run(args)


if __name__ == '__main__':
    main()
