# PostgreSQL 프레임워크 실행·승인·worker

Creative Checker · 2026-11-02 · 기존 M008 DB + 추가 스키마 AF001

2026-11-03 추가: PDF 등록에는 명시적 후속 **AF002**와 PDF 전용 저장 경로를 사용한다. [PDF 등록·버전 저장 가이드](PDF_등록_버전저장_가이드.html)를 따른다. 아래 AF001 실행·worker 명령도 유지되며 PDF 등록만으로 추출 작업이 실행되지는 않는다.

새 실행 경로는 **PostgreSQL API와 독립 worker 두 프로세스**다. API는 기존 사용자 세션·CSRF·프로젝트 권한을 검사하고 작업을 접수하며, 모델 역할은 worker에서 실행한다. 기존 `run.sh`, OCR worker, front-v2를 시작하지 않고 공통 프레임워크만 실행할 수 있다.

개발·테스트 프로필용 구현이며 현재 `production` 프로필은 허용하지 않는다. 메모리 기반 `demo`·`serve`·`check-flow`도 독립 개발 도구로 유지한다. 실제 PostgreSQL 검증과 실모델 정확도·운영 배포는 구분한다.

## 01. 무엇을 재사용하고 추가했는가

기존 PostgreSQL **동일 DB**의 M008 사용자·프로젝트·멤버십·세션·권한 검사, app Engine, 트랜잭션 처리를 사용한다. 기존 public 테이블·M008 migration head를 변경하지 않는다. 새 사용자를 자동 생성하거나 기존 이력을 복사하지 않는다.

추가 스키마 `agent_framework`에는 다음 여섯 테이블이 있다.

| 테이블 | 저장 내용 |
|---|---|
| `schema_version` | AF001 DDL 해시·구조 계약 |
| `runs` | 고정 입력·설정·공통 코드 revision, 부모 실행, 최신 전체 스냅샷 |
| `jobs` | 추출/검수 작업, 선점 token·lease·heartbeat·시도 번호 |
| `steps` | 역할 입력 해시·결과·결과 해시·실행 시각·오류 |
| `approvals` | 승인자·대상 문맥·결정·사유·서버 기록 시각 |
| `events` | 접수·선점·단계·승인·복구의 추가 전용 이벤트 |

기존 사용자·프로젝트 ID는 FK로 연결한다. PDF·조건·포스터 참조는 현재 프레임워크 계약과 입력 스냅샷으로 보존하며 이 작업이 M008에 없는 PDF 버전 테이블을 만들어주는 것은 아니다. 실제 파일·원문 검증은 담당 어댑터의 기존 `FileAccess` 연결이 수행한다. Neo4j projection/outbox 구현은 아직 포함하지 않는다.

현재 JSON 계약은 **2.0.0**, 구성 역할은 **A02·A04_A05·A06·A07 네 개**다. 독립 A03은 제거했다. `/api/framework/v1`은 HTTP 경로 버전이며 JSON 계약 버전과 별개다. 관계 조회는 후속 공통 문맥 준비 기능이고 이번 승인 입력 고정 함수는 외부 DB를 검색하지 않는다.

## 02. 설치와 설정 파일

프로젝트 루트에서 실행한다. Python 3.12 또는 3.13과 uv를 사용한다.

```sh
uv sync --locked --group dev
```

버전 정본은 `pyproject.toml`·`uv.lock`이다. 기존 인증·명시적 마이그레이션을 위해 Alembic·argon2-cffi·email-validator 의존성을 포함한다. 모델 가중치·DB 자체는 이 명령으로 설치되지 않는다.

[app 설정 예시](postgres-app.env.example)를 `.runtime/framework-app.env`로 복사해 **기존 DB의 제한된 app 계정**을 입력한다. [owner 설정 예시](postgres-owner.env.example)는 `.runtime/framework-owner.env`로 별도 준비한다. 두 경로는 새로 준비할 개인 설정이며 자동 생성·탐색하지 않는다.

