"""Recover complete cached predictions into a separate report; no GPU/model calls."""
import argparse
import fcntl
import math
from pathlib import Path
import json

import evaluate_all_requirements as evaluator
from requirements_compact import prompt
from requirements_condition_policy import normalize_exact_duplicates
from resultlib import digest, inside, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
# The only permitted old code difference is the known CSV header exporter bug.
LEGACY_EXPORTER_SHA = '1093147ea156619379ea4d56b2a4ea8873df0365137ff6ffee3a8b73d88a0440'
FIXED_EXPORTER_SHA = 'b7fa689275355f604ef97cdebac0fe9010f9044f6f4793e5907c113d44eae1b5'


def recover(args):
    protocol = read(args.source_run / 'protocol.json')
    config, signature = protocol['config'], protocol['signature']
    require(config['schema'] == 'all-requirements-development-v1' and digest(config) == signature,
            'Unexpected or altered source protocol')
    status_path = args.source_run / 'status.json'
    source_status = read(status_path)
    require(source_status['protocol_signature'] == signature and source_status['status'] in ('failed', 'complete'),
            'Source must be stopped and have a valid signature')
    require(set(config['code_and_data_hashes']) == set(evaluator.hashes()), 'Source dependency population differs')
    for name, expected in config['code_and_data_hashes'].items():
        actual = sha(HERE / name)
        require(actual == expected or name == 'evaluate_all_requirements.py' and
                expected == LEGACY_EXPORTER_SHA and actual == FIXED_EXPORTER_SHA,
                'Source dependency changed outside the known exporter fix: ' + name)
    require(sha(args.review_file) == config['review_sha256'], 'Review data changed')
    groups, metadata = evaluator.datasets(args)
    require({k: digest(v) for k, v in groups.items()} == config['datasets'], 'Case populations changed')
    profile = config['profile']
    require(profile in evaluator.PROFILES.values() and prompt(profile) == config['prompt'] and
            config['retry_instruction'] == evaluator.retry.RETRY_INSTRUCTION,
            'Original inference contract changed')
    evidence = {'protocol.json': sha(args.source_run / 'protocol.json'), 'status.json': sha(status_path)}
    results = {}
    # The existing writable lock file is used without truncation (Linux/NFS).
    with (args.source_run / '.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for dataset, cases in groups.items():
            folder = inside(args.source_run, 'requests/' + dataset)
            require({p.name for p in folder.glob('*.json')} == {c['id']+'.json' for c in cases},
                    'Missing or unexpected cached requests: ' + dataset)
            results[dataset] = []
            for case in cases:
                relative = 'requests/' + dataset + '/' + case['id'] + '.json'
                path = inside(args.source_run, relative)
                evidence[relative] = sha(path)
                row = read(path)
                evaluator.retry.validate_cached(row, case, signature, profile)
                chosen = row['retry'] if row['selected'] == 'retry' else row['first']
                replay = normalize_exact_duplicates(chosen, case['text'])
                cached = row['postprocessed']
                require({k:v for k,v in replay.items() if k != 'postprocess_ms'} ==
                        {k:v for k,v in cached.items() if k != 'postprocess_ms'} and
                        type(cached['postprocess_ms']) in (int,float) and
                        math.isfinite(cached['postprocess_ms']) and cached['postprocess_ms'] >= 0,
                        'Cached postprocessing differs: ' + relative)
                results[dataset].append(row)
        require(not args.out.exists(), 'Choose a new recovery output directory')
        comparisons, labels, cells, records = [], [], [], []
        for dataset, cases in groups.items():
            for variant in ('final', 'normalized'):
                s,l,c,r = evaluator.score_group(dataset, cases, results[dataset], variant)
                comparisons.append(s); labels.extend(l); cells.extend(c)
                if variant == 'final': records.extend(r)
        require(all(sha(inside(args.source_run,n)) == h for n,h in evidence.items()),
                'Source evidence changed during recovery')
        args.out.mkdir(parents=True, exist_ok=False)
        recovery_config = dict(schema='cached-requirements-report-recovery-v1',
            source_run=str(args.source_run), source_protocol_signature=signature, source_hashes=evidence,
            source_exporter_sha256=config['code_and_data_hashes']['evaluate_all_requirements.py'],
            recovery_code_sha256=sha(Path(__file__)), current_exporter_sha256=sha(HERE/'evaluate_all_requirements.py'),
            only_allowed_change='CSV header union; inference, parser, cases and metric contracts unchanged',
            inference_executed=False, training_executed=False)
        recovered_signature = digest(recovery_config)
        save(args.out/'protocol.json', dict(signature=recovered_signature, config=recovery_config))
        write_csv(args.out/'comparison.csv', comparisons)
        write_csv(args.out/'per_label.csv', labels)
        write_csv(args.out/'per_case_label.csv', cells,
                  fields=list(dict.fromkeys(key for row in cells for key in row)))
        save(args.out/'predictions.json', records)
        save(args.out/'summary.json', dict(results=comparisons, **metadata,
            recovered_from=str(args.source_run), original_status=source_status['status'],
            inference_executed=False, training_executed=False, synthetic=True, human_verified=False,
            independent_test=False, timing_source='original saved inference timings; no remeasurement'))
        names = ('protocol.json','comparison.csv','per_label.csv','per_case_label.csv','predictions.json','summary.json')
        save(args.out/'status.json', dict(status='complete', protocol_signature=recovered_signature,
            artifact_hashes={n:sha(args.out/n) for n in names}, inference_executed=False,
            CUDA_executed=False, training_executed=False, recovered_from=str(args.source_run)))
    print(json.dumps(dict(status='report_recovered', out=str(args.out),
        cached_cases=sum(len(c) for c in groups.values()), inference_executed=False), ensure_ascii=False, indent=2))
    print((args.out/'comparison.csv').read_text(encoding='utf-8-sig'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-run',type=Path,default=HERE/'runs/qwen4b-all-requirements-structure-v1')
    p.add_argument('--out',type=Path,default=HERE/'runs/qwen4b-all-requirements-structure-recovered-v1')
    p.add_argument('--review-file',type=Path,default=Path('/workspace/qwen-eval-agent-70-pending-2.json'))
    args=p.parse_args()
    for key in ('source_run','out','review_file'):
        setattr(args,key,getattr(args,key).expanduser().resolve())
    require(not args.out.is_relative_to(args.source_run) and not args.source_run.is_relative_to(args.out)
            and not args.review_file.is_relative_to(args.out), 'Recovery output must be separate')
    recover(args)


if __name__=='__main__': main()
