# 에이전트 공통 개발 프레임워크

각 담당자가 같은 JSON 계약으로 자신의 역할을 단독 개발하고, 합성 입력으로 전체 흐름을 연결하는 개발용 기반이다. `backend/agent_framework`는 기존 제품 앱과 분리되어 있다.

**[담당자별 연결 위치·실행법 HTML](에이전트_프레임워크_가이드.html)** · [Markdown 원본](에이전트_프레임워크_가이드.md) — 역할별 수정 파일·factory·입출력·테스트 명령과 Git 기준 인수 절차를 정리했다.

**[PostgreSQL 실행·승인·worker 가이드](에이전트_프레임워크_PostgreSQL_실행가이드.html)** · [MD](에이전트_프레임워크_PostgreSQL_실행가이드.md) — 기존 M008 DB의 AF001 추가 마이그레이션과 새 API·worker 실행법이다.

**[HTML 업무 흐름 열기](flow.html)** — 노드 선택·검색·확대/이동·기본 검수/재검수/근거 확인 필요 시나리오 탐색을 제공한다. HTML 파일을 브라우저에서 열면 되며 Python이나 서버를 실행할 필요가 없다. 실제 코드의 함수·클래스 관계는 **[Graphify 코드 지도](graphify-out/graph.html)**와 [추출 진단 보고서](graphify-out/GRAPH_REPORT.md)에서 확인한다. 업무 흐름은 코드·계획을 대조해 정리한 설명 그래프이며 실행 모니터가 아니다.

브라우저에서 열 파일은 **`docs/agent-framework/flow.html`**이다. 업무 흐름의 원본은 `flow-data.json`과 빌드 전용 `scripts/templates/agent-flow.html.in`이며 `python -m scripts.build_agent_flow_html`로 단일 HTML을 재생성한다. 이전 `scripts/templates/agent-flow.html` 경로는 완성본으로 이동하는 안내 페이지다. Graphify 코드 지도는 별도 임시 환경의 공식 `graphifyy==0.9.100`으로 생성했다. 프로젝트 의존성에는 추가하지 않았다. 두 HTML은 자동 외부 자원을 로드하지 않도록 만들었으며 정적 구조·JavaScript 문법을 검사했다. 브라우저 렌더링은 미검증이다.

현재 제공하는 것은 Pydantic 계약, 역할 포트, LangGraph 개발 실행, 승인·재검수·단계 기록, 파일 접근, CLI다. 메모리 개발 도구와 별도로 **기존 M008 DB·세션 인증·CSRF를 재사용하는 PostgreSQL API 및 독립 worker**를 제공한다. API는 접수·승인·조회만 처리하고 worker가 역할을 실행한다. **Qwen·PostgreSQL/Neo4j RAG 실모델·실검색 어댑터, MCP, Neo4j projection, front-v2 및 운영 배포는 후속 범위다.** 실제 DB 적용은 명시적 AF001 마이그레이션으로 수행한다.

이번 변경과 최신 로컬 검증은 [A03 제거 정리](a03-retirement-2026-11-02.md)를 따른다. [이전 검증 기록](verification-2026-11-02.md)은 변경 전 이력이다.

제품 목표는 **PDF·Qwen3.5-4B·PostgreSQL/Neo4j 근거 검색 및 RAG·데스크톱 front-v2**다. PaddleOCR를 포함한 별도 OCR 대체 경로, 벡터 DB·임베딩·벡터 인덱스를 추가하지 않는다. 실제 어댑터 구현 범위는 [전체 계획](../planning/pdf-qwen-rag-workflow-plan-2026-11-02.md)을 따른다.

## 최신 역할 결정

