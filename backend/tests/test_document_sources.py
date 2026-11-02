"""실제 실패 문서의 OCR 문단과 모의 추론으로 양식·근거 범위의 회귀를 검증한다."""
import json
from pathlib import Path

import pytest

from backend.app import requirements_pdf as pdf
from backend.app.document_conditions import field_for_label
from backend.tests.test_requirements_pdf import condition, document_bytes, paddle_text, paddle_stub

FIXTURES=json.loads((Path(__file__).parent/'fixtures/pdf_format_ocr.json').read_text())


@pytest.mark.parametrize('name,count', [('NEW103',9),('NEW114',8),('NEW052',8)])
def test_captured_card_ocr_keeps_all_ids_bodies_levels_and_context_roles(name,count):
    sources=pdf._sources(FIXTURES[name+'_spec_v1']['pages'])
    cards=[s for s in sources if s.get('requirement_id')]
    assert {s['requirement_id'] for s in cards}=={f'{name}-C{i:02d}' for i in range(1,count+1)}
    assert all(s.get('card') and not s.get('header_only') for s in cards)
    assert all('다음' in s['text'] or '납품' in s['text'] for s in cards)
    assert all(c['level']=='필수' and c['level_source']['source'] for c in cards if '다음' in c['text'])
    assert any(s['role']=='common_rule' for s in sources)
    assert any(s['role']=='note' for s in sources)
    assert not [s for s in sources if pdf._supplement_source(s)]


def test_suwon_comparison_principle_is_context_instead_of_an_uncovered_requirement():
    name=next(name for name in FIXTURES if '수원재즈' in name)
    sources=pdf._sources(FIXTURES[name]['pages'])
    assert len({s['requirement_id'] for s in sources if s.get('requirement_id')})==16
    assert len([s for s in sources if s['role']=='common_rule'])==1
    assert not [s for s in sources if pdf._supplement_source(s)]


def test_document_id_version_footer_is_not_a_requirement_table_row():
    sources=pdf._sources([{'page':1,'text':'브랜드 | 온결 또는 ONGYEOL\n\nOG-2026-AUT-001 | v1.0 | ONGYEOL / 01'},
                         {'page':2,'text':'IMG-01 | 브랜드 | 온결 또는 ONGYEOL 표기\n\nIMG-02 | 상품명 | 테스트 라떼'}])
    assert {s['requirement_id'] for s in sources if s.get('requirement_id')}=={'IMG-01','IMG-02'}
    assert sources[1]['role']=='metadata'


@pytest.mark.parametrize('text', [
    '아래 조건을 만족하는 포스터를 검수하는 테스트에 사용합니다. 관찰하여 역작성했습니다.',
    '작성근거: 사람 확정 정답 및 위반 사례는 포함하지 않습니다.',
    '문구 판정: 공백을 정규화하고 읽을 수 없는 문자는 추정하지 않습니다.',
    '문구 비교 원칙 날짜를 정규화하고 출연진의 연결을 유지해야 한다.',
    '확인 필요: 제목은 제45회이나 세부 문구는 제44회로 회차 충돌이 있습니다.',
])
def test_context_and_metadata_do_not_start_a_supplementary_condition_call(text):
    assert not pdf._supplement_source({'text':text})


def test_common_rule_and_conflict_note_are_preserved_without_extra_conditions(monkeypatch,paddle_stub):
    paddle_text(monkeypatch,'R01 필수 | 가격 | 5,900원\nR02 필수 | 상품명 | 테스트 라떼\n문구 비교 원칙: 공백 차이를 허용한다.\n확인 필요: 제목 제45회와 세부 제44회가 충돌한다.')
    calls=[]
    def chat(messages,*args):
        context=json.loads(messages[1]['content']);calls.append(context)
        assert len(context['common_rules'])==1 and len(context['document_notes'])==1
        assert all(s['role']=='requirement' for s in context['sources'])
        return {'conditions':[condition(level='필수'),condition(label='상품명',requirement='테스트 라떼',level='필수',source_id='p1-s2')]}
    monkeypatch.setattr(pdf,'_chat',chat)
    result=pdf.extract_pdf(document_bytes())
    assert len(calls)==1 and len(result['conditions'])==2
    assert all('공백 차이' in c['inspection'] for c in result['conditions'])
    assert all(any(s.get('role')=='common_rule' for s in c['source_references']) for c in result['conditions'])
    assert result['source_coverage']['common_rules']==result['source_coverage']['attached_common_rules']==1
    assert '제44회' in result['document_notes'][0]['text']
    assert any('충돌' in warning for warning in result['warnings'])


