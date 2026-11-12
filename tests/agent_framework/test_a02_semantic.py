"""Semantic protocol boundaries with fake HTTP, never a live model score."""
from copy import deepcopy
from hashlib import sha256
import json

import httpx
import pytest

from backend.agent_framework.a02 import QwenProvider,RegisteredPDFExtractor,prepare_sources
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.a02_semantic import SEMANTIC_PROMPT,SEMANTIC_PROMPT_HASH
from backend.agent_framework.adapters.synthetic import sample_inputs
from backend.agent_framework.contracts import DocumentRef,ExtractRequest
from backend.agent_framework.files import VerifiedDocument
from backend.agent_framework.invocation import invoke_role,accept_role_output
from backend.agent_framework.ports import AdapterFailure

BRIEF='행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n장소: 직지문화공원\n김천시 로고가 포함되어야 합니다.'


def text_document(text=BRIEF):
    context,_,_=sample_inputs();context=context.model_copy(update={'asset_version_id':None})
    raw=text.encode();ref=DocumentRef(file_id='text-input',document_version_id=context.document_version_id,
        sha256=sha256(raw).hexdigest(),media_type='text/plain',page_count=1)
    return VerifiedDocument(context,ref,raw,1)


def requirement(line,field,expected,**changes):
    return {'source':{'start_line':f'input-text:L{line}','end_line':f'input-text:L{line}'},
        'target':'poster_text','field':field,'kind':'text','operator':'contains','expected':expected,
        'normalized_expected':None,'unit':None,'required':True,'unresolved':False,'qualifiers':[],**changes}


def model_reply():
    return {'requirements':[requirement(1,'event_name','김천김밥축제'),
        requirement(2,'start_date','2026.10.23',normalized_expected='2026-10-23'),
        requirement(2,'end_date','10.25',normalized_expected='2026-10-25'),
        requirement(3,'venue','직지문화공원'),
        requirement(4,'logo','김천시 로고',target='poster_visual',kind='visual',operator='present')]}


def extract(reply=None,doc=None,*,respond=None,presentation=False):
    doc=doc or text_document();reply=model_reply() if reply is None else reply
    settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions',presentation=presentation)
    raw=json.dumps(reply,ensure_ascii=False);calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        if respond:respond(calls[-1])
        return httpx.Response(200,json={'model':settings.model,'choices':[{'finish_reason':'stop','message':{'content':raw}}]})
    provider=QwenProvider(settings,transport=httpx.MockTransport(handler))
    req=ExtractRequest(context=doc.context,document=doc.reference,synthetic=True)
    draft=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=provider),req)
    return req,draft,calls,raw


def test_free_input_is_semantically_structured_by_the_model_without_literal_compilation(monkeypatch):
    def forbidden(*a,**k):raise AssertionError('literal compiler used in semantic profile')
    monkeypatch.setattr('backend.agent_framework.a02.compile_requirements',forbidden)
    req,draft,calls,raw=extract()
    assert len(calls)==1 and calls[0]['messages'][0]['content']==SEMANTIC_PROMPT
    assert 'parts' not in calls[0]['messages'][1]['content'] and 'actor_id' not in calls[0]['messages'][1]['content']
    assert [(c.details.field,c.expected) for c in draft.criteria]==[
        ('event_name','김천김밥축제'),('start_date','2026.10.23'),('end_date','10.25'),('venue','직지문화공원'),('logo','김천시 로고')]
    assert draft.criteria[-1].kind=='visual' and draft.criteria[-1].details.target=='poster_visual'
    assert draft.criteria[2].details.normalized_expected=='2026-10-25'
    assert all(c.details.origin=='model_structured' and c.details.compiler=='qwen-semantic-v1' for c in draft.criteria)
    assert draft.extraction.input_kind=='free_text' and draft.extraction.parser=='utf8-source-v1'
    assert draft.extraction.policy=='a02-semantic-v3' and draft.extraction.prompt_sha256==SEMANTIC_PROMPT_HASH
    assert draft.extraction.response_sha256==sha256(raw.encode()).hexdigest()


