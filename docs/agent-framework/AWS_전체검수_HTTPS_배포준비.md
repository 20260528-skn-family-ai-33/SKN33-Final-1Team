# 전체 검수·Neo4j RAG·AWS HTTPS 배포 준비

2026-11-05 · 팀 개발·발표용 · 역할 계약 3.0.0

## 이번 구성과 완료 경계

현재 front-v2와 `backend.agent_framework`를 단일 Linux EC2에서 실행하고, PostgreSQL은 RDS 17, Qwen은 기존 RunPod를 사용한다. API·worker는 EBS의 같은 PDF·포스터 저장소를 공유한다. Neo4j·projector·monitor·recovery도 같은 EC2에 둔다. S3 직접 파일 저장, AWS GPU, ECS, ECR 업로드·CD 자동 배포는 이번 단계에 포함하지 않는다.

이 문서는 배포할 코드·설정을 준비한 기록이다. AWS 자원 생성, 도메인·인증서 발급, RDS 이전, 실제 Qwen 전체 검수 성공을 뜻하지 않는다. 기존 A02 설정·개인 env·DB·모델 서버는 자동 변경하지 않는다.

| 파일 | 용도 |
|---|---|
| `deploy/qwen-a02/worker-config.rag.example.json` | 실제 A02 → A04/A05 → A06 → A07 및 `neo4j_rag` 구성 |
| `scripts/framework_deployment.py` | 비공개 입력에서 API·worker·graph별 파일을 새 폴더에 준비 |
| `deploy/agent-framework/compose.aws.yaml` | AWS용 별도 Compose 프로젝트 8개 서비스 |
| `Dockerfile.aws` | 현재 프레임워크 API·worker 공통 이미지 |
| `Dockerfile.web` | 기존 front-v2와 Nginx HTTPS 이미지 |
| `Dockerfile.tunnel` | PC와 독립적으로 유지되는 RunPod SSH 터널 |

기존 `deploy/Dockerfile.v1`은 별도 `backend.creative_checker` 재구축 경로다. 이 구성에서 사용하지 않는다. 팀 설정의 `profile=development`는 기존 역할 조립 계약이며 실제 웹 환경은 별도의 `APP_ENV=production`으로 지정한다. 합성·미설정 역할과 mock 추출은 production 진입점에서 거부한다. `/api/framework/v1/health`의 `production_ready=false`는 운영 전체 검증을 완료했다는 주장을 하지 않기 위해 유지한다.

front-v2가 정적으로 참조하는 `backend/app/modeltest/prompts.json`도 웹 빌드 단계에 해당 파일만 포함한다. 이 참조 때문에 프론트 폴더만 복사하면 로컬 빌드는 성공해도 컨테이너 빌드는 실패할 수 있다.

## 1. AWS 생성 전에 확인할 사항

- RDS PostgreSQL **17**, public M008·agent_framework AF005와 app/owner 역할 분리. 새 RDS에서 명시적으로 초기화·이전하고 원래 로컬 DB는 보존한다.
- RDS CA 번들, 서비스 도메인의 `fullchain.pem`·`privkey.pem`, 현재 RunPod SSH host/port.
- RunPod에 등록한 **서버 전용 SSH 공개 키**와 별도로 검증한 SSH host key. 개인 Mac 키를 공유하지 않는다.
- Qwen의 이미지 입력 지원. 현재 A02용 서버가 `--language-model-only`라면 A04/A05 이미지 요청은 사용할 수 없다. 모델/VRAM 검증과 명시적 서버 변경은 별도 단계다. 이 구성은 RunPod를 재시작하지 않는다.
- EC2 Docker 네트워크 `172.30.66.0/24`가 VPC·기존 네트워크와 겹치지 않아야 한다. 바꿀 때는 Compose의 Nginx IP와 준비 코드의 `PROXY` 값을 함께 수정하고 검사한다.
- production API는 기존 규칙대로 활성 developer 계정이 있으면 시작을 거부한다. 복사본의 계정·프로젝트 권한을 운영자가 검토하며 원본 DB 계정은 임의 변경하지 않는다.

