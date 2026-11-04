# PDF 등록·문서 버전 저장

2026-11-03 · A02 샘플 작업 4단계 · `feat/37-a02-pdf-registration`

후속 5단계는 [A02 조건 추출 연결가이드](A02_조건추출_연결가이드.html)를 따른다. 이 문서의 ‘다음 단계’ 표기는 4단계 당시의 구분이며 PDF-only worker·mock 초안 저장은 5단계에서 구현했다.

## 01. 현재 구현 범위

클라이언트 PDF 원본을 기존 M008 사용자·프로젝트에 등록하고 **문서 ID, 변경 불가능한 버전, SHA-256, 페이지 수, 제출자, DB 기록 시각**을 저장한다. 로그인 세션·CSRF·프로젝트 권한과 기존 `LocalStorage`의 저장·검증 기능을 재사용한다. `public` M008 및 `AF001.sql`은 그대로 두고 같은 DB의 `agent_framework.document_versions`를 후속 AF002로 추가한다.

가상 의뢰서 PDF 3종을 사용한 PostgreSQL 17 격리 통합 테스트를 수행했다. 운영 DB 적용·front-v2 화면 연결·Qwen 호출은 수행하지 않았다. 조건 추출은 5단계, 보완·기준 승인은 6단계다. 이 API는 추출 실행이나 승인 기록을 만들지 않는다.

PDF 컨테이너·암호화 여부·크기·페이지를 검사한다. 상한은 **20MiB, 50쪽**, 암호화 PDF는 거부한다. 텍스트·스캔 PDF 지원 구분과 모델 입력 준비는 5단계에서 처리한다. 현재 등록 성공을 텍스트 추출 성공으로 해석하지 않는다.

## 02. 저장 경로와 적용 준비

원본 바이트는 DB 밖의 파일로, 메타데이터는 기존 PostgreSQL에 저장한다. 물리 경로는 UUID로만 만들며 한글 원래 이름은 메타데이터와 다운로드 이름으로 보존한다. 파일명으로 경로를 만들거나 기존 파일을 덮어쓰지 않는다.

**기존 이미지 저장 폴더와 다른 새 PDF 전용 폴더**를 사용한다. 이미지 복구·GC는 PDF 버전 테이블을 모르기 때문에 같은 경로를 공유하지 않는다. 저장 구현은 기존 `LocalStorage`를 재사용하고 PDF 전용 표시 파일을 추가했다. 초기화는 명령으로 한 번 수행하고, 서버 시작 때 자동 생성하지 않는다.

현재 `LocalStorage`는 POSIX 파일 디스크립터·권한·하드링크를 요구한다. macOS에서 검증했고 Windows 네이티브 파일 저장 실행은 지원 확인 전이다. Windows 팀원은 WSL2의 Linux 파일시스템에서 백엔드를 실행하는 방향으로 별도 검증해야 한다. 아래 HTML·문서 링크는 상대 경로이므로 두 OS에서 열람할 수 있다.

프로젝트 루트에서 기존 [app/owner 설정 예시](postgres-app.env.example)를 참고해 별도의 `.runtime/framework-app.env`, `.runtime/framework-owner.env`를 준비한다. 기존 `.env`를 수정하거나 owner 비밀번호를 app 파일에 넣지 않는다. 예시 값은 실제 키가 아니다. M008 DB를 먼저 준비하고 owner는 명시적 마이그레이션에만 사용한다.

```bash
.venv/bin/python -m backend.agent_framework migrate-postgres --env-file .runtime/framework-owner.env --revision AF002
.venv/bin/python -m backend.agent_framework postgres-check --env-file .runtime/framework-app.env
.venv/bin/python -m backend.agent_framework init-pdf-storage --root "$PWD/.runtime/framework-pdf"
.venv/bin/python -m backend.agent_framework postgres-serve --env-file .runtime/framework-app.env --config examples/agent_framework/team_config.synthetic.json --pdf-storage-root "$PWD/.runtime/framework-pdf" --port 8012
```

