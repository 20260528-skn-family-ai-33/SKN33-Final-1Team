> 현재 계약은 3.0.0이다. 관측 상태·단위와 A06 상세 결과를 포함한다. [A06 계약 가이드](../../docs/agent-framework/a06-contract-v3.md)에 따라 새 예제와 설정으로 전환한다. 구버전 실행은 자동 변환·재개하지 않는다. 승인 출처는 공통 context.py에서 고정하며 외부 관계 조회는 아직 실행하지 않는다.

# 합성 에이전트 계약 예제

이 폴더의 JSON은 실제 PDF, 포스터, 모델, PostgreSQL, Neo4j 실행 결과가 아닙니다.
파일 ID와 SHA-256도 **합성 참조 문자열**을 나타냅니다. 원본 미디어 파일은 제공하지 않습니다.
모든 역할 출력의 `synthetic`은 `true`이며 모델 정확도나 배포 완료 증거로 사용할 수 없습니다.

| 역할 | 요청 JSON | 응답 JSON | 계약 |
|---|---|---|---|
| A02 조건 추출 | `A02.json` | `A02.output.json` | ExtractRequest → CriteriaDraft |
| A04/A05 통합 검사 | `A04_A05.json` | `A04_A05.output.json` | InspectRequest → InspectionResult |
| A06 코드 검증 | `A06.json` | `A06.output.json` | ValidateRequest → ValidatedResult |
| A07 보고서 | `A07.json` | `A07.output.json` | ReportRequest → ReportDraft |

기본 예제는 `MORNING CUP` 정확문구와 딸기 `3개` 조건을 사용합니다.
`scenarios/`에는 `pass`, `fail`, `review`, `stale`, `unavailable`의 A06 검증 결과가 있습니다.
`stale`과 `unavailable`에서는 합성 검사기가 충족을 주장해도 검색 근거가 불완전하므로 코드 검증이 `review`로 남깁니다.

저장소 루트에서 같은 JSON을 다시 생성할 수 있습니다. 외부 서비스에 접속하지 않습니다.

```sh
python -m backend.agent_framework.adapters.generate_examples
```

`custom_adapter.py`는 팀별 어댑터 팩터리의 연결 위치만 보여 줍니다. 구현하지 않은 메서드는
`ADAPTER_NOT_CONFIGURED`를 반환하며 합성 결과로 대체하지 않습니다. 실제 서비스를 연결할 때는
각 메서드의 입력·출력 계약과 현재 버전·출처·인용 검증을 유지해야 합니다.

## 팀별 역할 설정

- `team_config.template.json`: A02·A04_A05·A07은 미설정, A06은 참조 검증기입니다. 팀 연결용으로 복사해 사용합니다.
- `team_config.synthetic.json`: 합성 역할과 A06 참조 검증기를 명시적으로 조합합니다. 실제 모델·DB 연결을 하지 않습니다.
- `team_adapters.py`: 역할별 `create_extractor`, `create_inspector`, `create_validator`, `create_reporter` factory의 시작점입니다. 모델·검색 stub은 호출 시 `ADAPTER_NOT_CONFIGURED`로 실패합니다.

설정 파일에는 네 역할을 모두 선언하고, 역할 하나의 binding을 다음처럼 바꿉니다. 아래는 `roles.A04_A05`의 값이며 완전한 설정 파일이 아닙니다.

```json
{
  "kind": "factory",
  "factory": "examples.agent_framework.team_adapters:create_inspector"
}
```

역할별 factory는 인자 없이 **단일 역할 어댑터**를 반환합니다. 기존 `custom_adapter:make_agents`와 CLI의 `--factory`는 `Agents` 전체를 반환하는 별도 경로로 유지됩니다. factory import·생성자는 부작용 없이 작성하고, 클라이언트는 역할 메서드에서 필요할 때 초기화합니다. 여러 실행이 인스턴스를 공유할 수 있으므로 클라이언트 초기화·호출 동시 접근도 담당자가 관리합니다. 미구현·연결 실패를 합성 데이터로 대체하지 않습니다.

macOS:

```sh
.venv/bin/python -m backend.agent_framework doctor --config examples/agent_framework/team_config.synthetic.json
.venv/bin/python -m backend.agent_framework role A04_A05 --input examples/agent_framework/A04_A05.json --config examples/agent_framework/team_config.synthetic.json
.venv/bin/python -m backend.agent_framework serve --config examples/agent_framework/team_config.synthetic.json --port 8012
```

Windows PowerShell에서는 위의 실행 파일을 `.\.venv\Scripts\python.exe`로 바꾸고 동일한 인자를 사용합니다.

`doctor`는 설정 검사·설명만 하며 설정된 모듈의 import, factory·역할 호출, 접속 검증은 하지 않습니다. `role --config`는 선택한 역할만 생성합니다. `serve --config`는 동일 구성으로 개발 API를 열며 loopback `127.0.0.1`, 단일 worker, 인증 없음, 메모리 저장 범위입니다. 시작만으로 검수·승인을 실행하지 않습니다. `GET /api/framework/v1/configuration`에서 구성 선언과 초기화 상태를 확인할 수 있으며 실제 연결 검증은 별개입니다.

