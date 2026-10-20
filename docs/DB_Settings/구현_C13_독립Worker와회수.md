# C13 — 독립 PostgreSQL worker·lease·중단 회수

작성일: 2026-10-19

상태: **C13 구현·검증 완료.** 기본 Python 645개·서브테스트 7개, DB/API core 1,285개·직접/Vite 실제 브라우저 2개·실제 worker Paddle 1개(통합 고유 1,288개) 통과. 신규 C13 사례는 단위 26개·DB/프로세스 20개·실제 OCR 1개다. 다음은 C14이며 이번에 진행하지 않는다.

웹은 검수를 DB 큐에 접수하고 독립 worker가 OCR·판정·원자적 결과 저장을 담당한다. 웹을 종료해도 이미 접수한 작업은 worker가 수행한다. 기존 C10 `background_tasks` 프로필과 기존 JSON 시제품은 유지한다. 새 migration 없이 M008의 큐·토큰·lease·전이 제약을 사용한다.

## 1. 코드와 실행 책임

| 파일 | 책임 |
|---|---|
| [worker.py](../../backend/app/worker.py) | 앱 역할만 받는 CLI, schema 점검, worker 루프, 별도 heartbeat 스레드, SIGTERM/SIGINT drain |
| [queue.py](../../backend/app/execution/queue.py) | ready 큐 인수·토큰/lease 확인·호출 전 dispatch 기록·만료 회수·안전 재대기 |
| [worker_config.py](../../backend/app/execution/worker_config.py) | worker가 생성하는 고정 실행 설정과 웹 접수용 읽기 전용 adapter |
| [reviews.py](../../backend/app/services/reviews.py) | executor별 접수/용량/입력 manifest, 현재 시도·토큰·DB 실제 시각 확인 |
| [review_results.py](../../backend/app/services/review_results.py) | C10의 준비/파일/전체 결과 저장 재사용, 이미 인수한 작업 실행, 커밋 미확인 재조회 |
| [main.py](../../backend/app/main.py), [v1_config.py](../../backend/app/v1_config.py) | 명시적 worker 접수 프로필. 모델 로딩·BackgroundTasks 실행·background 재시작 마감 없음 |
| [local.py](../../backend/app/storage/local.py), [files.py](../../backend/app/services/files.py) | worker 프로필의 공유 서비스 잠금, 동시 시작 capability 검사 직렬화, 살아 있는 파일 작업의 복구 건너뛰기 |
| [.env.worker.example](../../.env.worker.example), [.env.v1.example](../../.env.v1.example) | worker와 웹의 별도 환경 예제 |

기존 이메일 로그인·DB 세션·CSRF·프로젝트 권한을 그대로 사용한다. `admin/admin`도 기존 개발 계정 조건으로 유지한다. 웹의 개발 로그인 설정과 worker의 설정을 분리하며 운영에서는 기존대로 개발 계정을 비활성화한다.

## 2. 실행 순서

C08/C09의 DB 스키마·계정·전용 파일 저장소와 검증된 로컬 Paddle 환경/가중치를 먼저 준비한다. 이번 코드가 자동으로 migration·계정 발급·저장소 초기화·패키지 설치·모델 다운로드를 수행하지 않는다. `.env.worker.example`을 비공개 `.env.worker`로 복사하여 **앱 역할 비밀번호**와 기존 저장소의 절대 경로를 설정한다. Compose `.env`를 worker 환경으로 넘기지 않는다.

worker 환경의 핵심 설정은 다음과 같다.

```text
PERSISTENCE_MODE=postgres
APP_ENV=development
LOCAL_ADMIN_LOGIN=0
REVIEW_EXECUTOR=postgres_worker
REVIEW_PIPELINE=paddle-only
RUN_TARGET=local
STORAGE_ROOT=/absolute/private/storage
```

DB_HOST/DB_PORT/POSTGRES_DB/DB_APP_USER/DB_APP_PASSWORD도 예제에 따라 설정한다. worker에는 브라우저 origin·CSRF/CURSOR 비밀이 필요하지 않다. owner/admin 비밀번호가 들어오면 시작을 거부한다.

설정 형식만 확인할 때:

```text
.venv/bin/python -m backend.app.worker --env-file .env.worker --check-only
```

worker가 확인한 실제 모델 identity·코드 지문·전처리 설정을 **새 파일**로 만든다. 같은 파일을 덮어쓰지 않는다.

```text
.venv/bin/python -m backend.app.worker --env-file .env.worker --write-config /absolute/private/worker-config-v1.json
```

이 명령은 DB·파일 저장소·추론을 시작하지 않는다. 로컬 모델 파일 준비를 확인하여 접수용 명세를 만드는 명령이며 실제 추론 성공과 구분한다. 생성 파일은 비밀·DB URL·머신 경로를 포함하지 않는다. 웹과 worker는 같은 backend 코드 revision을 사용해야 하며 코드/모델이 바뀌면 새 명세를 만들고 배포 설정을 함께 변경한다. 옛 설정의 큐를 새 설정으로 임의 실행하지 않는다.