`.runtime` 부모 폴더는 먼저 준비한다. 초기화는 이미 있는 목적지를 거부한다. root·파일은 각각 0700·0600으로 생성한다. `--pdf-storage-root`를 빼면 PDF API는 503으로 비활성화되며 기존 AF001 실행 API를 계속 사용할 수 있다. PDF 활성화는 AF002 readiness가 통과해야 한다. 마이그레이션은 AF001에서 AF002로 올리거나 M008에 AF001·AF002를 순서대로 추가하며 기존 SQL을 수정하지 않는다. 기본 `--revision`은 이전 호환을 위해 AF001이다. 이미 AF002이면 낮추지 않는다.

등록·조회는 API 단독으로 동작한다. 아직 PDF-only A02 worker를 시작하는 명령은 아니다. `team_config.synthetic.json`도 모델 추론 성공을 뜻하지 않는다.

## 03. 인증과 요청 형식

기존 `/api/v1/auth/csrf` → 로그인 API의 세션 쿠키·CSRF 방식을 그대로 사용한다. `Origin`은 설정된 서버 origin과 일치해야 한다. 기존 사용자만 사용하고 이 기능이 계정·프로젝트를 자동 생성하지 않는다. 기존 업무 API에서 만든 프로젝트 ID를 사용한다. 조회는 활성 멤버, 등록은 editor/owner 권한이 필요하다. 보관된 프로젝트에 새 버전을 등록할 수 없다.

등록은 multipart 대신 **원본 PDF 바이트**를 `Content-Type: application/pdf`로 전송한다. 권한을 먼저 확인한 뒤 스트림을 크기 제한 내에서 읽는다. 호출마다 `Idempotency-Key` UUID를 만들고 응답을 못 받았을 때 **동일한 요청 키·파일·이름·이전 버전**으로 확인한다. UUID는 비밀 값이 아니다.

| 메서드·경로 (`/api/framework/v1` 아래) | 기능 |
|---|---|
| POST `/projects/{project_id}/documents?filename=...` | 새 논리 문서와 버전 1 등록 |
| POST `/projects/{project_id}/documents/{document_id}/versions?previous_version_id=...&filename=...` | 지정한 최신 버전의 다음 버전 등록 |
| GET `/projects/{project_id}/document-versions/{version_id}` | 메타데이터·DocumentRef 조회 |
| GET `/projects/{project_id}/document-versions/{version_id}/content` | 크기·해시·PDF 페이지 재검증 후 원본 다운로드 |
| GET `/projects/{project_id}/documents/{document_id}/versions` | 오래된 버전을 포함한 순서별 이력 |

파일 전송 클라이언트는 `filename`을 URL query로 인코딩한다. 한글 파일명은 저장 경로로 사용하지 않는다. 응답의 `document`에는 `file_id`, `document_version_id`, `sha256`, `page_count`, `media_type`가 있고 실제 디스크 경로는 공개하지 않는다.

## 04. 중복·버전 충돌·실패 처리

같은 프로젝트·같은 키와 같은 입력의 재전송은 동일한 ID와 메타데이터를 반환한다. 같은 키에서 내용·이름·사용자·부모 버전을 바꾸면 409다. 새 버전은 서버의 최신 버전 ID를 `previous_version_id`로 지정한다. 같은 부모에서 두 버전을 동시에 만들면 하나만 확정되고 다른 요청은 409다. 기존 버전은 UPDATE/DELETE 권한을 주지 않고 DB trigger로도 변경을 차단한다.

파일을 먼저 동기화·공개하고 짧은 DB 트랜잭션에서 권한·최신 버전을 다시 확인한 뒤 메타데이터를 확정한다. 파일 I/O 중 DB 잠금을 유지하지 않는다. 작업 journal은 같은 요청 키를 재사용할 때 최초 발급한 파일·문서·버전 ID를 재사용하기 위한 기록이다. DB가 업무 정본이며 journal만으로 등록 완료를 판정하지 않는다.

