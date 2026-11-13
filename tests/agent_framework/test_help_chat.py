import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.agent_framework import postgres_api
from backend.agent_framework.composition import load_config
from backend.agent_framework.help_chat import (
    ChatInput, ChatLimits, FAQ, FAQ_REPLIES, FALLBACK_REPLIES, LunaResponder, RESPONSES_URL,
)
from backend.app.security.errors import AuthError
from backend.app.v1_config import load_v1_settings
from backend.runtime_environment import runtime_values
from scripts.framework_desktop import read_env
from tests.app.auth_fixtures import auth_environment

ORIGIN = "http://localhost:8000"
PREFIX = "/api/framework/v1"
SESSION = "s" * 43


@pytest.fixture
def chat_app(monkeypatch):
    from backend.app.management import maintenance
    from backend.app.persistence import schema_inventory
    from backend.app.services.auth import AuthService

    principal = SimpleNamespace(user_id=uuid4())
    monkeypatch.setattr(AuthService, "current", lambda self, raw, **_: principal if raw == SESSION else None)
    monkeypatch.setattr(schema_inventory, "check_schema", lambda *_: None)
    monkeypatch.setattr(maintenance, "active", lambda *_: False)
    monkeypatch.setattr(postgres_api, "unit_of_work", lambda _: nullcontext(None))
    monkeypatch.setattr(postgres_api, "check_extension", lambda _: {"revision": "AF004"})
    monkeypatch.setattr(postgres_api, "check_app_permissions", lambda _: None)
    config = load_config("examples/agent_framework/team_config.synthetic.json")

    def create(key=""):
        settings = load_v1_settings(auth_environment(creative_checker_openapi=key))
        return TestClient(
            postgres_api.create_postgres_app(settings, config, engine=object()),
            base_url=ORIGIN,
        )
    return create


def csrf_headers(client, *, signed_in=True):
    if signed_in:
        client.cookies.set("creative_checker_session", SESSION)
    token = client.get(f"{PREFIX}/csrf").json()["csrf_token"]
    return {"Origin": ORIGIN, "X-CSRFToken": token}


def completed(text):
    return {"status": "completed", "output": [
        {"type": "reasoning"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
    ]}


def test_route_reuses_framework_session_and_csrf(chat_app, monkeypatch):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completed(json.dumps({"route": "FAQ-03"})))

    key = "synthetic-chat-key"
    monkeypatch.setattr(postgres_api, "LunaResponder",
                        lambda api_key: LunaResponder(api_key, transport=httpx.MockTransport(provider)))
    question = {"question": "PDF 없이도 검수할 수 있어요?", "history": [{"question": "소개", "reply": "포스터 검수 서비스"}]}
    with chat_app(key) as client:
        url = f"{PREFIX}/help/chat"
        assert client.post(url, json=question, headers=csrf_headers(client, signed_in=False)).status_code == 401
        headers = csrf_headers(client)
        assert client.post(url, json=question).status_code == 403
        assert client.post(url, json=question, headers={**headers, "Origin": "https://example.org"}).status_code == 403
        bad = client.post(url, json={"question": " ", "project_id": "private"}, headers=headers)
        assert bad.status_code == 422
        assert "private" not in bad.text
        exchange = question["history"][0]
        assert client.post(url, json={**question, "history": [exchange] * 4}, headers=headers).status_code == 422
        too_large = client.post(url, content=b" " * 65537, headers={**headers, "Content-Type": "application/json"})
        assert too_large.status_code == 413
        assert not calls
        answer = client.post(url, json=question, headers=headers)
        assert answer.status_code == 200
        assert answer.json() == {"reply": FAQ_REPLIES["FAQ-03"]}
        assert answer.headers["cache-control"] == "no-store"
    request, = calls
    assert request.url == RESPONSES_URL
    assert request.headers["authorization"] == f"Bearer {key}"
    payload = json.loads(request.content)
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["store"] is False
    message, = payload["input"]
    assert message["role"] == "user"
    assert json.loads(message["content"]) == {"previous_questions": ["소개"], "question": question["question"]}
    assert "포스터 검수 서비스" not in message["content"]
    format_ = payload["text"]["format"]
    assert format_["type"] == "json_schema" and format_["strict"] is True
    assert set(format_["schema"]["properties"]["route"]["enum"]) == FAQ_REPLIES.keys() | FALLBACK_REPLIES.keys()
    assert "tools" not in payload


