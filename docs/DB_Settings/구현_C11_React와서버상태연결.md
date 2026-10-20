# C11 — React와 서버 상태 연결

작성일: 2026-10-18

상태: **C11 구현·검증 완료.** v1 단위 35개·브라우저 14개, 기존 프런트 단위 18개·브라우저 16개, Python 기본 592개·하위 7개 통과. 신규 C11 사례는 49개다. 브라우저는 실제 Chrome에서 실행했으며 **API는 계약에 맞춘 합성 응답**이다. 실제 PostgreSQL·API·OCR·프록시를 한 동선으로 검증한 결과는 아니다.

기존 시연 프로필과 `admin/admin`을 유지하며 새 화면을 명시적 `v1` 빌드 프로필로 추가했다. M008·백엔드·기존 OCR/인증/API·실행 스크립트·기존 배포용 빌드는 보존했다. 이번에 일상 DB에 접속하거나 migration·계정 발급·seed를 실행하지 않았다. 다음은 **C12 — 통합 E2E·개발/운영 프록시·실제 OCR 선택 경로 검증**이다.

## 1. 코드와 프로필

| 파일 | 책임 |
|---|---|
| [main.jsx](../../frontend/src/main.jsx), [vite.config.js](../../frontend/vite.config.js), [package.json](../../frontend/package.json) | 기본 legacy 유지, v1 선택·별도 출력·테스트 명령. v1 모듈은 선택 시 지연 로드 |
| [v1/App.jsx](../../frontend/src/v1/App.jsx), [auth.jsx](../../frontend/src/v1/auth.jsx) | Data Router·UUID 경로·이메일/개발 로그인·서버 세션·허용 주소 복귀 |
| [api.js](../../frontend/src/v1/api.js), [contracts.js](../../frontend/src/v1/contracts.js) | 동일 출처 /api/v1 전용, meta/UUID/필수 상태·revision 검사, 안전한 오류·CSRF·업로드·export |
| [pending.js](../../frontend/src/v1/pending.js) | 버전 있는 미확인 요청 키/본문 보관·같은 요청 복구·계정별 분리 |
| [hooks.jsx](../../frontend/src/v1/hooks.jsx), [polling.js](../../frontend/src/v1/polling.js) | 조회/변경 분리·중복 클릭 차단·취소·늦은 응답 폐기·dirty 이탈 안내·직렬 폴링 |
| [projectPages.jsx](../../frontend/src/v1/projectPages.jsx) | 프로젝트·역할/보관·이미지·기준 묶음·초안/확정본 목록 |
| [criteriaPages.jsx](../../frontend/src/v1/criteriaPages.jsx) | 초안 저장·revision 충돌 비교·확정·불변 기준 확인·검수 접수 |
| [reviewPages.jsx](../../frontend/src/v1/reviewPages.jsx), [v1.css](../../frontend/src/v1/v1.css) | 이력·실행/결과·지연 근거/OCR·표시 박스·다운로드·모바일 배치 |
| [tests/v1](../../frontend/tests/v1) | Node 계약/클라이언트/요청/폴링 검사와 격리 Chrome 흐름. fixture는 제품 코드에서 참조하지 않음 |

기본 `npm run dev`, `npm run build`, `run.sh`/`run.cmd`는 기존 프로필을 유지한다. `npm run dev:v1`, `npm run build:v1`, `npm run preview:v1`은 v1을 선택한다. 명시적 `VITE_API_PROFILE=legacy` 또는 `v1` 환경 값이 우선하며 다른 값은 빌드 설정에서 거부한다. 프로필은 빌드 시 결정하므로 preview 명령만 바꿔 기존 legacy 파일을 v1으로 전환할 수 없다. v1 결과는 `frontend/dist-v1/`이며 생성물은 Git에서 제외한다.

v1은 `/api/v1`에만 요청한다. 실패 시 구형 JSON 저장·샘플 데이터로 전환하지 않는다. 기존 파서의 순수한 줄 분리만 재사용하고 메모리 확정·정수 버전 증가·multipart 검수 생성·현재 화면 JSON 직렬화는 사용하지 않는다.

## 2. 새 화면 실행

먼저 [C08 인증/계정](구현_C08_인증과계정관리.md)·[C09 파일 저장소](구현_C09_파일저장과기준확정.md)·[C10 실행 설정](구현_C10_검수접수와원자적결과.md)의 별도 v1 서버를 준비한다. 아래 명령이 DB를 생성하거나 사용자를 발급하지는 않는다. 기존 시제품 서버에 연결하면 계약 오류로 표시한다.

저장소의 `frontend` 폴더에서 실행한다. 명령은 Bash·PowerShell 공통이다.

```text
npm run dev:v1
```

기본 API 대상은 `http://127.0.0.1:8000`이며 `API_PROXY_TARGET`으로 변경한다. 예를 들어 v1 서버가 8001이면 Vite 실행 전에 다음을 지정한다.

