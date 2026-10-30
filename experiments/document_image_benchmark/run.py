"""Read-only document/image inspection benchmark; model execution is opt-in."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time
import unicodedata

from core import compare, read, save, sha, validate_packet
from metrics import evaluate


def report(data):
    def number(value, percent=False):
        if value is None:
            return '해당 없음'
        return f'{value * 100:.2f}%' if percent else str(value)
    print('\n===== 문서·이미지 조건 대조 결과 =====', flush=True)
    print('예정 조건:', data['overall']['expected_count'])
    for key, title in [('human_verified', '사람 확정 정답 기준 성능'),
                       ('draft_agreement', '초안 정답과의 일치도 (독립 성능 아님)')]:
        row = data[key]
        print('\n' + title)
        print('평가 조건 수:', row['n'])
        for name, metric in [('정확도', 'accuracy'), ('Macro F1', 'macro_f1'),
                             ('자동 판정 비율', 'auto_coverage'), ('확인 필요 비율', 'review_rate'),
                             ('오류로 판정 불가한 비율', 'error_rate'), ('누락 예측 비율', 'missing_rate'),
                             ('위반 검출 재현율', 'fail_recall'), ('위반을 통과시킨 비율', 'false_pass_rate'),
                             ('위반 미검출 비율(보류·오류 포함)', 'missed_violation_rate')]:
            print(f'  {name}: {number(row[metric], True)}')
    print('\n미라벨 조건:', data['overall']['unlabeled_count'])
    print('하위 검사 실행 오류 수:', data.get('check_execution_errors', '미기록'))
    print('Macro F1은 충족/위반/확인필요 3종 평균이며, 미정의 클래스 F1은 0입니다.')
    print('기존 8개 이미지 라벨 F1과 다른 지표입니다. 합성·시연 자료는 서비스 일반화 성능이 아닙니다.')


def export_csv(path, predictions):
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['case_id', 'condition_id', 'level', 'requirement', 'execution_status', 'verdict', 'reason', 'evidence'])
        for row in predictions['results']:
            values = [row.get(key, '') for key in ('case_id', 'condition_id', 'level', 'requirement', 'execution_status', 'verdict', 'reason')]
            values.append(json.dumps(row.get('evidence', []), ensure_ascii=False))
            # Treat document text as text even when opened in a spreadsheet.
            writer.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else v for v in values])


def fresh_output(path, protected):
    path = path.resolve()
    for source in protected:
        if source is None:
            continue
        source = source.resolve()
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError('출력 폴더가 입력/모델/코드와 겹칩니다: ' + str(source))
    if path.exists():
        raise ValueError('출력 폴더가 이미 있습니다. 새 --output 경로를 지정하세요.')
    path.mkdir(parents=True, exist_ok=False)


def compare_runs(paths):
    if not paths or len(paths) < 2:
        raise ValueError('compare-runs에는 --runs 결과폴더1 결과폴더2가 필요합니다.')
    runs = [(read(p / 'run_manifest.json'), read(p / 'metrics.json')) for p in paths]
    identities = {(m['packet_sha256'], m['gold_sha256']) for m, _ in runs}
    if len(identities) != 1:
        raise ValueError('서로 다른 조건 패킷 또는 정답 버전의 실행은 직접 비교하지 않습니다.')
    for partition, title in [('human_verified', '사람 확정 정답'), ('draft_agreement', '초안 정답과의 일치도')]:
        rows = [['지표'] + [m['engine'] + f' [{i+1}]' for i, (m, _) in enumerate(runs)]]
        for label, key in [('평가 조건 수', 'n'), ('정확도', 'accuracy'), ('Macro F1', 'macro_f1'),
                           ('자동 판정 비율', 'auto_coverage'), ('확인 필요 비율', 'review_rate'),
                           ('위반 검출 재현율', 'fail_recall'), ('위반을 통과시킨 비율', 'false_pass_rate')]:
            values = [data[partition][key] for _, data in runs]
            rows.append([label] + [('해당 없음' if v is None else str(v) if key == 'n' else f'{v * 100:.2f}%') for v in values])
        def width(s):
            return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)
        widths = [max(width(r[i]) for r in rows) for i in range(len(rows[0]))]
        print('\n===== ' + title + ' =====')
        for row in rows:
            print(' | '.join(s + ' ' * (widths[i] - width(s)) for i, s in enumerate(row)))
            print('-+-'.join('-' * w for w in widths))
    for i, path in enumerate(paths, 1):
        print(f'[{i}] {path}')
    print('문서 조건별 3종 판정 지표입니다. 기존 이미지 8라벨 F1과 직접 비교하지 않습니다.')


def main():
    here = Path(__file__).resolve().parent
    package = here.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'preflight', 'run', 'compare', 'score', 'report', 'compare-runs'])
    parser.add_argument('--packet', type=Path, default=package / 'packet')
    parser.add_argument('--gold', type=Path, default=package / 'reviewer/gold.json')
    parser.add_argument('--output', type=Path, default=Path('/workspace/document-image-review-run-001'))
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--runs', nargs='+', type=Path)
    parser.add_argument('--engine', choices=['siglip_ocr', 'qwen'], default='siglip_ocr')
    parser.add_argument('--qwen-model-dir', type=Path, default=Path('/workspace/models/qwen3.5-4b'))
    parser.add_argument('--repo', type=Path, default=(here / 'dependencies' if (here / 'dependencies').exists()
                                                   else Path('/workspace/SKN33-Final-1Team')))
    parser.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    parser.add_argument('--reference', type=Path, default=Path('/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/runs/siglip-ai-reviewed-v1-fp32'))
    parser.add_argument('--ocr-python', type=Path, default=Path('/workspace/venvs/poster-paddleocr/bin/python'))
    parser.add_argument('--ocr-models', type=Path, default=Path('/workspace/SKN33-Final-1Team/.runtime/paddle-models'))
    parser.add_argument('--ocr-device', choices=['cpu'], default='cpu')
    parser.add_argument('--min-ocr-confidence', type=float, default=.90)
    parser.add_argument('--visual-margin', type=float, default=.10)
    args = parser.parse_args()
    if args.command == 'compare-runs':
        compare_runs(args.runs)
        return 0
    if not 0 <= args.min_ocr_confidence <= 1 or not 0 <= args.visual_margin <= 1:
        parser.error('판정 기준은 0~1 범위여야 합니다.')
    if args.command == 'report':
        report(read(args.output / 'metrics.json'))
        return 0
    packet = validate_packet(args.packet)
    digest = sha(args.packet / 'packet.json')
    info = {'schema': packet['schema'], 'packet_sha256': digest, 'cases': len(packet['cases']),
            'conditions': sum(len(c['conditions']) for c in packet['cases']),
            'groups': len({c['group_id'] for c in packet['cases']}),
            'inference_executed': False, 'training_executed': False,
            'document_extraction_evaluated': False,
            'condition_input': 'source-linked structured draft conditions', 'engine': args.engine,
            'conditions_with_unsupported_baseline_checks': sum(any(k['kind'] == 'unsupported' for k in c['checks'])
                for case in packet['cases'] for c in case['conditions'])}
    # Gold is intentionally not passed to the observer or comparator.
    gold = read(args.gold) if args.gold.is_file() else {
        'schema': 'document-image-gold-v1', 'packet_sha256': digest, 'labels': []}
    if gold.get('packet_sha256') != digest:
        raise ValueError('정답 파일의 조건 패킷 해시가 다릅니다.')
    empty_predictions = {'schema': 'document-image-predictions-v1', 'packet_sha256': digest, 'results': []}
    validation = evaluate(packet, gold, empty_predictions)
    info.update(human_verified_labels=validation['overall']['human_verified_count'],
                draft_labels=validation['overall']['draft_count'], unlabeled=validation['overall']['unlabeled_count'])
    if args.command == 'validate':
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    if args.command in ('preflight', 'run'):
        if args.engine == 'qwen':
            import qwen_judge as active_runtime
        else:
            import runtime as active_runtime
        checked = active_runtime.preflight(args)
        print(json.dumps({**info, 'runtime': checked}, ensure_ascii=False, indent=2), flush=True)
        if args.command == 'preflight' or not checked['ready']:
            return 0 if checked['ready'] else 2
    if args.command == 'compare' and not args.observations:
        parser.error('compare에는 --observations 파일이 필요합니다.')
    if args.command == 'score' and not args.predictions:
        parser.error('score에는 --predictions 파일이 필요합니다.')
    protected = [args.packet, args.gold, args.model_dir, args.reference, args.repo,
                 args.qwen_model_dir, args.ocr_models, args.observations, args.predictions, here]
    fresh_output(args.output, protected)
    started = time.monotonic()
    try:
        manifest = {**info, 'command': args.command, 'gold_sha256': sha(args.gold) if args.gold.is_file() else None,
                    'code_sha256': {p.name: sha(p) for p in here.glob('*.py')},
                    'min_ocr_confidence': args.min_ocr_confidence, 'visual_margin': args.visual_margin}
        manifest['initial_inference_executed'] = manifest.pop('inference_executed')
        save(args.output / 'run_manifest.json', manifest)
        if args.command == 'score':
            predictions = read(args.predictions)
            if predictions.get('packet_sha256') != digest:
                raise ValueError('판정 파일의 조건 패킷 해시가 다릅니다.')
        elif args.command == 'run' and args.engine == 'qwen':
            predictions = active_runtime.judge(args, packet, args.packet, args.output)
        else:
            if args.command == 'run':
                observations = active_runtime.observe(args, packet, args.packet, args.output)
            else:
                observations = read(args.observations)
            save(args.output / 'observations.json', observations)
            predictions = compare(packet, observations, digest, args.min_ocr_confidence, args.visual_margin)
        if sha(args.packet / 'packet.json') != digest:
            raise ValueError('실행 중 조건 패킷이 변경되었습니다.')
        metrics = evaluate(packet, gold, predictions)
        metrics['check_execution_errors'] = sum(c.get('execution_status') == 'error'
            for row in predictions['results'] for c in row.get('checks', []))
        save(args.output / 'predictions.json', predictions)
        save(args.output / 'metrics.json', metrics)
        export_csv(args.output / 'conditions.csv', predictions)
        save(args.output / 'status.json', {'status': 'completed', 'seconds': time.monotonic() - started,
                                         'training_executed': False, 'model_deployed': False,
                                         'inference_executed': (bool((predictions if args.engine == 'qwen' else observations)
                                             .get('runtime', {}).get('inference_executed')) if args.command == 'run' else False),
                                         'condition_execution_errors': metrics['overall']['prediction_counts']['execution_error']})
        report(metrics)
        print('\n결과 저장:', args.output, flush=True)
        return 0
    except Exception as exc:
        save(args.output / 'status.json', {'status': 'failed', 'error': str(exc),
                                         'training_executed': False, 'model_deployed': False})
        raise


if __name__ == '__main__':
    raise SystemExit(main())
