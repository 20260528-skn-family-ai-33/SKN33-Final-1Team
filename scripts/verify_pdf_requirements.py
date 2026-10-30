"""기존 PDF로 실제 PaddleOCR + 로컬 vision LLM 추출 결과와 실행 증거를 저장한다."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import requirements_pdf as pdf
from backend.app.model_ocr import ollama_request
from scripts.local_ollama import requirements_model, requirements_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('documents', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if requirements_status() != 'ready':
        parser.error('로컬 vision 모델이 준비되지 않았습니다. local_ollama.py serve/pull을 확인하세요.')
    model = requirements_model()
    runtime = {'model': model, 'ollama': ollama_request('/api/version'),
               'installed_model': next(m for m in ollama_request('/api/tags')['models'] if m['name'] == model),
               'model_info': ollama_request('/api/show', {'model': model}),
               'platform': platform.platform(), 'python': platform.python_version(),
               'extractor_sha256': hashlib.sha256(Path(pdf.__file__).read_bytes()).hexdigest(),
               'code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                              ('backend/app/requirements_pdf.py', 'backend/app/document_sources.py',
                               'backend/app/document_conditions.py')}}
    # 대용량 템플릿·가중치 메타데이터 대신 재현에 필요한 모델 정체성만 보존한다.
    runtime['model_info'] = {k: runtime['model_info'].get(k) for k in ('details', 'capabilities', 'parameters')}
    (args.output / 'runtime.json').write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + '\n')
    summary = []
    actual_chat = pdf._chat
    for index, path in enumerate(args.documents, 1):
        calls = []
        def traced_chat(messages, schema, model, deadline):
            context = json.loads(messages[1]['content'])
            evidence = {'pages': context['extraction_pages'],
                        'phase': context.get('phase', 'primary'),
                        'sources': context['sources'],
                        'image_count': sum(len(m.get('images', [])) for m in messages),
                        'image_bytes': [len(base64.b64decode(m['images'][0])) for m in messages if m.get('images')],
                        'ocr_source_count': len(context['sources']),
                        'allowed_source_ids': schema['$defs']['Condition']['properties']['source_id']['enum']}
            calls.append(evidence)
            start = time.monotonic()
            try:
                result = actual_chat(messages, schema, model, deadline)
            except pdf.DocumentError as error:
                evidence.update(seconds=round(time.monotonic() - start, 3), error=str(error))
                raise
            evidence.update(seconds=round(time.monotonic() - start, 3), raw_output=result)
            return result
        pdf._chat = traced_chat
        print(f'[{index}/{len(args.documents)}] {path.name} 시작', flush=True)
        started = time.monotonic()
        data = path.read_bytes()
        evidence = {'document': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest()}
        try:
            evidence.update(status='success', result=pdf.extract_pdf(data))
        except pdf.DocumentError as error:
            evidence.update(status='failed', error=str(error), http_status=error.status)
        finally:
            pdf._chat = actual_chat
        evidence.update(elapsed_seconds=round(time.monotonic() - started, 3), calls=calls)
        filename = f'{index:02d}-{path.stem}.json'
        (args.output / filename).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        row = {k: evidence[k] for k in ('document', 'sha256', 'status', 'elapsed_seconds')}
        row.update(result_file=filename, conditions=len(evidence.get('result', {}).get('conditions', [])),
                   image_count=sum(call['image_count'] for call in calls))
        summary.append(row)
        (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return int(any(row['status'] != 'success' for row in summary))


if __name__ == '__main__':
    raise SystemExit(main())
