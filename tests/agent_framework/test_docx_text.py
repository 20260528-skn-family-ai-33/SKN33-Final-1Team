"""DOCX -> requirement text conversion. In-memory packages only; no model or DB."""
from hashlib import sha256
import io
import zipfile

import pytest

from backend.agent_framework.docx_text import (CONVERTER,DOCX_MEDIA_TYPE,MAIN_CONTENT_TYPE,MAX_XML_BYTES,
    ConvertedDocument,describe_source,docx_filename,docx_requirement_text)
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.requirement_text import MAX_TEXT_CHARACTERS,inspect_text

NS=('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')


def types(content_type=MAIN_CONTENT_TYPE):
    return ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{content_type}"/></Types>')


def package(body,*,content_type=MAIN_CONTENT_TYPE,document=None,extra=()):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml',types(content_type))
        archive.writestr('word/document.xml',document if document is not None else
            f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{body}<w:sectPr/></w:body></w:document>')
        for name,data in extra:archive.writestr(name,data)
    return out.getvalue()


def p(*runs):
    return '<w:p>'+''.join(runs)+'</w:p>'


def r(text):
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def table(*rows):
    return '<w:tbl><w:tblPr/>'+''.join('<w:tr>'+''.join(f'<w:tc><w:tcPr/>{cell}</w:tc>' for cell in row)+'</w:tr>' for row in rows)+'</w:tbl>'


def code(raw):
    with pytest.raises(AdapterFailure) as error:docx_requirement_text(raw)
    return error.value.code


def test_body_order_paragraph_groups_and_table_rows_are_blank_line_blocks():
    raw=package(p(r('전시 포스터 제작 의뢰서'))+p(r('행사명은 '),r('빛의 기록'),r('으로 표기한다.'))
        +table([p(r('항목')),p(r('요구 내용'))],[p(r('장소')),p(r('서울 문화관'))+p(r('2층'))])+p(r('끝.')))
    converted=docx_requirement_text(raw)
    assert converted.text==('전시 포스터 제작 의뢰서\n행사명은 빛의 기록으로 표기한다.\n\n'
        '항목 | 요구 내용\n장소 | 서울 문화관 / 2층\n\n끝.\n')
    assert (converted.blocks,converted.paragraphs,converted.table_rows)==(3,3,2)
    assert inspect_text(converted.text.encode())==1


def test_accepted_view_skips_deleted_hidden_field_code_and_fallback_duplicates():
    hidden='<w:r><w:rPr><w:vanish/></w:rPr><w:t>숨김</w:t></w:r>'
    visible_false='<w:r><w:rPr><w:vanish w:val="false"/></w:rPr><w:t>보임</w:t></w:r>'
    deleted='<w:del w:id="1" w:author="a"><w:r><w:delText>삭제</w:delText></w:r></w:del>'
    inserted='<w:ins w:id="2" w:author="a"><w:r><w:t>추가</w:t></w:r></w:ins>'
    field='<w:r><w:instrText> PAGE </w:instrText></w:r>'
    alternate=('<mc:AlternateContent><mc:Choice Requires="wps"><w:r><w:t>선택</w:t></w:r></mc:Choice>'
        '<mc:Fallback><w:r><w:t>대체</w:t></w:r></mc:Fallback></mc:AlternateContent>')
    converted=docx_requirement_text(package(p(r('A'),hidden,visible_false,deleted,inserted,field,alternate)))
    assert converted.text=='A보임추가선택\n'


def test_breaks_tabs_text_boxes_and_content_controls_keep_their_text_once():
    box=('<w:r><w:drawing><w:txbxContent>'+p(r('상자 문구'))+'</w:txbxContent></w:drawing></w:r>')
    control='<w:sdt><w:sdtPr/><w:sdtContent>'+p(r('통제 문단'))+'</w:sdtContent></w:sdt>'
    raw=package(p(r('첫 줄'),'<w:r><w:br/></w:r>',r('둘째\t'),'<w:r><w:tab/></w:r>',r('끝'),box)+control)
    assert docx_requirement_text(raw).text=='첫 줄\n둘째\t\t끝\n상자 문구\n통제 문단\n'


def test_nested_table_rows_stay_inside_their_outer_cell_without_duplication():
    inner=table([p(r('가')),p(r('나'))])
    raw=package(table([p(r('바깥'))+inner,p(r('옆'))]))
    assert docx_requirement_text(raw).text=='바깥 / 가 | 나 | 옆\n'


@pytest.mark.parametrize('raw,expected',[
    (b'%PDF-1.7','DOCX_INVALID'),
    (b'PK\x03\x04broken','DOCX_INVALID'),
    (package(p(r('x')),content_type='application/vnd.ms-word.document.macroEnabled.main+xml'),'DOCX_INVALID'),
    (package(p(r('x')),content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml'),'DOCX_INVALID'),
    (package('',document='<x/>'),'DOCX_INVALID'),
    (package('',document='<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e "x">]><d>&e;</d>'),'DOCX_INVALID'),
    (package(p('')+table([p('')])),'DOCX_TEXT_UNAVAILABLE'),
])
def test_invalid_template_macro_entity_and_empty_documents_are_rejected(raw,expected):
    assert code(raw)==expected


def test_body_size_text_size_and_depth_limits_are_enforced_before_registration():
    oversized=package('',document=f'<w:document {NS}><w:body>'+' '*(MAX_XML_BYTES+1)+'</w:body></w:document>')
    assert code(oversized)=='DOCX_TOO_LARGE'
    long=package(''.join(p(r('가'*1000)) for _ in range(MAX_TEXT_CHARACTERS//1000+1)))
    assert code(long)=='DOCX_TEXT_TOO_LARGE'
    deep=package('<w:sdt><w:sdtContent>'*300+p(r('x'))+'</w:sdtContent></w:sdt>'*300)
    assert code(deep)=='DOCX_TOO_COMPLEX'


def test_source_description_hashes_the_uploaded_docx_not_the_converted_text():
    raw=package(p(r('행사명: 새벽')));converted=docx_requirement_text(raw)
    source=describe_source(raw,'의뢰서.docx',converted)
    assert source.sha256==sha256(raw).hexdigest()!=sha256(converted.text.encode()).hexdigest()
    assert (source.media_type,source.converter,source.characters)==(DOCX_MEDIA_TYPE,CONVERTER,len(converted.text))
    assert 'source' in ConvertedDocument.model_fields


@pytest.mark.parametrize('name',['의뢰서.DOCX','a b.docx','é.docx'])
def test_docx_names_are_normalized_and_accept_case_insensitive_suffix(name):
    assert docx_filename(name).lower().endswith('.docx')


@pytest.mark.parametrize('name',[None,'','.docx',' a.docx','a.doc','a.pdf','dir/a.docx','a\\b.docx','a\x00.docx','x'*250+'.docx'])
def test_invalid_docx_names_are_rejected(name):
    with pytest.raises(AdapterFailure) as error:docx_filename(name)
    assert error.value.code=='DOCX_FILENAME_INVALID'