def test_long_pdf_keeps_source_ids_and_marks_incomplete_chunks():
    import pymupdf
    with pymupdf.open() as pdf:
        for text in ('Venue: City Hall','Title: Festival','Date: November 20'):
            pdf.new_page().insert_text((40,40),text)
        raw=pdf.tobytes()
    context=text_document().context
    ref=DocumentRef(file_id='long-pdf',document_version_id=context.document_version_id,
        sha256=sha256(raw).hexdigest(),media_type='application/pdf',page_count=3)
    doc=VerifiedDocument(context,ref,raw,3)
    settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')
    calls=[]
    def handler(req):
        data=json.loads(req.content)
        sources=json.loads(data['messages'][1]['content'])['sources']
        calls.append([source['page'] for source in sources])
        if sources[0]['page']==3:
            return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{...'}}]})
        item=requirement(1,'venue','City Hall',source={'start_line':'p1-text:L1','end_line':'p1-text:L1'})
        unsent=requirement(1,'venue','November 20',source={'start_line':'p3-text:L1','end_line':'p3-text:L1'})
        content=json.dumps({'requirements':[item,unsent]})
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]})
    provider=QwenProvider(settings,transport=httpx.MockTransport(handler))
    draft=RegisteredPDFExtractor(lambda _:doc,settings,provider=provider).extract(
        ExtractRequest(context=context,document=ref,synthetic=True))
    assert calls==[[1,2],[3]]
    assert [(item.source.page,item.expected) for item in draft.criteria]==[(1,'City Hall')]
    assert draft.extraction.rejected_candidates[0].code=='A02_SOURCE_MISMATCH'
    assert 'page_3_model_output_incomplete' in draft.warnings


@pytest.mark.parametrize('value',['10,000','1,000,000','10,000장'])
def test_number_separators_preserve_literal_source_and_match_a06_syntax(value):
    from backend.agent_framework.validation import _number
    unit='장';text='납품 수량: '+value+('' if value.endswith(unit) else unit)
    _,draft,calls,_=extract({'requirements':[requirement(1,'quantity',value,target='delivery',kind='number',operator='equals',unit=unit)]},text_document(text))
    assert len(calls)==1 and draft.criteria[0].expected==value
    assert draft.criteria[0].details.expected_source.quote==value
    assert _number(value,unit)[1]==unit


@pytest.mark.parametrize('value',['0,001','001,000','1,00','1e3'])
def test_invalid_numeric_separators_remain_rejected(value):
    with pytest.raises(AdapterFailure) as error:
        extract({'requirements':[requirement(1,'quantity',value,target='delivery',kind='number',operator='equals')]},text_document('납품 수량: '+value))
    assert error.value.code=='A02_RESPONSE_INVALID'


def test_new_free_form_field_and_visual_meaning_are_preserved_instead_of_rule_reclassification():
    doc=text_document('서체 분위기: 잉크가 번진 듯한 손글씨')
    reply={'requirements':[requirement(1,'typography_style','잉크가 번진 듯한 손글씨',target='poster_visual',kind='visual',operator='present')]}
    _,draft,_,_=extract(reply,doc)
    assert draft.criteria[0].expected=='잉크가 번진 듯한 손글씨'
    assert draft.criteria[0].details.field=='typography_style' and draft.criteria[0].operator=='present'


def test_generation_schema_enforces_visual_types_and_date_only_normalization():
    from jsonschema import Draft202012Validator
    from backend.agent_framework.a02_semantic import semantic_schema
    from backend.agent_framework.a02_source_selection import index_sources
    doc=text_document();settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')
    index,_=index_sources(prepare_sources(doc,settings));schema=semantic_schema(index)
    Draft202012Validator.check_schema(schema);validator=Draft202012Validator(schema)
    assert validator.is_valid(model_reply())
    bad=model_reply();bad['requirements'][-1].update(kind='text',operator='contains')
    assert not validator.is_valid(bad)
    bad=model_reply();bad['requirements'][0]['normalized_expected']='2026-01-01'
    assert not validator.is_valid(bad)
    bad=model_reply();bad['requirements'][0].update(target='file_metadata',field='pixel_width',expected='3000픽셀')
    assert not validator.is_valid(bad)


