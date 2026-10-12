#!/usr/bin/env python3
"""Build the offline A4 collection report from current evidence; stdlib only."""
from __future__ import annotations
import base64
import csv
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "data/collection"
OUT = ROOT / "docs/reports/data-collection"
TEMPLATE = "https://docs.google.com/document/d/1JkpF21eSmv-509OQK14vYRk5wqiu5yHr/edit"
GITHUB = "https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team"
FIELD_MAP = []
PAGES = []


def esc(value):
    return html.escape(str(value), quote=True)


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def paragraph(text):
    return f"<p>{text}</p>"


def table(headers, rows, klass=""):
    return '<table class="' + klass + '"><thead><tr>' + ''.join('<th>'+str(x)+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+str(x)+'</td>' for x in row)+'</tr>' for row in rows) + '</tbody></table>'


def field(key, label, body, sources):
    FIELD_MAP.append({"field_id":key, "official_label":label, "page":len(PAGES)+1,
                      "html_id":"f-"+key.replace('.', '-'), "evidence":sources,
                      "present":True})
    return f'<section class="field" id="f-{key.replace(".", "-")}" data-template-field="{key}"><h3>{label}</h3>{body}</section>'


def page(title, body, klass=""):
    index=len(PAGES)+1
    PAGES.append(f'<article class="page {klass}" id="page-{index}"><div class="content"><div class="running">SKN33 1팀 · 수집 데이터 보고서</div><h2>{title}</h2>{body}</div><footer><span>{title}</span><span>{index:02d} / 11</span></footer></article>')


