"""Real sample PDF bytes + mocked protocol only; no live Qwen or database."""
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from backend.agent_framework.a02 import (RegisteredPDFExtractor,QwenProvider,MockProvider,prepare_sources,read_sources,
    PROMPT_HASH,QWEN_PROMPT,QWEN_PROMPT_HASH)
from backend.agent_framework.a02_source_selection import index_sources,materialize_selection,selection_schema,SelectedProviderReply
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.adapters.synthetic import sample_inputs
from backend.agent_framework.composition import load_config,build_role
from backend.agent_framework.contracts import DocumentRef,ExtractRequest,PDFTextSource
from backend.agent_framework.files import VerifiedDocument
from backend.agent_framework.invocation import invoke_role,accept_role_output
from backend.agent_framework.ports import AdapterFailure

SAMPLES=Path(__file__).resolve().parents[2]/'docs/poster-baselines/2026-11-03/workflow/client-inputs'


def document(case='EXH-NEW004'):
    raw=next((SAMPLES/case).glob('*.pdf')).read_bytes()
    context,_,_=sample_inputs()
    context=context.model_copy(update={'asset_version_id':None})
    ref=DocumentRef(file_id='registered-pdf',document_version_id=context.document_version_id,
        sha256=sha256(raw).hexdigest(),page_count=2)
    return VerifiedDocument(context,ref,raw,2)


def request(doc):return ExtractRequest(context=doc.context,document=doc.reference)


def candidate(sources):
    source=sources[0]
    quote=next(line.strip() for line in source.text.splitlines() if '표기한다' in line)
    return {'text':quote,'kind':'text','operator':'contains','expected':quote.split()[0],
        'required':True,'unit':None,'block_id':source.block_id,'page':source.page,'quote':quote,'unresolved':False}


def raw_reply(item):return json.dumps({'candidates':[item]},ensure_ascii=False).encode()


def selection_reply(sources,item=None):
    item=item or candidate(sources)
    index,_=index_sources(sources)
    line=next(key for key,value in index.lines.items() if value.source.block_id==item['block_id']
        and item['quote'] in value.source.text[value.start:value.end])
    choice={key:item[key] for key in ('kind','operator','expected','required','unit','unresolved')}
    def reference(value):
        if value is None or value=='':return None
        pairs=[(a,b) for a,first in index.parts.items() for b,last in index.parts.items()
            if first.source.block_id==item['block_id']==last.source.block_id and first.start<=last.start
            and first.source.text[first.start:last.end]==value]
        a,b=pairs[0]
        return {'start':a,'end':b}
    choice.update(expected=reference(item['expected']),unit=reference(item['unit']))
    choice.update(start_line=line,end_line=line)
    return json.dumps({'candidates':[choice]},ensure_ascii=False).encode()


@pytest.mark.parametrize('case',('EXH-NEW004','CON-NEW043','FES-NEW072'))
def test_mock_uses_actual_pdf_only_with_explicit_unapproved_trace(case):
    doc=document(case);settings=PDFExtractionConfiguration(mode='mock')
    result=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings),request(doc))
    assert result.synthetic and result.extraction.mode=='mock'
    assert len(result.extraction.sources)==2 and result.extraction.unresolved_criterion_ids
    assert all(c.expected=='' and c.operator=='manual' and not c.human_added for c in result.criteria)
    for c in result.criteria:
        source=next(s for s in result.extraction.sources if s.block_id==c.source.block_id)
        assert c.source.quote in source.text and c.source.source_hash==sha256(c.source.quote.encode()).hexdigest()


@pytest.mark.parametrize('mutation,code',(
    ('quote','A02_SOURCE_MISMATCH'),('page','A02_SOURCE_MISMATCH'),('block','A02_SOURCE_MISMATCH'),
    ('text','A02_SOURCE_MISMATCH'),('expected','A02_EXPECTED_UNGROUNDED'),('unit','A02_UNIT_UNGROUNDED'),('actor','A02_RESPONSE_INVALID'),
    ('kind_as_unit','A02_UNIT_UNGROUNDED'),
    ('missing','A02_UNRESOLVED_VALUE_INVENTED')))
