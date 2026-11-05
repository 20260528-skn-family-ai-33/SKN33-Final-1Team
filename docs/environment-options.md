# 선택 설정 참고와 env 정리 기록

2026-11-05 · Creative Checker

기본 설정 파일의 활성 항목은 실행·관리·선택적 테스트·레거시 코드에서 사용처가 확인됐다. 이번 확인 범위에서 안전하게 삭제할 수 있다고 확정된 활성 항목은 **0개**다. 설정이 특정 실행에서 선택되지 않는 것과 코드에서 사용하지 않는 것은 구분한다.

루트 env 파일에 섞여 있던 비활성 `# KEY=value` 예시와 중복 선택지 **40줄**은 이 참고 문서와 비공개 원본 백업으로 분리했다. 모든 활성 키·값은 실제 env 파서로 전후 비교해 동일함을 확인했다. env 예시는 기존 실행용·관리용·테스트용 세 파일을 유지하며 새 env 파일은 만들지 않았다.

## 현재 사용하는 값

| 설정 묶음 | 사용처 |
|---|---|
| `CC_QWEN_BASE_URL`, `CC_QWEN_API_KEY`, `CC_QWEN_MODEL`, `CC_QWEN_REVISION` | 공통 Qwen 클라이언트·A04/A05·A07·A01/A08 실모델 테스트 |
| `A02_QWEN_API_KEY` | 기존 A02 worker. 주소는 team config의 `pdf_extraction.endpoint` |
| `DB_*`, `POSTGRES_DB`, `PERSISTENCE_MODE` | M008 API·프레임워크·DB 계정 검사 |
| `AUTH_*`, `CSRF_SECRET`, `CURSOR_SECRET`, `WEB_CONCURRENCY` | 기존 로그인·세션·웹 실행 제한 |
| `CC_NEO4J_*`, `AF_NEO4J_PASSWORD` | 관계 검색·projection·Docker Neo4j 인증 |
| `DB_OWNER_*`, `POSTGRES_PASSWORD`, `PG_TOOLS_*` | 명시적인 관리·초기화·백업·복원. worker 공통 파일에 합치지 않음 |
| `DATABASE_URL`, `CC_MCP_*`, `CC_STORAGE_*`, `CC_S3_*` | 별도 재구축 실행·MCP·스토리지 프로필 |
| `TEST_DB_*`, `C12_*`, `C13_*` | 폐기 가능한 DB·Docker·HTTPS·실 OCR 선택 검사 |
| `RUN_TARGET`, `OCR_ALLOW_DOWNLOAD`, 기존 OCR/RunPod 선택값 | 레거시 및 별도 모델 실험 경로. 새 PDF·Qwen 흐름의 기본 경로와 구분 |

예를 들어 `C12_CONTAINER_DB_HOST`는 일반 backend 코드 검색만으로 찾기 어렵지만 `tests/app/test_deployment_integration.py`와 `test_web_https_integration.py`에서 사용한다. `CC_TEST_NEO4J_USER`도 재구축 통합 검사 helper에서 읽는다. 이런 항목은 불필요하다고 삭제하지 않았다.

Qwen API라는 이름은 직접 실행한 vLLM 서버에 접속하는 HTTP 통로를 뜻한다. 공통 접속 주소·인증값은 유지한다. 비밀번호·키 실값을 이 문서나 Git에 복사하지 않는다.

## 모델·웹 선택 설정

필요한 기능을 선택했을 때만 기존 개인 실행 파일에 추가한다. 같은 키가 이미 있으면 해당 줄을 수정하고 중복 선언하지 않는다. 아래 줄은 설명용 예시이며 자동으로 적용되지 않는다.

```dotenv
# A04/A05 모델 입력 예산과 좌표
A04_A05_MODEL_TIMEOUT=120
A04_A05_MODEL_MAX_PIXELS=2000000
A04_A05_MODEL_BOX_FORMAT=normalized

# 프레임워크 PDF·이미지 저장소와 웹 파일
STORAGE_ROOT=/absolute/private/storage
FRONTEND_DIST=/absolute/repository/frontend/dist-v1
TRUSTED_PROXY_IPS=172.20.0.2

# PostgreSQL 작업 큐를 사용하는 기존 v1 web/worker
REVIEW_EXECUTOR=postgres_worker
REVIEW_CONFIG_FILE=/absolute/private/worker-config.json

# HTTPS 배포를 선택할 때만
CC_TLS_DIRECTORY=/absolute/private/tls

# Vite 개발 서버의 공개 가능한 API 프록시 주소
API_PROXY_TARGET=http://127.0.0.1:8000
```

