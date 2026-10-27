"""Evaluate explicitly reviewed synthetic requirements with frozen B-4 + validation retry."""
import argparse
from datetime import datetime
import fcntl
import gc
import hashlib
import json
import math
import time
import traceback
import unicodedata
from pathlib import Path

import evaluate_requirements_json as base
import evaluate_requirements_retry as retry_lib
from evaluate_qwen_label_accuracy import score as score_labels
from requirements_compact import BALANCED_PROFILE, interpret, prompt
from requirements_json_contract import aggregate, evaluate, parse
from resultlib import LABELS, digest, inside, percentile, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'qwen_reviewed_72_manifest.json'


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def approval_timestamp(value):
    require(isinstance(value, str) and value.endswith('Z'), 'Missing UTC review timestamp')
    try:
        datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise ValueError('Invalid UTC review timestamp') from exc


def validate_review(path):
    packet = read(path)
    manifest = read(MANIFEST)
    require(packet.get('schema') == 'qwen-requirements-human-review-draft-v1' and
            packet.get('synthetic') is True and packet.get('human_verified') is True and
            packet.get('approval_state') == 'HUMAN_REVIEW_COMPLETE' and
            packet.get('use_for_training') is False and packet.get('use_for_evaluation') is False and
            isinstance(packet.get('reviewer'), str) and bool(packet['reviewer'].strip()),
            'Need the fully approved review export, with a reviewer; draft/progress files are not accepted')
    approval_timestamp(packet.get('exported_at_utc'))
    require(manifest['schema'] == 'qwen-reviewed-72-input-manifest-v1' and
            packet.get('old_dev_text_sha256') == manifest['previous_dev_file_sha256'] == sha(base.DATA) and
            packet.get('labels') == LABELS, 'Review packet or original development dataset differs')
    rows = packet.get('cases')
    require(isinstance(rows, list) and len(rows) == len(manifest['cases']) == 72 and
            len({r['id'] for r in rows}) == 72, 'Expected exactly 72 unique reviewed cases')
    reference = {r['id']: r for r in manifest['cases']}
    require({r['id'] for r in rows} == set(reference), 'Reviewed case IDs differ from the frozen manifest')
    old = read(base.DATA)
    old_texts = {normalized(r['text']) for r in old['cases']}
    texts = []
    for r in rows:
        original = reference[r['id']]
        require(r.get('review_status') == 'APPROVED', 'Unapproved case: ' + r['id'])
        approval_timestamp(r.get('reviewed_at_utc'))
        require(isinstance(r.get('text'), str) and 0 < len(r['text']) <= 6000 and
                r.get('group_id') == original['group_id'] and text_sha(r['text']) == original['text_sha256'] and
                text_sha(normalized(r['text'])) == original['normalized_text_sha256'],
                'Case wording/group changed; create and approve a new manifest: ' + r['id'])
        require(isinstance(r.get('labels'), dict) and set(r['labels']) == set(LABELS) and
                all(v is None or type(v) is int and v in (0, 1) for v in r['labels'].values()),
                'Invalid reviewed labels: ' + r['id'])
        gold = parse(json.dumps(r['expected'], ensure_ascii=False), r['text'])
        require(gold['json_valid'] and gold['schema_valid'] and gold['source_valid'],
                'Invalid reviewed conditions: ' + r['id'])
        named = {item['field'] for item in r['expected']['items'] if item['field'] in LABELS}
        require(all(r['labels'][label] is None or (label in named) == bool(r['labels'][label]) for label in LABELS),
                'Reviewed labels disagree with reviewed conditions: ' + r['id'])
        texts.append(normalized(r['text']))
    require(len(set(texts)) == len(texts) and not old_texts.intersection(texts),
            'Duplicate normalized case or exact overlap with the 40-case development set')
    counts = {label: dict(positive=sum(r['labels'][label] == 1 for r in rows),
                          negative=sum(r['labels'][label] == 0 for r in rows),
                          unknown=sum(r['labels'][label] is None for r in rows)) for label in LABELS}
    return packet, counts