웹의 비공개 `.env.v1`에는 기존 이메일/개발 인증 설정을 유지하고 다음을 지정한다.

```text
REVIEW_EXECUTOR=postgres_worker
REVIEW_PIPELINE=disabled
REVIEW_CONFIG_FILE=/absolute/private/worker-config-v1.json
STORAGE_ROOT=/absolute/private/storage
```

웹과 worker의 DB·저장소는 같아야 한다. 웹은 이 명세만 읽으며 Paddle 환경·가중치를 직접 확보하거나 추론하지 않는다. 개발 웹에서는 `LOCAL_ADMIN_LOGIN=1`과 기존 DEV_PROJECT_ID를 유지할 수 있다.

두 터미널에서 각각 실행한다.

```text
.venv/bin/python -m scripts.run_v1 --env-file .env.v1 --host 127.0.0.1 --port 8000
.venv/bin/python -m backend.app.worker --env-file .env.worker
```

`--once`는 만료 회수 후 현재 인수 가능한 일치 작업을 최대 한 개 처리하고 종료한다. 프로세스 종료 코드 0 자체를 검수 성공으로 해석하지 말고 API 실행 상태를 조회한다. worker가 없는 동안에도 웹은 최대 16개의 queued/running 작업을 접수할 수 있다. `/health`의 worker는 별도 가동 관측 체계가 없으므로 `unknown`이며 모델/추론 ready를 추측하지 않는다.

기존 C10 프로필은 REVIEW_EXECUTOR의 기본값 `background_tasks`를 사용한다. 두 프로필은 서로의 큐를 인수하거나 재시작 실패 처리하지 않는다. 같은 저장소에서는 background의 배타 서비스 잠금과 worker 프로필의 공유 서비스 잠금이 경합하여 잘못 섞인 시작을 거부한다.

## 3. 인수·lease·저장 경계

| 경계 | 실제 처리 |
|---|---|
| 큐 조회 | service_execution/postgres_worker·queued·available_at 도달·workflow/실행 설정/재시도 정책 일치 |
| 인수 | project → run 잠금. 사용 중인 프로젝트/run은 SKIP LOCKED. attempt 생성·새 토큰·running 포인터를 한 번에 커밋 |
| 시도 번호 | 같은 run에서 1부터 증가, 총 3회 상한. run.started_at은 최초 시작 시각 유지 |
| heartbeat | 독립 스레드, 15초 간격·90초 lease. 잠금 획득 뒤 clock_timestamp()로 만료 확인. 만료 lease를 연장하지 않음 |
| OCR 호출 직전 | 현재 run/attempt/token/lease 확인 → dispatching 커밋 → 권한 확인 → DB 잠금 밖에서 로컬 OCR 호출 |
| 결과 저장 | 현재 토큰/포인터·실제 lease 재확인. C10의 OCR/추출/항목/근거/채택/manifest·봉인 전체 원자적 커밋 |
| 늦은 결과 | 옛 토큰·현재 시도 불일치·만료 lease·마감 run은 저장 거부. 채택/기존 결과 변화 없음 |
| 파일 | 작업 잠금 → 공유 gate → 파일 게시 → 짧은 DB 저장. 중단 산출물은 보존하고 C14 정리 대상으로 남김 |

heartbeat가 한 번이라도 실패/미확인되면 그 소유자의 권한 플래그를 영구 상실로 둔다. DB 연결이 돌아왔다는 이유로 옛 시도의 실행 권한을 자동 재획득하지 않는다. 다른 worker는 실제 만료 뒤에만 회수한다. 파일 처리·이미지 변환·모델/원격 대기를 DB 트랜잭션 안에 넣지 않는다.

## 4. 중단·커밋 미확인·원격 정책

| 상황 | 처리 |
|---|---|
| lease 만료 + not_dispatched + 원격 job 없음 | 이전 attempt failed/retryable. 같은 고정 정책에서 3회 미만이면 queued·5/30초 backoff, 상한이면 최종 failed |
| lease 만료 + dispatching/submitted/unknown | attempt unknown·run failed·outcome_unknown. 자동 OCR/유료 제출 없음 |
| 인수 COMMIT 미확인 | OCR 호출 없음. 새 소유권을 추측하지 않고 실제 DB/lease 회수에 맡김 |
| dispatch COMMIT 미확인 | OCR 호출 없음. DB의 제출 여부가 불명확하므로 보수적으로 마감/회수 |
| 결과 COMMIT 미확인 | 같은 attempt/manifest/files로 primary에서 기존 성공 확인. 새 OCR이나 새 키 접수 없음 |
| SIGTERM/SIGINT | 새 작업 인수 중단, 현재 작업의 heartbeat를 유지하며 완료 후 종료 |
| SIGKILL | OS 잠금 해제·DB lease 잔존. 다른 worker가 실제 만료 후 위 정책으로 회수 |
| 사용자 재검수 | 기존 API로 별도 run·새 키·retry_of_run_id. 자동 회수와 구분 |

