# PostgreSQL·Neo4j 근거 검색·RAG 연결

2026-11-04 · 공통 역할 계약 3.0.0 · 조회 정책 `af-approved-sources-v1` · AF005

## 무엇을 연결했는가

현재 `agent_framework`의 PostgreSQL 기록을 Neo4j의 `AFEntity`·`AFProjection` 이름 공간으로 투영한다. 별도 재구축 경로인 `backend/creative_checker`의 `cc_*` 업무 테이블·그래프를 원장으로 사용하지 않는다. 독립 A03, VectorDB, 새 LLM은 추가하지 않았다.

검수 worker의 A04·A05 호출 직전에 **PostgreSQL 권한·승인 기준 확인 → Neo4j 고정 질의 → PostgreSQL 원문·버전·해시 재확인 → 불변 근거 저장**을 수행한다. A04·A05와 A07은 같은 실행에 고정된 EvidenceBundle을 받는다. 모델의 관측값·기계 판정·사람 승인은 기존 역할 경계를 따른다.

1차 모델 문맥은 승인 기준의 원문과 보완 값 출처 전체다. 과거 실행·승인 관계는 조회 추적 정보이며 모델의 현재 관측·판정·승인을 대신하지 않는다. 의미 검색이나 임의 Cypher, LLM이 추정한 관계를 지원하지 않는다. 현재 기준 전체를 검색 top-k로 줄이지 않는다.

## 저장과 동기화

| AF005 테이블 | 저장 내용 |
|---|---|
| `graph_state` | 프로젝트별 요구/반영 revision, lease, 지연 재시도·오류 |
| `graph_outbox` | 업무 저장과 같은 트랜잭션의 동기화 요청·처리 시각 |
| `evidence_contexts` | 실행별 근거 묶음·해시·조회 backend·revision·대체 사유·관계 ID |

원본 M008과 기존 불변 기준·승인 기록을 유지한다. `evidence_contexts`도 app 계정에서 변경·삭제할 수 없다. 모델 본문은 PostgreSQL에서 읽으며, 그래프에는 ID·버전·해시·상태와 관계만 저장한다. 조건 키는 프로젝트·기준 버전·조건 ID로 구분한다.

문서 등록, 실행 생성, 추출/검수 단계 완료, 기준 수정·승인, 사람 결정은 outbox를 추가한다. heartbeat·선점·근거 조회 자체는 그래프 revision을 올리지 않는다. AF004 이하에서는 동기화 기록을 만들지 않는다.

projection worker는 짧은 PostgreSQL 선점 트랜잭션 후 일관된 스냅샷을 읽고 트랜잭션을 종료한 뒤 Neo4j를 호출한다. Neo4j는 프로젝트별 원자 교체와 revision 확인으로 중복·역순 전달을 방어한다. PostgreSQL 완료 표시는 전달한 revision까지만 처리하며, lease를 잃은 worker는 완료를 기록하지 못한다. 반영 응답이 유실되면 지연 재시도해 같은 revision을 확인한다.

## 실행 준비 — Mac·Windows 공통 Docker Linux

기존 `.env`, 모델·DB·볼륨을 보존한다. 명령은 저장소 루트에서 실행한다. 개인 설정은 Git에 올리지 않는다.

1. 별도 `.runtime/framework-graph-dev.env`에 `AF_NEO4J_PASSWORD`를 준비한다. 암호를 채팅이나 명령 인자에 붙이지 않는다.
2. HTTP 57431·Bolt 57631의 충돌을 확인하고 새 Neo4j만 시작한다. 아래 Compose에는 PostgreSQL 서비스가 없으며 기존 DB를 초기화하지 않는다.

```bash
docker compose --env-file .runtime/framework-graph-dev.env -f deploy/agent-framework/compose.neo4j.yaml up -d neo4j
```

3. 기존 app 전용 env를 **새 파일** `.runtime/framework-graph-app.env`로 복사하고 다음 항목을 추가한다. owner/admin 암호가 있는 파일은 app·worker에 사용하지 않는다.

