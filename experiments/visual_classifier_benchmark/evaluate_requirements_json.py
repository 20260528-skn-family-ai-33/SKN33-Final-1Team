"""Sequential Qwen3.5-2B/4B Korean requirements-to-JSON draft pilot; no installation/training."""
from pathlib import Path
import argparse
import fcntl
import fnmatch
import gc
import json
import math
import os
import shutil
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
from evaluate_qwen_zero_shot import environment
from benchlib import log
from resultlib import digest, inside, percentile, read, require, save, sha, write_csv
from requirements_json_contract import aggregate, evaluate, load_cases, parse, prompt

SPECS = {
    '2b': dict(repo='Qwen/Qwen3.5-2B', estimate=4_600_000_000, gpu=8 * 1024**3, ram=8 * 1024**3),
    '4b': dict(repo='Qwen/Qwen3.5-4B', estimate=9_400_000_000, gpu=14 * 1024**3, ram=14 * 1024**3),
}
MANIFEST = 'requirements_download_manifest.json'
DATA = HERE / 'requirements_pilot_v1.json'
SCHEMA = HERE / 'requirements_json_schema.json'


def folder(args, model):
    return args.models_root / ('qwen3.5-' + model)


def ram_status():
    """Estimate capacity after clean inactive-file reclaim, retaining every visible hard limit."""
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    host_available = int(values['MemAvailable'].split()[0]) * 1024
    available, raw_available, controllers = host_available, host_available, []
    for root, cap_name, used_name, hierarchical in (
        (Path('/sys/fs/cgroup'), 'memory.max', 'memory.current', False),
        (Path('/sys/fs/cgroup/memory'), 'memory.limit_in_bytes', 'memory.usage_in_bytes', True),
    ):
        if not (root / cap_name).is_file() or not (root / used_name).is_file():
            continue
        cap_text = (root / cap_name).read_text().strip()
        if cap_text == 'max':
            controllers.append(dict(path=str(root), limit_bytes=None))
            continue
        cap = int(cap_text)
        used = max(0, int((root / used_name).read_text().strip()))
        stats_path = root / 'memory.stat'
        stats = dict(line.split() for line in stats_path.read_text().splitlines()) if stats_path.is_file() else {}

        def counter_key(name):
            return 'total_' + name if hierarchical and 'total_' + name in stats else name

        def counter(name):
            return max(0, int(stats.get(counter_key(name), '0')))

        inactive = counter('inactive_file')
        file_cache = counter('cache' if hierarchical else 'file')
        shmem = counter('shmem')
        dirty = counter('dirty' if hierarchical else 'file_dirty')
        writeback = counter('writeback' if hierarchical else 'file_writeback')
        # Do not credit active cache, tmpfs/shared memory, dirty/writeback pages, or kernel slabs.
        # The counters may overlap; subtracting dirty+writeback is intentionally conservative.
        needed = ('inactive_file', 'cache', 'shmem', 'dirty', 'writeback') if hierarchical else (
            'inactive_file', 'file', 'shmem', 'file_dirty', 'file_writeback')
        complete_stats = all(counter_key(name) in stats for name in needed)
        reclaim = max(0, min(inactive, max(0, file_cache - shmem), used) - dirty - writeback) if complete_stats else 0
        raw_headroom = max(0, cap - used)
        estimated_headroom = min(cap, max(0, cap - used + reclaim))
        raw_available = min(raw_available, raw_headroom)
        available = min(available, estimated_headroom)
        controllers.append(dict(path=str(root), limit_bytes=cap, used_bytes=used,
            headroom_without_cache_bytes=raw_headroom, file_cache_bytes=file_cache,
            inactive_file_bytes=inactive, shmem_bytes=shmem, dirty_bytes=dirty,
            writeback_bytes=writeback, cache_credit_counters_complete=complete_stats,
            credited_clean_inactive_file_bytes=reclaim,
            headroom_after_reclaim_estimate_bytes=estimated_headroom))
    return dict(host_available_bytes=host_available, available_estimate_bytes=available,
        headroom_without_cache_bytes=raw_available, controllers=controllers,
        policy='min(host MemAvailable, each visible cgroup hard-limit headroom plus clean inactive-file estimate); '
               'no forced cache clearing, no limit changes, no guarantee that all credited cache is immediately reclaimable')