app/worker 파일에는 owner·관리자 비밀번호를 넣지 않는다. owner 파일은 마이그레이션 명령에만 사용한다. 실제 비밀번호·세션·운영 입력은 Git에 넣지 않는다. 설정 파일은 명시적으로 읽으며 기존 `.env`를 변경하지 않는다. 프로세스 환경변수가 파일보다 우선하므로 런타임 셸에 owner 자격증명을 export하지 않는다.

## 03. 추가 마이그레이션 — 한 번

대상은 기존 M008 DB다. 변경 전에 팀의 기존 백업·복원 절차로 대상 DB를 확인하고 백업한다. 이 명령은 기존 DB 소유자 계정을 요구하며 public 구조·기존 권한 계약을 확인한 뒤 **추가 스키마만 한 트랜잭션으로** 적용한다.

```sh
.venv/bin/python -m backend.agent_framework migrate-postgres --env-file .runtime/framework-owner.env
.venv/bin/python -m backend.agent_framework postgres-check --env-file .runtime/framework-app.env
```

Windows는 실행 파일만 `.\.venv\Scripts\python.exe`로 바꾼다. 첫 명령은 명시적 DDL이고 두 번째는 읽기 전용 검사다. 이미 같은 AF001이 적용되어 있으면 구조를 확인하고 반환한다. 앱·worker 시작 시 자동 마이그레이션은 수행하지 않는다. 운영 DB 적용과 운영 worker 시작은 배포 작업으로 별도 수행한다.

이번 제거는 아직 운영 적용하지 않은 개발 AF001에 반영했다. 이전 해시의 AF001이 적용된 별도 환경은 readiness가 해시 불일치를 거부한다. 저장 실행을 그대로 새 코드로 재개하지 말고 명시적 마이그레이션으로 정리한다.

AF001 이후 DDL을 바꿀 때는 기존 SQL을 덮어쓰지 말고 새 revision과 명시적 후속 마이그레이션을 추가한다. 이전 M008 도구의 public 전용 백업 검증만으로 새 스키마의 복원까지 검증했다고 간주하지 않는다. 실제 배포 전 전체 DB 백업에 `agent_framework`도 포함되는지 확인한다.

## 04. API와 worker 실행

터미널 1 — 기존 세션 인증과 CSRF를 사용하는 프레임워크 API:

```sh
.venv/bin/python -m backend.agent_framework postgres-serve --env-file .runtime/framework-app.env --config examples/agent_framework/team_config.synthetic.json --port 8012
```

터미널 2 — 같은 DB·같은 팀 설정으로 worker 실행:

```sh
.venv/bin/python -m backend.agent_framework worker --env-file .runtime/framework-app.env --config examples/agent_framework/team_config.synthetic.json
```

한 번만 실행하려면 다음을 사용한다. 한 번은 실행 전체가 아니라 현재 접수된 **추출 또는 검수 한 phase**다. 조건 승인은 이후 별도 review phase를 접수한다.

```sh
.venv/bin/python -m backend.agent_framework worker --env-file .runtime/framework-app.env --config examples/agent_framework/team_config.synthetic.json --once
```