공통 CLI·실행기 호출은 같은 계약 검증을 적용하고 중첩 합성 입력의 표시를 결과까지 유지합니다. 자세한 소유 범위·계약 규칙은 [개발 가이드](../../docs/agent-framework/README.md)를 따릅니다. 개발 정본은 루트 `backend/agent_framework`, `docs/agent-framework`, `tests/agent_framework`이며 `share/에이전트_흐름도`는 수정 대상이 아닌 배포 사본입니다.

## 여러 예제의 계약 검사

`checks/`의 역할별 `*.suite.json`과 요청 fixture는 `python -m scripts.export_agent_framework`로 생성합니다. 네 역할에 총 17개 사례가 있으며 A06/A07의 정상 `fail`·`review` 판정과 범위 위반 거부도 구분합니다.

```sh
.venv/bin/python -m backend.agent_framework check A07 --config examples/agent_framework/team_config.synthetic.json --cases examples/agent_framework/checks/A07.suite.json
```

Windows PowerShell에서는 실행 파일을 `.\.venv\Scripts\python.exe`로 바꿉니다. 입력 경로는 suite 파일 기준의 상대 경로입니다. `--output <파일>`로 결과 요약을 저장할 수 있으며 종료 코드는 기대 일치 0, 검사 실패 1, 잘못된 입력·설정 2입니다. 결과 불명은 실패로 남기고 나머지 사례를 중단하며 자동 재시도하지 않습니다.

이 fixture에는 실제 미디어 파일이 없습니다. 실모델·검색 어댑터를 검사할 때는 자기 개발 입력과 기대값을 가진 별도 suite를 만듭니다. 보고서의 `passed`는 선언한 기대와의 일치이며 모델 정확도나 DB 연결 성공 인증이 아닙니다. 세부 규격은 [개발 가이드의 역할별 계약 검사](../../docs/agent-framework/README.md#역할별-계약-검사-묶음-실행)를 참고하세요.

## 실제 파일 바이트 연결 예제

`python -m examples.agent_framework.file_access_demo`는 실행할 때 PDF·PNG를 생성하고, 기존 `services.store_file`·`LocalStorage`와 격리 SQLite의 기존 모델로 등록·조회합니다. 실제 바이트 해시·페이지·치수 및 변경된 해시 참조 거부를 확인합니다. 생성 테스트 미디어이며 운영 DB·사용자 업로드·Qwen·Neo4j를 사용하지 않습니다.

## 단계별 실행 기록

`python -m backend.agent_framework demo`의 `runs[].step_records`에서 역할별 상태·UTC 시작/종료 시각·소요 시간을 확인합니다. 재검수의 A02는 `reused`와 부모 실행 ID로 표시되며 모델을 다시 실행한 기록이 아닙니다.

같은 `Workflow` 객체의 `get_steps(run_id)` 또는 개발 API `GET /api/framework/v1/runs/{run_id}/steps`로 실행 중인 역할도 조회할 수 있습니다. 시작·승인 POST는 여전히 동기식입니다. 오류 시 후속 역할을 실행하지 않으며 결과 불명과 일반 실패를 구분합니다. 예제는 합성 어댑터와 테스트 승인을 사용하고 기록은 메모리에만 남습니다. 별도로 실행한 CLI와 API 프로세스는 기록을 공유하지 않습니다.

## 설정한 역할의 전체 흐름 검사

```sh
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
.venv/bin/python -m examples.agent_framework.file_flow_demo
```

Windows는 실행 파일을 `.\.venv\Scripts\python.exe`로 바꿉니다. `check-flow`는 네 역할을 구성해 테스트 조건 승인→검사·보고→수정 승인→수정본 재검수→최종 승인을 확인합니다. `--output 보고서.json`으로 요약을 저장하며 전체 기대 일치 0, 실패·결과 불명·불일치 1, 입력·설정 오류 2를 반환합니다. 오류·기대 불일치 뒤에는 나머지 동작을 건너뜁니다.

`workflow.synthetic.json`은 실제 미디어가 없는 합성 참조입니다. 담당자 구성에는 별도 개발 사례의 PDF·포스터 ID·버전·해시와 기대 판정을 입력합니다. `file_flow_demo`는 생성 PDF와 실제 PNG 두 버전을 기존 저장소에 넣고, 파일 검증 후 PDF 텍스트·픽셀 테스트 콜백으로 최초 불합격→수정본 통과를 확인합니다. Qwen·Neo4j 호출은 없으며 임시 SQLite·파일은 종료 후 정리됩니다. 모든 승인은 테스트 값이고 실행 기록은 메모리에만 남습니다.

자세한 사례 계약과 보고서 규격은 [전체 흐름 검증 안내](../../docs/agent-framework/README.md#설정한-어댑터의-전체-흐름-검증)를 참고하세요.

파일 접근용 `VerifiedFileAccess`, 기존 저장소 어댑터, `FileBackedExtractor/FileBackedInspector` 연결 방법은 [개발 가이드](../../docs/agent-framework/README.md#기존-저장소의-pdf포스터-읽기)에 있습니다. 테스트 fixture의 합성 참조를 실파일 ID로 자동 변환하지 않습니다. macOS는 `.venv/bin/python`, Windows는 `.\.venv\Scripts\python.exe`로 실행합니다.
