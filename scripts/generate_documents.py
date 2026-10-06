#!/usr/bin/env python3
"""Store ten LLM-authored synthetic rule documents; no answer-key generation.

stdlib only. Document bytes depend on case-specs.json and this script, never on
wall-clock time. The production evidence records the actual storage run time.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/collection"
SPEC = BASE / "case-specs.json"
DEST = BASE / "raw/synthetic/documents"
EVIDENCE = BASE / "evidence/document-production.jsonl"
PROMPT_ID = "SKN33-DOC-RULES-20261006"
PROMPT = (
    "SKN33 1팀 광고 납품 검수 초도 개발용 합성 기준문서 10개 작성. "
    "공유 case-specs.json의 브랜드·제품·할인을 사용해 브랜드별 발주 의뢰서, "
    "제작 가이드, 검수 체크리스트 각 1개 및 공통 FAQ/업무절차 1개를 작성. "
    "정상·불일치·모호·증빙부족의 판단 규칙과 근거 기록 방법을 설명하되 "
    "특정 이미지의 검수 정답은 넣지 말 것. 합성/내부 사용, 사람 승인 대기, "
    "권리 미검토 및 독립 평가용 아님을 명시할 것. 실무 연락처는 넣지 말 것."
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def header(title: str, doc_id: str, group: str) -> str:
    return f"""# {title}

| 항목 | 내용 |
|---|---|
| 문서 ID | {doc_id} |
| 문서 버전 | 1.0 |
| 원본 그룹 | {group} |
| 자료 유형 | synthetic · LLM이 작성한 가상 규칙문서 |
| 사용 목적 | SKN33 1팀 광고 납품 검수 초도 개발·시연 |
| 승인 상태 | 사람 검토·승인 미실행(pending) |
| 사용 조건 | 프로젝트 내부 합성 자료; 외부 공개·상업 이용 승인 없음 |
| 권리 검토 | 브랜드 이름 및 제작물 권리 검토 미실행; 사용 허가를 뜻하지 않음 |
| 평가 제한 | 독립 일반화 성능 평가용 아님; 학습·평가 준비 완료 아님 |
| 작성 근거 | case-specs.json 및 생성 요청 {PROMPT_ID} |

이 문서는 실제 고객 발주, 실무 계약 또는 팀이 제공한 업무 자료가 아니다.
연락처와 실제 거래처 정보는 포함하지 않는다. 수집시각·해시·도구 정보는
`data/collection/evidence/document-production.jsonl`에 별도로 기록한다.
"""


def rules() -> str:
    return """
## 공통 검수 판단 규칙

- 정상: 승인된 규칙과 확인 가능한 광고 관찰값이 일치하고, 제출 패키지의
  제작 근거가 확보된 상태를 뜻한다. 사람 승인 완료를 자동으로 뜻하지 않는다.
- 불일치: 읽을 수 있는 관찰값이 기준의 브랜드·제품·할인·필수 표시 중
  하나 이상과 명확히 다르다. 해당 항목과 위치 및 근거를 함께 기록한다.
- 모호: 적용 대상·범위가 명확하지 않거나, 흐림·겹침·대비 부족·축소 또는
  OCR 후보 충돌로 특정 조건을 확정할 수 없다. 문자가 읽혀도 최대 할인과
  일부 메뉴 적용처럼 기준 상품의 적용 범위를 확인할 수 없으면 의미가
  모호하다. 제작 의도만으로 일치 여부를 확정하지 않는다.
- 증빙부족: 제출 패키지에 원본 출처·버전·제작 이력 등 필요한 근거가
  누락된 상태이다. 이미지의 픽셀 오류나 OCR 실패와 구분한다.

텍스트 조건, 가독성 및 패키지 증빙을 각각 기록한다. 여러 문제가 동시에
있으면 한 상태로 덮어쓰지 않고 항목별 결과를 보존한 뒤 검토 대기로 둔다.
OCR 신뢰도는 참고 신호이며 고정된 숫자 하나로 사람 검토를 대체하지 않는다.
"""


def brief(c: dict, doc_id: str) -> str:
    return header(f"{c['brand']} 가상 광고 발주 의뢰서", doc_id, c['group_id']) + f"""
## 1. 발주 개요와 적용 범위

- 가상 발주 ID: {c['project_id']}; 연결 원본 그룹: {c['group_id']}.
- 목적: 납품 광고가 기준문서의 조건과 일치하는지 비교하는 개발 시나리오.
- 대상: {c['brand']}의 {c['product']} 광고 원본과 그 편집 파생본.
- 이 의뢰서는 소비자에게 제공하는 실제 할인 행사나 발주 계약이 아니다.
- 사용기간·매체·규격은 실제 제출 패키지에 기록한다. 여기서 임의의 행사
  날짜나 매체 규격을 승인 조건으로 추가하지 않는다.

