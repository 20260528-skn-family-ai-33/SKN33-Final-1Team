# C02 — Alembic 기반과 사용자·세션·프로젝트 스키마

작성일: 2026-10-17

상태: **C02 구현·격리 PostgreSQL 검증 완료.** 아래는 C02 당시 기록이다. 이후 [C03](구현_C03_파일스키마와M002.md)·[C04 — 규격·인계·OCR 스키마/M003](구현_C04_OCR스키마와M003.md)도 완료했고 [C05 — 검수 기준 스키마/M004](구현_C05_검수기준스키마와M004.md)도 완료했으며 [C06 — 실행·결과와 전체 FK](구현_C06_실행결과와전체FK.md)도 완료했으며 [C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md) 초안 검증도 완료했으며 [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)도 완료했으며 [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했으며 [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했으며 [C09 — 파일 저장과 기준 확정](구현_C09_파일저장과기준확정.md)도 완료했으며 [C10 — 검수 접수와 원자적 결과](구현_C10_검수접수와원자적결과.md)도 완료했으며 다음은 C11이다. M001을 격리 테스트 DB에만 적용했으며 기존 API·admin/admin·JSON 저장은 유지한다.

## 1. 구현 범위

| 구성 | 실제 산출물 |
|---|---|
| Core metadata | [schema/__init__.py](../../backend/app/persistence/schema/__init__.py)·[identity.py](../../backend/app/persistence/schema/identity.py) |
| Alembic 설정 | [alembic.ini](../../backend/migrations/alembic.ini)·[env.py](../../backend/migrations/env.py)·[revision 템플릿](../../backend/migrations/script.py.mako) |
| owner 연결 분리 | [configuration.py](../../backend/migrations/configuration.py). 앱 설정/Engine과 별도, 비밀번호 마스킹·실제 역할 확인 |
| 첫 revision | [M001_identity.py](../../backend/migrations/versions/M001_identity.py). 변경 가능한 현재 metadata를 import하지 않는 명시적 DDL |
| 의존성 | [requirements-db.txt](../../requirements-db.txt)에 alembic==1.20.0 추가. 현재 환경의 전이 설치는 Mako 1.4.3 |
| 검증 | [설정 테스트](../../backend/tests/test_migration_config.py)·[정적 metadata/SQL 테스트](../../backend/tests/test_identity_schema.py)·[실제 DB 테스트](../../backend/tests/test_identity_migration_integration.py) |

| 테이블 | 컬럼 | PK | UNIQUE | CHECK |
|---|---:|---:|---:|---:|
| users | 8 | 1 | 1 | 2 |
| user_sessions | 6 | 1 | 1 | 3 |
| projects | 10 | 1 | 0 | 3 |
| project_members | 6 | 1 | 0 | 2 |
| 합계 | **30** | **4** | **2** | **10** |

Alembic의 alembic_version은 도구 이력 테이블이므로 업무 테이블 수에 포함하지 않는다. UUID PK는 앱 발급이며 DB 난수 기본값·자동 seed가 없다. 시각은 TIMESTAMPTZ, 현재 시각 기본값·유한값·순서 조건을 적용했고 테이블·컬럼의 한글 코멘트를 반영했다.

users는 email/developer를 구분한다. email 종류는 정규화된 ASCII 이메일과 비어 있지 않은 password_hash가 필요하며 developer는 둘 다 NULL이다. 세션 토큰 해시는 소문자 16진수 64자리, 만료는 발급 이후여야 한다. 프로젝트 사용처는 JSONB 문자열 배열이며 보관 상태/시각을 함께 검사한다. 역할은 owner/editor/viewer만 허용한다. 실제 이메일 형식·Argon2 해시 생성·로그인·세션 발급은 C08 범위다.

## 2. 기본 권한과 마이그레이션 원자성

M001은 **4개 업무 테이블 생성 전에** owner의 app/PUBLIC에 대한 기본 TABLES·SEQUENCES 권한을 전역 및 public 스키마 범위에서 회수한다. 스키마 범위 REVOKE만으로 전역 GRANT를 취소할 수 없으므로 두 범위를 처리한다. Alembic이 먼저 생성하는 alembic_version의 기존 권한도 명시적으로 회수한다. [PostgreSQL 기본 권한 문서](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html)

M008의 최종 권한 부여 전까지 app은 4개 테이블과 alembic_version에 SELECT/INSERT/UPDATE/DELETE 등의 권한이 없다. 기존 C01의 app 연결 점검·SELECT 1은 계속 가능하다. owner는 실제 DB 소유자여야 하며 관리자 속성이 없어야 한다. 초기 app 역할은 관리자 속성과 다른 역할의 상속/멤버십이 없어야 한다. owner 비밀은 일반 앱 설정에 넣지 않는다.

온라인 실행은 UTC/UTF8·PostgreSQL 17·역할을 확인하고 DB별 advisory transaction lock 아래 전체 upgrade를 한 트랜잭션으로 실행한다. lock_timeout 2초·statement_timeout 60초를 사용한다. SQL/파라미터/DSN을 오류에 재출력하지 않고 안전한 오류와 가능한 SQLSTATE만 전달한다. COMMIT 미확인 시 current revision·실제 스키마를 확인하기 전 자동 재시도하지 않는다.

처음 M001을 적용할 때 public에 기존 업무 관계가 있으면 거부하고 덮어쓰지 않는다. 동일 head의 재실행은 변경하지 않는다. 파괴적 downgrade는 거부하며 테스트 DB 재생성이나 별도로 검토한 복원 절차를 사용한다.

## 3. 명시적 실행 방법

저장소 루트의 Python 3.12 가상환경에서 실행한다. 이 명령을 앱 시작 스크립트나 FastAPI startup에서 호출하지 않는다.

```bash
.venv/bin/python -m pip install -r requirements-db.txt
.venv/bin/python -m pip check
.venv/bin/python -m alembic -c backend/migrations/alembic.ini heads
```

C02 완료 시 heads의 결과는 **M001 (head)**였다. 이후 C03에서 M002를 추가했다. 아래 적용 예시는 C02의 M001에 고정하며 전체 계획의 M008까지 완성했다는 뜻은 아니다.

owner 설정 파일은 DB_HOST·DB_PORT·POSTGRES_DB·DB_OWNER_USER·DB_OWNER_PASSWORD·DB_APP_USER를 포함한다. 명시한 파일의 리터럴 값을 읽으며 이미 export한 환경변수가 우선한다. DB_APP_PASSWORD나 관리자 비밀번호는 마이그레이션 연결에 사용하지 않는다. 파일 경로를 주지 않으면 명시적으로 export한 설정만 읽는다. alembic.ini에는 URL/비밀을 저장하지 않는다.

```bash
# 실제 적용 전 검토할 SQL 출력: DB에 연결하지 않는다.
CC_MIGRATION_ENV_FILE=.env.owner-test .venv/bin/python -m alembic -c backend/migrations/alembic.ini upgrade M001 --sql

# 명시한 owner 대상의 현재 이력 확인
CC_MIGRATION_ENV_FILE=.env.owner-test .venv/bin/python -m alembic -c backend/migrations/alembic.ini current

# 준비한 빈 격리 DB에 명시적으로 적용
CC_MIGRATION_ENV_FILE=.env.owner-test .venv/bin/python -m alembic -c backend/migrations/alembic.ini upgrade M001
```

이번 작업에서는 일상 .env의 업무 DB에 upgrade를 실행하지 않았다. 아래 통합 테스트가 생성한 별도 UUID DB에만 실제 M001을 적용했다. 운영·일상 DB 적용은 전체 스키마와 후속 권한/검증이 준비된 뒤 전환 절차를 따른다.

## 4. 실제 검증 결과

기본 테스트와 전용 DB 설정은 [C01 실행 방법](구현_C01_DB연결과트랜잭션.md#4-전용-postgresql-테스트-실행)을 따른다.

```bash
.venv/bin/python -m pytest -q
TEST_DB_ENV_FILE=.env.db-test .venv/bin/python -m pytest -m db_integration -q
```

- **기본 Python 테스트 404 passed**, 추가 subtest 7 passed, 실제 DB 46개는 기본 실행에서 제외했다. 신규 C02 설정/metadata/SQL 사례는 17개다.
- **실제 PostgreSQL 46 passed**: 기존 C01 8개 + C02 38개. 실제 inventory·metadata·자료형·NULL/default·PK/UQ/CHECK 이름·코멘트를 대조했다. 초기 4개 업무 테이블은 빈 상태로 생성됐고 seed는 없다.
- 정상 이메일/developer·세션·중복 프로젝트명·기본 역할은 허용했다. 잘못된 계정 종류/조건부 NULL/이메일 정규화·해시·시각·JSON 배열·상태·역할·중복 키는 거부했다.
- 전역/스키마별 app/PUBLIC 기본 GRANT를 테스트 DB에 먼저 재현한 뒤 회수를 검증했다. 이후 생성하는 테스트용 테이블·시퀀스에도 권한이 자동 부여되지 않았으며 테스트 probe DDL은 rollback했다.
- M001 마지막에 의도적인 오류를 주입한 복사본으로 **4개 테이블·버전 이력·기본 ACL 변경이 함께 롤백**되는 것을 확인했다. 원본 revision은 수정하지 않았다. 기존 public 테이블이 있으면 이를 보존한 채 초기 적용이 거부됐다.
- 동일 head 재실행·app 역할의 마이그레이션 거부·파괴적 downgrade 거부·테스트 DB 정리와 일상 DB의 public 관계/기본 ACL 불변을 확인했다. macOS Python 3.12.14와 기존 PostgreSQL 17.11에서 실행했고 pip check도 통과했다.

[실행 검증 기록](verification/2026-10-17-implementation-C02.json)에 실제 결과와 파일 지문을 저장한다. 이전 단계의 JSON은 당시 검증 이력으로 보존한다. Linux/Windows 실실행·실제 네트워크 COMMIT 장애 주입·운영 전환은 이번 검증 범위가 아니다.

## 5. 아직 적용하지 않은 항목과 다음 작업

M001은 컬럼·PK/UQ/CHECK까지만 생성한다. FK는 M006/C06, developer 최대 1행의 부분 유일 인덱스와 일반 인덱스·account_kind 변경 금지/마지막 owner 등 교차 행 가드는 M007/C07, app의 최종 컬럼 권한은 M008/C07c에서 추가한다. 중간 schema를 v1 업무 쓰기에 공개하지 않는다.

이메일 로그인·DB 세션·프로젝트 권한 API·개발 계정 초기화는 C08이며 지금 `admin/admin` 동작이나 기존 JSON 검수 흐름을 바꾸지 않았다. [C03 — 파일 metadata/M002](구현_C03_파일스키마와M002.md)도 완료했다. [C04 — 규격·인계·OCR 스키마/M003](구현_C04_OCR스키마와M003.md)도 완료했다. [C05 — 검수 기준 스키마/M004](구현_C05_검수기준스키마와M004.md)도 완료했다. [C06 — 실행·결과와 전체 FK](구현_C06_실행결과와전체FK.md)도 완료했다. [C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md)의 초안 구현·격리 DB 검증도 완료했다. [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)의 가드와 정식 M007도 구현·검증했다. [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했다. 정식 head는 M008이다. [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했다. [C09 — 파일 저장과 기준 확정](구현_C09_파일저장과기준확정.md)도 완료했다. [C10 — 검수 접수와 원자적 결과](구현_C10_검수접수와원자적결과.md)도 구현·검증했다. 다음은 **C11 — React 프로젝트/초안/이력·URL 복원·근거·이메일 UI**다. 사용자가 “다음”이라고 하면 C11만 진행한다.
