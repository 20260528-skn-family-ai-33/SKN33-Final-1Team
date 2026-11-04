# A02 PDF·Qwen·저장·승인 연결 구조

2026-11-02 · 설계안 · feat/37-a02-pdf-postgres · 관련 Issue #37 / #23 / #32

2026-11-03 구현 갱신: `feat/37-a02-pdf-registration`에서 **4단계 PDF 등록·버전·해시·기존 프로젝트 권한 연결**을 구현했다. 아래 본문은 최초 전체 설계의 기록이다. 현재 실행 범위는 [PDF 등록 가이드](PDF_등록_버전저장_가이드.html), 단계별 상태는 [진행현황](../poster-baselines/2026-11-03/workflow/진행현황.json)을 따른다. Qwen 추출·조건 저장·기준 승인은 계속 후속 작업이며 운영 DB는 변경하지 않았다.

## 01. 이번 결정과 현재 상태

6단계 갱신: [기준 보완·승인 가이드](기준_보완승인_가이드.html)의 AF003 불변 버전·보완 출처·기존 승인 이력 연결·승인 대상 해시·재조회를 구현했다. 개발용 합성 보완/승인으로 검증했으며 실제 회신·승인·Qwen 추론은 수행하지 않았다. PDF-only 승인 뒤 `waiting_asset`에서 대기한다.

5단계 갱신: [A02 조건 추출 가이드](A02_조건추출_연결가이드.html)의 PDF-only 접수·worker·출처 검증·미승인 초안/본문 trace 저장·재조회를 구현했다. mock과 가짜 HTTP transport로 검증했으며 실제 Qwen 서버는 아직 없고 보완·기준 승인은 후속이다. 아래 최초 설계의 미구현 표기는 이 갱신과 단계별 진행 기록에 맞춰 읽는다.

사용자 요청에 따라 이번 단계는 **실제 서버 구축과 제품 코드 구현 전의 구조 설계**로 제한한다. Qwen 서버 주소는 아직 없다. 새 GPU 인스턴스·컨테이너·모델 다운로드·운영 DB 마이그레이션을 실행한 상태가 아니다.

프레임워크 PR #47이 아직 열려 있어, 검증된 `feat/agent-framework`의 `70931cd`에서 `feat/37-a02-pdf-postgres` 브랜치를 만들었다. 후속 구현이 끝나도 공통 프레임워크의 main 반영 여부와 비교 기준을 다시 확인한다. 이 문서에서 제안한 API·파일·테이블·상태는 현재 실행 가능한 기능이 아니다.

| 구성 | 현재 확인한 코드 | 이번에 정할 연결 |
|---|---|---|
| 공통 실행 | A02 추출 phase, 승인 대기, PostgreSQL worker·lease·단계 저장 | 실제 파일과 Qwen 어댑터 주입 |
| 실제 파일 검증 | VerifiedFileAccess, FileBackedExtractor, 기존 저장소 reader | M008 사용자·프로젝트에 속한 PDF 버전 조회 |
| A02 | TeamPDFExtractor는 미설정 stub | 검증 PDF → Qwen → CriteriaDraft |
| 기존 DB | 동일 M008 DB의 agent_framework 실행·단계·승인 저장 | PDF 등록 메타데이터와 승인 기준 조회 |
| Qwen 추론 | 별도 제품 경로에 Qwen3.5-4B HTTP client 존재 | GPU 서버 구축과 단일 호출 정책 적용 |
| PDF 처리 | 기존 코드에 텍스트 PDF 본문·페이지 추출 기능 존재 | 원문 블록과 A02 계약의 출처 매핑 |

## 02. 세 계층의 책임

**업무 API·A02 worker·Qwen 모델 서버를 나눈다.** PostgreSQL을 업무 기록의 정본으로 사용하고 PDF 원본 바이트는 기존 파일 저장 기능을 재사용해 보관한다.