def frozen_reference(root, model_packet, instruction, args):
    status_path, protocol_path = root / 'status.json', root / 'protocol.json'
    status, protocol = read(status_path), read(protocol_path)
    require(status['status'] == 'complete' and status['protocol_signature'] == protocol['signature'] ==
            digest(protocol['config']), 'B-4 reference must be complete and have a valid protocol')
    for name in ('protocol.json', 'comparison_summary.json'):
        require(name in status['artifact_hashes'] and sha(inside(root, name)) == status['artifact_hashes'][name],
                'B-4 reference evidence changed: ' + name)
    cfg = protocol['config']
    require(cfg['prompt_profile'] == BALANCED_PROFILE and cfg['first_prompt'] == instruction and
            cfg['retry_instruction'] == retry_lib.RETRY_INSTRUCTION and cfg['model_packet'] == model_packet and
            cfg['do_sample'] is False and cfg['max_new_tokens'] == args.max_new_tokens and
            cfg['max_input_tokens'] == args.max_input_tokens and
            cfg['retry_policy'] == 'only json/schema/source validation failure; at most one; use valid retry else first',
            'Frozen B-4 prompt, model, retry policy or generation budgets differ')
    return dict(protocol_signature=protocol['signature'], protocol_sha256=sha(protocol_path),
                status_sha256=sha(status_path))


def predicted_labels(parsed):
    if not retry_lib.valid(parsed):
        return {label: None for label in LABELS}
    value = parsed['value']
    positive = {item['field'] for item in value['items'] if item['field'] in LABELS}
    unresolved = any(item['reason'] != 'non_requirement' for item in value['needs_review'])
    return {label: 1 if label in positive else None if unresolved else 0 for label in LABELS}


def label_cells(case, parsed):
    predicted = predicted_labels(parsed)
    return [dict(case_id=case['id'], label=label, truth=case['labels'][label], prediction=predicted[label],
                 parsed_valid=retry_lib.valid(parsed)) for label in LABELS]


def timing(values):
    return dict(mean_ms=sum(values) / len(values), median_ms=percentile(values, .5),
                p95_ms=percentile(values, .95), timing_n=len(values))


def source_hashes():
    return dict(base.sources(), **{name: sha(HERE / name) for name in (
        'evaluate_requirements_retry.py', 'evaluate_qwen_label_accuracy.py',
        'evaluate_qwen_reviewed_eval.py', 'run_qwen_reviewed_eval.sh', 'qwen_reviewed_72_manifest.json')})


def preflight(args):
    packet, counts = validate_review(args.review_file)
    print(json.dumps(dict(status='review_record_ready', cases=len(packet['cases']),
        reviewer=packet['reviewer'], approval_state=packet['approval_state'],
        review_file_sha256=sha(args.review_file), label_counts=counts,
        approved_unknown_cells=sum(c['unknown'] for c in counts.values()),
        exact_overlap_with_original_40=0, synthetic=True, human_verified_self_attested=True,
        instruction='This only checks submitted review records and structure, not independent annotation quality.',
        installation_executed=False, inference_executed=False, training_executed=False),
        ensure_ascii=False, indent=2))


