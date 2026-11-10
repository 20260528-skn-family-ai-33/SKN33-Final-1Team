"""Probe: can a chat model answer evidence questions with the five read-only MCP evidence tools?

Test-only harness. Product agents (A01/A08) are unchanged and still see only
their own tools. Each probe offers all five evidence tools, bound by code to one
run and described by their MCP docstrings, asks one short question written for
this check (Claude-written, not a team evaluation set), and grades the answer
against the same tool called by code afterwards. A probe passes only when the
reported facts match and the model actually called a tool that holds them.
The model's free-text summary is recorded, never graded.

A pass means "the model used the tools and repeated their facts". It is not an
accuracy figure for real reviews.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import inspect
import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

from backend.agent_framework.agents.loop import MODEL, ChatModel, ToolClient, ToolSpec, bound, read_fact, run_agent
from backend.agent_framework.mcp_server import EVIDENCE_TOOL_NAMES, FrameworkTools
from backend.agent_framework.ports import AdapterFailure

NOTE = 'Questions were written by Claude for a working check; results are not a performance figure.'
SYSTEM = ('너는 포스터 검수 기록을 조회하는 근거 확인 도우미다.\n'
          '도구로 확인한 값만 답한다. 도구 결과에 없는 값은 지어내지 말고 null이나 빈 목록으로 둔다.\n'
          '승인·판정을 바꾸거나 새로 만들지 않는다. 마지막 답은 지시한 JSON 객체 하나만 출력한다.')
PREFIXES = ('run:', 'criteria:', 'document:', 'approval:', 'poster:')  # get_relations node IDs


class Answer(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    summary: str = ''


class CriteriaAnswer(Answer):
    criterion_ids: list[str]


class HistoryAnswer(Answer):
    criteria_version_count: int
    approved_criteria_version_id: str | None = None


class SourcesAnswer(Answer):
    status: Literal['verified', 'stale', 'mismatch']
    mismatch_criterion_ids: list[str] = []


class RelationsAnswer(Answer):
    pinned_criteria_version_id: str | None = None
    parent_run_id: str | None = None


class ImpactAnswer(Answer):
    affected_run_ids: list[str]
    document_changed: bool


def _edge(result: dict, start: str, kind: str) -> str | None:
    return next((e['target'] for e in result['edges'] if e['source'] == start and e['kind'] == kind), None)


def _parent(result: dict, child: str) -> str | None:
    return next((e['source'] for e in result['edges'] if e['target'] == child and e['kind'] == 'reinspected_as'), None)


@dataclass(frozen=True)
class Probe:
    name: str
    question: str
    answer: type[Answer]
    truth_tool: str
    holders: frozenset[str]  # Tools whose results contain the graded facts.
    facts: Callable[[dict, str], dict]  # (truth tool result, run ID) -> expected facts


PROBES = (
    Probe('criteria', '이 실행에 적용된 승인 기준의 조건 ID를 모두 확인하라. '
          'JSON: {"criterion_ids": ["..."], "summary": "한국어 한두 문장"}',
          CriteriaAnswer, 'get_approved_criteria', frozenset({'get_approved_criteria'}),
          lambda r, _: {'criterion_ids': [c['criterion_id'] for c in r['criteria']]}),
    Probe('history', '이 실행의 기준 버전이 몇 개 만들어졌는지, 그중 승인된 기준 버전 ID가 무엇인지 확인하라. '
          'JSON: {"criteria_version_count": 0, "approved_criteria_version_id": "...", "summary": "한국어 한두 문장"}',
          HistoryAnswer, 'get_history', frozenset({'get_history'}),
          lambda r, _: {'criteria_version_count': len(r['criteria_versions']),
                        'approved_criteria_version_id': next((v['criteria_version_id'] for v in r['criteria_versions']
                                                              if v['approved']), None)}),
    Probe('sources', '승인 기준의 인용 문장이 저장된 문서 원문과 맞는지 확인하라. 전체 상태와 맞지 않는 조건 ID를 답하라. '
          'JSON: {"status": "verified|stale|mismatch", "mismatch_criterion_ids": [], "summary": "한국어 한두 문장"}',
          SourcesAnswer, 'verify_sources', frozenset({'verify_sources'}),
          lambda r, _: {'status': r['status'],
                        'mismatch_criterion_ids': [i['criterion_id'] for i in r['criteria'] if i['status'] == 'mismatch']}),
    Probe('relations', '연결 정보에서 이 실행이 적용한(applies) 기준 버전 ID와, 재검수 연결(reinspected_as)에서 이 실행을 '
          '가리키는 부모 실행 ID를 찾아라. 없으면 null. JSON: {"pinned_criteria_version_id": "...", "parent_run_id": "...", '
          '"summary": "한국어 한두 문장"}',
          RelationsAnswer, 'get_relations', frozenset({'get_relations'}),
          lambda r, run_id: {'pinned_criteria_version_id': _edge(r, 'run:' + run_id, 'applies'),
                             'parent_run_id': _parent(r, 'run:' + run_id)}),
    Probe('impact', '이 실행의 승인 기준을 고정한 포스터 검수 실행 ID를 모두 찾고, 기준을 만든 문서가 그 뒤 새 버전으로 '
          '바뀌었는지 답하라. JSON: {"affected_run_ids": ["..."], "document_changed": false, '
          '"summary": "한국어 한두 문장"}',
          ImpactAnswer, 'get_change_impact', frozenset({'get_change_impact'}),
          lambda r, _: {'affected_run_ids': [a['run_id'] for a in r['affected_runs']],
                        'document_changed': r['document_changed']}),
)


def _bare(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if not value or value.lower() == 'null':
            return None
        for prefix in PREFIXES:
            if value.startswith(prefix):
                return value[len(prefix):]
    return value


def normalize(facts: dict) -> dict:
    """Order-free lists and relation-node prefixes removed; values are otherwise compared exactly."""
    return {key: sorted({_bare(v) for v in value} - {None}) if isinstance(value, (list, tuple)) else _bare(value)
            for key, value in facts.items()}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    question: str
    passed: bool
    grounded: bool
    tools_used: tuple[str, ...]
    expected: dict
    reported: dict | None
    mismatched: tuple[str, ...]
    error: str | None
    model_summary: str | None


def tool_specs(tools: ToolClient, run_id: str) -> list[ToolSpec]:
    """All five evidence tools, run ID bound by code, described exactly as the MCP server does."""
    args = {'run_id': run_id}
    return [ToolSpec(name, inspect.getdoc(getattr(FrameworkTools, name)), bound(tools, name, args))
            for name in EVIDENCE_TOOL_NAMES]


def run_probe(model: ChatModel, tools: ToolClient, run_id: str, probe: Probe) -> ProbeResult:
    task = f'실행 {json.dumps(run_id)}에 대해 도구로 확인한 뒤 답하라. {probe.question}'
    draft, used, error = None, [], None
    try:
        draft, used = run_agent(model, system=SYSTEM, task=task, tools=tool_specs(tools, run_id), output=probe.answer)
    except AdapterFailure as failure:
        error = failure.code
    # Truth comes from the same tool called by code after the model answered.
    expected = normalize(probe.facts(read_fact(tools, probe.truth_tool, {'run_id': run_id}), run_id))
    reported = normalize(draft.model_dump(exclude={'summary'})) if draft is not None else None
    mismatched = tuple(key for key in expected if reported is None or reported.get(key) != expected[key])
    grounded = bool(set(used) & probe.holders)
    return ProbeResult(name=probe.name, question=probe.question, passed=draft is not None and grounded and not mismatched,
                       grounded=grounded, tools_used=tuple(used), expected=expected, reported=reported,
                       mismatched=mismatched, error=error, model_summary=draft.summary if draft is not None else None)


def write_results(path: str | Path, results: list[ProbeResult], **meta: Any) -> None:
    """Results JSON for people to read; carries no endpoint or credential."""
    data = {'note': NOTE, 'model': MODEL, **meta, 'passed': sum(r.passed for r in results), 'total': len(results),
            'probes': [asdict(r) for r in results]}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
