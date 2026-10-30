# 크리에이티브체커 실행가이드

이 문서는 Issue #29의 새 실행 경로만 설명합니다. 기존 `.env.v1`, DB, 모델 체크포인트와 연구 자료는 그대로 보존합니다. **새 빈 PostgreSQL DB에 초기화**하고 구형 Alembic 명령·M001~M008을 이 DB에 적용하지 않습니다.

## 개발 환경 준비

Python 3.12, uv 0.12.10, Node.js 22.12 이상, Docker Compose, FFmpeg·FFprobe가 필요합니다. Python과 프런트 의존성은 잠금 파일을 사용합니다. Docker 구성에는 PostgreSQL 17과 Neo4j 5.26만 포함되며 GPU 모델 서버를 생성하지 않습니다.

저장소 루트에서 실행합니다.

```powershell
Copy-Item .env.rebuild.example .env.rebuild
uv sync --locked --group dev
npm --prefix frontend ci
docker compose --env-file .env.rebuild -f compose.rebuild.yaml up -d
```

`.env.rebuild`의 `CC_MCP_TOKEN`을 32자 이상의 무작위 값으로 바꾸고, 실제 Qwen endpoint와 인증값을 직접 설정합니다. 예제 DB·Neo4j 비밀번호는 루프백 개발용입니다. 외부 환경에서 그대로 사용하지 않습니다. 모델 endpoint는 OpenAI 호환 `/v1/chat/completions`와 JSON schema 응답을 지원해야 하며 모델 이름은 `Qwen/Qwen3.5-4B`입니다. `CC_QWEN_REVISION`에는 배포한 공식 가중치 revision을 기록합니다. 이 값은 운영자 선언이며 서버 가중치를 자동 검증한 결과가 아닙니다.

```powershell
uv run --no-sync --env-file .env.rebuild python -m backend.creative_checker.cli init-db
uv run --no-sync --env-file .env.rebuild python -m backend.creative_checker.cli graph-init
uv run --no-sync --env-file .env.rebuild python -m backend.creative_checker.cli create-user --email operator@example.com --name 운영자 --admin
```

계정 발급은 비밀번호를 대화형으로 입력받습니다. 기본 계정·공개 가입·이메일 재설정은 없습니다. `init-db`는 새 테이블과 checkpoint를 만드는 명시적 초기화 명령이며, 이후 스키마 변경을 자동 이관하는 도구가 아닙니다.

## 프로세스 실행

아래 명령을 각각 별도 터미널에서 실행합니다. API는 모델을 적재하지 않습니다. worker는 MCP를 통해 처리하고, projection 프로세스가 outbox를 Neo4j로 전달합니다. 영상은 묶음 사이에 대기열로 돌아갑니다.

```powershell
.\run.cmd api
.\run.cmd mcp
.\run.cmd worker
.\run.cmd projection
npm --prefix frontend run dev -- --port 5173
```

macOS/Linux에서는 `./run.sh api`처럼 실행합니다. 셸 실행 권한이 없으면 `bash run.sh api`를 사용합니다. 프런트 주소는 `http://127.0.0.1:5173`, API는 8000, MCP는 8129입니다. 로컬 DB는 25429, Neo4j HTTP는 57429, Bolt는 57629를 사용합니다. 프로세스는 Ctrl+C로 중지합니다. 데이터 보존을 위해 Compose 중지 시 볼륨 삭제 옵션을 사용하지 않습니다.

개발 HTTP에서는 `CC_COOKIE_SECURE=false`와 루프백 주소를 함께 사용합니다. 프런트가 `/api`를 프록시하므로 브라우저와 API의 호스트를 임의로 섞지 않습니다. API health의 `ready`는 업무 DB 스키마 준비를 확인하며 GPU·Neo4j 정상 동작을 보장하지 않습니다. endpoint가 없거나 실패하면 실제 실패 상태를 남기며 모의 검사 결과로 대체하지 않습니다.

## 실행·재시도·복구

실행마다 입력·조건·로고 버전과 모델 식별자·선언 revision·프롬프트/처리 코드 해시를 저장합니다. 생성 이후 설정이나 처리 코드가 바뀌면 추론을 중단합니다. 기존 실행의 설정을 덮어쓰지 말고 새 실행을 생성합니다. 실패 재시도는 같은 실행의 새 attempt이며 고정된 조건을 유지합니다.

Qwen 호출은 단계당 HTTP 시도 최대 32회·600초, 요청당 최대 120초입니다. 네트워크 오류 재시도 최대 2회와 형식 보정 1회도 호출 상한에 포함됩니다. 문서 추출은 한 단계, 포스터는 검사·보고 두 단계, 영상은 최대 세 검사 묶음과 보고 한 단계입니다. 따라서 한 attempt의 추론 상한은 문서 32회/600초, 포스터 64회/1,200초, 영상 128회/2,400초입니다. 대기열·사람의 승인 대기·전처리 시간은 이 추론 상한에 포함되지 않습니다. 사용자가 요청한 새 attempt는 새 예산을 사용합니다.

worker의 lease는 900초, heartbeat는 20초입니다. worker 재시작 후 만료된 작업을 다시 가져오며 저장된 checkpoint와 도구 호출 기록을 사용합니다. 중단된 외부 호출의 성공 여부를 확인할 수 없으면 같은 호출을 무조건 재전송하지 않고 실패로 남깁니다. 취소 요청은 진행 중인 GPU 요청을 즉시 강제 종료한다는 뜻이 아닙니다.

Neo4j 장애 시 PostgreSQL 원본은 유지하고 동기화 지연/오류를 표시합니다. 연결을 복구한 뒤 projection 프로세스를 실행합니다. 그래프를 잃었다면 다음 명령으로 전체 프로젝트를 다시 반영합니다.

