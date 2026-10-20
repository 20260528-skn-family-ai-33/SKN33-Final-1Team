# PostgreSQL 물리 설계 3단계 — 규격·인계·감사·OCR·근거

작성일: 2026-10-16

상태: **설계 3단계 작성 완료.** 첫 구축 범위 26개 중 `schema_definitions`, `import_packages`, `audit_events`, `ocr_runs`, `ocr_tokens`, `poster_extractions`, `poster_field_observations`, `evidence_records`, `evidence_ocr_tokens`의 9개 테이블을 다룬다. 이번 산출물은 컬럼·자료형·필수값·키를 정리한 Markdown이며 실제 DB·ORM·마이그레이션에 적용된 구조가 아니다.

## 1. 근거와 공통 규칙

근거는 [범위·관계 정리](물리설계_01_범위와관계.md), [사용자·파일 설계](물리설계_02_사용자와파일.md), [ERD 초안](../ERD_물리.md), [프로젝트 명세](../project-specification.md), [현재 검수 API](../../backend/app/api.py), [행사 정보 추출기](../../backend/app/extract_event.py), [여러 줄 추출기](../../backend/app/event_layout.py), [최초 DB 구조 설계](</Users/choejiheum/Documents/Codex/2026-09-25/x20-x20/outputs/모닝컵_가상납품검수_예제/크리에이티브체커_DB_구조설계_v0.2_2026-10-14.md>)다. 현재 React + FastAPI 구조와 단계별 결정이 초기 설계의 Django 표현보다 우선한다.