def ram_available():
    return ram_status()['available_estimate_bytes']


def resources(args, models):
    import torch
    free_gpu, total_gpu = torch.cuda.mem_get_info(0)
    memory = ram_status()
    available_ram = memory['available_estimate_bytes']
    parent = args.models_root
    while not parent.exists():
        parent = parent.parent
    free_disk = shutil.disk_usage(parent).free
    remaining = 0
    for model in models:
        root = folder(args, model)
        present = sum(p.stat().st_size for p in root.glob('*.safetensors')
                      if p.is_file() and not p.is_symlink()) if root.exists() else 0
        remaining += max(0, SPECS[model]['estimate'] - present)
    issues = []
    if free_gpu < max(SPECS[m]['gpu'] for m in models):
        issues.append('Insufficient free VRAM. Run one model at a time with other GPU jobs finished.')
    if available_ram < max(SPECS[m]['ram'] for m in models):
        issues.append('Insufficient available host/container RAM for BF16 loading.')
    if free_disk < remaining + 2 * 1024**3:
        issues.append('Insufficient model-volume disk space. No files are automatically deleted.')
    report = dict(ready=not issues, GPU_free_bytes=free_gpu, GPU_total_bytes=total_gpu,
        RAM_available_bytes=available_ram, RAM_available_is_estimate=True,
        RAM_headroom_without_cache_bytes=memory['headroom_without_cache_bytes'],
        RAM_memory_details=memory, disk_free_bytes=free_disk,
        estimated_remaining_download_bytes=remaining, required_disk_free_bytes=remaining + 2 * 1024**3,
        capacity_policy='Conservative estimates, not measured peaks/cache guarantees', issues=issues)
    print(json.dumps(dict(resource_check=report), ensure_ascii=False, indent=2), flush=True)
    require(report['ready'], 'Resource check failed; no packages changed or files deleted')
    return report


def packet(args, model, verify=True):
    root = folder(args, model)
    value = read(root / MANIFEST)
    require(value['schema'] == 'requirements-model-download-v1' and value['model_id'] == SPECS[model]['repo'] and
            len(value['revision']) == 40 and value['files'], 'Wrong model download packet')
    for name, expected in value['files'].items():
        path = inside(root, name)
        require(path.stat().st_size == expected['bytes'] and (not verify or sha(path) == expected['sha256']),
                'Downloaded file changed: ' + str(path))
    config = read(root / 'config.json')
    require(config['model_type'] == 'qwen3_5' and config['architectures'] == ['Qwen3_5ForConditionalGeneration'] and
            'chat_template.jinja' in value['files'] and any(n.endswith('.safetensors') for n in value['files']),
            'Expected the official full Qwen3.5 checkpoint')
    return value


