# 담당자별 연결 위치와 실행법

Creative Checker · 공통 프레임워크 인수 가이드 · 2026-11-02

PDF·Qwen·PostgreSQL·Neo4j 기반 RAG와 승인·재검수를 담당자별로 연결하기 위한 안내다. 개인 이름 대신 역할 ID를 사용한다. 팀에서 담당자를 정한 뒤 해당 역할의 작업 범위를 가져가면 된다.

**Git 저장소의 개발 정본**을 사용한다. 코드 위치는 `backend/agent_framework`, 예제는 `examples/agent_framework`, 테스트는 `tests/agent_framework`다. 아래 경로는 모두 프로젝트 루트 기준이다. HTML 열람에는 서버·설치·인터넷이 필요 없다.

## 01. 처음 받은 사람이 할 일

Python 3.12 또는 3.13과 uv를 준비하고 `pyproject.toml`·`uv.lock`이 있는 프로젝트 루트에서 실행한다. 의존성은 두 파일을 기준으로 관리한다.

```sh
uv sync --locked --group dev
.venv/bin/python -m backend.agent_framework doctor --config examples/agent_framework/team_config.synthetic.json
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
```

Windows PowerShell은 실행 파일만 `.\.venv\Scripts\python.exe`로 바꾼다. HTML 상단의 운영체제 선택으로 모든 셸 명령을 전환할 수 있다. `.venv/bin/python -m ...`은 가상환경의 Python으로 해당 모듈을 실행하는 명령이다. 별도로 가상환경을 활성화할 필요가 없다.

`doctor`는 구성 형식을 검사하며 factory를 불러오거나 모델·DB에 접속하지 않는다. `check-flow`는 선택한 구성의 모든 역할을 초기화하고 테스트 승인을 실행한다. 위 기본 구성은 합성 어댑터다.

## 02. 역할과 수정 위치 한눈에 보기

현재 시작 코드는 [team_adapters.py](../../examples/agent_framework/team_adapters.py)에 모여 있다. 빠르게 연결하려면 **자기 역할의 클래스와 factory만** 수정한다. 협업 중 충돌을 줄이려면 아래 권장 신규 파일로 구현을 분리하고, 팀 설정에서 그 모듈의 factory를 직접 가리킨다.

| 담당 | 현재 수정할 클래스·함수 | 권장 구현 파일 — 신규 생성 | 호출 계약 |
|---|---|---|---|
| A02 PDF·Qwen | `TeamPDFExtractor.extract`, `create_extractor` | `backend/agent_framework/adapters/team_a02.py` | `ExtractRequest → CriteriaDraft` |
| A03 근거 검색·RAG | `TeamEvidenceRetriever.retrieve`, `create_retriever` | `backend/agent_framework/adapters/team_a03.py` | `RetrieveRequest → EvidenceBundle` |
| A04·A05 포스터 검사 | `TeamPosterInspector.inspect`, `create_inspector` | `backend/agent_framework/adapters/team_a04_a05.py` | `InspectRequest → InspectionResult` |
| A06 공통 검증 | `ReferenceValidator.validate` | 기존 `backend/agent_framework/validation.py` | `ValidateRequest → ValidatedResult` |
| A07 보고서 | `TeamReportWriter.report`, `create_reporter` | `backend/agent_framework/adapters/team_a07.py` | `ReportRequest → ReportDraft` |
| A01·A08 실행·재검수 | `Workflow.start/approve/get/get_steps/rereview` | 기존 `backend/agent_framework/runtime.py` | `RunSnapshot`, `StepTimeline` |

**신규 생성으로 적은 파일과 개인 역할 테스트는 아직 없다.** 현재 존재하는 시작 클래스와 공통 테스트는 이 문서의 링크에서 확인한다. 미구현 모델·검색 클래스는 `ADAPTER_NOT_CONFIGURED`로 실패하며 합성 성공으로 대체되지 않는다.

공통 계약은 [contracts.py](../../backend/agent_framework/contracts.py), 역할 인터페이스는 [ports.py](../../backend/agent_framework/ports.py), 호출·출처 검증 경계는 [invocation.py](../../backend/agent_framework/invocation.py)다. 각 역할은 다른 담당자의 내부 모델·ORM에 의존하기보다 이 계약으로 연결한다.

## 03. 팀 설정과 factory 연결

[team_config.template.json](../../examples/agent_framework/team_config.template.json)을 복사한다. 아래 대상 파일은 이 단계에서 새로 만드는 팀 설정이다. 생성 도구가 관리하는 template·synthetic 파일을 실제 접속 설정으로 바꾸지 않는다.

