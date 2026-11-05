"""Bounded tool-calling loop for A01/A08 agents (vLLM OpenAI-compatible Qwen).

The model chooses tools and writes the explanation. Tool arguments are bound by
code, a write tool runs at most once, and an ambiguous write is never retried.
The final JSON is schema-validated here and fact-checked by each agent.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Protocol, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ValidationError

from ..ports import AdapterFailure

MODEL = 'Qwen/Qwen3.5-4B'
MAX_TURNS = 8
MAX_RESPONSE_BYTES = 1024 * 1024
T = TypeVar('T', bound=BaseModel)


class ToolClient(Protocol):
    def call(self, name: str, arguments: dict[str, Any]) -> dict: ...


class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        """Return one assistant message: {'content': str|None, 'tool_calls': [...]}."""


@dataclass(frozen=True)
class ToolSpec:
    """A model-visible tool. `call` closes over code-bound arguments."""
    name: str
    description: str
    call: Callable[[], dict]
    writes: bool = False

    def schema(self) -> dict:
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description,
                                                 'parameters': {'type': 'object', 'properties': {}}}}


def bound(tools: ToolClient, name: str, arguments: dict) -> Callable[[], dict]:
    return lambda: tools.call(name, arguments)


class OpenAICompatibleChat:
    """Single-attempt chat completions; the caller decides whether to start again."""

    def __init__(self, base_url: str, api_key: str | None = None, *, timeout: float = 120):
        parsed = urlsplit(base_url or '')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query:
            raise AdapterFailure('INVALID_MODEL_ENDPOINT', 'The model endpoint setting is invalid.')
        path = parsed.path.rstrip('/')
        if not path.endswith('/chat/completions'):
            path += '/chat/completions' if path.endswith('/v1') else '/v1/chat/completions'
        self.url = urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))
        headers = {'Accept': 'application/json'} | ({'Authorization': f'Bearer {api_key}'} if api_key else {})
        self._http = httpx.Client(headers=headers, follow_redirects=False, trust_env=False,
                                  timeout=httpx.Timeout(min(timeout, 120), connect=10))

    def close(self):
        self._http.close()

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {'model': MODEL, 'messages': messages, 'temperature': 0, 'max_tokens': 2048,
                   'chat_template_kwargs': {'enable_thinking': False}}
        if tools:
            payload.update(tools=tools, tool_choice='auto')
        try:
            response = self._http.post(self.url, json=payload)
        except httpx.HTTPError:
            raise AdapterFailure('MODEL_UNAVAILABLE', 'The model endpoint did not respond.', retryable=True) from None
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            raise AdapterFailure('MODEL_REQUEST_FAILED', 'The model endpoint rejected the request.',
                                 retryable=response.status_code == 429 or response.status_code >= 500)
        try:
            message = response.json()['choices'][0]['message']
        except (ValueError, KeyError, IndexError, TypeError):
            message = None
        if not isinstance(message, dict):
            raise AdapterFailure('MODEL_INVALID_RESPONSE', 'The model response has no message.')
        return message


def _reject_constant(_):
    raise ValueError('nonfinite JSON number')


def parse_final(content: str, output: type[T]) -> T:
    """Take the last top-level JSON object; surrounding prose is ignored, never merged."""
    text = re.sub(r'<think>.*?</think>', '', content or '', flags=re.S).split('</think>')[-1]
    text = (re.findall(r'```(?:json)?\s*(.*?)\s*```', text, re.S) or [text])[-1]
    decoder, found, index = json.JSONDecoder(parse_constant=_reject_constant), None, text.find('{')
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index = text.find('{', index + 1)
            continue
        found = value if isinstance(value, dict) else found
        index = text.find('{', end)
    try:
        return output.model_validate(found)
    except ValidationError:
        raise AdapterFailure('AGENT_OUTPUT_INVALID', 'The agent answer is not the required JSON contract.') from None


def run_agent(model: ChatModel, *, system: str, task: str, tools: list[ToolSpec], output: type[T],
              revise: Callable[[T, list[str]], str | None] | None = None,
              completed_tools: list[str] | None = None,
              format_guidance: str | None = None) -> tuple[T, list[str]]:
    """Return the parsed answer and the names of tools that completed.

    `revise` may return a correction once when the answer contradicts what was
    executed; the model then answers again. Callers still fact-check the result.
    """
    specs = {tool.name: tool for tool in tools}
    messages: list[dict] = [{'role': 'system', 'content': system}, {'role': 'user', 'content': task}]
    used = completed_tools if completed_tools is not None else []
    attempted: list[str] = []
    reformatted = False
    for _turn in range(MAX_TURNS):
        # Small models re-call tools they already saw; offer each tool until it has been tried once.
        message = model.complete(messages, [tool.schema() for tool in tools if tool.name not in attempted])
        content, calls = message.get('content') or '', message.get('tool_calls') or []
        if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
            raise AdapterFailure('MODEL_INVALID_RESPONSE', 'The model returned malformed tool calls.')
        if not calls:
            try:
                draft = parse_final(content, output)
            except AdapterFailure:
                if reformatted:
                    raise
                reformatted = True  # Small models drift into prose or object lists; ask for the contract once.
                messages += [{'role': 'assistant', 'content': content}, {'role': 'user', 'content': (
                    '최종 답이 요구한 JSON 형식이 아니다. 설명 없이 시스템 지시의 JSON 객체 하나만 다시 출력하라. '
                    + (format_guidance or '목록에는 ID 문자열만 넣는다.'))}]
                continue
            correction = revise(draft, used) if revise else None
            if correction is None:
                return draft, used
            revise = None  # One correction only; a repeated contradiction reaches the caller's check.
            messages += [{'role': 'assistant', 'content': content}, {'role': 'user', 'content': correction}]
            continue
        messages.append({'role': 'assistant', 'content': content, 'tool_calls': calls})
        for call in calls:
            name = (call.get('function') or {}).get('name')
            spec = specs.get(name)
            if spec is not None and spec.name in attempted:
                receipt = {'state': 'failed', 'error': {'code': 'WRITE_ALREADY_DONE' if spec.writes else 'ALREADY_CALLED',
                                                        'message': 'This tool already returned a result above.'}}
            else:
                if spec is not None:
                    attempted.append(spec.name)
                receipt = _execute(spec, used)
            messages.append({'role': 'tool', 'tool_call_id': call.get('id', ''), 'name': name or '',
                             'content': json.dumps(receipt, ensure_ascii=False, allow_nan=False)})
        remaining = [tool.name for tool in tools if tool.name not in attempted]
        messages.append({'role': 'user', 'content': (
            f'이미 결과를 받은 도구: {attempted}. 같은 도구를 다시 호출하지 마라. '
            + (f'아직 쓸 수 있는 도구: {remaining}. 어떤 동작을 하기로 판단했다면 답하기 전에 지금 그 도구를 호출하라. '
               '호출하지 않은 도구의 결과를 했다고 쓰지 마라. ' if remaining else '')
            + '더 필요한 도구가 없으면 설명 없이 최종 JSON 객체 하나만 출력하라.')})
    raise AdapterFailure('AGENT_TURN_LIMIT', 'The agent did not answer within the turn limit.')


def _execute(spec: ToolSpec | None, used: list[str]) -> dict:
    if spec is None:
        return {'state': 'failed', 'error': {'code': 'UNKNOWN_TOOL', 'message': 'This tool is not available.'}}
    try:
        receipt = spec.call()
    except Exception:
        receipt = None
        if not spec.writes:
            raise AdapterFailure('TOOL_UNAVAILABLE', 'A read tool could not be reached.', retryable=True) from None
    if not isinstance(receipt, dict) or receipt.get('state') not in ('completed', 'failed'):
        if spec.writes:
            # The command may have been applied. Never call it again automatically.
            raise AdapterFailure('TOOL_OUTCOME_UNKNOWN', 'A write tool result is unknown; reconcile first.',
                                 outcome_unknown=True)
        raise AdapterFailure('TOOL_INVALID_RECEIPT', 'A tool returned an invalid receipt.')
    if receipt['state'] == 'completed':
        used.append(spec.name)
    return receipt


def read_fact(tools: ToolClient, name: str, arguments: dict[str, Any]) -> Any:
    """Code-side read outside the model loop; transport errors become adapter failures."""
    try:
        receipt = tools.call(name, arguments)
    except Exception:
        raise AdapterFailure('TOOL_UNAVAILABLE', 'A read tool could not be reached.', retryable=True) from None
    return required(receipt)


def required(receipt: dict) -> Any:
    """Code-side fact reads must succeed; never let a model explain a missing run."""
    if isinstance(receipt, dict) and receipt.get('state') == 'completed':
        return receipt['result']
    code = str(((receipt or {}).get('error') or {}).get('code', '')) if isinstance(receipt, dict) else ''
    raise AdapterFailure(code if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', code) else 'TOOL_FAILED',
                         'Run facts could not be read through the framework tools.')