## 2. 비공개 공통 입력 준비

예시는 기존 루트 `.env.example` 하나를 사용한다. Mac에서는 `cp`, Windows PowerShell에서는 `Copy-Item`으로 **없는 새 파일** `.env.staging`을 만든다. 기존 파일이 있으면 덮어쓰지 않는다. 실제 비밀값과 배포 출력은 Git에 넣지 않는다.

아래 항목을 배포 대상에 맞게 수정한다. 이 블록에는 실제 비밀값이 없으며 그대로 실행 가능한 계정 설정이 아니다.

```dotenv
PERSISTENCE_MODE=postgres
APP_ENV=production
LOCAL_ADMIN_LOGIN=0
DEV_PROJECT_ID=
DB_HOST=your-db.ap-northeast-2.rds.amazonaws.com
DB_PORT=5432
POSTGRES_DB=creative_checker
DB_APP_USER=creative_checker_app
DB_APP_PASSWORD=<배포용 app 비밀번호>
DB_SSLMODE=verify-full
DB_SSLROOTCERT=/run/certs/rds-ca.pem
AUTH_ALLOWED_ORIGINS=https://checker.example.com
AUTH_ALLOWED_HOSTS=checker.example.com
CSRF_SECRET=<배포용 무작위 비밀값 32바이트 이상>
CURSOR_SECRET=<CSRF와 다른 배포용 비밀값 32바이트 이상>
CC_QWEN_BASE_URL=http://qwen-tunnel:18015/v1
CC_QWEN_API_KEY=<현재 RunPod 서버 인증키>
CC_QWEN_MODEL=Qwen/Qwen3.5-4B
CC_QWEN_REVISION=851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a
CC_NEO4J_USER=neo4j
CC_NEO4J_PASSWORD=<별도의 배포용 그래프 비밀번호>
```

`A02_QWEN_API_KEY`가 비어 있으면 준비 도구가 위 공통 키를 새 worker 파일에만 연결한다. 두 키가 모두 있으면 같아야 한다. PDF endpoint도 같은 서버로 맞춘 새 `team.json`을 만든다. 원래 A02 JSON·env는 수정하지 않는다. `CC_QWEN_REVISION`은 배포자의 선언이며 가중치를 실시간 검증했다는 뜻은 아니다.

공통 파일의 재구축용 `DATABASE_URL`·S3·MCP 값은 이 프로필에 전달되지 않는다. owner/admin 비밀번호 또는 시험용 DB 파일이 섞이면 준비를 거부한다. 프로세스 환경변수가 파일보다 우선하므로 이전 설정을 export한 터미널은 정리한다.

## 3. 실행 파일 생성 — 네트워크 연결 없이 수행

아래는 **EC2 Linux에서 준비할 때의 경로 예시**다. 아직 없는 AWS 인증서·계정·키를 자동 발급하지 않는다. Mac/Windows에서도 같은 Python 모듈을 사용할 수 있지만 실제 EC2 경로는 EC2에서 생성해야 하며 PC의 절대 경로가 담긴 `compose.env`를 그대로 옮기지 않는다.

```bash
uv run --locked --no-sync python -m scripts.framework_deployment \
  --env-file .env.staging \
  --output-dir .runtime/aws-deploy \
  --public-host checker.example.com \
  --ca-file /srv/creative-checker/certs/rds-ca.pem \
  --tls-directory /srv/creative-checker/tls \
  --identity /srv/creative-checker/ssh/id_ed25519 \
  --known-hosts /srv/creative-checker/ssh/known_hosts \
  --ssh-host <현재-RunPod-SSH-host> \
  --ssh-port <현재-RunPod-SSH-port>
```