```sh
cp examples/agent_framework/team_config.template.json examples/agent_framework/team_config.team.json
```

설정은 다섯 역할을 모두 선언해야 한다. 자기 역할만 `factory`로 바꾸고 나머지는 template의 `unconfigured`를 유지해도 단독 `role`·`check` 명령은 실행할 수 있다. 전체 `check-flow`·개발 서버에는 모든 역할의 구성이 필요하다. A06은 `reference`를 유지한다.

예를 들어 A02의 현재 시작 코드에 연결할 항목은 다음과 같다.

```json
{
  "kind": "factory",
  "factory": "examples.agent_framework.team_adapters:create_extractor"
}
```

| 역할 | 현재 factory 참조 | 신규 파일로 분리한 경우의 참조 |
|---|---|---|
| A02 | `examples.agent_framework.team_adapters:create_extractor` | `backend.agent_framework.adapters.team_a02:create_extractor` |
| A03 | `examples.agent_framework.team_adapters:create_retriever` | `backend.agent_framework.adapters.team_a03:create_retriever` |
| A04_A05 | `examples.agent_framework.team_adapters:create_inspector` | `backend.agent_framework.adapters.team_a04_a05:create_inspector` |
| A06 | `kind: reference` | 그대로 사용 |
| A07 | `examples.agent_framework.team_adapters:create_reporter` | `backend.agent_framework.adapters.team_a07:create_reporter` |

factory는 **인자 없는 동기 함수**이며 해당 역할 어댑터 인스턴스 하나를 반환한다. 설정의 factory와 별도 CLI `--factory`는 다르다. 역할별 설정에 `Agents` 전체를 반환하면 안 된다. 네트워크 접속·모델 로드는 import 시 실행하지 않고 역할 메서드에서 필요한 시점에 수행한다. 초기화 성공을 실제 접속 성공으로 해석하지 않는다.

```sh
.venv/bin/python -m backend.agent_framework doctor --config examples/agent_framework/team_config.team.json
```

API 키·접속 비밀번호는 공유 JSON이나 Git에 넣지 않는다. 팀에서 정한 별도 로컬 설정을 사용한다. 실제 모델용 Python 패키지가 추가되면 `pyproject.toml`·`uv.lock`에 함께 반영한다. 모델 가중치·외부 서비스 설정은 이 두 파일만으로 설치되지 않으므로 담당자가 별도로 설명한다.

## 04. A02 — PDF 조건 추출

현재 수정 위치는 [team_adapters.py의 TeamPDFExtractor·create_extractor](../../examples/agent_framework/team_adapters.py)다. 분리할 경우 `team_a02.py`에 같은 factory와 동기 `extract(request)`를 구현한다. 개인 회귀 테스트는 `tests/agent_framework/test_team_a02.py`로 신규 작성한다.

입력 `ExtractRequest`의 `context`·`document`를 유지하고 `CriteriaDraft`를 반환한다. 조건별 `criterion_id`, 기대값·연산자, PDF 페이지·블록·원문 인용과 인용 해시를 제공한다. 현재 범위는 PDF다.

실제 PDF는 [FileBackedExtractor](../../backend/agent_framework/adapters/file_backed.py)에 공통 파일 접근 객체와 추출 콜백을 주입해 읽는다. 콜백은 `(request, VerifiedDocument)`를 받고 `document.content` 바이트를 사용한다. 검증된 파일 해시와 인용문 해시는 서로 다른 값이다.

**기존 합성 계약·파일 경계 확인:**

```sh
.venv/bin/python -m backend.agent_framework check A02 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A02.suite.json
.venv/bin/python -m pytest tests/agent_framework/test_files.py tests/agent_framework/test_project_files.py tests/agent_framework/test_invocation.py -q
```

완료 기준은 실제 개발 PDF에서 원문 출처를 유지한 조건을 반환하고, 다른 문서 버전·잘못된 파일 해시를 거부하는 것이다. 모델이 임의로 버전 ID를 만들거나 인용을 추측하지 않도록 한다.

## 05. A03 — PostgreSQL·Neo4j 근거 검색과 RAG

현재 수정 위치는 [TeamEvidenceRetriever·create_retriever](../../examples/agent_framework/team_adapters.py)다. 분리할 경우 `team_a03.py`에 동기 `retrieve(request)`를 구현한다. 개인 테스트는 `tests/agent_framework/test_team_a03.py`로 신규 작성한다.