def test_rejects_fabricated_sources_values_and_model_owned_identity(mutation,code):
    doc=document();settings=PDFExtractionConfiguration(mode='mock');sources=prepare_sources(doc,settings)
    item=candidate(sources)
    if mutation=='quote':item.update(text='Fabricated quote',quote='Fabricated quote')
    if mutation=='page':item['page']=2
    if mutation=='block':item['block_id']='absent-block'
    if mutation=='text':item['text']='Uncited paraphrase'
    if mutation=='expected':item['expected']='not in PDF 999999'
    if mutation=='unit':item['unit']='invented-unit'
    if mutation=='kind_as_unit':item['unit']='text'
    if mutation=='actor':item['actor_id']='model-cannot-select-actor'
    if mutation=='missing':item['unresolved']=True
    adapter=RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:raw_reply(item))
    with pytest.raises(AdapterFailure) as error:invoke_role('A02',adapter,request(doc))
    assert error.value.code==code


def test_undecided_source_cannot_be_filled_even_when_provider_claims_resolved():
    doc=document();settings=PDFExtractionConfiguration(mode='mock');sources=prepare_sources(doc,settings)
    item=candidate(sources);source=sources[1]
    quote=next(line.strip() for line in source.text.splitlines() if '확인 중' in line)
    item.update(text=quote,quote=quote,expected=quote[0],block_id=source.block_id,page=2)
    with pytest.raises(AdapterFailure,match='PDF extraction') as error:
        RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:raw_reply(item)).extract(request(doc))
    assert error.value.code=='A02_UNRESOLVED_VALUE_INVENTED'


@pytest.mark.parametrize('raw', (b'{',b'{"candidates":[],"candidates":[]}',b'{"candidates":[]}',b'{"candidates":NaN}',b'x'*(256*1024+1)),
    ids=('invalid-json','duplicate-key','empty-candidates','nonfinite-json','oversized-response'))
def test_invalid_or_oversized_json_never_becomes_a_draft(raw):
    doc=document();adapter=RegisteredPDFExtractor(lambda _:doc,PDFExtractionConfiguration(mode='mock'),provider=lambda _:raw)
    with pytest.raises(AdapterFailure):adapter.extract(request(doc))


def test_trace_tampering_is_rechecked_at_common_role_boundary():
    doc=document();adapter=RegisteredPDFExtractor(lambda _:doc,PDFExtractionConfiguration(mode='mock'))
    result=adapter.extract(request(doc));source=result.extraction.sources[0]
    bad=source.model_copy(update={'text':'tampered'})
    result=result.model_copy(update={'extraction':result.extraction.model_copy(update={'sources':(bad,result.extraction.sources[1])})})
    with pytest.raises(AdapterFailure) as error:accept_role_output('A02',request(doc),result)
    assert error.value.code=='EXTRACTION_SOURCE_HASH_MISMATCH'


@pytest.mark.parametrize('kind,code',(('blank','PDF_TEXT_UNAVAILABLE'),('scan','PDF_TEXT_UNAVAILABLE'),('limit','PDF_TEXT_LIMIT_EXCEEDED'),('parser','PDF_PARSER_VERSION_MISMATCH')))
def test_unsupported_input_fails_before_provider(kind,code):
    doc=document();settings=PDFExtractionConfiguration(mode='mock');calls=[]
    if kind=='limit':settings=settings.model_copy(update={'max_text_chars':100})
    if kind=='parser':settings=settings.model_copy(update={'parser_version':'0.0.0'})
    if kind in ('blank','scan'):doc=built_document(doc,(None,) if kind=='blank' else (('',True),))
    with pytest.raises(AdapterFailure) as error:
        RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:calls.append(1)).extract(request(doc))
    assert error.value.code==code and not calls


def built_document(doc,pages):
    """One PDF page per entry: None is blank, otherwise (text, has_image)."""
    with pymupdf.open() as pdf:
        for entry in pages:
            page=pdf.new_page()
            if entry is None:continue
            text,image=entry
            if text:page.insert_text((30,40),text)
            if image:page.insert_image(pymupdf.Rect(20,60,120,160),stream=b'P6\n1 1\n255\n\xff\x00\x00')
        raw=pdf.tobytes()
    ref=doc.reference.model_copy(update={'sha256':sha256(raw).hexdigest(),'page_count':len(pages)})
    return VerifiedDocument(doc.context,ref,raw,len(pages))


