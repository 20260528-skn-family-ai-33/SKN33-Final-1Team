# C14 — 수입 dry-run/apply·GC·실제 백업 복원

작성일: 2026-10-19

상태: **구현·격리 PostgreSQL·실제 백업/복원·전체 회귀·브라우저 검증 완료.** 다음은 C15이며 운영 전환은 이번에 진행하지 않는다.

기존 데이터를 변경하지 않는 수입 계획, 검토한 계획의 멱등 적용, 미참조 파일 격리/삭제, 정확한 바이트 복구, PostgreSQL custom dump와 파일의 실제 복원 훈련을 구현했다. **정식 head는 M008**이다. 새 migration·일상 DB 적용·계정 발급·기존 JSON 이력의 실제 이전은 하지 않았다. `admin/admin` 개발 계정도 유지한다.

## 1. 구현 위치

| 파일 | 책임 |
|---|---|
| [management/imports.py](../../backend/app/management/imports.py) | 읽기 전용 계획·고정 intake·지문/별칭·received/needs_review/failed/completed·원자적 수입 |
| [management/files.py](../../backend/app/management/files.py) | GC dry-run/격리/7일 뒤 삭제·중단 재개·DB 모든 state 보호·파일 점검/동일 바이트 복구 |
| [management/backup.py](../../backend/app/management/backup.py) | 실제 pg_dump/pg_restore·DB/파일 inventory·원본/복원 전체 대조·세션 폐기·새 키·복원 hold/release |
| [management/maintenance.py](../../backend/app/management/maintenance.py) | DB 점검 잠금·서비스 배타 잠금·전체 테이블 SHARE 잠금으로 쓰기 중지 확인 |
| [management/io.py](../../backend/app/management/io.py) | dir_fd/O_NOFOLLOW 기반 입력·복사·해시·UTF-16 정렬·원문 JSON 정밀도/한도 |
| [management CLI](../../backend/app/management/__main__.py), [operations.py](../../backend/app/management/operations.py) | 기존 계정 명령에 C14 관리 명령 추가. 비밀은 인수/출력에 넣지 않음 |
| [main.py](../../backend/app/main.py), [local.py](../../backend/app/storage/local.py) | 점검 중 API 503/health not_ready, 복원 검증 전 웹/worker 시작 차단, 수입 저널 지원 |
| [.env.operations.example](../../.env.operations.example) | owner 전용 관리 설정·입력 허용 루트·명시적 PostgreSQL 17 도구 |

보호된 관리 환경 파일만 owner 비밀번호를 받는다. 웹·worker에는 계속 앱 역할만 공급한다. 관리 도구는 자동 DB 생성·migration·계정 seed·모델 다운로드·추론·원격 유료 요청을 하지 않는다.

## 2. 수입 계획과 적용

관리자는 먼저 기존 서비스의 쓰기를 멈추고 단일 논리 포스터의 읽기 전용 스냅샷을 준비한다. IMPORT_ALLOWED_ROOT에 그 스냅샷의 허용 상위 경로를 명시한다. 현재 저장소와 겹치는 입력은 거부한다.

지원 입력:

- 기존 `.runtime/reviews/{ID}` 스냅샷: `snapshot.json`, `result.json`, `original.bin` 또는 실제 제공된 `normalized.png`. 기존 API 모듈을 import하거나 시작 복구를 실행하지 않는다.
- 외부 입력의 명시적 파일 대응: 아래 `cc-import-source-v1` manifest. 실제 받은 manifest 자체도 원문 inventory에 포함한다. 일반 외부 규격을 검증했다고 주장하지 않는다.

```json
{"contract_version":"cc-import-source-v1","files":{"snapshot":"snapshot.json","result":"result.json","image":"poster.png"},"sha256":{"poster.png":"실제 제공자가 명시한 SHA256"}}
```

sha256은 선택 사항이다. 제공자 해시 없음은 not_checked, 불일치는 mismatch다. 로컬 intake 복사 대조 matched와 제공자 검사를 구분한다. ZIP 해제·다중 포스터 분할·부족 파일 다운로드는 하지 않는다. 원문 로그/기타 진단은 intake에 보존하며 JSON으로 허위 등록하지 않는다.

