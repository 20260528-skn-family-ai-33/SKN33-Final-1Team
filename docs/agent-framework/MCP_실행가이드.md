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
