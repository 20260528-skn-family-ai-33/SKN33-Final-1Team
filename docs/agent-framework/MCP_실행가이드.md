# 에이전트 프레임워크 MCP 실행 가이드

`mcp-serve`는 에이전트용 도구를 제공하는 MCP 서버를 실제 PostgreSQL에 연결해 실행합니다. 이 서버는 시험·개발 클라이언트가 직접 호출할 수 있으며, 아직 검수 화면과 worker의 흐름에는 연결되어 있지 않습니다.

## 도구

| 묶음 | 도구 | 하는 일 |
|---|---|---|
| 실행 상태 | `get_run`, `get_steps`, `get_approval_history` | 실행 상태·단계·승인 이력 읽기 |
| 실행 요청 | `start_run`, `request_rereview` | 실행 시작·재검수 요청. 등록 포스터 실행은 HTTP API로만 받고 거절 코드를 돌려줌 |
| 근거 | `get_approved_criteria`, `get_history`, `verify_sources` | 승인 기준과 원문, 이력, 저장 기록 대조 |
| 관계 | `get_relations`, `get_change_impact` | 실행과 연결된 문서·기준·승인, 기준이 바뀔 때 영향받는 실행 |

사람이 하는 승인은 도구로 열지 않습니다. 기준 승인, 수정 요청 승인, 최종·예외 승인은 HTTP API에서만 합니다.

## 실행

토큰은 env 파일이 아니라 프로세스 환경변수로 넘깁니다. 32자 이상이어야 하며, 서버는 `127.0.0.1`에만 바인딩됩니다.

```bash
AGENT_FRAMEWORK_MCP_TOKEN=<32자 이상> python -m backend.agent_framework mcp-serve --env-file <앱 전용 env 파일> --config <팀 설정 파일>
```

- 기본 포트는 `8013`이고 `--port`로 바꿉니다.
- `--read-only`를 지정하면 실행·근거·관계 조회 도구 8개를 등록합니다.
- `--config`에는 worker와 같은 팀 설정 파일을 줍니다. 설정이 다르면 worker가 그 실행을 처리하지 않습니다.
- 설정은 그래프 프로젝터와 같은 `framework-projection` 프로필로 읽습니다. 웹 비밀값과 모델 키는 이 프로세스에 들어오지 않습니다.
- `CC_NEO4J_*`가 없어도 켜집니다. 그때 관계 도구는 PostgreSQL로 답합니다.

## 권한

토큰을 가진 쪽은 어떤 실행 번호든 물어볼 수 있으므로 토큰은 백엔드 호스트 밖으로 내보내지 않습니다. 각 호출은 세션을 빌리지 않고, 그 실행을 요청한 사람의 계정·프로젝트 권한(owner·editor)을 매번 다시 확인합니다.

## 관계 도구가 답하는 순서

Neo4j 그래프는 팀의 `graph-projector`만 씁니다. MCP는 읽기만 합니다.

1. PostgreSQL에서 권한과 그래프가 도달해야 할 revision을 확인합니다.
2. Neo4j의 revision이 같을 때만 그래프로 답합니다.
3. PostgreSQL에서 권한과 돌려줄 행을 다시 확인합니다.

그래프가 없거나, 뒤처졌거나, 꺼져 있으면 같은 답을 PostgreSQL로 계산합니다. 응답의 `answered_by`와 `warnings`로 구분합니다.

| `answered_by` | `warnings` | 뜻 |
|---|---|---|
| `neo4j` | 없음 | 현재 revision의 그래프로 답함 |
| `postgres_fallback` | `GRAPH_NOT_CONFIGURED` | Neo4j 설정 없음 |
| `postgres_fallback` | `GRAPH_NOT_PROJECTED` | 아직 투영된 적 없음 |
| `postgres_fallback` | `GRAPH_STALE` | 투영이 아직 따라오지 못함 |
| `postgres_fallback` | `GRAPH_UNAVAILABLE` 등 | 연결·스키마 문제 |

## 시험

```bash
python -m pytest tests/agent_framework/test_mcp_graph.py tests/agent_framework/test_mcp_commands.py tests/agent_framework/test_mcp_evidence.py tests/agent_framework/test_mcp_postgres.py -q
```

실제 PostgreSQL·Neo4j 시험은 Docker Linux에서 실행합니다.

```bash
python scripts/framework_graph_test.py -- tests/agent_framework/test_mcp_graph_integration.py tests/agent_framework/test_mcp_evidence_integration.py tests/agent_framework/test_mcp_postgres_integration.py -q
```

## 아직 하지 않은 것

- 검수 화면과 worker에서 이 도구를 부르는 연결(예: A01 "다음 할 일 안내" 버튼)
- 실제 Qwen이 도구를 골라 쓰는지 확인(`tests/agent_framework/test_evidence_probe_live.py`, 모델 서버 필요)
- 문서 안의 정의·상호참조를 찾는 `find_definition`

## D15 AWS_TEST 1차 연결안