API 주소는 `http://localhost:8012`다. 현재 UI·front-v2·Swagger 화면을 제공하지 않는다. 기존 `GET /api/v1/auth/csrf`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`을 재사용한다. 기존에 발급한 활성 이메일 계정을 사용하며 신규 계정·프로젝트를 자동 생성하지 않는다. 기존 개발용 admin 흐름은 이 초기 프레임워크 저장 경로의 지원 대상이 아니다.

API는 역할 factory를 초기화하지 않는다. factory는 worker에서 초기화한다. 담당자 기능 연결 후 두 터미널의 `--config`를 동일한 팀 설정으로 바꾼다. 초기 예시는 합성 역할이므로 PostgreSQL 저장 성공을 Qwen·RAG 실연결 성공으로 해석하지 않는다.

## 05. 접수·조회·승인 API

기존 사용자 세션과 프로젝트 권한을 다시 확인한다. `Context.actor_id`는 로그인 사용자 ID와 같아야 하고, 지속 실행의 `project_id`·`actor_id`에는 기존 DB의 실제 UUID를 사용한다. 파일·버전·해시는 담당자의 개발 입력과 일치시킨다. 기본 `workflow.synthetic.json`의 문자열 프로젝트·사용자 ID를 그대로 접수할 수 없다.

| 경로 | 동작 |
|---|---|
| `GET /api/framework/v1/health` | PostgreSQL 프로필·인증 방식·API의 모델 초기화 없음 표시 |
| `POST /api/framework/v1/runs` | `{context,document,asset}`를 고정·저장하고 `202`, `queued` 반환 |
| `GET /api/framework/v1/runs/{run_id}` | 재시작 후에도 저장된 전체 스냅샷 조회 |
| `GET /api/framework/v1/runs/{run_id}/steps` | PostgreSQL 단계 기록 조회 |
| `GET /api/framework/v1/runs/{run_id}/approvals` | 승인 ID·서버 시각·승인 내용 조회 |
| `POST /api/framework/v1/runs/{run_id}/approvals` | `Approval` 저장. 조건 승인·근거 고정·검수 작업 접수를 같은 트랜잭션으로 확정 |
| `POST /api/framework/v1/runs/{run_id}/revisions` | `{context,asset}`로 새 자식 실행 생성, A02 조건 초안 재사용 |

쓰기 요청은 허용 Origin, 세션 쿠키, `X-CSRFToken`을 사용한다. 예를 들어 Python 클라이언트에서 기존 이메일 계정으로 로그인하려면 다음처럼 한다. 비밀번호는 출력하거나 소스에 넣지 않는다.

```python
import getpass
import httpx

client = httpx.Client(base_url="http://localhost:8012", headers={"Origin": "http://localhost:8012"})

def post(path, body):
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = client.post(path, json=body, headers={"X-CSRFToken": token})
    response.raise_for_status()
    return response.json()