def test_missing_key_only_disables_free_questions(chat_app):
    with chat_app() as client:
        response = client.post(f"{PREFIX}/help/chat", json={"question": "사용법"}, headers=csrf_headers(client))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "HELP_CHAT_NOT_CONFIGURED"
        assert client.get(f"{PREFIX}/health").status_code == 200


@pytest.mark.parametrize(("status", "body", "code"), [
    (401, {}, "HELP_CHAT_PROVIDER_ACCESS"),
    (429, {}, "HELP_CHAT_PROVIDER_LIMIT"),
    (500, {}, "HELP_CHAT_PROVIDER_FAILED"),
    (200, {"status": "incomplete", "output": []}, "HELP_CHAT_PROVIDER_FAILED"),
    (200, completed(""), "HELP_CHAT_PROVIDER_FAILED"),
    (200, completed("arbitrary model text"), "HELP_CHAT_PROVIDER_FAILED"),
    (200, completed('{"route":"FAQ-999"}'), "HELP_CHAT_PROVIDER_FAILED"),
    (200, completed('{"route":"FAQ-01","answer":"arbitrary model text"}'), "HELP_CHAT_PROVIDER_FAILED"),
    (200, completed('{"route":"FAQ-01","route":"out_of_scope"}'), "HELP_CHAT_PROVIDER_FAILED"),
])
def test_provider_errors_have_safe_messages(status, body, code):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json=body))
    with pytest.raises(AuthError) as error:
        asyncio.run(LunaResponder("synthetic", transport=transport).reply(ChatInput(question="사용법")))
    assert error.value.code == code


def test_provider_deadline():
    async def provider(_):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=completed("too late"))

    responder = LunaResponder("synthetic", transport=httpx.MockTransport(provider), timeout=0.001)
    with pytest.raises(AuthError) as error:
        asyncio.run(responder.reply(ChatInput(question="사용법")))
    assert error.value.code == "HELP_CHAT_TIMEOUT"


def test_per_user_limit_and_expiry():
    now = 100.0
    limits = ChatLimits(lambda: now)
    limits.begin("first")
    with pytest.raises(AuthError) as busy:
        limits.begin("first")
    assert busy.value.code == "HELP_CHAT_BUSY"
    limits.begin("second")
    limits.finish("first")
    for _ in range(9):
        limits.begin("first")
        limits.finish("first")
    with pytest.raises(AuthError) as rate:
        limits.begin("first")
    assert rate.value.code == "HELP_CHAT_RATE_LIMITED"
    assert rate.value.retry_after == 60
    now += 60
    limits.begin("first")


def test_existing_openai_key_loads_from_framework_api_env(tmp_path):
    path = tmp_path / "runtime.env"
    path.write_text("creative_checker_openapi=synthetic-shared-key\n", encoding="utf-8")
    assert read_env(path)["creative_checker_openapi"] == "synthetic-shared-key"
    assert runtime_values("v1", path, {})["creative_checker_openapi"] == "synthetic-shared-key"


@pytest.mark.parametrize("route", ["out_of_scope", "unknown", "clarify", "approval_request", "FAQ-13"])
def test_only_server_text_is_returned(route):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=completed(json.dumps({"route": route}))))
    reply = asyncio.run(LunaResponder("synthetic", transport=transport).reply(ChatInput(question="질문")))
    assert reply == (FAQ_REPLIES | FALLBACK_REPLIES)[route]


def test_model_refusal_uses_fixed_text():
    result = {"status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "arbitrary text"}]},
    ]}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    reply = asyncio.run(LunaResponder("synthetic", transport=transport).reply(ChatInput(question="질문")))
    assert reply == FALLBACK_REPLIES["out_of_scope"]


def test_catalog_matches_reviewed_document():
    path = Path(__file__).resolve().parents[2] / "docs/agent-framework/챗봇_FAQ_초안.md"
    rows = re.findall(r"^\| (FAQ-\d+) \| ([^|]+) \| ([^|]+) \|$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert rows and len(rows) == len(FAQ) == len(FAQ_REPLIES)
    assert FAQ == [
        {"id": id_, "questions": questions.strip().split(" / "), "answer": answer.strip().replace("**", "")}
        for id_, questions, answer in rows
    ]