| 계층 | 담당 내용 | 보유하지 않을 책임 |
|---|---|---|
| 업무 API | 기존 로그인·CSRF·프로젝트 권한, PDF 등록, 추출 접수, 조건 조회·승인 | 모델 적재·추론 실행 |
| A02 worker | 파일·해시·버전 확인, PDF 입력 준비, Qwen 호출, 응답·출처 검증, 초안 확정 | 사람의 승인 자동 생성 |
| Qwen GPU 서버 | Qwen3.5-4B 적재와 OpenAI 호환 추론 응답 | PostgreSQL 계정, 업무 권한·기준 승인·직접 DB 저장 |

GPU 서버 위치는 기존 방향에 맞춰 RunPod를 우선 검토하고, 추론 런타임은 vLLM을 1차 후보로 둔다. 모델 공식 문서에서 Qwen3.5-4B의 vLLM 서빙과 Chat Completions 사용 방법을 확인했다. 실제 GPU 종류·메모리·CUDA·양자화·런타임 버전은 구축 시 호환성을 확인해 고정한다. 이 문서에서 특정 GPU의 성능·최소 VRAM·처리 시간을 확정하지 않는다. [공식 모델 문서](https://huggingface.co/Qwen/Qwen3.5-4B)

모델 프로세스는 서버 시작 시 모델을 적재해 재사용한다. 초기 추론 동시 실행은 1개부터 검증하는 안이다. 모델 캐시·기존 RunPod 작업물은 보존하며 API 프로세스가 시작될 때 GPU 서버를 자동 생성하지 않는다.

## 03. 첫 연결 범위와 업무 흐름

1차 구현은 **텍스트 PDF 한 건의 등록 → Qwen 조건 추출 → 초안 저장 → 사람의 기준 승인 → 승인 버전 재조회**를 완료 기준으로 삼는다. 기존 파일 검증 한도인 PDF 20MiB·50페이지를 상한으로 시작하되, 실제 모델 입력은 별도의 호출·토큰 예산으로 제한한다.

1. 사용자가 접근 가능한 프로젝트에 PDF를 제출한다.
2. API가 권한·형식·페이지·크기·실제 바이트 SHA-256을 확인한다.
3. 원본 파일을 보관하고 서버가 문서 버전·파일 ID를 발급한다.
4. API가 실행 ID·조건 버전 ID를 발급하고 A02 추출 작업을 PostgreSQL에 접수한다.
5. worker가 권한·고정 버전·바이트 해시를 다시 확인하고 PDF 본문·페이지·블록을 준비한다.
6. A02 어댑터가 Qwen 서버를 호출하고 원문·출처·스키마를 검증한다.
7. 검증한 CriteriaDraft·단계 결과·실행 상태를 같은 DB 트랜잭션으로 저장한다.
8. 사용자가 PDF 원문과 초안을 대조하고 기준을 승인한다.
9. 서버가 승인자·기록 시각·정확한 문서/기준 버전과 승인 근거 묶음을 저장한다.
10. 새 API 인스턴스에서도 같은 실행·조건·승인 정보를 조회한다.

스캔·이미지만 있는 PDF는 이 첫 단계에서 지원 여부를 명시적으로 판별하고, 지원하지 않으면 실패 사유를 반환한다. 후속 지원 시에도 Qwen 기반 경로를 설계하며 별도 PaddleOCR·OCR fallback을 추가하지 않는다. PDF 밖의 DOCX/HWPX·영상·포스터 검사·front-v2 화면 변경은 이번 A02 첫 연결 범위에 포함하지 않는다.

## 04. 포스터 없이 시작할 때의 상태

현재 Workflow.start는 PDF와 포스터 참조를 함께 요구하고, 기준 승인 뒤 검수 phase를 접수한다. PDF만 추출·승인하는 작업에는 이 부분을 보완해야 한다.

| 단계 | 제안 상태 | 실행되는 동작 |
|---|---|---|
| PDF 추출 접수 | queued | A02 작업만 생성 |
| worker 선점 | extracting | 실제 PDF/Qwen 추출 |
| 초안 저장 | waiting_criteria | 사람의 원문 대조·승인 대기 |
| PDF 기준 승인 | waiting_asset (신규 상태 제안) | 승인·근거 저장, 포스터 검수는 접수하지 않음 |
| 이후 포스터 연결 | 후속 업무 통합에서 정의 | 문서·기준·산출물 문맥 고정과 필요한 기준 재승인 |

존재하지 않는 포스터 ID나 합성 AssetRef로 A02를 실행하지 않는다. 새 상태와 optional asset 지원은 Workflow·PostgresStore·worker·OpenAPI에 함께 반영해야 한다. 기존 PDF+포스터 전체 실행의 승인·검수 동작은 유지한다. 문서 기준 승인과 새 산출물 실행의 승인을 혼동하거나 부모 승인을 자동 승계하지 않는다.

## 05. Qwen 요청과 응답의 경계

worker와 추론 서버의 HTTP 경계는 OpenAI 호환 `POST /v1/chat/completions`로 잡는다. 서버 주소는 신뢰한 배포 설정에서만 읽고 사용자 업로드나 요청 본문이 모델 서버 주소를 지정할 수 없도록 한다. [Qwen의 API 서빙 설명](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)

| 구분 | 보낼 내용 / 확인할 내용 |
|---|---|
| 모델 | Qwen/Qwen3.5-4B 및 실제 배포 revision |
| 입력 | PDF에서 읽은 블록 ID·페이지·본문, 추출 지시·응답 스키마 |
| 출력 초안 | 조건 설명·종류·기대값·연산자·필수 여부·원문 블록·페이지·인용 |
| 서버가 부여 | 실행·문서·기준 버전 ID, 승인자, 승인 시각, 검증된 인용 해시 |
| 서버 검증 | 원문 블록/페이지 존재, 인용의 원문 일치, 기대값의 근거, 중복·누락·잘린 응답 |
| 보류 범위 | 미지원 조건·AND/OR·예외·조건부 범위를 단순 문자열 비교로 바꿀 수 없으면 unsupported/manual 및 경고 유지 |

문서 안의 명령은 자료로 취급한다. 모델이 프로젝트·문서 버전·승인 결과를 발급하지 않는다. 파일 SHA-256과 원문 인용 SHA-256을 각각 계산한다. 조건 원문·기대값을 임의로 잘라 자동 충족으로 만들지 않는다. 빈·공백 텍스트 기대값은 기존 A06 정책대로 review로 남는다.

기존 QwenClient에는 네트워크 재시도와 응답 보정이 내장돼 있다. 이를 그대로 A02에 붙이면 프레임워크의 호출 불명·자동 중복 호출 방지 규칙과 어긋난다. 재사용 시 **프레임워크 경로에서 HTTP 재시도·형식 보정 재호출을 명시적으로 끄는 연결부**가 필요하다. PDF 분할 처리가 필요한 경우에도 입력 범위·호출 순서·완료/미완료 구간·총 예산을 명시한다.

모델 응답이 도착한 뒤 스키마·출처 검증에 실패하면 실행 실패로 기록한다. 요청 후 통신이 끊겨 실행 여부를 판단할 수 없으면 outcome_unknown으로 처리하고 자동 재호출하지 않는다. 정상 JSON 수신만으로 추출 정확도·누락 없음·사람 승인을 확정하지 않는다.

## 06. PostgreSQL과 파일 저장 구조

기존 M008 데이터베이스·사용자·프로젝트·세션·권한을 재사용한다. 별도 DB나 cc_* 사용자 체계를 다시 생성해 연결하지 않는다. 기존 파일 저장 구현의 재사용 범위와 PDF 보관 위치를 먼저 정하고, M008의 이미지 저장소 정리 작업과 새 PDF 파일이 충돌하지 않도록 한다.

| 기록 | 제안 저장 위치 | 원칙 |
|---|---|---|
| PDF 원본 바이트 | 기존 LocalStorage 코드 재사용, PDF용 명시적 보관 범위 | 불변 파일, 서버 발급 경로, 임의 경로 입력 금지 |
| PDF 등록 메타데이터 | AF002 추가 마이그레이션의 문서 버전 기록 (제안) | 프로젝트·파일·문서 버전·해시·크기·페이지·등록자 연결 |
| 추출 입력·설정 | 기존 agent_framework.runs | 고정 입력과 설정·공통 코드 revision 유지 |
| 조건 초안·모델 단계 결과 | 기존 steps.output_json 및 runs.snapshot_json | 중복 기준 테이블을 먼저 만들지 않고 기존 확정 경계를 사용 |
| 승인 정보 | 기존 approvals·events 및 승인 후 snapshot | 승인자·서버 시각·사유·대상 문서/기준 버전 보존 |
| 조회 결과 | 위 기록을 권한 확인 후 조합 | draft/approved 상태와 문서/기준 버전·경고 표시 |

조건은 우선 기존 JSONB 저장 경로를 재사용한다. 기존 제품의 public.criteria_versions·criteria_items에 같은 내용을 동시에 쓰는 방식은 채택하지 않는다. 나중에 공식 제품 기준 테이블로 연동할 때 별도 매핑·트랜잭션·중복 처리 정책을 정한다.

M008에는 새 A02용 PDF 버전 연결이 없어 추가 문서 기록이 필요하다. AF001.sql을 덮어쓰지 않고 AF002의 명시적 후속 마이그레이션으로 설계한다. owner만 적용하고 app/worker는 제한된 조회·등록 권한을 사용한다. public M008 스키마의 기존 계약·사용자 자료·.env는 보존한다. 이 문서 작성으로 AF002나 운영 DB를 적용한 것은 아니다.

모델 호출 동안 DB 트랜잭션·행 잠금을 유지하지 않는다. 기존 worker의 lease·heartbeat·token을 사용하고 저장 직전에 현재 권한과 소유권을 다시 확인한다. 파일 저장과 DB 커밋이 자동으로 원자적이라고 가정하지 않으며, 커밋 결과 불명 때 파일을 임의 삭제하거나 재등록하지 않는다.

## 07. 계획 API와 코드 연결 위치

아래 경로와 파일은 **구현 예정안**이다. 현재 명령이나 API로 실행할 수 있다고 표시하지 않는다.

| API 제안 | 역할 |
|---|---|
| POST /api/framework/v1/projects/{project_id}/documents | 인증된 PDF 등록, 서버 발급 DocumentRef 반환 |
| POST /api/framework/v1/projects/{project_id}/documents/{document_version_id}/extractions | A02 전용 실행 접수, run/criteria version 발급 |
| GET /api/framework/v1/runs/{run_id}/criteria | 저장된 조건·출처·draft/approved 상태 조회 |
| POST /api/framework/v1/runs/{run_id}/approvals | 기존 승인 계약 재사용, 기준 승인 저장 |
| GET /api/framework/v1/runs/{run_id} | 기존 실행·오류·경고·현재 상태 조회 |

| 연결 위치 | 현재 / 예정 |
|---|---|
| backend/agent_framework/adapters/file_backed.py | 기존 FileBackedExtractor 재사용 |
| backend/agent_framework/adapters/qwen_pdf.py | 신규 예정: PDF·Qwen 조건 추출·계약 매핑 |
| backend/agent_framework/documents.py | 신규 예정: PDF 등록·버전 조회·카탈로그 연결 |
| backend/agent_framework/postgres_api.py / postgres.py | 기존 인증 경계·접수·저장·승인·조회 확장 |
| backend/agent_framework/runtime.py / durable_worker.py | A02 전용 시작과 포스터 대기, 기존 phase/lease 유지 |
| backend/creative_checker/qwen.py / documents.py | 기존 HTTP·PDF 처리의 재사용 후보, 현재 제품 계약과 직접 혼합하지 않음 |
| deploy/qwen/ | 신규 예정: 서버 이미지·실행 설정·모델 revision·상태 확인 문서 |

PDF 등록·추출 접수는 멱등 키와 입력 해시로 같은 요청의 재전송을 같은 접수로 처리한다. 같은 키에 다른 PDF·설정을 넣으면 충돌로 거부하고, 시간 초과 뒤 새 실행 ID를 자동 발급해 모델을 중복 호출하지 않는다.

A02 factory는 worker의 명시적인 설정·파일 접근·DB 의존성을 주입받도록 조립한다. import나 API 시작만으로 모델 적재·네트워크 호출·마이그레이션을 실행하지 않는다. 나머지 담당자의 factory 규약을 한꺼번에 바꾸지 않는다.

## 08. 설정·진단과 서버 없는 개발

설정은 기존 명시적 app/worker 파일을 사용하고 owner 자격증명은 분리한다. 실제 서버 주소와 키는 공유 JSON·Git·표준 출력에 넣지 않는다.

| 설정 후보 | 용도 |
|---|---|
| CC_QWEN_BASE_URL | 신뢰한 Qwen API 주소 |
| CC_QWEN_API_KEY | worker 전용 비밀키 |
| CC_QWEN_MODEL | Qwen/Qwen3.5-4B |
| CC_QWEN_REVISION | 실제 배포한 모델 revision |
| FRAMEWORK_PDF_STORAGE_ROOT | PDF 전용으로 명시한 보관 범위 |

서버가 없어도 어댑터 입력·출력, 실제 PDF 바이트·출처 검증, HTTP 모의 응답, 격리 PostgreSQL의 저장·승인·재조회는 개발할 수 있다. 모의 응답은 테스트에서만 주입하고 실모델 연결 성공으로 표시하지 않는다. 런타임에서 서버 설정이 없거나 연결이 실패하면 합성 조건으로 대체하지 않는다.

기존 doctor의 설정 검사와 실제 모델 상태 확인을 구분한다. 구축 후 상태 확인은 접속·로드 여부를, 실제 PDF 추론은 조건·원문·누락·시간을 확인하는 별도 시험으로 수행한다. JSON Schema 제약이 의미 정확도나 사람의 승인까지 보증하지 않는다.

## 09. 구현 순서와 완료 기준

| 순서 | 작업 | 확인할 결과 |
|---|---|---|
| 1 | A02 전용 계약·상태·저장 경계 확정 | 포스터 없는 시작, 미확정 조건·승인 범위 명시 |
| 2 | PDF 등록·버전·AF002 설계/구현 | 기존 M008 보존, 실제 해시·파일·권한 검증 |
| 3 | Qwen 어댑터와 HTTP 모의 시험 | 정상·잘린 JSON·위조 출처·빈 기대값·통신 불명 차단 |
| 4 | 기존 DB 저장·승인·재조회 연결 | 새 API/worker 인스턴스에서 같은 조건과 승인 복원 |
| 5 | GPU 서버 구축 | 모델·런타임·이미지 revision 고정, API 접근·모델 로드 확인 |
| 6 | 실제 PDF 1건의 관통 시험 | 실제 모델 추출 → 원문 대조 → 기준 승인 → DB 재조회 증거 |

단계 1~4는 모델 서버를 기다리지 않고 진행할 수 있다. 단계 5~6 전에는 GPU 환경·API 접근 설정과 사용할 실제 PDF를 준비한다. PDF 한 건의 관통 성공과 다양한 문서에 대한 추출 품질 평가는 구분한다.

이번 설계 완료는 위 구현·배포의 완료가 아니다. 현재 브랜치에는 설계문서·열람용 HTML·문서 재생성 도구를 추가하고, API·DB·모델 기능 코드는 다음 구현 단계에서 변경한다.

## 10. 참고 자료

- [현재 담당자 가이드](에이전트_프레임워크_가이드.html)
- [현재 PostgreSQL 실행가이드](에이전트_프레임워크_PostgreSQL_실행가이드.html)
- [공통 프레임워크 전체 계획](../planning/pdf-qwen-rag-workflow-plan-2026-11-02.html)
- [Issue 37: Qwen 문서·포스터 베타 연결](https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team/issues/37)
- [공식 Qwen3.5-4B 모델·서빙 안내](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Qwen 공식 vLLM API 설명](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)

공식 문서는 모델과 API 구조의 근거로 사용했다. 실제 GPU·모델 적재·호환성·속도·정확도 검증 결과를 대신하지 않는다.