def download(args):
    from huggingface_hub import HfApi, snapshot_download
    environment()
    resources(args, args.models)
    args.models_root.mkdir(parents=True, exist_ok=True)
    with (args.models_root / '.requirements-download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for model in args.models:
            root = folder(args, model)
            if (root / MANIFEST).exists():
                value = packet(args, model)
                require(args.revision in ('main', value['revision']), 'Revision differs; use another models-root')
                print(json.dumps(dict(model=model, download_reused=True, revision=value['revision'])))
                continue
            root.mkdir(parents=True, exist_ok=True)
            intent = root / 'requirements_download_intent.json'
            if intent.exists():
                wanted = read(intent)
                require(wanted['model_id'] == SPECS[model]['repo'] and wanted['requested_revision'] == args.revision,
                        'Existing download intent differs')
            else:
                require(not list(root.glob('*.safetensors')), 'Untracked weights exist; use a separate models-root')
                info = HfApi().model_info(SPECS[model]['repo'], revision=args.revision)
                patterns = ('*.json', '*.safetensors', '*.jinja', '*.txt', '*.model', 'LICENSE', 'README.md')
                names = sorted(s.rfilename for s in info.siblings if any(fnmatch.fnmatch(s.rfilename, p) for p in patterns))
                wanted = dict(model_id=SPECS[model]['repo'], requested_revision=args.revision, revision=info.sha, names=names)
                save(intent, wanted)
            snapshot_download(wanted['model_id'], revision=wanted['revision'], local_dir=str(root), allow_patterns=wanted['names'])
            save(root / MANIFEST, dict(schema='requirements-model-download-v1', model_id=wanted['model_id'],
                revision=wanted['revision'], files={n: dict(bytes=inside(root, n).stat().st_size,
                sha256=sha(inside(root, n))) for n in wanted['names']}))
            value = packet(args, model, verify=False)
            print(json.dumps(dict(model=model, download_complete=True, revision=value['revision'], installation_executed=False)))


def sources():
    names = ('evaluate_requirements_json.py', 'requirements_json_contract.py', 'requirements_json_schema.json',
             'run_requirements_json.sh', 'evaluate_qwen_zero_shot.py', 'qwen_zero_shot_report.py',
             'resultlib.py', '../poster_benchmark/benchlib.py')
    return {n: sha(HERE / n) for n in names}


def infer(text, instruction, processor, model, args):
    import torch
    torch.cuda.synchronize()
    start = time.perf_counter()
    messages = [dict(role='system', content=instruction), dict(role='user', content=text)]
    rendered = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    require(rendered.endswith('<think>\n\n</think>\n\n'), 'Non-thinking template was not applied')
    inputs = processor(text=[rendered], return_tensors='pt')
    tokens_in = int(inputs['input_ids'].shape[1])
    require(tokens_in <= args.max_input_tokens, 'Input exceeds frozen token budget; no truncation is allowed')
    prepared = time.perf_counter()
    inputs = {k: v.to('cuda:0') for k, v in inputs.items()}
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False,
                                num_beams=1, use_cache=True, return_dict_in_generate=False, logits_to_keep=1)
    torch.cuda.synchronize()
    generated = time.perf_counter()
    tokens = output[0, tokens_in:]
    raw = processor.decode(tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False)
    parsed = parse(raw, text)
    end = time.perf_counter()
    return dict(raw=raw, parsed=parsed, input_tokens=tokens_in, generated_tokens=int(tokens.numel()),
        reached_token_limit=int(tokens.numel()) >= args.max_new_tokens,
        tokenize_ms=(prepared - start) * 1000, transfer_generate_ms=(generated - prepared) * 1000,
        decode_validate_ms=(end - generated) * 1000, total_ms=(end - start) * 1000)


