"""A07 Qwen 문장 다듬기 호출. OpenAI 호환 /v1/chat/completions(vLLM)에 prompt.build_payload를 보낸다.

- 서버는 팀 공용 Qwen3.5-4B를 쓴다(A07이 따로 띄우지 않는다). create_reporter()는 팀 공용 설정 CC_QWEN_BASE_URL이
  있을 때만 붙인다.
- 연결 실패·거절·잘린 응답·형식·check 위반은 None을 돌려준다. 보고기는 규칙 문장을 쓴다.
  이유는 last_problems에 남긴다.
- 배치마다 한 번만 전송한다. 전송 후 통신 오류는 결과 불명으로 올리고 자동 재호출하지 않는다.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.a07_reporter.adapter import ItemReport
from backend.a07_reporter.prompt import MAX_ITEMS_PER_CALL, MODEL, build_payload, check, prompt_items
from backend.agent_framework.ports import AdapterFailure

MAX_RESPONSE_BYTES = 1024 * 1024


def _endpoint(base_url) -> str:
    parsed = urlsplit(base_url) if isinstance(base_url, str) else None
    if (parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise AdapterFailure("QWEN_CONFIG_INVALID", "Qwen 서버 주소가 올바르지 않습니다.")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions" if path.endswith("/v1") else "/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class QwenPolisher:
    """TeamA07Reporter의 문장 다듬기. polish()는 조건 ID별 (explanation, action) 또는 None."""

    def __init__(self, base_url: str, *, api_key: str | None = None, model: str = MODEL, timeout: float = 60.0,
                 example: bool = False, transport: httpx.BaseTransport | None = None):
        self.url, self.model, self.example = _endpoint(base_url), model, example
        headers = {'Accept-Encoding':'identity',**({"Authorization": f"Bearer {api_key}"} if api_key else {})}
        self.http = httpx.Client(headers=headers, timeout=httpx.Timeout(timeout, connect=10.0), transport=transport,
                                 trust_env=False, follow_redirects=False)
        self.calls = 0
        self.last_problems: tuple[str, ...] = ()

    def close(self):
        self.http.close()

    def _post(self, payload: dict) -> dict | None:
        self.calls += 1
        try:
            with self.http.stream('POST',self.url,json=payload) as response:
                if response.status_code>=500:
                    self.last_problems=(f'Qwen 호출 결과를 알 수 없습니다(HTTP {response.status_code}).',)
                    raise AdapterFailure('MODEL_OUTCOME_UNKNOWN',self.last_problems[0],outcome_unknown=True)
                if response.status_code!=200:
                    self.last_problems=(f'Qwen 서버가 요청을 거절했습니다(HTTP {response.status_code}).',)
                    return None
                if response.headers.get('content-encoding','identity').lower()!='identity':
                    self.last_problems=('압축된 Qwen 응답은 허용하지 않습니다.',)
                    return None
                content=bytearray()
                for chunk in response.iter_bytes():
                    if len(content)+len(chunk)>MAX_RESPONSE_BYTES:
                        self.last_problems=('Qwen 응답이 크기 제한을 넘었습니다.',)
                        return None
                    content.extend(chunk)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol):
            self.last_problems = ("Qwen 서버에 연결하지 못했습니다.",)
            return None
        except httpx.TransportError:
            self.last_problems = ("Qwen 호출 결과를 알 수 없습니다. 자동으로 다시 호출하지 않습니다.",)
            raise AdapterFailure("MODEL_OUTCOME_UNKNOWN", self.last_problems[0], outcome_unknown=True) from None
        return json.loads(content)

    def polish(self, reports: tuple[ItemReport, ...]) -> dict[str, tuple[str, str]] | None:
        self.last_problems = ()
        items = prompt_items(reports)
        out = {}
        for start in range(0, len(items), MAX_ITEMS_PER_CALL):
            chunk = items[start:start + MAX_ITEMS_PER_CALL]
            try:
                envelope = self._post(build_payload(chunk, model=self.model, example=self.example))
                if envelope is None:
                    return None
                choice, = envelope["choices"]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("truncated or refused")
                output = json.loads(choice["message"]["content"])
            except (ValueError, KeyError, TypeError):
                self.last_problems = ("Qwen 응답 형식이 올바르지 않습니다.",)
                return None
            problems = check(chunk, output)
            if problems:
                self.last_problems = problems
                return None
            out.update({row["criterion_id"]: (row["explanation"], row["action"]) for row in output["items"]})
        return out
