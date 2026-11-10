from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from backend.agent_framework.a02 import validate_trace
from backend.agent_framework.contracts import CriteriaDraft
from backend.agent_framework.a02_semantic import PRESENTATION_PROMPT
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.requirements_presentation import present_requirements
from backend.app.security.errors import AuthError
from tests.agent_framework.test_a02_semantic import extract,model_reply,text_document,requirement


def presentation_reply():
    reply=model_reply()
    values=[('행사명','김천김밥축제'),('기간','2026.10.23 ~ 10.25'),
            ('기간','2026.10.23 ~ 10.25'),('장소','직지문화공원'),('로고','김천시 로고가 포함되어야 합니다.')]
    for item,(label,value) in zip(reply['requirements'],values):item['presentation']={'label':label,'value':value}
    return reply


def projected(draft):
    return present_requirements(SimpleNamespace(criteria=draft,context=draft.context))


def test_one_qwen_call_returns_checked_paragraphs_with_both_date_sources():
    _,draft,calls,_=extract(presentation_reply(),presentation=True)
    assert len(calls)==1 and calls[0]['messages'][0]['content']==PRESENTATION_PROMPT
    assert draft.extraction.policy=='a02-semantic-v4'
    result=projected(draft)
    assert [item['text'] for item in result['conditions']]==[
        '행사명: 김천김밥축제','기간: 2026.10.23 ~ 10.25','장소: 직지문화공원','김천시 로고가 포함되어야 합니다.']
    assert result['conditions'][1]['criterion_ids']==['c002','c003']
    assert len(result['conditions'][1]['source_references'])==2
    assert result['approved'] is False and len(draft.criteria)==5


def test_arbitrary_labels_multiple_places_and_negative_visual_rules_are_not_limited_to_three_fields():
    text='장소: 가공원\n장소: 나공원\n장소: 다공원\n문구와 이미지가 잘리지 않아야 합니다.\n글꼴 두께는 굵게 유지합니다.'
    reply={'requirements':[]}
    for line,value in enumerate(['가공원','나공원','다공원'],1):
        reply['requirements'].append(requirement(line,'venue',value,presentation={'label':'장소','value':value}))
    for line,field,label,value in [(4,'content_visibility','잘림','문구와 이미지가 잘리지 않아야 합니다.'),
                                   (5,'typography_style','글꼴','글꼴 두께는 굵게 유지합니다.')]:
        reply['requirements'].append(requirement(line,field,value,target='poster_visual',kind='visual',operator='present',
            presentation={'label':label,'value':value}))
    _,draft,_,_=extract(reply,doc=text_document(text),presentation=True)
    result=projected(draft)
    assert len(result['conditions'])==5
    assert [c['text'] for c in result['conditions'][:3]]==['장소: 가공원','장소: 나공원','장소: 다공원']
    assert '잘리지 않아야' in result['conditions'][3]['text']
    assert result['conditions'][4]['type']=='visual'


@pytest.mark.parametrize('value',['문서에 없는 행사','김천김밥축제\n추가 문구','없는 이름 김천김밥축제'])
def test_ungrounded_or_multiline_presentation_is_rejected(value):
    reply=presentation_reply();reply['requirements'][0]['presentation']['value']=value
    _,draft,_,_=extract(reply,presentation=True)
    assert len(draft.criteria)==4
    assert draft.extraction.rejected_candidates[0].code=='A02_PRESENTATION_UNGROUNDED'


def test_pending_item_is_kept_in_draft_and_notes_without_entering_the_review_text():
    doc=text_document('행사명: 우리 전시\n관람시간 - 확인 중')
    reply={'requirements':[
        requirement(1,'event_name','우리 전시',presentation={'label':'행사명','value':'우리 전시'}),
        requirement(2,'viewing_hours','',operator='manual',unresolved=True,presentation=None)]}
    _,draft,_,_=extract(reply,doc=doc,presentation=True)
    result=projected(draft)
    assert len(draft.criteria)==2 and len(result['conditions'])==1
    assert result['pending'][0]['criterion_id']=='c002'
    assert draft.extraction.unresolved_criterion_ids==('c002',)


def test_old_semantic_runs_remain_valid_without_manufacturing_model_paragraphs():
    doc=text_document();_,draft,_,_=extract(doc=doc)
    validate_trace(doc.reference,draft)
    with pytest.raises(AuthError) as error:projected(draft)
    assert error.value.code=='REQUIREMENTS_PRESENTATION_NOT_AVAILABLE'


