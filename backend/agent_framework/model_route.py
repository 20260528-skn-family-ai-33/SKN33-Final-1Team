"""실행별 모델 경로(2026-11-12 사용자 결정: 자료 입력 첫 화면의 보안 여부 선택).

- openai  : 보안이 필요 없는 일반 자료. A02 요구사항 추출과 A04/A05 포스터 검사를 OpenAI API 키로 보낸다.
            (포스터 로고는 위치 후보 탐지만 OpenAI, 정밀 대조는 기존 RunPod 워커 — backend/app/poster/logos)
- runpod  : 보안이 필요한 자료. 기존 Qwen/RunPod 파이프라인만 쓴다. Qwen 실패 뒤 OpenAI 대체 추출도 하지 않는다.
- default : 경로를 받지 않은 기존 실행·다른 화면. 이전과 똑같이 동작한다(설정돼 있으면 OpenAI 대체 추출 포함).

경로는 실행을 만들 때 input_json['model_route']에 고정되고(기본값은 저장하지 않아 기존 실행의 입력 해시가 그대로다),
포스터 검수 하위 실행은 기준을 만든 A02 실행의 경로를 물려받는다. 워커는 역할 호출 동안 using_route()로 경로를 알려 주고,
어댑터는 current_route()로 읽는다(공통 계약의 요청 스키마는 바꾸지 않는다).

키: openai_fallback.KEY_NAMES 순서(A02_OPENAI_API_KEY → OPENAI_API_KEY → creative_checker_openapi). 키 값은 어디에도 쓰지 않는다.
모델: OPENAI_ROUTE_MODEL → A02_OPENAI_MODEL → 기본값. 이미지(포스터·로고)를 보내므로 비전 입력을 받는 모델이어야 한다.
추론 강도: OPENAI_ROUTE_REASONING_EFFORT → A02_OPENAI_REASONING_EFFORT → low.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os

from .openai_fallback import DEFAULT_EFFORT, DEFAULT_MODEL, EFFORTS, KEY_NAMES, FallbackSettings
from .ports import AdapterFailure

ROUTES = ('default', 'openai', 'runpod')
ROUTE_ENV_NAMES = frozenset({'OPENAI_ROUTE_MODEL', 'OPENAI_ROUTE_REASONING_EFFORT'})
OPENAI_BASE_URL = 'https://api.openai.com/v1'
_route: ContextVar[str] = ContextVar('creative_checker_model_route', default='default')


def normalize(value) -> str:
    value = str(value or 'default').strip().lower()
    return value if value in ROUTES else 'default'


def route_of(fixed) -> str:
    """실행 input_json에서 경로를 읽는다. 값이 없거나 모르는 값이면 default."""
    return normalize((fixed or {}).get('model_route')) if isinstance(fixed, dict) else 'default'


def stored(route) -> str | None:
    """input_json에 적을 값. default는 적지 않아 기존 실행·요청과 입력 해시가 같다."""
    route = normalize(route)
    return None if route == 'default' else route


def current_route() -> str:
    return _route.get()


@contextmanager
def using_route(route):
    token = _route.set(normalize(route))
    try:
        yield
    finally:
        _route.reset(token)


def openai_settings(values=None) -> FallbackSettings:
    """openai 경로 설정. A02_OPENAI_FALLBACK 스위치와 무관하게 키만 있으면 쓴다(대체 추출과 별개 기능).
    키가 없으면 다른 경로로 몰래 바꾸지 않고 실패한다."""
    values = os.environ if values is None else values
    key = next((str(values.get(name) or '').strip() for name in KEY_NAMES if str(values.get(name) or '').strip()), '')
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise AdapterFailure('OPENAI_ROUTE_NOT_CONFIGURED',
                             '일반 자료 검수에 쓸 OpenAI API 키가 설정되지 않았습니다. 서버의 OpenAI 키 설정을 확인해 주세요.')
    model = (str(values.get('OPENAI_ROUTE_MODEL') or '').strip() or str(values.get('A02_OPENAI_MODEL') or '').strip()
             or DEFAULT_MODEL)
    effort = (str(values.get('OPENAI_ROUTE_REASONING_EFFORT') or '').strip()
              or str(values.get('A02_OPENAI_REASONING_EFFORT') or '').strip() or DEFAULT_EFFORT)
    return FallbackSettings(model=model, api_key=key, reasoning_effort=effort if effort in EFFORTS else DEFAULT_EFFORT)