```bash
export API_PROXY_TARGET=http://127.0.0.1:8001
npm run dev:v1
```

```powershell
$env:API_PROXY_TARGET = "http://127.0.0.1:8001"
npm run dev:v1
```

Vite에 표시된 주소로 접속한다. 첫 이메일 계정은 C08 CLI로 발급한 계정을 사용한다. `auth/csrf`가 `developer_login_available=true`를 반환할 때만 별도의 개발 계정 전환 버튼을 표시한다. 개발 계정 사용 조건과 서버 설정은 C08 기준이며 UI에서 비밀번호를 하드코딩하지 않는다. 공개 가입·비밀번호 재설정 UI는 이번 범위가 아니다.

쿠키·CSRF·Origin 허용값·URL 재진입을 포함한 실제 개발/운영 프록시 설정의 종합 검증은 C12에서 수행한다. `health.checks.db=ready`는 ‘서버 연결됨’으로만 표시하며 모델 실행 준비 완료로 표시하지 않는다. C10 기본 `REVIEW_PIPELINE=disabled`에서는 새 검수가 503일 수 있다. 새 키/다른 OCR로 자동 재시도하지 않는다.

## 3. 서버 UUID와 저장 흐름

| 경로 | 복원/동작 |
|---|---|
| `/projects` | 현재 사용자 프로젝트·보관 필터·cursor·명시적 생성 |
| `/projects/{project}/review/new` | 이미지 등록·등록 이미지 재선택·기준 묶음·요구사항·초안 저장 |
| `/projects/{project}/drafts` | 편집 초안/확정 기준 목록·cursor |
| `/projects/{project}/drafts/{draft}` | 서버 revision 문자열·5필드 항목·원문/출처 보존·충돌 비교·확정 |
| `/projects/{project}/criteria/{version}` | 서버 version_no·과거 확정본·실제 원 초안 링크·현재 일치 여부 |
| `/projects/{project}/reviews` | 실행 상태 필터·cursor·뒤로 가기/새로고침 |
| `/projects/{project}/reviews/{run}` | queued/running/completed/failed/imported·근거·OCR·export·명시적 재검수 |
| `/review/new` | 로그인 후 프로젝트 목록으로 연결 |

업로드 전 File은 URL/저장소에 보관하지 않으며 이탈 시 재선택을 안내한다. 등록이 성공하면 image UUID를 query에 넣고 서버 표시 이미지를 GET한다. 복원 과정에서 업로드·확정·검수 POST를 실행하지 않는다. 읽기 응답의 project가 URL과 다르면 로컬에서도 404 오류로 처리하고 내용을 표시하지 않는다. 실제 서버 인가 검증을 대신하는 기능은 아니다.

새 기준 묶음·초안은 명시적 버튼으로만 생성한다. 업로드/초안 생성에는 HTTP 멱등 계약이 없으므로 오류 뒤 자동 재저장하지 않으며 목록에서 저장 여부를 확인하도록 안내한다. viewer/보관 프로젝트의 신규 쓰기 버튼을 막고 서버의 최종 권한 검사는 그대로 따른다.

## 4. 미저장 편집과 충돌

초안 원문·전체 항목의 id/text/type/source/selected·source_documents를 저장한다. 미선택 항목과 NULL 출처도 유지한다. Unicode 코드 포인트로 원문 20,000자·항목 100개·문구/출처 500자를 검사하며 자동으로 잘라 저장하지 않는다.

revision은 BIGINT 범위의 10진 **문자열**로 왕복한다. 저장 성공 응답 전에는 기준 확정을 막고, 원문 수정이 항목을 자동 덮어쓰지 않는다. 요구사항에서 추가 항목을 만들거나 편집·삭제할 수 있다. 저장된 서버 기준 번호를 사용하며 브라우저에서 버전을 증가시키지 않는다.

DRAFT_REVISION_CONFLICT만 최신 초안을 GET하여 내 편집과 함께 표시한다. 내 버퍼를 자동 덮어쓰거나 새 revision으로 재제출하지 않는다. 사용자가 명시적으로 최신본을 불러와 다시 편집한다. 다른 409는 원래 오류를 표시한다. 미저장 상태의 내부 경로 이동·새로고침/닫기·명시적 로그아웃을 안내하며 취소하면 편집을 유지한다. 세션 만료/계정 전환 때 편집을 다른 사용자에게 복원하지 않는다.

## 5. 응답 미확인과 조회

확정·검수 접수·재검수는 actor/operation/project/body/source_run과 UUID v4 키를 요청 전에 동결한다. sessionStorage에는 이 식별/요청 정보만 버전 1로 보관한다. 비밀번호·세션/CSRF 토큰·이미지·결과는 넣지 않는다. 같은 사용자 재로그인은 보관하고 다른 사용자 확인/성공한 명시적 로그아웃은 제거한다. storage 오류는 메모리 보관과 새로고침 한계를 화면에 알린다.

