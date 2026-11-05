# Neo4j·동기화 worker 공통 실행과 재시작

2026-11-04 · 개발용 Docker Linux · Mac/Windows 공통 · Aura 사용 없음

## 무엇을 자동화했나

기존 `compose.neo4j.yaml`에 `compose.runtime.yaml`을 겹쳐 실행한다. 프로젝트명·Neo4j 데이터 볼륨·57431/57631 포트는 유지한다. Neo4j 준비 상태를 기다린 뒤 독립 projection worker를 시작하고, 두 서비스에 `unless-stopped` 정책을 적용한다. PostgreSQL·API·모델 서버를 추가 기동하거나 스키마를 자동 변경하지 않는다.

Docker Desktop이 실행 중이어야 한다. Docker 엔진이 재시작되면 서비스를 다시 시작하지만, 사용자가 명시적으로 stop한 서비스는 그대로 멈춘다. OS 로그인 시 Docker Desktop 자체를 켜는 설정은 이번 코드가 변경하지 않는다.

## 최초 준비

기존 PostgreSQL M008/AF005, 제한된 app 계정, 초기화된 개발 Neo4j가 필요하다. 새 PC의 마이그레이션과 `graph-init`은 [근거 검색 연결 가이드](Neo4j_근거검색_RAG_연결가이드.md)에 따라 명시적으로 한 번 수행한다. 실제 초기화·데이터 삭제·프로젝트 전체 재구축은 자동 실행하지 않는다.

기존 `.runtime/framework-graph-app.env`와 `.runtime/framework-graph-dev.env`를 사용한다. 소유자 암호가 있는 파일은 app 설정으로 사용할 수 없다. 두 설정의 Neo4j 암호가 다르면 실행을 거부한다.

새 이미지는 최초 또는 코드 변경 후 빌드한다.

Mac:

```bash
python3 scripts/framework_desktop.py build --image creative-checker-framework:graph-runtime-20261104
```

Windows PowerShell:

```powershell
py -3.12 scripts/framework_desktop.py build --image creative-checker-framework:graph-runtime-20261104
```

## 일상 실행 — 저장소 루트

Mac:

```bash
python3 scripts/framework_graph_runtime.py up
python3 scripts/framework_graph_runtime.py status
```

Windows PowerShell:

```powershell
py -3.12 scripts/framework_graph_runtime.py up
py -3.12 scripts/framework_graph_runtime.py status
```

`up`은 app 계정으로 AF005 준비 상태를 읽기 전용 검사한 뒤 서비스를 시작하고 두 서비스의 healthy를 기다린다. 같은 이미지·설정으로 다시 실행하면 기존 파생 설정과 컨테이너를 재사용한다. 새 설정이나 이미지로 실행하면 새 파생 설정을 만들고 적용한다.

개인 파일·이미지 경로가 다르면 `--env-file`, `--graph-env-file`, `--image`를 명시한다. 호스트 Python에 모델·Neo4j 패키지를 설치할 필요는 없다.

의도적으로 멈출 때는 같은 명령에서 `up` 대신 `stop`을 사용한다. 컨테이너만 중지하며 볼륨은 삭제하지 않는다. 다시 `up`을 실행하면 시작한다. 이전에 터미널에서 수동 실행하던 `graph-projector`는 새 Compose worker 확인 후 종료해 중복 실행을 피한다.

## 실제 접속 확인

Neo4j의 HTTP 준비 상태와 별도로 worker의 healthcheck는 `graph-check`로 PostgreSQL 권한·AF005 구조 및 Neo4j 인증·AF 제약 조건을 읽기 전용으로 확인한다. API의 정적 `graph_projection=true`를 접속 성공으로 대신하지 않는다.

직접 한 번 검사할 때:

```text
python3 scripts/framework_desktop.py graph-check --image creative-checker-framework:graph-runtime-20261104 --env-file .runtime/framework-graph-app.env
```

Windows에서는 위의 `python3`를 `py -3.12`로 바꾼다. 정상 결과는 PostgreSQL·Neo4j가 ready, schema AF005, read_only true다. 인증·제약 조건을 확인하지 못하면 실패 종료하며 초기화로 숨기지 않는다.

`healthy/unhealthy`는 의존 서비스의 접속 상태다. 건강 검사 실패만으로 프로세스를 강제 재시작하지 않는다. 프로세스 종료·Docker 엔진 재시작에 대한 복구는 restart 정책이 담당한다. 동기화 지연·worker 루프 정지·SQL 대체 빈도 감시와 알림은 별도 후속 범위다.

## 개인 설정 보존