입력 `RetrieveRequest`의 프로젝트·문서·조건 문맥을 유지하고 `EvidenceBundle`을 반환한다. 현재 요구조건, 적용 가능한 승인 예외, 과거 이력을 구분하고 원본 ID·인용·조건 연결을 보존한다. **벡터 DB·임베딩을 추가하지 않고 PostgreSQL·Neo4j 기반 근거 검색과 RAG를 연결한다.**

검색 결과는 `complete`, `empty`, `stale`, `unavailable`로 구분한다. Neo4j 반영 지연·장애를 정상 검색으로 숨기지 않는다. SQL 대체 조회를 구현할 경우 같은 권한·버전·의미의 조회인지 확인한다. 현재 stub이 SQL fallback이나 실제 쿼리를 제공하는 것은 아니다.

```sh
.venv/bin/python -m backend.agent_framework check A03 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A03.suite.json
.venv/bin/python -m pytest tests/agent_framework/test_invocation.py tests/agent_framework/test_validation.py tests/agent_framework/test_checks.py -q
```

완료 기준은 권한·프로젝트·원문 버전을 제한한 실제 개발 DB 조회와 명시적인 지연·장애 응답이다. 공통 합성 테스트 통과와 실제 DB 조회 증거를 구분해 제출한다.

## 06. A04·A05 — 현재 포스터 검사

현재 수정 위치는 [TeamPosterInspector·create_inspector](../../examples/agent_framework/team_adapters.py)다. 분리할 경우 `team_a04_a05.py`에 동기 `inspect(request)`를 구현한다. 개인 테스트는 `tests/agent_framework/test_team_a04_a05.py`로 신규 작성한다.

두 역할은 **현재 포트에서 A04_A05 하나로 연결**된다. `InspectRequest`의 승인 조건 전체·현재 포스터·근거 묶음을 사용하고 `InspectionResult`의 조건별 `Observation`을 반환한다. 텍스트·수량·시각 조건을 담당자 내부에서 나눠도 외부 계약은 하나를 유지한다.

[FileBackedInspector](../../backend/agent_framework/adapters/file_backed.py)의 `(request, VerifiedPoster)` 콜백을 사용하면 실제 이미지·버전·해시 검증 후 검사한다. 이미지 근거는 현재 `asset_version_id`·`sha256`을 가리킨다. `bbox`는 정규화된 `x1,y1,x2,y2`이며 EXIF와 `coordinate_space`를 맞춘다. 관측이 불확실하면 `uncertainty`를 남긴다.

```sh
.venv/bin/python -m backend.agent_framework check A04_A05 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A04_A05.suite.json
.venv/bin/python -m pytest tests/agent_framework/test_files.py tests/agent_framework/test_invocation.py tests/agent_framework/test_validation.py -q
```

완료 기준은 모든 조건의 현재 관측과 이미지·근거 연결을 반환하고, 과거 통과 기록으로 현재 포스터 관측을 대신하지 않는 것이다. 수정본은 새 버전의 이미지를 다시 읽어 전체 검사한다.

## 07. A06 — 공통 검증과 판정 집계

기본 구현은 [validation.py의 ReferenceValidator](../../backend/agent_framework/validation.py)다. 팀 설정의 `A06`을 `reference`로 두면 사용된다. 각 모델 담당자가 별도 판정 집계기를 만들 필요는 없다. 규칙 변경은 공통 담당이 수행한다.

`ValidateRequest`에서 조건 커버리지·관측·숫자 비교·인용·검색 상태를 확인하고 `ValidatedResult`를 반환한다. 누락·불확실·불완전 검색은 `review`로 유지하고 확실한 위반을 `pass`로 바꾸지 않는다.

```sh
.venv/bin/python -m backend.agent_framework check A06 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A06.suite.json
.venv/bin/python -m pytest tests/agent_framework/test_validation.py tests/agent_framework/test_runtime.py -q
```

정상 `fail/review`도 유효한 결과다. 계약·출처 위반과 불합격 판정을 구분하며, 기계 판정을 사람의 승인 상태와 합치지 않는다.

## 08. A07 — 검수 보고서와 수정 요청

현재 수정 위치는 [TeamReportWriter·create_reporter](../../examples/agent_framework/team_adapters.py)다. 분리할 경우 `team_a07.py`에 동기 `report(request)`를 구현한다. 개인 테스트는 `tests/agent_framework/test_team_a07.py`로 신규 작성한다.

`ReportRequest`의 검증된 결과와 근거를 받아 `ReportDraft`의 설명·근거 ID·수정 요청을 작성한다. A06의 판정을 그대로 유지하고 사용한 근거와 한계를 설명한다. 현재 계약에 없는 문서·이미지 경로를 추측해 다시 읽거나, A07에서 사람의 최종 승인을 만들지 않는다.