def test_page_with_text_and_images_is_read_and_reported_instead_of_rejected():
    doc=built_document(document(),(('Title must be shown',False),('Logo must be used',True)))
    settings=PDFExtractionConfiguration(mode='mock')
    sources,image_pages=read_sources(doc,settings)
    assert [s.block_id for s in sources]==['p1-text','p2-text'] and image_pages==(2,)
    assert prepare_sources(doc,settings)==sources
    quote='Logo must be used'
    item=dict(text=quote,kind='unsupported',operator='manual',expected='',required=True,unit=None,
        block_id='p2-text',page=2,quote=quote,unresolved=False)
    draft=RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:raw_reply(item)).extract(request(doc))
    assert draft.criteria[0].source.page==2 and draft.criteria[0].source.quote==quote
    assert [w for w in draft.warnings if w.endswith('_embedded_images_not_read')]==['page_2_embedded_images_not_read']


def test_pdf_without_images_carries_no_image_warning():
    doc=document();adapter=RegisteredPDFExtractor(lambda _:doc,PDFExtractionConfiguration(mode='mock'))
    assert not [w for w in adapter.extract(request(doc)).warnings if w.endswith('_embedded_images_not_read')]


def qwen_settings():return PDFExtractionConfiguration(mode='qwen',structuring='literal_legacy',endpoint='https://qwen.invalid/v1/chat/completions')


def test_http_mock_transport_checks_one_live_protocol_response_without_real_model():
    doc=document();settings=qwen_settings();sources=prepare_sources(doc,settings);seen=[]
    def respond(req):
        seen.append(req);body=json.loads(req.content)
        assert req.headers['Authorization']=='Bearer test-key-not-real'
        assert body['model']=='Qwen/Qwen3.5-4B' and body['stream'] is False
        assert 'actor_id' not in body['messages'][1]['content']
        schema=body['response_format']['json_schema']['schema']
        assert body['messages'][0]['content']==QWEN_PROMPT
        sent=json.loads(body['messages'][1]['content'])['sources']
        line_ids=[line['id'] for source in sent for line in source['lines']]
        variants=schema['properties']['candidates']['items']['anyOf']
        assert list(dict.fromkeys(v['properties']['start_line']['const'] for v in variants))==line_ids
        lines={line['id']:line for source in sent for line in source['lines']}
        for variant in variants:
            properties=variant['properties']
            line=lines[properties['start_line']['const']]
            assert properties['end_line']['enum'][0]==line['id']
            if properties['kind']=={'const':'text'}:
                reference=properties['expected']['$ref']
                spans=schema['$defs'][reference.split('/')[-1]]['anyOf']
                ids=[part['id'] for part in line['parts']]
                assert [span['properties']['start']['const'] for span in spans]==ids
                for i,span in enumerate(spans):assert span['properties']['end']['enum']==ids[i:]
        return httpx.Response(200,json={'model':settings.model,'choices':[{'finish_reason':'stop','message':{'content':selection_reply(sources).decode()}}]})
    provider=QwenProvider(settings,api_key='test-key-not-real',transport=httpx.MockTransport(respond))
    result=invoke_role('A02',RegisteredPDFExtractor(lambda _:doc,settings,provider=provider),request(doc))
    assert len(seen)==1 and not result.synthetic and result.extraction.mode=='qwen'
    assert result.extraction.prompt_sha256==sha256(json.loads(seen[0].content)['messages'][0]['content'].encode()).hexdigest()==QWEN_PROMPT_HASH
    assert result.extraction.response_sha256==sha256(selection_reply(sources)).hexdigest()
    assert 'explicit_unresolved_source_retained_by_policy' in result.warnings
    assert result.extraction.policy=='a02-source-compile-v2' and result.extraction.unresolved_criterion_ids
    assert 'missing_values_require_client_clarification' in result.warnings
    assert 'test-key-not-real' not in result.model_dump_json()