첫 한도는 500파일·전체 512 MiB·개별 JSON 20 MiB, 이미지 한도는 C09와 같다. 경로는 허용 루트 안의 UTF-8 POSIX 상대 경로 255바이트 이하다. 절대/상위/드라이브 경로·역슬래시·심볼릭/하드 링크·NFC/casefold 이름 충돌은 거부한다. 복사 전후·적용 전·최종 준비 단계의 파일 목록/바이트를 대조한다.

`.env.operations.example`을 비공개 `.env.operations`로 복사하고 owner 연결과 IMPORT_ALLOWED_ROOT를 설정한다. 대상 프로젝트와 이번 수입의 실제 활성 owner 사용자 ID를 직접 지정한다. 과거 local-admin 문자열을 새 사용자로 추정하지 않는다.

```text
.venv/bin/python -m backend.app.management --env-file .env.operations import-dry-run --root /absolute/private/storage --source /absolute/snapshots/review-1 --project-id <UUID> --actor-user-id <UUID> --namespace legacy-runtime-review/v1 --display-name 과거검수
```

dry-run은 업무 DB를 읽기 전용으로 조회하고 원문을 `intake/{operation_id}/sources/`에 복사한다. 업무 INSERT/UPDATE가 없다. 계획·미리 발급한 UUID·검사 범위는 `plan.json`, 그 계획 전체의 지문은 별도 `plan.sha256`으로 고정한다. 출력의 operation_id를 검토한 뒤 적용한다.

```text
.venv/bin/python -m backend.app.management --env-file .env.operations import-apply --root /absolute/private/storage --source /absolute/snapshots/review-1 --plan-id <operation_id> --actor-user-id <UUID>
```

cc-import-envelope-v1 지문에는 namespace·대상 프로젝트·실제 파일 이름/바이트 수/해시·변환 profile/명시적 기존 이미지/기준 묶음 연결을 포함한다. 시각·새 UUID·현재 수입자·입력 절대 경로는 제외한다. 새 계획 UUID가 달라도 같은 입력과 결정이면 같은 package로 연결한다. 고정 계획 자체의 UUID 편집은 별도 plan SHA와 DB에 저장한 plan SHA로 차단한다.

`--existing-image-id`·`--criteria-set-id`는 dry-run에서만 명시할 수 있다. 같은 프로젝트의 실제 리소스를 검사하며 이름으로 자동 대응하지 않는다. 결정이 달라지면 다른 package 지문이 된다.

| 확인 결과 | 적용 |
|---|---|
| 실제 이미지/기준/결과 ID 대응 확인 | 파일·자료/frame·미확정 기준/항목·imported run/로컬 묶음·실제 결과/채택·각 봉인·package completed를 한 트랜잭션으로 저장 |
| 일부 결과 없음 | 실제 결과만 저장. results_complete=false이며 없는 항목을 생성하지 않음 |
| 이미지/기준 없음·모호한 ID·높은 정밀도/NUL·해시 불일치·미지원 규격/다중 포스터 | intake와 package needs_review만 보존. 가짜 FK/업무 그래프 없음 |
| final 저장 중 DB 오류·롤백 확인 | 그래프 전체 롤백, package failed·안전한 진단 |
| COMMIT 미확인 | primary/작업 잠금/package를 재조회. 새 그래프/추론 없이 성공 확인 또는 보류 |
| 동일 package 동시 apply | 관리 작업 잠금으로 거부/직렬화. completed 재실행은 기존 ID/별칭 반환 |

실패/중단/검토 대기의 재검증은 원래 고정 계획으로 `--revalidate`를 명시한다. failed→received 전에 감사 이벤트를 남긴다. 원문/변환 결정 변경은 새 계획이며 기존 완료를 덮어쓰지 않는다.

기준은 unconfirmed, run/attempt/item은 imported, requested_by/confirmed_by·원래 시도 번호·추론 시각·로컬 verdict는 모르면 NULL이다. 과거 상태·판정·시각·항목/근거 원문은 파일/스냅샷으로 보존한다. 현재 보수적 profile은 OCR/좌표/근거를 source_only로 보존하며 새 display에 과거 bbox를 추정 투영하지 않는다. schema_unavailable을 valid로 바꾸지 않는다. 새 검수는 로그인한 사용자가 정상 초안/확정 API를 거쳐야 한다.

## 3. GC와 정확한 파일 복구

