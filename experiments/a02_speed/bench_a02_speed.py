"""A02 조건 추출 속도 측정: 지금 방식(요청 1회) vs 블록 병렬(요청 N개 동시).

실제 Qwen 서버에 같은 문서를 보내 걸린 시간과 vLLM이 돌려준 토큰 수(usage)를 기록한다.
DB·파일 저장소·승인은 건드리지 않는다. 결과는 승인되지 않은 초안의 '개수·거부 사유'만 비교한다.

- single: 운영 QwenProvider를 그대로 호출한다(같은 프롬프트·스키마·요청 본문). 응답만 옆에서 기록한다.
- blocks: 원문을 블록(PDF 쪽, DOCX 문단 묶음·표, 텍스트 빈 줄 단위)으로 나눠 --parallel 개 요청으로 묶고
  동시에 보낸다. 각 요청은 자기 줄 ID만 고를 수 있다(스키마 enum). 결과는 합친 뒤 운영과 같은
  materialize_semantics로 원문 대조한다. 서버가 --max-num-seqs 1이면 요청이 줄을 서므로 빨라지지 않는다.

실행(서버 주소는 운영과 같은 환경 변수):
  CC_QWEN_BASE_URL=http://127.0.0.1:18015/v1 A02_QWEN_API_KEY=... \\
  python experiments/a02_speed/bench_a02_speed.py <문서.pdf|.docx|.txt> --mode both --parallel 4 --out result.json
모델 없이 배선만 확인: --fake
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.agent_framework.a02 import QwenProvider, prepare_sources  # noqa: E402
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration  # noqa: E402
from backend.agent_framework.a02_semantic import (PRESENTATION_PROMPT, SEMANTIC_PROMPT, SemanticReply,  # noqa: E402
    SemanticRequirement, materialize_semantics, semantic_schema)
from backend.agent_framework.a02_source_selection import index_sources  # noqa: E402
from backend.agent_framework.contracts import DocumentRef  # noqa: E402
from backend.agent_framework.ports import AdapterFailure  # noqa: E402

VERSION = 'bench-document-version'


# ---------------------------------------------------------------- 문서 -> 원문 블록(운영 함수 그대로)

def load_sources(path: Path, settings):
    raw = path.read_bytes()
    kind = 'pdf'
    if raw.startswith(b'%PDF-'):
        from backend.agent_framework.files import inspect_document
        reference = DocumentRef(file_id='bench', document_version_id=VERSION, sha256=sha256(raw).hexdigest(),
                                media_type='application/pdf', page_count=inspect_document(raw))
        content = raw
    else:
        if raw.startswith(b'PK\x03\x04'):
            from backend.agent_framework.docx_text import docx_requirement_text
            started = perf_counter()
            content = docx_requirement_text(raw).text.encode('utf-8')
            kind = f'docx ({(perf_counter() - started) * 1000:.1f}ms 변환)'
        else:
            content, kind = raw, 'text'
        reference = DocumentRef(file_id='bench', document_version_id=VERSION, sha256=sha256(content).hexdigest(),
                                media_type='text/plain', page_count=1)
    started = perf_counter()
    sources = prepare_sources(SimpleNamespace(content=content, reference=reference), settings)
    return kind, sources, (perf_counter() - started) * 1000


# ---------------------------------------------------------------- 응답 기록용 전송층

class RecordingTransport(httpx.BaseTransport):
    """운영 QwenProvider 요청을 그대로 보내고, 응답 본문(usage 포함)만 옆에 남긴다."""
    def __init__(self, inner):
        self.inner, self.bodies = inner, []

    def handle_request(self, request):
        response = self.inner.handle_request(request)
        body = response.read()
        self.bodies.append(body)
        headers = [(k, v) for k, v in response.headers.items() if k.lower() not in ('transfer-encoding', 'content-length')]
        return httpx.Response(response.status_code, headers=headers, content=body)


def fake_transport(presentation):
    """모델 없이 배선만 확인한다. 각 요청의 첫 줄 하나를 미정 항목으로 돌려준다(품질 의미 없음)."""
    def respond(request):
        body = json.loads(request.content)
        enum = body['response_format']['json_schema']['schema']['$defs']['SourceRange']['properties']['start_line']['enum']
        item = {'source': {'start_line': enum[0], 'end_line': enum[0]}, 'target': 'unspecified', 'field': 'unspecified',
                'kind': 'unsupported', 'operator': 'manual', 'expected': '', 'normalized_expected': None, 'unit': None,
                'required': True, 'unresolved': True, 'qualifiers': [], 'presentation': None}
        content = json.dumps({'requirements': [item]}, ensure_ascii=False)
        return httpx.Response(200, json={'model': body['model'], 'usage': {'prompt_tokens': 0, 'completion_tokens': 0},
                                         'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
    return httpx.MockTransport(respond)


def usage(body):
    try:
        data = json.loads(body)
        return data.get('usage') or {}, data['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError):
        return {}, None


# ---------------------------------------------------------------- 블록 나누기

def segments(payload):
    """빈 줄(줄 번호가 끊긴 곳)과 블록 경계에서 자른 연속 줄 묶음."""
    result = []
    for block in payload:
        current, last = [], None
        for line in block['lines']:
            number = int(line['id'].rsplit(':L', 1)[1])
            if current and last is not None and number != last + 1:
                result.append((block, current)); current = []
            current.append(line); last = number
        if current:
            result.append((block, current))
    return result


def chunks(payload, parallel):
    """순서를 지키며 글자 수가 비슷하도록 묶음을 parallel개 이하 요청으로 나눈다."""
    parts = segments(payload)
    total = sum(len(l['text']) for _, lines in parts for l in lines)
    target, groups, current, size = total / max(1, parallel), [], [], 0
    for block, lines in parts:
        length = sum(len(l['text']) for l in lines)
        if current and size + length > target and len(groups) < parallel - 1:
            groups.append(current); current, size = [], 0
        current.append((block, lines)); size += length
    if current:
        groups.append(current)
    requests = []
    for group in groups:
        merged = {}
        for block, lines in group:
            entry = merged.setdefault(block['block_id'], {'block_id': block['block_id'], 'page': block['page'], 'lines': []})
            entry['lines'].extend({'id': l['id'], 'text': l['text']} for l in lines)
        requests.append(list(merged.values()))
    return requests


def block_request(settings, sources_payload, presentation):
    ids = [line['id'] for source in sources_payload for line in source['lines']]
    schema = semantic_schema(SimpleNamespace(lines={i: None for i in ids}), presentation=presentation)
    schema['properties']['requirements']['minItems'] = 0  # 요구사항이 없는 블록(서명란 등)은 빈 목록 허용
    return {'model': settings.model, 'messages': [
                {'role': 'system', 'content': PRESENTATION_PROMPT if presentation else SEMANTIC_PROMPT},
                {'role': 'user', 'content': json.dumps({'sources': sources_payload}, ensure_ascii=False)}],
            'stream': False, 'temperature': 0, 'max_tokens': settings.max_output_tokens,
            'chat_template_kwargs': {'enable_thinking': False},
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'poster_semantic_requirements', 'strict': True, 'schema': schema}}}


# ---------------------------------------------------------------- 실행

def summarize(reply, index, sources, presentation):
    try:
        items, warnings, rejections = materialize_semantics(reply, index, sources, VERSION, presentation=presentation)
    except AdapterFailure as failure:
        return {'accepted': 0, 'rejected': [failure.code], 'fields': [], 'warnings': []}
    return {'accepted': len(items), 'rejected': [r.code for r in rejections],
            # 거부 원인을 볼 수 있게 거부된 모델 후보 원문(줄 ID·기대값·표시 문단)을 함께 남긴다.
            'rejected_items': [{'candidate_index': r.candidate_index, 'code': r.code,
                                'candidate': reply.requirements[r.candidate_index - 1].model_dump(mode='json')}
                               for r in rejections],
            'fields': [i.details.field for i in items], 'warnings': list(warnings),
            # 비교용 초안 내용(승인 아님): 조건 문장·기대값·연산·표시 문단·원문 인용
            'items': [{'field': i.details.field, 'text': i.text, 'expected': i.expected, 'operator': i.operator,
                       'unresolved': i.unresolved, 'quote': i.quote,
                       'presentation': (i.details.presentation.model_dump() if i.details.presentation else None)}
                      for i in items]}


def run_single(settings, sources, index, api_key, transport, presentation):
    recorder = RecordingTransport(transport)
    started = perf_counter()
    try:
        reply = QwenProvider(settings, api_key=api_key, transport=recorder)(sources)
    except AdapterFailure as failure:
        # 운영과 같은 검증에서 실패하면 원인을 볼 수 있게 응답 원문·종료 사유를 남기고 다음 측정으로 넘어간다.
        seconds = perf_counter() - started
        used, content = usage(recorder.bodies[0]) if recorder.bodies else ({}, None)
        try:
            finish = json.loads(recorder.bodies[0])['choices'][0]['finish_reason']
        except (IndexError, KeyError, TypeError, ValueError):
            finish = None
        return {'wall_seconds': round(seconds, 2), 'requests': [request_row(seconds, used)], 'accepted': 0,
                'rejected': [failure.code], 'fields': [], 'warnings': [], 'items': [],
                'finish_reason': finish, 'raw_content': content}
    seconds = perf_counter() - started
    used, _ = usage(recorder.bodies[0]) if recorder.bodies else ({}, None)
    return {'wall_seconds': round(seconds, 2), 'requests': [request_row(seconds, used)],
            **summarize(reply.result, index, sources, presentation)}


def request_row(seconds, used):
    completion = used.get('completion_tokens')
    return {'seconds': round(seconds, 2), 'prompt_tokens': used.get('prompt_tokens'), 'completion_tokens': completion,
            'completion_tokens_per_second': round(completion / seconds, 1) if completion else None}


def run_blocks(settings, payload, sources, index, api_key, transport, parallel, presentation):
    groups = chunks(payload, parallel)
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key

    def send(client, group):
        body = block_request(settings, group, presentation)
        started = perf_counter()
        response = client.post(settings.endpoint, json=body)
        seconds = perf_counter() - started
        if response.status_code != 200:
            raise SystemExit(f'Qwen HTTP {response.status_code}: {response.text[:300]}')
        used, content = usage(response.content)
        finish = json.loads(response.content)['choices'][0]['finish_reason']
        if finish != 'stop':
            # 끝까지 생성되지 않은 조각은 원문과 종료 사유를 남긴다(측정 전체를 멈추지 않음).
            return seconds, used, None, {'finish_reason': finish, 'raw_content': content}
        items = [SemanticRequirement.model_validate(item) for item in json.loads(content)['requirements']]
        return seconds, used, items, None

    started = perf_counter()
    # 한 Client(연결 풀)를 스레드들이 함께 쓴다. 요청은 동시에 나가고 서버가 묶어 처리할지 정한다.
    with httpx.Client(transport=transport, timeout=settings.timeout_seconds, follow_redirects=False,
                      trust_env=False, headers=headers) as client, ThreadPoolExecutor(max_workers=len(groups)) as pool:
        results = list(pool.map(lambda group: send(client, group), groups))
    wall = perf_counter() - started
    failures = [failure for *_, failure in results if failure]
    if failures:
        return {'wall_seconds': round(wall, 2), 'chunks': [sum(len(s['lines']) for s in g) for g in groups],
                'requests': [request_row(s, u) for s, u, _, _ in results], 'accepted': 0,
                'rejected': ['QWEN_OUTPUT_INCOMPLETE'] * len(failures), 'fields': [], 'warnings': [], 'items': [],
                'incomplete_chunks': failures}
    results = [(s, u, items) for s, u, items, _ in results]
    merged = [item for _, _, items in results for item in items]
    if not merged:
        return {'wall_seconds': round(wall, 2), 'requests': [request_row(s, u) for s, u, _ in results],
                'accepted': 0, 'rejected': ['NO_REQUIREMENTS'], 'fields': [], 'warnings': []}
    reply = SemanticReply.model_validate({'requirements': [item.model_dump() for item in merged]})
    return {'wall_seconds': round(wall, 2), 'chunks': [sum(len(s['lines']) for s in g) for g in groups],
            'requests': [request_row(s, u) for s, u, _ in results], **summarize(reply, index, sources, presentation)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('document', type=Path)
    parser.add_argument('--mode', choices=('single', 'blocks', 'both'), default='both')
    parser.add_argument('--parallel', type=int, default=4)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--no-presentation', action='store_true', help='v3(문단 없음)로 측정. 기본은 운영 v4(문단 포함).')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--max-output-tokens', type=int, default=None,
                        help='진단용 출력 상한. 생략하면 운영 기본값(8192)을 그대로 쓴다.')
    parser.add_argument('--fake', action='store_true', help='모델 없이 배선만 확인(시간·품질 의미 없음)')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    presentation = not args.no_presentation
    base = os.environ.get('CC_QWEN_BASE_URL', '').rstrip('/') or ('https://qwen.invalid/v1' if args.fake else '')
    if not base:
        raise SystemExit('CC_QWEN_BASE_URL(예: http://127.0.0.1:18015/v1)을 설정하세요.')
    api_key = None if args.fake else (os.environ.get('A02_QWEN_API_KEY') or os.environ.get('CC_QWEN_API_KEY') or None)
    limits = {'max_output_tokens': args.max_output_tokens} if args.max_output_tokens else {}
    settings = PDFExtractionConfiguration(mode='qwen', endpoint=base + '/chat/completions', presentation=presentation,
                                          model=os.environ.get('CC_QWEN_MODEL') or 'Qwen/Qwen3.5-4B', timeout_seconds=args.timeout,
                                          **limits)
    kind, sources, parse_ms = load_sources(args.document, settings)
    index, payload = index_sources(sources)
    payload = [{**s, 'lines': [{'id': l['id'], 'text': l['text']} for l in s['lines']]} for s in payload]
    report = {'document': args.document.name, 'kind': kind, 'parse_ms': round(parse_ms, 1),
              'characters': sum(len(s.text) for s in sources), 'lines': len(index.lines), 'blocks': len(sources),
              'presentation': presentation, 'parallel': args.parallel, 'fake': args.fake,
              'max_output_tokens': settings.max_output_tokens, 'runs': []}
    for attempt in range(1, args.repeat + 1):
        transport = fake_transport(presentation) if args.fake else httpx.HTTPTransport(retries=0)
        if args.mode in ('single', 'both'):
            report['runs'].append({'attempt': attempt, 'mode': 'single',
                                   **run_single(settings, sources, index, api_key, transport, presentation)})
        if args.mode in ('blocks', 'both'):
            report['runs'].append({'attempt': attempt, 'mode': f'blocks x{args.parallel}',
                                   **run_blocks(settings, payload, sources, index, api_key, transport, args.parallel, presentation)})
    for run in report['runs']:
        tokens = [r['completion_tokens'] for r in run['requests'] if r['completion_tokens'] is not None]
        print(f"{run['mode']:<10} #{run['attempt']} wall {run['wall_seconds']:>7.2f}s  requests {len(run['requests'])}"
              f"  output_tokens {sum(tokens) if tokens else '-'}  accepted {run['accepted']}  rejected {len(run['rejected'])}")
    print(f"문서 {report['document']} · {kind} · 파싱 {report['parse_ms']}ms · {report['characters']}자 · {report['lines']}줄")
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
