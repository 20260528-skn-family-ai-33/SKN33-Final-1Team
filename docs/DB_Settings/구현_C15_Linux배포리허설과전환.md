# C15 — Linux 배포 리허설·구형 쓰기 종료·전환 자료

작성일: 2026-10-19

상태: **C15 구현·사용자 지정 로컬 Linux 리허설·최종 회귀 완료.**

범위: 사용자가 선택한 **로컬 Linux 배포 리허설과 전환 자료**. 외부 운영 서버·도메인 공개·일상 DB 적용은 진행하지 않는다. 정식 schema는 M008이며 새 migration은 없다. `admin/admin`과 기존 launcher의 개발 기본 동작은 유지한다.

## 1. 구현과 실제 검증의 구분

| 산출물 | 내용 |
|---|---|
| [Compose](../../deploy/compose.v1.yaml)·[경로 설정 예제](../../.env.deploy.example) | HTTPS 프록시·비공개 API·독립 worker·공유 영속 볼륨. 웹/worker는 UID 10001·읽기 전용 root·capabilities 제거. owner/admin 비밀 미공급 |
| [구형 경계](../../backend/app/legacy_boundary.py)·[v1 앱](../../backend/app/main.py) | v1 앱의 구형 `/api/reviews` 변경 요청은 본문/CSRF/DB 접근 전 410. 구형 읽기는 404 |
| [기존 앱](../../backend/app/api.py) | 명시적인 LEGACY_READ_ONLY=1에서 localhost 읽기·개발 로그인만 유지. 원격 요청 거부, queued/running 원문의 재시작 상태 변경도 생략 |
| [전환 전 검사](../../backend/app/management/release.py) | 웹/worker DB·비밀번호·루트 일치, HTTPS/프록시·개발 계정 경계, 실제 복원 증거가 있는 최신 DB/파일 백업, 전체 무결성, 이메일 owner, 코드/빌드/worker/schema 호환 지문 |
| [worker 이미지 정의](../../deploy/Dockerfile.worker) | 별도 Paddle venv·고정 requirements·외부 검증 모델·캐시 볼륨. Dockerfile 정적 검사만 통과. 실제 모델 이미지 빌드/추론은 이번에 실행하지 않음 |
| [리허설 테스트](../../backend/tests/test_c15_deployment_integration.py) | 실제 Compose·Linux·TLS·브라우저·DB·파일·큐·백업/새 대상 복원·재시작. 명시적 테스트 override의 합성 worker 사용 |

운영 이미지에는 `backend/tests`를 복사하지 않는다. 합성 worker는 리허설 override에서만 테스트 폴더를 읽기 전용으로 mount하고 APP_ENV=test를 강제한다. 웹은 APP_ENV=production이며 실제 HTTPS·DB 인증·권한·CSRF·파일·검수 접수·결과 저장 코드를 실행한다. 합성 OCR 1개 항목의 성공을 실제 Paddle 모델 검증으로 확대하지 않는다.

worker 설정은 부작용 없는 `worker_profile`과 실제 호스트 검증을 하는 `load_worker_settings`로 나눴다. 검사 도구는 Linux 대상 설정을 macOS에서도 읽을 수 있지만, 실제 production worker의 비 Linux 시작 거부는 유지한다. 코드 지문이 바뀌므로 새 배포에는 새 worker config를 생성한다.

## 2. 배포 파일과 비밀의 경계

프로젝트 루트에서 실행한다. 기존 DB 전용 `compose.yaml`과 새 `deploy/compose.v1.yaml`을 자동으로 합치지 않는다. 새 Compose는 DB 생성·migration·계정 seed·모델 다운로드·백업·구형 앱 실행을 수행하지 않는다.