GC 기본값은 dry-run이다. C13 공유 서비스 프로필에서는 살아 있는 웹/worker와 저장소를 공유할 수 있고, C10 배타 서비스와 경합하면 중지 후 실행한다.

```text
.venv/bin/python -m backend.app.management --env-file .env.operations gc --root /absolute/private/storage
.venv/bin/python -m backend.app.management --env-file .env.operations gc --root /absolute/private/storage --apply
.venv/bin/python -m backend.app.management --env-file .env.operations gc --root /absolute/private/storage --apply --delete
```

| 대상 | 규칙 |
|---|---|
| 살아 있는 파일/DB 작업 | 배타 gate·작업 OS 잠금·DB 작업 잠금 사용 중이면 제외/중단 |
| 등록된 stored_files | stored/missing 등 모든 state를 보호. 사용하지 않는 파일 행도 자동 삭제 안 함 |
| 소유가 확인된 미등록 payload | primary에서 예정 리소스/활성 시도 없음·바이트 대조·마지막 변경 24시간 이상일 때 quarantine으로 격리 |
| quarantine payload | 7일 보존 뒤 같은 잠금·DB/바이트 재검사를 통과하고 `--apply --delete`를 명시해야 삭제 |
| 저널 없음·소유 불명·링크/저널 충돌 | 자동 삭제하지 않고 보존 |
| 수입 intake·불명/관련 수입·원문/진단/저널 | 보존. 현재 명령은 알려진 payload만 처리하고 저널/진단은 자동 prune하지 않음 |

격리 의도와 삭제 시작 기록을 먼저 내구성 있게 남긴다. link→unlink 사이 중단은 원래 source/target inode·해시가 일치할 때만 이어간다. 삭제 중단도 기록된 후보의 남은 파일만 다시 확인한다. 업무 테이블 DELETE·기존 파일 덮어쓰기·구형 JSON 원문 삭제가 없다.

파일 점검은 GET의 부작용과 분리한다.

```text
.venv/bin/python -m backend.app.management --env-file .env.operations check-files --root /absolute/private/storage
.venv/bin/python -m backend.app.management --env-file .env.operations check-files --root /absolute/private/storage --mark-missing
.venv/bin/python -m backend.app.management --env-file .env.operations repair-file --root /absolute/private/storage --file-id <UUID> --replacement /absolute/snapshots/exact-payload.bin
```

누락/변조는 missing·감사로 기록하고 API는 FILE_UNAVAILABLE 503을 반환한다. repair는 **기존 SHA256·크기와 정확히 같은 바이트**만 허용하며 손상본은 진단 격리한다. 원 해시·이미지 버전·과거 verdict를 바꾸거나 재인코딩한 이미지를 같은 원본으로 간주하지 않는다.

## 4. 실제 백업 세트 생성

먼저 웹·worker·수입·GC를 명시적으로 중지하고 현재 작업을 정상 drain한다. 도구가 강제로 종료하거나 running을 성공/실패로 추측하지 않는다. 서비스 잠금을 얻기까지 최대 900초를 기다리고, active 실행/시도가 남으면 실패한다. 점검 잠금 동안 API는 503, health는 maintenance=true/not_ready다. 전체 테이블 SHARE 잠금으로 웹 밖의 DML도 차단한다.

도구는 명시적 native PostgreSQL 17 디렉터리 또는 이미 존재하는 `postgres:17.11-bookworm` Docker image를 사용한다. Docker 방식은 --pull=never이며 비밀번호를 명령 인수·manifest에 넣지 않는다. `.env.operations.example`의 PG_TOOLS_MODE/IMAGE/HOST/PORT 또는 PG_TOOLS_BIN을 설정한다.

```text
.venv/bin/python -m backend.app.management --env-file .env.operations backup --root /absolute/private/storage --output /absolute/backups/set-001 --app-env-file .env.v1 --legacy-source /absolute/snapshots/legacy
```

출력은 기존 저장소 밖의 **새 디렉터리**여야 한다. `--legacy-source`는 선택 사항이며 중지한 원문의 고정 스냅샷을 추가 보관한다. 별도 앱 환경은 같은 DB/루트의 실제 세션 키를 확인하는 데만 쓰고, 평문 비밀을 세트에 복사하지 않는다.