def run(args):
    require(args.confirm_reviewed, 'Pass --confirm-reviewed after checking the fully approved review file')
    packet, counts = validate_review(args.review_file)
    import torch
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    environment = base.environment()
    model_packet = base.packet(args, '4b')
    instruction = prompt(BALANCED_PROFILE)
    reference = frozen_reference(args.reference_retry_run, model_packet, instruction, args)
    review_sha = sha(args.review_file)
    sources = source_hashes()
    config = dict(schema='qwen-reviewed-synthetic-evaluation-v1', dataset_sha256=review_sha,
        manifest_sha256=sha(MANIFEST), old_dev_sha256=sha(base.DATA), model_packet=model_packet,
        reference=reference, environment=environment, source_hashes=sources,
        prompt_profile=BALANCED_PROFILE, prompt=instruction, retry_instruction=retry_lib.RETRY_INSTRUCTION,
        retry_policy='only json/schema/source validation failure; at most one; use valid retry else first',
        labels_passed_to_model=False, precision='bf16', attn_implementation='sdpa', thinking=False,
        do_sample=False, max_new_tokens=args.max_new_tokens, max_input_tokens=args.max_input_tokens,
        ram_policy=args.ram_policy, warmups=2, synthetic=True, human_review_attested=True,
        reviewer=packet['reviewer'], training_executed=False, model_deployed=False,
        metric='Reviewed synthetic requirements; exact text non-overlap does not establish real-world generalization')
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        protocol = dict(signature=signature, config=config)
        if protocol_path.exists():
            require(args.resume and read(protocol_path) == protocol, 'Resume requires identical dataset and protocol')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Use a new empty run directory')
            save(protocol_path, protocol)
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            require(all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                    'Completed evidence changed')
            print('already_complete: no inference repeated')
            return
        save(status_path, dict(status='running', protocol_signature=signature, training_executed=False))
        processor = model = None
        try:
            base.resources(args, ['4b'])
            processor = AutoProcessor.from_pretrained(str(base.folder(args, '4b')), local_files_only=True, trust_remote_code=False)
            processor.tokenizer.padding_side = 'left'
            model = Qwen3_5ForConditionalGeneration.from_pretrained(str(base.folder(args, '4b')),
                local_files_only=True, trust_remote_code=False, use_safetensors=True, dtype=torch.bfloat16,
                attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
            args.prompt_profile = BALANCED_PROFILE
            for _ in range(2):
                base.infer('행사명: 준비 예시', instruction, processor, model, args)
            requests = args.run_dir / 'requests'
            requests.mkdir(exist_ok=True)
            # Model inference receives only text. Do not pass ID, label, condition or review metadata.
            attempts = []
            for index, case in enumerate(packet['cases'], 1):
                path = requests / (case['id'] + '.json')
                if path.exists():
                    require(args.resume, 'Cached requests require --resume')
                    row = read(path)
                    retry_lib.validate_cached(row, case, signature, BALANCED_PROFILE)
                else:
                    started = time.perf_counter()
                    first = base.infer(case['text'], instruction, processor, model, args)
                    eligible = not retry_lib.valid(first['parsed'])
                    again = retry_lib.retry_infer(case['text'], first, instruction, processor, model, args) if eligible else None
                    selected = 'retry' if again is not None and not again['skipped'] and retry_lib.valid(again['parsed']) else 'first'
                    elapsed = (time.perf_counter() - started) * 1000
                    row = dict(protocol_signature=signature, case_id=case['id'], text_sha256=digest(case['text']),
                        first=first, retry_eligible=eligible, retry_feedback=retry_lib.feedback(first) if eligible else None,
                        retry=again, selected=selected, first_ms=first['total_ms'], end_to_end_ms=elapsed)
                    save(path, row)
                attempts.append(row)
                print(json.dumps(dict(event='reviewed_eval_progress', completed=index, total=len(packet['cases']),
                    retried=row['retry'] is not None and not row['retry']['skipped']), ensure_ascii=False), flush=True)
            require(source_hashes() == sources and sha(args.review_file) == review_sha and
                    base.packet(args, '4b') == model_packet and
                    frozen_reference(args.reference_retry_run, model_packet, instruction, args) == reference,
                    'Source, review file, model or reference changed during run')
            # Score only after all requested predictions have been collected.
            records, all_cells, comparisons, label_reports = [], [], [], []
            for variant in ('first_pass', 'final'):
                scores, cells = [], []
                for case, row in zip(packet['cases'], attempts):
                    chosen = row['retry'] if variant == 'final' and row['selected'] == 'retry' else row['first']
                    scores.append(evaluate(dict(expected=case['expected']), chosen['parsed']))
                    cells.extend(label_cells(case, chosen['parsed']))
                label_totals, per_label = score_labels(cells)
                condition = aggregate(scores)
                times = timing([r['first_ms'] if variant == 'first_pass' else r['end_to_end_ms'] for r in attempts])
                comparisons.append(dict(variant=variant, **label_totals, **times,
                    condition_exact_match_rate=condition['draft_exact_match_rate'],
                    condition_item_f1=condition['draft_item_f1'],
                    json_valid_rate=condition['json_valid_rate'], schema_valid_rate=condition['schema_valid_rate'],
                    source_valid_rate=condition['source_valid_rate'],
                    goal_accuracy_90=label_totals['label_accuracy'] is not None and label_totals['label_accuracy'] >= .90,
                    goal_macro_f1_90=label_totals['macro_f1_all_8'] >= .90))
                label_reports.extend(dict(variant=variant, **value) for value in per_label)
                all_cells.extend(dict(variant=variant, **value) for value in cells)
            for case, row in zip(packet['cases'], attempts):
                first, chosen = row['first'], row['retry'] if row['selected'] == 'retry' else row['first']
                records.append(dict(case_id=case['id'], text=case['text'], reviewed_labels=case['labels'],
                    expected=case['expected'], first=first, retry=row['retry'], selected=row['selected'],
                    first_score=evaluate(dict(expected=case['expected']), first['parsed']),
                    final_score=evaluate(dict(expected=case['expected']), chosen['parsed']),
                    first_ms=row['first_ms'], end_to_end_ms=row['end_to_end_ms']))
            attempted = sum(r['retry'] is not None and not r['retry']['skipped'] for r in attempts)
            save(args.run_dir / 'predictions.json', records)
            save(args.run_dir / 'summary.json', dict(results=comparisons, reviewed_label_counts=counts,
                retry_attempted=attempted, retry_eligible=sum(r['retry_eligible'] for r in attempts),
                retry_skipped=sum(r['retry'] is not None and r['retry']['skipped'] for r in attempts),
                timing_scope='includes initial inference, validation and conditional retry; excludes loading, warmup, scoring and file writes',
                label_definition='Mentioned requirement in eight fields, including forbidden and optional; not image presence',
                condition_scope='Supplementary full condition matching, not label accuracy',
                synthetic=True, human_review_attested=True, training_executed=False,
                caveat='Human-reviewed synthetic draft; exact non-overlap is not proof of semantic independence or real-user generalization'))
            write_csv(args.run_dir / 'comparison.csv', [dict(r,
                macro_f1_excluded_labels=json.dumps(r['macro_f1_excluded_labels'])) for r in comparisons])
            write_csv(args.run_dir / 'per_label.csv', label_reports)
            write_csv(args.run_dir / 'per_case_label.csv', all_cells)
            excluded = {'.lock', 'status.json', 'failure.json'}
            hashes = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                      if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=signature, CUDA_executed=True,
                training_executed=False, OCR_executed=False, model_deployed=False, synthetic=True,
                human_review_attested=True, artifact_hashes=hashes))
            print(json.dumps(dict(event='reviewed_eval_complete', run=str(args.run_dir),
                                  retry_attempted=attempted), ensure_ascii=False), flush=True)
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
    p.add_argument('--review-file', type=Path)
    p.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    p.add_argument('--reference-retry-run', type=Path, default=HERE / 'runs/qwen4b-requirements-retry-v1')
    p.add_argument('--run-dir', type=Path, default=HERE / 'runs/qwen4b-reviewed-72-v1')
    p.add_argument('--ram-policy', choices=('inactive-file', 'clean-unmapped-file'), default='inactive-file')
    p.add_argument('--max-new-tokens', type=int, default=768)
    p.add_argument('--max-input-tokens', type=int, default=4096)
    p.add_argument('--confirm-reviewed', action='store_true')
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    require(256 <= args.max_new_tokens <= 2048 and 512 <= args.max_input_tokens <= 4096, 'Invalid token budgets')
    for name in ('review_file', 'models_root', 'reference_retry_run', 'run_dir'):
        path = getattr(args, name)
        if path is not None:
            setattr(args, name, path.expanduser().resolve())
    require(not args.run_dir.is_relative_to(args.models_root) and not args.models_root.is_relative_to(args.run_dir) and
            not args.run_dir.is_relative_to(args.reference_retry_run) and not args.reference_retry_run.is_relative_to(args.run_dir) and
            (args.review_file is None or not args.review_file.is_relative_to(args.run_dir)),
            'Output must be separate from the models, reference and review file')
    if args.command in ('preflight', 'run'):
        require(args.review_file is not None, '--review-file is required')
    if args.command == 'preflight':
        preflight(args)
    elif args.command == 'run':
        run(args)
    else:
        status = read(args.run_dir / 'status.json')
        require(status['status'] == 'complete' and
                all(sha(inside(args.run_dir, n)) == h for n, h in status['artifact_hashes'].items()),
                'Results incomplete or changed')
        print(json.dumps({k: v for k, v in status.items() if k != 'artifact_hashes'}, ensure_ascii=False, indent=2))
        print((args.run_dir / 'comparison.csv').read_text(encoding='utf-8-sig'))
        print((args.run_dir / 'per_label.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    main()
