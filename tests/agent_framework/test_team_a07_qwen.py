"""A07 Qwen 문장 다듬기 호출: 가짜 서버(httpx.MockTransport)로 성공·재시도·실패 시 규칙 문장. 실제 서버 없음."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.a07_reporter.adapter import POLISH_NOTICE, TeamA07Reporter, create_reporter, items
from backend.a07_reporter.qwen import QwenPolisher
from backend.agent_framework.contracts import ReportRequest
from backend.agent_framework.invocation import invoke_role
from backend.agent_framework.ports import AdapterFailure

CHECKS = Path(__file__).resolve().parents[2] / "examples/agent_framework/checks"
GOOD = {"items": [{"criterion_id": "requirement-number",
                   "explanation": "딸기 개수가 기준과 다릅니다. 기준은 3개입니다.",
                   "action": "딸기가 3개 보이도록 고쳐 주세요."}]}


def fixture(name) -> ReportRequest:
    return ReportRequest.model_validate_json((CHECKS / f"A07.{name}.json").read_text(encoding="utf-8"))


def envelope(output, finish="stop"):
    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}]}


def server(*responses):
    """응답을 차례로 돌려주는 가짜 서버. 보낸 요청은 sent에 쌓인다."""
    queue, sent = list(responses), []
    def handle(request: httpx.Request):
        sent.append(json.loads(request.content))
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)
    return httpx.MockTransport(handle), sent


def polisher(*responses):
    transport, sent = server(*responses)
    return QwenPolisher("http://127.0.0.1:18290", transport=transport), sent


def test_polished_sentences_are_added_without_changing_facts():
    request = fixture("fail")
    qwen, sent = polisher(envelope(GOOD))
    plain = invoke_role("A07", TeamA07Reporter(), request)
    draft = invoke_role("A07", TeamA07Reporter(qwen), request)
    payload, = sent
    assert payload["temperature"] == 0 and payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "requirement-text" not in payload["messages"][-1]["content"]   # 통과 항목은 보내지 않는다
    assert (draft.verdict, draft.evidence_ids) == (plain.verdict, plain.evidence_ids)
    assert POLISH_NOTICE in draft.summary and "  할 일: 딸기가 3개 보이도록 고쳐 주세요." in draft.summary
    assert all(line in draft.summary.splitlines() for line in plain.summary.splitlines())   # 규칙 사실은 그대로
    change, = draft.change_requests
    assert change.startswith("수정 요청 [requirement-number] 딸기가 3개 보이도록 고쳐 주세요. 기준 원문:")
    assert change.endswith("설명: 딸기 개수가 기준과 다릅니다. 기준은 3개입니다.")


@pytest.mark.parametrize(("responses", "problem"), [
    ((envelope({"items": [{**GOOD["items"][0], "explanation": "딸기가 5개로 보입니다."}]}),), "입력에 없는 숫자 5"),
    ((envelope(GOOD, finish="length"),), "형식"),
    ((envelope("이건 JSON이 아닙니다"),), "형식"),
    ((httpx.Response(200, text="<html>"),), "형식"),
    ((httpx.Response(400),), "HTTP 400"),
    ((httpx.Response(503), httpx.Response(500)), "연결하지 못했습니다"),
    ((httpx.ConnectError("down"), httpx.ConnectError("down")), "연결하지 못했습니다"),
])
def test_any_failure_falls_back_to_the_rule_report(responses, problem):
    request = fixture("fail")
    qwen, _ = polisher(*responses)
    draft = invoke_role("A07", TeamA07Reporter(qwen), request)
    assert draft == invoke_role("A07", TeamA07Reporter(), request)
    assert any(problem in p for p in qwen.last_problems)


def test_connection_errors_and_5xx_are_retried_once_but_4xx_is_not():
    qwen, sent = polisher(httpx.Response(503), envelope(GOOD))
    assert qwen.polish(items(fixture("fail"))) == {"requirement-number": (GOOD["items"][0]["explanation"],
                                                                         GOOD["items"][0]["action"])}
    assert len(sent) == 2 and qwen.calls == 2
    rejected, sent = polisher(httpx.Response(400), envelope(GOOD))
    assert rejected.polish(items(fixture("fail"))) is None and len(sent) == 1


def test_passing_report_never_calls_the_server():
    qwen, sent = polisher()
    draft = invoke_role("A07", TeamA07Reporter(qwen), fixture("pass"))
    assert sent == [] and POLISH_NOTICE not in draft.summary


def test_an_unexpected_polisher_error_does_not_stop_the_report():
    class Broken:
        def polish(self, reports):
            raise RuntimeError("bug")
    request = fixture("review")
    assert invoke_role("A07", TeamA07Reporter(Broken()), request) == invoke_role("A07", TeamA07Reporter(), request)


@pytest.mark.parametrize("url", ["ftp://host", "http://user:pw@host", "http://host?x=1", "not a url", None])
def test_bad_server_address_is_rejected_when_configured(url):
    with pytest.raises(AdapterFailure) as error:
        QwenPolisher(url)
    assert error.value.code == "QWEN_CONFIG_INVALID"


@pytest.mark.parametrize(("base", "endpoint"), [("http://127.0.0.1:18290", "http://127.0.0.1:18290/v1/chat/completions"),
                                                ("http://h:1/v1/", "http://h:1/v1/chat/completions"),
                                                ("https://h/v1/chat/completions", "https://h/v1/chat/completions")])
def test_endpoint_is_completed_from_the_base_address(base, endpoint):
    assert QwenPolisher(base).url == endpoint


def test_factory_reads_the_shared_cc_qwen_settings(monkeypatch):
    for name in ("CC_QWEN_BASE_URL", "CC_QWEN_API_KEY", "CC_QWEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("A07_QWEN_BASE_URL", "http://127.0.0.1:18290")   # 예전 A07 전용 이름은 읽지 않는다
    assert create_reporter().polisher is None
    monkeypatch.setenv("CC_QWEN_BASE_URL", "http://127.0.0.1:8001/v1")
    reporter = create_reporter()
    assert isinstance(reporter.polisher, QwenPolisher)
    assert reporter.polisher.url == "http://127.0.0.1:8001/v1/chat/completions"
    assert reporter.polisher.model == "Qwen/Qwen3.5-4B" and "Authorization" not in reporter.polisher.http.headers
    monkeypatch.setenv("CC_QWEN_MODEL", "team-qwen")
    monkeypatch.setenv("CC_QWEN_API_KEY", "test-key")
    reporter = create_reporter()
    assert reporter.polisher.model == "team-qwen"
    assert reporter.polisher.http.headers["Authorization"] == "Bearer test-key"
