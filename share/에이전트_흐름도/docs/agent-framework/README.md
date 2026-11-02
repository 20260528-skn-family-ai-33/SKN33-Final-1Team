# 에이전트 공통 개발 프레임워크

각 담당자가 같은 JSON 계약으로 자신의 역할을 단독 개발하고, 합성 입력으로 전체 흐름을 연결하는 개발용 기반이다. `backend/agent_framework`는 기존 제품 앱과 분리되어 있다.

**[HTML 업무 흐름 열기](flow.html)** — 노드 선택·검색·확대/이동·기본 검수/재검수/검색 장애 시나리오 탐색을 제공한다. HTML 파일을 브라우저에서 열면 되며 Python이나 서버를 실행할 필요가 없다. 실제 코드의 함수·클래스 관계는 **[Graphify 코드 지도](graphify-out/graph.html)**와 [추출 진단 보고서](graphify-out/GRAPH_REPORT.md)에서 확인한다. 업무 흐름은 코드·계획을 대조해 정리한 설명 그래프이며 실행 모니터가 아니다.

브라우저에서 열 파일은 **`docs/agent-framework/flow.html`**이다. 업무 흐름의 원본은 `flow-data.json`과 빌드 전용 `scripts/templates/agent-flow.html.in`이며 `python -m scripts.build_agent_flow_html`로 단일 HTML을 재생성한다. 이전 `scripts/templates/agent-flow.html` 경로는 완성본으로 이동하는 안내 페이지다. Graphify 코드 지도는 별도 임시 환경의 공식 `graphifyy==0.9.100`으로 생성했다. 프로젝트 의존성에는 추가하지 않았다. 두 HTML은 자동 외부 자원을 로드하지 않도록 만들었으며 정적 구조·JavaScript 문법을 검사했다. 브라우저 렌더링은 미검증이다.

현재 제공하는 것은 Pydantic 계약, 교체 가능한 역할 포트, 실제 LangGraph 단계 실행, 메모리 내 승인·재검수 상태, 참조 검증기, 합성 어댑터, CLI 및 개발 API다. **실제 PDF·포스터 읽기, Qwen 추론, PostgreSQL·Neo4j 조회, MCP 통신, 영속 worker, 인증, front-v2 연결은 구현하지 않았다.**

현재 로컬 검증 결과와 범위는 [검증 기록](verification-2026-11-02.md)에 정리했다.

제품 목표는 **PDF·Qwen3.5-4B·PostgreSQL/Neo4j 근거 검색 및 RAG·데스크톱 front-v2**다. PaddleOCR를 포함한 별도 OCR 대체 경로, 벡터 DB·임베딩·벡터 인덱스를 추가하지 않는다. 실제 어댑터 구현 범위는 [전체 계획](../planning/pdf-qwen-rag-workflow-plan-2026-11-02.md)을 따른다.

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

`--output <파일>`로 JSON을 저장할 수 있다. 상위 디렉터리는 미리 존재해야 한다. 합성 참조의 파일 해시는 예제 문자열의 해시이며 실제 PDF·이미지 파일의 해시가 아니다.

## 담당별 입력과 출력