1. [.env.v1.example](../../.env.v1.example)에서 웹 전용 비공개 설정, [.env.worker.example](../../.env.worker.example)에서 worker 전용 비공개 설정을 별도로 준비한다. DB role/password는 제한된 app만 사용한다. 파일은 Git 밖의 보호된 디렉터리에 두고 0600으로 관리한다. 서비스 env_file은 `format: raw`로 읽어 `$`·따옴표·`#`를 확장/제거하지 않는다. 저장소의 리터럴 설정 파일 형식대로 값을 적으며 셸용 따옴표를 추가하지 않는다. Docker 관리 권한도 이 비밀을 볼 수 있는 권한으로 취급한다.
2. 웹은 APP_ENV=production, LOCAL_ADMIN_LOGIN=0, DEV_PROJECT_ID 빈 값, STORAGE_ROOT=/data/storage, FRONTEND_DIST=/web, REVIEW_EXECUTOR=postgres_worker, REVIEW_PIPELINE=disabled, REVIEW_CONFIG_FILE=/run/worker-config.json으로 설정한다. 정확한 HTTPS origin·host와 Compose 프록시의 고정 IP를 TRUSTED_PROXY_IPS에 지정한다.
3. worker는 APP_ENV=production, LOCAL_ADMIN_LOGIN=0, 같은 DB/role/password/루트, REVIEW_EXECUTOR=postgres_worker, REVIEW_PIPELINE=paddle-only, RUN_TARGET=local을 사용한다. 세션·CSRF·owner/admin 설정을 넣지 않는다.
4. [.env.deploy.example](../../.env.deploy.example)에는 절대 경로와 검토한 이미지 식별자만 넣는다. CC_SERVER_NAME은 인증서/Origin과 같아야 한다. CC_PROXY_IP는 웹의 정확한 신뢰 IP와 일치해야 하며 CC_SUBNET은 호스트의 기존 네트워크와 겹치지 않아야 한다. 기본 공개 바인딩은 127.0.0.1이다.
5. 실제 TLS의 cert.pem/key.pem, 고정 v1 빌드, 검증된 탐지/인식 모델, worker 생성 config를 준비한다. config는 비밀이 없으므로 컨테이너 UID 10001이 읽을 수 있게 전달한다. 모델은 읽기 전용이다. 임시 자체 서명 인증서는 리허설에서만 사용한다.

DB_HOST는 컨테이너에서 접근 가능한 실제 비공개 DB 주소여야 한다. 컨테이너의 127.0.0.1은 그 컨테이너 자신이다. 이번 Docker Desktop 리허설은 host.docker.internal 경로를 사용했으며, 실제 Linux 호스트의 loopback에만 publish한 DB가 같은 방식으로 접근된다고 가정하지 않는다. 운영 호스트에서는 DB 사설 네트워크/접근 정책을 별도로 검증한다.

```text
cd frontend
npm ci
npm run build:v1
cd ..
docker build -f deploy/Dockerfile.v1 -t creative-checker-v1:reviewed-release .
docker build -f deploy/Dockerfile.worker -t creative-checker-worker:reviewed-release .
```

두 번째 실제 worker 빌드는 준비 명령이며 이번 검증에서 실행한 것이 아니다. 선택 Linux 아키텍처의 패키지 설치·모델 import·실제 추론을 별도로 통과해야 한다. 웹/worker는 같은 소스와 고정 requirements에서 만들고 이미지 digest를 기록한다. 태그를 이동해 이전 증거를 재사용하지 않는다.

Compose를 처음 실행하기 **전** worker 이미지로 검증된 모델의 새 config를 만든다. `/absolute/release-output`은 새 파일을 UID 10001이 쓸 수 있게 준비한 디렉터리이며 `/absolute/models`는 두 모델을 포함하는 검증된 디렉터리다. 명령은 모델 다운로드/추론/DB 접속을 하지 않는다. 기존 config 위에 덮어쓰지 않는다.

