"""Evaluate saved synthetic requirement collections with an existing Qwen 4B checkpoint.

This is a development audit, not an independent generalization estimate.
No training, downloading, installation or changes to input data are performed.
"""
import argparse
import fcntl
import gc
import json
import math
import time
import traceback
from pathlib import Path

import evaluate_requirements_json as base
import evaluate_requirements_retry as retry
import evaluate_qwen_reviewed_eval as reviewed
import evaluate_structure_dev as structure_dev
from evaluate_qwen_label_accuracy import project_case, score
from requirements_compact import CONDITION_PROFILE, STRUCTURE_PROFILE, input_message, prompt
from requirements_condition_policy import normalize_exact_duplicates
from requirements_json_contract import aggregate, evaluate, load_cases
from resultlib import LABELS, digest, inside, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
PROFILES = {'structure': STRUCTURE_PROFILE, 'conditions': CONDITION_PROFILE}


def datasets(args):
    """Construct separate case populations; never infer from an unapproved label."""
    original = load_cases(base.DATA)['cases']
    packet, counts, states = reviewed.validate_review(args.review_file, 'agent-partial')
    development = structure_dev.dataset()
    collections = {'original40': original, 'agent72': packet['cases'], 'structure28': development}
    identities, normalized = set(), set()
    for group, cases in collections.items():
        for case in cases:
            key = (group, case['id'])
            text_key = reviewed.normalized(case['text'])
            require(key not in identities and text_key not in normalized,
                    'Duplicate collection/case or normalized text across collections: ' + str(key))
            identities.add(key)
            normalized.add(text_key)
    return collections, dict(agent_label_counts=counts, agent_review_status_counts=states,
                             agent_unknown_cases=[r['id'] for r in packet['cases']
                                                  if r['review_status'] == 'UNREVIEWED'])


def hashes():
    names = ('evaluate_all_requirements.py', 'run_all_requirements.sh',
             'evaluate_requirements_retry.py', 'evaluate_qwen_reviewed_eval.py',
             'evaluate_qwen_label_accuracy.py', 'evaluate_structure_dev.py',
             'requirements_condition_policy.py', 'requirements_structure_dev_v1.json',
             'qwen_reviewed_72_manifest.json', 'requirements_pilot_v1.json')
    return dict(base.sources(), **{name: sha(HERE / name) for name in names})


def preflight(args):
    groups, metadata = datasets(args)
    print(json.dumps(dict(status='requirements_inventory_ready',
        datasets={k: len(v) for k, v in groups.items()}, **metadata,
        text_overlap='no exact normalized overlap among the three collections',
        synthetic=True, human_verified=False, independent_test=False,
        training_executed=False, installation_executed=False, inference_executed=False,
        note='Agent-approved labels remain provisional. UNREVIEWED cases are predicted but all their labels are excluded.'),
        ensure_ascii=False, indent=2))


def label_cells(group, case, parsed):
    if group == 'agent72':
        return reviewed.label_cells(case, parsed)
    return project_case(dict(case_id=case['id'], expected_draft=case['expected']), parsed)


def condition_scorable(group, case):
    return reviewed.condition_scorable(case) if group == 'agent72' else True


def score_group(group, cases, requests, variant):
    cells, condition, timings, records = [], [], [], []
    for case, row in zip(cases, requests):
        chosen = row['retry'] if row['selected'] == 'retry' else row['first']
        parsed = row['postprocessed']['parsed'] if variant == 'normalized' else chosen['parsed']
        cells.extend(label_cells(group, case, parsed))
        if condition_scorable(group, case):
            condition.append(evaluate(dict(expected=case['expected']), parsed))
        timings.append(row['end_to_end_ms'] +
                       (row['postprocessed']['postprocess_ms'] if variant == 'normalized' else 0))
        records.append(dict(dataset=group, case_id=case['id'], text=case['text'],
            review_status=case.get('review_status', 'SYNTHETIC_DRAFT'),
            condition_scorable=condition_scorable(group, case), expected_draft=case['expected'],
            scoring_labels=case.get('labels') if case.get('review_status') != 'UNREVIEWED' else None,
            **{k: v for k, v in row.items() if k != 'case_id'}))
    total, per_label = score(cells)
    cond = aggregate(condition) if condition else None
    summary = dict(dataset=group, variant=variant, cases=len(cases),
        label_accuracy=total['label_accuracy'], macro_f1_all_8=total['macro_f1_all_8'],
        coverage=total['coverage'], known_cells=total['known_cells'], gold_unknown_cells=total['gold_unknown_cells'],
        unanswered_positive=total['unanswered_positive'], unanswered_negative=total['unanswered_negative'],
        unknown_gold_positive_predictions=sum(c['truth'] is None and c['prediction'] == 1 for c in cells),
        condition_cases_scored=len(condition), condition_cases_excluded=len(cases)-len(condition),
        condition_exact=cond['draft_exact_match_rate'] if cond else None,
        condition_item_f1=cond['draft_item_f1'] if cond else None,
        review_tp=cond['review_tp'] if cond else None, review_fp=cond['review_fp'] if cond else None,
        review_fn=cond['review_fn'] if cond else None,
        retry_attempted=sum(r['retry'] is not None and not r['retry']['skipped'] for r in requests),
        retry_skipped=sum(r['retry'] is not None and r['retry']['skipped'] for r in requests),
        removed_exact_duplicates=sum(len(r['postprocessed']['removed']) for r in requests) if variant == 'normalized' else 0,
        **retry.timing(timings))
    return summary, [dict(dataset=group, variant=variant, **r) for r in per_label], [
        dict(dataset=group, variant=variant, **r) for r in cells], records