세트는 전체 DB custom archive·전체 저장소 바이트(작업 잠금 제외)·stored/missing inventory·intake/원문/미해결 저널·진단/격리·마이그레이션/권한·행 수/행 내용 SHA·키 fingerprint·pending 상태·검사 결과를 포함한다. 파일은 스트리밍으로 복사하고 전후 목록/해시와 DB 상태를 대조한다. 초기 복사 한도는 10,000파일·4 GiB, 표당 1,000,000행이다.

현재 결과 manifest 전체 대조에는 `staging/{attempt}/plan.json`과 작업 저널도 사용한다. 따라서 등록 결과의 계획/저널을 수동으로 지우지 않으며 백업에도 포함한다.

`manifest.json`을 기록한 뒤 그 바이트 SHA를 가진 `COMPLETE.json`을 마지막에 만든다. 중간 오류는 incomplete로 남는다. missing/가용성 결손이 있으면 degraded이며 자동 복원을 거부한다. complete도 실제 훈련 전에는 restore_ready=false다.

```text
.venv/bin/python -m backend.app.management --env-file .env.operations inspect-backup --backup /absolute/backups/set-001
```

검사는 DB 접속 없이 동작한다. dump/파일/manifest 한 개라도 누락·변조되면 실패한다. 실제 복원 성공은 원 manifest를 수정하지 않고 연결된 `verifications/` 기록으로 추가하며, inspect의 유효 restore_ready는 그 훈련 기록까지 대조한 값이다.

## 5. 새 대상 복원과 공개 전 검증

같은 PostgreSQL 17 권한 정책의 owner/app 역할과 **다른 이름의 빈 template0 DB**를 먼저 준비한다. 역할/비밀번호 생성은 도구가 수행하지 않는다. 원래 DB/루트 위에 복원하지 않는다. 대상 전용 owner 환경 `.env.restore-owner`, 새 DB·새 STORAGE_ROOT·서로 다른 새 CSRF_SECRET/CURSOR_SECRET을 가진 앱 환경 `.env.restore-app`을 별도로 준비한다.

```text
.venv/bin/python -m backend.app.management --env-file .env.restore-owner restore --backup /absolute/backups/set-001 --new-root /absolute/private/restored-storage --app-env-file .env.restore-app
```

| 검증 | 처리 |
|---|---|
| 덤프 복원 | pg_restore --single-transaction --exit-on-error. DML용 FK/트리거 비활성화 없음 |
| DB 단위 ACL | --create 없는 단일 DB dump에 없는 CREATE/TEMP/CONNECT 경계를 **새 대상에만 M008 정책으로 적용** |
| PostgreSQL CHECK 표기 | VARCHAR 배열 cast의 알려진 dump/reparse 표기 차이만 허용해 원래 검토된 CHECK SQL로 전체 행을 검증하며 재생성. 다른 차이는 거부 |
| 최종 schema | 기존 M008 byte-exact 카탈로그·컬럼별 권한 검사 그대로 사용. 원 migration/계약 변경 없음 |
| 전체 데이터 | 모든 사용자 테이블의 행 수/정렬 독립 행 SHA가 원본과 일치하는지 보안 조치 전에 확인 |
| 무결성 | 전체 FK·owner·frame/계보·OCR/추출/기준/run 봉인·결과/채택/근거·서비스/수입 manifest·고정 계획·파일 해시를 별도 재검사 |
| 보안 | 복원된 미폐기 세션 전부 폐기, 양쪽 키 fingerprint의 변경 확인, 보안 감사 기록 |
| pending | queued/미완료 package/저널 보존. worker·API를 시작하지 않음 |

새 루트에 restore-hold를 먼저 만들므로 pg_restore/검증이 실패해도 웹·worker를 시작할 수 없다. 성공 후에도 상태는 validated_held다. 파일 점검/정확한 바이트 복구는 이 상태에서 관리 명령으로 가능하다. 공개 전 검사를 다시 통과한 뒤 별도로 해제한다.

```text
.venv/bin/python -m backend.app.management --env-file .env.restore-owner restore-release --root /absolute/private/restored-storage --app-env-file .env.restore-app
```

