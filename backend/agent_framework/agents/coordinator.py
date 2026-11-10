"""A01 run-coordination agent: explains state and proposes the next action.

It has read tools only. Approvals and admissions remain human/API commands, and
the proposed action must be one the current execution state allows.
"""
from __future__ import annotations

import json

from ..ports import AdapterFailure
from ..human_review import can_review
from .contracts import CoordinationDraft, CoordinationNote
from .loop import ChatModel, ToolClient, ToolSpec, bound, read_fact, run_agent

ACTIONS = {
    'wait_for_worker': 'worker가 역할을 실행 중이거나 대기 중이다.',
    'request_criteria_approval': 'A02 조건 초안을 사람이 확정해야 한다.',
    'request_final_approval': '기계 판정 pass, 최종 승인을 요청한다.',
    'request_human_review': '보류 항목을 사람이 직접 확인하고 판단·관측 내용·근거를 기록해야 한다.',
    'request_change_approval': '수정 요청 승인을 요청한다.',
    'request_exception_approval': '확정 fail에 대한 예외 승인 검토를 요청한다.',
    'submit_poster': '승인한 기준으로 검수할 포스터를 등록한다.',
    'submit_revision': '제작자가 수정한 새 포스터 버전을 제출해야 한다.',
    'start_new_run': '실패한 실행이다. 원인을 확인한 뒤 새 실행이 필요하다.',
    'reconcile_manually': '외부 호출 결과가 불명이다. 자동 재시도 없이 사람이 확인해야 한다.',
    'none': '완료되어 할 일이 없다.',
}
# execution_status -> (who the run waits on, allowed next actions); waiting_review depends on the verdict.
_STATES = {
    'queued': ('worker', {'wait_for_worker'}), 'extracting': ('worker', {'wait_for_worker'}),
    'reviewing': ('worker', {'wait_for_worker'}), 'waiting_criteria': ('human', {'request_criteria_approval'}),
    'waiting_asset': ('creator', {'submit_poster'}),
    'waiting_review': ('human', set()), 'waiting_revision': ('creator', {'submit_revision'}),
    'completed': ('none', {'none'}), 'failed': ('human', {'start_new_run'}),
    'outcome_unknown': ('human', {'reconcile_manually'}),
}
_REVIEW_ALLOWED = {
    'pass': {'request_final_approval', 'request_change_approval'},
    'fail': {'request_change_approval', 'request_exception_approval'},
    'review': {'request_change_approval'},
}

SYSTEM = ('너는 포스터 검수 워크플로의 A01 실행 조정 에이전트다.\n'
          '도구로 실행 상태를 확인한 뒤 사람에게 다음 조치를 안내한다. 승인·판정은 직접 하지 않는다.\n'
          'next_action 의미:\n' + ''.join(f'- {key}: {text}\n' for key, text in ACTIONS.items())
          + '판정(machine_verdict)과 검증 항목을 바꾸거나 새로 만들지 마라.\n'
          '마지막 답은 JSON 객체 하나만 출력한다: {"next_action": "...", "summary": "한국어 설명", "blockers": ["..."]}')


def allowed_actions(facts: dict) -> set[str]:
    status = facts['execution_status']
    if status != 'waiting_review':
        return _STATES[status][1]
    actions = set(_REVIEW_ALLOWED.get(facts['machine_verdict'] or '', ()))
    if any(item['verdict']=='review' and can_review(item['issues']) for item in facts['validated_items']):
        actions.add('request_human_review')
    # Mirrors Workflow._exception_scope: only plain value mismatches can be excepted.
    if any(item['verdict'] == 'review' or set(item['issues']) - {'value_mismatch'} for item in facts['validated_items']):
        actions.discard('request_exception_approval')
    return actions


def fact_summary(facts: dict, action: str) -> str:
    """User-facing summary built only from run facts and the checked action."""
    open_count = sum(item['verdict'] != 'pass' for item in facts['validated_items'])
    return ' '.join([f"상태 {facts['execution_status']}, 기계 판정 {facts['machine_verdict'] or '없음'}."]
                    + ([f"pass가 아닌 조건: {open_count}건."] if open_count else [])
                    + ([f"오류 코드: {facts['error']['code']}."] if facts['error'] else [])
                    + [f'다음 조치: {ACTIONS[action]}'])


class RunCoordinator:
    def __init__(self, model: ChatModel, tools: ToolClient):
        self.model, self.tools = model, tools

    def assess(self, run_id: str) -> CoordinationNote:
        args = {'run_id': run_id}
        current = read_fact(self.tools, 'get_run', args)  # Unknown runs never reach the model.
        specs = [ToolSpec('get_run', '현재 실행의 상태·판정·보고·승인 결정을 조회한다.', bound(self.tools, 'get_run', args)),
                 ToolSpec('get_steps', '현재 실행의 역할별 단계 기록(실패·결과 불명 포함)을 조회한다.',
                          bound(self.tools, 'get_steps', args)),
                 ToolSpec('get_approval_history', '현재 실행의 사람 승인 기록을 조회한다.',
                          bound(self.tools, 'get_approval_history', args))]
        # A 4B model mislabels states; code narrows the choice, the model picks and explains.
        task = (f'실행 {json.dumps(run_id)}의 상태를 도구로 확인하고 다음 조치를 JSON으로 답하라. '
                f'현재 상태에서 고를 수 있는 next_action: {sorted(allowed_actions(current))}. 이 목록 밖의 값은 쓰지 마라.')
        draft, used = run_agent(self.model, system=SYSTEM, task=task, tools=specs, output=CoordinationDraft)
        facts = read_fact(self.tools, 'get_run', args)  # A worker may have moved the run meanwhile.
        if draft.next_action not in allowed_actions(facts):
            raise AdapterFailure('AGENT_ACTION_NOT_ALLOWED', 'The proposed action is not allowed in this run state.')
        status = facts['execution_status']
        return CoordinationNote(run_id=run_id, execution_status=status, machine_verdict=facts['machine_verdict'],
                                waiting_on=_STATES[status][0], next_action=draft.next_action,
                                summary=fact_summary(facts, draft.next_action), model_summary=draft.summary,
                                model_blockers=draft.blockers, tools_used=tuple(used), synthetic=facts['synthetic'])
