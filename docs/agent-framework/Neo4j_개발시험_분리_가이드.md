# Neo4j 개발용·시험용 분리 실행

2026-11-04 · Aura 없이 Docker Linux 사용 · Mac/Windows 공통

## 환경 구분

| 항목 | 개발용 | 시험용 |
|---|---|---|
| Compose | 기존 `compose.neo4j.yaml` | 새 `compose.test.yaml` |
| 프로젝트 | `creative-checker-agent-framework` | 매 실행 `cc-af-test-<무작위 12자리>` |
| 호스트 포트 | HTTP 57431 / Bolt 57631 | loopback의 임의 Bolt·PostgreSQL 포트 |
| 그래프 저장소 | 기존 named volume 보존 | 실행별 graph-data·graph-logs volume |
| PostgreSQL | 기존 `creative_checker` 보존 | 별도 PostgreSQL 17 컨테이너·볼륨 |
| 인증 | 기존 개인 설정 유지 | 실행마다 별도 임시 암호 |
| 시험 코드 | 개발 서비스 변경 없음 | Linux 컨테이너의 POSIX 저장소 사용 |
| 종료 | 기존 동작 유지 | 해당 실행의 컨테이너·볼륨·네트워크·이미지 정리 |

Compose 파일은 `deploy/agent-framework/`에 있다. 기존 개발 그래프와 이전 시험 잔재로 보이는 데이터도 그대로 보존했다. 새 시험이 개발 그래프에 쓰지 않도록 실행 경로를 분리했다.

## 준비와 실행

호스트에는 Python 3.12와 Docker Desktop의 **Linux 컨테이너 엔진**이 필요하다. 기본 명령은 호스트에 pytest·Neo4j 드라이버를 설치할 필요가 없다. 시험 이미지 안에서 기존 `pyproject.toml`·`uv.lock`의 dev 의존성을 설치한다. 첫 실행에는 이미지·패키지 다운로드가 필요하며 이후 Docker 빌드 캐시를 사용한다. Aura 계정·개발 env는 필요하지 않다.

저장소 루트에서 실행한다.

Mac:

```bash
python3 scripts/framework_graph_test.py
```

Windows PowerShell:

```powershell
py -3.12 scripts/framework_graph_test.py
```

시험 이미지 빌드 → 새 DB 준비 확인 → 시험 역할·표식 생성 → RAG 통합 검사 → 해당 자원 정리를 수행한다. 기본 대상은 `tests/agent_framework/test_graph_postgres_integration.py`다. 정상 종료 시 `isolated_databases_removed`, `isolated_test_image_removed`가 출력된다.

전체 DB 통합 검사로 넓히려면 같은 명령 뒤에 `-- tests/agent_framework -q`를 붙인다. 실행기는 `db_integration` marker를 고정한다. 브라우저·외부 모델·호스트 Docker CLI가 필요한 opt-in 검사는 별도 조건에 따라 제외된다.

## 개발 서비스는 기존 명령 유지

Mac·Windows 공통:

```text
docker compose --env-file .runtime/framework-graph-dev.env -f deploy/agent-framework/compose.neo4j.yaml up -d neo4j
```

기존 env·포트·볼륨은 바꾸지 않는다. 개발 Compose와 시험 Compose를 합쳐 실행하지 않는다. 시험 실행기는 기존 `.env` 대신 명시적인 임시 env와 고유 프로젝트명을 사용한다.

## 오접속 방어와 정리

시험 코드는 스키마·자료를 쓰기 전 읽기 전용으로 시험 표식을 확인한다. 실행 ID와 임시 토큰 해시가 맞아야 한다. 주소·암호만 개발 Neo4j로 바꿔 직접 pytest를 실행하는 경로는 거부한다. 기존 `CC_*`, `DB_*`, `TEST_DB_*`, `AF_*` 등 서비스 설정을 상속하지 않고 새 시험 값으로 구성한다.