def test_model_proposed_canonical_dates_keep_the_original_literal_citation():
    reply=model_reply()
    reply['requirements'][1]['expected']='2026-10-23'
    reply['requirements'][2]['expected']='2026-10-25'
    _,draft,_,_=extract(reply)
    assert draft.criteria[1].expected=='2026-10-23'
    assert draft.criteria[1].details.expected_source.quote=='2026.10.23'
    assert draft.criteria[2].expected=='2026-10-25'
    assert draft.criteria[2].details.expected_source.quote=='10.25'
    bad=model_reply();bad['requirements'][2].update(expected='2027-10-25',normalized_expected='2027-10-25')
    _,partial,_,_=extract(bad)
    assert len(partial.criteria)==4 and partial.extraction.rejected_candidates[0].candidate_index==3


@pytest.mark.parametrize('mutation,code',[
    ('value','A02_EXPECTED_UNGROUNDED'),('source','A02_SOURCE_MISMATCH'),('backwards','A02_SOURCE_MISMATCH'),
    ('qualifier','A02_QUALIFIER_UNGROUNDED'),('unit','A02_UNIT_UNGROUNDED'),
    ('normalized','A02_NORMALIZED_VALUE_UNGROUNDED'),('normalization_kind','A02_NORMALIZATION_UNSUPPORTED'),
    ('duplicate','A02_DUPLICATE_REQUIREMENT'),
])
def test_semantic_output_is_rejected_instead_of_repaired(mutation,code):
    reply=model_reply();item=reply['requirements'][0]
    if mutation=='value':item['expected']='없는 행사 이름'
    if mutation=='source':item['source']['start_line']='unknown:L1'
    if mutation=='backwards':item['source'].update(start_line='input-text:L4',end_line='input-text:L1')
    if mutation=='qualifier':item['qualifiers']=[{'start_line':'input-text:L3','end_line':'input-text:L3'}]
    if mutation=='unit':item['unit']='text'
    if mutation=='normalized':reply['requirements'][2]['normalized_expected']='2027-10-25'
    if mutation=='normalization_kind':item['normalized_expected']='2026-10-23'
    if mutation=='duplicate':reply['requirements'].append(deepcopy(item))
    _,draft,_,_=extract(reply)
    rejected,=draft.extraction.rejected_candidates
    assert rejected.code==code
    assert len(draft.criteria)==len(reply['requirements'])-1
    if mutation in ('source','backwards'):
        assert rejected.source is None
    else:
        assert rejected.source.quote in BRIEF


def test_response_with_unowned_fields_still_fails_as_an_invalid_document_reply():
    reply=model_reply()
    reply['requirements'][0]['actor_id']='not-owned-by-model'
    with pytest.raises(AdapterFailure) as error:extract(reply)
    assert error.value.code=='A02_RESPONSE_INVALID'


def test_all_rejected_items_fail_even_when_the_source_has_a_pending_note():
    doc=text_document(BRIEF+'\n관람시간은 확인 중입니다.')
    reply={'requirements':[requirement(1,'event_name','없는 행사 이름')]}
    with pytest.raises(AdapterFailure) as error:extract(reply,doc)
    assert error.value.code=='A02_EXPECTED_UNGROUNDED'


def test_model_missing_requirement_cannot_be_filled_and_omitted_source_is_retained():
    doc=text_document(BRIEF+'\n관람시간은 확인 중입니다.')
    req,draft,_,_=extract(doc=doc)
    missing=[c for c in draft.criteria if c.criterion_id in draft.extraction.unresolved_criterion_ids]
    assert len(missing)==1 and missing[0].expected=='' and missing[0].details.origin=='retained_unresolved_source'
    bad=draft.model_copy(update={'criteria':draft.criteria[:-1],
        'extraction':draft.extraction.model_copy(update={'unresolved_criterion_ids':()})})
    with pytest.raises(AdapterFailure) as error:accept_role_output('A02',req,bad)
    assert error.value.code=='EXTRACTION_UNRESOLVED_SOURCE_OMITTED'
    reply=model_reply();reply['requirements'].append(requirement(5,'viewing_hours','확인 중',operator='manual',unresolved=True))
    _,partial,_,_=extract(reply,doc)
    rejected,=partial.extraction.rejected_candidates
    assert rejected.code=='A02_UNRESOLVED_VALUE_INVENTED'
    assert partial.criteria==draft.criteria


