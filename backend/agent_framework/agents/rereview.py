"""A08 rereview agent: decides whether to open a child run and compares results.

The only write is `request_rereview`, with the new context/asset bound by code.
Workflow still enforces the approved change request and version rules. Every
criterion is rechecked by the child run; this agent never carries a pass over.
"""
from __future__ import annotations

import json

from ..contracts import AssetRef, Context, FrameworkError
from ..ports import AdapterFailure
from .contracts import RereviewDraft, RereviewSummary
from .loop import ChatModel, ToolClient, ToolSpec, bound, read_fact, required, run_agent

_ROLE = '너는 포스터 검수 워크플로의 A08 재검수 에이전트다.\n'
_ANSWER = '마지막 답은 JSON 객체 하나만 출력한다:\n'
_REQUEST_OUTPUT_GUIDANCE = (
    '지금은 재검수 요청 여부를 판단하는 단계이며 전후 검사 결과를 비교하는 단계가 아니다. '
    'resolved, unresolved, new_violations는 비교 결과의 조건 ID 목록이므로 '
    '요청 성공·거절 여부와 관계없이 세 목록을 항상 빈 배열 []로 출력한다. '
    '승인 누락·거절 이유·실행 상태는 summary 문자열에만 적고 세 목록에는 넣지 않는다.\n')
REQUEST_SYSTEM = _ROLE + """부모 실행을 조회해 재검수 조건을 확인한다. 조건은 두 가지가 모두 참일 때만 충족된다:
1) execution_status == "waiting_revision"  2) human_decision == "changes_approved"
approve_criteria는 조건 초안 확정이며 수정 요청 승인이 아니다. 수정 요청 승인은 approve_changes다.
조건이 충족되면 request_rereview를 한 번 호출한다. 충족되지 않으면 호출하지 말고 이유를 설명한다.
rereview_requested는 request_rereview를 실제로 호출해 completed 결과를 받았을 때만 true다.
과거 pass나 승인은 새 버전에 승계되지 않으며, 새 실행에서 조건을 다시 승인하고 전체 조건을 다시 검사한다.
""" + _REQUEST_OUTPUT_GUIDANCE + _ANSWER + '{"rereview_requested": true|false, "resolved": [], "unresolved": [], "new_violations": [], "summary": "한국어 설명"}'
COMPARE_SYSTEM = _ROLE + """부모 실행(수정 전)과 자식 실행(수정 후)의 validated_items를 조건 ID별로 비교해 설명한다.
- resolved: 부모에서 pass가 아니었고 자식에서 pass인 조건
- unresolved: 부모에서 pass가 아니었고 자식에서도 pass가 아닌 조건
- new_violations: 부모에서 pass였거나 없던 조건인데 자식에서 pass가 아닌 조건
자식 실행에 검증 결과가 아직 없으면 세 목록을 모두 비우고 대기 상태를 설명한다. 판정을 바꾸지 마라.
세 목록에는 조건 ID 문자열만 넣는다(예: ["requirement-number"]). 비교 설명은 summary에만 쓴다.
""" + _ANSWER + '{"resolved": [], "unresolved": [], "new_violations": [], "summary": "한국어 설명"}'
_FINAL = '최종 JSON 객체 하나만 다시 출력하라.'


def compare_verdicts(parent: dict, child: dict) -> tuple[bool, tuple, tuple, tuple]:
    before = {item['criterion_id']: item['verdict'] for item in parent['validated_items']}
    after = {item['criterion_id']: item['verdict'] for item in child['validated_items']}
    if not before or not after:
        return False, (), (), ()
    open_items = {key for key, verdict in before.items() if verdict != 'pass'}
    return (True, tuple(sorted(key for key in open_items if after.get(key) == 'pass')),
            tuple(sorted(key for key in open_items if after.get(key) != 'pass')),
            tuple(sorted(key for key, verdict in after.items() if verdict != 'pass' and key not in open_items)))


def _consistent(draft: RereviewDraft, *expected: tuple) -> None:
    if [set(draft.resolved), set(draft.unresolved), set(draft.new_violations)] != [set(ids) for ids in expected]:
        raise AdapterFailure('AGENT_OUTPUT_INCONSISTENT', 'The agent comparison differs from the recorded verdicts.')