@pytest.mark.parametrize('unit,code',[(None,None),('', 'A02_RESPONSE_INVALID'),(' ', 'A02_RESPONSE_INVALID')])
def test_absent_unit_is_null_and_invalid_blanks_are_never_repaired(unit,code):
    doc=document();settings=qwen_settings();sources=prepare_sources(doc,settings)
    item=json.loads(selection_reply(sources));item['candidates'][0]['unit']=unit;calls=[]
    def respond(req):
        calls.append(1)
        return httpx.Response(200,json={'model':settings.model,'choices':[{'finish_reason':'stop','message':{'content':json.dumps(item,ensure_ascii=False)}}]})
    provider=QwenProvider(settings,transport=httpx.MockTransport(respond))
    adapter=RegisteredPDFExtractor(lambda _:doc,settings,provider=provider)
    if code:
        with pytest.raises(AdapterFailure) as error:invoke_role('A02',adapter,request(doc))
        assert error.value.code==code
    else:
        assert invoke_role('A02',adapter,request(doc)).criteria[0].unit is None
    assert calls==[1]


@pytest.mark.parametrize('unit,accepted',[('픽셀',True),('px',False)])
def test_unit_requires_the_literal_source_spelling_not_a_normalized_alias(unit,accepted):
    doc=document();settings=PDFExtractionConfiguration(mode='mock');sources=prepare_sources(doc,settings)
    source,quote=next((source,line.strip()) for source in sources for line in source.text.splitlines() if '3000픽셀' in line)
    item=candidate(sources)
    item.update(text=quote,quote=quote,block_id=source.block_id,page=source.page,kind='number',operator='gte',expected='3000',unit=unit)
    adapter=RegisteredPDFExtractor(lambda _:doc,settings,provider=lambda _:raw_reply(item))
    if accepted:assert invoke_role('A02',adapter,request(doc)).criteria[0].unit=='픽셀'
    else:
        with pytest.raises(AdapterFailure) as error:invoke_role('A02',adapter,request(doc))
        assert error.value.code=='A02_UNIT_UNGROUNDED'


def test_saved_mock_fixture_retains_its_original_policy_fingerprint():
    path=SAMPLES.parents[0]/'extraction-demo/EXH-NEW004_mock.json'
    fixture=json.loads(path.read_text(encoding='utf-8'))
    assert fixture['extraction']['prompt_sha256']==PROMPT_HASH
    assert QWEN_PROMPT_HASH!=PROMPT_HASH


def source_block(text,page=1):
    return PDFTextSource(page=page,block_id=f'p{page}-text',text=text,text_sha256=sha256(text.encode()).hexdigest())


def source_choice(start='p1-text:L2',end='p1-text:L3',**values):
    return {'candidates':[{'start_line':start,'end_line':end,'kind':'unsupported','operator':'manual',
        'expected':None,'required':True,'unit':None,'unresolved':False,**values}]}


def test_selected_lines_preserve_original_spaces_crlf_and_empty_lines_without_model_quotes():
    text='표 머리글\r\n  가로 3000픽셀  \r\n\r\n허용 범위: 공백 유지  \r\n끝'
    sources=(source_block(text),);index,sent=index_sources(sources)
    assert [line['id'] for line in sent[0]['lines']]==['p1-text:L1','p1-text:L2','p1-text:L4','p1-text:L5']
    raw=materialize_selection(source_choice(end='p1-text:L4'),index)
    item=json.loads(raw)['candidates'][0]
    assert item['text']==item['quote']=='  가로 3000픽셀  \r\n\r\n허용 범위: 공백 유지  '
    assert item['quote'] in text and item['block_id']=='p1-text' and item['page']==1
    assert 'start_line' not in item and 'end_line' not in item


@pytest.mark.parametrize('start,end',[
    ('missing','p1-text:L2'),('p1-text:L2','missing'),
    ('p1-text:L2','p1-text:L1'),('p1-text:L1','p2-text:L1'),
])
def test_unknown_reversed_or_cross_page_ranges_are_never_repaired(start,end):
    index,_=index_sources((source_block('첫째\n둘째'),source_block('다른 페이지',2)))
    with pytest.raises(AdapterFailure) as error:materialize_selection(source_choice(start,end),index)
    assert error.value.code=='A02_SOURCE_MISMATCH'


def test_model_cannot_supply_a_rewritten_quote_or_an_oversized_range():
    from pydantic import ValidationError
    index,_=index_sources((source_block('제목\n원문\n허용 조건'),))
    with pytest.raises(ValidationError):materialize_selection(source_choice(quote='모델의 재작성'),index)
    index,_=index_sources((source_block('x'*4001),))
    with pytest.raises(AdapterFailure) as error:
        materialize_selection(source_choice('p1-text:L1','p1-text:L1'),index)
    assert error.value.code=='A02_SOURCE_MISMATCH'