release도 프로세스를 시작하거나 원래 환경을 바꾸지 않는다. 운영 앱 설정에서 개발 계정이 활성 상태이면 해제하지 않는다. 소유권 이전·개발 계정 비활성화·새 운영 설정 공급은 기존 명시적 관리 절차를 따른다. 이번 훈련은 test 환경의 합성 계정으로 수행했으며 기존 `admin/admin` 개발 동작을 유지했다.

## 6. 검증·경계

최종 수치·시간과 사례는 [대응표](검증_C14_수입GC와복원_대응표.md)·[증거 JSON](verification/2026-10-19-implementation-C14.json)·[복원 훈련 기록](verification/C14/restore-drill.json)에 기록했다. 실제 pg_dump/pg_restore와 새 대상의 로그인·쿠키 무효화·이미지/JSON·새 검수·재시작 조회를 수행했다. 실제 원격 유료 호출은 0회다.

| 실행 | 최종 결과 |
|---|---|
| 기본 Python 검사 | 670개·추가 서브테스트 7개 통과. C14 기본 검사 25개 포함 |
| C14 집중 PostgreSQL 검사 | 37개 통과. 수입/GC 26개·실제 백업/복원 11개 |
| 전체 PostgreSQL core | 1,322개 통과. C14 37개 포함 |
| 실제 직접 접속·Vite 브라우저 | 2개 통과. core와 합쳐 DB 연계 고유 1,324개 |
| 보호·정리 | 시작 시점 보호 파일 104개 SHA 일치. 일상 DB public 관계/OID·기본 ACL 불변, 임시 DB 0개 |

C14 신규 사례는 기본 25개와 PostgreSQL 37개를 합친 **62개**다. 집중 검사와 core에 중복 실행한 37개를 다시 더하지 않는다. 이전 C12 Linux HTTPS·실제 Paddle와 C13 실제 worker/Paddle 증거는 보존했으며 이번 단계에서 재실행한 것으로 계산하지 않는다. 원격 CI도 실행하지 않았다.

최종 core의 작은 합성 자료는 사용자 테이블 28개(업무 26개·Alembic·테스트 probe), 등록 파일 11개다. 백업 0.9794초·복원 검증 1.0062초를 측정했고, 복원 세션 1개를 폐기하고 양쪽 키를 바꿨다. 서비스/수입 manifest 각 1개와 모든 FK·봉인·계보·행/파일 SHA·M008 구조/권한을 대조했다. 이 측정은 운영 RPO/RTO나 용량 보장이 아니다.

전체 회귀 중 기존 검사 두 곳의 불안정성도 수정했다. C13의 NOWAIT 검사는 짧은 heartbeat 갱신과 경합할 수 있어 OCR이 별도 gate에서 멈춘 동안 500ms 이내에 행 잠금을 얻는지 검사한다. M007 재실행 스냅샷은 다른 테이블의 같은 트리거 이름을 구분해 테이블명/트리거명으로 정렬한다. 운영 worker·원 migration·DB 제약은 바꾸지 않았으며 수정 후 전체 core를 끝까지 통과했다.

```text
TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite operations --output /private/tmp/c14-operations
TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite core --output /private/tmp/c14-core
PLAYWRIGHT_BROWSERS_PATH=/absolute/pinned/browsers TEST_DB_ENV_FILE=/absolute/private/test.env .venv/bin/python -m scripts.verify_v1 --suite browser --output /private/tmp/c14-browser
.venv/bin/python -m pytest -q
```

실제 DB suite는 순서대로 실행한다. 선택한 `--guard-env .env`는 일상 DB public 관계/OID·기본 ACL을 읽기 전후 대조하는 용도이며 그 비밀을 웹/worker에 전달하지 않는다. 생성한 정확한 임시 DB만 DROP하고 FORCE·무관 세션 종료를 사용하지 않는다.

생성 세트는 0700 디렉터리·0600 파일의 로컬 보관이다. 이번 훈련은 암호화된 원격 보관·다른 장치 재해 복구·PITR·운영 RPO 24시간/RTO 2시간 달성·모든 OS의 전원 장애 내구성을 인증하지 않는다. CI 정의와 실제 로컬 실행도 구분한다. 운영 저장/암호화/정기 실행/전환은 C15에서 실제 환경에 맞춰 검증한다.

**다음은 C15 — 배포 프로필 전체 검증·구형 쓰기 종료·운영 전환 자료.** 사용자 “다음” 신호 뒤 그 단계만 진행한다.
