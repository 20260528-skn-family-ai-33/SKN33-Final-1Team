# C06 — 실행·결과 스키마와 전체 FK

작성일: 2026-10-17

상태: **C06 구현·격리 PostgreSQL 검증 완료. C07a 인덱스와 C07b 가드·정식 M007 검증도 완료. [C07c](구현_C07c_권한과최종스키마.md)의 M008 권한·최종 inventory도 완료했으며 [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했으며 [C09 — 파일 저장과 기준 확정](구현_C09_파일저장과기준확정.md)도 완료했으며 [C10 — 검수 접수와 원자적 결과](구현_C10_검수접수와원자적결과.md)도 완료했으며 다음은 C11이다.** 실행·시도·항목 결과·최종 채택 4개 테이블을 M005로 추가하고 M006에서 전체 81개 FK를 연결했다. 기존 API·admin/admin·JSON 검수는 유지하며 일상 creative_checker DB에는 적용하지 않았다.

## 1. 구현 범위

| 파일 | 책임 |
|---|---|
| [reviews.py](../../backend/app/persistence/schema/reviews.py) | 실행·시도·결과·채택의 컬럼·PK/UQ/CHECK·기본값·코멘트 |
| [relations.py](../../backend/app/persistence/schema/relations.py) | 모든 테이블 등록 후 81개 FK를 metadata에 연결. import 때 연결·DDL 없음 |
| [schema/__init__.py](../../backend/app/persistence/schema/__init__.py) | 전체 테이블 → 관계 순서의 metadata 등록 |
| [M005_reviews.py](../../backend/migrations/versions/M005_reviews.py) | 실행 4개 테이블의 고정된 명시적 DDL. FK 없음 |
| [M006_relations.py](../../backend/migrations/versions/M006_relations.py) | 81개 FK의 고정된 명시적 DDL. 기존 행도 검증하며 NOT VALID·자동 채택 없음 |
| [정적 스키마 테스트](../../backend/tests/test_review_schema.py) | 컬럼·키·CHECK·revision 불변·전체 FK 계약·증분 SQL |
| [M005 DB 테스트](../../backend/tests/test_review_migration_integration.py) | 실행 수명·출처·상태/판정·비용·멱등 키·증분 보존·롤백 |
| [M006 DB 테스트](../../backend/tests/test_relations_migration_integration.py) | 전체 관계·실제 ID의 문맥 오류·NULL·RESTRICT·원자적 적용 |
| [합성 관계 fixture](../../backend/tests/relation_fixtures.py) | 테스트 DB에 실제 FK가 맞는 합성 메타데이터 그래프 구성. 이미지/JSON 파일을 생성하거나 OCR을 실행하지 않음 |
| [테스트 metadata 도구](../../backend/tests/schema_helpers.py) | 과거 revision 비교와 기본 public 스키마 표기 정규화. 실제 head FK 검증은 생략하지 않음 |

| 추가 테이블 | 컬럼 | PK | UNIQUE | CHECK |
|---|---:|---:|---:|---:|
| review_runs | 40 | 1 | 3 | 4 |
| review_attempts | 24 | 1 | 3 | 3 |
| item_results | 23 | 1 | 3 | 4 |
| run_item_selections | 9 | 1 | 0 | 2 |
| C06 추가 | **96** | **4** | **9** | **13** |
| M001~M006 누적 | **395** | **26** | **41** | **77** |

M005까지는 FK가 없으며 M006에서 **81개 FK 제약**을 추가한다. 복합 FK의 개별 컬럼 수와 제약 개수를 혼동하지 않는다. alembic_version은 업무 테이블 수에서 제외한다. C06 당시에는 UNIQUE/PK 지원 외의 인덱스·업무 가드·최종 GRANT를 추가하지 않았다. 이후 C07a에서 인덱스 후보를 별도로 검증했으며 정식 M006 metadata는 유지한다.

## 2. 실행 수명과 결과의 구분

- 신규 서비스 실행은 사용자·operation·멱등키·요청 해시/규격, workflow·집계 정책·실행 대상·설정, 입력 manifest·규격·재시도 정책을 요구한다. 자기 재검수는 금지하며 원 실행이 있으면 retry_review, 없으면 create_review다.
- queued/running/completed/failed의 단계·포인터·시각·오류·봉인·결과 지문·판정 조합을 검사한다. completed만 progress=100이며 채택 시도·시작/종료·봉인·결과 해시·요약 판정이 모두 필요하다. 처리 시작 전 failed는 started_at=NULL을 허용한다.
- imported_result/legacy_result는 imported 실행·executor·mode를 사용하고 로컬 단계·진행률·시작/종료·현재 시도·요약 판정·서비스 요청 키/해시는 NULL이다. 외부 인계 또는 과거 원문을 요구한다. 수입의 커밋 시 봉인·manifest 완결성은 M007에서 완성한다.
- 시도는 active/succeeded/failed/unknown/imported를 구분한다. 서비스 시도는 시작·토큰이 필요하고 terminal은 종료도 요구한다. imported는 로컬 토큰·lease·heartbeat·시작/종료가 NULL이며 출처 정보를 요구한다. 부모 executor별 lease 필수 조건과 active 시도 최대 하나는 후속 가드/인덱스다.
- lease·heartbeat와 비용·통화는 각 쌍이 모두 없거나 모두 제공돼야 한다. lease는 heartbeat보다 뒤이며 비용은 유한 비음수 NUMERIC(20,8), 통화는 영문 대문자 3자리다. 미측정 비용·미확인 retryable은 NULL을 보존한다.
- 서비스 항목 결과는 사유·검사기 버전·입력 지문이 필요하다. completed만 pass/fail/review 판정을 가지며 failed/unsupported는 판정 NULL이다. 수입 결과는 원문과 imported/NULL 판정을 사용한다. 원문 pass를 로컬 판정으로 복사하지 않는다.
- 최종 항목 채택은 pipeline_final/import_projection과 revision=1을 사용한다. 사람 승인 기록이 아니다. 같은 이미지·기준으로 다른 키를 사용한 새 실행은 허용하며 실행 내 자동 재시도와 별도 run의 재검수를 구분한다.
- JSON 객체·객체/배열 한계 정보·가변 observed_value, 유한 시각·시작/종료 순서·SHA256 외형을 검사한다. 해시의 실제 계산·원문 일치나 자동 요약 정책 실행은 아직 구현하지 않았다.

## 3. 전체 FK와 남는 경계

81개 관계의 이름·참조 양쪽 컬럼 순서는 [제약 목록](물리설계_06_제약목록.md)을 따른다. 모두 public 스키마에서 **ON DELETE RESTRICT·ON UPDATE RESTRICT·NOT DEFERRABLE·MATCH SIMPLE**이며 기존 행을 즉시 검증한다. 잘못된 기존 행은 자동 수정하거나 무시하지 않고 M006 전체를 실패시킨다. [PostgreSQL FK](https://www.postgresql.org/docs/17/ddl-constraints.html#DDL-CONSTRAINTS-FK)·[ALTER TABLE 검증](https://www.postgresql.org/docs/17/sql-altertable.html)

- 프로젝트·이미지·기준·실행·시도·항목·결과의 복합 문맥을 검사한다. 존재하는 ID라도 다른 문맥이면 거부한다. 필수 문맥 컬럼은 NOT NULL이므로 NULL로 복합 FK를 우회할 수 없다.
- 근거와 토큰은 같은 프로젝트·이미지·OCR 실행이어야 한다. 같은 이미지라도 다른 OCR 토큰/근거를 연결할 수 없다. 채택 FK는 실행·이미지·기준 버전·항목·결과를 함께 검사한다.
- 파일↔인계는 파일의 인계 연결을 NULL로 등록 → received 인계 → 파일 연결 순서, 실행↔시도는 포인터 NULL인 run → 시도 → 현재/채택 포인터 연결 순서를 실제 DB에서 확인했다. FK를 비활성화하거나 지연하지 않았다.
- 전역 schema_definitions에서 파일 ID를 참조하는 두 FK는 프로젝트 복합 키의 의도적인 예외다. 이 예외가 파일 다운로드 권한을 부여하지 않는다.
- FK는 사용자 존재를 검사하며 현재 멤버십·활성 상태·역할을 검사하지 않는다. 파일 역할/내용/해시·좌표 범위, 긴 순환, 봉인 후 변경, OCR과 실제 사용 시도·추출본 일치, 전체 항목 완결성·채택 시도 일치는 M007과 저장 서비스에서 검증한다.

과거 M001~M005 비교는 해당 테이블의 별도 metadata 복사본에서 후속 FK를 제외한다. 생산 metadata와 실제 DB 제약은 수정하거나 끄지 않는다. head 비교에서는 모든 관계를 유지하고 Alembic이 기본 public을 schema=None으로 반영하는 표기만 정규화한다. 실제 PostgreSQL catalog는 public·81개 이름·컬럼·정책·검증 플래그를 별도로 전부 대조한다. [SQLAlchemy schema 복사](https://docs.sqlalchemy.org/en/21/core/metadata.html#sqlalchemy.schema.Table.to_metadata)

## 4. 적용과 실행 경계

M001~M004 revision 소스는 바꾸지 않았다. 새 패키지·owner/app 설정 변경과 업무 seed는 없다. 이전 검증 JSON은 당시 이력으로 보존한다.

```bash
.venv/bin/python -m alembic -c backend/migrations/alembic.ini heads

# 검토용 증분 SQL. DB 접속·적용 없음.
CC_MIGRATION_ENV_FILE=.env.owner-test .venv/bin/python -m alembic -c backend/migrations/alembic.ini upgrade M004:M005 --sql
CC_MIGRATION_ENV_FILE=.env.owner-test .venv/bin/python -m alembic -c backend/migrations/alembic.ini upgrade M005:M006 --sql

# 명시한 격리 DB에만 적용. 일상 DB에는 아직 실행하지 않는다.
CC_MIGRATION_ENV_FILE=.env.owner-test .venv/bin/python -m alembic -c backend/migrations/alembic.ini upgrade M006
```

owner 파일·테스트 설정은 [C02 실행 방법](구현_C02_Alembic과사용자스키마.md#3-명시적-실행-방법)·[C01 테스트 설정](구현_C01_DB연결과트랜잭션.md#4-전용-postgresql-테스트-실행)을 따른다. 파일명은 명시 설정의 예시이며 저장소에 자격증명 파일을 생성하지 않았다. 일반 앱·launcher의 자동 upgrade, 기존 객체 자동 채택·덮어쓰기와 파괴적 downgrade는 제공하지 않는다. C06 당시 head는 M006이며 M007/M008까지 준비된 운영 스키마가 아니므로 v1 쓰기를 공개할 수 없다.

## 5. 실제 검증 결과

```bash
.venv/bin/python -m pytest -q
TEST_DB_ENV_FILE=.env.db-test .venv/bin/python -m pytest -m db_integration -q
```

- 기본 Python **425 passed**, 추가 subtest 7 passed. DB 통합 833개는 기본 실행에서 제외했다. 신규 C06 정적 사례는 7개다.
- 실제 PostgreSQL **833 passed**: 기존 회귀 491개와 C06의 M005 211개·M006 131개다. macOS Python 3.12.14·기존 PostgreSQL 17.11을 사용했다.
- 기존 역할 값을 명시적인 TEST_DB_* 자식 프로세스 환경으로 전달했다. 비밀은 명령행·출력·검증 JSON에 기록하지 않았으며 업무 DB명을 테스트 fixture에 전달하지 않았다. 일반 DB 설정 자동 복사·업무 DB fallback은 없다.
- 26개 테이블·395개 컬럼과 PK 26·UNIQUE 41·CHECK 77·FK 81을 metadata/실제 inventory와 대조했다. M005 신규 4개 테이블이 빈 상태이고 기존 M004 행 내용이 보존되는지 확인했다. 신규 CHECK 13개 묶음 모두에서 잘못된 행을 거부했다.
- FK를 구성한 뒤에는 두 프로젝트와 같은 프로젝트의 다른 이미지가 포함된 합성 그래프를 보존했고, FK가 켜진 상태에서 새 그래프와 두 순환 연결을 다시 만들었다. 파일 바이트·OCR·외부 규격 검증을 수행한 그래프라는 뜻은 아니다.
- 81개 FK 경로의 고아 참조 변경은 모두 23503으로 거부됐다. 같은 컬럼이 복수 관계·역참조에 걸리면 다른 FK가 먼저 거부할 수 있으므로 요청한 FK와 실제 발행된 이름을 JUnit/검증 기록에 함께 남긴다. 모든 개별 FK의 정확한 정의·활성/검증 상태는 catalog에서 별도로 확인했다.
- 실제 존재하는 다른 프로젝트·이미지 ID 22개 조합, 같은 이미지의 다른 OCR 2개, 같은 이미지의 다른 run/항목 3개 연결을 거부했다. 필수 문맥 NULL 4개는 23502, 참조된 부모 삭제/키 변경 16개는 23503으로 거부됐다. 선택 FK의 정상 NULL과 전역 규격 파일 예외도 허용됐다.
- 기존 item_results 충돌과 M005 끝의 오류는 M004 객체·행·버전·기본 ACL을 보존했다. 기존 부분 스키마에 잘못된 관계가 있는 M004→M005→M006 요청은 M005도 함께 롤백했다.
- 마지막 FK의 고아 행으로 M006이 실패했을 때 먼저 추가한 관계도 모두 롤백되고 원래 행을 보존했다. M006 끝의 오류 주입도 기존 M005의 객체 OID·행·버전·기본 ACL을 보존했다. 빈 DB→M006 전체 실패도 DDL·버전·기본 ACL을 함께 롤백했다.
- 81개 FK 모두 convalidated=true·condeferrable=false·condeferred=false·MATCH SIMPLE·DELETE/UPDATE RESTRICT였다. 같은 revision 재실행과 M005/M006 파괴적 downgrade 거부를 확인했다. app은 업무 테이블 권한이 없으며 실제 SELECT도 거부됐다.
- 테스트 전후 일상 DB의 public 관계·기본 ACL이 같았고 생성한 임시 DB를 모두 정리했다. pip check·구문·문서 링크·기존 소스/revision 지문 검사도 통과했다. 기존 API·admin/admin·React·OCR·launcher는 변경하지 않았다.

실제 결과와 지문은 [C06 검증 기록](verification/2026-10-17-implementation-C06.json)에 저장한다. Linux/Windows·실제 브라우저/OCR·네트워크 COMMIT 장애·파일 내구성·백업 복원·후속 M007/M008 검증은 이번에 실행하지 않았다.

## 6. 다음 단계

[C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md)의 초안 구현·격리 DB 검증도 완료했다. [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)의 가드와 정식 M007도 구현·검증했다. [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했다. [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했다. 정식 head는 M008이며 다음은 **C09 — 파일 저장·잠금/저널·JCS·업로드·초안·확정**이다. 조회 인덱스 25개와 부분 유일 인덱스 2개는 C07a에서 검증했고 C07b에서 가드를 완성해 정식 M007로 승격했다. 사용자가 “다음”이라고 하면 C10만 진행한다. 이메일 로그인·저장 API·React·worker는 기존 C08 이후 순서를 유지한다.