```dotenv
CC_NEO4J_URI=bolt://127.0.0.1:57631
CC_NEO4J_USER=neo4j
CC_NEO4J_PASSWORD=<별도 개발용 Neo4j와 동일한 값>
```

Docker 실행기는 loopback 주소를 컨테이너용 `host.docker.internal`로 바꾸는 임시 사본을 사용한다. 원본 env는 유지한다. Neo4j 설정은 framework worker·projection에만 전달하며 웹 API 환경에서는 제거한다.

4. 새 이미지를 별도 태그로 빌드한다.

```bash
python3 scripts/framework_desktop.py build --image creative-checker-framework:neo4j-rag-20261104
```

Windows에서는 `python3` 대신 `py -3.12`를 사용한다. 뒤의 스크립트 인자는 동일하다.

5. **DB 전체 백업 후 별도 owner 설정으로 AF005를 명시적으로 적용**한다. 실제 사용 중인 설정 경로를 확인한다.

```bash
python3 scripts/framework_desktop.py migrate --image creative-checker-framework:neo4j-rag-20261104 --env-file .runtime/framework-owner.env --revision AF005
python3 scripts/framework_desktop.py check --image creative-checker-framework:neo4j-rag-20261104 --env-file .runtime/framework-graph-app.env
```

AF005는 additive migration이다. 기존 실행을 자동 변환하거나 새 runtime으로 재개하지 않는다. DB를 AF005로 올린 뒤에는 AF005를 모르는 옛 이미지로 서비스를 재시작하지 않는다. 새 이미지와 설정 적용을 함께 준비한다.

6. 그래프의 제약 조건을 명시적으로 초기화한다. 이 명령은 그래프 데이터를 삭제하지 않는다.

```bash
python3 scripts/framework_desktop.py graph-init --image creative-checker-framework:neo4j-rag-20261104 --env-file .runtime/framework-graph-app.env
```

7. 필요한 **프로젝트 한 개**를 초기 투영/재구축 요청한다. 이미 존재하던 DB 레코드도 이때 읽어 관계를 재구성한다.

Mac Bash의 `PROJECT_ID` 또는 Windows PowerShell의 `$ProjectId`에 실제 UUID를 설정한다.

```bash
python3 scripts/framework_desktop.py graph-rebuild --image creative-checker-framework:neo4j-rag-20261104 --env-file .runtime/framework-graph-app.env --project-id "$PROJECT_ID"
python3 scripts/framework_desktop.py graph-projector --image creative-checker-framework:neo4j-rag-20261104 --env-file .runtime/framework-graph-app.env --once
```

PowerShell에서는 마지막 인자를 `--project-id $ProjectId`로 쓴다. 지속 동기화는 `graph-projector`에서 `--once`를 뺀다. `synced`는 전달 성공, `deferred`는 지연 재시도, `authority_lost`는 저장 권한/lease 상실이며 단일 실행 모드에서는 후자의 두 상태가 실패 종료 코드다.

## RAG를 켜는 실행 설정

기존에 실제 어댑터를 연결한 팀 설정을 **새 파일로 복사**하고 아래 항목을 추가한다. 기존 승인 출처 고정 방식이 기본이며, opt-in은 `registered_poster` 프로필에만 허용한다.

```json
"context_preparation": {
  "contract_version": "3.0.0",
  "mode": "neo4j_rag",
  "policy": "af-approved-sources-v1"
}
```

설정은 실행 해시에 포함된다. API·worker는 동일 이미지와 팀 설정을 사용한다. A02·A04_A05·A06·A07의 역할 입출력 버전은 계속 3.0.0이다. 미설정 항목은 기존 설정 JSON에 새 null 필드를 추가하지 않는다.

`examples/agent_framework/team_config.neo4j-rag.example.json`은 **mock 추출·개발용 어댑터를 사용하는 배선 예시**이며 실모델 운영 설정이 아니다. 담당자의 실제 어댑터로 바꾸기 전 실데이터 검수에 쓰지 않는다. 포스터 프로필에는 기존 포스터 볼륨과 경로도 지정해야 한다.