프로젝트 Python 서비스에는 `--env-file`로 파일을 명시한다. 프로세스 환경변수가 파일보다 우선하며 env 파일은 자동 탐색하지 않는다. Docker·Linux 파일 저장소를 쓰는 Mac/Windows 실행은 기존 [공통 가이드](agent-framework/Mac_Windows_공통실행_가이드.md)를 따른다.

## 관리·통합 시험 선택 설정

관리 값은 관리 파일에만 둔다. 예를 들어 native PostgreSQL 도구를 선택하면 기존 `PG_TOOLS_MODE=docker` 줄을 `native`로 수정하고 도구 경로를 추가한다.

```dotenv
PG_TOOLS_MODE=native
PG_TOOLS_BIN=/absolute/postgresql-17/bin
CC_MIGRATION_ENV_FILE=/absolute/private/admin.env
```

기존 재구축 통합 검사는 별도의 폐기 가능한 인스턴스에 맞춰 다음 값을 설정할 수 있다. 실제 개발 DB·Neo4j를 시험 대상으로 넣지 않는다. 현재 프레임워크의 권장 격리 실행은 `scripts/framework_graph_test.py`이며 실행별 시험 DB와 포트를 직접 만든다.

```dotenv
CC_INTEGRATION_DATABASE_URL=postgresql+psycopg://checker:replace-test-secret@127.0.0.1:25431/checker_rebuild_test
CC_TEST_NEO4J_URI=bolt://127.0.0.1:58413
CC_TEST_NEO4J_USER=neo4j
CC_TEST_NEO4J_PASSWORD=replace-test-graph-secret
```

위 URI·비밀번호는 형식 예시다. 전용 시험 대상인지 소유 표식·이름·포트를 확인하지 않고 사용하지 않는다.

## 레거시·별도 실험 참고

아래 항목은 현재 PDF·Qwen 기본 흐름에 새로 추가하는 설정이 아니다. 과거 기능과 팀원의 별도 작업을 재현할 때만 사용한다.

```dotenv
OCR_PIPELINE=paddle-only
OCR_CONDITION_RETRY=1
OCR_MODEL_DIR=/absolute/path/to/model-cache
OCR_OLLAMA_URL=http://127.0.0.1:11435
REQUIREMENTS_LLM_MODEL=qwen3.5:4b
REQUIREMENTS_LLM_AUTOSTART=0
CC_WEB_POSTGRES_ENV_FILE=/absolute/private/runtime.env
RUNPOD_ENDPOINT_ID=your-endpoint-id
RUNPOD_API_KEY=your-server-side-key
RUNPOD_TIMEOUT_SECONDS=300
RUNPOD_POLL_SECONDS=2
```

현재 실행값인 `RUN_TARGET`·`OCR_ALLOW_DOWNLOAD`는 그대로 두었다. 개인 파일에만 있던 중복 OCR 선택지·주석 값은 비공개 백업에 보존했고 공유 문서에 실값을 복사하지 않았다.

## 정리·보존 확인

| 파일 | 활성 항목 수 | 정리한 비활성 예시 줄 |
|---|---:|---:|
| `.env` | 11 | 12 |
| `.env.example` | 40 | 21 |
| `.env.admin.example` | 25 | 3 |
| `.env.test.example` | 14 | 4 |

`.runtime`의 개인 실행 파일 7개와 Docker용 app 예시까지 총 **12개 파일**을 확인했다. 활성 값은 전부 동일하고 `.runtime` 파일과 Docker app 예시는 바꾸지 않았다. 원본 바이트 백업은 `.runtime/env-audit-20261105/originals/`에 보관했다(폴더 0700·파일 0600). 이 폴더에는 실제 비밀값이 있으므로 공유·Git 업로드하지 않는다. 값 없는 키 이름·사용처와 파서 비교 결과는 같은 비공개 점검 폴더에 둔다.

이번 정리는 실제 미사용 옵션을 찾는 조사와 비활성 예시 분리다. 사용 중인 옵션을 이름만 보고 삭제하거나 Qwen 접속 설정을 제거한 작업이 아니다. DB·모델 서버·컨테이너를 재시작하지 않았다.

설정 전달·권한 분리·DB 설정·마이그레이션/관리 도구 회귀 검사는 **88개 통과**했다. 실제 DB 연결·모델 호출은 이번 정리에서 재실행하지 않았다.
