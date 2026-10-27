"""Paired B-4 and structure-prompt comparison on separate synthetic draft cases."""
import argparse
import fcntl
import gc
import json
import time
import traceback
from pathlib import Path

import evaluate_requirements_json as base
import evaluate_requirements_retry as retry
from evaluate_qwen_label_accuracy import project_case, score
from evaluate_qwen_reviewed_eval import frozen_reference, normalized, text_sha
from requirements_compact import BALANCED_PROFILE, STRUCTURE_PROFILE, prompt
from requirements_json_contract import FIELD_TYPES, aggregate, evaluate, parse
from resultlib import digest, inside, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
DATA = HERE / 'requirements_structure_dev_v1.json'
MANIFEST = HERE / 'qwen_reviewed_72_manifest.json'
PROFILES = {'b4': BALANCED_PROFILE, 'structure': STRUCTURE_PROFILE}


def dataset():
    packet = read(DATA)
    require(packet['schema'] == 'structure-dev-draft-v1' and packet['synthetic'] is True and
            packet['human_verified'] is False and packet['use_for_training'] is False,
            'Expected non-human-approved synthetic development data')
    old = read(base.DATA)
    manifest = read(MANIFEST)
    require(manifest['schema'] == 'qwen-reviewed-72-input-manifest-v1' and
            manifest['previous_dev_file_sha256'] == sha(base.DATA), 'Original development manifest differs')
    old_texts = {normalized(row['text']) for row in old['cases']}
    exposed_hashes = {row['normalized_text_sha256'] for row in manifest['cases']}
    cases = []
    for row in packet['cases']:
        text = row['text']
        require(row['id'].isalnum() and isinstance(text, str) and 0 < len(text) <= 500,
                'Invalid case identity or source')
        require(normalized(text) not in old_texts and text_sha(normalized(text)) not in exposed_hashes,
                'Exact normalized overlap with earlier exposed cases: ' + row['id'])
        items = [dict(type=FIELD_TYPES[field], field=field, target=target, operator=operator,
                      value=value, source=text) for field, target, operator, value in row['items']]
        reviews = [dict(source=text, reason=row['review'])] if row.get('review') else []
        expected = dict(items=items, needs_review=reviews)
        parsed = parse(json.dumps(expected, ensure_ascii=False), text)
        require(retry.valid(parsed), 'Invalid draft condition: ' + row['id'])
        cases.append(dict(id=row['id'], text=text, expected=expected))
    require(cases and len({r['id'] for r in cases}) == len(cases) and
            len({normalized(r['text']) for r in cases}) == len(cases), 'Duplicate development cases')
    return cases


def hashes():
    return dict(base.sources(), **{name: sha(HERE / name) for name in (
        'evaluate_structure_dev.py', 'run_structure_dev.sh', 'requirements_structure_dev_v1.json',
        'evaluate_requirements_retry.py', 'evaluate_qwen_label_accuracy.py',
        'evaluate_qwen_reviewed_eval.py', 'qwen_reviewed_72_manifest.json')})


def preflight():
    cases = dataset()
    counts = {name: sum(any(item['field'] == name for item in case['expected']['items']) for case in cases)
              for name in base_labels()}
    print(json.dumps(dict(status='draft_ready', cases=len(cases), label_positive_cases=counts,
        exact_normalized_overlap_with_40_and_72=0, synthetic=True, human_verified=False,
        inference_executed=False, training_executed=False, installation_executed=False,
        caveat='Exact string non-overlap does not establish independent or real-world performance.'),
        ensure_ascii=False, indent=2))


def base_labels():
    from resultlib import LABELS
    return LABELS


