"""A02 문서 추출의 OpenAI 대체 호출(2026-11-12 베타테스트용, 팀 합의).

Qwen 호출·응답 검증이 실패하면 같은 프롬프트·같은 원문 줄 스키마로 OpenAI에 한 번 더 보낸다.
돌아온 결과는 Qwen 결과와 똑같은 원문 대조 검증을 거친다(검증을 건너뛰지 않는다).
추출 기록에는 실제로 쓴 모델(OpenAI)과 Qwen 실패 코드를 남긴다.

끄기: A02_OPENAI_FALLBACK=0. 키: A02_OPENAI_API_KEY → OPENAI_API_KEY → creative_checker_openapi 순서.
모델: A02_OPENAI_MODEL(기본 gpt-6-luna), 추론 강도: A02_OPENAI_REASONING_EFFORT(기본 low).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from time import monotonic

ENDPOINT = 'https://api.openai.com/v1/chat/completions'
DEFAULT_MODEL = 'gpt-6-luna'
DEFAULT_EFFORT = 'low'
EFFORTS = ('none', 'low', 'medium', 'high', 'xhigh', 'max')
KEY_NAMES = ('A02_OPENAI_API_KEY', 'OPENAI_API_KEY', 'creative_checker_openapi')
ENV_NAMES = frozenset({'A02_OPENAI_FALLBACK', 'A02_OPENAI_MODEL', 'A02_OPENAI_REASONING_EFFORT', *KEY_NAMES})
MAX_RESPONSE = 2 * 1024 * 1024
WARNING = 'openai_fallback_after_qwen_failure'
# 문서 자체의 문제(글자층 없음·크기 초과 등)는 모델을 바꿔도 같은 글자가 가므로 넘기지 않는다.
DOCUMENT_PREFIXES = ('PDF_', 'FILE_', 'DOCUMENT_', 'TEXT_', 'MOCK_')


class FallbackFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FallbackSettings:
    model: str
    api_key: str = field(repr=False)
    reasoning_effort: str = DEFAULT_EFFORT
    timeout_seconds: int = 300
    max_completion_tokens: int = 32768
    endpoint: str = ENDPOINT


def fallback_settings(values=None) -> FallbackSettings | None:
    """키가 있고 스위치가 꺼져 있지 않을 때만 설정을 돌려준다. 키 값은 어디에도 쓰지 않는다."""
    values = os.environ if values is None else values
    if str(values.get('A02_OPENAI_FALLBACK', '1')).strip().lower() in ('0', 'false', 'off', 'no'):
        return None
    key = next((str(values.get(name) or '').strip() for name in KEY_NAMES if str(values.get(name) or '').strip()), '')
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        return None
    model = str(values.get('A02_OPENAI_MODEL') or '').strip() or DEFAULT_MODEL
    effort = str(values.get('A02_OPENAI_REASONING_EFFORT') or '').strip() or DEFAULT_EFFORT
    return FallbackSettings(model=model, api_key=key, reasoning_effort=effort if effort in EFFORTS else DEFAULT_EFFORT)


def should_fall_back(code: str) -> bool:
    return not str(code).startswith(DOCUMENT_PREFIXES)


def strict_schema(schema: dict) -> dict:
    """OpenAI strict 모드는 모든 객체 속성을 required에 넣어야 한다(값 허용 범위는 그대로)."""
    value = deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get('properties'), dict):
                node['required'] = list(node['properties'])
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return value


def call_openai(settings: FallbackSettings, *, system: str, user: str | list, schema: dict, name: str,
                transport=None) -> tuple[bytes, str]:
    """요청 1회. 모델 응답 본문(JSON 문자열의 UTF-8)과 응답에 적힌 모델 이름을 돌려준다.
    user는 글자 또는 이미지가 섞인 content 배열(포스터 로고 위치 탐지, backend/app/poster/logos/openai_candidates.py)이다."""
    import httpx
    body = {'model': settings.model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'max_completion_tokens': settings.max_completion_tokens, 'reasoning_effort': settings.reasoning_effort,
            'response_format': {'type': 'json_schema', 'json_schema': {'name': name, 'strict': True, 'schema': strict_schema(schema)}}}
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Authorization': 'Bearer ' + settings.api_key}
    deadline = monotonic() + settings.timeout_seconds
    try:
        with httpx.Client(transport=transport or httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False,
                          timeout=httpx.Timeout(settings.timeout_seconds, connect=10), headers=headers) as client:
            with client.stream('POST', settings.endpoint, json=body) as response:
                if response.status_code >= 500:
                    raise FallbackFailure('OPENAI_OUTCOME_UNKNOWN')
                if response.status_code != 200:
                    raise FallbackFailure('OPENAI_HTTP_REJECTED')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() > deadline:
                        raise FallbackFailure('OPENAI_OUTCOME_UNKNOWN')
                    if len(raw) + len(chunk) > MAX_RESPONSE:
                        raise FallbackFailure('OPENAI_RESPONSE_TOO_LARGE')
                    raw.extend(chunk)
    except FallbackFailure:
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol):
        raise FallbackFailure('OPENAI_UNAVAILABLE') from None
    except httpx.TransportError:
        raise FallbackFailure('OPENAI_OUTCOME_UNKNOWN') from None
    try:
        envelope = json.loads(bytes(raw))
        choices = envelope['choices']
        if len(choices) != 1:
            raise FallbackFailure('OPENAI_RESPONSE_INVALID')
        message = choices[0]['message']
        if message.get('refusal'):
            raise FallbackFailure('OPENAI_REFUSED')
        if choices[0]['finish_reason'] != 'stop':
            raise FallbackFailure('OPENAI_OUTPUT_INCOMPLETE')
        content = message['content']
        if not isinstance(content, str):
            raise FallbackFailure('OPENAI_RESPONSE_INVALID')
        model = envelope.get('model') or settings.model
        if not isinstance(model, str) or not model.startswith(settings.model):
            raise FallbackFailure('OPENAI_MODEL_MISMATCH')
        return content.encode('utf-8'), settings.model
    except FallbackFailure:
        raise
    except (ValueError, KeyError, TypeError, IndexError, UnicodeError):
        raise FallbackFailure('OPENAI_RESPONSE_INVALID') from None