동기화 worker는 모델을 호출하지 않는다. 검사 worker가 처음 승인 기준의 근거를 준비할 때만 Neo4j를 조회하고, 고정된 근거는 재개와 보고에서도 재사용한다. 승인 시의 불변 evidence_json은 수정하지 않는다.

## 조회 API

기존 M008 세션·프로젝트 권한을 적용한다. GET은 모델 호출이나 동기화 요청을 만들지 않는다.

| 경로(`/api/framework/v1` 아래) | 반환 |
|---|---|
| `/projects/{project_id}/graph-status` | 요구/반영 revision, pending/synced/error, 동기화 시각 |
| `/runs/{run_id}/evidence-context` | 저장된 EvidenceBundle·해시·조회 추적. 준비 전은 409 |
| `/projects/{project_id}/criteria-impact?criteria_version_id=…&criterion_id=c001` | 권한·기준 버전을 확인한 실행·승인·재검수 관계 |

영향 조회 API는 PostgreSQL 정본에서 관계를 재확인해 반환하고 `backend=postgresql`로 표시한다. worker의 Neo4j 조회는 승인 원문 관계와 관련 실행·승인 ID를 함께 가져와 PostgreSQL과 대조하며, 결과를 실행의 조회 기록에 고정한다. HTTP API에 Neo4j 비밀을 전달해 즉석 쿼리를 실행하지 않는다.

## 장애와 안전 경계

- 그래프 중지·미설정·지연·잘못된 출처/관계에는 동일한 승인 출처 질의만 PostgreSQL로 대체한다. `fallback_reason`과 실제 관측 graph revision을 보존한다.
- SQL 대체를 쓴 EvidenceBundle의 `graph_revision`은 null이다. 사용하지 않은 오래된 graph revision을 complete 근거의 revision으로 표시하지 않는다.
- 필수 원문·승인 기준·버전·해시 또는 권한을 확인할 수 없으면 모델 호출 전에 기술적 실패/확인 필요로 보류한다. 창작물의 pass/fail로 바꾸지 않는다.
- 과거 판정·예외 승인으로 현재 관측을 대신하지 않으며, 미승인 기준이나 다른 프로젝트를 RAG 입력으로 가져오지 않는다.
- 그래프 장애로 승인 조건의 일부만 고르거나 누락시키지 않는다. 임의 SQL/Cypher·의미 검색·타 프로젝트 검색은 지원하지 않는다.
- 기존 `freeze_approved_context`는 네트워크 없는 순수 함수로 유지한다. 일반 메모리 demo와 기존 A02-only 설정은 Neo4j에 의존하지 않는다.

## 검증

기본 검사:

```bash
uv run --locked --no-sync pytest tests/agent_framework/test_graph_configuration.py tests/test_runtime_environment.py tests/test_framework_desktop.py -q
```

실제 통합 검사는 [개발용·시험용 분리 실행기](Neo4j_개발시험_분리_가이드.md)로 수행한다. 매번 새 Neo4j·PostgreSQL·볼륨·인증을 만들고 해당 실행 자원만 정리한다. 개발 URI를 `AF_TEST_NEO4J_URI`에 복사하지 않는다. 시험 표식 없는 대상은 쓰기 전에 거부한다.

```bash
python3 scripts/framework_graph_test.py
```

통합 검사에서는 합성 승인·관측을 명시적으로 사용한다. 실제 두 DB의 전달·SQL 대체·재시작·권한·불변 기록을 검증하지만 모델 품질이나 실제 사용자의 승인·최종 검수 성공을 뜻하지 않는다. 실제 전시 PDF의 미정 항목과 승인 0건 기록은 변경하지 않는다. Windows 네이티브 파일 저장소, 승인 UI·관계도 UI·AWS 배포는 이번 구현 범위가 아니다.

Windows PowerShell에서는 `py -3.12 scripts/framework_graph_test.py`를 사용한다. 시험 코드도 Linux 컨테이너에서 실행하며 기존 개발 Compose·볼륨을 재사용하지 않는다.