| 역할 | 호출 포트 | 입력 → 출력 | 담당 범위 |
|---|---|---|---|
| A01 | `Workflow.start/approve/get` | 실행 문맥·파일 참조·승인 → `RunSnapshot` | 공통 실행·상태 전환·계약 연결 |
| A02 | `Extractor.extract` | `ExtractRequest` → `CriteriaDraft` | PDF 전체 처리·조건·원문 출처 |
| A03 | `Retriever.retrieve` | `RetrieveRequest` → `EvidenceBundle` | 승인 기준·원문·예외·이력 검색 |
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
.venv/bin/python -m backend.agent_framework role A03 --input examples/agent_framework/A03.json --synthetic
.venv/bin/python -m backend.agent_framework role A04_A05 --input examples/agent_framework/A04_A05.json --synthetic --scenario fail
.venv/bin/python -m backend.agent_framework role A06 --input examples/agent_framework/A06.json --synthetic
```

Windows 실행 파일은 `.\.venv\Scripts\python.exe`이며 인자는 동일하다.

`--synthetic` 시나리오는 `pass`, `fail`, `review`, `stale`, `unavailable`다. 시나리오 효과는 해당 역할에 한정된다. 예를 들어 A06는 받은 관측을 검증하므로 `--scenario fail`만 주어 관측을 임의 변경하지 않는다. 연속 시나리오의 검증 결과는 `examples/agent_framework/scenarios/*.validated.json`에 있다.

CLI는 요청·응답 스키마와 공통 역할 검증을 적용한다. `--synthetic` 또는 `--factory`를 지정하지 않으면 `ADAPTER_NOT_CONFIGURED`로 종료한다. 미구현 어댑터를 합성 성공으로 대체하지 않는다.

종료 코드는 정상 계약 출력 `0`, 어댑터·역할 검증 실패 `1`, 입력·파일·설정 오류 `2`다. 기계 판정 `fail/review`도 정상적인 검사 결과이므로 그것만으로 CLI 오류가 되지는 않는다. 역할 처리 오류는 `error` 안에 `FrameworkError` 계약을 담은 JSON으로 반환하며 실패 시 기존 출력 파일을 덮어쓰지 않는다. 명령 인자 사용법 오류는 stderr의 안내를 확인한다.

## 자신의 어댑터 연결

`examples/agent_framework/custom_adapter.py`는 연결 지점 예제다. 기본 상태에서는 실제 서비스를 호출하지 않고 `ADAPTER_NOT_CONFIGURED`를 발생시킨다. 메서드를 구현한 뒤 명시적으로 factory를 선택한다.

```sh
.venv/bin/python -m backend.agent_framework role A03 --input examples/agent_framework/A03.json --factory examples.agent_framework.custom_adapter:make_agents
```

factory는 인자 없이 호출되며 `ports.Agents`를 반환해야 한다. factory import는 로컬 Python 코드를 실행하므로 팀이 관리하는 신뢰된 모듈만 지정한다. 요청 JSON 안에서는 factory나 모듈을 지정할 수 없다.

각 메서드는 동기식 계약을 따른다. 현재 프레임워크가 async 함수·원격 호출 timeout·재시도·비밀정보 설정을 대신 구현하지 않는다. 원격 호출 결과를 알 수 없으면 `AdapterFailure(..., outcome_unknown=True)`로 반환하고 자동으로 다시 호출하지 않는다. 알려진 오류 메시지만 안전하게 전달한다.

실제 출력은 실행 증거가 있을 때만 `synthetic=False`로 표시한다. 합성 입력이나 이전 단계가 섞인 전체 실행은 계속 `synthetic=True`로 추적된다. 이 플래그 자체가 실제 모델·DB 연결을 증명하지는 않는다.

모든 역할 요청에도 `synthetic` 필드가 있다. 합성 조건 초안에서 조건 목록만 꺼내 A03에 전달할 때도 이 표시를 유지한다. 같은 어댑터 인스턴스는 여러 실행에서 호출될 수 있으므로 공유 상태의 스레드 안전성과 모델 동시 실행 제한은 해당 구현에서 관리한다.

## 계약에서 지켜야 할 경계

- **ID와 버전:** 프로젝트·실행·문서·조건·산출물 버전은 요청 문맥과 동일해야 한다. ID는 공통 `Identifier` 문자열 규격을 사용하며 모델이 임의 발급·변경하지 않는다. 제품 UUID 매핑은 이후 저장소 어댑터가 맡는다.
- **원문:** 인용 위치는 1부터 시작하는 페이지와 블록 ID를 사용한다. `SourceRef.source_hash`는 인용문 UTF-8 바이트의 SHA-256이며 파일 해시와 다르다. 참조 검증은 해시 정합성을 확인할 뿐 실제 PDF에 그 문장이 있다는 사실을 증명하지 않는다.
- **현재 관측:** 이미지 근거는 현재 산출물 버전·파일 해시를 가리킨다. 좌표가 있으면 정규화된 `x1,y1,x2,y2`를 사용한다. 과거 통과나 예외 승인은 현재 이미지 관측이 아니다.
- **근거:** `current_requirement`, `approved_exception`, `historical_context`를 구분한다. A03의 예외 유효기간·철회·권한 확인은 실제 검색 구현의 책임이다. 참조 코드의 범위 검사를 실제 권한 검사로 부르지 않는다.
- **조건 전체:** A03 검색이 검사 조건을 줄이면 안 된다. A06는 누락·중복·알 수 없는 ID·근거 부족을 확인하며 잘못된 통과를 차단한다.
- **판정:** 실행 상태, 기계 판정, 사람 결정을 별도로 기록한다. A07은 A06의 판정을 변경하지 않는다. 예외 승인도 원래 기계 판정을 보존한다.
- **재검수:** 수정 요청 승인 후 새 실행·산출물 버전을 등록한다. 현재 메서드는 같은 문서·조건 버전의 초안을 재사용하고 새 실행에서 다시 승인받는다. A03·A04/A05·A06·A07 모두 다시 실행한다. 문서나 기준 버전이 달라지면 새 `start`가 필요하다. 조건 편집 API는 이 범위에 없다.

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
| `POST /api/framework/v1/runs/{run_id}/approvals` | `Approval` → 갱신된 스냅샷 |
| `POST /api/framework/v1/runs/{run_id}/revisions` | `{context, asset}` → 새 실행 스냅샷 |

API를 실행하지 않아도 `examples/agent_framework/openapi.json`으로 계약을 확인할 수 있다. 실행 후에는 `/docs`와 `/openapi.json`을 사용한다. 입력 구조 오류는 422, 없는 실행은 404, 상태·승인 충돌은 409다. 단계 어댑터 실패는 반환된 스냅샷의 `failed/outcome_unknown`과 `error`로 확인한다. HTTP 성공만으로 검수 통과를 판단하지 않는다.

`actor_id`는 테스트 문맥이며 로그인·권한의 증명이 아니다. `127.0.0.1`에만 바인딩한다. Host 검사만으로 외부 노출을 안전하게 만들 수 없다. 메모리 상태를 쓰므로 다중 worker·다중 인스턴스로 실행하지 않는다.

현재 Workflow는 동기식이다. 조건 승인 요청은 해당 검사·보고 단계가 끝난 뒤 반환하며, 같은 실행의 GET도 그동안 기다릴 수 있다. 제품용 비동기 접수·진행률 조회 API는 후속 연결 범위다.

## 저장·복구 범위

실행 기록은 **현재 `Workflow` 인스턴스의 메모리에만 존재**한다. 프로세스 재시작이나 새 인스턴스 생성 시 실행·승인·단계 결과가 사라진다. LangGraph의 실제 노드를 사용하지만 PostgreSQL checkpoint·lease·heartbeat·영속 재개를 연결한 것은 아니다.

같은 프로세스에서는 중복·오래된 승인과 실행 ID 충돌을 거부하고, 호출 결과 불명은 `outcome_unknown`으로 남긴다. 자동 재시도는 하지 않는다. 이 동작을 운영 DB의 멱등성·장애 복구 검증으로 확대 해석하지 않는다.

PostgreSQL·Neo4j RAG의 실제 쿼리, projection/outbox, 권한·원문 버전·freshness, 동등한 SQL fallback, 원문 파일 검증, MCP 전송 및 모델 실행은 각 담당자가 연결해야 한다. 이 프레임워크를 실행한다고 기존 DB나 front-v2가 변경되지는 않는다.

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

별도 전달용은 저장소의 `share/agent-framework-2026-11-02.zip`이다. **전체 압축을 해제한 뒤 최상위 `index.html`**을 열며, 하위 폴더 구조를 유지한다. ZIP 내부에서 HTML만 여는 방식은 연결 문서를 찾지 못할 수 있다. 한글·공백이 있는 폴더에서도 상대 경로로 연결하도록 구성했다. 실제 Windows 장치 실행·렌더링은 별도 검증 대상이다.

코드 근거는 `source.html?file=저장소상대경로#L행번호`로 열어 브라우저에서 해당 행을 표시한다. 내장된 소스 스냅샷이므로 파일 변경 후 아래 명령으로 갱신한다. 전체 계획의 작성자 로컬 채팅은 링크 대신 출처 ID로 남겼다.

```sh
python -m scripts.package_agent_framework
```

표준 라이브러리만으로 시작 페이지·안내·업무 흐름·코드 뷰어와 ZIP을 재생성한다. ZIP에는 전체 계획, Graphify 결과·라이선스, 공통 Python 소스, 합성 예제·스키마, 테스트, 저장소 정본 `pyproject.toml`·`uv.lock`이 포함된다. 전체 제품 프론트/백엔드, `.env`, 모델 가중치, `.venv`, 업로드 파일, DB, Git 이력은 포함하지 않는다. 각 파일의 해시는 ZIP 안 `MANIFEST.json`에, ZIP의 해시는 옆 `.zip.sha256` 파일에 기록한다.

Graphify 관계 지도는 현재 코드의 AST 스냅샷이다. 구조가 바뀌면 `scripts/build_agent_code_graph.py`로 이를 먼저 갱신한 뒤 공유 묶음을 만든다. 원본 문서는 `docs/agent-framework`, 전체 계획은 `docs/planning`에서 관리하고 ZIP은 재생성 가능한 배포 사본으로 다룬다.