```text
docker run --rm --network none --read-only --tmpfs /tmp:rw,uid=10001,gid=10001,mode=0700 --env-file /absolute/private/worker.env --mount type=bind,source=/absolute/models,target=/opt/creative/.runtime/paddle-models,readonly --mount type=bind,source=/absolute/release-output,target=/release creative-checker-worker:reviewed-release python -m backend.app.worker --env-file /opt/creative/app.env --write-config /release/worker-config.json
```

이 파일을 검토해 CC_WORKER_CONFIG로 선택한다. config 파일의 읽기 권한을 확인한 뒤 아래 config 검사를 실행한다. `config --quiet`를 사용하며 비밀이 포함되는 전체 Compose 설정을 공유 로그에 출력하지 않는다.

```text
docker compose --env-file /absolute/private/deploy.env -f deploy/compose.v1.yaml -p cc-v1 config --quiet
```

PostgreSQL 17의 별도 대상 DB/owner/app 역할 준비와 M008 적용은 기존 [C07c 관리 절차](구현_C07c_권한과최종스키마.md), 실제 이메일 사용자·멤버/owner 지정과 developer 비활성화는 [C08](구현_C08_인증과계정관리.md)을 따른다. 실제 계정/비밀번호를 임의 발급하지 않는다. 기존 개발 `admin/admin`이 필요한 개발 DB와 운영 대상을 구분한다.

## 3. 첫 배포의 저장소·시작·중지

새 프로젝트 이름을 고정한다. Compose의 `data`는 해당 프로젝트의 새 영속 볼륨이다. **기존 루트·복원한 루트에는 init을 다시 실행하지 않는다.** init은 명시적 준비이며 앱 시작 시 실행되지 않는다.

```text
docker compose --env-file /absolute/private/deploy.env -f deploy/compose.v1.yaml -p cc-v1 run --rm --no-deps api python -m backend.app.storage --root /data/storage init
```

기존 DB/루트를 재사용하거나 다른 Compose 프로젝트로 옮길 때는 빈 볼륨을 잘못 연결하지 않게 루트 marker와 전체 파일 inventory를 대조한다. worker cache와 업무 저장소를 구분한다. API/worker 포트는 publish하지 않고 TLS 프록시만 공개한다. 기본 바인딩은 localhost이므로 이번 로컬 리허설이 외부 운영 공개를 뜻하지 않는다.

전환 전 백업/검증·운영 대상 검토가 끝난 뒤에만 다음 시작 명령을 사용한다. 자동 migration/seed나 구형 앱 fallback은 없다.

```text
docker compose --env-file /absolute/private/deploy.env -f deploy/compose.v1.yaml -p cc-v1 up -d api worker proxy
docker compose --env-file /absolute/private/deploy.env -f deploy/compose.v1.yaml -p cc-v1 ps
```

health의 worker/model/ocr_runtime unknown은 준비 완료가 아니다. worker 로그의 준비 이벤트만으로 실제 모델 추론 성공도 단정하지 않는다. 선택한 실제 이미지/모델로 새 검수 1회, 결과·OCR/근거·이미지·JSON, 새로고침/재시작, 권한·CSRF·쿠키를 검증해 별도 증거를 남긴다. 현재 리허설은 이 흐름의 모델 부분을 합성한 범위다.

중지는 프록시 쓰기 유입 차단→API 중지→worker SIGTERM/drain 순서로 수행한다. worker의 최대 로컬 호출은 120초, Compose의 정상 종료 유예는 180초다. 배포 도구가 실제로 SIGKILL을 사용했거나 running/active가 남으면 회수 정책으로 확인하고 새 추론을 추정 반복하지 않는다.

```text
docker compose --env-file /absolute/private/deploy.env -f deploy/compose.v1.yaml -p cc-v1 stop proxy api
docker compose --env-file /absolute/private/deploy.env -f deploy/compose.v1.yaml -p cc-v1 stop -t 180 worker
```