자동으로 변경 요청을 재실행하지 않는다. 새로고침 후에도 사용자가 ‘같은 요청으로 결과 확인’을 선택해야 같은 키/본문을 보낸다. 성공 응답의 프로젝트·출처 revision·이미지/기준·retry_of를 대조한 후에만 기록을 지운다. 결과가 미확인인 상태에서 본문을 바꿔 새 키로 덮어쓰지 않는다. 기록 지우기는 서버 이력 확인을 거쳐 선택하며 서버 작업 취소를 뜻하지 않는다.

진행 조회는 완료 뒤 2초, 통신 오류는 2/4/8/15초, 비활성 탭은 15초로 직렬 실행한다. terminal/imported·401/403/404/422·경로 이탈·로그아웃 때 중단한다. 재진입·탭 활성화·명시적 조회는 GET이다. AbortSignal과 세대 번호로 늦은 응답을 버린다. 통신 오류 중에는 마지막 서버 상태를 유지하며 failed/pass나 임의 진행률을 만들지 않는다.

## 6. 근거·OCR·내보내기

항목별 처리 상태와 자동 verdict를 구분하고 failed/unsupported/imported의 NULL 판정을 유지한다. 구조화된 observed_value는 항목/값으로 표시한다. 전체성 false와 가져온 과거 이력을 따로 안내하며 사람 승인/학습 라벨 버튼은 추가하지 않았다.

근거와 OCR은 사용자가 선택할 때 조회하고 cursor로 더 불러온다. 개수·일부/전체·조회 실패를 구분하며 0개와 미조회를 합치지 않는다. confidence=NULL은 ‘미등록’이다. evidence의 텍스트와 OCR 원문을 확인할 수 있으며 token ID는 서버 제공값만 사용한다.

박스는 image frame_id/coordinate_space와 일치하고 실제 이미지 치수가 DTO와 같으며 유한·범위 내인 bbox에만 표시한다. OCR 2배 입력 좌표로 다시 나누거나 바깥 좌표를 강제로 잘라 넣지 않는다. 위치가 없거나 좌표계가 다른 근거는 원문 조회만 허용한다. 현재 위치 UI는 bbox를 사용하며 polygon에서 가상 사각형을 계산하지 않는다.

다운로드는 서버 export의 원래 JSON 바이트를 받아 contract_version/run/project와 50 MiB 한도를 확인한다. 현재 상세 화면으로 임의 JSON을 만들지 않는다. 업로드 미리보기와 다운로드의 object URL은 사용 후/이탈 시 해제한다.

## 7. 검증과 재현

| 검증 | 이번 결과 | 범위 |
|---|---:|---|
| `npm test` | 18 통과 | 기존 프런트 단위 회귀 |
| `npm run test:v1` | 35 통과 | 계약·CSRF/클라이언트·pending·폴링·늦은 응답·한도 |
| 기존 `tests/e2e/login.spec.mjs` | 16 통과 | legacy 빌드 + 격리 Chrome + 합성 API |
| `npm run test:e2e:v1` | 14 통과 | v1 빌드 + 격리 Chrome + 합성 API, 1440px/390px 화면 |
| 기본/v1 Vite 빌드 | 모두 성공 | Node 22.23.3, npm 10.9.9, 설치된 잠금 파일의 패키지 사용 |
| `.venv/bin/python -m pytest -q` | 592 + 하위 7 통과 | DB 표식 1,265개 제외. 백엔드 소스/기존 시험은 불변 |

v1 브라우저 재현은 `npm run build:v1` 후 별도 터미널에서 아래 preview를 실행하고, 다른 터미널에서 `npm run test:e2e:v1`을 실행한다. 테스트는 설치된 Google Chrome/Playwright를 사용하며 개인 브라우저 세션을 사용하거나 실행 중 다운로드하지 않는다.

```text
npm run preview:v1 -- --port 8131 --strictPort
```

`E2E_V1_BASE_URL`로 시험 주소를 바꿀 수 있다. fixture가 모든 `/api/`를 가로채며 정의하지 않은 API/다른 출처 요청은 거부한다. 실제 사용자·DB·유료 호출·모델 실행이 없는 UI 검증이다. 대형 파일 경계/DB 경쟁/실제 권한·바이트 저장은 C09/C10의 별도 증거이며 이번 브라우저 결과로 재인증하지 않았다.

[대응표](검증_C11_UI와API_대응표.md) · [최종 증거 JSON](verification/2026-10-18-implementation-C11.json) · [데스크톱 합성 화면](verification/C11/desktop.png) · [모바일 합성 화면](verification/C11/mobile.png).

## 8. 다음 경계

C11은 새 화면 구현과 계약 기반 브라우저 시험까지 완료했다. 실제 v1 백엔드·DB·쿠키/CSRF·프록시·선택한 OCR 모델을 연결한 E2E와 운영 HTML fallback/보안 헤더는 C12다. 독립 worker는 C13, 이전·GC·백업/복원은 C14, 운영 전환은 C15이며 자동으로 진행하지 않는다.