def test_model_supplied_missing_condition_keeps_its_semantic_field():
    doc=text_document('관람시간은 확인 중입니다.')
    reply={'requirements':[requirement(1,'viewing_hours','',operator='manual',unresolved=True)]}
    _,draft,_,_=extract(reply,doc)
    assert len(draft.criteria)==1 and draft.criteria[0].details.field=='viewing_hours'
    assert draft.criteria[0].details.origin=='model_structured'


def test_unresolved_unspecified_model_field_is_retained_as_a_note():
    source='관람시간은 확인 중입니다.'
    doc=text_document(source)
    reply={'requirements':[requirement(1,'unspecified','',operator='manual',unresolved=True,presentation=None)]}
    _,draft,_,_=extract(reply,doc,presentation=True)
    assert len(draft.criteria)==1
    note=draft.criteria[0]
    assert (note.details.target,note.details.field,note.kind,note.operator)==('unspecified','unspecified','unsupported','manual')
    assert note.source.quote==source and note.expected=='' and note.details.presentation is None
    assert draft.extraction.unresolved_criterion_ids==(note.criterion_id,)


def test_resolved_unspecified_field_with_concrete_target_still_fails():
    reply={'requirements':[requirement(1,'unspecified','김천김밥축제')]}
    with pytest.raises(AdapterFailure) as error:extract(reply)
    assert error.value.code=='A02_RESPONSE_INVALID'


def test_allowances_are_preserved_without_rewriting_the_models_expected_value():
    doc=text_document('행사명은 ‘숲의 기록’으로 표기한다.\n허용 범위: 줄바꿈과 공백 차이는 허용한다.')
    reply={'requirements':[requirement(1,'event_name','숲의 기록')]}
    _,draft,_,_=extract(reply,doc)
    c=draft.criteria[0]
    assert c.expected=='숲의 기록' and c.operator=='contains'
    assert c.details.qualifiers and c.details.qualifiers[0].quote in c.source.quote
    assert 'source_qualifier_retained_by_policy' in draft.warnings


@pytest.mark.parametrize('text,field,value,literal,normalized',[
    ('행사일: 2025년 11월 2일','event_date','2025 년 11 월 2 일','2025년 11월 2일','2025-11-02'),
    ('행사 시간: 12시부터 19시까지','viewing_hours','12 시부터 19 시까지','12시부터 19시까지',None),
    ('행사명: 수원의\n영화','event_name','수원의 영화','수원의\n영화',None),
])
def test_spacing_only_values_keep_the_unique_original_spelling_and_trace(text,field,value,literal,normalized):
    item=requirement(1,field,value,normalized_expected=normalized,
        presentation={'contract_version':'3.0.0','label':'검수 항목','value':value})
    item['source']['end_line']='input-text:L'+str(len(text.splitlines()))
    _,draft,calls,_=extract({'requirements':[item]},text_document(text),presentation=True)
    c=draft.criteria[0]
    assert len(calls)==1 and c.expected==c.details.expected_source.quote==literal
    assert c.details.normalized_expected==normalized and c.source.quote==text
    assert c.details.presentation.value==value
    assert 'expected_spacing_copied_from_source' in draft.warnings


@pytest.mark.parametrize('source,expected',[
    ('주차공간이 협소하므로 가급적 대중교통을 이용하라는 안내를 포함한다.','대중교통 이용 안내'),
    ('행사 연도: 2025','2026'),('주요 제목: 오늘무해!','오늘무해?'),
    ('주요 제목: FESTIVAL','festival'),('장소: 서울시 / 서울 시','서울  시'),
])
def test_source_spacing_support_never_accepts_paraphrases_changed_values_or_ambiguous_spellings(source,expected):
    with pytest.raises(AdapterFailure) as error:
        extract({'requirements':[requirement(1,'wording',expected)]},text_document(source))
    assert error.value.code=='A02_EXPECTED_UNGROUNDED'


