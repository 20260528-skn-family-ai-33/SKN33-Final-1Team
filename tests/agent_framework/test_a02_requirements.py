"""Literal compilation regressions; no model, answers file or live database."""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from backend.agent_framework.a02_requirements import compile_requirements
from backend.agent_framework.contracts import PDFTextSource
from backend.agent_framework.ports import AdapterFailure


def compile_text(text,quotes):
    source=PDFTextSource(block_id='p1-text',page=1,text=text,text_sha256=sha256(text.encode()).hexdigest())
    candidates=[SimpleNamespace(quote=quote,block_id=source.block_id,required=True,unresolved=False,kind='text') for quote in quotes]
    return compile_requirements(candidates,(source,),'document-v1')


def test_literal_values_are_separate_from_instructions_and_keep_allowances():
    text=('제목에 ‘밤의 도서관’과 ‘달의 서가’를 모두 표기한다.\n'
          '허용 범위: 줄바꿈·공백 차이는 허용한다.\n'
          '행사 연도는 2031년으로 표기한다.\n'
          '허용 범위: 2031 또는 ‘31년’ 표기를 허용한다.\n'
          '장소는 ‘동쪽 마을문화관’으로 표기한다.\n')
    quotes=[line for line in text.splitlines() if line.endswith('표기한다.')]
    result,_=compile_text(text,quotes)
    assert [(r.details.field,r.expected) for r in result]==[
        ('title_phrase','밤의 도서관'),('title_phrase','달의 서가'),('year','2031'),('venue','동쪽 마을문화관')]
    assert all(r.details.expected_source.quote==r.expected for r in result)
    assert all(r.operator=='manual' and r.details.qualifiers for r in result[:3])
    assert result[3].operator=='contains'
    assert all(r.quote in text and r.details.expected_source.quote in r.quote for r in result)


def test_metadata_axes_are_distinct_and_duplicate_selections_do_not_duplicate_checks():
    text='파일 형식\nPNG (.png)\n픽셀 크기\n가로 4100픽셀 × 세로 2980픽셀\n색상 모드\nRGB\n'
    dimension='가로 4100픽셀 × 세로 2980픽셀'
    result,_=compile_text(text,['PNG (.png)',dimension,dimension,'RGB'])
    assert [(r.details.field,r.expected) for r in result]==[
        ('file_format','PNG'),('pixel_width','4100'),('pixel_height','2980'),('color_mode','RGB')]
    assert all(r.details.target=='file_metadata' for r in result)
    assert result[1].unit==result[2].unit=='픽셀'


def test_date_and_weekday_are_literal_and_tolerance_is_not_silently_normalized():
    text='개막일은 2031년 12월 9일로, 요일은 화요일로 표기한다.\n허용 범위: 구분자 차이는 허용한다.\n'
    result,_=compile_text(text,[text.splitlines()[0]])
    assert [(r.details.field,r.expected) for r in result]==[('event_date','2031년 12월 9일'),('weekday','화요일')]
    assert all(r.operator=='manual' and r.details.qualifiers for r in result)


def test_explicit_undecided_source_is_retained_even_if_model_omitted_it():
    text='장소는 ‘북쪽 전시관’으로 표기한다.\n관람시간 - 확인 중\n시작 시각은 확인 중입니다. 담당자 회신 후 반영해 주세요.\n'
    result,warnings=compile_text(text,[text.splitlines()[0]])
    pending=[r for r in result if r.unresolved]
    assert len(pending)==1 and pending[0].expected=='' and pending[0].operator=='manual'
    assert pending[0].required and pending[0].details.origin=='retained_unresolved_source'
    assert '시작 시각' in pending[0].quote and pending[0].quote in text
    assert 'explicit_unresolved_source_retained_by_policy' in warnings


def test_delivery_quantity_does_not_become_an_image_object_count():
    text='디지털 포스터 파일 2점'
    result,_=compile_text(text,[text])
    assert result[0].details.target=='delivery' and result[0].details.field=='quantity'
    assert result[0].expected=='2' and result[0].operator=='manual'


def test_unrecognized_meaning_does_not_keep_an_instruction_as_a_contains_value():
    text='차분하고 또렷한 인상을 전달한다.'
    result,_=compile_text(text,[text])
    assert result[0].expected=='' and result[0].operator=='manual'
    assert result[0].quote==text and result[0].details.target=='unspecified'


def test_a_quoted_visual_concept_does_not_become_required_poster_wording():
    text='‘과거와 미래’의 분위기를 사진으로 표현한다.'
    result,_=compile_text(text,[text])
    assert result[0].expected=='' and result[0].operator=='manual'


def test_wrapped_allowance_is_preserved_through_the_end_of_its_sentence():
    text='제목은 ‘새벽 도서관’으로 표기한다.\n허용 범위: 줄바꿈과\n 공백 차이를 허용한다.\n2\n다음 조건'
    result,_=compile_text(text,[text.splitlines()[0]])
    assert result[0].expected=='새벽 도서관' and result[0].operator=='manual'
    assert result[0].details.qualifiers[0].quote=='허용 범위: 줄바꿈과\n 공백 차이를 허용한다.'
    assert result[0].quote in text and '다음 조건' not in result[0].quote