def text_input_block(text):
    return PDFTextSource(page=1,block_id='input-text',text=text,text_sha256=sha256(text.encode()).hexdigest())


def test_multi_section_text_input_is_shown_as_blocks_but_resolves_to_the_one_stored_source():
    text='제목\n안내 문장\n\n항목 | 내용\n가로 | 3000픽셀\r\n\r\n\r\n관람시간 - 확인 중\n'
    source=text_input_block(text);index,sent=index_sources((source,))
    assert [(b['block_id'],b['page'],[l['id'] for l in b['lines']]) for b in sent]==[
        ('p1-text',1,['p1-text:L1','p1-text:L2']),
        ('p2-text',2,['p2-text:L1','p2-text:L2']),
        ('p3-text',3,['p3-text:L1'])]
    assert [l['text'] for b in sent for l in b['lines']]==['제목','안내 문장','항목 | 내용','가로 | 3000픽셀','관람시간 - 확인 중']
    assert all(line.source is source for line in index.lines.values())
    assert [text[line.start:line.end] for line in index.lines.values()]==[l['text'] for b in sent for l in b['lines']]
    part=index.parts['p2-text:L2:T3'];assert text[part.start:part.end]=='3000'
    # Selected excerpts keep the stored block, page and original bytes, even across sections.
    item=json.loads(materialize_selection(source_choice('p2-text:L2','p3-text:L1'),index))['candidates'][0]
    assert item['quote']=='가로 | 3000픽셀\r\n\r\n\r\n관람시간 - 확인 중' and item['block_id']=='input-text' and item['page']==1


@pytest.mark.parametrize('text',['제목\n원문\n허용 조건','\n\n제목\n원문\n\n\n','한 줄'])
def test_single_section_text_input_keeps_its_original_line_ids(text):
    _,sent=index_sources((text_input_block(text),))
    numbers=[n for n,line in enumerate(text.splitlines(),1) if line.strip()]
    assert [(b['block_id'],b['page']) for b in sent]==[('input-text',1)]
    assert [l['id'] for l in sent[0]['lines']]==[f'input-text:L{n}' for n in numbers]


def test_pdf_pages_with_blank_lines_are_never_split_into_sections():
    _,sent=index_sources((source_block('첫째\n\n둘째\n\n셋째'),))
    assert [(b['block_id'],[l['id'] for l in b['lines']]) for b in sent]==[('p1-text',['p1-text:L1','p1-text:L3','p1-text:L5'])]


def test_source_index_overflow_fails_without_silently_truncating():
    from backend.agent_framework.a02_source_selection import MAX_SOURCE_LINES
    with pytest.raises(AdapterFailure) as error:
        index_sources((source_block('line\n'*(MAX_SOURCE_LINES+1)),))
    assert error.value.code=='PDF_SOURCE_INDEX_LIMIT_EXCEEDED'


def test_source_part_overflow_fails_before_a_model_request():
    from backend.agent_framework.a02_source_selection import MAX_SOURCE_PARTS
    settings=qwen_settings();calls=[]
    provider=QwenProvider(settings,transport=httpx.MockTransport(lambda req:calls.append(req)))
    with pytest.raises(AdapterFailure) as error:provider((source_block('word '*(MAX_SOURCE_PARTS+1)),))
    assert error.value.code=='PDF_SOURCE_INDEX_LIMIT_EXCEEDED' and not calls


def test_generation_schema_binds_ordered_value_spans_to_the_first_source_line():
    from jsonschema import Draft202012Validator
    index,_=index_sources((source_block('제목\n가로 3000픽셀\n허용 범위'),source_block('다른 페이지',2)))
    schema=selection_schema(index);Draft202012Validator.check_schema(schema)
    validate=Draft202012Validator(schema)
    good=source_choice('p1-text:L2','p1-text:L3',kind='number',operator='equals',
        expected={'start':'p1-text:L2:T2','end':'p1-text:L2:T2'},unit={'start':'p1-text:L2:T3','end':'p1-text:L2:T3'})
    assert validate.is_valid(good)
    from copy import deepcopy
    for field,span in [('expected',{'start':'p1-text:L1:T1','end':'p1-text:L1:T1'}),
                       ('expected',{'start':'p1-text:L3:T1','end':'p1-text:L3:T1'}),
                       ('expected',{'start':'p1-text:L2:T3','end':'p1-text:L2:T2'}),
                       ('unit',{'start':'p2-text:L1:T1','end':'p2-text:L1:T1'})]:
        bad=deepcopy(good);bad['candidates'][0][field]=span
        assert not validate.is_valid(bad)
        with pytest.raises(AdapterFailure):materialize_selection(bad,index)
    bad=deepcopy(good);bad['candidates'][0]['end_line']='p2-text:L1'
    assert not validate.is_valid(bad)