def test_card_orphan_or_missing_condition_still_fails(monkeypatch,paddle_stub):
    paddle_text(monkeypatch,'02 | 필수 문구 및 행사 정보\nCASE123-C01 | 행사명\nCASE123-C02 | 장소\n장소에 다음 문구를 포함해야 합니다: 테스트 공원')
    monkeypatch.setattr(pdf,'_chat',lambda *a:pytest.fail('orphan must fail before inference'))
    with pytest.raises(pdf.DocumentError,match='조건 카드의 ID와 본문'):
        pdf.extract_pdf(document_bytes())
    paddle_text(monkeypatch,'02 | 필수 문구 및 행사 정보\nCASE123-C01 | 행사명\n행사명에 다음 문구를 포함해야 합니다: 테스트 축제\nCASE123-C02 | 장소\n장소에 다음 문구를 포함해야 합니다: 테스트 공원')
    monkeypatch.setattr(pdf,'_chat',lambda *a:{'conditions':[condition(label='행사명',requirement='테스트 축제',source_id='p1-s2')]})
    with pytest.raises(pdf.DocumentError,match='일부 항목'):
        pdf.extract_pdf(document_bytes())


@pytest.mark.parametrize('label,field', [('전시명','title'),('행사 제목','title'),('전시 시작일','date'),('전시 종료일','date'),
    ('전시 장소','location'),('기념식 시간','time'),('박람회 시간','time'),('참가비','text'),('참여 대상','text'),('원본 가로·세로','dimensions')])
def test_card_field_names_use_the_matching_information_comparator(label,field):
    assert field_for_label(label)==field


def test_card_normalization_preserves_qualified_dates_and_separate_times():
    from backend.app.document_conditions import render_condition
    for label in ['전시 시작일','전시 종료일','기념식 시간','박람회 시간']:
        assert render_condition({'label':label,'requirement':'실제 값'}).startswith(label+':')
    source={'id':'p2-s1','page':2,'role':'requirement','requirement_id':'CASE1-C08','card':True,
            'label':'원본 가로·세로','condition_type':'information',
            'text':'CASE1-C08 | 원본 가로·세로\n납품 포스터의 원본 크기는 가로 2041 px, 세로 2041 px여야 합니다.'}
    raw=pdf.Condition.model_validate(condition(label='이미지',requirement='가로 2041 px, 세로 2041 px이어야 합니다.',
        values=['가로 2041 px','세로 2041 px'],type='visual',source_id='p2-s1'))
    normalized=pdf._normalize_card(raw,[source])
    result=pdf._merge_conditions([(normalized,[source])],[source])[0]
    assert result['requirement']=='가로 2041 x 세로 2041픽셀' and result['type']=='information'
    assert result['model_type']=='visual'


def test_note_only_output_is_filtered_but_unknown_hallucination_is_not_hidden():
    actual=[{'id':'p2-s1','page':2,'text':'CASE1-C06 | 인물 일러스트 | 휠체어를 탄 인물'}]
    note=[{'id':'p2-s2','page':2,'role':'note','text':'확인 필요: 상단 제목은 제45회이나 세부 문구는 제44회로 회차 충돌이 있습니다.'}]
    raw=pdf.Condition.model_validate(condition(label='상단 제목',requirement='제45회',source_id='p2-s1'))
    assert pdf._is_context_output(raw,actual,note)
    invented=raw.model_copy(update={'label':'입장료','requirement':'99,000원'})
    assert not pdf._is_context_output(invented,actual,note)
    with pytest.raises(pdf.DocumentError,match='근거의 연결'):
        pdf._normalize_card(invented,actual)


def test_version_in_footer_is_not_a_delivery_condition_but_explicit_required_version_is():
    footer=[{'id':'p1-s9','page':1,'role':'metadata','text':'OG-2026-AUT-001 | v1.0 | ONGYEOL / 01'}]
    actual=[{'id':'p1-s1','page':1,'role':'requirement','text':'브랜드 | 온결'}]
    version=pdf.Condition.model_validate(condition(label='문서 버전',requirement='v1.0'))
    assert pdf._is_context_output(version,actual,footer)
    actual[0]['text']='R01 필수 | 필수 문구 | v1.0을 표기한다.'
    assert not pdf._is_context_output(version,actual,footer)