def run(args):
    require(args.confirm_agent_partial, 'Pass --confirm-agent-partial for provisional labels and unknown masking')
    groups, metadata = datasets(args)
    source_hashes, review_hash = hashes(), sha(args.review_file)
    import torch
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    env = base.environment()
    model_packet = base.packet(args, '4b')
    args.prompt_profile = PROFILES[args.profile]
    instruction = prompt(args.prompt_profile)
    config = dict(schema='all-requirements-development-v1', profile=args.prompt_profile,
        model_packet=model_packet, environment=env, prompt=instruction, review_sha256=review_hash,
        code_and_data_hashes=source_hashes, datasets={k: digest(v) for k, v in groups.items()},
        max_input_tokens=args.max_input_tokens, max_new_tokens=args.max_new_tokens,
        ram_policy=args.ram_policy, retry_instruction=retry.RETRY_INSTRUCTION,
        retry_policy='validation failures only; maximum one; valid retry else first',
        labels_passed_to_model=False, model_training=False, synthetic=True, human_verified=False,
        do_sample=False, precision='bf16', attn_implementation='sdpa', metadata=metadata)
    signature = digest(config)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.run_dir / 'protocol.json'
        protocol = dict(signature=signature, config=config)
        if protocol_path.exists():
            require(args.resume and read(protocol_path) == protocol, 'Resume requires identical data, model, environment and code')
        else:
            require(all(p.name == '.lock' for p in args.run_dir.iterdir()), 'Choose a new empty output directory')
            save(protocol_path, protocol)
        status_path = args.run_dir / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            status = read(status_path)
            require(all(sha(inside(args.run_dir, name)) == value for name, value in status['artifact_hashes'].items()),
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
            maximums = {}
            for group, cases in groups.items():
                sizes = []
                for case in cases:
                    messages = [dict(role='system', content=instruction),
                                dict(role='user', content=input_message(case['text']))]
                    rendered = processor.apply_chat_template(messages, tokenize=False,
                        add_generation_prompt=True, enable_thinking=False)
                    size = int(processor(text=[rendered], return_tensors='pt')['input_ids'].shape[1])
                    require(size <= args.max_input_tokens, 'Input too long; no truncation: ' + group + '/' + case['id'])
                    sizes.append(size)
                maximums[group] = max(sizes)
            save(args.run_dir / 'input_budget.json', dict(maximum_tokens=maximums, limit=args.max_input_tokens))
            model = Qwen3_5ForConditionalGeneration.from_pretrained(str(base.folder(args, '4b')),
                local_files_only=True, trust_remote_code=False, use_safetensors=True, dtype=torch.bfloat16,
                attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
            base.infer('행사명: 준비 예시', instruction, processor, model, args)
            requests_dir = args.run_dir / 'requests'
            requests_dir.mkdir(exist_ok=True)
            results = {}
            for group, cases in groups.items():
                group_dir = requests_dir / group
                group_dir.mkdir(exist_ok=True)
                results[group] = []
                for index, case in enumerate(cases, 1):
                    path = group_dir / (case['id'] + '.json')
                    if path.exists():
                        require(args.resume, 'Cached request requires --resume')
                        row = read(path)
                        retry.validate_cached(row, case, signature, args.prompt_profile)
                        chosen = row['retry'] if row['selected'] == 'retry' else row['first']
                        current = normalize_exact_duplicates(chosen, case['text'])
                        saved = row['postprocessed']
                        require({k:v for k,v in saved.items() if k != 'postprocess_ms'} ==
                                {k:v for k,v in current.items() if k != 'postprocess_ms'} and
                                type(saved['postprocess_ms']) in (int, float) and
                                math.isfinite(saved['postprocess_ms']) and saved['postprocess_ms'] >= 0,
                                'Cached postprocessing differs')
                    else:
                        started = time.perf_counter()
                        first = base.infer(case['text'], instruction, processor, model, args)
                        eligible = not retry.valid(first['parsed'])
                        again = retry.retry_infer(case['text'], first, instruction, processor, model, args) if eligible else None
                        selected = 'retry' if again is not None and not again['skipped'] and retry.valid(again['parsed']) else 'first'
                        elapsed = (time.perf_counter() - started) * 1000
                        chosen = again if selected == 'retry' else first
                        row = dict(protocol_signature=signature, case_id=case['id'], text_sha256=digest(case['text']),
                            first=first, retry_eligible=eligible, retry_feedback=retry.feedback(first) if eligible else None,
                            retry=again, selected=selected, first_ms=first['total_ms'], end_to_end_ms=elapsed,
                            postprocessed=normalize_exact_duplicates(chosen, case['text']))
                        save(path, row)
                    results[group].append(row)
                    print(json.dumps(dict(event='requirements_progress', dataset=group, completed=index,
                                          total=len(cases)), ensure_ascii=False), flush=True)
            require(hashes() == source_hashes and sha(args.review_file) == review_hash and
                    base.packet(args, '4b') == model_packet, 'Input, code or model changed during run')
            summary, labels, cells, records = [], [], [], []
            for group, cases in groups.items():
                for variant in ('final', 'normalized'):
                    s, l, c, r = score_group(group, cases, results[group], variant)
                    summary.append(s); labels.extend(l); cells.extend(c)
                    if variant == 'final':
                        records.extend(r)
            write_csv(args.run_dir / 'comparison.csv', summary)
            write_csv(args.run_dir / 'per_label.csv', labels)
            # Different datasets carry different review metadata. Preserve every
            # column instead of deriving the header from only the first row.
            fields = list(dict.fromkeys(key for row in cells for key in row))
            write_csv(args.run_dir / 'per_case_label.csv', cells, fields=fields)
            save(args.run_dir / 'predictions.json', records)
            save(args.run_dir / 'summary.json', dict(results=summary, **metadata,
                training_executed=False, installation_executed=False, synthetic=True, human_verified=False,
                independent_test=False, exposed_data=True,
                note='Text requirement label presence, including forbidden and optional; not image content. '
                     'Existing exposed synthetic datasets; H010/H032 are unreviewed and excluded from scoring. '
                     'Agent approvals are provisional. Unknown-positive output counts are review signals, not false positives.',
                timing='Includes initial request, validation and conditional retry; normalized adds postprocessing; '
                       'excludes loading, warmup, reporting and file writes.'))
            excluded = {'.lock', 'status.json', 'failure.json'}
            artifacts = {p.relative_to(args.run_dir).as_posix(): sha(p) for p in args.run_dir.rglob('*')
                         if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path, dict(status='complete', protocol_signature=signature, artifact_hashes=artifacts,
                training_executed=False, CUDA_executed=True, synthetic=True, human_verified=False, independent_test=False))
            print(json.dumps(dict(event='requirements_complete', run=str(args.run_dir)), ensure_ascii=False), flush=True)
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
    p.add_argument('--profile', choices=tuple(PROFILES), default='structure')
    p.add_argument('--review-file', type=Path, default=Path('/workspace/qwen-eval-agent-70-pending-2.json'))
    p.add_argument('--models-root', type=Path, default=Path('/workspace/models'))
    p.add_argument('--run-dir', type=Path)
    p.add_argument('--ram-policy', choices=('inactive-file', 'clean-unmapped-file'), default='inactive-file')
    p.add_argument('--max-new-tokens', type=int, default=768)
    p.add_argument('--max-input-tokens', type=int, default=4096)
    p.add_argument('--confirm-agent-partial', action='store_true')
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    if args.run_dir is None:
        args.run_dir = HERE / ('runs/qwen4b-all-requirements-' + args.profile + '-v2')
    for name in ('review_file', 'models_root', 'run_dir'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    require(256 <= args.max_new_tokens <= 2048 and 512 <= args.max_input_tokens <= 4096, 'Invalid token budget')
    require(not args.run_dir.is_relative_to(args.models_root) and not args.models_root.is_relative_to(args.run_dir) and
            not args.review_file.is_relative_to(args.run_dir), 'Run directory must be separate from inputs and models')
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