def test_excessive_selection_schema_fails_without_a_partial_request(monkeypatch):
    import backend.agent_framework.a02_source_selection as selection
    monkeypatch.setattr(selection,'MAX_SELECTION_SCHEMA_BYTES',100)
    calls=[];provider=QwenProvider(qwen_settings(),transport=httpx.MockTransport(lambda req:calls.append(req)))
    with pytest.raises(AdapterFailure) as error:provider((source_block('가로 3000픽셀'),))
    assert error.value.code=='PDF_SOURCE_INDEX_LIMIT_EXCEEDED' and not calls


def test_generation_schema_keeps_unresolved_values_empty_and_numbers_separate_from_units():
    from jsonschema import Draft202012Validator
    from copy import deepcopy
    index,_=index_sources((source_block('가로 3000픽셀\n관람시간 확인 중'),))
    validator=Draft202012Validator(selection_schema(index))
    missing=source_choice('p1-text:L2','p1-text:L2',unresolved=True)
    assert validator.is_valid(missing)
    bad=deepcopy(missing);bad['candidates'][0]['expected']={'start':'p1-text:L2:T1','end':'p1-text:L2:T1'}
    assert not validator.is_valid(bad)
    missing['candidates'][0]['unresolved']=False
    assert not validator.is_valid(missing)
    number=source_choice('p1-text:L1','p1-text:L1',kind='number',operator='equals',
        expected={'start':'p1-text:L1:T2','end':'p1-text:L1:T2'},unit={'start':'p1-text:L1:T3','end':'p1-text:L1:T3'})
    assert validator.is_valid(number)
    number['candidates'][0]['expected']['end']='p1-text:L1:T3'
    assert not validator.is_valid(number)
    with pytest.raises(AdapterFailure) as error:materialize_selection(number,index)
    assert error.value.code=='A02_RESPONSE_INVALID'
    with pytest.raises(AdapterFailure) as error:materialize_selection(bad,index)
    assert error.value.code=='A02_UNRESOLVED_VALUE_INVENTED'


@pytest.mark.parametrize('field,code',[('expected','A02_EXPECTED_UNGROUNDED'),('unit','A02_UNIT_UNGROUNDED')])
@pytest.mark.parametrize('start,end',[
    ('missing','p1-text:L2:T1'),('p1-text:L2:T1','missing'),
    ('p1-text:L2:T2','p1-text:L2:T1'),
    ('p1-text:L1:T1','p1-text:L2:T1'),('p1-text:L2:T1','p1-text:L3:T1'),
    ('p2-text:L1:T1','p2-text:L1:T1'),
])
def test_selected_values_must_be_ordered_and_within_their_own_excerpt(field,code,start,end):
    index,_=index_sources((source_block('제목\n가로 3000픽셀\n다른 조건'),source_block('다른 페이지',2)))
    value=source_choice('p1-text:L2','p1-text:L2',**{field:{'start':start,'end':end}})
    with pytest.raises(AdapterFailure) as error:materialize_selection(value,index)
    assert error.value.code==code


def test_expected_and_unit_are_derived_from_parts_with_original_whitespace():
    index,sent=index_sources((source_block('제목\n장소: 열린문화공간  후소 전시실\n가로 3000픽셀'),))
    value=source_choice('p1-text:L2','p1-text:L2',kind='text',operator='contains',expected={'start':'p1-text:L2:T3','end':'p1-text:L2:T5'})
    assert json.loads(materialize_selection(value,index))['candidates'][0]['expected']=='열린문화공간  후소 전시실'
    value=source_choice('p1-text:L3','p1-text:L3',kind='number',operator='equals',
        expected={'start':'p1-text:L3:T2','end':'p1-text:L3:T2'},unit={'start':'p1-text:L3:T3','end':'p1-text:L3:T3'})
    item=json.loads(materialize_selection(value,index))['candidates'][0]
    assert item['expected']=='3000' and item['unit']=='픽셀'