def test_missing_resolved_presentation_and_unresolved_fabricated_text_are_rejected():
    reply=presentation_reply();reply['requirements'][0].pop('presentation')
    _,draft,_,_=extract(reply,presentation=True)
    assert len(draft.criteria)==4
    assert draft.extraction.rejected_candidates[0].code=='A02_PRESENTATION_MISSING'
    doc=text_document('관람시간 - 확인 중')
    reply={'requirements':[requirement(1,'viewing_hours','',operator='manual',unresolved=True,
        presentation={'label':'시간','value':'관람시간'})]}
    with pytest.raises(AdapterFailure):extract(reply,doc=doc,presentation=True)


@pytest.mark.parametrize('bad_value,code',[
    (None,'A02_DUPLICATE_REQUIREMENT'),
    ('원문에 없는 장소','A02_EXPECTED_UNGROUNDED'),
])
def test_same_exhibition_pdf_keeps_valid_criteria_and_records_the_rejected_candidate(bad_value,code):
    from tests.agent_framework.replay_contract import current_contract_copy
    from tests.agent_framework.test_a02_extraction import document

    recording=json.loads((Path(__file__).parent/'fixtures/a02_presentation_pdf_response.json').read_text())
    doc=document('EXH-NEW004')
    assert doc.reference.sha256==recording['_meta']['source_sha256']
    reply=current_contract_copy(recording['response'])
    _,baseline,_,_=extract(reply,doc,presentation=True)
    assert 'rejected_candidates' not in baseline.extraction.model_dump()
    candidate=deepcopy(next(item for item in reply['requirements'] if item['field']=='venue'))
    if bad_value is not None:
        candidate['expected']=bad_value
    reply['requirements'].append(candidate)
    _,draft,calls,_=extract(reply,doc,presentation=True)
    assert len(calls)==1 and draft.criteria==baseline.criteria and len(draft.criteria)==13
    saved=CriteriaDraft.model_validate_json(draft.model_dump_json())
    validate_trace(doc.reference,saved)
    rejection,=saved.extraction.rejected_candidates
    assert rejection.candidate_index==len(reply['requirements']) and rejection.code==code
    assert '열린문화공간 후소 전시실' in ' '.join(rejection.source.quote.split())
    assert rejection.source.page==1
    assert bad_value is None or bad_value not in saved.model_dump_json()
    result=projected(saved)
    assert result['approved'] is False and len(result['conditions'])==12 and len(result['pending'])==1
    assert any('후보 1개' in warning for warning in result['warnings'])
    invalid_source=rejection.source.model_copy(update={'quote':'원문에 없는 인용문'})
    invalid_trace=saved.extraction.model_copy(update={
        'rejected_candidates':(rejection.model_copy(update={'source':invalid_source}),)})
    with pytest.raises(ValueError,match='EXTRACTION_REJECTION_SOURCE_MISMATCH'):
        validate_trace(doc.reference,saved.model_copy(update={'extraction':invalid_trace}))


def test_display_spacing_may_differ_while_literal_condition_and_source_stay_unchanged():
    doc=text_document('가로 3000픽셀')
    reply={'requirements':[requirement(1,'pixel_width','3000',target='file_metadata',kind='number',operator='equals',unit='픽셀',
        presentation={'label':'가로','value':'3000 픽셀'})]}
    _,draft,_,_=extract(reply,doc=doc,presentation=True)
    assert draft.criteria[0].expected=='3000' and draft.criteria[0].source.quote=='가로 3000픽셀'
    assert projected(draft)['conditions'][0]['text']=='가로: 3000 픽셀'
    reply['requirements'][0]['presentation']['value']='3000 px'
    with pytest.raises(AdapterFailure):extract(reply,doc=doc,presentation=True)


@pytest.mark.parametrize('field',['pixel_width','pixel_height'])
def test_dimensions_cannot_lose_their_axis_in_a_generic_display_paragraph(field):
    doc=text_document('가로 3000픽셀 × 세로 3000픽셀')
    item=requirement(1,field,'3000',target='file_metadata',kind='number',operator='equals',unit='픽셀',
        presentation={'label':'픽셀 크기','value':'3000픽셀'})
    with pytest.raises(AdapterFailure):extract({'requirements':[item]},doc=doc,presentation=True)
    item['presentation']['value']='가로 3000픽셀 × 세로 3000픽셀'
    _,draft,_,_=extract({'requirements':[item]},doc=doc,presentation=True)
    assert '가로' in projected(draft)['conditions'][0]['text']