def test_normalized_date_generation_cannot_bundle_a_weekday_or_time_into_expected():
    from jsonschema import Draft202012Validator
    from backend.agent_framework.a02_source_selection import index_sources
    from backend.agent_framework.a02_semantic import semantic_schema
    doc=text_document('행사일: 2025년 11월 2일 일요일 12시부터 19시까지')
    settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')
    index,_=index_sources(prepare_sources(doc,settings))
    validator=Draft202012Validator(semantic_schema(index))
    item=requirement(1,'event_date','2025 년 11 월 2 일',normalized_expected='2025-11-02')
    validator.validate({'requirements':[item]})
    item['expected']+=' 일요일'
    assert not validator.is_valid({'requirements':[item]})


@pytest.mark.parametrize('presentation',[False,True])
def test_old_prompt_records_remain_readable_but_unknown_and_other_policy_hashes_are_rejected(presentation):
    from backend.agent_framework.a02 import validate_trace
    from backend.agent_framework.a02_semantic import LEGACY_SEMANTIC_PROMPT_HASH,LEGACY_PRESENTATION_PROMPT_HASH
    reply=model_reply()
    if presentation:
        for item in reply['requirements']:
            item['presentation']={'contract_version':'3.0.0','label':'검수 항목','value':item['expected']}
    request,draft,_,_=extract(reply,presentation=presentation)
    old=LEGACY_PRESENTATION_PROMPT_HASH if presentation else LEGACY_SEMANTIC_PROMPT_HASH
    assert old!=draft.extraction.prompt_sha256
    historic=draft.model_copy(update={'extraction':draft.extraction.model_copy(update={'prompt_sha256':old})})
    validate_trace(request.document,historic)
    for wrong in ('0'*64,LEGACY_SEMANTIC_PROMPT_HASH if presentation else LEGACY_PRESENTATION_PROMPT_HASH):
        changed=draft.model_copy(update={'extraction':draft.extraction.model_copy(update={'prompt_sha256':wrong})})
        with pytest.raises(ValueError,match='EXTRACTION_PROMPT_MISMATCH'):validate_trace(request.document,changed)


def test_converted_docx_sections_are_separate_model_blocks_and_stored_as_one_text_source():
    from backend.agent_framework.a02 import validate_trace
    text=('전시 포스터 제작 의뢰서\n\n항목 | 표기 내용과 허용 범위\n'
        '1 | 전시명에 ‘수원의 영화’를 표기한다. / 허용 범위: 줄바꿈 차이는 허용한다.\n\n'
        '관람시간 - 확인 중\n관람시간은 확인 중입니다.\n')
    doc=text_document(text)
    item=requirement(1,'event_name','수원의 영화')
    item['source']={'start_line':'p2-text:L2','end_line':'p2-text:L2'}
    request,draft,calls,_=extract({'requirements':[item]},doc)
    sent=json.loads(calls[0]['messages'][1]['content'])['sources']
    assert [(s['block_id'],s['page'],[l['id'] for l in s['lines']]) for s in sent]==[
        ('p1-text',1,['p1-text:L1']),('p2-text',2,['p2-text:L1','p2-text:L2']),('p3-text',3,['p3-text:L1','p3-text:L2'])]
    enum=calls[0]['response_format']['json_schema']['schema']['$defs']['SourceRange']['properties']['start_line']['enum']
    assert enum==['p1-text:L1','p2-text:L1','p2-text:L2','p3-text:L1','p3-text:L2']
    first=draft.criteria[0]
    assert first.expected=='수원의 영화' and first.source.block_id=='input-text' and first.source.page==1
    assert first.source.quote=='1 | 전시명에 ‘수원의 영화’를 표기한다. / 허용 범위: 줄바꿈 차이는 허용한다.'
    assert [(s.block_id,s.page,s.text) for s in draft.extraction.sources]==[('input-text',1,text)]
    # The pending passage is retained from the same stored source by policy.
    assert any(c.source.block_id=='input-text' and '확인 중' in c.source.quote for c in draft.criteria[1:])
    validate_trace(request.document,draft)


def test_pdf_uses_the_same_semantic_contract_as_free_input():
    from tests.agent_framework.test_a02_extraction import document
    from backend.agent_framework.a02_source_selection import index_sources
    doc=document();settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')
    sources=prepare_sources(doc,settings);_,sent=index_sources(sources)
    line=next(line for source in sent for line in source['lines'] if '전시 장소는' in line['text'])
    item=requirement(1,'venue','열린문화공간 후소 전시실')
    item['source']={'start_line':line['id'],'end_line':line['id']}
    _,draft,_,_=extract({'requirements':[item]},doc)
    assert draft.extraction.input_kind=='pdf' and draft.extraction.policy=='a02-semantic-v3'
    assert draft.criteria[0].expected=='열린문화공간 후소 전시실' and draft.criteria[0].details.qualifiers