```powershell
uv run --no-sync --env-file .env.rebuild python -m backend.creative_checker.cli graph-init
uv run --no-sync --env-file .env.rebuild python -m backend.creative_checker.cli graph-rebuild --all
```

## 배포 구성

`deploy/Dockerfile.v1`은 새 API·worker 코드와 프런트 빌드를 담은 공통 이미지입니다. `deploy/compose.v1.yaml`은 EC2 A의 API·Nginx HTTPS, `deploy/compose.worker.yaml`은 EC2 B의 MCP·worker·projection 구성입니다. B에서도 A와 동일한 이미지를 사용해야 실행 해시가 일치합니다. `deploy/Dockerfile.worker`는 공통 이미지의 worker 진입점만 지정합니다.

배포 환경은 다음 값과 리소스를 별도로 준비해야 합니다. 이 작업에서 생성하거나 배포하지 않았습니다.

- 두 호스트가 공유하는 RDS PostgreSQL과 S3. `CC_STORAGE_BACKEND=s3`, 버킷·region, 최소 권한 인스턴스 역할을 설정합니다.
- `CC_COOKIE_SECURE=true`, HTTPS 도메인, `CC_TLS_DIRECTORY`의 `fullchain.pem`·`privkey.pem`.
- CPU worker만 접근하는 Qwen endpoint, 내부 MCP 주소·토큰, AuraDB의 `neo4j+s://` 연결과 인증값.
- RDS·S3·MCP의 접근 제한, 운영 로그·예산 알림, 백업 정책과 복원 검증.

MCP는 외부 공개 포트 없이 B 내부 네트워크로 연결합니다. 로컬 파일 저장소를 두 EC2가 서로 다른 디스크로 사용하면 파일을 공유할 수 없으므로 배포에서는 S3를 사용합니다. AuraDB가 중지되거나 데이터가 없어졌을 때는 연결 복구와 위 graph-rebuild 절차를 사용합니다.

## 내보내기·보존·삭제

프로젝트는 운영 중 보존하고 화면에서 보관 처리할 수 있습니다. 종료 전에 새 ZIP 경로로 원본 파일과 업무 기록을 내보냅니다. ZIP에는 비밀번호·세션 인증값을 포함하지 않습니다. 이 내보내기는 DB 전체 백업이나 자동 복원 기능이 아닙니다.

```powershell
uv run --no-sync --env-file .env.rebuild python -m backend.creative_checker.cli export --project-id PROJECT_UUID --output outputs/project-export.zip
```

영구 삭제 CLI는 보관된 프로젝트, 활성 작업 없음, 정확한 UUID 재입력을 요구합니다. PostgreSQL 업무·checkpoint 기록, 파일 저장소, Neo4j를 대상으로 합니다. 실제 운영자만 서버 명령을 실행할 수 있도록 OS·배포 접근을 제한해야 합니다. 외부 저장소 삭제 도중 오류가 나면 부분 삭제일 수 있으므로 같은 프로젝트의 오류를 해결한 뒤 재시도합니다. 이 가이드를 작성하면서 실제 자료를 삭제하지 않았습니다.

백업·S3의 이전 객체 버전·복제본 삭제를 최대 7일 안에 반영하려면 별도의 보존 정책과 복원 절차가 필요합니다. **현재 앱의 purge 명령이 이 정책까지 자동 집행하지는 않습니다.** 업로드 자료를 학습에 재사용하는 경로는 없습니다.

예산 계획은 AWS 300,000원 별도, RunPod·OpenAI 총 $600 중 $100 사용·$500 잔여입니다(사용자 제공값, 실시간 잔액 아님). 운영 종료 계획은 2026-11-20 22:00 KST입니다. 비용 한도·종료 시각의 자동 차단은 아직 구현하지 않았습니다.

## 관련 검사

```powershell
uv run pytest tests/rebuild -m "not integration" -q
npm --prefix frontend run build
```

실제 DB 통합 검사는 `CC_INTEGRATION_DATABASE_URL`, `CC_TEST_NEO4J_URI`, `CC_TEST_NEO4J_PASSWORD`가 있을 때 `uv run pytest tests/rebuild -m integration -q`로 실행합니다. 격리 PostgreSQL schema와 프로젝트 범위 Neo4j 데이터를 만들고 정리하므로 테스트 전용 인스턴스를 지정합니다. `.github/workflows/v1-integration.yml`도 같은 검사를 사용합니다. 과거 OCR·v1 테스트는 새 경로의 검증이 아닙니다. 현재 수행 결과는 [작업 현황](docs/rebuild/status.md)에 구분해 기록합니다.

실제 모델 검증은 별도 절차입니다. 현재 Pod 연결 검증과 Container Disk 정리는 사용자 요청으로 보류되어 있으며, 모델 주소를 자동으로 찾거나 Pod를 기동하지 않습니다. 사용 가능한 endpoint를 따로 마련한 후 아래 명령으로 전용 개발 DB에서 검증할 수 있습니다.

```powershell
uv run --no-sync --env-file .env.rebuild python -m scripts.rebuild.verify_live_workflow --output artifacts/rebuild/live-workflow-새이름
```

이 스크립트는 가상 PDF·포스터·영상을 만들고 실제 API·MCP 서버·worker·DB·모델로 조건 승인, 수정 요청, 새 버전 재검수와 검증용 예외 승인을 수행합니다. 모델 응답을 주입하지 않으며 결과·파일·격리 schema를 보존합니다. 성공해도 모델 정확도나 일반 업무에 대한 승인 근거가 되지는 않습니다. 실행 결과는 [검증 기록](docs/rebuild/verification-20261030.md)에 구분합니다.