Windows PowerShell에서는 같은 인수를 한 줄로 입력하거나 Bash의 `\` 대신 PowerShell 줄바꿈 문법을 사용한다. Python 설치 정본은 `pyproject.toml`·`uv.lock`이다. 기존 모델용 venv는 변경하지 않는다.

새 출력 폴더의 `api.env`는 브라우저 비밀만, `worker.env`는 모델·그래프 비밀만, `graph.env`는 DB·그래프 비밀만 갖는다. `compose.env`에는 파일 경로와 비밀이 아닌 주소·이미지명만 있다. CA·SSH 키·known_hosts는 읽기만 하고 원본을 복사하거나 수정하지 않는다. 출력 폴더가 이미 있으면 덮어쓰지 않는다. 결과 JSON은 `deployment_performed=false`를 명시한다.

## 4. EC2 파일 권한·DB·저장소 초기화 — 각각 명시적으로 수행

다음 명령의 대상은 **새 EC2의 `/srv/creative-checker` 전용 디렉터리와 새 배포 출력 파일**이다. 기존 자료가 있으면 먼저 백업하고 경로를 확인한다. 넓은 경로에 재귀 chown/chmod를 실행하지 않는다.

```bash
sudo install -d -m 0700 -o 10001 -g 10001 /srv/creative-checker/files /srv/creative-checker/state
sudo install -d -m 0700 -o 7474 -g 7474 /srv/creative-checker/neo4j
sudo chown 10001:10001 .runtime/aws-deploy/api.env .runtime/aws-deploy/worker.env .runtime/aws-deploy/graph.env .runtime/aws-deploy/team.json
sudo chown root:root .runtime/aws-deploy/neo4j-password
```

런타임 파일은 0600, 출력 폴더는 0700을 유지한다. CA는 컨테이너의 UID 10001이 읽을 수 있어야 한다. 서버 SSH 키는 터널 컨테이너의 root만 읽을 수 있도록 root 소유 0600으로 준비한다. Nginx의 인증서 디렉터리에는 실제 `fullchain.pem`·`privkey.pem`이 필요하다. Compose는 없는 입력 폴더를 임의로 만들지 않는다.

기존 파일 저장소를 복원할 때는 새 EC2 복사본의 내부 디렉터리·파일 소유권도 실행 UID에 맞춘다. 원본 파일·marker·해시와 원래 PC의 소유권은 변경하지 않는다. API·worker의 코드 해시가 달라지므로 예전 설정으로 접수한 실행을 임의로 재개하지 않고 새 접수로 검증한다.

RDS 관리에는 별도 `.env.admin.staging`을 사용하고 `DB_SSLMODE=verify-full`, 관리 프로세스에서 읽을 수 있는 `DB_SSLROOTCERT`를 지정한다. 관리 비밀번호를 app 파일에 넣지 않는다. M008 이전·AF005 마이그레이션·검증은 [기존 DB 실행 가이드](에이전트_프레임워크_PostgreSQL_실행가이드.html)에 따라 운영자가 명시적으로 수행한다. 관리 CLI의 TLS 옵션도 app과 같은 검증 방식으로 전달된다.

아래 명령은 실제 배포 단계에서만 실행한다. 이번 준비 작업에서 자동으로 실행하지 않는다.

```bash
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml config --quiet
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml build
# 새 빈 저장소에만 초기화. 기존 저장소는 marker·파일·해시를 보존해 복원한다.
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml run --no-deps --rm api init-pdf-storage --root /data/pdf
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml run --no-deps --rm --entrypoint /app/.venv/bin/python api -m backend.app.storage --root /data/posters init
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml up -d neo4j qwen-tunnel
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml run --no-deps --rm projector graph-init --env-file /run/secrets/graph_env
docker compose --env-file .runtime/aws-deploy/compose.env -f deploy/agent-framework/compose.aws.yaml up -d
```

로컬 개발용 Neo4j 프로젝트와 이름·디렉터리를 분리한다. 공개 포트는 Nginx의 80/443뿐이다. API 8012·Neo4j 7474/7687·터널 18015는 호스트에 공개하지 않는다.

## 5. HTTPS・復旧と確認範囲

Nginx가 HTTPS를 처리하고 클라이언트가 보낸 `X-Forwarded-*`를 덮어쓴다. API는 Nginx의 고정 IP 하나만 신뢰하며 와일드카드는 거부한다. HTTPS가 아닌 API 요청, 잘못된 Host, 합성·미설정 production 구성을 거부하고 Secure한 `__Host-` Cookie와 기존 CSRF 규칙을 유지한다. 긴 추론은 worker에서 처리하며 Nginx가 승인·등록 요청을 자동 재전송하지 않는다.

터널은 Docker 내부의 `qwen-tunnel:18015`로 접근한다. Linux EC2에서는 Docker Desktop의 `host.docker.internal`에 의존하지 않는다. 터널 재연결과 결과 불명 모델 호출의 재실행은 별개이며 모델 요청은 자동 반복하지 않는다.

EC2 재부팅 전에 Docker 서비스 자동 시작과 EBS의 고정 마운트를 별도로 구성한다. EBS 마운트가 없으면 컨테이너를 시작하지 않도록 호스트 서비스 의존성을 설정한다. TLS 인증서 갱신·Nginx reload, RDS 백업, 파일과 DB의 일관된 복구 시점, CloudWatch 전송과 비용 알림은 실제 인프라 구축 단계에서 연결한다. 이번 파일만으로 그 기능이 실행되지는 않는다.

발표 전에는 HTTPS 로그인·PDF와 자유 입력 추출·사람 기준 승인·포스터 검사·근거 조회·보고서·수정본 재검수·재시작 복원을 실제 환경에서 확인한다. 현재 front-v2의 PDF/A02 경로를 사용하며, 기존 legacy 검사 버튼·샘플·영상 경로를 새 프레임워크에 연결했다고 확대 해석하지 않는다. Windows 네이티브 파일 저장소와 실제 AWS/RDS/RunPod 전체 연결은 별도 검증이다.

참고: [RDS TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html), [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/), [Nginx proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

## 6. 로컬 준비 검증 기록

- PR #66의 CI 6개가 모두 성공한 `caac7e4`에서 `feat/37-framework-aws-deploy` 브랜치를 생성했다.
- 전체 기본 Python 검사 **2,373 passed, 11 skipped, 1,492 deselected, 15 subtests passed**. DB·실모델 표식은 별도이므로 통과 수에 포함하지 않는다.
- 배포·TLS·역할별 환경·마이그레이션·실행 경계 집중 검사 **105개 통과**. 전체 기본 검사와 중복 합산하지 않는다.
- 합성 비밀 파일로 `docker compose config --quiet`를 통과하고 API·웹·터널 이미지 **3개를 실제 로컬 Docker에서 빌드**했다.
- 격리한 실제 컨테이너·시험 인증서로 TLS 인증서 확인, front-v2 정적 페이지, Nginx→프레임워크 HTTPS 전달, 전달 헤더 덮어쓰기, Secure `__Host-` CSRF 쿠키, 신뢰하지 않는 직접 HTTP 우회 차단, 고정 도메인 리다이렉트를 확인했다. **API의 DB lifespan·maintenance는 합성 상태로 대체**했으며 RDS·모델을 호출하지 않았다.
- 시험 컨테이너·네트워크는 생성한 것만 정리했다. 실제 개인 env·기존 A02 JSON·개발 Compose 등 **17개 원본 파일의 SHA-256이 동일**하다.

비공개 실행 증거는 `.runtime/aws-deploy-preparation-20261105/`에 둔다. 실제 AWS 생성·도메인 인증서·RDS 권한/TLS·EBS 마운트·RunPod 이미지 지원·전체 검수·발표용 계정·재시작 복원은 후속 검증이다. Windows는 공통 코드·경로 계약을 검사했으며 실제 Windows Docker Desktop 실행을 완료했다고 주장하지 않는다.