def test_conflicting_repeated_selections_are_rejected():
    text='가로 500픽셀';source=PDFTextSource(block_id='p1-text',page=1,text=text,text_sha256=sha256(text.encode()).hexdigest())
    common={'quote':text,'block_id':source.block_id,'unresolved':False,'kind':'number'}
    with pytest.raises(AdapterFailure) as error:
        compile_requirements([SimpleNamespace(**common,required=True),SimpleNamespace(**common,required=False)],(source,),'doc-v1')
    assert error.value.code=='A02_SOURCE_SELECTION_CONFLICT'


def recorded_draft():
    import json
    from pathlib import Path
    import httpx
    from backend.agent_framework.a02 import RegisteredPDFExtractor,QwenProvider
    from backend.agent_framework.invocation import invoke_role
    from tests.agent_framework.test_a02_extraction import document,request,qwen_settings
    fixture=json.loads((Path(__file__).parent/'fixtures/a02_qwen_exhibition_source_selection.json').read_text())
    doc=document();assert doc.reference.sha256==fixture['document_sha256']
    settings=qwen_settings();content=json.dumps(fixture['selection'],ensure_ascii=False)
    provider=QwenProvider(settings,transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
        'model':settings.model,'choices':[{'finish_reason':'stop','message':{'content':content}}]})))
    req=request(doc).model_copy(update={'synthetic':True})
    return req,invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=provider),req)


def test_recorded_protocol_regression_separates_values_axes_and_retains_missing_content():
    _,draft=recorded_draft()
    fields={}
    for c in draft.criteria:fields.setdefault(c.details.field,[]).append(c)
    assert [c.expected for c in fields['title_phrase']]==['수원의 영화','수원의 극장']
    assert fields['year'][0].expected=='2026'
    assert fields['event_date'][0].expected=='2026년 5월 14일'
    assert fields['weekday'][0].expected=='목요일'
    assert fields['venue'][0].expected=='열린문화공간 후소 전시실'
    assert fields['file_format'][0].expected=='JPEG'
    assert fields['pixel_width'][0].expected==fields['pixel_height'][0].expected=='3000'
    assert len(fields['pixel_width'])==len(fields['pixel_height'])==1
    assert all(c.operator=='manual' and c.details.qualifiers for c in fields['title_phrase'])
    pending=[c for c in draft.criteria if c.criterion_id in draft.extraction.unresolved_criterion_ids]
    assert len(pending)==1 and '관람시간' in pending[0].text
    assert pending[0].expected=='' and not pending[0].human_added
    assert pending[0].details.origin=='retained_unresolved_source'
    assert draft.synthetic and draft.extraction.policy=='a02-source-compile-v2'


def test_common_boundary_rejects_a_removed_source_marked_unresolved_requirement():
    from backend.agent_framework.invocation import accept_role_output
    req,draft=recorded_draft();ids=draft.extraction.unresolved_criterion_ids
    bad=draft.model_copy(update={'criteria':tuple(c for c in draft.criteria if c.criterion_id not in ids),
        'extraction':draft.extraction.model_copy(update={'unresolved_criterion_ids':()})})
    with pytest.raises(AdapterFailure) as error:accept_role_output('A02',req,bad)
    assert error.value.code=='EXTRACTION_UNRESOLVED_SOURCE_OMITTED'


def test_human_resolution_uses_supplement_provenance_without_a_stale_pdf_value_anchor():
    from datetime import datetime,timezone
    from uuid import uuid4
    from backend.agent_framework.criteria_contracts import CriteriaVersionRequest,CriteriaEdit,CriterionResolution,SupplementInput
    from backend.agent_framework.criteria_review import revise_criteria
    from backend.agent_framework.postgres import digest
    _,draft=recorded_draft();year=next(c for c in draft.criteria if c.details.field=='year')
    edit=CriteriaEdit(criterion_id=year.criterion_id,action='amend',reason='Synthetic source replacement regression',
        source=SupplementInput(reference='synthetic test supplement',quote='2031'),
        resolution=CriterionResolution(kind='text',operator='contains',expected='2031',unit=None,required=True))
    req=CriteriaVersionRequest(parent_version_id=None,expected_base_sha256=digest(draft),purpose='synthetic_test',edits=(edit,))
    version=revise_criteria(draft,None,req,version_id=uuid4(),created_by=draft.context.actor_id,created_at=datetime.now(timezone.utc))
    amended=next(c for c in version.criteria if c.criterion_id==year.criterion_id)
    assert amended.expected=='2031' and amended.human_added and amended.value_source.synthetic
    assert amended.details.expected_source is None and amended.details.qualifiers==year.details.qualifiers