로컬 Paddle subprocess의 기존 240초 상한은 유지한다. 서비스 관리자도 정상 drain에 이 시간과 결과 저장 여유를 허용해야 한다. 강제 종료는 이미 수행 중인 OCR의 성공을 보장하지 않는다.

이번 worker는 **로컬 paddle-only**만 지원한다. 기존 RunPod 시제품 모듈을 새 worker에 연결하지 않았다. submitted/원격 job 상태의 회수 차단은 합성 행으로 검증하고 실제 유료 제출은 0회다. 원격 job 조회/결과 복구 adapter도 구현하지 않았다. DB에서 결과를 한 번 채택하는 것과 외부 추론의 exactly-once 보장은 구분한다.

## 5. 검증

검증 결과와 실제 테스트명은 [대응표](검증_C13_Worker와장애복구_대응표.md)·[증거 JSON](verification/2026-10-19-implementation-C13.json)에 기록한다.

| 실행 | 결과 |
|---|---|
| 기본 Python 전체 | 645개·서브테스트 7개 통과 |
| DB/API core | 기존 1,265개 + C13 DB/별도 프로세스 20개 = 1,285개 통과 |
| 실제 브라우저 회귀 | 고정 Chromium 151.0.7922.34, 직접 HTTP/Vite 2개 통과 |
| 실제 worker OCR | 배포용 CLI·고정 설정 명세·실제 HTTP/DB/파일·로컬 Paddle CPU 1개 통과 |
| CI YAML | 문법 확인. core에 C13 worker 장애 시험 포함. 원격 GitHub Actions 실행 아님 |
| 보호 확인 | M001~M008/schema contract·기존 증거/계약·프런트 원본 SHA256 유지. 일상 DB public 관계/OID·기본 ACL 전후 일치, 임시 DB 잔존 0 |

실제 포스터 1장은 표시 600×848·OCR 입력 1200×1696이며 OCR 18개·검수 항목 1개·attempt 1회로 completed였다. 모델 경로의 wall 9.9145초(load 2.9573·inference 6.6011), worker 전체 10.7874초, 하위 모델 peak RSS 8,853,061,632바이트였다. [실측 JSON](verification/C13/actual-worker-paddle.json)을 따른다. 포스터 1장의 단일 측정이며 정확도 평가·일반 지연/메모리 상한·SLA가 아니다.

최초 실행 때 자동 승인 검토 서비스의 용량 오류 2회, Docker Desktop 정지로 인한 DB 접속 거부 1회, 사라진 임시 Chromium으로 인한 브라우저 시작 실패 1회가 있었다. 검토 서비스 복구·기존 Docker/DB 시작·같은 고정 Chromium 준비 후 위 suite가 모두 통과했다. DB/테스트 실패를 skip으로 바꾸거나 승인 검토를 우회하지 않았다. 프런트 단위 53개/빌드와 Linux HTTPS·직접 웹 Paddle 시험은 C12 기록을 유지하며 이번에 다시 실행한 항목으로 합산하지 않는다.

테스트는 TEST_DB_ENV_FILE 또는 명시적 TEST_DB_*만 받으며 앱 `.env`로 자동 대체하지 않는다. 모든 DB suite는 순서대로 실행한다.

```text
TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite worker --output /private/tmp/c13-worker
TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite core --output /private/tmp/c13-core
C13_RUN_REAL_OCR=1 TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite worker-ocr --output /private/tmp/c13-real-ocr
.venv/bin/python -m pytest -q
```

`--guard-env .env`는 명시적으로 선택한 일상 DB의 public 관계/OID·기본 ACL을 읽기 전후 대조하는 옵션이다. 이 환경은 테스트 DB 접속이나 worker/웹 프로세스에 전달하지 않는다. 임시 DB 제거는 생성한 정확한 이름만 대상으로 하며 FORCE·무관 세션 종료를 하지 않는다.

실제 개발 검증 OS와 Linux 운영 인증을 구분한다. C12 API 이미지는 Paddle 환경/가중치를 포함하지 않으며 이번 단계가 Linux Paddle worker 이미지 준비나 공개 배포를 완료했다는 뜻은 아니다. 다중 호스트/NFS 저장소·원격 worker·Windows 파일 운영도 이번 검증 범위 밖이다.

## 6. 다음 단계

**C14 — 수입 dry-run/apply·GC 관리·실제 DB+파일 backup/restore 훈련.** 사용자 “다음” 신호 뒤 이 단계만 진행한다.
