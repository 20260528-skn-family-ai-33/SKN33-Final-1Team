"""Apply local comparison rules to saved model/OCR outputs; never run inference."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from .structured_review import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in (args.input.resolve(), args.snapshot.resolve()):
        raise SystemExit('기존 파일을 덮어쓰지 않는 새 출력 경로를 지정하세요.')
    input_bytes = args.input.read_bytes()
    snapshot_bytes = args.snapshot.read_bytes()
    original = json.loads(input_bytes)
    snapshot = json.loads(snapshot_bytes)
    criteria = original['criteria']
    if (snapshot['criteria'] != criteria or
        snapshot['image_source']['original_sha256'] != original['image']['sha256']):
        raise SystemExit('결과와 원본 스냅샷의 기준 또는 이미지 해시가 다릅니다.')
    result = copy.deepcopy(original)
    if [r['criterion_id'] for r in result['results']] != [c['id'] for c in criteria]:
        raise SystemExit('검수 항목의 순서 또는 ID가 일치하지 않습니다.')
    before = {r['criterion_id']: r['status'] for r in result['results']}
    result['results'] = compare(criteria, result['results'], result['ocr']['records'], snapshot['image_source'])
    result['posthoc_comparison'] = {
        'method': 'saved_ocr_and_model_outputs_no_inference',
        'original_sha256': hashlib.sha256(input_bytes).hexdigest(),
        'snapshot_sha256': hashlib.sha256(snapshot_bytes).hexdigest(),
        'note': '별도 후처리 초안이며 기존 검수 기록을 수정하지 않습니다. 원본과 함께 확인하세요.',
        'status_changes': [{'criterion_id': r['criterion_id'], 'before': before[r['criterion_id']],
                            'after': r['status']} for r in result['results'] if before[r['criterion_id']] != r['status']],
    }
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print('저장된 결과 비교 완료:', args.output)
    for row in result['results']:
        print(f"{row['criterion_id']} | {before[row['criterion_id']]} → {row['status']} | {row['criterion']}")


if __name__ == '__main__':
    main()
