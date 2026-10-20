# C01 — DB 설정·연결·트랜잭션 계층

작성일: 2026-10-17

상태: **C01 구현·검증 완료.** 아래는 C01 당시 기록이다. 이후 [C02](구현_C02_Alembic과사용자스키마.md)·[C03](구현_C03_파일스키마와M002.md)·[C04 — 규격·인계·OCR 스키마/M003](구현_C04_OCR스키마와M003.md)도 완료했으며 [C05 — 검수 기준 스키마/M004](구현_C05_검수기준스키마와M004.md)도 완료했으며 [C06 — 실행·결과와 전체 FK](구현_C06_실행결과와전체FK.md)도 완료했으며 [C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md) 초안 검증도 완료했으며 [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)도 완료했으며 [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했으며 [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했으며 다음 작업은 C09이다. 기존 API는 JSON 저장과 localhost admin/admin을 유지한다.

## 1. 구현한 범위

| 파일 | 실제 동작 |
|---|---|
| [config.py](../../backend/app/config.py) | 기본 legacy_json, 명시 postgres 설정 검증, 비밀번호 마스킹, 명시한 환경 파일의 리터럴 값 읽기 |
| [database.py](../../backend/app/persistence/database.py) | SQLAlchemy Core·psycopg 동기 Engine, 제한된 pool, UTC/UTF8·실제 app 역할/권한 확인 |
| [transaction.py](../../backend/app/persistence/transaction.py) | 같은 스레드의 Connection 생성/정리, commit/rollback, 커밋 미확인과 롤백 미확인 구분 |
| [check_database.py](../../scripts/check_database.py) | 비밀·DSN 없는 JSON 진단. SELECT만 수행하며 API·DDL·seed 실행 없음 |
| [requirements-db.txt](../../requirements-db.txt) | SQLAlchemy 2.1.5·psycopg[binary] 3.3.6, 기존 OCR/offline 의존성 파일과 별도 |
| [설정 테스트](../../backend/tests/test_db_config.py)·[트랜잭션 테스트](../../backend/tests/test_db_transaction.py) | 비밀 보호·설정 오류·선택 의존성·미확인 commit/rollback·테스트 DB 필수 설정 |
| [PostgreSQL 통합 테스트](../../backend/tests/test_db_integration.py) | 임의 이름의 별도 DB와 probe에서 실제 접속·커밋·롤백·오류·스레드 분리 |

기존 api.py/local_auth.py·React·OCR·launcher·Compose·DB 역할/볼륨은 수정하지 않았다. DB 소유자/관리자 자격 증명은 앱 설정에 저장하지 않는다. 첫 연결은 실제 DB 역할의 관리자 속성·DB owner 및 강한 권한 역할의 멤버십도 검사하므로 환경변수 이름만 app으로 바꿔 owner 접속을 허용하지 않는다.

## 2. 접속 점검

저장소 루트의 Python 3.12 가상환경에서 선택 의존성을 설치한다. 현재 macOS 환경에서는 설치와 pip check를 완료했다.

```bash
.venv/bin/python -m pip install -r requirements-db.txt
.venv/bin/python -m pip check
```

.env의 실제 비밀번호는 기존 PostgreSQL 초기화 값과 일치해야 한다. C01 도구는 .env를 자동 탐색하지 않으며 아래처럼 명시적으로 읽는다. 기본 legacy_json은 DB를 점검하지 않고 status=disabled·database_checked=false를 반환한다.

```bash
PERSISTENCE_MODE=postgres .venv/bin/python -m scripts.check_database --env-file .env
```

Windows에서는 가상환경 Python을 `.\.venv\Scripts\python.exe`로 바꾸고 PowerShell에서 `$env:PERSISTENCE_MODE = "postgres"`를 먼저 지정한다. Windows의 실제 접속 검증은 이번에 실행하지 않았다.

성공하면 status=connected, DB명·current_user·UTC·서버 버전 번호·UTF8만 반환한다. 실제 확인값은 creative_checker/creative_checker_app·PostgreSQL 17.11이다. 접속 실패는 종료 코드 1과 DB_CONNECTION_FAILED로 반환하며 JSON 저장·owner·다른 DSN으로 우회하지 않는다. 이 명령은 업무 API 연결이나 스키마 준비 완료를 의미하지 않는다.

환경 파일은 UTF-8의 한 줄 KEY=value, 단일/이중 따옴표, 주석, 선택적 export 접두사를 지원한다. 프로세스 환경변수가 파일보다 우선한다. 셸 실행·변수 치환·escape 해석·여러 줄 값은 지원하지 않는다. 비밀번호의 공백/#/특수문자를 그대로 유지하려면 따옴표로 감싼다. 파일을 source로 실행할 필요가 없다.

## 3. 트랜잭션과 오류

Engine은 프로세스당 한 번 명시적으로 만들고 종료 때 dispose한다. import나 설정 읽기만으로 Engine·DB 연결을 만들지 않는다. 서비스는 unit_of_work 안에서 Connection을 열고 같은 스레드에서 사용·정리한다. repository는 commit하지 않고 파일 수신·해시·OCR 중 DB 잠금을 유지하지 않는다. async 경로는 전체 UoW를 한 번의 threadpool 호출 안에 둔다.

READ COMMITTED, pool_size=5·max_overflow=0·pool_timeout=5초·pre_ping, connect_timeout=5초가 기본이다. 설정 허용 범위는 connect/pool 대기 1~60초·pool_size 1~20이다. 일반 UoW는 SET LOCAL lock_timeout=2초·statement_timeout=10초, import_operation은 statement_timeout=60초다. 자동 재시도는 하지 않는다.

| 상황 | 진단·결과 |
|---|---|
| 본문 정상·COMMIT 응답 확인 | 성공 후 Connection 반환 |
| 본문 예외·rollback 확인 | 변경 취소, 원래 애플리케이션 예외 또는 안전한 DB 오류 |
| commit 시 제약/충돌 오류 응답 | 제약 거부·확인된 rollback으로 분류. deferred 오류를 성공으로 반환하지 않음 |
| COMMIT 통신 실패/결과 미확인 | DB_COMMIT_OUTCOME_UNKNOWN·commit_unknown·retryable=false. 조회로 확인하기 전 재실행 금지 |
| rollback 확인 실패 | rollback_unconfirmed·retryable=false |
| COMMIT 성공 후 정리 실패 | DB_CLEANUP_FAILED·committed. 이미 커밋된 작업을 다시 만들지 않음 |

SQLSTATE 40001/40P01도 rollback이 확인된 경우에만 retryable=true다. PostgreSQL 원문 오류·SQL·파라미터·DSN을 외부 예외/점검 결과에 재출력하지 않는다. 커밋 응답 유실·정리 실패 등은 결정적 단위 테스트로 분류를 확인했으며 실제 네트워크 장애 주입을 실행한 것은 아니다.

## 4. 전용 PostgreSQL 테스트 실행

기본 pytest는 db_integration을 제외해 기존 offline CI를 유지한다. 전용 DB 검증은 명시적으로 선택한다. 패키지나 테스트 설정이 없으면 실패하며 전부 skip된 실행을 성공으로 처리하지 않는다.

Git에서 제외되는 .env.db-test에 다음 키를 준비한다. 비밀번호는 해당 테스트 서버의 기존 역할 값으로 직접 채우며 아래 값은 예시다. TEST_DB_*는 일반 DB_*로부터 자동 복사하지 않는다.

```dotenv
TEST_DB_ALLOW_CREATE=1
TEST_DB_HOST=127.0.0.1
TEST_DB_PORT=5432
TEST_DB_ADMIN_USER=postgres
TEST_DB_ADMIN_PASSWORD=replace-with-test-admin-secret
TEST_DB_OWNER_USER=creative_checker_owner
TEST_DB_OWNER_PASSWORD=replace-with-test-owner-secret
TEST_DB_APP_USER=creative_checker_app
TEST_DB_APP_PASSWORD=replace-with-test-app-secret
```

```bash
# 기존 offline 회귀 + 선택 의존성이 설치된 경우의 DB 단위 테스트
.venv/bin/python -m pytest -q

# 명시 테스트 설정으로 실제 PostgreSQL 검증
TEST_DB_ENV_FILE=.env.db-test .venv/bin/python -m pytest -m db_integration -q
```

테스트는 creative_checker_test_<임의 UUID> DB를 직접 생성한다. 사용자가 DB명을 지정할 수 없으며 일상 creative_checker DB에 probe를 만들지 않는다. owner가 전용 c01_probe schema와 records 테이블을 만들고 app에 해당 probe의 SELECT/INSERT만 허용한다. 종료 때 모든 연결을 정리한 후 그 테스트 DB만 삭제하며 FORCE나 다른 세션 종료를 하지 않는다. 관리자·owner 비밀은 테스트 관리 연결에만 사용한다.

## 5. 실제 검증 결과와 다음 범위

- 기본 Python 테스트: **387 passed, DB 통합 8 deselected, 추가 subtest 7 passed**. 신규 C01 단위 사례 42개와 기존 API·admin 로그인·launcher·OCR의 offline 회귀를 포함한다.
- 실제 PostgreSQL 통합: **8 passed**. 역할/UTC/UTF8/격리·제한·연결 반환, commit, 애플리케이션 예외 rollback, SQL 오류 후 회복, deferred commit 제약 거부, statement timeout, 스레드별 독립 연결, owner 접속 거부를 확인했다.
- 일상 DB의 app 접속과 고의로 잘못된 비밀번호의 안전한 실패를 확인했다. 테스트 전후 public 관계 목록은 빈 상태로 같았으며 임시 테스트 DB가 남지 않았다.
- 설치 버전과 pip check를 확인했다. Docker Desktop을 실행해 기존 컨테이너를 사용했으며 컨테이너·볼륨을 재생성하지 않았다.

[실행 검증 기록](verification/2026-10-17-implementation-C01.json)에 결과·명령·파일 지문·실행 범위를 저장한다. 상세 설계의 DB/AUTH/API/STORE 전체 114개와 IMPL 20개가 모두 통과했다는 뜻은 아니다. Linux/Windows 실실행, 새로운 DB CI job, 실제 통신 장애·백업/복원·브라우저/실제 OCR 검증은 이번 범위가 아니다.

후속 [C02 — Alembic 기반·identity metadata/M001과 기본 권한 회수](구현_C02_Alembic과사용자스키마.md)도 완료했다. [C03 — 파일 metadata/M002](구현_C03_파일스키마와M002.md)도 완료했다. [C04 — 규격·인계·OCR 스키마/M003](구현_C04_OCR스키마와M003.md)도 완료했다. [C05 — 검수 기준 스키마/M004](구현_C05_검수기준스키마와M004.md)도 완료했다. [C06 — 실행·결과와 전체 FK](구현_C06_실행결과와전체FK.md)도 완료했다. [C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md)의 초안 구현·격리 DB 검증도 완료했다. [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)의 가드와 정식 M007도 구현·검증했다. [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했다. 정식 head는 M008이다. [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했다. [C09 — 파일 저장과 기준 확정](구현_C09_파일저장과기준확정.md)도 완료했다. [C10 — 검수 접수와 원자적 결과](구현_C10_검수접수와원자적결과.md)도 구현·검증했다. 다음은 **C11 — React 프로젝트/초안/이력·URL 복원·근거·이메일 UI**다. 사용자가 “다음”이라고 하면 C11만 진행한다.