def one_model(args, name, cases, instruction):
    import torch
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    root = args.run_dir / name
    done = root / 'complete.json'
    if done.exists():
        value = read(done)
        require(value['protocol_signature'] == args.signature and
                all(sha(inside(root, n)) == h for n, h in value['files'].items()), 'Completed model outputs changed')
        return value['summary']
    root.mkdir(exist_ok=True)
    resources(args, [name])
    processor = AutoProcessor.from_pretrained(str(folder(args, name)), local_files_only=True, trust_remote_code=False)
    processor.tokenizer.padding_side = 'left'
    model = Qwen3_5ForConditionalGeneration.from_pretrained(str(folder(args, name)),
        local_files_only=True, trust_remote_code=False, use_safetensors=True, dtype=torch.bfloat16,
        attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
    requests = root / 'requests'
    requests.mkdir(exist_ok=True)
    try:
        for _ in range(args.warmups):
            infer(cases[0]['text'], instruction, processor, model, args)
        scores, timings, details, drafts = [], [], [], []
        for index, case in enumerate(cases, 1):
            repeats = []
            for repeat in range(args.repeats):
                p = requests / (case['id'] + '-r' + str(repeat) + '.json')
                identity = dict(protocol_signature=args.signature, model=name, case_id=case['id'],
                                text_sha256=digest(case['text']), repeat=repeat)
                if p.exists():
                    require(args.resume, 'Cached requests require --resume')
                    result = read(p)
                    require(all(result.get(k) == v for k, v in identity.items()) and
                            parse(result['raw'], case['text']) == result['parsed'] and
                            all(type(result[k]) in (int, float) and math.isfinite(result[k]) and result[k] >= 0
                                for k in ('tokenize_ms', 'transfer_generate_ms', 'decode_validate_ms', 'total_ms')),
                            'Cached request changed')
                else:
                    result = dict(**identity, **infer(case['text'], instruction, processor, model, args))
                    save(p, result)
                repeats.append(result)
                timings.append({k: result[k] for k in ('case_id', 'repeat', 'input_tokens', 'generated_tokens',
                    'reached_token_limit', 'tokenize_ms', 'transfer_generate_ms', 'decode_validate_ms', 'total_ms')})
            first = repeats[0]
            score = evaluate(case, first['parsed'])
            scores.append(score)
            details.append(dict(case_id=case['id'], group_id=case['group_id'], text=case['text'],
                expected_draft=case['expected'], raw=first['raw'], parsed=first['parsed'], result=score,
                repeat_disagreement=any(r['parsed']['value'] != first['parsed']['value'] for r in repeats[1:])
                if args.repeats > 1 else None))
            valid = first['parsed']['schema_valid'] and first['parsed']['source_valid']
            items = first['parsed']['value']['items'] if valid else []
            # These match the existing Item shape but remain unselected, with no API/DB calls.
            drafts.append(dict(case_id=case['id'], human_verified=False, raw_requirements=case['text'],
                source_documents=None, items=[dict(id=case['id'] + '-' + str(i), text=item['source'],
                    type=item['type'], source=item['source'], selected=False) for i, item in enumerate(items, 1)],
                structured_conditions=items, needs_review=first['parsed']['value']['needs_review'] if valid else []))
            log(args.run_dir, 'requirements_pilot_progress', model=name, completed=index, total=len(cases),
                schema_valid=score['schema_valid'], draft_exact=score['exact'])
        summary = dict(model=name, model_id=SPECS[name]['repo'], **aggregate(scores),
            mean_ms=sum(t['total_ms'] for t in timings) / len(timings),
            median_ms=percentile([t['total_ms'] for t in timings], .5),
            p95_ms=percentile([t['total_ms'] for t in timings], .95), timing_n=len(timings),
            repeat_disagreement_cases=sum(d['repeat_disagreement'] for d in details) if args.repeats > 1 else None,
            synthetic=True, human_verified=False, training_executed=False)
        save(root / 'metrics.json', summary)
        save(root / 'predictions.json', details)
        save(root / 'frontend_drafts.json', drafts)
        save(root / 'errors.json', [d for d in details if not d['result']['exact']])
        write_csv(root / 'timing_raw.csv', timings)
        write_csv(root / 'per_case.csv', [dict(case_id=d['case_id'], json_valid=d['result']['json_valid'],
            schema_valid=d['result']['schema_valid'], source_valid=d['result']['source_valid'],
            draft_exact=d['result']['exact'], tp=d['result']['tp'], fp=d['result']['fp'], fn=d['result']['fn'],
            source_error_items=d['result']['source_error_items'],
            error=d['parsed']['error'], repeat_disagreement=d['repeat_disagreement']) for d in details])
        files = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*')
                 if p.is_file() and p != done and not p.name.endswith('.tmp')}
        save(done, dict(protocol_signature=args.signature, summary=summary, files=files))
        return summary
    finally:
        del model, processor
        gc.collect()
        torch.cuda.empty_cache()