기존 데이터 보존을 위해 운영에서는 `down --volumes`를 사용하지 않는다. 리허설 테스트만 자신이 새로 만든 정확한 프로젝트/볼륨을 정리한다.

## 4. 전환 전 최신 백업 검사

[C14](구현_C14_수입GC와백업복원.md)의 owner 전용 backup→inspect→다른 빈 DB/새 루트 restore→전체 검증→필요 시 restore-release 절차를 먼저 수행한다. 원문/미완료 상태를 보존하고 기존 세션 폐기·새 키·정확한 전체 검사 없이 복원 대상을 공개하지 않는다.

백업 세트에 실제 복원 훈련 기록이 연결된 뒤, 원본 서비스가 중지된 상태에서 다음 검사를 실행한다. 관리 명령이 접근하는 STORAGE_ROOT와 웹/worker 설정의 루트가 같아야 한다. Docker named volume은 호스트 관리 도구에서 임의 경로로 추정하지 말고 중지한 Linux 호스트의 실제 mount 또는 검증한 고정 스냅샷으로 검사한다. 일상 서비스의 라이브 볼륨을 복사해 이 절차를 우회하지 않는다.

```text
.venv/bin/python -m backend.app.management --env-file /absolute/private/owner.env release-check --root /absolute/private/storage --backup /absolute/backups/pre-cutover-001 --app-env-file /absolute/private/web.env --worker-env-file /absolute/private/worker.env
```

| 검사 | 실패 시 |
|---|---|
| production HTTPS·정확한 proxy·app-only·worker와 같은 DB/password/루트 | 설정 거부. 비밀/DSN 출력 없음 |
| complete+actual restore_ready·동일 DB/roles/세션 키 fingerprint | 다른 대상/미훈련 세트 거부 |
| 원본 전체 테이블 행/내용 SHA·파일 inventory | 백업 후 변경은 stale로 거부. 새 백업/실제 훈련 필요 |
| M008·전체 FK/봉인/계보/manifest·등록 파일 바이트 | 자동 수정/제약 완화 없이 거부 |
| 활성 developer·활성 프로젝트의 이메일 owner | 관리 절차로 소유권/계정을 정리하고 새 세트 확보 |
| 실행 중 서비스/추론 | 점검/저장소 배타 잠금·active 검사로 거부. 강제 종료하지 않음 |
| 코드·v1 빌드·worker config·schema SHA | 결과 compatibility에 기록. 배포 이미지/모델을 바꾸면 재검증 |

결과는 `preflight_passed`이지 운영 공개 승인이 아니다. 명령은 업무 행을 쓰거나 세션을 폐기하거나 설정/프록시를 전환하지 않는다. `worker_runtime_verified=false`, `production_cutover_performed=false`와 실제 모델·TLS/망·백업 보관의 후속 검사를 함께 반환한다. queued/미완료 수입 수는 숨기지 않고 pending으로 보고하며 자동 resume하지 않는다.

이번 리허설에서는 중지한 Linux 원본 볼륨을 고정 스냅샷으로 복사한 뒤 macOS 관리 도구에서 PostgreSQL 17 custom dump를 만들고 실제 새 DB/루트에 복원했다. 해제된 새 루트를 **별도의 새 Linux 볼륨**에 복사해 같은 파일 키·바이트와 새 DB를 사용하는 HTTPS 앱을 다시 검증했다. 이것은 다른 호스트/장치 재해 복구나 Linux 네이티브 pg_dump 운영 스케줄의 인증이 아니다.

## 5. 구형 쓰기 종료와 되돌리기

신규 v1 앱은 구형 API 모듈을 import하지 않는다. `/api/reviews`와 그 하위 변경 요청은 410 LEGACY_WRITE_DISABLED, 구형 읽기는 404다. 기존 JSON을 DB 사용자에게 임의 귀속하거나 이중 쓰기하지 않는다. 잘못된 multipart·CSRF 없음·로그인 전 요청도 변경 경로에서는 410으로 안내한다. Host/HTTPS 경계는 여전히 먼저 검사한다.