- 단일 PK는 앱에서 발급하는 `UUID`, 시각은 UTC 기준 `TIMESTAMPTZ`를 사용한다. `현재 시각` 기본값은 DB의 `CURRENT_TIMESTAMP`다.
- **필수**는 NULL 불가, **선택**은 NULL 허용이다. 기본값이 없는 필수값은 저장 계층이 제공한다. 모르는 값은 임의의 버전·점수·상태로 채우지 않는다.
- JSON 문서·배열·좌표는 `JSONB`로 저장하고 원문 바이트는 `stored_files`가 가리키는 파일에 보존한다. 원문과 조회용 투영을 구분한다. [PostgreSQL JSON 자료형](https://www.postgresql.org/docs/17/datatype-json.html)
- 규격 메타데이터인 `schema_definitions`는 기존 설계처럼 전역이다. 나머지는 `project_id`를 두어 프로젝트별 자료·파일·인계·OCR·근거 연결을 검증한다.
- FK 삭제 정책은 우선 `RESTRICT`다. 확정 추출본·OCR 원문·감사 이력을 연쇄 삭제하거나 다른 프로젝트 데이터에 재연결하지 않는다.
- 아래 상태 값은 서비스 저장·변환 계약이다. 외부 표준의 전체 enum을 확보·검증한 것으로 표현하지 않는다. 수신 원문의 상태도 별도로 보존한다.
- 실행 완료, OCR confidence, 자동 판정, 사람의 검수 승인·정답 라벨 승인은 서로 다른 정보다.
- `admin/admin`은 개발용 계정으로 유지한다. 이메일 기반 정식 계정·DB 세션 설계와 구분하며, 이번 작업에서 로그인 코드를 변경하지 않는다.

## 2. schema_definitions — 규격 정의

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `schema_id` | `UUID` | 필수 | 앱 발급 | 규격 PK |
| `schema_name` | `VARCHAR(150)` | 필수 | 없음 | 실제 선언된 규격 이름 |
| `schema_version` | `VARCHAR(40)` | 필수 | 없음 | 실제 선언된 규격 버전 |
| `specification_file_id` | `UUID` | 선택 | NULL | HTML·설명 문서 파일 |
| `validation_schema_file_id` | `UUID` | 선택 | NULL | 실행 가능한 JSON Schema 파일 |
| `schema_sha256` | `VARCHAR(64)` | 선택 | NULL | 실제 검증 파일 바이트의 SHA256 |
| `availability_state` | `VARCHAR(40)` | 필수 | `metadata_only` | `metadata_only`·`available` |
| `access_scope` | `VARCHAR(40)` | 필수 | `metadata_only` | `metadata_only`·`service_shared`. 규격의 실제 사용 허용 범위 |
| `field_catalog_jsonb` | `JSONB` | 선택 | NULL | 확인한 규격의 필드 카탈로그 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 등록 시각 |

PK는 `schema_id`, UNIQUE는 `(schema_name, schema_version)`다. 파일 FK는 다음과 같다.

- `specification_file_id → stored_files.file_id`
- `validation_schema_file_id → stored_files.file_id`

같은 이름·버전의 내용이 달라지면 기존 정의를 덮어쓰지 않고 충돌을 확인하거나 새 버전으로 등록한다.

`available`은 실제 JSON Schema 파일을 확보하고 형식·해시를 확인해 검증기에 로드할 수 있을 때만 사용한다. 설명 HTML이나 보고서만 있으면 `metadata_only`다. 규격 파일의 존재는 어떤 포스터의 검증 통과를 뜻하지 않는다.

규격 정의는 전역 메타데이터이고 참조 파일은 소유 프로젝트를 가진다. 전역 등록을 이유로 파일 접근 권한을 우회하지 않는다. 설계 6단계에서 `access_scope`를 추가해 파일 확보 여부와 사용 허용을 분리했다. 운영 관리 경로에서 사용 범위를 확인한 `service_shared`·`available` 규격만 업무 검증에 사용할 수 있다. 일반 업무 API는 규격 파일 ID·저장 키를 전역으로 노출하지 않고, 실제 파일 다운로드는 원 소유 프로젝트 권한을 검사한다. 프로젝트별 업무 파일과 달리 이 FK는 의도적으로 전역 규격 정의에서 파일 ID를 참조하는 예외다. 상세 권한은 [6단계](물리설계_06_전체정합성.md) 및 후속 인증 설계를 따른다.

파일 역할은 설명 자료에 `schema_specification`, 실행 스키마에 `validation_schema`를 사용한다. 이름·버전이 선언되지 않은 로컬 OCR 결과를 연결하려고 임의의 외부 표준을 만들지 않는다.

## 3. import_packages — 인계 묶음

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `import_id` | `UUID` | 필수 | 앱 발급 | 인계 묶음 PK |
| `project_id` | `UUID` | 필수 | 없음 | 수신 프로젝트 |
| `manifest_file_id` | `UUID` | 선택 | NULL | 실제 받은 원문 manifest 또는 부재를 명시하고 생성한 로컬 접수 manifest |
| `replaces_import_id` | `UUID` | 선택 | NULL | 실제로 확인한 이전 묶음 |
| `source_namespace` | `VARCHAR(100)` | 필수 | 없음 | 인계 출처 구분 |
| `external_record_id` | `VARCHAR(200)` | 선택 | NULL | 외부 레코드 ID 원문 |
| `package_fingerprint` | `VARCHAR(64)` | 선택 | NULL | 실제 구성 파일 이름·해시로 계산한 묶음 지문 |
| `fingerprint_version` | `VARCHAR(40)` | 선택 | NULL | 지문 직렬화·계산 정책 버전 |
| `hash_check_state` | `VARCHAR(40)` | 필수 | `not_checked` | `not_checked`·`matched`·`mismatch` |
| `import_state` | `VARCHAR(40)` | 필수 | `received` | `received`·`completed`·`needs_review`·`failed` |
| `reported_validation_jsonb` | `JSONB` | 선택 | NULL | 전달자가 보고한 검사 결과 |
| `local_validation_jsonb` | `JSONB` | 선택 | NULL | 이번 수신에서 실제 수행한 검사와 미확인 범위 |
| `provenance_jsonb` | `JSONB` | 선택 | NULL | 출처·원문 추적 ID·계보 |
| `imported_by` | `UUID` | 선택 | NULL | 수신 사용자. 시스템 작업은 NULL 가능 |
| `imported_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 접수 시각 |

키는 PK `import_id`, UNIQUE `(project_id, import_id)`, UNIQUE `(project_id, source_namespace, package_fingerprint)`다. FK는 다음과 같다.

- `project_id → projects.project_id`
- `(project_id, manifest_file_id) → stored_files(project_id, file_id)`
- `(project_id, replaces_import_id) → import_packages(project_id, import_id)`
- `imported_by → users.user_id`

접수 중에는 지문·정책 버전·매니페스트가 NULL일 수 있다. 지문이 NULL인 미완성 접수는 동일 묶음으로 자동 병합하지 않는다. [추가 설계 9단계](추가설계_09_저장과이전복구.md#10-수입-지문중복단계별-저장)에서 cc-import-envelope-v1으로 실제 상대 파일명·바이트 수·SHA256의 정렬 목록과 대상 프로젝트·namespace·고정 변환/연결 profile을 묶도록 보강했다. 신규 UUID·시각·imported_by는 지문에서 제외하며 생성 manifest는 자기 지문 입력에 넣지 않는다. 원문 inventory 지문은 따로 보존한다. 전달자가 적은 해시 목록만으로 실제 파일 확인을 대신하지 않는다.

`completed`는 접수·파일 등록·검사 기록 저장이 완결됐다는 내부 상태다. 필수 파일 누락·해시 불일치가 있으면 완료로 표시하지 않는다. 포스터 의미 검수나 사람의 승인은 별도다. `reported_validation_jsonb`를 그대로 `local_validation_jsonb`의 성공 결과로 복사하지 않는다.

9단계의 hash_check_state=matched는 고정 로컬 inventory와 실제 intake/최종 바이트 대조가 기준이다. 제공자 해시 미제공은 외부 검증 not_checked로 별도 보고하며 출처 진위를 확인한 것으로 표현하지 않는다. 실제 제공자 해시 mismatch는 completed를 막는다.

인계의 순환 FK는 **매니페스트 파일을 인계 FK 없이 등록 → 인계 행 → 파일의 인계 FK 연결** 또는 **received 인계 → 파일/manifest 연결** 순서로 해소한다. 9단계는 고정 계획·received 접수를 먼저 짧게 기록하고, 실제 업무 그래프·파일 연결·검사/봉인을 최종 단일 트랜잭션으로 완료한다. 완료 전 그래프를 일반 이력으로 노출하지 않는다. 원문 manifest가 없으면 로컬 생성 사실을 provenance에 남긴다. `replaces_import_id`는 실제 수정 관계가 확인된 경우만 사용한다.

매니페스트의 파일 역할은 `import_manifest`다. 이전 접수와 같은 지문이 확인되면 기존 인계 ID를 반환하거나 중복 접수로 처리하고 이력을 덮어쓰지 않는다.

## 4. audit_events — 감사 이벤트

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `event_id` | `UUID` | 필수 | 앱 발급 | 이벤트 PK |
| `project_id` | `UUID` | 필수 | 없음 | 업무 프로젝트 |
| `actor_kind` | `VARCHAR(20)` | 필수 | 없음 | `user`·`developer`·`system` |
| `actor_user_id` | `UUID` | 선택 | NULL | 연결 가능한 실제 DB 사용자 |
| `action` | `VARCHAR(100)` | 필수 | 없음 | 발생한 업무 동작 |
| `target_type` | `VARCHAR(100)` | 필수 | 없음 | 대상 업무 종류 |
| `target_id` | `UUID` | 선택 | NULL | 실제 존재하는 대상 ID |
| `request_id` | `TEXT` | 선택 | NULL | 요청 추적 ID |
| `event_metadata` | `JSONB` | 선택 | NULL | 상태 변경·안전한 사유·미확인 정보 |
| `occurred_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 발생 시각 |

PK는 `event_id`, FK는 `project_id → projects.project_id`, `actor_user_id → users.user_id`다. 사용자 동작은 실제 사용자 UUID를 연결하고, 개발 계정 동작은 `actor_kind=developer`로 구분한다. `local-admin` 문자열을 UUID에 넣거나 사용자를 알 수 없다는 이유로 일반 사용자로 위장하지 않는다. 개발 사용자 매핑 전의 이벤트는 actor 종류와 출처를 남긴다.

이벤트는 추가 전용이다. 업무 저장 계층에 수정·삭제 API를 만들지 않으며, 6단계에서 이 테이블의 앱 UPDATE·DELETE 권한을 회수하고 SELECT·INSERT만 부여하도록 설계했다. 실제 권한 변경은 마이그레이션에서 적용한다. 현재 기본 DML 권한만으로 추가 전용이 강제된다고 표현하지 않는다.

`target_type`·`target_id`는 감사 설명용이며 업무 FK를 대체하지 않는다. 요청·대상·프로젝트의 일치는 저장 계층이 확인한다. 프로젝트가 없는 로그인 시도에 임의의 프로젝트 ID를 넣지 않는다. 원문 비밀번호·세션 토큰·API 키는 이벤트 메타데이터에 기록하지 않는다.

## 5. ocr_runs — OCR 실행

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `ocr_run_id` | `UUID` | 필수 | 앱 발급 | OCR 실행 PK |
| `project_id` | `UUID` | 필수 | 없음 | 대상 프로젝트 |
| `asset_version_id` | `UUID` | 필수 | 없음 | 대상 이미지 버전 |
| `attempt_id` | `UUID` | 선택 | NULL | 서비스의 처리 시도 |
| `import_package_id` | `UUID` | 선택 | NULL | 외부 OCR 결과의 수신 근거 |
| `schema_id` | `UUID` | 선택 | NULL | 실제 선언된 수신 규격 |
| `source_file_id` | `UUID` | 선택 | NULL | 원문 OCR JSON 파일. 결과 생성 전·실패 시 없을 수 있음 |
| `input_file_id` | `UUID` | 선택 | NULL | 실제 추론에 사용한 이미지 파일 |
| `frame_id` | `UUID` | 필수 | 없음 | 저장한 OCR 토큰 좌표의 기준 프레임 |
| `execution_status` | `VARCHAR(40)` | 필수 | 없음 | `queued`·`running`·`completed`·`failed`·`imported` |
| `source_status` | `VARCHAR(40)` | 선택 | NULL | 원문이 보고한 OCR 상태 |
| `engine_name` | `VARCHAR(100)` | 선택 | NULL | 실제 실행·출력이 보고한 엔진 |
| `engine_version` | `VARCHAR(100)` | 선택 | NULL | 확인한 엔진 버전 |
| `model_name` | `VARCHAR(200)` | 선택 | NULL | 확인한 대표 모델 이름 |
| `device` | `VARCHAR(100)` | 선택 | NULL | 실제 보고된 장치 |
| `execution_target` | `VARCHAR(20)` | 선택 | NULL | 실제 보고된 `local`·`runpod` |
| `pipeline_name` | `VARCHAR(100)` | 선택 | NULL | 실행 경로 이름 |
| `pipeline_version` | `VARCHAR(100)` | 선택 | NULL | 확인한 경로 버전 |
| `reported_coordinate_space` | `VARCHAR(100)` | 선택 | NULL | 원문이 선언한 좌표 기준 |
| `coordinate_space` | `VARCHAR(100)` | 필수 | 없음 | 실제 DB 토큰 좌표의 기준 |
| `engine_settings` | `JSONB` | 선택 | NULL | 실행에 사용한 비밀정보 제외 설정 |
| `runtime_metadata` | `JSONB` | 선택 | NULL | 모델별 해시·revision, 시도 내역, 원문 추적 ID·시간 범위·출처 |
| `error_jsonb` | `JSONB` | 선택 | NULL | 실패 코드·단계·안전한 사유 |
| `started_at` | `TIMESTAMPTZ` | 선택 | NULL | 서비스가 확인한 시작 시각 |
| `completed_at` | `TIMESTAMPTZ` | 선택 | NULL | 서비스가 확인한 종료 시각 |
| `sealed_at` | `TIMESTAMPTZ` | 선택 | NULL | 원문·토큰을 함께 동결한 로컬 시각. 생성 트랜잭션 종료 시 필수 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 등록 시각 |

키는 PK `ocr_run_id`, UNIQUE `(project_id, asset_version_id, ocr_run_id)`다. FK는 다음과 같다.

- `(project_id, asset_version_id) → asset_versions(project_id, asset_version_id)`
- `(project_id, asset_version_id, attempt_id) → review_attempts(project_id, asset_version_id, attempt_id)` — 설계 5단계에서 이미지 경계까지 보강한 대상 키
- `(project_id, import_package_id) → import_packages(project_id, import_id)`
- `schema_id → schema_definitions.schema_id`
- `(project_id, source_file_id) → stored_files(project_id, file_id)`
- `(project_id, input_file_id) → stored_files(project_id, file_id)`
- `(project_id, asset_version_id, frame_id) → image_coordinate_frames(project_id, asset_version_id, frame_id)`

처리 시도나 인계 묶음 중 하나 이상을 출처로 연결한다. 서비스 실행은 실제 입력 이미지가 필요하며, 외부 수신은 입력 이미지가 제공되지 않았다면 NULL과 미확인 이유를 보존한다. 현재 로컬 OCR JSON은 외부 표준 버전을 선언하지 않으므로 `schema_id`를 NULL로 둘 수 있다. 추론 실패를 저장할 때 결과 JSON 파일이 있다고 가정하지 않는다.

6단계 검토에 따라 최종 OCR·토큰은 같은 트랜잭션에서 생성하고 `sealed_at`을 마지막에 기록한다. 커밋한 OCR 스냅샷은 봉인돼 있어야 하며 그 뒤에는 토큰 추가도 금지한다. 실행 중 진행률은 review_attempts에 남긴다. 생성 중 임시 상태를 영속 OCR 작업 큐로 사용하지 않으며, 외부 수입의 봉인은 원문·토큰 구조를 저장한 로컬 시각이지 추론 완료·정답 승인이 아니다.

`imported`는 외부 결과를 등록한 상태다. 현재 서버에서 추론했거나 내용이 정답이라는 뜻이 아니다. 외부가 보고한 실행 상태·시간·장치와 현재 수신·검증 결과를 구분한다. Paddle의 탐지·인식 모델별 `model_identity`와 시간·메모리 측정 범위는 `runtime_metadata`에 따로 보존하며 모델 버전을 모르면 NULL로 둔다. API 전체 시간을 OCR 추론 시간으로 바꾸지 않는다.

현재 API의 저장 대상 OCR 토큰은 `normalized_original_pixels`이므로 표시 프레임을 참조한다. `input_file_id`는 2배 OCR 입력 파일이어도 된다. **입력 파일의 픽셀 공간과 최종 토큰 프레임은 서로 다른 역할**이다. `coordinate_space`는 참조 프레임과 일치시켜 검증하고 이미 복원한 좌표는 다시 나누지 않는다. 다른 원문 좌표를 변환했다면 원문 JSON·프레임 변환 내역을 보존한다.

## 6. ocr_tokens — OCR 문구

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `token_id` | `UUID` | 필수 | 앱 발급 | 내부 문구 PK |
| `project_id` | `UUID` | 필수 | 없음 | 소속 프로젝트 |
| `asset_version_id` | `UUID` | 필수 | 없음 | 대상 이미지 버전 |
| `ocr_run_id` | `UUID` | 필수 | 없음 | 소속 OCR 실행 |
| `external_token_id` | `VARCHAR(150)` | 필수 | 없음 | 원문 문구 ID의 문자열 표현 |
| `external_token_type` | `VARCHAR(10)` | 필수 | 없음 | 원문 ID의 `string`·`integer` 구분 |
| `token_order` | `INTEGER` | 필수 | 없음 | 해당 실행 안의 0 이상 순서 |
| `text` | `TEXT` | 필수 | 없음 | OCR이 읽은 문구 |
| `confidence` | `DOUBLE PRECISION` | 선택 | NULL | 실제 보고된 0~1의 유한 점수 |
| `bbox_xyxy` | `JSONB` | 선택 | NULL | 기준 프레임 안의 사각형 4개 수치 |
| `polygon` | `JSONB` | 선택 | NULL | 기준 프레임 안의 다각형 좌표열 |
| `recognition_engine` | `VARCHAR(100)` | 선택 | NULL | 문구별 실제 인식 엔진 |
| `requires_review` | `BOOLEAN` | 선택 | NULL | 원문이 명시한 보류 여부. 미제공은 미확인 |
| `source_record_jsonb` | `JSONB` | 필수 | 없음 | ID 타입·후보·이전 문구·점수·보류 사유를 포함한 원문 레코드 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 등록 시각 |

키는 PK `token_id`, UNIQUE `(ocr_run_id, external_token_type, external_token_id)`, UNIQUE `(ocr_run_id, token_order)`, UNIQUE `(project_id, asset_version_id, ocr_run_id, token_id)`다. FK는 `(project_id, asset_version_id, ocr_run_id) → ocr_runs(project_id, asset_version_id, ocr_run_id)`다.

현재 API는 문자열·정수 ID를 구분하므로 정수 `1`과 문자열 `"1"`을 같은 ID로 병합하지 않는다. 문자열 ID와 정수의 문자열 표현을 각각 타입과 함께 보존한다. 길이 초과·중복·잘못된 타입은 원문 파일에 남기고 등록 오류로 처리하며 잘라서 맞추지 않는다. ID 접두어만 보고 엔진을 판정하지 않는다.

기존 초안의 `NUMERIC(8,6)` 대신 `DOUBLE PRECISION`으로 현재 Python float 점수를 보존한다. 소수점 여섯 자리 강제 반올림으로 임계값 경계가 바뀌는 것을 피하려는 결정이며 원래 점수는 원문에도 남긴다. [PostgreSQL 숫자 자료형](https://www.postgresql.org/docs/17/datatype-numeric.html)

confidence 누락을 0이나 1로 채우지 않는다. `requires_review`가 NULL인 것을 검토 불필요로 단정하지 않는다. 원문 `baseline_text`, `baseline_confidence`, `baseline_requires_review`, `selection_reason`, `alternatives`는 `source_record_jsonb`에 보존하고 현재 판정 정책에 맞는 해석은 결과 설계·저장 연동에서 연결한다. 자동 교정 문구로 원문을 덮어쓰지 않는다.

## 7. poster_extractions — 행사 정보 추출본

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `extraction_id` | `UUID` | 필수 | 앱 발급 | 추출본 PK |
| `project_id` | `UUID` | 필수 | 없음 | 대상 프로젝트 |
| `asset_version_id` | `UUID` | 필수 | 없음 | 대상 이미지 버전 |
| `schema_id` | `UUID` | 선택 | NULL | 실제 선언된 추출 표준 |
| `ocr_run_id` | `UUID` | 선택 | NULL | 사용한 OCR 실행 |
| `import_package_id` | `UUID` | 선택 | NULL | 수신 묶음 |
| `predecessor_id` | `UUID` | 선택 | NULL | 같은 이미지 버전의 이전 추출본 |
| `source_file_id` | `UUID` | 선택 | NULL | 추출 원문 JSON 파일 |
| `version_no` | `INTEGER` | 필수 | 없음 | 이미지 버전 안의 1 이상 추출 버전 |
| `method` | `VARCHAR(50)` | 필수 | 없음 | 실제 추출·변환 방법 |
| `extractor_version` | `VARCHAR(100)` | 선택 | NULL | 확인한 추출기 버전 |
| `payload_jsonb` | `JSONB` | 필수 | 없음 | 상태·후보·경고·부분 날짜를 포함한 전체 추출 내용 |
| `title_official` | `TEXT` | 선택 | NULL | 실제 추출본에서 투영한 제목 |
| `location_raw` | `TEXT` | 선택 | NULL | 실제 추출본에서 투영한 장소 원문 |
| `year_value` | `INTEGER` | 선택 | NULL | 근거가 있는 연도 |
| `start_year` | `INTEGER` | 선택 | NULL | 시작 연도 |
| `start_month` | `INTEGER` | 선택 | NULL | 시작 월 |
| `start_day` | `INTEGER` | 선택 | NULL | 시작 일 |
| `end_year` | `INTEGER` | 선택 | NULL | 종료 연도 |
| `end_month` | `INTEGER` | 선택 | NULL | 종료 월 |
| `end_day` | `INTEGER` | 선택 | NULL | 종료 일 |
| `start_date` | `DATE` | 선택 | NULL | 완전하고 유효한 시작 날짜 |
| `end_date` | `DATE` | 선택 | NULL | 완전하고 유효한 종료 날짜 |
| `compatibility_state` | `VARCHAR(40)` | 필수 | 없음 | `native`·`legacy`·`pending_enrichment` |
| `validation_state` | `VARCHAR(40)` | 필수 | `unchecked` | `unchecked`·`valid`·`invalid`·`schema_unavailable` |
| `checks_jsonb` | `JSONB` | 선택 | NULL | 실제 검사·경고·실행하지 못한 검사 |
| `provenance_jsonb` | `JSONB` | 선택 | NULL | 생성 주체·변환·원문 추적 정보 |
| `projection_version` | `VARCHAR(100)` | 필수 | 없음 | 제목·날짜·관찰 행을 만드는 변환 정책 버전 |
| `sealed_at` | `TIMESTAMPTZ` | 선택 | NULL | 추출·관찰·관찰 근거를 함께 동결한 로컬 시각. 생성 트랜잭션 종료 시 필수 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 생성 시각 |

키는 PK `extraction_id`, UNIQUE `(asset_version_id, version_no)`, UNIQUE `(project_id, asset_version_id, extraction_id)`다. FK는 다음과 같다.

- `(project_id, asset_version_id) → asset_versions(project_id, asset_version_id)`
- `schema_id → schema_definitions.schema_id`
- `(project_id, asset_version_id, ocr_run_id) → ocr_runs(project_id, asset_version_id, ocr_run_id)`
- `(project_id, import_package_id) → import_packages(project_id, import_id)`
- `(project_id, asset_version_id, predecessor_id) → poster_extractions(project_id, asset_version_id, extraction_id)`
- `(project_id, source_file_id) → stored_files(project_id, file_id)`

추출 내용은 불변 스냅샷이다. 변경하면 이전 행을 덮어쓰지 않고 새 버전과 선행 관계를 만든다. 버전 번호는 트랜잭션과 UNIQUE로 경합을 처리한다. 날짜가 불완전하면 구성요소·원문·후보를 보존하고 `DATE`를 NULL로 둔다. 연도·월·일을 현재 날짜로 보충하거나 `null`을 부재로 해석하지 않는다.

6단계에서 관찰·근거의 뒤늦은 추가를 막기 위해 sealed_at을 추가했다. 생성 트랜잭션에서 관찰·근거·토큰 연결을 완성한 뒤 1회만 봉인하며, 이후 투영·근거를 보강하려면 새 추출 버전을 만든다.

현재 서비스 추출 결과에는 외부 표준 버전이 없을 수 있어 `schema_id`는 선택값이다. 로컬 추출기의 구조 검사를 통과한 사실과 외부 JSON Schema 검증을 구분한다. `validation_state=valid`는 실제 연결한 스키마로 검증한 경우에만 사용하며, 파일이 없으면 `schema_unavailable`로 남긴다. 수신자가 보고한 통과는 출처 메타데이터에 보존한다.

`native`는 명시한 형식에 맞춘 생성 경로, `legacy`는 구형 구조 원문, `pending_enrichment`는 결손이 남은 변환 초안을 뜻한다. `native` 자체가 검증·승인 완료는 아니다. 현재 `fields.event_name/start_date/end_date/location_raw`와 표준 포스터의 경로를 연결할 때는 실제 입력 구조와 변환 버전을 함께 기록한다. 목록·후보를 제목·날짜 컬럼 하나로 평탄화해 원문에서 삭제하지 않는다.

## 8. poster_field_observations — 필드별 관찰

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `observation_id` | `UUID` | 필수 | 앱 발급 | 관찰 PK |
| `project_id` | `UUID` | 필수 | 없음 | 대상 프로젝트 |
| `asset_version_id` | `UUID` | 필수 | 없음 | 대상 이미지 버전 |
| `extraction_id` | `UUID` | 필수 | 없음 | 원본 추출 스냅샷 |
| `field_code` | `VARCHAR(100)` | 필수 | 없음 | 명시한 추출 형식의 필드 코드 |
| `field_path` | `VARCHAR(255)` | 필수 | 없음 | 원래 payload 안의 필드 경로 |
| `presence_status` | `VARCHAR(40)` | 필수 | `not_checked` | `present`·`illegible`·`absent`·`not_checked` |
| `resolution_status` | `VARCHAR(40)` | 필수 | `unprocessed` | `resolved`·`review_needed`·`unprocessed` |
| `raw_text` | `TEXT` | 선택 | NULL | 실제 원문 문구 |
| `normalized_value` | `JSONB` | 선택 | NULL | 문자열·배열·부분값 등 필드 내용 |
| `source_status` | `VARCHAR(40)` | 선택 | NULL | `extracted`·`needs_review` 등 원문 추출기 상태 |
| `source_reason` | `TEXT` | 선택 | NULL | 원문 사유 |
| `adapter_version` | `VARCHAR(100)` | 필수 | 없음 | 필드 대응·상태 변환 정책 버전 |
| `normalization_jsonb` | `JSONB` | 선택 | NULL | 변환 단계·후보·원래 OCR ID·미확인 정보 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 투영 생성 시각 |

키는 PK `observation_id`, UNIQUE `(extraction_id, field_path)`, UNIQUE `(project_id, asset_version_id, observation_id)`다. FK는 `(project_id, asset_version_id, extraction_id) → poster_extractions(project_id, asset_version_id, extraction_id)`다.

필드 값과 상태는 원래 추출본에서 투영하며 다른 값으로 수동 덮어쓰기하지 않는다. 서비스 형식은 현재 핵심 필드의 경로를 사용하고, 외부 표준 형식은 확보한 규격 카탈로그·어댑터에 따라 경로를 정한다. 같은 payload 버전·변환 버전에 기대되는 관찰 행이 누락됐는지 검증한다.

`null`·빈 배열·OCR 미추출만으로 `absent`를 부여하지 않는다. 구형 데이터에서 표기 여부를 알 수 없으면 `not_checked`다. 후보 충돌·미지원·불완전 날짜는 `review_needed`와 이유를 남긴다. `illegible`은 실제로 표기가 있으나 읽지 못했음을 확인한 경우다.

현재 추출기의 `extracted`는 실제 값·OCR 근거·대응 정책을 확인한 뒤 `resolved`로 투영할 수 있으나 사람의 승인을 뜻하지 않는다. `needs_review`는 `review_needed`로 보존한다. 근거가 없는 값이나 누락된 OCR ID를 성공 상태로 올리지 않는다. `source_ocr_ids`는 해당 OCR 실행 안에서 실제 토큰을 찾아 근거 연결로 만들고, 찾지 못하면 원문 ID와 불일치 이유를 남긴다.

## 9. evidence_records — 근거

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `evidence_id` | `UUID` | 필수 | 앱 발급 | 근거 PK |
| `project_id` | `UUID` | 필수 | 없음 | 대상 프로젝트 |
| `observation_id` | `UUID` | 선택 | NULL | 소유 필드 관찰 |
| `item_result_id` | `UUID` | 선택 | NULL | 소유 항목 결과 |
| `asset_version_id` | `UUID` | 필수 | 없음 | 근거 대상 이미지 버전 |
| `source_file_id` | `UUID` | 선택 | NULL | 실제 근거 파일 |
| `frame_id` | `UUID` | 선택 | NULL | bbox·polygon의 기준 프레임 |
| `source_ocr_run_id` | `UUID` | 선택 | NULL | 실제 출처 OCR 실행 |
| `source_kind` | `VARCHAR(40)` | 필수 | 없음 | 첫 범위의 `ocr`·`image`·`document` |
| `field_pointer` | `VARCHAR(255)` | 선택 | NULL | 원문 필드·목록 항목의 구체 경로 |
| `page_no` | `INTEGER` | 선택 | NULL | 원문이 식별한 페이지 |
| `text` | `TEXT` | 선택 | NULL | 근거 원문 발췌 |
| `bbox_xyxy` | `JSONB` | 선택 | NULL | 근거 사각형 |
| `polygon` | `JSONB` | 선택 | NULL | 근거 다각형 |
| `confidence` | `DOUBLE PRECISION` | 선택 | NULL | 원문이 보고한 근거 점수 |
| `method_code` | `VARCHAR(100)` | 필수 | 없음 | 실제 근거 획득 방법 |
| `locator_jsonb` | `JSONB` | 선택 | NULL | 문서 위치·세부 출처 |
| `limitations_jsonb` | `JSONB` | 선택 | NULL | 확인 한계·보류 사유 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 생성 시각 |

키는 PK `evidence_id`, UNIQUE `(project_id, asset_version_id, evidence_id)`, UNIQUE `(project_id, asset_version_id, source_ocr_run_id, evidence_id)`다. FK는 다음과 같다.

- `(project_id, asset_version_id) → asset_versions(project_id, asset_version_id)`
- `(project_id, asset_version_id, observation_id) → poster_field_observations(project_id, asset_version_id, observation_id)`
- `(project_id, asset_version_id, item_result_id) → item_results(project_id, asset_version_id, result_id)` — 설계 5단계의 대상 키 조건
- `(project_id, source_file_id) → stored_files(project_id, file_id)`
- `(project_id, asset_version_id, frame_id) → image_coordinate_frames(project_id, asset_version_id, frame_id)`
- `(project_id, asset_version_id, source_ocr_run_id) → ocr_runs(project_id, asset_version_id, ocr_run_id)`

첫 범위에서는 소유 FK인 `observation_id`·`item_result_id` 중 **정확히 하나**를 연결한다. 1단계에서 미룬 `reference_field_result_id`, `human_decision_id`, `reference_record_id`는 이번 스키마에 넣지 않는다.

OCR 근거는 실제 OCR 실행·토큰, 이미지 근거는 실제 파일·프레임, 문서 근거는 실제 파일·위치를 요구한다. 이번 근거는 이미지 검수의 관찰·결과에 속하므로 대상 이미지 버전은 필수다. bbox·polygon이 있으면 프레임이 있어야 하고 좌표 범위를 검증한다. 점수·좌표가 없으면 NULL로 둔다. 여러 OCR 점수의 평균을 필드 정확도나 새로운 근거 confidence로 만들지 않는다.

## 10. evidence_ocr_tokens — 근거와 OCR 문구 연결

| 컬럼 | 자료형 | 필수 여부 | 기본값 | 의미 |
|---|---|---|---|---|
| `evidence_id` | `UUID` | 필수 | 없음 | 소유 근거 |
| `token_id` | `UUID` | 필수 | 없음 | 실제 OCR 문구 |
| `project_id` | `UUID` | 필수 | 없음 | 공통 프로젝트 |
| `asset_version_id` | `UUID` | 필수 | 없음 | 공통 이미지 버전 |
| `ocr_run_id` | `UUID` | 필수 | 없음 | 공통 OCR 실행 |
| `token_order` | `INTEGER` | 필수 | 없음 | 해당 근거 안의 0 이상 연결 순서 |
| `created_at` | `TIMESTAMPTZ` | 필수 | 현재 시각 | 연결 생성 시각 |

키는 복합 PK `(evidence_id, token_id)`, UNIQUE `(evidence_id, token_order)`다. FK는 다음과 같다.

- `(project_id, asset_version_id, ocr_run_id, evidence_id) → evidence_records(project_id, asset_version_id, source_ocr_run_id, evidence_id)`
- `(project_id, asset_version_id, ocr_run_id, token_id) → ocr_tokens(project_id, asset_version_id, ocr_run_id, token_id)`

공통 OCR 실행 ID를 두 FK에 함께 사용해 다른 실행의 토큰을 같은 근거에 섞는 것을 막는다. 연결 행의 ID들은 필수이므로 NULL로 복합 FK 검사를 건너뛰지 않는다. 서로 다른 OCR 실행이 필요하면 실행별 근거 행을 나누고 출처를 보존한다.

## 11. 다음 설계와 저장 연동에 넘길 조건

| 연결 대상 | 이번 결정 | 후속 확인 |
|---|---|---|
| `stored_files` | 규격 파일·매니페스트 역할 확정, `(project_id, import_id)` 대상 키 제공 | 파일·인계 연결 순서와 실패 시 파일 정리 |
| `review_attempts` | `(project_id, asset_version_id, attempt_id)` 유일 키가 OCR FK의 대상 | 설계 5단계에서 시도·실행·이미지 연결 정의. 6단계에서 전체 제약 대조 |
| `item_results` | `(project_id, asset_version_id, result_id)` 유일 키가 근거 FK의 대상 | 설계 5단계에서 결과·기준 항목·실행의 복합 FK 정의. 6단계에서 근거 문맥까지 대조 |
| 기준·결과 | 필드의 `resolved`·OCR의 `completed`를 사람 승인과 분리 | 설계 4·5단계의 판정·채택·확정 계약 |
| 전체 정합성 | 프로젝트·이미지·OCR 실행 복합 FK, 출처별 필수값, 상태 조건 | 설계 6단계에서 CHECK·인덱스·권한·공유 규격 예외 검토 |
| 현재 JSON 저장 | 원문 파일·입력 파일·최종 좌표·추출 스냅샷을 별도 연결 | API 저장 전환 단계에서 현재 응답 계약과 함께 검증 |

단일 행의 CHECK로 다른 행의 파일 내용·이미지 해시·프레임 크기나 권한까지 검증했다고 가정하지 않는다. 복합 FK와 저장 계층의 검사 책임을 구분한다. [PostgreSQL 제약 조건 안내](https://www.postgresql.org/docs/17/ddl-constraints.html)

## 12. 기존 ERD 초안 대비 변경과 완료 확인

- 기존 9개 테이블의 컬럼은 보존하되, 1단계에서 제외한 근거의 후속 FK 컬럼 3개만 이번 범위에서 뺐다.
- TEXT로 임시 지정된 설정·payload·원문 메타데이터·좌표·필드 값을 JSONB로 설계했다. 원본 파일 바이트도 따로 보존한다.
- 수신 전후 상태·미확인 모델 정보·재인식 후보·원문 ID 타입·실제 confidence를 보존할 컬럼을 추가했다.
- 선언되지 않은 규격과 아직 생성되지 않은 결과 파일을 허위 등록하지 않도록 OCR·추출본의 규격/원문 파일 연결을 선택값으로 정리했다.
- 인계 접수의 미완성 상태를 표현하도록 지문을 선택값으로 바꾸고, 실제 지문 계산·완료 조건을 분리했다.
- OCR·추출·관찰·근거·문구 연결에 프로젝트와 이미지 버전을 두고, 두 문서의 복합 키 연결 조건을 맞췄다.
- `admin/admin` 개발용 로그인 유지 방침을 연결 문서에 반영했다. 개발·정식 인증 코드의 통합·DB 적용은 이번 작업에 포함하지 않았다.

진행 갱신: [설계 6단계 — 전체 정합성](물리설계_06_전체정합성.md)에서 규격 사용 범위와 OCR·추출본 봉인까지 보강하고 [추가 설계 7단계 — 인증·권한](추가설계_07_인증과권한.md)과 [추가 설계 8단계 — API·화면·JSON](추가설계_08_API와화면JSON.md)과 [추가 설계 9단계 — 저장·복구](추가설계_09_저장과이전복구.md)을 완료했다. [추가 설계 10단계 — 구현 구조·검증 명세](추가설계_10_구현구조와검증명세.md)까지 완료했다. [C01 — DB 설정·연결·트랜잭션 계층](구현_C01_DB연결과트랜잭션.md)의 구현·격리 PostgreSQL 검증을 완료했다. [C02 — Alembic과 사용자 스키마](구현_C02_Alembic과사용자스키마.md)의 M001·격리 DB 검증도 완료했다. [C03 — 파일 스키마/M002](구현_C03_파일스키마와M002.md)도 구현·격리 DB 검증을 완료했다. [C04 — 규격·인계·OCR 스키마/M003](구현_C04_OCR스키마와M003.md)도 구현·격리 DB 검증을 완료했다. [C05 — 검수 기준 스키마/M004](구현_C05_검수기준스키마와M004.md)도 구현·격리 DB 검증을 완료했다. [C06 — 실행·결과와 전체 FK](구현_C06_실행결과와전체FK.md)도 구현·격리 DB 검증을 완료했다. [C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md)의 초안 구현·격리 DB 검증도 완료했다. [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)의 가드와 정식 M007도 구현·검증했다. [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했다. 정식 head는 M008이다. [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했다. [C09 — 파일 저장과 기준 확정](구현_C09_파일저장과기준확정.md)도 구현·검증했다. [C10 — 검수 접수와 원자적 결과](구현_C10_검수접수와원자적결과.md)도 구현·검증했다. [C11 — React와 서버 상태 연결](구현_C11_React와서버상태연결.md)도 구현·계약/합성 API 브라우저 검증했다. [C12 — 실제 E2E·프록시 검증](구현_C12_실제E2E와프록시검증.md)도 실제 직접/Vite·Linux HTTPS·로컬 Paddle CPU 경로로 검증했다. [C13 — 독립 worker와 회수](구현_C13_독립Worker와회수.md)도 구현·격리 DB/프로세스·실제 로컬 Paddle CLI로 검증했다. [C14 — 수입/GC·실제 백업 복원](구현_C14_수입GC와백업복원.md)도 구현·격리 훈련했다. [C15 — Linux 배포 리허설과 전환](구현_C15_Linux배포리허설과전환.md)도 사용자 지정 로컬 범위에서 완료했다. C01~C15의 구현/격리 검증을 마쳤으며 실제 운영 서버·모델·백업 보관·공개 전환은 별도 후속이다.
