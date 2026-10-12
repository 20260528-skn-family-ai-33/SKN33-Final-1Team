# 수집 데이터 보고서 패키지 · SKN33 1팀

작성 스냅샷: 2026-10-06 / 제출 예정일: 2026-10-08.

공식 양식의 표지와 6개 절을 반영한 결과는 `docs/reports/data-collection/report.html`과 `report.pdf`이다. HTML은 12개 광고 원자료를 내장해 오프라인에서도 열린다. 같은 HTML에서 A4 PDF를 출력한다.

## 실제 확보량

| 구분 | 추가분 | 별도 기존분 | 집계 단위 |
|---|---:|---:|---|
| 기준 문서 | 10 | — | Markdown 원문 파일 |
| 요청문 | 250 | 미확인 | JSONL 1파일 / 의미 상황 50그룹 / 각 5표현 |
| 광고 이미지 | 12 | 6 | 신규 원본 3그룹 / 기존 원본 1그룹 |

신규 주 원자료는 23파일이며 기존 이미지 6파일을 더한 inventory는 29행이다. 제작 이력·정답 초안·관찰·제출 패키지·보고서는 보조 증빙으로 따로 집계한다. 접수·실행 런타임의 복제 파일은 수집 실적으로 합산하지 않는다.

추가분은 모두 자체 제작한 합성 개발 자료이다. 실제 팀 자료는 아직 전달되지 않았으며 확보 상태는 미확인이다. 사람 승인·권리 검토·모델 학습·독립 성능 평가·외부 업로드는 실행하지 않았다. 기본 파일 검증 및 독립 AI 검토 결과와 사람이 확정한 고품질 자료 수(0)는 구분한다.

## 자료와 증빙

- `raw/synthetic/documents/`: 가상 발주 3그룹의 의뢰서·가이드·체크리스트 9개와 공통 FAQ·업무절차 1개.
- `raw/synthetic/requests/requests.jsonl`: 정답 라벨이 없는 250개 한국어 요청.
- `raw/synthetic/images/`: 내장 image_gen으로 별도 생성한 원본 3개와 편집 파생본 9개. 실제 규격은 1254×1254 PNG.
- `inventory.csv`, `summary.json`: 실제 파일·행 수·크기·SHA-256·원본 그룹·검토 상태.
- `evidence/*-production.jsonl`: 출처·도구·생성/저장 시각·부모 원본과 해시. 도구가 제공하지 않은 모델 revision은 미확인으로 기록한다.
- `evidence/prompts/`: **내장 image_gen으로 실제 사용한 최종 생성·편집 프롬프트 12개**. CLI/API 대체 경로를 사용하지 않았다.
- `evidence/request-labels.jsonl`, `image-observations.json`: AI 의도·관찰 초안이며 사람 정답이 아니다. 원자료·서비스 입력과 분리한다.
- `evidence/submission-packages.jsonl`: 12개 가상 제출 패키지. 증빙부족 3개는 패키지 내 제작 근거 제출을 의도적으로 생략한다. 전체 제작 이력은 계속 보존한다.
- `evidence/quality-review.json`, `report-review.json`, `pdf-export.json`: 파일 검증, 독립 검토, 실제 PDF 출력·시각 확인 근거.

원문 프롬프트·정답·평가 증빙을 RAG 입력으로 적재하지 않는다. 같은 원본 이미지나 요청 상황의 표현 변형을 서로 다른 독립 평가 그룹으로 분할하지 않는다. 가상 브랜드는 개발 시나리오에서 만든 표기이며 상표의 독점성·권리 확인을 주장하지 않는다.

## 재집계와 보고서 생성

프로젝트 루트에서 Pillow가 설치된 Python과 Playwright·Chromium을 사용할 수 있는 Node로 실행한다. 이 명령은 앱 데이터베이스나 검수 실행 이력을 변경하지 않는다.

```sh
python3 data/collection/scripts/build_collection.py
python3 data/collection/scripts/validate_collection.py
python3 data/collection/scripts/build_report.py
node data/collection/scripts/export_report.mjs
```

`COLLECTION_NODE_MODULES`와 `COLLECTION_CHROME_PATH`로 Playwright 모듈 위치와 헤드리스 Chromium 실행 파일을 지정할 수 있다. 현재 결과는 Codex 번들 Python·Node와 설치된 Chrome으로 생성했다.

문서·요청을 다시 제작하려면 `scripts/generate_documents.py`, `scripts/generate_requests.py`를 실행한다. 재제작 후에는 해시를 재집계하고 독립 내용 검토와 PDF 전체 페이지 확인을 다시 수행한다. 이미지 프롬프트 재사용은 동일 픽셀을 보장하지 않으며 생성 도구를 별도로 호출해야 한다.

실제 팀 자료의 접수 위치·사용 조건·비식별 절차는 `README-team-intake.md`에 정리했다. 현재 builder는 합성 원자료와 기존 샘플만 집계한다. 실무 자료가 도착하면 사용 가능한 사본을 별도로 등록하고 보고서의 팀 자료 상태·집계를 갱신해야 한다. 10월 7일 정오 접수와 10월 8일 제출은 예정이며 현재 완료된 행동으로 표시하지 않았다.