def test_recorded_pdf_protocol_preserves_model_values_with_verified_date_citation(monkeypatch):
    from pathlib import Path
    from tests.agent_framework.test_a02_extraction import document
    def forbidden(*a,**k):raise AssertionError('literal compiler must not produce semantic values')
    monkeypatch.setattr('backend.agent_framework.a02.compile_requirements',forbidden)
    fixture=json.loads((Path(__file__).parent/'fixtures/a02_semantic_pdf_response.json').read_text(encoding='utf-8'))
    doc=document();assert doc.reference.sha256==fixture['document_sha256']
    _,draft,_,_=extract(fixture['reply'],doc)
    assert len(draft.criteria)==13 and len(draft.extraction.unresolved_criterion_ids)==1
    date_item=next(c for c in draft.criteria if c.details.field=='start_date')
    assert date_item.expected==date_item.details.normalized_expected=='2026-05-14'
    assert date_item.details.expected_source.quote=='2026년 5월 14일'
    assert all(c.details.origin=='model_structured' for c in draft.criteria)


def test_recorded_festival_pdf_keeps_short_values_normalized_date_and_pending_rain_policy():
    from pathlib import Path
    from backend.agent_framework.a02_semantic import SemanticProviderReply,SemanticReply
    root=Path(__file__).resolve().parents[2]
    fixture=json.loads((Path(__file__).parent/'fixtures/a02_festival_semantic_response.json').read_text(encoding='utf-8'))
    raw=(root/'docs/poster-baselines/2026-11-03/workflow/client-inputs/FES-NEW072/축제_포스터_제작의뢰서_v0.2.pdf').read_bytes()
    assert sha256(raw).hexdigest()==fixture['document_sha256']
    context,_,_=sample_inputs();context=context.model_copy(update={'asset_version_id':None})
    ref=DocumentRef(file_id='recorded-festival',document_version_id=context.document_version_id,
        sha256=fixture['document_sha256'],media_type='application/pdf',page_count=2)
    doc=VerifiedDocument(context,ref,raw,2)
    settings=PDFExtractionConfiguration(mode='qwen',endpoint='https://recorded.invalid/v1/chat/completions',presentation=True)
    reply=SemanticProviderReply(SemanticReply.model_validate(fixture['reply']),fixture['response_sha256'])
    request=ExtractRequest(context=context,document=ref,synthetic=True)
    draft=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:reply),request)
    values={c.details.field:c.expected for c in draft.criteria}
    assert len(draft.criteria)==19 and values['event_name']=='2025 탄소중립 한마당 축제'
    assert values['event_date']=='2025년 11월 2일' and values['venue']=='고색역 상부공원 일원'
    assert values['viewing_hours']=='12시부터 19시까지' and 'end_date' not in values
    assert values['pixel_width']=='2382' and values['pixel_height']=='3368'
    dates=[c for c in draft.criteria if c.details.field=='event_date']
    assert dates[0].details.normalized_expected=='2025-11-02'
    pending=[c for c in draft.criteria if c.criterion_id in draft.extraction.unresolved_criterion_ids]
    assert len(pending)==1 and pending[0].expected=='' and pending[0].operator=='manual'
    assert '우천' in pending[0].source.quote and 'expected_spacing_copied_from_source' in draft.warnings
    assert draft.extraction.response_sha256==fixture['response_sha256']


@pytest.mark.parametrize('raw',[b'',b'  ',b'\xff',b'a\x00b',b'\xef\xbb\xbfhello'])
def test_invalid_free_input_is_rejected_before_model_call(raw):
    doc=text_document();doc=VerifiedDocument(doc.context,doc.reference.model_copy(update={'sha256':sha256(raw).hexdigest()}),raw,1)
    calls=[];settings=PDFExtractionConfiguration(mode='mock')
    with pytest.raises(AdapterFailure):RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:calls.append(1)).extract(ExtractRequest(context=doc.context,document=doc.reference))
    assert not calls