원본 env는 변경하지 않는다. 파생 설정은 `.runtime/framework-graph-runtime/run-*/`에 보관한다. worker에는 DB·Neo4j 설정만 전달하며 Qwen·웹 비밀과 owner 암호를 전달하지 않는다. Neo4j URI는 Compose 내부 DNS를 사용하고, DB의 호스트 loopback 주소만 Docker용 주소로 변환한다. Linux 호스트만 별도 host-gateway 구성을 추가한다.

worker가 재시작해도 설정을 다시 읽을 수 있도록 파생 파일을 유지한다. 실행 중 이 폴더를 삭제하지 않는다. `.runtime`의 설정·키는 Git에 포함하지 않는다. Windows에서는 기존처럼 Linux 컨테이너와 named volume을 사용한다.

## 이번 단계의 경계

개발용 자동 실행·프로세스 재시작·실제 접속 상태 확인까지 구현한다. 원문·승인 기록·실제 모델 호출·API 프론트 동선은 변경하지 않는다. 그래프 유실 자동 복구·원장에 없는 그래프의 자동 삭제·자동 마이그레이션은 추가하지 않는다. 시험용 DB는 [분리 실행기](Neo4j_개발시험_분리_가이드.md)를 계속 사용한다.

참고: [Docker 준비 상태와 실행 순서](https://docs.docker.com/compose/how-tos/startup-order/), [Docker 재시작 정책](https://docs.docker.com/engine/containers/start-containers-automatically/).

## 11/04 실제 적용·검증

- 기존 역할·CLI·근거 검색·실행기 회귀 **906 passed, 8 subtests**.
- 분리된 Neo4j·PostgreSQL·Linux 시험 컨테이너에서 RAG 및 인증 healthcheck **15 passed**. 잘못된 Neo4j 암호는 실패 종료하며 원장 revision은 바뀌지 않았다.
- 설정 재사용 보완 후 관련 실행기 검사 **25 passed**. 한글·공백·달러 기호가 있는 경로와 가짜 비밀번호도 실제 별도 컨테이너에서 원문대로 전달됐다.
- 새 이미지 `creative-checker-framework:graph-runtime-20261104`, SHA `2485b4c79f06bcf8d20e97bbd1cd32a6ee60cf9a1036f239375b3de7d8872cff`를 사용했다. 기존 이미지 태그는 유지했다.
- 실제 개발 Neo4j와 새 `creative-checker-agent-framework-projector-1`이 모두 healthy, restart 정책 unless-stopped다. 이전 수동 `cc-framework-graph-projector-20261104`는 새 worker 확인 후 종료했다.
- 새 Python worker 프로세스만 의도적으로 종료했다. Docker가 자동 재시작했고 **restart_count=1, healthy**로 복구했다. Neo4j DB 프로세스는 이 종료 시험의 대상이 아니었다.
- 같은 설정으로 up을 반복한 뒤에도 worker 재시작 횟수 1이 유지돼 불필요한 컨테이너 재생성이 없었다.
- 기존 PostgreSQL 실행 9건·승인 0건 등 전체 프레임워크 테이블과 개발 그래프 전체 **209노드·247관계**, 원래 개발 Compose의 논리 해시가 동일했다. Neo4j 데이터·로그 volume ID도 모두 동일했다.
- 기존 API·PostgreSQL·모델 설정은 유지했다. 사용자 승인·모델 호출·마이그레이션은 수행하지 않았다.

Mac Docker Linux에서 실제 적용했다. Windows 공통 Python/Docker 경로와 계약 CI는 준비했으며 Windows 실기기와 원격 CI 실행은 이번 작업에서 확인하지 않았다. 비공개 원본 기록은 `.runtime/neo4j-runtime-20261104/`, `.runtime/graph-runtime-tests-20261104.log`, `.runtime/graph-runtime-unit-20261104.log`에 남긴다.

## 후속 감시 서비스

이후 독립 monitor 서비스와 새 graph-monitor 이미지가 추가됐다. 최신 기본 빌드·실행·경고 확인 명령은 [동기화 감시 가이드](Neo4j_동기화_감시_가이드.md)를 따른다. 기존 자동 재시작 검증 기록은 당시 이미지 기준이며 현재 status에는 monitor와 synchronization 항목도 포함된다.

## 후속 자동 복구 적용

현재 공통 실행기의 기본 이미지는 `creative-checker-framework:graph-recovery-20261104`이며 `up`은 서비스 4개를 시작한다. 위 기록의 이전 단계 검증 수치는 당시 결과다. 현재 실행·복구 정책은 [프로젝트 자동 복구 가이드](Neo4j_프로젝트_자동복구_가이드.md)를 따른다. monitor 자체는 계속 읽기 전용이고, 별도 recovery 서비스가 복구 요청을 담당한다.