## 2. 반드시 확인할 광고 조건

| 검수 항목 | 기준 | 비교 방법 |
|---|---|---|
| 브랜드 | {c['brand']} | 화면의 브랜드 문구를 직접 확인하고 OCR 후보와 대조 |
| 상품명 | {c['product']} | 상품 표기 위치와 읽힌 문구를 기록 |
| 할인율 | {c['discount_percent']}% 할인 | 숫자와 %의 관계, 할인 대상 문구를 함께 확인 |
| 필수 표시 | {c['required_disclaimer']} | 가상 자료임을 알리는 문구의 존재와 가독성 확인 |
| 시각 방향 | {c['visual']} | 제작 의도와 실제 관찰을 별도로 기록 |

의미가 같은 띄어쓰기 차이는 원문을 보존한 채 비교 메모에 남긴다. 브랜드
철자나 할인 숫자의 차이를 띄어쓰기 정규화로 숨기지 않는다. 색감과 분위기
설명은 제작 방향이며 그 자체만으로 정량 합격 수치를 만들어내지 않는다.

## 3. 납품 패키지와 필요한 제작 근거

광고 파일, 파일 ID/경로, 원본 그룹, 파생본 관계, 제작 또는 편집시각,
사용 도구, 적용 기준문서 버전 및 변경 내용을 제출 기록에 포함한다.
신규 원본과 편집 파생본의 수를 구분하며 파생본을 신규 독립 원본으로
세지 않는다. 제작 도구가 모델 버전을 제공하지 않으면 unknown으로 기록한다.

## 4. 변경·재검수 절차

기준 변경을 요청받으면 새 버전과 변경 사유를 먼저 기록하고 사람 승인
대기로 둔다. 기존 버전으로 검수한 결과는 수정하지 않고 적용 버전을
남긴다. 재검수는 수정 파일 및 이전 결과를 연결하고 변경 항목을 비교한다.
""" + rules() + """
## 5. 완료 조건과 보류 사유

모든 필수 항목에 관찰값과 근거 위치를 기록한 뒤 사람이 규칙과 제출
패키지를 검토해야 최종 승인할 수 있다. 자료 부족, 조건 충돌 및 불명확한
요청은 보류 사유와 필요한 추가 자료를 남긴다. 이 문서 자체에는 파일별
판정 결과나 정답 라벨을 기재하지 않는다.
"""


def guide(c: dict, doc_id: str) -> str:
    return header(f"{c['brand']} 가상 광고 제작 가이드", doc_id, c['group_id']) + f"""
## 1. 제작 기준과 참조 순서

같은 원본 그룹의 발주 의뢰서가 브랜드·상품·할인 조건의 기준이다. 이
가이드는 그 조건을 읽을 수 있게 표현하고 제작 근거를 남기는 방법을
설명한다. 기준문서끼리 충돌하면 임의로 우선순위를 정해 진행하지 않고
적용 버전을 확인한 뒤 사람 검토 요청을 기록한다.

## 2. 표기와 시각 구성

- 브랜드는 `{c['brand']}`, 상품명은 `{c['product']}`, 할인율은
  `{c['discount_percent']}% 할인`으로 표현하는 것이 제작 기준이다.
- 필수 표시 `{c['required_disclaimer']}`를 배경과 구별되게 배치한다.
- 시각 방향: {c['visual']}.
- 브랜드·상품·할인율의 관계를 쉽게 파악할 수 있게 배치하고 글자끼리
  겹치거나 화면 경계에 잘리지 않게 한다. 실제 크기와 축소 보기에서 확인한다.
- 할인율이 다른 가격이나 수치와 혼동되지 않게 한다. 근거 없는 행사기간,
  재고, 가격, 원산지 또는 실제 업체 연락처를 추가하지 않는다.

## 3. 원본 제작과 파생본 이력

원본은 신규 생성 기록과 생성 프롬프트를 연결한다. 편집 시에는 원본 ID,
편집 목표, 실제 변경 내용, 도구 및 산출물 경로를 남긴다. 생성 모델의
문자 표현이 의도와 다를 수 있으므로 프롬프트에 쓴 조건을 화면에서
실제로 읽혔다고 기록하지 않는다. 생성 의도와 관찰 결과는 다른 필드이다.

## 4. 가독성 점검과 예외 처리

글자가 흐리거나 겹친 경우 원본을 확대해서 확인하고 위치를 기록한다.
그래도 확정할 수 없으면 원문 재제출 또는 사람 확인을 요청한다. OCR의
복수 후보를 임의로 원하는 조건에 맞춰 선택하지 않는다. 문자 인식 오류가
반복되면 입력 해상도, 대비, 회전 여부를 점검하되 원본 파일을 덮어쓰지 않는다.
""" + rules() + """
## 5. 내부 사용과 승인 기록