```sh
.venv/bin/python -m backend.agent_framework check A07 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A07.suite.json
.venv/bin/python -m pytest tests/agent_framework/test_report_boundary.py tests/agent_framework/test_invocation.py -q
```

완료 기준은 `pass/fail/review`와 검색 지연·장애의 한계를 보존한 보고서다. 판정 변경·다른 프로젝트 근거·허위 근거 ID를 거부해야 한다.

## 09. A01·A08 — 공통 실행 담당

기존 [runtime.py](../../backend/agent_framework/runtime.py), [composition.py](../../backend/agent_framework/composition.py), [api.py](../../backend/agent_framework/api.py), [cli.py](../../backend/agent_framework/cli.py)를 관리한다. A01·A08은 별도의 모델 factory나 설정 키를 추가하지 않는다.

A01은 역할 조합·단계 실행·승인 상태를 연결한다. A08의 현재 구현은 승인된 수정 요청에서 새 실행·산출물 버전을 만드는 `Workflow.rereview`다. 변경 영향 검색의 별도 실모델 연결을 완료한 것으로 해석하지 않는다.

```sh
.venv/bin/python -m pytest tests/agent_framework/test_runtime.py tests/agent_framework/test_step_records.py tests/agent_framework/test_flow_checks.py tests/agent_framework/test_api.py -q
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
```

단계 조회는 같은 `Workflow`의 `get_steps(run_id)` 또는 개발 API `GET /api/framework/v1/runs/{run_id}/steps`다. 단계 `completed`, 기계 `pass/fail/review`, 사람의 승인 결정은 별개다. 현재 실행·승인은 메모리에만 남으며 프로세스 재시작 후 사라진다.

## 10. 실제 파일 접근을 주입하는 방법

공통 파일 접근은 [files.py](../../backend/agent_framework/files.py)와 [project_files.py](../../backend/agent_framework/adapters/project_files.py)에 있다. 새 업로드 저장소를 만들지 않고 기존 버전 메타데이터·권한·스토리지를 재사용한다.

| 기존 경로 | 주입할 객체 | 지원 범위 |
|---|---|---|
| creative_checker | `CreativeCheckerCatalog(sessions, authenticated_user_id)` + `CreativeCheckerStorageReader(storage)` | 저장된 PDF·포스터 |
| M008 | `LegacyPosterCatalog(engine, authorization, subject)` + `LegacyStorageReader(storage)` | 포스터 원본·표시 이미지 |

M008에는 영속 PDF 버전·파일 매핑이 없어 PDF 조회는 `FILE_SOURCE_UNSUPPORTED`다. 별도 cc_* 자료를 M008 기록으로 간주하지 않는다. M008의 기존 로컬 저장소는 POSIX 기능을 사용하므로 해당 실행은 macOS/Linux 대상이다. Windows 예제는 creative_checker의 기존 LocalStorage 경로를 사용한다.

아래 코드는 **인증 경계에서 이미 만든 객체를 연결하는 구조 예시**다. sessions·storage·사용자는 호출자가 주입하며 복사만 해서 실행되는 서비스 설정이 아니다.

```python
files = VerifiedFileAccess(
    CreativeCheckerCatalog(sessions, actor_id=authenticated_user.id),
    CreativeCheckerStorageReader(storage),
)
extractor = FileBackedExtractor(files, extract_with_qwen)
inspector = FileBackedInspector(files, inspect_with_qwen)
```