정리는 실행 ID·시험 환경 라벨이 일치하는 고유 프로젝트 자원만 대상으로 한다. 다른 소유자의 자원이 있으면 거부한다. pytest 실패·일반 Ctrl+C·SIGTERM에서도 정리를 시도한다. 전원 종료·강제 제거에서는 자동 정리가 보장되지 않으므로 출력된 프로젝트 ID로 남은 자원을 확인한다.

```text
docker ps -a --filter label=io.creativechecker.environment=test
docker volume ls --filter label=io.creativechecker.environment=test
```

## CI와 POSIX 개발자 옵션

Ubuntu CI는 `uv run python scripts/framework_graph_test.py --host -- tests/agent_framework -q`로 호스트 Docker CLI 통합 검사까지 수행한다. 이 옵션도 DB는 새 컨테이너로 분리한다. 기본값은 Mac·Windows 공통 Linux 시험 컨테이너 방식이다. Windows에서는 `--host`를 거부하며 Windows 네이티브 POSIX 저장소 지원을 추가하지 않았다.

Ubuntu/Windows 계약 CI에 실행기 단위 검사를 추가했다. PostgreSQL CI도 고정 57631 포트·고정 계정 대신 격리 실행기를 사용한다. 로컬 검사·원격 CI·Windows 실기기 검증은 구분한다.

## 구현 위치와 범위

- `scripts/framework_graph_test.py`: 표준 라이브러리 호스트 실행기·환경 분리·소유 자원 정리.
- `scripts/framework_graph_test_container.py`: 새 DB의 시험 역할·표식 준비와 pytest 실행.
- `deploy/agent-framework/compose.test.yaml`, `Dockerfile.test`, `Dockerfile.test.dockerignore`: 시험 구성·빌드 경계.
- `tests/agent_framework/graph_test_environment.py`: 읽기 전용 시험 표식 확인.
- `tests/test_framework_graph_test.py`: 환경 상속·경로·실패/중단·정리 범위 검사.

기존 DB 마이그레이션·초기화·승인·Qwen 호출은 수행하지 않는다. 실제 모델 정확도·사람 승인·전체 포스터 검수 성공은 이 시험의 완료 기준이 아니다. 서비스 자동 재시작·지속 감시·자동 재구축은 다음 단계다.

## 11/04 검증 기록

- 기본 Linux 시험 컨테이너: RAG 통합 **13 passed**. 시험 컨테이너·볼륨·네트워크·실행별 이미지 정리를 확인했다.
- 기존 프레임워크·인증·Docker 통합: 새 격리 실행기로 **157 passed, 1 skipped**. 개발 URI·오래된 시험 env를 일부러 상속시켜도 새 DB만 사용했다. 브라우저 opt-in 검사는 제외됐다.
- 실행기·환경·설정 검사: **50 passed**. 기존 생성 계약·문서 검사: **2 passed**. 위 수치는 서로 겹치는 검사가 있어 합산하지 않는다.
- 의도적으로 없는 pytest 파일을 지정해 종료 코드 4를 발생시켰다. 이 경우도 해당 시험의 DB·볼륨·이미지가 모두 정리됐다.
- 실제 개발 Neo4j 주소를 시험 대상으로 전달했을 때 읽기 전용 표식 확인 뒤 거부돼 스키마·자료 쓰기가 발생하지 않았다.
- 기존 PostgreSQL AF005의 실행 9건·승인 0건 및 전체 프레임워크 테이블, 개발 Neo4j의 **전체 노드 209개·관계 247개**, 개발 Compose 파일의 변경 전후 논리 해시가 동일했다. 이전 시험 잔재를 포함한 기존 데이터는 삭제하지 않았다.
- Mac의 Docker Linux에서 실제 실행했다. Windows용 기본 경로와 Ubuntu/Windows CI 단위 검사 구성을 추가했으며, Windows Docker Desktop 실기기·원격 CI는 이번 작업에서 실행하지 않았다.

개인 검증 원본은 `.runtime/neo4j-isolation-20261104/`에 보존하며 자격증명과 원본 DB는 Git에 넣지 않는다. 기존 개발 API·worker·Neo4j는 유지했고 실제 모델 호출·사용자 승인은 만들지 않았다.