@pytest.mark.parametrize('field',['expected','unit'])
def test_model_cannot_return_value_strings_instead_of_source_references(field):
    from pydantic import ValidationError
    index,_=index_sources((source_block('제목\n원문'),))
    with pytest.raises(ValidationError):
        materialize_selection(source_choice('p1-text:L2','p1-text:L2',**{field:'원문'}),index)


@pytest.mark.parametrize('failure,unknown,code',(
    ('connect',False,'QWEN_UNAVAILABLE'),('connect_timeout',False,'QWEN_UNAVAILABLE'),
    ('timeout',True,'QWEN_OUTCOME_UNKNOWN'),('server',True,'QWEN_OUTCOME_UNKNOWN'),
    ('rate',False,'QWEN_HTTP_REJECTED'),('redirect',False,'QWEN_HTTP_REJECTED'),
    ('incomplete',False,'QWEN_OUTPUT_INCOMPLETE'),('badjson',False,'A02_RESPONSE_INVALID')))
def test_http_failure_never_retries_repairs_or_falls_back(failure,unknown,code):
    calls=[];settings=qwen_settings();doc=document()
    def respond(req):
        calls.append(1)
        if failure=='connect':raise httpx.ConnectError('DO_NOT_LEAK_SECRET',request=req)
        if failure=='connect_timeout':raise httpx.ConnectTimeout('DO_NOT_LEAK_SECRET',request=req)
        if failure=='timeout':raise httpx.ReadTimeout('DO_NOT_LEAK_SECRET',request=req)
        if failure=='server':return httpx.Response(500,text='DO_NOT_LEAK_SECRET')
        if failure=='rate':return httpx.Response(429)
        if failure=='redirect':return httpx.Response(302,headers={'Location':'https://other.invalid'})
        if failure=='badjson':return httpx.Response(200,content=b'{')
        return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
    provider=QwenProvider(settings,transport=httpx.MockTransport(respond))
    with pytest.raises(AdapterFailure) as error:
        RegisteredPDFExtractor(lambda _:doc,settings,provider=provider).extract(request(doc))
    assert len(calls)==1 and error.value.code==code and error.value.outcome_unknown is unknown
    assert error.value.retryable is (failure in ('connect','connect_timeout','rate'))
    assert 'DO_NOT_LEAK_SECRET' not in str(error.value)


def test_registered_role_cannot_be_built_without_project_storage_context():
    cfg=load_config('examples/agent_framework/team_config.pdf-mock.json')
    with pytest.raises(AdapterFailure) as error:build_role(cfg,'A02')
    assert error.value.code=='ADAPTER_CONTEXT_REQUIRED'


def test_legacy_json_does_not_gain_null_trace_or_configuration_fields():
    from backend.agent_framework.adapters.synthetic import make_agents
    cfg=load_config('examples/agent_framework/team_config.synthetic.json')
    assert 'pdf_extraction' not in cfg.model_dump(mode='json')
    context,ref,_=sample_inputs()
    draft=make_agents().extractor.extract(ExtractRequest(context=context,document=ref))
    assert 'extraction' not in draft.model_dump(mode='json')


@pytest.mark.parametrize('values',(
    {'mode':'mock','endpoint':'https://unexpected.invalid/v1/chat/completions'},
    {'mode':'qwen'},
    {'mode':'qwen','endpoint':'https://key:secret@qwen.invalid/v1/chat/completions'},
    {'mode':'qwen','endpoint':'https://qwen.invalid/v1/chat/completions?api_key=secret'},
    {'mode':'qwen','endpoint':'file:///tmp/model'},
    {'mode':'qwen','endpoint':'https://qwen.invalid/v1/chat/completions','max_text_chars':60001},
    {'mode':'qwen','endpoint':'https://qwen.invalid/v1/chat/completions','timeout_seconds':901},
))
def test_model_mode_limits_and_endpoint_are_trusted_explicit_configuration(values):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):PDFExtractionConfiguration.model_validate(values)