def main():
    FIELD_MAP.clear()
    PAGES.clear()
    summary=load(BASE/'summary.json')
    spec=load(BASE/'case-specs.json')
    qa=load(BASE/'evidence/quality-review.json')
    docreview=load(BASE/'evidence/document-review.json')
    requests=jsonl(BASE/'raw/synthetic/requests/requests.jsonl')
    imagelog=jsonl(BASE/'evidence/image-production.jsonl')
    docslog=jsonl(BASE/'evidence/document-production.jsonl')
    inventory=[]
    if (BASE/'inventory.csv').exists():
        with (BASE/'inventory.csv').open(encoding='utf-8-sig', newline='') as stream:
            inventory=list(csv.DictReader(stream))
    def count(key): return esc(summary.get(key, '집계 대기'))
    asof=summary.get('as_of_date','2026-10-06')
    due=summary.get('submission_date','2026-10-08')
    metrics=qa.get('metrics',{})
    qs=qa.get('summary',{})
    inventory_current=all((ROOT/r.get('path','')).is_file() and hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()==r.get('sha256') for r in inventory)
    freshness=bool(summary and inventory and inventory_current and metrics.get('new_images')==summary.get('new_images') and metrics.get('documents')==summary.get('new_documents'))
    qa_label={'passed':'기본 점검 통과','failed':'실패 항목 있음','pending':'추가 확인 대기'}.get(qa.get('overall_status'),'검증 대기') if freshness else '수집 중간 기록 · 최종 재검증 대기'
    # Keep all private provider result paths out of the export.
    evidence_paths=['data/collection/inventory.csv','data/collection/summary.json',
                    'data/collection/evidence/quality-review.json']
    body='<div class="cover-title"><span class="pill">데이터 수집 및 저장</span><h1>수집 데이터<br>보고서</h1><p class="subtitle">광고 납품 검수 · 합성 개발 자료 추가 수집</p></div>'
    cover=[('cohort','기수','SKN33 33기'),('team','팀','1팀'),('stage','단계','데이터 수집 및 저장'),
           ('submitted','제출일',esc(due)+' 제출 예정 · '+esc(asof)+' 작성 스냅샷'),
           ('github','GitHub',f'<a href="{GITHUB}">{GITHUB}</a>'),
           ('authors','작성 팀원','SKN33 1팀 · AI 작성 보조(실명 미확인)')]
    for key,label,value in cover: body+=field('cover.'+key,label,paragraph(value),['summary.json','프로젝트 지정 정보'])
    body+='<div class="notice"><b>현재 자료의 성격</b><br>추가분은 자체 제작한 합성 자료이다. 실제 팀 보유 자료는 미제공·확인 대기 상태이며, 팀에 자료가 없다고 판단한 것은 아니다. 사람 승인과 권리 검토는 미실행이다.</div>'
    page('표지',body,'cover')

    body=field('overview.name','프로젝트명',paragraph('다중 에이전트 협업 기반 광고 납품 검수 시스템 · SKN33 1팀'),['case-specs.json'])
    body+=field('overview.target','수집 대상',table(['유형','추가분','단위·관계'],[
        ['기준문서',count('new_documents')+'개','3브랜드 × 3종 + 공통 FAQ 1개'],
        ['업무 요청',count('new_requests')+'건','1개 JSONL · '+count('request_groups')+'상황 그룹 × 5표현'],
        ['광고 이미지',count('new_images')+'개',count('new_image_groups')+'신규 합성 원본 그룹 · 편집본 포함'],
        ['기존 이미지',count('existing_images')+'개',count('existing_image_groups')+'원본 그룹 · 추가분에서 제외']]),evidence_paths)
    body+=field('overview.purpose','수집 목적',paragraph('발주 조건을 참조하는 검수 흐름, 요청 의도 분류, 광고 문자·조건 비교, 근거 누락 시 추가 자료 요청 기능을 개발·시연하기 위해 자료를 확보한다. 독립 일반화 성능을 증명하는 수집은 아니다.'),['case-specs.json'])
    body+=field('overview.functions','활용 기능',table(['자료','연결 기능'],[
        ['의뢰서·가이드·체크리스트','기준 검색·참조(RAG)와 항목별 조건 비교'],['요청 문장','신규 검수·재검수·기준 변경·상태 조회·기타의 라우팅 개발'],['광고 원본·편집본','OCR·시각 확인, 불일치·모호·증빙부족 대응 개발']]),['case-specs.json','raw/synthetic/'])
    body+=field('overview.source','데이터 출처 및 저작권',paragraph('문서와 요청은 AI 작성 초안을 Python으로 구성·저장했다. 이미지는 내장 image_gen으로 원본 3개를 별도 생성하고 같은 그룹 내에서 편집했다. 외부 문서·웹 크롤링 자료는 추가 수집하지 않았다. 브랜드 및 제작물 권리는 미검토이며 프로젝트 내부 개발용 사용 조건만 기록한다.'),['evidence/document-production.jsonl','evidence/request-production.jsonl','evidence/image-production.jsonl'])
    body+='<div class="notice">실제 팀 자료 접수 계획: 2026-10-07 12:00 KST까지. 접수·권리 확인·재집계는 현재 완료되지 않았다.</div>'
    page('1. 데이터 수집 개요',body)

    body=field('method.mode','수집 방식',paragraph('☐ 웹 크롤링　☐ 외부 API　☐ 파일 업로드 완료　☐ 수작업 외부 수집　<b>☑ 기타: 자체 합성 제작</b>')+paragraph('실제 팀 파일 업로드는 향후 접수 계획이다. 현재 추가분을 외부 실무 수집 자료로 분류하지 않는다.'),['evidence/*-production.jsonl','README-team-intake.md'])
    body+=field('method.tools','사용 언어 및 라이브러리',table(['도구','실제 수행','제한'],[
        ['Python 표준 라이브러리','문서·요청 저장, JSON/CSV, 해시·집계','생성 스크립트의 모델 API 직접 호출 없음'],
        ['Pillow','이미지 파일 열기·규격 점검','원본 광고 수정 없음'],['내장 image_gen','별도 원본 생성 및 그룹 내 이미지 편집','모델 revision은 도구가 미제공'],['AI 작성 보조','규칙·업무 상황 및 표현 초안 작성','정확한 모델 버전 미확인']]),['scripts/generate_documents.py','scripts/generate_requests.py','evidence/image-production.jsonl','scripts/validate_collection.py'])
    body+=field('method.cycle','자동화 여부 및 수집 주기',paragraph('이번 수집은 2026-10-06에 실행한 일회성 합성 제작이다. 문서·요청은 재실행 가능한 Python 스크립트로 저장하며 이미지 제작은 프롬프트·부모 원본·생성 기록을 보존한다. 주기적 자동 수집과 외부 API 연동은 운영하지 않는다.'),['evidence/*-production.jsonl'])
    body+=field('method.exceptions','예외 처리',paragraph('파일 손상·JSON 파싱 오류·빈 필드·중복 ID·누락 시나리오·원본 연결 오류·해시 불일치는 검증 기록에 실패 또는 대기로 남긴다. 제작 의도와 실제 관찰을 구분하고, 가독성·적용 범위가 모호하거나 제작 근거가 누락되면 사람 검토 대기로 유지한다.'),['scripts/validate_collection.py','evidence/quality-review.json'])
    body+=field('method.flow','수집 스크립트 및 흐름',paragraph('공유 조건 → 문서·요청 생성 / image_gen 원본·편집 → 출처 로그 → 파일 inventory·summary → 품질 검증 → HTML/PDF 보고서.')+'<pre>python3 scripts/generate_documents.py\npython3 scripts/generate_requests.py\npython3 data/collection/scripts/build_collection.py\npython3 data/collection/scripts/validate_collection.py</pre>'+paragraph('이미지 생성은 Python 스크립트가 재호출하지 않는다. 같은 프롬프트로 재생성해도 동일 픽셀을 보장하지 않는다. 생성 로그의 시각은 도구 결과 등록 또는 Python 조립·저장 시각이며, 미노출 제공자 생성시각은 단정하지 않는다.'),['scripts/','evidence/prompts/','evidence/*-production.jsonl'])
    page('2. 데이터 수집 방법',body)

    dict_rows=[
        ['Markdown 문서','본문 / 제목','text','발주 조건·가이드·체크리스트','FABLE CUP / 25% 할인'],
        ['문서 제작 로그','document_id / group_id','string','문서와 원본 그룹 연결','SYN-DOC-01-BRIEF / SYN-AD-01'],
        ['문서 제작 로그','path / sha256 / bytes','string / string / int','원문 위치·무결성·크기','raw/synthetic/documents/…'],
        ['요청 원자료','request_id / text','string','요청 ID와 실제 문장','REQ-0001 / 광고 검사 요청'],
        ['요청 원자료','request_group_id','string','같은 업무 상황의 표현 변형 그룹','REQ-G-001'],
        ['요청 원자료','case_group_id / project_id','string','광고 원본 그룹·가상 발주 연결','SYN-AD-01 / SYN-ORDER-01'],
        ['요청 원자료','synthetic / source_method','bool / string','합성 표시·작성 방법','true / AI 작성 + Python'],
        ['요청 원자료','created_at','ISO datetime','저장·생성 기록 시각','2026-10-06…+09:00'],
        ['별도 라벨 증빙','intent_label / human_review_status','string','5종 의도의 AI 초안과 사람 상태','new_inspection / pending'],
        ['이미지 원자료','PNG pixels','RGB/RGBA bitmap','광고의 실제 시각 내용',count('image_dimensions')+' px'],
        ['이미지 제작 로그','asset_id / parent_asset_id','string / nullable','신규 원본·편집본 관계','SYN-AD-01-mismatch → normal'],
        ['이미지 제작 로그','prompt_path / operation','string','생성 근거·동작','evidence/prompts/… / edit'],
        ['파일 inventory','path / asset_type / sha256','string','파일 단위 분류·위치·해시','inventory.csv의 실제 행'],
    ]
    body=field('structure.dictionary','파일·필드명·타입·설명·예시',table(['파일','필드','타입','설명','예시'],dict_rows,'dictionary'),['raw/synthetic/','inventory.csv','evidence/*-production.jsonl'])
    intent_counts=summary.get('request_intent_counts',{})
    intent_names={'new_inspection':'신규 검수','reinspection':'재검수','criteria_change':'기준 변경','status_query':'상태 조회','other':'기타'}
    if intent_counts:
        body+='<p class="small">AI 초안 의도 구성: '+ ' · '.join(esc(intent_names.get(k,k))+' '+esc(v)+'건' for k,v in intent_counts.items())+'. 모든 라벨은 사람 검토 대기이다.</p>'
    body+='<div class="notice">요청 원문에는 정답 의도를 내장하지 않는다. AI 초안 라벨은 <code>evidence/request-labels.jsonl</code>로 분리한다. 문서는 판단 규칙이며 이미지별 정답표가 아니다.</div>'
    body+='<p class="small">필드사전의 파일 경로는 프로젝트 기준 상대경로이다. 자세한 파일별 크기·해시·출처·검토 상태는 inventory와 생성 로그에서 연결한다.</p>'
    page('3. 데이터 설명 및 구성 · 필드사전',body)

    bytes_map=summary.get('by_type_bytes',{})
    byte_table=table(['집계 유형','실측 bytes'],[(esc(k),f'{v:,}' if isinstance(v,int) else esc(v)) for k,v in bytes_map.items()]) if bytes_map else paragraph('유형별 바이트 집계 대기.')
    body=field('structure.total','전체 데이터 건수',paragraph(f'원자료 파일은 신규 {count("new_raw_files")}개 + 기존 {count("existing_raw_files")}개 = 총 <b>{count("total_raw_files")}개</b>이다. 신규 요청 {count("new_requests")}건은 JSONL 1파일이며, 파일 수와 레코드 수를 합산하지 않는다. 제작 로그·관찰·라벨은 보조 증빙으로 별도 관리한다. inventory는 현재 {len(inventory)}행이다.')+byte_table,['inventory.csv','summary.json'])
    body+=field('structure.filtered','필터링 후 고품질 데이터',paragraph('AI 기본 점검과 사람이 확정한 고품질 자료를 구분한다. 현재 사람 승인 자료는 <b>'+count('human_approved_count')+'개</b>이며 학습·평가 준비 완료 수량으로 집계하지 않는다. 품질 점검 통과 건수는 5절의 검증 기록이다.'),['summary.json','evidence/quality-review.json'])
    storage=[['문서','data/collection/raw/synthetic/documents/','Markdown · UTF-8'],['요청','data/collection/raw/synthetic/requests/requests.jsonl','JSONL · UTF-8'],['신규 이미지','data/collection/raw/synthetic/images/','PNG · '+count('image_dimensions')+' px'],['기존 이미지','data/samples/','PNG · 별도 기존분'],['제작·검토 근거','data/collection/evidence/','JSON / JSONL / TXT · UTF-8'],['파일별 목록·집계','data/collection/inventory.csv / summary.json','CSV / JSON · UTF-8']]
    body+=field('structure.storage','저장 경로·포맷·인코딩',table(['대상','프로젝트 상대경로','형식'],storage,'paths'),['inventory.csv','summary.json'])
    samples=[]
    for row in inventory[:3]:
        path=row.get('path') or row.get('relative_path') or '경로 필드 확인 대기'
        digest=row.get('sha256','해시 확인 대기')
        samples.append([esc(path),'<code>'+esc(digest)+'</code>'])
    body+='<h3>무결성 표본과 패키지 연결</h3>'+(table(['원자료 상대경로','SHA-256'],samples,'hashes') if samples else paragraph('inventory 작성 후 SHA-256 표본을 표시한다.'))
    page('3. 데이터 설명 및 구성 · 현황·저장',body)

    body=field('legal.privacy','개인정보 포함 여부',paragraph('합성 문서·요청의 자동 패턴 점검과 AI 표본 확인에서 개인정보를 발견하지 않았다. 자동 점검은 개인정보 부재를 보증하지 않으며 사람 확인은 미완료이다. 팀 보유 실무 자료는 아직 제공되지 않아 개인정보·권리 상태를 판단하지 않았다.'),['evidence/quality-review.json','README-team-intake.md'])
    body+=field('legal.deidentify','비식별 처리',paragraph('현재 합성 원자료에는 실제 고객 이름·연락처를 넣지 않았다. 실제 팀 자료 접수 시 원문을 비공개 보관하고 작업 사본에서 불필요한 이름·전화·메일을 제거한 뒤 변환 근거를 남길 계획이다. 실제 자료의 비식별 실행을 완료했다고 표시하지 않는다.'),['raw/synthetic/','README-team-intake.md'])
    body+=field('legal.access','공개 및 내부 자료 여부',paragraph('추가분은 프로젝트 내부 개발·시연을 위한 합성 자료이다. 보고서·원문·생성 이미지의 공개 재배포나 외부 제출·공유 작업은 이 제작 범위에서 수행하지 않았다. 실제 팀 원문은 <code>.runtime/collection-team-input/</code>에서 비공개로 접수하며 Git 포함을 금지한다.'),['README-team-intake.md','evidence/*-production.jsonl'])
    body+=field('legal.license','라이선스·사용 조건 확인',paragraph('브랜드 이름과 생성 이미지의 권리, 외부 재배포·상업 사용 조건은 사람에게 검토되지 않았다. 합성 생성 또는 AI 점검을 공개 라이선스·사용 허가로 해석하지 않는다. 권리 상태는 <b>미검토(not_reviewed)</b>이고 내부 개발용 제한을 기록했다.'),['evidence/document-production.jsonl','evidence/image-production.jsonl'])
    body+=field('legal.reviewer','검토자',paragraph('AI 문서 작성·후속 검토: report_requirements 역할. 작성자와 분리한 AI 품질 검토: quality_evidence 역할. 실제 검토자의 실명 및 사람 승인 담당자는 미확인이다. AI 역할 식별은 사람의 서명·승인을 대체하지 않는다.'),['evidence/document-review.json','evidence/quality-review.json'])
    body+=field('legal.date','검토 일자',paragraph(esc(asof)+' AI 점검 스냅샷. 사람 개인정보·권리 검토 일자: 미실행. 실제 증빙의 실행시각을 보존하며 승인 일자를 임의로 채우지 않는다.'),['evidence/quality-review.json'])
    page('4. 법적·윤리적 고려사항',body)

    body='<div class="notice"><b>검증 상태: '+esc(qa_label)+'</b><br>통과 '+esc(qs.get('passed',0 if qa else '대기'))+' · 실패 '+esc(qs.get('failed',0 if qa else '대기'))+' · 대기 '+esc(qs.get('pending',0 if qa else '대기'))+'개 점검. 이는 검사 항목 수이며 고품질 자료 수가 아니다.</div>'
    body+=table(['추가 목표','실제 집계','판정 기준'],[['기준문서 10개',count('new_documents')+'개','유형·조건·출처·해시'],['요청 250건 / 50그룹',count('new_requests')+'건 / '+count('request_groups')+'그룹','유일 ID·문장·그룹·라벨 연결'],['이미지 12개 / 신규 원본 3그룹',count('new_images')+'개 / '+count('new_image_groups')+'그룹','정상·불일치·모호·증빙부족 의도'],['사람 확정 고품질 자료',count('human_approved_count')+'개','사람 승인 미실행']])
    body+=field('quality.duplicates','중복 검사',paragraph('요청 ID·문장의 유일성과 파일 SHA-256의 동일 바이트 중복을 점검한다. 같은 상황의 표현 변형 5개는 독립 상황으로 세지 않는다. 정확 해시 중복이 없어도 지각적 유사성이나 내용 다양성이 충분하다고 결론 내리지 않는다.'),['evidence/quality-review.json'])
    body+=field('quality.consistency','정합성 검사',paragraph('문서 브랜드·상품·할인·가상 표시와 공유 조건을 대조한다. 요청의 50상황 그룹·250표현 및 별도 라벨 연결, 이미지 원본·편집 관계와 제작 로그 해시를 확인한다. 제작 의도·AI 관찰·사람 확인을 구분한다.'),['case-specs.json','evidence/quality-review.json'])
    body+=field('quality.missing','결측 및 오류 처리',paragraph('필수 ID·텍스트·경로·출처·해시·원본 연결 누락과 파일 파싱 오류를 기록한다. 증빙부족은 3개 제출 패키지에서 제작 근거 제출을 의도적으로 생략한 통제 시나리오이다. 전체 이미지 제작 로그는 보존되며, 광고 픽셀 오류나 전역 로그 유실을 뜻하지 않는다. 사람 승인·권리 확인은 미완료로 유지한다.'),['evidence/quality-review.json','evidence/image-observations.json','evidence/submission-packages.jsonl'])
    body+=field('quality.standardization','표준화 방식',paragraph('문서·요청·증빙은 UTF-8, JSON/JSONL/CSV 및 안정적인 자료 ID로 관리한다. ISO 시각의 시간대와 원문을 보존하고 보고서에서는 KST 기준 날짜를 사용한다. 원문 광고의 픽셀은 보고서 제작 과정에서 수정하지 않는다.'),['inventory.csv','evidence/*-production.jsonl'])
    independent=qa.get('independent_reviews',[])
    body+='<p class="small">독립 AI 검토 기록 '+str(len(independent))+'개. '+('최신 집계와 검증 대상 수량이 일치한다.' if freshness else '최종 inventory 반영 후 재검증이 필요하다.')+' 요청은 반복적인 표현 틀의 단서가 강하며, 원본 3그룹은 합성 자료이다. 학습·독립 성능 평가·일반화 검증은 수행하지 않았다.</p>'
    page('5. 데이터 품질 검토',body)

    body=field('history.changes','데이터 변경 이력',paragraph('보고서 버전 1.0 · 합성 제작 및 AI 점검 스냅샷. 변경자는 실제 AI 역할로 식별하며 사람 실명·승인은 미확인이다.')+table(['일자','변경 내용','변경자','상태·근거'],[
        [esc(asof),'공유 합성 기준 작성','AI 총괄','case-specs.json · 사람 승인 대기'],
        [esc(asof),'문서'+count('new_documents')+'·요청'+count('new_requests')+' 생성','AI 문서·요청 작성','생성 스크립트·제작 로그'],
        [esc(asof),'원본'+count('new_image_groups')+'그룹·광고'+count('new_images')+' 제작','AI 총괄','image_gen 원본/편집'],
        [esc(asof),'3상황 요청 수정: 고지 최소24px·편집 전후SHA·브랜드1회','AI 요청 작성·품질 검토','독립 AI 의미 검토 후 수정'],
        [esc(asof),'모호 규칙에 할인 적용 대상·범위 불명확 추가','AI 문서 작성·품질 검토','문서·제작 로그 해시 갱신'],
        [esc(asof),'파일목록·품질·보고서 작성','AI 총괄·문서·품질 역할','inventory·summary·quality-review'],
        ['2026-10-07 12:00 KST','실제 팀 자료 접수 목표','담당 사람 미확인','계획 · 미완료'],
        [esc(due),'보고서 제출','담당 사람 미확인','예정 · 실제 제출 미실행']],'history-table'),['summary.json','evidence/','README-team-intake.md'])
    FIELD_MAP[-1]['official_subfields']=['일자','변경 내용','변경자','비고·상태']
    body+='<h3>기준 양식과 누락 확인</h3><p>공식 양식의 표지, 수집 개요, 수집 방법, 데이터 구성, 법·윤리, 품질, 변경 이력을 대응했다. 필드별 위치와 근거는 <code>docs/reports/data-collection/template-field-map.json</code>에 기록한다.</p>'
    body+=f'<p class="small">공식 양식: <a href="{TEMPLATE}">{TEMPLATE}</a></p>'
    body+='<h3>패키지에서 확인할 근거</h3>'+table(['근거','역할'],[
        ['data/collection/inventory.csv / summary.json','29파일 행과 유형별 수량·크기 집계의 기준'],['data/collection/evidence/*-production.jsonl','문서·요청·이미지의 출처·도구·시각·해시'],['data/collection/evidence/request-labels.jsonl','사람 미검토 의도 초안; 요청 원자료와 분리'],['data/collection/evidence/image-observations.json','실제 시각 관찰과 생성 의도의 분리'],['data/collection/evidence/submission-packages.jsonl','개별 제출 패키지의 통제된 근거 생략'],['data/collection/evidence/quality-review.json','기본 검증·독립 AI 검토·사람 승인 대기'],['data/collection/README-team-intake.md','실제 팀 자료 접수 계획 및 비공개 처리']],'paths')
    body+='<div class="notice">이 문서의 완료는 합성 원자료와 보고서 패키지 제작의 완료이다. 사람 승인, 실제 팀 자료 확인, 권리 검토, 학습·평가 및 실제 제출 완료를 의미하지 않는다.</div>'
    page('6. 변경 이력 및 근거 패키지',body)

    labels={'normal':'정상 조건 제작 의도','mismatch':'불일치 조건 제작 의도','ambiguous':'할인 적용범위 모호 의도','missing_evidence':'제출 패키지 증빙부족 의도'}
    for c in spec.get('cases',[]):
        body=paragraph(f'<b>{esc(c["brand"])}</b> · {esc(c["product"])} · 기준 할인 {esc(c["discount_percent"])}%')
        body+='<p class="small">새로 생성한 합성 원본 1개와 같은 원본의 편집 파생본 3개이다. 아래 표시는 제작 의도이며 사람 확정 정답이 아니다.</p><div class="gallery">'
        for scenario,label in labels.items():
            aid=c['group_id']+'-'+scenario
            path=BASE/'raw/synthetic/images'/f'{aid}.png'
            data='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else ''
            graphic=f'<img src="{data}" alt="{esc(aid)} 합성 광고 원자료" />' if data else '<div class="missing-image">원자료 제작 대기</div>'
            digest=hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else '해시 대기'
            body+=f'<figure>{graphic}<figcaption><b>{esc(label)}</b><br>{esc(aid)}<br><span class="small">SHA-256 앞16: {digest}</span></figcaption></figure>'
        body+='</div><p class="small">원자료: <code>data/collection/raw/synthetic/images/</code><br>전체 해시·부모 관계: image-production.jsonl 및 inventory.csv.<br>모호함은 할인 적용범위의 의미 모호 사례이다. 증빙부족은 개별 제출 패키지의 근거 생략이며 전체 제작 로그는 보존한다. AI 시각 관찰 초안이며 OCR 자동 실행 결과나 사람 확정 정답이 아니다.</p>'
        page('부록 · '+c['group_id']+' 광고 원자료',body,'appendix')

    css='''@page{size:A4;margin:0}*{box-sizing:border-box}html{background:#f8f6f1}body{margin:0;color:#1b2925;font:10pt/1.65 "Apple SD Gothic Neo","Noto Sans CJK KR",Arial,sans-serif}a{color:#173f38;text-decoration:none;overflow-wrap:anywhere}.page{width:210mm;height:297mm;background:#fff;margin:8mm auto;padding:16mm 18mm;position:relative;box-shadow:0 2mm 7mm #173f3814;break-after:page;page-break-after:always}.page:last-child{break-after:auto;page-break-after:auto}.content{height:250mm}.running{font-size:8pt;letter-spacing:.07em;color:#5b766d;border-bottom:1px solid #b8cbc3;padding-bottom:3mm;margin-bottom:5mm}h1{font-size:34pt;line-height:1.35;color:#173f38;font-weight:700;margin:5mm 0}h2{font-size:19pt;line-height:1.4;color:#173f38;margin:0 0 5mm}h3{font-size:10.6pt;color:#173f38;margin:4mm 0 1.5mm}p{margin:1.3mm 0 3mm;word-break:keep-all;overflow-wrap:anywhere}.field{break-inside:avoid}.notice{padding:3mm 4mm;background:#f1f5f2;border-left:1.3mm solid #527f71;margin:4mm 0;font-size:9.5pt;line-height:1.65}table{border-collapse:collapse;width:100%;table-layout:fixed;margin:2mm 0 4mm;font-size:9pt;line-height:1.55}th,td{text-align:left;vertical-align:top;padding:2.1mm 2.4mm;border-bottom:1px solid #d8e1dc;overflow-wrap:anywhere;word-break:keep-all}th{background:#edf3ef;color:#173f38;font-weight:700}tr{break-inside:avoid}.small{font-size:8.2pt;line-height:1.55;color:#51675d}.dictionary{font-size:8pt;line-height:1.45}.dictionary th,.dictionary td{padding:1.9mm 1.8mm}.dictionary th:nth-child(1){width:17%}.dictionary th:nth-child(2){width:26%}.dictionary th:nth-child(3){width:17%}.paths{font-size:8.3pt}.paths th:nth-child(2){width:54%}.history-table{font-size:8pt;line-height:1.45}.history-table th:nth-child(1){width:17%}.history-table th:nth-child(2){width:37%}.history-table th:nth-child(3){width:21%}.history-table td{padding:1.7mm 2mm}.hashes{font-size:7.7pt;line-height:1.45}.hashes td{word-break:break-all}.hashes th:nth-child(1){width:45%}code{font:8pt/1.5 ui-monospace,Menlo,monospace;overflow-wrap:anywhere;word-break:break-all}pre{font:8.5pt/1.75 ui-monospace,Menlo,monospace;background:#f2f4f1;padding:3mm;margin:2mm 0 4mm;white-space:pre-wrap;overflow-wrap:anywhere}footer{position:absolute;left:18mm;right:18mm;bottom:11mm;display:flex;justify-content:space-between;gap:5mm;border-top:1px solid #cedbd4;padding-top:2mm;font-size:8pt;color:#60746a}.cover-title{margin:11mm 0 8mm}.pill{display:inline-block;background:#173f38;color:white;padding:1mm 3mm;font-size:8.7pt}.subtitle{font-size:13pt;color:#567267}.cover .field{display:grid;grid-template-columns:27mm 1fr;align-items:start;border-bottom:1px solid #e0e7e2;padding:2mm 0}.cover .field h3,.cover .field p{margin:0}.cover .notice{margin-top:8mm}.gallery{display:grid;grid-template-columns:1fr 1fr;gap:4mm 5mm;margin-top:5mm}.gallery figure{margin:0;border:1px solid #d9e4dc;padding:2.5mm;background:#fafbf9;text-align:center;break-inside:avoid}.gallery img,.missing-image{width:62mm;height:62mm;object-fit:contain;display:block;margin:0 auto;background:#f3f4ef}.gallery figcaption{font-size:8.1pt;line-height:1.5;margin-top:1.7mm}.gallery .small{font-size:7.3pt}.appendix> .content>p:last-child{margin-top:4mm}@media print{html,body{background:white}.page{margin:0;box-shadow:none}}'''
    OUT.mkdir(parents=True,exist_ok=True)
    document='<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SKN33 1팀 수집 데이터 보고서</title><style>'+css+'</style></head><body>'+''.join(PAGES)+'</body></html>'
    (OUT/'report.html').write_text(document,encoding='utf-8')
    mapping={'schema_version':'1.0','official_template_url':TEMPLATE,'report_path':'docs/reports/data-collection/report.html','generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'fields':FIELD_MAP,'required_fields':len(FIELD_MAP),'present_fields':len(FIELD_MAP),'missing_fields':0,'pages':len(PAGES),'assumptions':['HTML+PDF is user-selected delivery format','Actual team member names unconfirmed','Human approval and rights review pending'],'inventory_rows':len(inventory),'quality_record_fresh':freshness}
    (OUT/'template-field-map.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'pages':len(PAGES),'mapped_fields':len(FIELD_MAP),'inventory_rows':len(inventory),'embedded_images':document.count('data:image/png;base64,'),'quality_record_fresh':freshness,'report':'docs/reports/data-collection/report.html'},ensure_ascii=False))


if __name__=='__main__':
    main()
