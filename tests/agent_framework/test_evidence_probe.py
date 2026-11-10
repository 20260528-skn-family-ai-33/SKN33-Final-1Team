"""Evidence probe harness with a scripted model: tool offer, binding and grading only.

No model server or database. The tool results below are structural fixtures for
the grading code, not review data. Real Qwen runs live in
test_evidence_probe_live.py (live_model + db_integration).
"""
import json

import pytest

from backend.agent_framework.mcp_server import EVIDENCE_TOOL_NAMES
from tests.agent_framework.evidence_probe import NOTE, PROBES, run_probe, write_results
from tests.agent_framework.test_agents_mcp import Scripted, answer, call

RUN = 'child-run'
RESULTS = {
    'get_approved_criteria': {'criteria': [{'criterion_id': 'c-1'}, {'criterion_id': 'c-2'}]},
    'get_history': {'criteria_versions': [{'criteria_version_id': 'v-1', 'approved': False},
                                          {'criteria_version_id': 'v-2', 'approved': True}]},
    'verify_sources': {'status': 'stale', 'criteria': [{'criterion_id': 'c-1', 'status': 'verified'},
                                                       {'criterion_id': 'c-2', 'status': 'verified'}]},
    'get_relations': {'edges': [{'source': 'run:' + RUN, 'kind': 'applies', 'target': 'criteria:v-2'},
                                {'source': 'run:parent-run', 'kind': 'reinspected_as', 'target': 'run:' + RUN},
                                {'source': 'run:criteria-run', 'kind': 'applies', 'target': 'criteria:v-2'}]},
    'get_change_impact': {'affected_runs': [{'run_id': 'parent-run'}, {'run_id': RUN}], 'document_changed': True},
}
RIGHT = {
    'criteria': {'criterion_ids': ['c-2', 'c-1']},
    'history': {'criteria_version_count': 2, 'approved_criteria_version_id': 'v-2'},
    'sources': {'status': 'stale', 'mismatch_criterion_ids': []},
    'relations': {'pinned_criteria_version_id': 'criteria:v-2', 'parent_run_id': 'parent-run'},
    'impact': {'affected_run_ids': [RUN, 'parent-run'], 'document_changed': True},
}
BY_NAME = {probe.name: probe for probe in PROBES}


class Tools:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {'state': 'completed', 'result': RESULTS[name]}


@pytest.mark.parametrize('probe', PROBES, ids=lambda p: p.name)
def test_each_probe_offers_the_five_tools_bound_to_the_run_and_grades_by_the_tool(probe):
    model, tools = Scripted(call(probe.truth_tool), answer(summary='모델 문장', **RIGHT[probe.name])), Tools()
    result = run_probe(model, tools, RUN, probe)
    assert model.tool_names[0] == list(EVIDENCE_TOOL_NAMES)
    assert {args['run_id'] for _, args in tools.calls} == {RUN}  # The model never chooses the run.
    assert result.passed and result.grounded and result.tools_used == (probe.truth_tool,)
    assert result.model_summary == '모델 문장' and result.error is None
    assert tools.calls[-1][0] == probe.truth_tool  # Truth is read again by code after the answer.


def test_right_facts_without_calling_a_tool_do_not_pass():
    result = run_probe(Scripted(answer(**RIGHT['impact'])), Tools(), RUN, BY_NAME['impact'])
    assert not result.passed and not result.grounded and not result.mismatched


def test_calling_a_tool_that_lacks_the_facts_is_not_grounding():
    model = Scripted(call('get_history'), answer(**RIGHT['relations']))
    result = run_probe(model, Tools(), RUN, BY_NAME['relations'])
    assert not result.passed and result.tools_used == ('get_history',) and not result.grounded


def test_wrong_facts_are_named_by_field():
    model = Scripted(call('verify_sources'), answer(status='verified', mismatch_criterion_ids=[]))
    result = run_probe(model, Tools(), RUN, BY_NAME['sources'])
    assert not result.passed and result.mismatched == ('status',)
    assert result.expected == {'status': 'stale', 'mismatch_criterion_ids': []}


def test_missing_relations_are_null_and_blank_answers_count_as_null():
    result = run_probe(Scripted(call('get_relations'), answer(pinned_criteria_version_id='v-2', parent_run_id='')),
                       Tools(), 'parent-run', BY_NAME['relations'])
    assert result.expected == {'pinned_criteria_version_id': None, 'parent_run_id': None}
    assert result.mismatched == ('pinned_criteria_version_id',)


def test_model_failures_are_recorded_not_raised():
    prose = {'content': '조건은 두 개입니다.'}
    result = run_probe(Scripted(prose, prose), Tools(), RUN, BY_NAME['criteria'])
    assert not result.passed and result.error == 'AGENT_OUTPUT_INVALID' and result.reported is None


def test_results_file_marks_the_questions_as_a_working_check(tmp_path):
    model = Scripted(call('get_approved_criteria'), answer(**RIGHT['criteria']))
    result = run_probe(model, Tools(), RUN, BY_NAME['criteria'])
    path = tmp_path / 'probe.json'
    write_results(path, [result], scene='structural fixture')
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['note'] == NOTE and data['passed'] == data['total'] == 1
    assert data['probes'][0]['question'] == BY_NAME['criteria'].question and 'http' not in path.read_text(encoding='utf-8')
