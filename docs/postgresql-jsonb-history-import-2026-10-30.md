# PostgreSQL JSONB 확인 및 기존 검수 이력 이관

확인일: 2026-10-30. 로컬 Docker의 `creative_checker`를 실제 조회하고 보유 검수 이력을 적재한 결과다. 모델 재추론, 웹앱 기본 저장 방식 전환, Neo4j 동기화는 이번 작업에 포함하지 않았다.

## 확인 결과

- PostgreSQL 17.11 컨테이너 정상 실행, 마이그레이션 `M008` 적용 상태.
- 업무 테이블 26개, 전체 컬럼 395개, JSONB 컬럼 48개. 스키마 변경 없이 기존 컬럼으로 구조화 결과를 저장할 수 있다.
- `.runtime/reviews`는 JSON 파일을 사용하는 레거시 저장소다. 조사 범위의 검수 저장소 및 실험 폴더에서는 JSONL이 발견되지 않았다. JSONL은 테이블 컬럼 타입이 아니라 파일 직렬화 형식이다.
- 두 모델 비교 채팅의 최신 실험 출력도 JSON이다. DB에서는 기대값·관찰값·원문 결과 등을 JSONB 컬럼으로 보존할 수 있다. 모델 변경만을 이유로 기존 컬럼을 삭제하거나 바꿀 필요는 없다.

## 실제 적재 결과

기존 기준 문서 프로젝트를 보존하고, `[미승인 이관] 로컬 검수 이력 2026-10-30` 프로젝트를 별도로 만들었다. 기존 모델의 이력도 원문 그대로 남겼다.

| 항목 | 적재 전 DB 전체 | 적재 후 DB 전체 | 이번 추가 |
|---|---:|---:|---:|
| 프로젝트 | 1 | 2 | 1 |
| 이미지 버전 | 1 | 39 | 38 |
| 기준 버전 | 1 | 39 | 38 |
| 기준 항목 | 20 | 381 | 361 |
| 검수 실행 이력 | 0 | 38 | 38 |
| 항목 결과 | 0 | 301 | 301 |
| 저장 파일 | 5 | 295 | 290 |

원본 실행 상태는 완료 32건, 실패 6건이다. DB의 이번 이관 상태는 38건 모두 `imported`이며, 실패 기록을 성공으로 바꾸지 않았다. 기준 버전은 모두 `unconfirmed`, 로컬 판정 `verdict`는 NULL로 유지했다. 원본 모델 판정은 `source_status`와 `source_result_jsonb`에 남긴다. 결과가 없는 기준을 통과로 채우지 않는다.

## JSONB 구성

| 저장 위치 | 내용 |
|---|---|
| `criteria_versions.source_snapshot_jsonb` | 원본 기준 스냅샷 전체 |
| `criteria_items.source_item_jsonb` | 기준 원문과 `document_condition` 전체 |
| `criteria_items.expected_value` | 원문이 명시한 기대값. 직접 제공된 객체·배열·숫자도 원형 보존 |
| `criteria_items.source_locator` | 실제 제공된 문서 페이지·출처 목록 |
| `review_runs.source_snapshot_jsonb` | 원본 결과 전체와 제공된 실행 메타데이터 |
| `item_results.observed_value` | 명시된 관찰값 또는 기존 `observed_text` |
| `item_results.source_result_jsonb` | 원본 판정·이유·증거와 추가 필드 전체 |

PDF 구조 조건 54개에서 기대값을 분리했고, 이 중 실제 출처 목록이 제공된 24개는 `source_locator`로 조회된다. 나머지 30개에 출처를 만들어 넣지 않았다. `is_required`는 명시된 수준만 반영하며, 미제공은 NULL이다. 사용자가 표시 문구를 수정해 첨부 PDF 조건과 달라진 경우, 오래된 기대값을 자동 적용하지 않는다.

최신 Qwen 실험의 `checks`, `provenance`, 개수·객체·좌표 같은 중첩 필드는 원문 JSONB에 보존할 수 있다. 자유 문장에서 개수를 추측하거나 모델 관찰을 사람 승인으로 바꾸지 않는다. 별도 원격 실험의 **19장·98조건 전체 예측 파일은 이번 적재 대상에 포함하지 않았다.** 이번 301개 결과는 보유한 로컬 검수 이력이다. 원문에 없는 모델 이름도 최종 모델 이름으로 덮어쓰지 않았다.

## 검증 및 보존

- 실제 DB 통합 테스트를 포함한 36개 테스트 통과.
- 기준 스냅샷 38개·결과 JSON 38개·기준 항목 361개·결과 항목 301개가 PostgreSQL 조회값과 일치.
- 38개 패키지를 다시 적용해도 DB 행이 변하지 않는 것을 검증.
- 제한된 앱 DB 계정으로 기준 361개, 기대값 54개, 출처 24개를 실제 조회.
- 등록 파일 295개의 바이트 크기·SHA-256·존재 확인 완료.
- 원본 파일 388개 및 기존 DB 행 보존. 스키마 해시도 변경 없음.
- DB 변경 전 custom-format `pg_dump`와 기존 비공개 파일 저장소 복사본 생성. 덤프 목록 조회는 확인했으며 전체 복구 리허설은 이번에 수행하지 않았다.

이관기 프로필은 `conservative-source-v2`로 구분한다. 기존 v1 동결 계획의 투영 방식과 해시 검증도 계속 지원한다. 커다란 파생 OCR 입력 이미지와 잘라낸 이미지는 원래 위치에 보존하고, DB 파일 적재 대상은 기본 원본·표시 이미지와 JSON으로 제한했다. OCR 좌표는 검증된 `evidence_records`/`ocr_tokens`로 승격하지 않았으며, 원본 JSON에 보존한다.

## 앞으로 필요한 작업

1. 기본 `start.py`의 레거시 API 저장 경로와 PostgreSQL v1 경로를 서비스 수준에서 통합한다. 이번 이관 이후 새로 생성하는 검수 기록이 자동으로 PostgreSQL에 저장되도록 전환하는 작업은 별도다.
2. 최신 벤치마크 출력의 `case_id`·`condition_id`를 실제 프로젝트·이미지·기준 버전에 연결하는 명시적 어댑터를 만든다. 연결 없이 98조건을 서비스 검수 이력으로 취급하지 않는다.
3. 실제 모델명·프롬프트/어댑터 버전·입력 해시가 제공되도록 실행 메타데이터 계약을 연결하고, PostgreSQL 저장 이후 Neo4j에 기준·출처 관계를 동기화한다.

## 조회 예시

이번 이관 프로젝트 ID는 동결 원본 인벤토리로부터 결정되며 실행 산출물 `verification.json`에 기록되어 있다. 다른 프로젝트와 합산하지 않도록 실제 ID를 넣어 조회한다.

```sql
SELECT external_item_id, expected_value, is_required,
       source_locator -> 'sources' AS document_sources
FROM public.criteria_items
WHERE project_id = '<verification.json의 project_id>'::uuid
ORDER BY criteria_version_id, position;

SELECT source_status, verdict, observed_value,
       source_result_jsonb -> 'evidence' AS original_evidence
FROM public.item_results
WHERE project_id = '<verification.json의 project_id>'::uuid;
```