기존 프로세스 자체는 새로운 앱이 원격으로 중지할 수 없다. 전환 시 기존 listener/작업을 명시적으로 중지하고 미완료 원문 상태를 보존한다. 과거 자료 조회가 필요할 때만 별도 loopback 포트에 APP_ENV=development/test·LEGACY_READ_ONLY=1을 지정한 기존 앱을 실행하고, 인터넷 프록시에는 연결하지 않는다. 이 모드는 원격 client·Host를 검사하며 Forwarded 헤더를 loopback 증거로 사용하지 않는다. `admin/admin` 개발 로그인은 해당 로컬 모드에서도 유지한다. 플래그 없는 기존 개발 launcher는 이번에 자동 전환하지 않았다.

| 시점/사고 | 대응 |
|---|---|
| v1 신규 쓰기 전 | 새 listener/worker 중지 후 원래 개발 코드/설정으로 복귀 가능. 원본 JSON과 검증 DB/세트 보존 |
| v1 신규 쓰기 후 | 새 데이터를 보존하고 검증된 **호환 v1 이미지**만 교체. 구형 multipart 쓰기를 다시 열지 않음 |
| 새 코드/모델 장애 | 유입 중지·정상 drain·현재 DB/파일 보존. M008뿐 아니라 코드/worker config·대기 실행 호환을 확인한 이전 v1 조합만 선택 |
| DB/파일 무결성 사고 | 원본을 조사용으로 보존, 별도 대상 복원/전체 검증, 백업 이후 손실 범위를 확인한 뒤 대상 전환 판단 |
| 잘못된 완료 수입 | completed 행/결과 삭제나 schema downgrade 없음. 프로젝트 보관/접근 제한·감사 후 별도 수정 판단 |

이번에 검증한 것은 현재 C15 코드의 재시작과 별도 대상 복원 후 재가동이다. 이전 버전 이미지로의 무손실 롤백을 검증했다고 보고하지 않는다. `ffa287b` 시제품/기존 `run.sh`는 JSON 쓰기 경로이므로 v1 데이터를 저장한 뒤 사용하는 호환 롤백 대상이 아니다. 새 코드의 M008 일치만으로 이전 worker가 새 code_revision의 대기 작업을 처리할 수 있다고 가정하지 않는다.

## 6. 백업 보관·공개 판단의 남은 조건

로컬 복원 세트는 비밀번호 해시·원문·세션 자료를 포함할 수 있는 비공개 0700/0600 데이터다. 이번 리허설은 세트/쿠키/환경 파일을 Git artifact로 공개하지 않는다. 테스트 자격증명은 정리하고 안전한 요약/해시만 [증거](verification/2026-10-19-implementation-C15.json)에 남긴다.

실제 운영 전에는 다음 자료를 실제 대상에 맞게 채운다. 이번에는 미정 사항을 성공으로 표시하지 않았다.

| 항목 | 이번 상태 | 실제 공개 전에 기록할 내용 |
|---|---|---|
| 서버·도메인·TLS/방화벽·DB/영속 mount | 로컬 Docker Linux·임시 TLS·loopback만 검증 | 정확한 호스트/인증서/갱신·공개 경로·DB 제한·볼륨 소유자 |
| 실제 Linux OCR | 합성 worker만 검증 | 아키텍처·Paddle 이미지 digest·두 모델 hash·실제 추론/재시작 결과 |
| 백업 암호화·다른 장치/위치 보관 | 미구현/미실행 | 암호화 방식·키 복구 권한·실제 원격 사본 hash/복호화·복원 증거 |
| 정기 백업/보존 | 미실행 | 하루 1회·배포/수입 전 추가, 일별 7·주별 4 제안의 실제 운영 승인/스케줄·실패 알림 |
| RPO 24시간/RTO 2시간 | 목표값, SLA 미검증 | 실제 자료량과 장애 조건·시간·백업 이후 쓰기 손실 범위 |
| 최종 전환 | 미실행 | 최신 pre-cutover 세트·검사·미완료 수·단일 쓰기 경로·운영 담당 판단 |