DB commit 응답이 끊기면 새 연결에서 advisory lock과 메타데이터를 **읽기 전용으로 대조**한다. 확인되면 기존 결과를 반환하고 불명확하면 `DOCUMENT_UPLOAD_OUTCOME_UNCONFIRMED` 503이다. 자동으로 DB 등록을 다시 시도하지 않는다. 사용자가 확인 후 동일 키로 재전송할 수 있다. 파일 저장 작업이 이미 진행 중이면 503이 반환될 수 있다.

중단·충돌로 생긴 파일은 자동 삭제하지 않는다. 재전송 시 해당 요청 journal을 통해 복구한다. 전체 journal 자동 스캔·고아 파일 GC·보존 정책은 아직 구현하지 않았다. 파일·journal을 임의로 지우거나 다른 키로 연속 재시도하지 않는다. 원본이 바뀌거나 사라지면 읽기를 503으로 거부하며 재전송으로 변조 파일을 덮어쓰지 않는다.

DB와 PDF 저장 디렉터리는 함께 보존해야 한다. 기존 public 전용 백업만으로 PDF 버전과 원본 복구까지 완료했다고 간주하지 않는다.

## 05. A02 담당자의 다음 연결 위치

| 파일 | 연결점 |
|---|---|
| `backend/agent_framework/documents.py` | `PostgresDocuments`, `RegisteredProjectCatalog`, 등록 문서→DocumentRef |
| `backend/agent_framework/files.py` | `inspect_document`, `VerifiedFileAccess.read_document`의 실제 바이트·해시·페이지 검증 |
| `backend/agent_framework/adapters/project_files.py` | `LegacyStorageReader`에 PDF 저장소 주입. 기존 포스터 catalog는 유지 |
| `backend/agent_framework/postgres_api.py` | 인증·CSRF·프로젝트 기반 등록/조회 API |
| `backend/agent_framework/AF002.sql` | PDF 원본 버전 메타데이터. 추후 DDL은 새 revision으로 추가 |

인증한 identity를 바인딩한 `RegisteredProjectCatalog`와 `LegacyStorageReader(pdf_storage)`를 `VerifiedFileAccess`에 주입하면 실제 PDF 바이트를 읽을 수 있다. Context.actor_id는 바인딩한 identity와 같아야 한다. 임의 요청 actor ID를 인증 증거로 사용하지 않는다. PDF·포스터가 다른 저장 root이면 같은 reader 하나로 두 경로를 처리할 수 없으므로 다음 통합 단계에서 구분해 주입한다.

5단계에서는 PDF-only 실행 상태, Qwen/모의 응답 경계, 원문 출처 검증, 추출 결과 저장을 추가한다. 현재 `/runs`는 기존 PDF+포스터 계약을 유지하므로 PDF 등록 응답만으로 가짜 포스터 참조를 만들어 실행하지 않는다.

## 06. 테스트 명령과 검증 범위

```bash
.venv/bin/python -m pytest tests/agent_framework -q
TEST_DB_ENV_FILE=/absolute/path/to/test-only.env .venv/bin/python -m pytest tests/agent_framework/test_pdf_postgres_integration.py tests/agent_framework/test_postgres_integration.py -m db_integration -q
```

두 번째 명령은 명시적인 테스트 전용 PostgreSQL 17과 `TEST_DB_ALLOW_CREATE=1`·admin/owner/app 테스트 계정이 필요하다. fixture는 매번 무작위 `creative_checker_test_*` DB를 만들고 그 DB만 제거한다. 기존 운영 DB를 지정하지 않는다. 샘플 파일은 `docs/poster-baselines/2026-11-03/workflow/client-inputs`의 PDF 3종을 사용한다. 검토표·정답 JSON은 추출 입력으로 보내지 않는다.

격리 테스트에서 샘플 3종 저장·동일 바이트 재조회, 기존 M008 보존, AF001→AF002 반복 적용, 세션·CSRF·권한, 버전 이력, 중복/동시 요청, 변조 차단, 저장 중 권한 회수, commit 응답 불명과 파일 공개 중단의 복구를 확인했다. 기존 실행·승인·worker PostgreSQL 회귀 테스트도 함께 통과했다. Windows 실제 백엔드, Chrome UI, 원격 CI, Qwen 추론·추출 정확도는 이 검증에 포함하지 않는다.