**독립 A03 검색·근거 에이전트는 폐기했다.** [11월 2일 통합문서](reference/프로젝트_통합문서_20261102.html#section-014)를 따른다. 네 역할(A02·A04_A05·A06·A07)과 공통 A01·A08만 유지한다. 승인 조건 출처는 `context.freeze_approved_context`로 고정하며 이 함수는 외부 조회를 하지 않는다. PostgreSQL→Neo4j→PostgreSQL 관계 조회·RAG는 후속 공통 문맥 준비 기능이고 별도 GraphDB 에이전트 배치는 미확정이다. 구버전 JSON과 설정은 계약 2.0.0으로 교체한다.

## 설치와 첫 실행

저장소 루트에서 실행한다. Python **3.12 이상, 3.14 미만**과 `uv`가 필요하다. 의존성 정본은 루트 `pyproject.toml`·`uv.lock`이며 개별 에이전트용 requirements를 따로 만들지 않는다.

```sh
uv sync --locked --group dev
```

이 명령은 잠금 버전으로 개발 의존성을 동기화한다. 이미 설치된 `.venv`를 사용해도 되지만 잠금 파일과 다른 환경의 결과를 동일한 검증으로 표시하지 않는다.

macOS:

```sh
.venv/bin/python -m backend.agent_framework demo
.venv/bin/python -m pytest tests/agent_framework -q
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m backend.agent_framework demo
.\.venv\Scripts\python.exe -m pytest tests/agent_framework -q
```

`demo`는 **SYNTHETIC** 안내를 stderr에, 결과 JSON을 stdout에 출력한다. 조건 승인 → 검사·보고 → 수정 요청 승인 → 새 포스터 버전 → 조건 재승인 → 전체 재검수 → 최종 승인까지 두 실행을 만든다. 여기서 승인자는 스크립트가 넣은 테스트 값이며 실제 사용자의 승인 증거가 아니다.

콘솔 JSON은 Windows 리디렉션·CI의 CP1252 출력에서도 값이 보존되도록 표준 Unicode escape로 출력한다. JSON을 파싱하면 한글 원문으로 복원된다. 명시적 출력 파일은 UTF-8 한글로 저장한다.

`--output <파일>`로 JSON을 저장할 수 있다. 상위 디렉터리는 미리 존재해야 한다. 합성 참조의 파일 해시는 예제 문자열의 해시이며 실제 PDF·이미지 파일의 해시가 아니다.

## 담당별 입력과 출력

| 역할 | 호출 포트 | 입력 → 출력 | 담당 범위 |
|---|---|---|---|
| A01 | `Workflow.start/approve/get` | 실행 문맥·파일 참조·승인 → `RunSnapshot` | 공통 실행·상태 전환·계약 연결 |
| A02 | `Extractor.extract` | `ExtractRequest` → `CriteriaDraft` | PDF 전체 처리·조건·원문 출처 |
| A04·A05 | `Inspector.inspect` | `InspectRequest` → `InspectionResult` | 현재 포스터 관측·조건별 판정 초안 |
| A06 | `Validator.validate` | `ValidateRequest` → `ValidatedResult` | 전체 조건 커버리지·관측·인용·판정 검증 |
| A07 | `Reporter.report` | `ReportRequest` → `ReportDraft` | 검증된 판정의 설명·수정 요청 초안 |
| A08 | `Workflow.rereview` | 부모 실행·새 문맥·새 산출물 → 새 `RunSnapshot` | 수정 요청 승인 확인·전체 재검수 |

소유 파일은 다음처럼 나눈다.

- 공통 기반 담당: `contracts.py`, `ports.py`, `runtime.py`, `validation.py`, `api.py`, CLI, 계약 스키마, 통합 테스트.
- 개별 담당: 팀 소유 어댑터 모듈과 해당 테스트. 다른 역할의 내부 구현이나 ORM을 직접 import하지 않는다.
- A01·A08 상태 전환은 공통 기반 담당이 관리한다. DB 스키마·마이그레이션도 통합 담당 한 명이 소유하며 각 역할이 독립 테이블을 임의 생성하지 않는다.
- front-v2·기존 인증·M008·운영 worker 변경은 별도 제품 통합 작업이다.

## 한 역할만 실행하기

예제 입력·예상 출력은 `examples/agent_framework/`에 있다. 역할 입력은 `A02.json`처럼, 예상 출력은 `A02.output.json`처럼 구분한다.

```sh
.venv/bin/python -m backend.agent_framework role A04_A05 --input examples/agent_framework/A04_A05.json --synthetic --scenario fail
.venv/bin/python -m backend.agent_framework role A06 --input examples/agent_framework/A06.json --synthetic
```

Windows 실행 파일은 `.\.venv\Scripts\python.exe`이며 인자는 동일하다.

`--synthetic` 시나리오는 `pass`, `fail`, `review`, `stale`, `unavailable`다. 시나리오 효과는 해당 역할에 한정된다. 예를 들어 A06는 받은 관측을 검증하므로 `--scenario fail`만 주어 관측을 임의 변경하지 않는다. 연속 시나리오의 검증 결과는 `examples/agent_framework/scenarios/*.validated.json`에 있다.

CLI는 요청·응답 스키마와 공통 역할 검증을 적용한다. `--synthetic`, `--factory`, `--config` 중 어느 것도 지정하지 않으면 `ADAPTER_NOT_CONFIGURED`로 종료한다. 미구현 어댑터를 합성 성공으로 대체하지 않는다.

종료 코드는 정상 계약 출력 `0`, 어댑터·역할 검증 실패 `1`, 입력·파일·설정 오류 `2`다. 기계 판정 `fail/review`도 정상적인 검사 결과이므로 그것만으로 CLI 오류가 되지는 않는다. 역할 처리 오류는 `error` 안에 `FrameworkError` 계약을 담은 JSON으로 반환하며 실패 시 기존 출력 파일을 덮어쓰지 않는다. 명령 인자 사용법 오류는 stderr의 안내를 확인한다.

## 자신의 어댑터 연결

`examples/agent_framework/custom_adapter.py`는 연결 지점 예제다. 기본 상태에서는 실제 서비스를 호출하지 않고 `ADAPTER_NOT_CONFIGURED`를 발생시킨다. 메서드를 구현한 뒤 명시적으로 factory를 선택한다.

```sh
```

기존 `--factory`의 factory는 인자 없이 호출되며 `ports.Agents`를 반환해야 한다. 아래 역할별 설정의 factory는 단일 역할 어댑터를 반환한다. factory import는 로컬 Python 코드를 실행하므로 팀이 관리하는 신뢰된 모듈만 지정한다. 요청 JSON 안에서는 factory나 모듈을 지정할 수 없다.

각 메서드는 동기식 계약을 따른다. 현재 프레임워크가 async 함수·원격 호출 timeout·재시도·비밀정보 설정을 대신 구현하지 않는다. 원격 호출 결과를 알 수 없으면 `AdapterFailure(..., outcome_unknown=True)`로 반환하고 자동으로 다시 호출하지 않는다. 알려진 오류 메시지만 안전하게 전달한다.

실제 출력은 실행 증거가 있을 때만 `synthetic=False`로 표시한다. 합성 입력이나 이전 단계가 섞인 전체 실행은 계속 `synthetic=True`로 추적된다. 이 플래그 자체가 실제 모델·DB 연결을 증명하지는 않는다.

모든 역할 요청에도 `synthetic` 필드가 있다. 합성 조건 초안의 출처 묶음을 공통 함수로 고정할 때도 이 표시를 유지한다. 같은 어댑터 인스턴스는 여러 실행에서 호출될 수 있으므로 공유 상태의 스레드 안전성과 모델 동시 실행 제한은 해당 구현에서 관리한다.

## 계약에서 지켜야 할 경계

- **ID와 버전:** 프로젝트·실행·문서·조건·산출물 버전은 요청 문맥과 동일해야 한다. ID는 공통 `Identifier` 문자열 규격을 사용하며 모델이 임의 발급·변경하지 않는다. 제품 UUID 매핑은 이후 저장소 어댑터가 맡는다.
- **원문:** 인용 위치는 1부터 시작하는 페이지와 블록 ID를 사용한다. `SourceRef.source_hash`는 인용문 UTF-8 바이트의 SHA-256이며 파일 해시와 다르다. 참조 검증은 해시 정합성을 확인할 뿐 실제 PDF에 그 문장이 있다는 사실을 증명하지 않는다.
- **현재 관측:** 이미지 근거는 현재 산출물 버전·파일 해시를 가리킨다. 좌표가 있으면 정규화된 `x1,y1,x2,y2`를 사용한다. 과거 통과나 예외 승인은 현재 이미지 관측이 아니다.
- **근거:** `current_requirement`, `approved_exception`, `historical_context`를 구분한다. 예외 유효기간·철회·권한 확인은 후속 공통 문맥 준비 서비스의 책임이다. 참조 코드의 범위 검사를 실제 권한 검사로 부르지 않는다.
- **조건 전체:** 공통 근거 조회가 검사 조건을 줄이면 안 된다. A06는 누락·중복·알 수 없는 ID·근거 부족을 확인하며 잘못된 통과를 차단한다.
- **판정:** 실행 상태, 기계 판정, 사람 결정을 별도로 기록한다. A07은 A06의 판정을 변경하지 않는다. 예외 승인도 원래 기계 판정을 보존한다.
- **재검수:** 수정 요청 승인 후 새 실행·산출물 버전을 등록한다. 현재 메서드는 같은 문서·조건 버전의 초안을 재사용하고 새 실행에서 다시 승인받는다. 승인 입력·근거를 다시 고정하고 A04_A05·A06·A07을 모두 다시 실행한다. 문서나 기준 버전이 달라지면 새 `start`가 필요하다. 조건 편집 API는 이 범위에 없다.

참조 A06는 텍스트·수치 대조와 누락·근거·범위의 기초 정책을 제공한다. 객체를 잘못 센 모델을 자동으로 교정하거나 시각 관측의 정확도를 보장하지 않는다. 복잡한 조건 그룹·정책 예외·문맥 판단은 후속 구현과 평가가 필요하다.

일반 최종 승인은 `pass` 실행에만 허용한다. 개발 참조 정책의 예외 승인은 근거가 완전한 확정 `fail`만 대상으로 하며 `review`나 알 수 없는 검증 사유는 거부한다. 현재 허용 사유는 `value_mismatch`다. 새 검증 사유를 도입할 때에는 공통 담당과 예외 승인 정책도 함께 정한다.

## 개발 API 프로토콜

CLI 없이 팀별 클라이언트를 붙일 때 사용하는 **인증 없는 loopback 개발 API**다. 기존 제품 앱에는 마운트하지 않는다. API 기본 어댑터는 합성이며 health와 snapshot으로 개발 상태를 확인한다.

macOS 실행 명령:

```sh
.venv/bin/python -m uvicorn backend.agent_framework.api:create_app --factory --host 127.0.0.1 --port 8012
```

Windows PowerShell 실행 명령:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.agent_framework.api:create_app --factory --host 127.0.0.1 --port 8012
```

| 메서드·경로 | JSON 본문·응답 |
|---|---|
| `GET /api/framework/v1/health` | 개발 profile·합성 기본값·메모리 저장·인증 없음 |
| `POST /api/framework/v1/runs` | `{context, document, asset}` → `RunSnapshot` |
| `GET /api/framework/v1/runs/{run_id}` | 현재 프로세스가 보유한 실행 스냅샷 |
| `GET /api/framework/v1/runs/{run_id}/steps` | 실행 중에도 조회 가능한 역할별 `StepTimeline` |
| `POST /api/framework/v1/runs/{run_id}/approvals` | `Approval` → 갱신된 스냅샷 |
| `POST /api/framework/v1/runs/{run_id}/revisions` | `{context, asset}` → 새 실행 스냅샷 |

API를 실행하지 않아도 `examples/agent_framework/openapi.json`으로 계약을 확인할 수 있다. 실행 후에는 `/docs`와 `/openapi.json`을 사용한다. 입력 구조 오류는 422, 없는 실행은 404, 상태·승인 충돌은 409다. 단계 어댑터 실패는 반환된 스냅샷의 `failed/outcome_unknown`과 `error`로 확인한다. HTTP 성공만으로 검수 통과를 판단하지 않는다.

`actor_id`는 테스트 문맥이며 로그인·권한의 증명이 아니다. `127.0.0.1`에만 바인딩한다. Host 검사만으로 외부 노출을 안전하게 만들 수 없다. 메모리 상태를 쓰므로 다중 worker·다중 인스턴스로 실행하지 않는다.

현재 Workflow는 동기식이다. 시작·조건 승인 요청은 해당 역할 실행이 끝난 뒤 반환한다. 실행 명령용 잠금과 조회용 잠금을 분리하여 같은 실행의 GET은 어댑터 종료를 기다리지 않고 일관된 최신 스냅샷을 반환한다. 실행 중 상태는 `extracting` 또는 `reviewing`이며 실제 시작한 역할을 아래 단계 기록으로 확인한다. 제품용 비동기 작업 접수·작업 큐 API는 후속 범위다.

## 단계별 실행 기록

`RunSnapshot.step_records`와 `Workflow.get_steps(run_id)`에서 A02, A04_A05, A06, A07의 실행 기록을 확인한다. 개발 API의 `/runs/{run_id}/steps`는 원문·검사 결과 본문 없이 문맥 ID, 실행 상태, 역할별 기록을 반환한다. 기록의 `ordinal`은 해당 실행 안의 1부터 시작하는 순서다.

| 상태 | 의미 |
|---|---|
| `running` | 역할 입력 구성·공통 검증·어댑터 호출·출력 검증을 진행 중 |
| `completed` | 공통 계약과 출처 검증을 통과한 결과를 스냅샷에 반영함 |
| `failed` | 오류로 종료했으며 후속 역할을 실행하지 않음 |
| `outcome_unknown` | 외부 호출의 결과가 불명확함. 자동 재시도하지 않음 |
| `reused` | 재검수에서 부모의 A02 조건 초안을 재사용함. 새 모델 호출이 아님 |

- `recorded_at`, `started_at`, `finished_at`은 UTC 시각이고 `duration_ms`는 단조 시계로 측정한다. 사람의 승인 대기 시간은 역할 소요 시간에 포함하지 않는다. 시스템 시각이 뒤로 바뀌면 종료 시각은 시작보다 앞서지 않게 보정하며 소요 시간은 단조 시계 값을 유지한다.
- 진행 중에는 종료 시각·소요 시간·오류가 `null`이다. `reused`에는 `source_run_id`만 지정하고 호출 시각·소요 시간을 만들지 않는다.
- 오류 기록에는 제한된 형식의 코드, 공통 안내, 재시도 가능 여부, 결과 불명 여부만 담는다. 원문, 파일 바이트, 프롬프트, 공급자 예외 문자열은 넣지 않는다. `retryable=true`도 자동 재시도를 뜻하지 않는다.
- 출력 계약 위반은 단계의 구체적인 코드로 확인한다. 기존 실행 스냅샷의 상위 오류 분류 `contract_violation`은 유지한다.
- 기존 `stages`는 완료한 흐름 표시로 유지한다. A01/A08 조정과 사람 승인은 모델 역할 호출이 아니므로 단계 호출 기록을 만들지 않으며, 승인 내역은 `approvals`에 남는다. `completed`는 역할 처리 완료이며 기계 판정 `pass`나 최종 사람 승인과 다르다.
- 부모와 재검수 실행의 기록은 분리된다. 중복·오래된 승인, 중복 실행 ID 거부는 역할을 재호출하거나 기록을 추가하지 않는다.
- `synthetic`은 합성 자료의 전파 표시다. `false`나 역할 처리 완료만으로 실모델·DB 연결을 증명하지 않는다. 메모리 기록이므로 프로세스 종료 후 복구되지 않으며 시도 재개·영속 감사 로그가 아니다.

기존 CLI 데모에도 각 실행의 `step_records`가 포함된다.

```sh
.venv/bin/python -m backend.agent_framework demo
```

Windows에서는 `.\.venv\Scripts\python.exe -m backend.agent_framework demo`를 실행한다. Python에서는 같은 `Workflow` 객체의 `get_steps(run_id)`를 호출하며, API에서는 시작 요청에 사용한 실행 ID로 위 GET 경로를 조회한다. CLI 데모와 별도로 실행한 API 서버는 메모리를 공유하지 않는다. 역할 단독 `role`·`check` 명령은 Workflow 실행을 만들지 않으므로 이 이력에 기록되지 않는다.

## 저장·복구 범위

`demo`, `serve`, `check-flow`의 `Workflow`는 **메모리 개발 도구**이며 종료하면 기록이 사라진다. 새 `postgres-serve`·`worker` 경로는 동일 M008 DB의 `agent_framework` 스키마에 실행·단계·승인·작업·이벤트를 저장한다. 기존 인증과 프로젝트 권한을 재사용하며 승인 대기에는 lease를 유지하지 않는다. 자세한 실행법은 PostgreSQL 가이드를 따른다.

메모리 경로는 중복·오래된 명령을 거부한다. PostgreSQL 경로는 같은 고정 입력·승인의 재전송을 기존 기록으로 반환하고, 완료한 역할 결과를 재사용한다. 역할 시작 표시 전에만 안전한 재선점을 허용하며, 호출 가능성이 있으면 결과 불명으로 닫고 자동 재호출하지 않는다. LangGraph 자체 checkpoint와 업무 DB가 하나의 트랜잭션이라고 가정하지 않고 DB에 확정한 결과를 기준으로 재개한다.

PostgreSQL·Neo4j RAG의 실제 쿼리, projection/outbox, 검색 권한·원문 버전·freshness, 동등한 SQL fallback, MCP 전송 및 모델 실행은 각 담당자가 연결해야 한다. 원문 파일 읽기·해시 검증에는 아래 공통 파일 접근 연결부를 사용한다. 메모리 개발 명령은 기존 DB를 변경하지 않으며 PostgreSQL 명령은 명시적으로 선택한 DB의 AF001 기록을 읽고 쓴다. front-v2 통합은 후속 범위다.

## 설정한 어댑터의 전체 흐름 검증

`check-flow`는 팀 설정의 **네 역할을 모두 초기화**하고 같은 `Workflow`에서 시작 → 조건 승인 → 수정 승인 → 수정본 실행 생성 → 수정본 조건 승인 → 최종 승인 순으로 실행한다. 시작/조건 승인이 각각 추출 및 검사·보고 그래프를 실행한다. 모든 승인은 로컬 개발용 테스트 입력이며 운영 승인 권한을 대신하지 않는다.

프로젝트 루트에서 기본 합성 구성으로 확인한다.

```sh
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
```

`--output 보고서.json`으로 성공·실패 결과를 저장한다. 종료 코드는 전체 기대 일치 `0`, 실행 실패·결과 불명·기대 불일치 `1`, 잘못된 입력·설정 `2`다. 입력 검증과 factory 초기화 실패 시 기존 보고서를 유지하며 설정·사례 파일을 출력으로 덮어쓸 수 없다. 출력 폴더는 미리 생성한다.

담당자의 어댑터를 연결할 때는 자기 팀 구성 JSON의 `factory`를 지정하고, `workflow.synthetic.json`을 별도 사례 파일로 복사해 아래 값을 실제 개발 데이터에 맞춘다. 기본 합성 파일 참조를 실모델 입력으로 사용하지 않는다.

| 필드 | 값 |
|---|---|
| `id` | 개발 검사 사례 식별자 |
| `context`, `document`, `asset` | 최초 실행 문맥과 PDF·포스터 참조 |
| `revision_context`, `revision_asset` | 부모 ID를 지정한 새 실행과 새 포스터 버전 |
| `expected_initial_verdict` | 최초 검사에서 기대하는 `pass`·`fail`·`review` |
| `expected_revised_verdict` | 정상 최종 승인을 위한 `pass` |
| `change_reason` | 테스트 수정 승인 사유 |

같은 프로젝트·PDF·조건 버전을 유지하고 새 실행 ID·포스터 버전으로 재검수한다. `actor_id`와 파일 권한은 기존 인증 경계에서 주입한 카탈로그와 일치해야 한다. 사례·구성 JSON은 UTF-8 BOM을 허용하고 중복 키·잘못된 계약·버전 연결은 factory 실행 전에 거부한다. 입력에는 파일 시스템 경로나 factory를 넣지 않는다. factory를 초기화하거나 메서드를 호출할 때는 담당 코드가 모델·DB에 접속할 수 있으므로 개발 데이터와 적절한 설정으로 명시적으로 실행한다.

기대 판정·상태·사람 결정·실제 역할 순서 중 하나라도 다르거나 단계 오류가 나면 **이후 동작을 전부 건너뛴다**. 최초 `fail/review`도 선언한 기대와 같으면 수정 승인으로 이어질 수 있지만, 수정본이 `pass`가 아니면 최종 승인을 하지 않는다. 자동 재시도·예외 승인은 수행하지 않는다.

보고서에는 동작별 성공/실패/건너뜀, 실제 상태·기계 판정·테스트 사람 결정, 합성 표시, 역할별 시간·오류 기록, 입력·구성·스냅샷의 SHA-256이 포함된다. 해시는 기본값을 포함한 **검증된 JSON의 정규 직렬화**를 대상으로 하며 입력 파일 바이트의 해시가 아니다. 스냅샷 해시는 당시 시각·소요 시간도 포함하므로 반복 실행 간 같다고 기대하지 않는다. 원문·프롬프트·역할 출력 본문·승인 사유는 보고서에 넣지 않는다. `passed`는 테스트 기대 일치이며 `connection_verified`와 `production_ready`는 계속 `false`다.

기존 파일 저장 기능까지 포함한 별도 예제:

```sh
.venv/bin/python -m examples.agent_framework.file_flow_demo
```

Windows에서는 `.\.venv\Scripts\python.exe -m examples.agent_framework.file_flow_demo`를 실행한다. 이 예제는 PDF와 두 포스터를 생성하여 기존 `services.store_file`·`LocalStorage`와 격리 SQLite에 등록한다. 설정 파일의 factory로 `FileBackedExtractor`·`FileBackedInspector`를 조합하고 실제 파일·버전·해시 검증을 거쳐 **초록 표식 2개 불합격 → 3개 수정본 통과 → 테스트 최종 승인**을 확인한다. PDF는 텍스트를 직접 읽고 이미지는 정해진 위치의 픽셀을 세는 테스트 콜백이며 Qwen·Neo4j를 호출하지 않는다. 합성 근거·보고와 테스트 승인을 사용하므로 모든 실행은 `synthetic=true`다. 임시 파일과 DB는 종료 후 정리된다.

`file_flow_demo`의 factory는 예제 실행 안에서 바인딩한 임시 의존성만 사용한다. 외부 팀 설정에 그대로 복사하는 상시 서비스 factory가 아니다. 독립 담당자는 `team_adapters.py`와 파일 접근 가이드에 맞춰 자신의 인증·저장소·모델 클라이언트를 주입한다.

## 테스트·예제·스키마 갱신

```sh
.venv/bin/python -m pytest tests/agent_framework -q
.venv/bin/python -m backend.agent_framework schema
.venv/bin/python -m scripts.export_agent_framework
```

`scripts/export_agent_framework.py`는 `examples/agent_framework/` 아래 합성 입력·출력, `schemas/*.schema.json`, `openapi.json`을 함께 재생성한다. 실제 모델·DB는 실행하지 않는다. Windows에서는 `.\.venv\Scripts\python.exe -m scripts.export_agent_framework`로 실행한다.

스키마만 별도 디렉터리에 받으려면 `schema --output-dir <디렉터리>`를 사용한다. `CONTRACT_TYPES`의 각 타입에 대해 `NAME.schema.json`을 생성한다. 합성 역할 예제만 재생성하는 명령은 `python -m backend.agent_framework.adapters.generate_examples`다.

공통 회귀에서 보존할 여섯 반례 묶음:

| 반례 | 기대 동작 |
|---|---|
| 다른 프로젝트·문서·산출물 버전 | 범위 변경을 거부하고 성공 출력 차단 |
| 근거 ID·인용 해시 위조, 과거 이력의 현재 관측 사용 | 인용·근거 검증 실패 |
| 조건 누락·중복, 수량 불일치인데 통과 | 누락은 확인 필요, 확실한 위반은 유지 |
| 검색 stale/unavailable, 관측 부족 | 확인 필요를 숨기지 않고 정상 통과 차단 |
| A07 판정 변경, 승인 전 재검수·중복 승인 | 상태·판정 경계에서 거부 |
| 미설정 어댑터, 모델 호출 결과 불명 | 합성 대체·자동 재호출 없이 명시적 실패/결과 불명 |

CI에는 같은 계약·어댑터·CLI·runtime·API 테스트를 연결한다. 테스트 결과는 합성/계약 검증이며 실모델 정확도·실제 DB 성능·Windows 장치 검증을 대신하지 않는다. Windows 명령은 해당 플랫폼에서 실행한 증거와 별도로 관리한다.

## 계약 변경 규칙

1. 변경 제안에 영향받는 역할·필드·예제·소비자를 적고 공통 기반 담당과 먼저 합의한다.
2. `contract_version`을 관리한다. 필드 제거·이름/의미/타입 변경은 호환되지 않는 변경으로 취급하고 이전 소비자 처리 방안을 함께 정한다.
3. 계약을 바꿀 때 JSON Schema, 입력·출력 fixture, 반례, 문서·API 명세를 같이 갱신한다. 생성 파일만 수작업으로 고치지 않는다.
4. 자기 역할 단독 테스트와 전체 합성 승인·재검수 테스트를 모두 통과시킨다. 인터페이스만 맞는다고 제품 통합 완료로 보고하지 않는다.
5. 제품의 인증·파일 접근·M008·영속 실행기에 연결할 때에는 공통 통합 담당이 마이그레이션·권한·실행 소유권을 조정한다. 운영 데이터 변경은 이 개발 프레임워크 범위 밖이다.

## 팀 공유 · macOS / Windows

Git으로 받은 팀원은 **[공유 시작 페이지](index.html)**를 Chrome에서 연다. HTML만 열람할 때 Python, 서버, 가상환경은 필요하지 않다. [브라우저용 개발 안내](guide.html)에 두 OS의 실행 명령이 있다.

공유 기준은 Git 저장소다. `share/에이전트_흐름도`와 기존 ZIP은 이전 공유 사본이며 현재 작업의 재배포 대상으로 갱신하지 않는다. 팀원은 루트의 코드·예제·테스트와 `docs/agent-framework`를 사용한다. 문서는 저장소 상대 경로로 연결하며 실제 Windows 장치 실행·렌더링은 별도 검증 대상이다.

코드 근거는 `source.html?file=저장소상대경로#L행번호`로 열어 브라우저에서 해당 행을 표시한다. 내장된 소스 스냅샷이므로 파일 변경 후 아래 명령으로 갱신한다. 전체 계획의 작성자 로컬 채팅은 링크 대신 출처 ID로 남겼다.

```sh
python -m scripts.build_agent_flow_html
python -m scripts.build_agent_handoff
python -c "from scripts.package_agent_framework import build_pages; build_pages()"
```

위 명령은 표준 라이브러리로 업무 흐름·코드 뷰어·담당자 가이드·시작 페이지를 재생성하며 ZIP을 만들지 않는다. `scripts.package_agent_framework`의 전체 실행은 기존 ZIP 제작 기능이므로 현재 Git 공유 절차에 사용하지 않는다. `.env`, 가상환경, 모델 가중치, 업로드·운영 DB 자료는 문서 공유 대상에 포함하지 않는다.

Graphify 관계 지도는 현재 코드의 AST 스냅샷이다. 코드 구조가 바뀌면 별도 환경의 `scripts/build_agent_code_graph.py`로 갱신한다. 원본 문서는 `docs/agent-framework`, 전체 계획은 `docs/planning`에서 관리한다.

## 역할별 팀 설정으로 개발하기

개발 정본은 저장소 루트의 `backend/agent_framework`, `examples/agent_framework`, `tests/agent_framework`, `docs/agent-framework`다. Git에 있는 `share/에이전트_흐름도`와 ZIP은 이전 공유 시점의 사본이다. 사본의 코드를 따로 수정하지 않고 루트 정본을 수정·검증하여 Git으로 공유한다.

각 담당자가 어댑터 하나만 반환하는 factory를 제공하면 공통 설정이 네 역할을 조합한다. A01·A08 실행 조정과 승인 상태는 공통 실행기가 계속 소유한다. 실제 모델·검색·DB·front-v2 제품 연결은 이 설정 기능만으로 구현되지 않는다.

| 설정 파일 | 용도 | 기본 역할 구성 |
|---|---|---|
| `examples/agent_framework/team_config.template.json` | 팀이 복사해 연결 위치를 채울 시작점 | A02·A04_A05·A07은 `unconfigured`, A06은 `reference` |
| `examples/agent_framework/team_config.synthetic.json` | 외부 연결 없이 명시적으로 선택하는 합성 구성 | A02·A04_A05·A07은 `synthetic`, A06은 `reference` |

설정의 `roles`는 `A02`, `A04_A05`, `A06`, `A07`을 빠짐없이 선언한다. `kind`는 `unconfigured`, `synthetic`, `reference`, `factory` 중 하나다. `reference`는 A06에만 허용한다. `factory` 항목은 `kind: factory`일 때만, `scenario` 항목은 `kind: synthetic`일 때만 사용한다. 합성 시나리오는 `pass`, `fail`, `review`, `stale`, `unavailable`이고 생략 시 `pass`다. 설정의 개발 프로필은 `development` 또는 `test`이며 운영 프로필은 없다.

팀 설정의 `roles.A04_A05`만 교체하는 예시는 다음과 같다. 나머지 세 역할의 선언도 설정 파일에 유지해야 한다.

```json
{
  "kind": "factory",
  "factory": "examples.agent_framework.team_adapters:create_inspector"
}
```

`examples/agent_framework/team_adapters.py`에 `create_extractor`, `create_inspector`, `create_reporter`를 각각 분리했다. 이들은 인자 없는 동기 함수이며 **해당 역할 어댑터 인스턴스 하나**를 반환한다. 선택적으로 `create_validator`를 사용하면 참조 `ReferenceValidator`를 반환한다. 현재 모델·검색 클래스는 미구현 stub이므로 메서드 호출 시 `ADAPTER_NOT_CONFIGURED`로 실패한다. 정상 생성이나 설정 검사 통과가 실제 연결 성공을 뜻하지 않으며 합성 결과로 대체하지 않는다.

기존 `--factory examples.agent_framework.custom_adapter:make_agents`도 유지한다. 이는 `ports.Agents` 전체를 반환하는 기존 경로이고, 설정 파일의 역할별 factory와 반환 규격이 다르다. 요청 JSON에는 factory를 넣지 않는다. 설정은 개발자가 지정한 로컬 파일이며 업무 요청으로 교체하지 않는다.

### 설정 검사, 역할 실행, 개발 서버

저장소 루트에서 실행한다. 아래는 macOS 명령이고 Windows PowerShell에서는 실행 파일만 `.\.venv\Scripts\python.exe`로 바꾸며 나머지 인자는 동일하다.

```sh
.venv/bin/python -m backend.agent_framework doctor --config examples/agent_framework/team_config.synthetic.json
.venv/bin/python -m backend.agent_framework serve --config examples/agent_framework/team_config.synthetic.json --port 8012
```

Windows PowerShell의 같은 역할 실행 예:

```powershell
```

- `doctor`는 JSON 구문·계약·역할 구성을 검사하고 선언 내용을 출력한다. 설정된 모듈을 import하거나 factory·역할 메서드를 호출하지 않는다. 파일명에 적은 모듈의 존재, 실제 메서드, 모델·DB 연결은 검증하지 않는다.
- `role --config`는 선택한 역할만 생성하고 호출한다. 다른 역할의 factory는 import·실행하지 않으므로 팀원은 자기 역할만 독립 개발할 수 있다. 공통 호출 경계에서 CLI와 실행기에 같은 요청·응답 검증을 적용한다. 요청 안의 조건·근거·관측 등 중첩 데이터가 합성이면 전체 결과에도 합성 표시가 전파된다.
- `serve --config`는 같은 설정 파일로 네 역할을 조합해 개발 API를 연다. 서버 시작만으로 검수 실행·승인을 만들지 않는다. `127.0.0.1`, 단일 worker, 인증 없음, 메모리 저장 범위이며 종료하면 실행 기록이 사라진다. 구성 조회는 `GET /api/framework/v1/configuration`에서 선언과 초기화 상태를 구분해 확인한다. `connection_verified: false`는 실제 외부 연결을 검증하지 않았다는 뜻이다.

팀 연결을 시작할 때는 `team_config.template.json`을 복사하고 담당 역할만 `factory`로 바꾼다. 합성 구성은 개발 테스트를 위해 명시적으로 선택하며 미구현 역할의 자동 대체용이 아니다. 실제 연결에 필요한 비밀정보는 팀의 별도 설정 방식으로 관리하고 공유 JSON에 넣지 않는다.

### factory와 어댑터 구현 경계

factory 모듈 import와 생성자는 연결 정보만 준비하고 네트워크·DB 접속·모델 로드를 하지 않도록 작성한다. 클라이언트가 필요하면 역할 메서드에서 처음 사용할 때 생성한다. `doctor`는 개발 코드를 실행하지 않지만 `role`과 `serve`는 신뢰된 로컬 factory 코드를 실행한다. 사용자 작성 factory의 부작용까지 공통 프레임워크가 차단하는 것은 아니다.

서버의 어댑터 인스턴스는 여러 실행이 공유할 수 있다. 지연 생성 클라이언트와 변경 가능한 상태의 동시 접근, 모델 호출 제한, timeout, 오류 분류는 역할 담당자가 관리한다. 실제 호출 결과가 불명확하면 `AdapterFailure(..., outcome_unknown=True)`로 보고하며 합성 성공이나 자동 재호출로 바꾸지 않는다.

검사 대상 버전·인용·기계 판정·사람 승인은 기존 계약을 그대로 유지한다. 중첩 합성 표시가 없는 출력, 메서드 존재, 설정 초기화 성공만으로 실제 추론·검색·영속 저장·제품 통합 완료를 주장하지 않는다.

## 역할별 계약 검사 묶음 실행

`check`는 팀원이 연결한 역할 하나에 여러 요청 예제를 순서대로 넣고, 공통 계약 검증과 기대 결과를 비교한다. `doctor`는 설정만 검사하고, `role`은 단건 출력을 반환하며, `check`는 **검사 묶음의 요약 보고서**를 반환한다. 설정 파일의 다른 역할은 초기화하지 않는다.

macOS에서 A06의 통과·불합격·확인 필요·오래된 검색·검색 불가 예제 다섯 개를 검사한다.

```sh
.venv/bin/python -m backend.agent_framework check A06 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A06.suite.json
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m backend.agent_framework check A06 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A06.suite.json
```

자기 역할을 연결할 때는 `team_config.template.json`을 복사해 해당 역할의 factory를 지정하고, `checks/A04_A05.suite.json` 같은 예제 묶음을 복사해 실제 개발 입력으로 바꾼다. **기본 fixture의 PDF·포스터는 합성 참조이므로 실제 미디어 파일을 읽는 어댑터의 실행 입력으로 사용할 수 없다.** 파일·모델·DB 접근은 연결한 어댑터가 수행하며, 프레임워크는 대신 접속하거나 성공 결과로 대체하지 않는다.

묶음 파일 형식:

```json
{
  "contract_version": "2.0.0",
  "role": "A06",
  "cases": [
    {"id": "normal", "input": "inputs/normal.json", "expected_verdict": "pass"},
    {"id": "violation", "input": "inputs/violation.json", "expected_verdict": "fail"},
    {"id": "wrong-scope", "input": "inputs/wrong-scope.json", "expected_error": "CONTEXT_MISMATCH"}
  ]
}
```

- 입력 경로는 실행 위치가 아닌 **묶음 JSON 파일이 있는 폴더 기준**이다. `/` 구분자를 사용하며 절대 경로·Windows 드라이브 경로는 넣지 않는다. UTF-8 BOM도 읽는다.
- 케이스 ID는 중복 없이 1–100개다. 전체 입력 파일의 JSON·요청 스키마를 먼저 확인한 뒤 선택한 factory를 한 번 생성한다. 잘못된 JSON·누락 파일은 `expected_error`로 성공 처리할 수 없다.
- 기대값을 생략하면 공통 계약을 통과한 출력을 기대한다. A06·A07에만 `expected_verdict`를 지정할 수 있다. 정상적인 `fail`·`review` 출력은 어댑터 오류가 아니다.
- `expected_error`는 구조화된 `AdapterFailure` 또는 공통 호출 경계의 정확한 오류 코드와 비교한다. 임의 예외의 상세 메시지는 보고서에 내보내지 않으며 정상적인 기대 오류로도 처리하지 않는다.
- 각 케이스는 한 번만 호출한다. `retryable=true`여도 재호출하지 않는다. `outcome_unknown=true`이면 해당 케이스는 실패하고 남은 케이스는 `skipped`로 남긴다.
- 보고서에는 사례별 통과/실패/건너뜀, 입력 파일 해시, 계약을 통과한 출력의 해시·합성 표시·판정 또는 검색 상태를 남긴다. 원문·프롬프트·모델 출력 전체는 포함하지 않는다.
- 최상위 `passed`는 선언한 기대와의 일치 여부다. `summary.contract_outputs_checked`는 실제로 계약 검증을 통과한 출력 수이며, 예상한 오류만 확인한 경우에는 0일 수 있다. 실제 모델 정확도·근거 진위·DB 연결·승인 검증을 뜻하지 않는다.
- 종료 코드는 모두 기대 일치 `0`, 불일치/역할 실패/결과 불명 `1`, 입력·설정 오류 `2`다. `--output <파일>`은 실패 결과도 보고서로 저장한다. 입력 확인·초기화 실패 때는 기존 보고서를 유지하며, 설정·묶음·입력 파일 자체를 출력으로 덮어쓸 수 없다.

기본 제공 묶음은 A02 2개, A04/A05 2개, A06 5개, A07 6개로 총 15개 사례다. 범위가 잘못된 입력 거부도 포함한다. 묶음과 스키마는 `python -m scripts.export_agent_framework`로 재생성하며, 검사 스키마만 확인하려면 `python -m backend.agent_framework schema --include-checks`를 사용한다.

A07 단독 호출도 근거의 프로젝트·현재 문서·예외 산출물 범위, 인용 해시, 중복 ID, 검색 revision 모순을 보고서 어댑터 호출 전에 검사한다. `ReportRequest`에는 전체 조건 원문·필수 여부·현재 관측이 없으므로 A06의 전체 판단을 다시 검증하지 않는다. 실제 원문 진위·권한·예외 승인 유효성은 각 실서비스 구현의 책임이다.

## 기존 저장소의 PDF·포스터 읽기

`ports.FileAccess`는 `read_document(context, DocumentRef)`와 `read_asset(context, AssetRef)`를 제공한다. 구현인 `VerifiedFileAccess`에는 **권한·버전 메타데이터 조회 카탈로그**와 **기존 저장소 바이트 리더**를 주입한다. 새로운 업로드 디렉터리, 운영 테이블 또는 마이그레이션을 만들지 않는다. 생성자는 I/O를 수행하지 않는다.

| 기존 경로 | 재사용하는 정보·코드 | 지원 범위 |
|---|---|---|
| `backend/creative_checker` | `cc_documents`, `cc_asset_versions`, `cc_files`, `object_access/project_access`, 기존 `LocalStorage/S3Storage.read` | PDF와 PNG/JPEG/WebP 포스터 |
| `backend/app` M008 | `asset_versions`, `stored_files`, `Authorization.resource`, 기존 `LocalStorage.verified` | 포스터 원본 또는 표시 이미지 |

M008의 PDF 추출 API는 PDF 바이트를 추출기에 전달하지만 문서 버전·파일로 보존하는 경로가 아니다. 따라서 `LegacyPosterCatalog.lookup_document`는 `FILE_SOURCE_UNSUPPORTED`로 거부한다. 별도 `creative_checker` 경로의 PDF 기록을 M008 기록으로 가장하거나 두 DB의 ID를 임의로 연결하지 않는다.

재구축 경로 연결 예:

```python
from backend.agent_framework.files import VerifiedFileAccess
from backend.agent_framework.adapters.project_files import (
    CreativeCheckerCatalog, CreativeCheckerStorageReader,
)
from backend.agent_framework.adapters.file_backed import FileBackedExtractor, FileBackedInspector

# 기존 앱이 소유한 sessions, storage, 인증이 확인된 identity를 주입한다.
files = VerifiedFileAccess(
    CreativeCheckerCatalog(sessions, actor_id=authenticated_user.id),
    CreativeCheckerStorageReader(storage),
)
extractor = FileBackedExtractor(files, extract_with_qwen)
inspector = FileBackedInspector(files, inspect_with_qwen)
```

`extract_with_qwen(request, verified_document)`와 `inspect_with_qwen(request, verified_poster)`는 담당자가 구현할 동기 콜백이다. 검증된 `.content` 바이트를 사용하고 기존 `CriteriaDraft`·`InspectionResult`를 반환한다. 파일 읽기·검증이 실패하면 콜백은 호출되지 않는다. 기존 `invoke_role`, CLI `role/check`, Workflow의 응답·출처 검증은 그대로 적용된다. 파일을 읽었다는 이유로 `synthetic` 표시를 지우지 않는다.

M008에서 기존 인증 주체·권한 서비스·engine·storage를 이미 갖고 있다면 다음을 주입한다.

```python
from backend.agent_framework.adapters.project_files import LegacyPosterCatalog, LegacyStorageReader

files = VerifiedFileAccess(
    LegacyPosterCatalog(engine, authorization, subject, developer_allowed=False),
    LegacyStorageReader(storage),
)
```

`Context.actor_id`는 요청 필드이며 로그인 증거가 아니다. 카탈로그는 외부에서 인증한 주체를 바인딩하고 요청 주체 일치와 현재 계정·프로젝트 접근을 확인한다. 재구축 연결부의 `actor_id` 주입은 세션 인증을 새로 수행하지 않으므로 **인증된 API/worker 호출 경계에서 생성**해야 한다. 서로 다른 사용자에게 하나의 고정 사용자 카탈로그를 공유하지 않는다. 프레임워크의 인증 없는 개발 API에 운영 파일 접근을 자동으로 마운트하지 않는다.

버전·파일 매핑:

- PDF의 `document_version_id`는 `cc_documents.id`다. 정수 `version`이나 `file_id`와 다르며 해당 행의 `file_id`와 요청 파일이 일치해야 한다. `page_count`는 실제 PDF로 검사한다.
- 재구축 포스터의 `asset_version_id`는 `cc_asset_versions.id`다. 치수는 기존 `media_metadata`와 동일하게 EXIF 방향 적용 후 값을 사용한다.
- M008의 `asset_version_id`는 `asset_versions.asset_version_id`다. 원본 `file_id`는 원본 해시·MIME·원본 치수를, 표시 `file_id`는 표시 PNG 해시·치수를 사용한다. 두 표현을 섞지 않는다.
- `VerifiedPoster.coordinate_space`는 `encoded`(저장 바이트의 픽셀 방향) 또는 `display`(EXIF 적용 방향)다. `encoded_width/encoded_height`, `exif_orientation`도 제공한다. `.content`는 변환하지 않은 원본 바이트이며 출력 좌표가 display라면 소비 모델도 같은 EXIF 방향을 적용해야 한다.

검증 순서는 요청 계약·문맥 → 현재 접근 권한 → 프로젝트·파일·버전 연결 → 저장된 MIME·크기·해시·치수 → 실제 바이트 크기와 SHA-256 → PDF/이미지 해독 및 페이지·치수 비교다. 인용문 해시가 아닌 **파일 전체 바이트의 SHA-256**을 비교한다. 실패 시 `AdapterFailure`로 종료하며 경로·원문·공급자 예외 메시지를 노출하지 않는다.

기본 한도는 PDF 20MiB·50페이지, 포스터 40MiB·1,000만 픽셀·한 변 8,192px이다. 포스터 40MiB는 기존 M008의 정규화 표시 PNG 한도를 수용하며 기존 업로드 제한을 완화하는 설정이 아니다. 암호화 PDF, 다중 프레임 이미지, 형식 위조, 해시·크기·페이지·치수 불일치는 거부한다. `FileLimits`로 호출자에게 더 작은 한도를 적용할 수 있다.

### 실제 파일 바이트로 확인하는 독립 예제

macOS:

```sh
.venv/bin/python -m examples.agent_framework.file_access_demo
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m examples.agent_framework.file_access_demo
```

이 예제는 생성한 실제 PDF·PNG를 **기존 `services.store_file`과 `LocalStorage`**에 저장하고, 격리된 메모리 SQLite의 기존 모델로 연결한 뒤 파일 접근 포트로 읽는다. 정상 해시·페이지·치수를 확인하고 잘못된 해시 참조를 거부한다. 임시 저장소는 실행 후 정리된다. 사용자 파일·운영 DB·`.env`를 읽거나 변경하지 않고, Qwen·Neo4j를 호출하지 않는다. 생성 파일과 테스트 사용자이므로 결과를 실모델 평가·제품 인증 검증으로 해석하지 않는다.

M008의 기존 로컬 저장소는 POSIX 디렉터리 기능을 사용하므로 해당 저장소 실행은 macOS/Linux 대상이다. Windows용 새 저장소를 만들지 않았으며, Windows에서는 재구축의 기존 LocalStorage 기반 예제를 사용한다. 실제 Windows 실행은 별도 검증 대상이다.
