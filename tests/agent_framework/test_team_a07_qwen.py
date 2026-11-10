"""A07 Qwen 문장 다듬기: 단일 호출·결과 불명·확정 실패 시 규칙 문장. 실제 서버 없음."""
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


def assert_rule_report_with_notice(draft,request,status='failed'):
    plain=invoke_role('A07',TeamA07Reporter(),request)
    assert draft.polish_status==status
    assert (draft.verdict,draft.evidence_ids,draft.change_requests)==(plain.verdict,plain.evidence_ids,plain.change_requests)
    assert draft.summary.startswith(plain.summary+'\n') and '규칙 기반 보고서' in draft.summary


def test_polished_sentences_are_added_without_changing_facts():
    request = fixture("fail")
    qwen, sent = polisher(envelope(GOOD))
    plain = invoke_role("A07", TeamA07Reporter(), request)
    draft = invoke_role("A07", TeamA07Reporter(qwen), request)
    payload, = sent
    assert payload["temperature"] == 0 and payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "requirement-text" not in payload["messages"][-1]["content"]   # 통과 항목은 보내지 않는다
    assert (draft.verdict, draft.evidence_ids) == (plain.verdict, plain.evidence_ids)
    assert f"- {POLISH_NOTICE}" in draft.summary.splitlines()
    assert all(line in draft.summary.splitlines() for line in plain.summary.splitlines())   # 규칙 사실은 그대로
    assert draft.change_requests == ("수정 [requirement-number] 딸기가 3개 보이도록 고쳐 주세요.",)   # 할 일만 Qwen 문장
    assert "딸기 개수가 기준과 다릅니다" not in draft.summary   # 템플릿에는 설명 칸이 없다(사유는 코드표 한 줄)


@pytest.mark.parametrize(("responses", "problem"), [
    ((envelope({"items": [{**GOOD["items"][0], "explanation": "딸기가 5개로 보입니다."}]}),), "입력에 없는 숫자 5"),
    ((envelope(GOOD, finish="length"),), "형식"),
    ((envelope("이건 JSON이 아닙니다"),), "형식"),
    ((httpx.Response(200, text="<html>"),), "형식"),
    ((httpx.Response(400),), "HTTP 400"),
    ((httpx.Response(503),), "HTTP 503"),
    ((httpx.ConnectError("down"),), "연결하지 못했습니다"),
])
def test_any_failure_falls_back_to_the_rule_report(responses, problem):
    request = fixture("fail")
    qwen, _ = polisher(*responses)
    draft = invoke_role("A07", TeamA07Reporter(qwen), request)
    assert_rule_report_with_notice(draft,request,'outcome_unknown' if responses[0].__class__ is httpx.Response and responses[0].status_code>=500 else 'failed')
    assert any(problem in p for p in qwen.last_problems)


@pytest.mark.parametrize('response', [httpx.Response(503), httpx.Response(429), httpx.Response(400),
    httpx.ConnectError('down'), httpx.ConnectTimeout('connect'), httpx.PoolTimeout('pool')])
def test_known_failure_falls_back_without_automatic_resubmission(response):
    qwen, sent = polisher(response, envelope(GOOD))
    request = fixture('fail')
    draft=invoke_role('A07',TeamA07Reporter(qwen),request)
    assert_rule_report_with_notice(draft,request,'outcome_unknown' if isinstance(response,httpx.Response) and response.status_code>=500 else 'failed')
    assert len(sent) == qwen.calls == 1


def test_grouped_check_lines_keep_the_template_sentence_even_when_polished():
    """같은 사유로 묶인 '확인' 줄은 항목별 Qwen 문장 대신 템플릿 규칙 문장 하나를 쓴다."""
    request = fixture("review")
    rows = [{"criterion_id": i.criterion_id, "explanation": "자동으로 읽지 못했습니다.",
             "action": "포스터를 직접 확인해 주세요."} for i in items(request)]
    qwen, sent = polisher(envelope({"items": rows}))
    draft = invoke_role("A07", TeamA07Reporter(qwen), request)
    plain = invoke_role("A07", TeamA07Reporter(), request)
    assert len(sent) == 1 and f"- {POLISH_NOTICE}" not in draft.summary.splitlines()   # 쓰이지 않은 Qwen 문장은 안내하지 않는다
    assert draft.change_requests == plain.change_requests and len(draft.change_requests) == 1


@pytest.mark.parametrize('failure', [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError,
    httpx.WriteError, httpx.RemoteProtocolError])
def test_uncertain_optional_polish_keeps_rule_report_without_retry(failure):
    qwen, sent = polisher(failure('unknown'), envelope(GOOD))
    request=fixture('fail');draft=invoke_role('A07',TeamA07Reporter(qwen),request)
    assert_rule_report_with_notice(draft,request,'outcome_unknown')
    assert len(sent) == qwen.calls == 1
    assert qwen.last_problems


def test_passing_report_never_calls_the_server():
    qwen, sent = polisher()
    draft = invoke_role("A07", TeamA07Reporter(qwen), fixture("pass"))
    assert sent == [] and POLISH_NOTICE not in draft.summary


def test_optional_polish_timeout_does_not_block_correction_approval():
    from tests.agent_framework.test_runtime import Examples,context,document,asset,approval
    from backend.agent_framework.ports import Agents
    from backend.agent_framework.runtime import Workflow
    example=Examples(observed='Different');qwen,sent=polisher(httpx.ReadTimeout('unknown'),envelope(GOOD))
    flow=Workflow(Agents(extractor=example,inspector=example,validator=example,reporter=TeamA07Reporter(qwen)),profile='test')
    ctx=context();flow.start(ctx,document(),asset())
    result=flow.approve(ctx.run_id,approval(ctx,'approve_criteria'))
    assert result.execution_status=='waiting_review' and result.error is None
    assert result.machine_verdict=='fail' and result.report.polish_status=='outcome_unknown'
    assert flow.approve(ctx.run_id,approval(ctx,'approve_changes',reason='수정 요청')).execution_status=='waiting_revision'
    assert len(sent)==qwen.calls==1


def test_an_unexpected_polisher_error_does_not_stop_the_report():
    class Broken:
        def polish(self, reports):
            raise RuntimeError("bug")
    request = fixture("review")
    assert_rule_report_with_notice(invoke_role('A07',TeamA07Reporter(Broken()),request),request)


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