def run(args):
    require(args.allow_draft_labels, 'Pilot expectations are AI-authored and unreviewed; explicitly pass --allow-draft-labels')
    env = environment()
    packet_data = load_cases(args.dataset)
    require(args.prompt_profile == 'baseline' or args.models == ['4b'],
            'The 4b-v2 prompt profile requires --models 4b')
    instruction = prompt(read(SCHEMA), args.prompt_profile)
    src = sources()
    downloads = {m: packet(args, m) for m in args.models}
    initial_stats = {m: {n: (inside(folder(args, m), n).stat().st_size,
                             inside(folder(args, m), n).stat().st_mtime_ns) for n in p['files']}
                     for m, p in downloads.items()}
    config = dict(schema='qwen-requirements-json-pilot-v1', environment=env, models=args.models,
        model_packets=downloads, dataset_sha256=sha(args.dataset), source_hashes=src,
        prompt=instruction, precision='bf16', attn_implementation='sdpa', thinking=False, do_sample=False,
        max_new_tokens=args.max_new_tokens, max_input_tokens=args.max_input_tokens,
        warmups=args.warmups, repeats=args.repeats, synthetic=True, human_verified=False,
        labels_passed_to_model=False, training_executed=False, model_deployed=False,
        metric='Agreement with unreviewed AI-authored expectations, not independent real-user accuracy',
        selection='none; both models reported, no tuning or deployment')
    args.signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol = args.run_dir / 'protocol.json'
        if protocol.exists():
            require(args.resume and read(protocol) == dict(signature=args.signature, config=config),
                    'Resume requires identical environment/code/models/data/prompt/settings')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Use a new empty run-dir')
            save(protocol, dict(signature=args.signature, config=config))
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            require(all(sha(inside(args.run_dir, n)) == h for n, h in read(status_path)['artifact_hashes'].items()),
                    'Completed results changed')
            print('already_complete: no inference repeated')
            return
        save(status_path, dict(status='running', protocol_signature=args.signature, training_executed=False))
        try:
            summaries = [one_model(args, name, packet_data['cases'], instruction) for name in args.models]
            require(sources() == src and sha(args.dataset) == config['dataset_sha256'], 'Sources/data changed during execution')
            require(all((inside(folder(args, m), n).stat().st_size, inside(folder(args, m), n).stat().st_mtime_ns) == stats
                        for m, files in initial_stats.items() for n, stats in files.items()), 'Downloaded files changed')
            save(args.run_dir / 'comparison_summary.json', dict(results=summaries, synthetic=True, human_verified=False,
                scope='Requirements JSON extraction; never compare these draft scores with SigLIP image-label accuracy.',
                training_executed=False, model_deployed=False))
            write_csv(args.run_dir / 'comparison.csv', summaries)
            excluded = {'.lock', 'status.json', 'failure.json', 'events.jsonl'}
            hashes = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                      if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=args.signature, CUDA_executed=True,
                training_executed=False, OCR_executed=False, model_deployed=False, synthetic=True,
                human_verified=False, artifact_hashes=hashes))
            log(args.run_dir, 'requirements_pilot_complete', run=str(args.run_dir))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=args.signature, training_executed=False))
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('check-env', 'download', 'run', 'show-results'))
    p.add_argument('--models', nargs='+', choices=tuple(SPECS), default=['2b', '4b'])
    p.add_argument('--prompt-profile', choices=('baseline', '4b-v2'), default='baseline')
    p.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    p.add_argument('--revision', default='main')
    p.add_argument('--dataset', type=Path, default=DATA)
    p.add_argument('--run-dir', type=Path, default=HERE / 'runs/qwen-requirements-json-pilot-v1')
    p.add_argument('--max-new-tokens', type=int, default=768)
    p.add_argument('--max-input-tokens', type=int, default=2048)
    p.add_argument('--repeats', type=int, default=1)
    p.add_argument('--warmups', type=int, default=2)
    p.add_argument('--allow-draft-labels', action='store_true')
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    require(len(set(args.models)) == len(args.models) and 1 <= args.repeats <= 10 and 1 <= args.warmups <= 10 and
            256 <= args.max_new_tokens <= 2048 and 512 <= args.max_input_tokens <= 4096, 'Invalid fixed budgets')
    for key in ('models_root', 'dataset', 'run_dir'):
        setattr(args, key, getattr(args, key).expanduser().resolve())
    require(not args.run_dir.is_relative_to(args.models_root) and not args.models_root.is_relative_to(args.run_dir),
            'Models and output must be separate')
    require(not args.dataset.is_relative_to(args.run_dir), 'Dataset must be outside the new output directory')
    if args.command == 'check-env':
        print(json.dumps(dict(environment=environment(), installation_executed=False, inference_executed=False), indent=2))
        resources(args, args.models)
    elif args.command == 'download':
        download(args)
    elif args.command == 'run':
        run(args)
    else:
        status = read(args.run_dir / 'status.json')
        if status['status'] == 'complete':
            require(all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()), 'Results changed')
        print(json.dumps({k: v for k, v in status.items() if k != 'artifact_hashes'}, indent=2))
        table = args.run_dir / 'comparison.csv'
        if table.exists():
            print(table.read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    main()