def run(args):
    require(args.confirm_draft, 'Pass --confirm-draft to score the non-human-approved development draft')
    cases = dataset()
    import torch
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    env = base.environment()
    model_packet = base.packet(args, '4b')
    instructions = {name: prompt(profile) for name, profile in PROFILES.items()}
    ref = frozen_reference(args.reference_retry_run, model_packet, instructions['b4'], args)
    code_hashes = hashes()
    config = dict(schema='qwen-structure-dev-v1', dataset_sha256=sha(DATA), sources=code_hashes,
        reference=ref, model_packet=model_packet, environment=env, prompts=instructions,
        retry_instruction=retry.RETRY_INSTRUCTION, profiles=PROFILES,
        retry_policy='only json/schema/source validation failure; at most one; use valid retry else first',
        labels_passed_to_model=False, precision='bf16', attn_implementation='sdpa', do_sample=False,
        max_input_tokens=args.max_input_tokens, max_new_tokens=args.max_new_tokens, ram_policy=args.ram_policy,
        synthetic=True, human_verified=False, training_executed=False)
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        protocol = dict(signature=signature, config=config)
        if protocol_path.exists():
            require(args.resume and read(protocol_path) == protocol, 'Resume needs identical frozen protocol')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Use a new empty run directory')
            save(protocol_path, protocol)
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            require(all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                    'Completed run has changed')
            print('already_complete: no inference repeated')
            return
        save(status_path, dict(status='running', protocol_signature=signature, training_executed=False))
        processor = model = None
        try:
            base.resources(args, ['4b'])
            processor = AutoProcessor.from_pretrained(str(base.folder(args, '4b')), local_files_only=True,
                                                      trust_remote_code=False)
            processor.tokenizer.padding_side = 'left'
            model = Qwen3_5ForConditionalGeneration.from_pretrained(str(base.folder(args, '4b')),
                local_files_only=True, trust_remote_code=False, use_safetensors=True, dtype=torch.bfloat16,
                attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
            for name, profile in PROFILES.items():
                args.prompt_profile = profile
                base.infer('행사명: 준비 예시', instructions[name], processor, model, args)
            requests = args.run_dir / 'requests'
            requests.mkdir(exist_ok=True)
            results = {name: [] for name in PROFILES}
            for index, case in enumerate(cases, 1):
                for name, profile in PROFILES.items():
                    args.prompt_profile = profile
                    path = requests / (name + '-' + case['id'] + '.json')
                    if path.exists():
                        require(args.resume, 'Cached request requires --resume')
                        row = read(path)
                        retry.validate_cached(row, case, signature, profile)
                    else:
                        started = time.perf_counter()
                        first = base.infer(case['text'], instructions[name], processor, model, args)
                        eligible = not retry.valid(first['parsed'])
                        again = retry.retry_infer(case['text'], first, instructions[name], processor, model, args) if eligible else None
                        selected = 'retry' if again is not None and not again['skipped'] and retry.valid(again['parsed']) else 'first'
                        elapsed = (time.perf_counter() - started) * 1000
                        row = dict(protocol_signature=signature, case_id=case['id'], text_sha256=digest(case['text']),
                            first=first, retry_eligible=eligible, retry_feedback=retry.feedback(first) if eligible else None,
                            retry=again, selected=selected, first_ms=first['total_ms'], end_to_end_ms=elapsed)
                        save(path, row)
                    results[name].append(row)
                print(json.dumps(dict(event='structure_dev_progress', completed=index, total=len(cases))), flush=True)
            require(hashes() == code_hashes and base.packet(args, '4b') == model_packet and
                    frozen_reference(args.reference_retry_run, model_packet, instructions['b4'], args) == ref,
                    'Code, dataset, model or B-4 reference changed during evaluation')
            comparisons, labels, cells, records = [], [], [], []
            for name in PROFILES:
                for variant in ('first', 'final'):
                    outcomes, projected = [], []
                    for case, row in zip(cases, results[name]):
                        chosen = row['retry'] if variant == 'final' and row['selected'] == 'retry' else row['first']
                        outcomes.append(evaluate(case, chosen['parsed']))
                        projected.extend(project_case(dict(case_id=case['id'], expected_draft=case['expected']), chosen['parsed']))
                    total, per_label = score(projected)
                    condition = aggregate(outcomes)
                    times = retry.timing([r['first_ms'] if variant == 'first' else r['end_to_end_ms'] for r in results[name]])
                    building = next(r for r in per_label if r['label'] == 'building_structure')
                    comparisons.append(dict(profile=name, variant=variant, cases=len(cases),
                        label_accuracy=total['label_accuracy'], macro_f1_all_8=total['macro_f1_all_8'],
                        coverage=total['coverage'], known_cells=total['known_cells'],
                        unanswered_positive=total['unanswered_positive'], unanswered_negative=total['unanswered_negative'],
                        building_tp=building['tp'], building_fp=building['fp'], building_fn=building['fn'],
                        building_unanswered_positive=building['unanswered_positive'], building_f1=building['f1'],
                        condition_exact=condition['draft_exact_match_rate'], condition_item_f1=condition['draft_item_f1'],
                        forbidden_recall=condition['draft_forbidden_recall'], count_recall=condition['draft_count_recall'],
                        retry_attempted=sum(r['retry'] is not None and not r['retry']['skipped'] for r in results[name]),
                        **times))
                    labels.extend(dict(profile=name, variant=variant, **r) for r in per_label)
                    cells.extend(dict(profile=name, variant=variant, **r) for r in projected)
                for case, row in zip(cases, results[name]):
                    records.append(dict(profile=name, case_id=case['id'], text=case['text'],
                        expected_draft=case['expected'], **{k: v for k, v in row.items() if k != 'case_id'}))
            write_csv(args.run_dir / 'comparison.csv', comparisons)
            write_csv(args.run_dir / 'per_label.csv', labels)
            write_csv(args.run_dir / 'per_case_label.csv', cells)
            save(args.run_dir / 'predictions.json', records)
            save(args.run_dir / 'summary.json', dict(comparison=comparisons, synthetic=True, human_verified=False,
                timing='First inference and validation plus conditional retry; excludes loading, warmup and file writes.',
                task='Requirement label presence; forbidden and optional count as label present, not image presence.',
                limitations='Provisional development cases informed by exposed errors. Exact text overlap checks do not prove independence.'))
            excluded = {'.lock', 'status.json', 'failure.json'}
            artifacts = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                         if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=signature, artifact_hashes=artifacts,
                CUDA_executed=True, training_executed=False, synthetic=True, human_verified=False))
            print(json.dumps(dict(event='structure_dev_complete', run=str(args.run_dir)), ensure_ascii=False))
        except Exception as exc:
            save(args.run_dir / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=signature, training_executed=False))
            raise
        finally:
            del model, processor
            gc.collect()
            torch.cuda.empty_cache()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('preflight', 'run', 'show-results'))
    p.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    p.add_argument('--reference-retry-run', type=Path, default=HERE / 'runs/qwen4b-requirements-retry-v1')
    p.add_argument('--run-dir', type=Path, default=HERE / 'runs/qwen4b-structure-dev-v1')
    p.add_argument('--ram-policy', choices=('inactive-file', 'clean-unmapped-file'), default='inactive-file')
    p.add_argument('--max-new-tokens', type=int, default=768)
    p.add_argument('--max-input-tokens', type=int, default=4096)
    p.add_argument('--confirm-draft', action='store_true')
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    require(256 <= args.max_new_tokens <= 2048 and 512 <= args.max_input_tokens <= 4096, 'Invalid token budgets')
    for name in ('models_root', 'reference_retry_run', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    require(all(not args.run_dir.is_relative_to(p) and not p.is_relative_to(args.run_dir)
                for p in (args.models_root, args.reference_retry_run)), 'Run directory must be separate')
    if args.command == 'preflight':
        preflight()
    elif args.command == 'run':
        run(args)
    else:
        status = read(args.run_dir / 'status.json')
        require(status['status'] == 'complete' and
                all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                'Results incomplete or changed')
        print(json.dumps({k: v for k, v in status.items() if k != 'artifact_hashes'}, indent=2))
        print((args.run_dir / 'comparison.csv').read_text(encoding='utf-8-sig'))
        print((args.run_dir / 'per_label.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    main()