실제 import와 상세 한도는 [기존 파일 접근 가이드](README.md#기존-저장소의-pdf포스터-읽기)를 참고한다. `Context.actor_id`만으로 인증하지 않는다. 인증이 확인된 주체를 카탈로그에 바인딩하고 요청 시 현재 접근 권한을 확인한다. 인증 없는 개발 API에 운영 파일 읽기를 그대로 마운트하지 않는다.

## 11. 내 어댑터의 실행·검사 순서

앞선 역할별 명령은 **현재 존재하는 합성 계약·공통 회귀 검사**다. 실제 담당 어댑터를 검사할 때는 준비한 `team_config.team.json`과 자기 개발 입력으로 바꾼다. `path/to/` 경로는 교체할 자리이며 해당 파일은 직접 준비해야 한다.

예를 들어 A02는 다음처럼 실행한다. 다른 담당자는 `A02`를 `A03`, `A04_A05`, `A06`, `A07`로 바꾼다.

```sh
.venv/bin/python -m backend.agent_framework role A02 --config examples/agent_framework/team_config.team.json --input path/to/A02.real.json
.venv/bin/python -m backend.agent_framework check A02 --config examples/agent_framework/team_config.team.json --cases path/to/A02.real.suite.json --output path/to/A02.report.json
```

입력 JSON은 역할의 요청 계약을 따른다. 여러 사례 검사 `suite`는 상대 입력 경로·기대값을 명시한다. 아래 구조를 참고하고 역할별 기존 [checks 예제 폴더](../../examples/agent_framework/checks/A02.suite.json)를 확인한다.

```json
{
  "contract_version": "1.0.0",
  "role": "A02",
  "cases": [
    {"id": "normal", "input": "A02.real.json"}
  ]
}
```

`suite`의 `input`은 suite 파일이 있는 폴더 기준이다. `expected_verdict`는 A06/A07의 `check`에서만 사용하고, `expected_error`와 동시에 지정하지 않는다. schema 입력 오류는 예상 어댑터 오류로 성공 처리할 수 없다. 실파일 역할에는 합성 참조의 파일 ID·해시를 그대로 사용하지 않는다.

모든 역할이 연결되면 개발용 전체 사례의 최초·수정본 ID와 기대 판정을 작성해 실행한다.

```sh
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.team.json --case path/to/workflow.real.json --output path/to/workflow.report.json
.venv/bin/python -m pytest tests/agent_framework -q
```

전체 사례는 [workflow.synthetic.json](../../examples/agent_framework/workflow.synthetic.json)의 필드 구조를 참고한다. 같은 프로젝트·PDF·조건 버전, 새 실행 ID·부모 ID·새 포스터 버전이 필요하다. 최초 기대 판정은 `pass/fail/review`, 수정본 기대 판정은 정상 최종 승인을 위한 `pass`다. 출력 폴더는 미리 만든다.

`check-flow`의 모든 승인은 스크립트가 넣은 테스트 입력이다. 오류·결과 불명·기대 불일치 뒤에는 후속 동작을 건너뛴다. CLI 종료 코드는 전체 기대 일치 `0`, 실행 실패·불일치 `1`, 입력·설정 오류 `2`다. `passed`는 검사 기대와의 일치이며 모델 정확도나 실제 DB 접속 성공의 자동 인증이 아니다.

## 12. 예제·인수 기준과 다음 공통 작업

다음 두 예제는 실제 바이트를 생성해 기존 파일 저장 기능을 확인한다. 운영 DB·사용자 파일은 사용하지 않는다.

```sh
.venv/bin/python -m examples.agent_framework.file_access_demo
.venv/bin/python -m examples.agent_framework.file_flow_demo
```

`file_flow_demo`는 PDF 텍스트 추출·픽셀 테스트 콜백으로 최초 불합격→수정본 통과→테스트 최종 승인을 확인한다. Qwen·Neo4j 호출은 0회이고 검색·보고는 합성이다. 이 예제의 임시 factory는 상시 서비스 설정용이 아니다.

담당자가 인수할 때는 다음 결과를 함께 남긴다.

1. 구현 모듈·factory 참조·필요 패키지·로컬 설정 방법.
2. 사용한 개발 입력·기대값·정상 출력과 대표 실패 코드. 공유 가능한 비식별 자료만 Git에 포함한다.
3. 역할별 회귀와 자기 사례 `check` 결과, 전체 연결 후 `check-flow` 보고서.
4. 실제 모델·DB 실행 증거와 합성·계약 검증 결과의 구분. 미확인 범위와 알려진 제약.

계약을 바꾸면 공통 담당과 영향 역할을 정리하고 스키마·fixture·문서·테스트를 함께 갱신한다.

```sh
.venv/bin/python -m scripts.export_agent_framework
```

계약 정본과 생성 파일을 각자 다르게 수정하지 않는다. 공통 담당이 다음으로 진행할 범위는 PostgreSQL 영속 실행·승인 기록, worker·복구·중복 실행 방지, 기존 인증·front-v2 통합이다. 역할 담당자는 현재 메모리 런타임이나 DB 스키마를 독립적으로 교체하지 않는다.

현재까지의 검증 근거는 [파일 접근 검증](file-access-verification-2026-11-02.md), [단계 기록 검증](step-records-verification-2026-11-02.md), [전체 흐름 검증](workflow-check-verification-2026-11-02.md)에 있다. 실제 Windows 실행·새 환경 설치·운영 DB·실모델 연결 완료를 문서 작성만으로 주장하지 않는다.

HTML은 `python3 -m scripts.build_agent_handoff`로 이 Markdown에서 재생성한다. Windows에서는 설치한 Python의 `python -m scripts.build_agent_handoff`를 사용한다.