post("/api/v1/auth/login", {"email": input("기존 이메일: "), "password": getpass.getpass()})
user = client.get("/api/v1/auth/me").json()["user"]
# 준비한 요청의 context.actor_id에는 user["id"]를 사용한다.
# project_id는 이 사용자가 접근할 수 있는 기존 프로젝트 UUID다.
```

입력·승인 문법은 [담당자 가이드](에이전트_프레임워크_가이드.html), [요청 계약](../../backend/agent_framework/contracts.py), [PostgreSQL API 코드](../../backend/agent_framework/postgres_api.py)를 참고한다. 사용자·버전·이력 상태를 맞추지 않은 승인과 정상 `pass` 전 최종 승인은 거부된다.

## 06. 실행·재개 규칙

최초 접수 → `queued` → worker의 A02 실행 → `waiting_criteria`. 조건 승인·승인 기준 출처 묶음 고정 → `queued` → A04_A05·A06·A07 → `waiting_review`. 수정 승인 → `waiting_revision`. 새 포스터 버전은 자식 실행에서 조건 재승인 후 전체 검수한다.

승인 대기에는 worker lease가 없다. 단계 결과·현재 스냅샷·완료 이벤트를 같은 트랜잭션으로 확정한다. worker는 단계 호출 중 DB 트랜잭션을 유지하지 않는다. 승인 시 고정한 기준·근거와 완료 단계는 재개 시 당시 스냅샷을 재사용한다. 별도 검색 역할은 없다.

| 상황 | 처리 |
|---|---|
| 같은 run_id·같은 고정 입력 재접수 | 기존 실행 반환. 다른 입력·설정이면 충돌 |
| 같은 승인 내용 재전송 | 기존 결과 반환. 결정 종류는 같고 내용이 다르면 충돌 |
| 같은 부모·같은 수정본 재전송 | 기존 자식 반환. 다른 두 번째 자식은 충돌 |
| 두 worker가 동시에 선점 | 잠금·token으로 한 worker만 권한 획득 |
| 역할 시작 표시 전에 worker 종료 | lease 만료 후 최대 3회까지 안전한 재선점 |
| 역할 시작 표시 뒤 종료·통신 단절 | 자동 재호출 없이 `outcome_unknown`으로 종료 |
| 완료 단계 뒤 다음 역할 전 종료 | 완료 결과를 재사용하고 남은 역할만 실행 |
| heartbeat 실패·lease 만료·권한 회수 | 이전 worker의 늦은 결과 저장 거부 |
| COMMIT 응답 유실 | 임의 재시도하지 않고 DB 기록과 lease 복구로 확인 |

복구로 결과 불명이 된 단계의 `duration_ms`는 확인할 수 없으므로 `null`이다. 종료 시각은 결과를 확인한 시각이 아니라 결과 불명으로 닫은 시각이다. 살아 있는 worker의 성공 역할 시간은 단조 시계로 측정한다.

API·worker는 같은 팀 설정 해시와 공통 코드 revision을 사용한다. 실행 중 코드를 바꾸면 기존 대기 작업을 다른 코드로 임의 재실행하지 않는다. 작업을 정리한 뒤 배포하고 새 실행 ID를 사용하거나 기록을 확인해 별도 복구 정책을 결정한다. 실제 모델 revision·프롬프트·timeout은 역할 담당자가 추가로 기록·관리한다.

worker는 역할별 LangGraph 노드를 실행하고 다음 단계 분기를 관리한다. 영속 재개 여부는 PostgreSQL에 확정된 단계 행으로 판단한다. LangGraph 자체 checkpoint를 업무 DB와 원자적으로 확정했다고 가정하지 않는다. 현재 승인 계약은 실행 문맥의 actor와 로그인 승인자가 같아야 하며, 다른 사용자에게 승인 역할을 위임하는 정책은 후속 제품 통합 범위다.

SIGTERM/SIGINT는 새 작업 선점을 멈춘다. 이미 호출 중인 동기 어댑터는 반환을 기다리므로 담당자는 호출 timeout을 설정해야 한다. 단계 사이에 중단되면 lease 만료 뒤 다른 worker가 재개한다. 결과 불명은 성공으로 바꾸거나 기록을 지우지 않고 별도 확인 후 명시적인 새 실행으로 처리한다.

## 07. 테스트와 적용 범위

일반 회귀는 운영 DB에 접속하지 않는다.

```sh
.venv/bin/python -m pytest tests/agent_framework -q
```

실제 PostgreSQL 검사는 기존 저장소의 격리 fixture를 사용한다. `TEST_DB_ALLOW_CREATE=1`과 명시적 `TEST_DB_*` 계정이 있는 테스트 전용 환경에서만 실행한다. 매번 새 무작위 `creative_checker_test_*` DB를 만들고 그 DB만 제거한다.

```sh
.venv/bin/python -m pytest tests/agent_framework/test_postgres_integration.py -m db_integration -q
```

`TEST_DB_ENV_FILE`은 비밀정보가 있는 별도 테스트 설정 파일 경로다. 현재 운영 DB를 테스트 DB로 지정하는 방식은 사용하지 않는다. PostgreSQL 17에서 M008 보존, 승인·재검수 저장, 새 API 인스턴스 복원, 독립 worker 프로세스, 중복 선점, lease 복구, 권한 회수, 결과 기록 불변성·스키마 권한 변조 거부를 검증한다.

기존 front-v2·OCR worker와 모델 구현은 이 작업에서 변경하지 않았다. 실모델·Neo4j projection·프로덕션 운영·실제 Windows 장치 검증은 별도 범위다. 문서의 마이그레이션 명령은 실제 DB를 변경하는 명령이므로 대상 확인 후 명시적으로 실행한다.