가상 광고 표시는 내부 시연에서도 유지한다. 브랜드 권리 및 제작물 이용
조건은 사람이 검토하지 않았고 공개·상업 사용을 허가하지 않는다. AI가
규칙을 확인하거나 파일을 생성한 것은 사람 승인과 다르다. 문서 검토자는
검토 일자·변경 항목·승인 상태를 별도 기록한다.
"""


def checklist(c: dict, doc_id: str) -> str:
    return header(f"{c['brand']} 가상 광고 검수 체크리스트", doc_id, c['group_id']) + f"""
## 1. 검수 시작 전 확인

검수자는 광고 파일 ID, 그룹 ID `{c['group_id']}`, 발주 ID
`{c['project_id']}`, 기준문서 버전, 제출 패키지 경로와 검수시각을 기록한다.
지원하지 않는 파일, 열리지 않는 파일 또는 기준을 찾을 수 없는 요청은
그 이유를 남기고 추가 자료를 요청한다. 보이지 않는 항목을 추정하지 않는다.

## 2. 항목별 확인표

| 항목 | 확인할 기준 | 기록할 근거 |
|---|---|---|
| 브랜드 | {c['brand']} | 관찰 문구·텍스트 위치·OCR 후보 |
| 상품 | {c['product']} | 상품명 관찰값·위치·가독성 |
| 할인 | {c['discount_percent']}% 할인 | 숫자·기호·할인 대상의 관계 |
| 가상 표시 | {c['required_disclaimer']} | 문구 존재·관찰값·가독성 |
| 제작 방향 | {c['visual']} | 관찰 사실 및 제작 의도와의 차이 |
| 기준 연결 | 같은 그룹 발주·가이드 버전 | 참조한 문서 ID와 버전 |
| 출처 연결 | 원본·파생본 관계 및 제작 이력 | 원본 경로·편집 기록·사용 도구 |

표는 규칙 확인용이며 특정 파일의 정답을 담지 않는다. 검수 결과에는 각
항목의 관찰값, 적용 기준, 상태와 근거를 분리해서 기록한다.
""" + rules() + """
## 3. 재검수와 상태 조회

재검수는 이전 요청 ID와 수정 파일을 연결한 뒤 수정 대상뿐 아니라 필수
표시의 훼손 여부도 확인한다. 기준 변경 요청은 변경 전후 문서 버전과
사람 승인 상태를 확인한다. 상태 조회는 저장된 진행 상태를 설명하는
업무이며 새 검수 완료나 사람 승인을 만들어내지 않는다.

## 4. 종료 전 체크

- 관찰값과 제작 의도를 분리했는가?
- 확인 불가능한 항목 및 증빙 누락을 누락 없이 기록했는가?
- 자동 분석 실패를 광고 자체 오류로 오해하지 않았는가?
- 적용 문서 버전, 파일 경로 및 원본 관계를 남겼는가?
- 사람이 승인하지 않았다면 pending을 유지했는가?

검수 메모에서 파일별 초안 판단을 작성할 수 있으나 본 규칙문서에 결과를
추가하지 않는다. 승인된 평가 데이터와 별도 분리하여 관리한다.
"""


def faq(cases: list[dict], doc_id: str) -> str:
    mappings = "\n".join(
        f"| {c['group_id']} | {c['brand']} | {c['product']} | {c['discount_percent']}% |"
        for c in cases
    )
    return header("합성 광고 납품 검수 공통 FAQ 및 업무절차", doc_id, "공통") + f"""
## 1. 그룹별 기준문서 연결

| 원본 그룹 | 브랜드 | 상품명 | 기준 할인율 |
|---|---|---|---|
{mappings}

각 그룹의 의뢰서·가이드·체크리스트를 함께 참조한다. 표는 기준 조건만
표시하며 제출 광고의 실제 판정이나 이미지별 정답을 담지 않는다.

## 2. 접수부터 사람 검토까지의 업무 흐름

1. 요청의 목적을 신규 검수·재검수·기준 변경·상태 조회·기타로 구분한다.
2. 요청 ID, 제출 광고 및 패키지 경로, 해당 원본 그룹과 문서 버전을 기록한다.
3. 자료가 열리는지 확인하고 원본·편집 파생본 관계와 제작 근거를 점검한다.
4. OCR과 시각 관찰을 수행하고 필수 조건의 관찰값과 위치를 기록한다.
5. 항목별 규칙과 대조하여 불일치·모호·증빙 누락을 근거와 함께 기록한다.
6. 추가 자료 또는 수정이 필요한 경우 필요한 항목과 사유를 명확히 남긴다.
7. AI 분석 결과는 초안으로 보존하고 최종 사람 검토·승인을 별도 요청한다.

## 3. 자주 묻는 질문