class RereviewAgent:
    def __init__(self, model: ChatModel, tools: ToolClient):
        self.model, self.tools = model, tools

    def request(self, parent_run_id: str, context: Context, asset: AssetRef) -> RereviewSummary:
        context, asset = Context.model_validate(context), AssetRef.model_validate(asset)
        parent = read_fact(self.tools, 'get_run', {'run_id': parent_run_id})
        write = bound(self.tools, 'request_rereview', {'parent_run_id': parent_run_id,
                      'context': context.model_dump(mode='json'), 'asset': asset.model_dump(mode='json')})
        written: dict = {}  # The write receipt itself: a failed re-read must not hide a created child run.
        specs = [ToolSpec('get_parent_run', '부모 실행의 상태·판정·수정 요청을 조회한다.',
                          bound(self.tools, 'get_run', {'run_id': parent_run_id})),
                 ToolSpec('get_parent_approvals', '부모 실행의 사람 승인 기록을 조회한다.',
                          bound(self.tools, 'get_approval_history', {'run_id': parent_run_id})),
                 ToolSpec('request_rereview', f'새 포스터 버전 {asset.asset_version_id}로 자식 실행을 만든다. 한 번만 호출한다.',
                          lambda: written.setdefault('receipt', write()), writes=True)]

        def revise(answer: RereviewDraft, done: list[str]) -> str | None:
            # A 4B model often says it requested without calling the tool; point at the fact.
            called = 'request_rereview' in done
            if answer.rereview_requested == called:
                return None
            if called:
                return 'request_rereview가 completed로 끝나 자식 실행이 만들어졌다. rereview_requested를 true로 하여 ' + _FINAL
            if 'receipt' in written:
                return ('request_rereview 호출이 거절되어 자식 실행이 만들어지지 않았다. '
                        'rereview_requested를 false로 하고 거절 이유를 설명하는 ' + _FINAL)
            return ('request_rereview를 아직 호출하지 않았다. 재검수를 요청하기로 판단했다면 지금 request_rereview를 '
                    '호출하라. 요청하지 않을 거라면 rereview_requested를 false로 하여 ' + _FINAL)

        task = (f'부모 실행 {json.dumps(parent_run_id)}에 새 포스터 버전 {json.dumps(asset.asset_version_id)}이 '
                f'제출되었다. 재검수를 요청할지 판단하고 JSON으로 답하라. ' + _REQUEST_OUTPUT_GUIDANCE)
        used: list[str] = []
        try:
            draft, _ = run_agent(self.model, system=REQUEST_SYSTEM, task=task, tools=specs, output=RereviewDraft,
                                    revise=revise, completed_tools=used, format_guidance=_REQUEST_OUTPUT_GUIDANCE)
            requested = 'request_rereview' in used
            if draft.rereview_requested != requested:
                raise AdapterFailure('AGENT_OUTPUT_INCONSISTENT', 'The agent answer differs from the executed command.')
            _consistent(draft, (), (), ())
        except Exception:
            # The write receipt survives a later model/formatting failure.
            if not isinstance(written.get('receipt'), dict) or written['receipt'].get('state') != 'completed':
                raise
            requested = True
            draft = RereviewDraft(rereview_requested=True, summary='모델의 후속 설명을 확인하지 못했습니다.')
        child = required(written['receipt']) if requested else None
        rejected = None if requested else written.get('receipt', {}).get('error')
        error = FrameworkError(code=str(rejected.get('code') or 'TOOL_FAILED'),
                               message=str(rejected.get('message') or 'The tool call was rejected.')
                               ) if isinstance(rejected, dict) else None
        if child:
            summary = (f"자식 실행 {child['run_id']}을 만들었습니다(상태 {child['execution_status']}). "
                       '새 실행에서 조건을 다시 승인한 뒤 전체 조건을 다시 검사합니다.')
        elif error:
            summary = f'재검수 요청이 거절되었습니다({error.code}). 자식 실행은 만들어지지 않았습니다.'
        else:
            summary = (f"재검수를 요청하지 않았습니다. 부모 실행 상태 {parent['execution_status']}, "
                       f"사람 결정 {parent['human_decision']}.")
        return RereviewSummary(parent_run_id=parent_run_id, rereview_requested=requested, request_error=error,
                               child_run_id=child and child['run_id'], child_status=child and child['execution_status'],
                               summary=summary, model_summary=draft.summary, tools_used=tuple(used),
                               synthetic=(child or parent)['synthetic'])

    def compare(self, parent_run_id: str, child_run_id: str) -> RereviewSummary:
        if read_fact(self.tools, 'get_run', {'run_id': child_run_id})['parent_run_id'] != parent_run_id:
            raise AdapterFailure('RUN_SCOPE_MISMATCH', 'The child run does not belong to this parent run.')
        specs = [ToolSpec('get_parent_run', '부모 실행(수정 전)의 검증 항목과 수정 요청을 조회한다.',
                          bound(self.tools, 'get_run', {'run_id': parent_run_id})),
                 ToolSpec('get_child_run', '자식 실행(수정 후)의 상태와 검증 항목을 조회한다.',
                          bound(self.tools, 'get_run', {'run_id': child_run_id})),
                 ToolSpec('get_child_steps', '자식 실행의 역할별 단계 기록을 조회한다.',
                          bound(self.tools, 'get_steps', {'run_id': child_run_id}))]
        task = (f'부모 실행 {json.dumps(parent_run_id)}과 자식 실행 {json.dumps(child_run_id)}의 '
                f'검수 결과를 비교하고 JSON으로 답하라.')
        draft, used = run_agent(self.model, system=COMPARE_SYSTEM, task=task, tools=specs, output=RereviewDraft)
        parent = read_fact(self.tools, 'get_run', {'run_id': parent_run_id})
        child = read_fact(self.tools, 'get_run', {'run_id': child_run_id})
        compared, resolved, unresolved, new = compare_verdicts(parent, child)
        _consistent(draft, resolved, unresolved, new)
        summary = (' '.join(f'{label} {len(ids)}건' + (f"({', '.join(ids)})" if ids else '') + '.'
                            for label, ids in (('해결', resolved), ('미해결', unresolved), ('새 위반', new)))
                   if compared else f"자식 실행이 {child['execution_status']} 상태라 비교할 검증 결과가 아직 없습니다.")
        return RereviewSummary(parent_run_id=parent_run_id, child_run_id=child_run_id,
                               child_status=child['execution_status'], rereview_requested=True,
                               compared=compared, resolved=resolved, unresolved=unresolved, new_violations=new,
                               summary=summary, model_summary=draft.summary, tools_used=tuple(used),
                               synthetic=child['synthetic'])