## 7. 최종 검증 기록

최종 실행 결과는 [대응표](검증_C15_배포와전환_대응표.md)·[증거 JSON](verification/2026-10-19-implementation-C15.json)·[Linux 리허설 요약](verification/C15/linux-rehearsal.json)을 따른다. 실제 Docker/API 이미지·고정 Chromium·제한된 PostgreSQL 17 app/owner·합성 이메일 계정을 사용한다. 유료 원격 호출은 0회다.

| 실행 | 결과 |
|---|---|
| 기본 Python | 686개·추가 서브테스트 7개 통과. C15 기본 검사 16개 포함 |
| C15 전환 전 검사 집중 실행 | 5개 통과. 실제 백업/복원 증거·stale DB/파일·실제 CLI |
| 전체 PostgreSQL core | 1,327개 통과. C15 전환 전 검사 5개 포함 |
| Linux HTTPS | 기존 C12 HTTPS 1개를 현재 C15 이미지로 통과 |
| C15 Linux Compose 리허설 | 2개 통과. 새 대상 복원/재가동과 활성 developer 시작 거부 |
| 직접 접속·Vite 브라우저 | 2개 통과 |
| 프런트·빌드·의존성 | 기존 18개+v1 35개 통과·v1 빌드 성공·pip check 성공 |
| 보존·정리 | 보호 파일 105개 SHA 일치. 일상 DB/OID·기본 ACL 불변·임시 DB/리허설 리소스 정리 |

C15 신규 사례는 16+5+2=**23개**다. 집중 5개를 core와 중복 합산하지 않으며 DB 연계 고유 사례는 **1,332개**다. Python의 추가 서브테스트도 별도로 기록했다. 기존 legacy API 파일은 로컬 읽기 모드를 추가한 의도적 수정이며, 변경 없는 migration·계약·과거 증거·프런트·개발 인증/launcher를 보호 파일로 계산했다.

리허설의 합성 원본/복원 검수는 각각 OCR 1개·기준 1개다. 실제 세트 생성 0.9743초·복원 전체 검증 0.9717초, 세션 폐기 1개와 두 새 키를 확인했다. 전체 M008 26테이블·395컬럼·81FK·27추가 인덱스·35트리거·19함수 검사를 유지했다. 시간은 작은 로컬 합성 자료의 실측이며 RPO/RTO SLA나 운영 처리량이 아니다.

```text
TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite release --output /private/tmp/c15-release
TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite core --output /private/tmp/c15-core
C12_RUN_HTTPS=1 C12_API_IMAGE=creative-checker-c15-api:test TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite https --output /private/tmp/c15-https
C15_RUN_DEPLOYMENT=1 C15_API_IMAGE=creative-checker-c15-api:test TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite deployment --output /private/tmp/c15-deployment
```

DB suite는 순서대로 실행하고 브라우저에는 고정 PLAYWRIGHT_BROWSERS_PATH를 공급한다. `--guard-env .env`는 일상 DB public 관계/OID·기본 ACL을 읽기 전후 대조하는 선택 옵션이다. 테스트는 자신이 생성한 정확한 임시 DB만 DROP하고 FORCE·무관 세션 종료를 사용하지 않는다. 원격 Actions는 이번에 실행하지 않았다.

C01~C15의 코드 구현과 지정된 격리 검증을 정리한 단계다. 후속 실제 운영 검증/전환은 서버·도메인·실제 모델·보관 정책이 정해진 뒤 별도 작업으로 진행한다. C16을 임의로 추가하거나 일상 환경을 자동으로 전환하지 않는다.