### 프롬프트에 조건을 적었으면 정상인가?
아니다. 프롬프트는 제작 의도이다. 생성 광고의 실제 글자와 조건은 별도로
관찰해야 한다. 모델의 문자 표현 오류 가능성을 숨기지 않는다.

### 화면이 정확해 보여도 제작 근거가 없으면 어떻게 하는가?
텍스트 관찰은 그대로 기록하고 패키지 증빙은 부족으로 기록한다. 원본
관계나 제작 이력 부족을 픽셀 오류로 표현하지 않는다.

### OCR 후보가 기준과 다르면 바로 광고 오류인가?
아니다. 이미지에서 읽을 수 있는지를 확인하고 후보 충돌·흐림은 모호로
남긴다. 근거가 없으면 원하는 후보를 임의로 선택하지 않는다.

### 기준 변경과 재검수는 같은 요청인가?
아니다. 기준 변경은 적용 문서의 내용·버전을 바꾸는 요청이고 재검수는
수정 광고를 다시 확인하는 요청이다. 기준 변경은 사람 승인 대기를 유지한다.

### 합성 자료는 실제 수집 자료 및 평가자료로 셀 수 있는가?
합성 추가분으로만 집계한다. 실제 팀 보유 자료, 외부 출처 수집 및 합성
자료는 구분한다. 같은 원본의 편집본은 독립 원본 수에 더하지 않는다.
사람 승인과 독립성 검토 없이 학습·평가 준비 완료라고 표시하지 않는다.

### 공개·상업 사용이 가능한가?
이 자료의 사용 조건은 프로젝트 내부 개발·시연이다. 브랜드 권리와 제작물
이용 조건은 검토하지 않았다. 임의로 공개 라이선스를 부여하거나 실제
거래처의 발주 자료라고 표시하지 않는다.
""" + rules() + """
## 4. 예외·변경 기록

문서 간 조건 충돌, 제출 누락, 파일 손상, 모델/도구 정보 부재는 발생 사실과
확인 가능한 근거만 기록한다. 정보가 없으면 unknown 또는 pending으로
표시한다. 요청별 담당자와 시각은 실제 접수 후 기록하며 연락처를 꾸며내지 않는다.
변경 이력에는 버전, 수정 항목, 수정 이유, AI 검토와 사람 승인 상태를 남긴다.
"""


def main() -> None:
    raw_spec = SPEC.read_bytes()
    specs = json.loads(raw_spec)
    cases = specs["cases"]
    if len(cases) != 3 or specs["source_kind"] != "synthetic":
        raise ValueError("Expected three synthetic source groups")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    DEST.mkdir(parents=True, exist_ok=True)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    outputs = []
    for i, case in enumerate(cases, 1):
        for kind, writer in [("brief", brief), ("guide", guide), ("checklist", checklist)]:
            doc_id = f"SYN-DOC-{i:02d}-{kind.upper()}"
            outputs.append((f"{case['group_id'].lower()}-{kind}.md", doc_id,
                            case["group_id"], kind, writer(case, doc_id)))
    outputs.append(("syn-common-faq-workflow.md", "SYN-DOC-COMMON-FAQ", "common",
                    "faq_workflow", faq(cases, "SYN-DOC-COMMON-FAQ")))
    records = []
    for filename, doc_id, group, kind, body in outputs:
        path = DEST / filename
        payload = body.encode("utf-8")
        path.write_bytes(payload)
        records.append({
            "schema_version": "1.0", "document_id": doc_id,
            "path": path.relative_to(ROOT).as_posix(), "group_id": group,
            "document_kind": kind, "source_kind": "synthetic", "format": "markdown",
            "encoding": "utf-8", "bytes": len(payload), "sha256": sha256(payload),
            "generated_at": now, "stored_at": now,
            "generation_time_kind": "Python text assembly and storage run; LLM authoring time not independently recorded",
            "authoring_method": "LLM-authored rule text; Python templating and storage",
            "authoring_model": "unknown", "model_version": "unknown",
            "tool": "Python standard library", "python_version": platform.python_version(),
            "generator": "scripts/generate_documents.py",
            "generator_sha256": sha256(Path(__file__).read_bytes()),
            "case_specs_path": SPEC.relative_to(ROOT).as_posix(),
            "case_specs_sha256": sha256(raw_spec),
            "prompt_id": PROMPT_ID, "prompt": PROMPT,
            "license": "project-internal synthetic use only; rights not reviewed",
            "rights_review_status": "not_reviewed", "human_review_status": "pending",
            "independent_evaluation_eligible": False,
            "contains_image_answer_key": False,
        })
    EVIDENCE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                        encoding="utf-8")
    print(json.dumps({"documents": len(records), "bytes": sum(r["bytes"] for r in records),
                      "evidence": EVIDENCE.relative_to(ROOT).as_posix(),
                      "human_review_status": "pending"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