2026-11-18 · 팀 검토용 · 기준 브랜치 `AWS_TEST` (`8bf32ab`)

### 도구와 권한

| AWS_TEST 내부 MCP에 노출할 읽기 도구 | 사용 범위 |
|---|---|
| `get_run`, `get_steps`, `get_approval_history` | A01 실행 안내. A08 비교에는 `get_run`, `get_steps`만 사용 |
| `get_approved_criteria`, `get_history`, `verify_sources` | 승인 기준, 이력, 원문 대조를 위한 내부 조회 |
| `get_relations`, `get_change_impact` | 관계와 기준 변경 영향 조회. 현재 Neo4j 투영 또는 PostgreSQL 응답 사용 |

AWS_TEST의 `tools/list`와 직접 호출 모두 위 8개만 허용한다. `start_run`과 `request_rereview`의 MCP 노출은 후속 안건으로 둔다. 기준 승인, 수정 요청 승인, 최종·예외 승인은 로그인한 사람이 기존 HTTP API에서 수행한다.

MCP는 별도 내부 서비스로 두고 전용 Bearer 토큰은 서버와 호출 백엔드에만 전달한다. 호출 백엔드는 먼저 로그인 사용자가 현재 프로젝트·실행을 조회할 수 있는지 확인하고, 서버가 검증한 실행 ID에 도구 호출을 한정한다. A08은 같은 프로젝트의 실제 부모·자식 쌍으로 제한한다. MCP 서버도 각 호출에서 실행 요청자의 현재 owner·editor 권한을 확인한다. PostgreSQL·Neo4j 데이터는 조회 용도로 사용하며, 서비스 계정의 권한으로 기동과 조회가 가능한지 2단계에서 시험한다. 호출 결과는 서비스 로그에서 실행 ID·도구명·성공 여부로 추적한다.

### 실제 검수 화면 연결

| 기능 | 호출 시점 | 화면에 보여줄 내용 |
|---|---|---|
| A01 실행 안내 | `queued`, `failed`, `outcome_unknown`인 실행의 상세 화면에서 사용자가 안내를 요청할 때 `RunCoordinator.assess()` 호출 | 현재 상태와 가능한 다음 조치. 결과 불명은 수동 확인을 안내 |
| A08 전후 비교 | 기존 [`/poster-revisions`](../../backend/agent_framework/postgres_api.py#L242)에서 접수된 자식 실행에 검증 결과가 저장된 뒤, 재검수 결과 화면에서 비교를 요청할 때 `RereviewAgent.compare()` 호출 | 부모와 자식 판정에서 해결된 항목, 남은 항목, 새로 발견된 위반 |
| 기존 도움말 챗봇 | 서비스 사용법 문의 | 기존 도움말 |

기능 설정의 기본값은 꺼짐이다. 기존 API가 실행 상태를 제공하고 Worker가 큐를 처리한다. MCP나 모델 호출 실패 시 기존 상태·검수 결과와 안내 또는 비교의 오류를 각각 표시한다. 모델 호출은 해당 실행의 자료 구분에 허용된 경로를 따른다. 수정본 접수와 중복 요청 처리는 [기존 포스터 서비스](../../backend/agent_framework/poster_review.py#L226)에서 수행한다.

### AWS_TEST 기동 확인 (2026-11-18)

포스터 API EC2에 전용 컨테이너 `creative-checker-aws-test-mcp-18136`을 `--read-only`로 띄웠다. 주소는 `http://127.0.0.1:18136/mcp`이고 재시작 정책은 `unless-stopped`이다. 컨테이너 루트는 읽기 전용이고 Python 임시 파일은 `/tmp` 메모리 파일시스템을 사용한다.

MCP 인증, 읽기 도구 8개, 쓰기 도구 직접 호출 거절, PostgreSQL 조회를 확인했다. 실제 AWS_TEST 포스터 API 컨테이너에서 저장된 검수 실행 한 건을 `get_run`으로 조회해 DB 상태와 대조했고 잘못된 토큰은 HTTP 401로 거절됐다. 이 배포는 포스터 API의 현재 앱 DB 설정을 사용하며, 관계 도구는 PostgreSQL로 응답한다. A01 안내와 A08 비교의 화면 호출은 다음 단계다.

### 다음 단계에서 확인할 항목

- 로그인 사용자와 실행의 프로젝트가 다를 때 A01·A08 호출이 거절되는지 화면 연결 후 확인한다.
- 실제 AWS_TEST EC2 Qwen의 도구 호출과 A01의 대기·실패·결과 불명 안내를 확인한다. 일반 상태 조회로는 모델 호출이 발생하지 않는다.
- 브라우저에서 수정 요청 승인 → 수정본 접수 → 검수 결과 저장 → A08 비교를 확인한다. 같은 접수 요청을 다시 보내도 자식 실행은 한 건이다. 기능을 끄거나 MCP 호출이 실패해도 기존 검수·승인 흐름을 사용할 수 있다.
