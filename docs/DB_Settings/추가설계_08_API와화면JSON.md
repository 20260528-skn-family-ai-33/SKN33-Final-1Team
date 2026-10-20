# 추가 설계 8단계 — API·화면·JSON·해시 계약

작성일: 2026-10-17

상태: **8단계 설계 완료.** 서버 초안→기준 확정→검수 접수, 상세·목록·다운로드, 화면 복원, 기존 API 전환, JSON 한도와 해시 입력을 정의한다. 문서와 합성 예제 데이터만 추가하며 애플리케이션 코드·SQL·DB·계정·기존 실행 결과는 변경하지 않는다.

## 1. 기준과 이번 결정

[인증·권한 7단계](추가설계_07_인증과권한.md), [기준 4단계](물리설계_04_검수기준.md), [실행·결과 5단계](물리설계_05_검수실행과결과.md), [전체 정합성](물리설계_06_전체정합성.md), [프로젝트 명세](../project-specification.md)를 잇는다. 현재 근거는 [FastAPI](../../backend/app/api.py), [브라우저 기준·상태](../../frontend/src/workflow.js), [검수 요청·조회](../../frontend/src/pages/review/ReviewProvider.jsx), [API 오류 파서](../../frontend/src/api/client.js), [라우트](../../frontend/src/App.jsx), [결과 화면](../../frontend/src/pages/review/ResultsPage.jsx)다.

| 항목 | 현재 구현 | 이번 목표 결정 |
|---|---|---|
| 기준 확정 | 브라우저 정수 증가·메모리 복사 | 서버 초안 revision 확인 후 불변 UUID·프로젝트별 번호 발급 |
| 제출 | /api/reviews에 multipart 4개 필드 | 이미지 업로드·초안·확정·실행을 분리 |
| 새로고침 | 파일·초안·현재 run이 React 메모리에 있음 | 저장한 draft/run UUID의 URL에서 DB 재조회 |
| 재시도 | 같은 multipart를 다시 전송 | 요청 재전송과 명시적 새 재검수를 구분 |
| 결과 | 기존 JSON 파일을 그대로 반환 | DB의 채택 결과 투영, 큰 OCR/근거는 별도 페이지 조회 |
| 해시 | 원본 이미지 바이트 SHA256 등 일부만 존재 | 내용·요청·입력/결과 manifest별 버전과 고정 예제 |

물리 범위는 **26개 테이블·395개 컬럼·PK 26개·UNIQUE 41개·FK 81개** 그대로다. API를 추가한다는 이유로 범용 멱등 테이블이나 새로운 승인 테이블을 추가하지 않는다. 외부 festival-poster 규격이나 인계 규격의 버전과 내부 `cc-api-v1`을 구분한다.

## 2. 공통 전송·형식·한도

기본 경로는 `/api/v1`, trailing slash 없는 주소다. 아래 상대 경로에 이 접두부를 붙인다. JSON은 application/json·UTF-8이며 요청의 알 수 없는 필드·타입 강제 변환·중복 객체 키를 거부한다. UUID는 유효한 v4를 받아 소문자 표준 표기로 정규화한다. 이름·문구·요구사항은 대소문자·공백·Unicode 정규화를 임의 적용하지 않으며, 공백만인 필수 문구는 검증에서 거부한다. 이메일 정규화만 7단계 예외를 따른다.

| 항목 | 계약 |
|---|---|
| 일반 JSON 요청 | UTF-8 바이트 262144 이하, 중첩 깊이 32 이하, 전체 값 노드 100000 이하 |
| 로그인 JSON | 7단계의 4096바이트 제한 우선 |
| 문자열 길이 | Unicode 코드포인트 수. 프런트 UTF-16 length와 차이가 있으므로 서버가 최종 검증 |
| 프로젝트명 / 기준 묶음명 | 각각 1~120자 / 1~200자. DB 프로젝트명 200자가 API 한도를 늘리지 않음 |
| 요구사항 원문 | 1~20000자, 공백만 입력 거부. 줄바꿈·원문 보존 |
| 초안 항목 | 전체 0~100개. 확정은 선택 항목 1~100개 |
| 항목 ID / text / source | ID 1~100자·초안 안 유일. text 0~500자, source 1~500자. 확정할 text는 공백 아닌 내용 필수 |
| 이미지 | 파일 바이트 20 MiB 이하, 디코딩 후 10000000픽셀·한 변 8192 이하. 실제 PNG/JPEG/WEBP만 |
| 일반 목록 / OCR·근거 목록 | 기본 limit=20, 최대 100. 합계 개수·has_more·next_cursor로 잘림을 명시 |
| 일반 JSON 응답 | 2 MiB 이하. 상세는 OCR·근거 본문을 모두 중첩하지 않음. 초과 시 작은 503 RESPONSE_TOO_LARGE와 사용 가능한 export 주소를 반환하며 조용히 잘라내지 않음 |
| 결과 JSON 다운로드 | 별도 스트리밍 응답, 최초 50 MiB 이하. 초과는 다운로드 전 422 EXPORT_TOO_LARGE |
| 시각 | 서버 응답은 UTC `YYYY-MM-DDTHH:mm:ss.ffffffZ`. 모르는 과거 시각은 null·출처에 원문 보존 |
| BIGINT revision | `"1"` 같은 1~9223372036854775807의 십진 문자열. 선행 0·부호·지수·JSON 숫자 금지 |
| 서버 version_no | 1~2147483647의 JSON 정수. 소진 시 409 VERSION_LIMIT_REACHED, 순환 재사용 금지 |

JSON 문법·상호운용 기준은 [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html)를 따른다. 단위·크기·필드 한도는 프로젝트 정책이다. 일반 API 입력은 유한 binary64 수치만 허용하고 정수값은 안전 정수 범위로 제한한다. NaN·Infinity·단독 surrogate·NUL 문자는 거부한다. 외부 원문에 더 큰 수/정밀도·규격 미확인 값이 있으면 파일 바이트로 보존하고 9단계 수입 검사에서 처리한다. 잘라내거나 반올림한 뒤 원문과 같다고 주장하지 않는다.

일반 JSON 응답에는 `meta: {contract_version:"cc-api-v1", request_id:"<UUID>"}`를 추가하고 같은 request_id를 X-Request-ID로 보낸다. 성공 본문을 data로 한 번 더 감싸지 않는다. 오류에도 같은 meta를 넣는다. 204와 바이너리/파일 응답은 본문 meta가 없고 헤더만 제공한다. 합성 예제의 meta는 비교용 고정값이다.

인증·사용자별 응답은 no-store다. 모든 변경 요청에 7단계 Origin/CSRF를 적용한다. 압축 전후 제한 우회를 막도록 최초 JSON 요청 Content-Encoding은 identity만 허용한다. 파일 크기·실제 디코딩·원격 전송 한도는 각각 검사하며 원본 20 MiB 통과가 RunPod 전송 가능을 뜻하지 않는다.

## 3. API 목록과 권한

V/E/O는 해당 프로젝트의 활성 viewer/editor/owner, U는 활성 로그인 사용자다. 비멤버·타 프로젝트 리소스는 404, 소속이 있지만 쓰기 역할이 부족하면 403이다. 보관 프로젝트의 새 쓰기는 409이며 owner의 복원은 허용한다. 모든 세부 경로에도 같은 검사를 적용한다.

| 메서드 | 상대 경로 | 성공 | 권한·계약 |
|---|---|---|---|
| GET | /auth/csrf | 200 | 공개 준비. csrf_token·developer_login_available·meta |
| POST | /auth/login | 200 | email/password 또는 username/password, 7단계 |
| GET | /auth/me | 200 | authenticated·user/null·meta. DB 장애는 503 |
| POST | /auth/logout | 204 | Origin/CSRF·DB 세션 폐기 |
| GET | /projects | 200 | U. 소속 목록, 보관 포함 여부 필터 |
| POST | /projects | 201 | U. 프로젝트와 owner 함께 생성 |
| GET | /projects/{project_id} | 200 | V/E/O. 현재 내 role·프로젝트 상태 |
| PATCH | /projects/{project_id} | 200 | O. name 또는 archived 변경 |
| GET | /projects/{project_id}/images | 200 | V/E/O. 저장한 이미지 버전 최신순 |
| POST | /projects/{project_id}/images | 201 | E/O. multipart image 하나 |
| GET | /images/{image_id} | 200 | V/E/O. 이미지 버전 메타데이터 |
| GET | /images/{image_id}/content | 200 | V/E/O. 표시 PNG, 원본 저장 키 공개 금지 |
| GET | /projects/{project_id}/criteria-sets | 200 | V/E/O. 기준 묶음 목록 |
| POST | /projects/{project_id}/criteria-sets | 201 | E/O. 명시적 새 묶음 생성 |
| GET | /projects/{project_id}/criteria-drafts | 200 | V/E/O. 초안 목록, image_id/set 필터 |
| POST | /projects/{project_id}/criteria-drafts | 201 | E/O. 이미지·묶음 고정한 편집 초안 |
| GET | /projects/{project_id}/criteria-drafts/{draft_id} | 200 | V/E/O. 전체 초안·revision |
| PUT | /projects/{project_id}/criteria-drafts/{draft_id} | 200 | E/O. expected_revision과 편집 필드 전체 교체 |
| GET | /projects/{project_id}/criteria-versions | 200 | V/E/O. 버전 목록, image_id/set 필터 |
| POST | /projects/{project_id}/criteria-versions | 201/200 | E/O. 초안 확정, 멱등키 필수 |
| GET | /projects/{project_id}/criteria-versions/{criteria_version_id} | 200 | V/E/O. 불변 기준 전체·초안 최신 일치 여부 |
| POST | /reviews | 202/200 | E/O. 새 검수, 멱등키 필수 |
| GET | /projects/{project_id}/reviews | 200 | V/E/O. 이력·status 필터 |
| GET | /reviews/{run_id} | 200 | V/E/O. 현재 상태·기준·채택 결과 요약 |
| POST | /reviews/{run_id}/retry | 202/200 | E/O. 종료된 서비스 실행의 새 재검수 |
| GET | /reviews/{run_id}/image | 200 | V/E/O. 고정 표시 이미지. 현재 업로드 이미지로 대체 금지 |
| GET | /reviews/{run_id}/ocr-records | 200 | V/E/O. 해당 채택 OCR 문구 페이지 |
| GET | /reviews/{run_id}/results/{result_id}/evidence | 200 | V/E/O. 채택 결과의 근거 페이지 |
| GET | /reviews/{run_id}/export | 200 | V/E/O. 로컬 결과 JSON 다운로드 |
| GET | /sample | 200 | U. 번들 예제의 이름·요구사항·image_url |
| GET | /sample/image | 200 | U. 번들 예제 파일. 프로젝트 파일에 대한 우회 경로 없음 |
| GET | /health | 200 | 공개·안전한 준비 상태·한도. 점검 불가 상태는 unknown |

sample은 서버 배포물의 공통 예제만 제공한다. 샘플을 업무로 사용하려면 선택한 프로젝트에 일반 업로드·초안 저장을 거친다. 프로젝트별 비공개 파일을 공통 sample 경로로 노출하지 않는다. 멤버십·계정·전역 규격 관리 API는 만들지 않으며 7단계 보호된 관리 명령 범위를 유지한다.

## 4. 프로젝트·이미지·식별자 대응

`image_id`는 **asset_versions.asset_version_id**다. assets.asset_id와 구분한다. 응답은 asset_id도 별도 제공한다. 최초 업로드는 새 asset+version_no=1이며 같은 이름·해시를 이유로 기존 자료와 자동 병합하지 않는다. 첫 UI는 이미지 교체 시 새 업로드·새 초안을 만들며 과거 초안/확정본의 image_id를 수정하지 않는다.

프로젝트 POST 본문은 `{name}`만 받는다. purpose·due_at은 NULL, use_channels는 []로 시작한다. PATCH는 `{name?,archived?}`이며 적어도 하나가 필요하다. archived는 불리언이고 실제 보관 시각은 서버가 기록한다. 프로젝트 응답은 id/name/business_state/archived_at/my_role/created_at/updated_at이다. 기준 묶음 POST는 `{name}`, 응답은 id/project_id/name/created_at/updated_at이다.

이미지 multipart는 image 필드 하나만 받으며 브라우저가 boundary를 만든다. client Content-Type·확장자·파일명만 믿지 않는다. 파일명은 저장 경로로 쓰지 않으며 실제 형식 확인·EXIF 방향 처리·표시 PNG 생성이 끝나고 DB/파일 저장 성공 후 201을 보낸다. 응답 필드는 다음과 같다.

| 필드 | 자료형·의미 |
|---|---|
| id / asset_id / project_id | 각각 이미지 버전·자료·프로젝트 UUID |
| version_no | 자료 안의 버전 정수 |
| width / height | 표시 PNG의 양의 정수 치수 |
| original_sha256 / display_sha256 | 각각 원본 바이트·실제로 저장한 표시 PNG 바이트의 해시 |
| actual_format / content_type | 디코딩한 PNG/JPEG/WEBP / 표시본 image/png |
| coordinate_space / frame_id | normalized_original_pixels / 실제 표시 프레임 UUID |
| url | 같은 origin의 /api/v1/images/{id}/content 상대 주소 |
| created_at | 로컬 저장 시각 |

현재 명칭 normalized_original_pixels는 이미 **표시 이미지 픽셀**로 복원된 좌표다. OCR 입력의 2배 좌표를 프런트가 다시 나누지 않는다. DB의 frame_kind=display와 연결하며 외부 display_pixels 명칭은 어댑터에서 명시적으로 대응한다. 전처리 정책 변경은 새 이미지·새 계약 버전으로 처리한다.

프로젝트·묶음·업로드·초안 POST에는 이번 범위의 영속 멱등 보장을 붙이지 않는다. 응답 유실 후 자동 POST 반복을 하지 않고 해당 프로젝트 목록에서 생성 여부를 확인한다. 확인 없이 새 요청하면 중복 자료가 생길 수 있음을 UI에서 알린다. 이 제한을 이미지 해시 유일성이나 별도 멱등 테이블로 감추지 않는다. 파일 정리·복구는 [9단계 계약](추가설계_09_저장과이전복구.md)을 따른다.

## 5. 서버 초안과 기준 확정

초안 생성 본문은 criteria_set_id/image_id/raw_requirements/items와 선택적 source_documents다. 처음 revision은 `"1"`, state는 editing이다. 응답은 id/project_id/criteria_set_id/image_id/revision/state/raw_requirements/items/source_documents/updated_by/created_at/updated_at이다. source_documents를 생략하거나 null로 보내면 NULL로 정규화한다.

초안 items는 `{id,text,type,source,selected}` 5개 필드가 필수다. type은 information/visual, selected는 실제 불리언이다. 순서는 배열 순서가 기준이며 id는 초안 안에서 유일하다. 선택되지 않은 빈 text는 편집 상태로 저장할 수 있다. 현재 정규식 분류는 사용자 편집 제안이며 규칙 추론 완료가 아니다. 초기 서비스 입력은 rule_code/field_code/expected_value/is_required 등 추가 필드를 받지 않고 422로 거부한다. 새 확정 항목에서는 해당 미제공 컬럼을 SQL NULL로 둔다. 구조화 기준 입력은 별도 API 계약 개정 대상이다.

source_documents는 null 또는 최대 20개의 `{asset_version_id, locator}` 배열이다. 각 객체의 두 키는 필수다. locator는 null 또는 `{page_no,field_pointer}` 객체이며 두 키를 모두 제공한다. page_no는 양의 정수/null, field_pointer는 255자 이하 문자열/null이다. 동일 프로젝트의 실제 등록 자료만 참조할 수 있다. 아직 등록할 수 없는 문서를 가짜 이미지 버전으로 만들지 않는다. 첫 텍스트 입력 UI는 null을 보내며 source 문자열 자체가 실제 파일 ID라는 뜻은 아니다.

PUT은 `{expected_revision,raw_requirements,items,source_documents}` 전체 편집값을 받는다. project·image·set·state·작성자는 변경 입력이 아니다. 기본값 적용 후 내용이 실제로 같은 요청은 revision을 올리지 않는다. 내용이 다르면 revision을 1 올리고 updated_by/at을 갱신한다. expected_revision 불일치는 내용 비교보다 먼저 409 DRAFT_REVISION_CONFLICT다. 네트워크 단절 후 같은 PUT을 반복해 충돌하면 GET으로 실제 저장 내용을 확인한다. 다른 탭 변경을 자동 덮어쓰지 않는다. revision 최대값이면 409 REVISION_LIMIT_REACHED다.

기준 확정은 다음 한 가지 방식으로 통일한다.

```http
POST /api/v1/projects/{project_id}/criteria-versions
Content-Type: application/json
Idempotency-Key: <UUID v4>
X-CSRFToken: <서명 CSRF 토큰>

{"draft_id":"<UUID>","expected_revision":"3"}
```

본문에 별도 items·브라우저 version을 넣지 않는다. 서버가 잠근 초안의 선택 항목을 읽고 검증한 뒤 버전·항목·멱등 정보·감사 이벤트를 함께 생성·봉인한다. 프로젝트별 서버 번호를 발급하고 첫 성공은 201, 동일 요청 재전송은 200이다. Location은 불변 버전 GET 주소다. 초안은 editing으로 유지하며 후속 편집은 새 확정이 필요하다.

버전 응답은 id/project_id/criteria_set_id/image_id/version_no/origin_kind/confirmation_state/source_draft_id/source_draft_revision/criteria_hash/hash_version/confirmed_by/confirmed_at/criteria 및 `draft_is_current`를 제공한다. criteria 배열은 criteria_item_id·id(외부 항목 ID)·position·text·type·source·is_required를 제공하고 is_required는 null이다. 초안의 미선택 항목은 포함하지 않는다. draft_is_current는 조회 시 권한 있는 서버가 비교한 현재 상태이며 불변 스냅샷·해시에 포함하지 않는다.

서버 기준을 새 실행에 사용할 때 초안 revision이 달라지면 409 CRITERIA_STALE다. **이미 접수한 실행·과거 기준·과거 결과를 무효화하거나 삭제하지 않는다.** 이전 기준을 확인하는 화면은 과거 확정 내용과 현재 편집 초안의 차이를 구분한다. imported/legacy의 unconfirmed 기준은 새 초안에서 사용자 재확정을 거치며 구체적 이전 경로는 9단계에서 연결한다.

## 6. 검수 생성·재검수·요청 재전송

새 검수 POST /reviews 본문은 `{project_id,image_id,criteria_version_id}`뿐이다. 서버 주소·장치·모델·원격 endpoint·실행 옵션은 입력받지 않는다. 서버가 실제 실행 설정과 코드/정책 버전을 검증·고정한다. 원 실행의 설정과 후속 재검수의 현재 서버 설정이 같다고 보장하지 않으며 각각의 manifest로 확인한다.

POST /reviews/{run_id}/retry는 본문 `{}`와 새 멱등키를 받는다. 원 실행이 terminal인 서비스 completed/failed이며 같은 프로젝트·이미지·기준을 사용할 수 있어야 한다. 진행 중은 409 REVIEW_NOT_TERMINAL, imported/legacy 또는 재실행 불가 기준은 409 SOURCE_NOT_RETRYABLE/CRITERIA_NOT_CONFIRMED, 현재 초안 변경은 CRITERIA_STALE다. 입력을 바꾸려면 새 확정 후 일반 POST /reviews를 사용한다.

최초 성공은 202와 `{id,status:"queued",project_id,image_id,criteria_version_id,retry_of,meta}`이며 Location은 상세 GET 주소다. 재전송은 200과 동일 필드·**현재 실제 status**를 반환한다. 원 응답이 queued였더라도 이미 completed이면 completed다. Idempotency-Replayed:true 헤더로 재전송 응답을 표시한다. 202는 접수 의미이며 실행 성공·사람 승인이 아니다. HTTP 의미는 [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-202-accepted)을 참고한다.

| operation | 키의 DB 범위 | 요청 지문 payload |
|---|---|---|
| confirm_criteria | criteria_versions의 (confirmed_by,confirmation_key) | actor_user_id·operation·project_id·body(draft_id,expected_revision) |
| create_review | review_runs의 (requested_by,request_operation,idempotency_key) | actor_user_id·operation·body(project_id,image_id,criteria_version_id) |
| retry_review | 위와 동일하되 다른 operation | actor_user_id·operation·source_run_id·body={} |

사용자·operation·키는 성공 리소스가 보존되는 동안 유지한다. 같은 키에 다른 지문은 409 IDEMPOTENCY_KEY_REUSED다. 키를 바꾸면 같은 이미지·기준도 별도 사용자 요청이다. 멱등키 자체·CSRF·현재 서버 설정·현재 초안 내용·요청 시각은 요청 지문에서 제외한다. 인증·현재 권한·접근 가능한 기존 리소스·기존 키 지문을 확인한 뒤, 새 요청일 때만 현재 초안/설정/원 실행의 상태를 검사한다. 따라서 성공 뒤 초안이나 서버 설정이 바뀌어도 같은 요청 재전송은 기존 리소스를 반환한다. 권한이 사라지면 기존 성공 리소스도 반환하지 않는다.

웹은 사용자 동작마다 키와 **보낸 본문**을 고정하고 응답 유실 시 동일 조합으로 재시도한다. draft 수정 후에는 이전 confirm 키를 재사용하지 않는다. 성공 응답은 UUID URL로 이동한 뒤 pending 기록을 지운다. 서버에서 4xx/5xx가 왔다는 이유로 매번 새 키를 생성하지 않는다. 동시 유일 키 충돌의 롤백·기존 행 조회는 4·5단계를 따른다.

## 7. 상세·결과·OCR·근거 응답

GET /reviews/{run_id}는 아래 외형을 유지한다. [전체 예제](contracts/stage8-examples.json)의 review_completed는 실행하지 않은 합성 데이터다.

| 필드 | 의미·형식 |
|---|---|
| id/project_id/image_id/criteria_version_id | 실제 DB UUID. image_id는 이미지 버전 |
| status/stage/progress | 실행 상태 queued/running/completed/failed/imported, 현재 단계/null, 0~100/null |
| project_name | 접수 당시 project_name_snapshot. 현재 프로젝트명으로 과거를 바꾸지 않음 |
| criteria_version | 새 v1 경로의 서버 version_no. 과거 브라우저 숫자는 source.criteria_version에 별도 보존 |
| origin_kind/source/retry_of | 생성 출처, 원래 상태·버전·시각/null, 재검수 원 실행 UUID/null |
| criteria/image | 불변 항목 스냅샷·고정 표시 이미지. 동일 버전의 UUID/문자열 ID 대응 |
| results | 최종 채택 결과 요약 배열. queued/running/전체 실패는 []·results_complete=false |
| overall_verdict/aggregation_policy_version | 서비스 자동 요약/null·실제 고정 정책. imported는 로컬 verdict=null |
| runtime | 비밀 없는 실제 시도·장치·모델·처리 상태 요약/null. 추정한 원격 성공 표시 금지 |
| ocr | 채택 OCR UUID·record_count·records_url·좌표·입력 치수·scale/null. records 본문은 별도 조회 |
| event_extraction | 추출 UUID·변환/검증 상태·안전한 필드 관찰 요약/null. 원문 JSON은 export에서 분리 |
| error | 안전한 오류 객체/null. 전체 실패·결과 불명과 통신 실패 구분 |
| created_at/updated_at/started_at/finished_at/sealed_at | 실제 로컬 시각 또는 null |

최초 상세에서 results는 최대 100개이며 position 순이다. 각 결과는 result_id/criteria_item_id/criterion_id/criterion/type/processing_status/verdict/status/observed_text/reason/check_method/limitations/evidence/evidence_count/evidence_complete/evidence_url을 제공한다. criterion_id는 같은 버전의 external_item_id이며 수입에서 없으면 null, React key는 criteria_item_id를 사용한다. observed_value가 문자열일 때만 observed_text에 복사하고 구조화 값은 observed_value로 별도 제공한다. 임의 JSON.stringify로 원래 문구를 만들지 않는다.

| 원 상태 | verdict | 표시 status | 의미 |
|---|---|---|---|
| service completed 항목 | pass/fail/review | 같은 값 | 자동 판정. 승인 아님 |
| service failed/unsupported 항목 | null | review | 사유·처리 실패/미지원 표시를 함께 제공 |
| imported 항목 | null | review | 원 판정은 source_status·source_result에 보존, 로컬 판정 아님 |

results_complete=true는 봉인된 결과 집합의 모든 기준 항목이 있다는 뜻이다. imported도 구조가 완전할 때만 true이며 추론·승인이 아니다. 부분 집합을 완전 결과로 표시하지 않는다. first scope는 단일 채택 attempt 전체를 사용하고 이전 실패 시도 결과를 섞지 않는다. 단계 진행률은 실제 보고값이며 타이머로 가짜 증가시키지 않는다. 자동 재시도 시 단계가 되돌아갈 수 있음을 화면에 표시한다.

일반 상세의 evidence는 []이고 실제 개수와 evidence_complete=(개수==0)를 함께 보낸다. 근거가 있으면 evidence_url에서 순서대로 페이지를 읽는다. `[]`만 보고 근거 부재로 판정하거나 잘라낸 5개를 전체라고 표시하지 않는다. 결과 화면은 evidence_count로 위치 보기 버튼을 만들고 조회 중/일부 로드/전체 로드를 구분해야 한다.

근거 객체는 id/source_kind/text/bbox_xyxy/polygon/confidence/frame_id/coordinate_space/token_ids/method_code/limitations를 가진다. 표시 프레임에 대응한 유한 좌표만 오버레이로 그린다. confidence=null은 0이 아니며, 원문 후보·재인식·requires_review·use_for_training=false 같은 미확인 표시는 limitations의 구조화 정보와 원문에 보존한다. 토큰 목록이 큰 경우 token_ids가 아닌 별도 OCR 조회·원문 파일 연결로 대조하며 임의 토큰을 생성하지 않는다.

OCR 문구 페이지는 id(UUID)/external_token_id/external_token_type/order/text/confidence/bbox_xyxy/polygon/coordinate_space/frame_id를 제공한다. 외부 숫자/문자 ID의 타입을 별도 보존한다. bbox 없는 문구는 null을 허용하고 가상의 사각형을 만들지 않는다. 채택 OCR이 아직 없으면 200 items=[]·availability=pending/none, 확정된 채택 OCR은 availability=available이다. 페이지 cursor는 OCR UUID에도 결합해 다른 시도의 토큰을 섞지 않는다.

event_extraction은 내부 필드 관찰의 field_code/presence_status/resolution_status/raw_text/normalized_value와 근거 조회 정보를 제공한다. null/빈 배열만으로 absent를 만들지 않으며 불완전 날짜·복수 장소·후보는 review_needed로 남긴다. schema_unavailable은 실제 외부 스키마 검사 통과가 아니다. 내부 cc-api-v1 외형을 확인한 사실과 외부 포스터 표준 적합성을 구분한다.

## 8. 목록·cursor·다운로드·health

목록 외형은 `{items,has_more,next_cursor,meta}`다. 마지막 페이지는 false/null이며 total_count는 최초 범위에서 제공하지 않는다. OCR·근거의 확정 record_count/evidence_count는 고정 집합의 별도 메타데이터이며 가변 이력의 total_count와 구분한다. status 필터는 실행 상태 전체 enum 중 하나만 허용하며 미지정은 전체다. 프로젝트 목록의 archived는 exclude/include/only, 기본 exclude다. image_id/criteria_set_id 필터는 같은 프로젝트 UUID만 받는다. 알 수 없는 query·중복 query·잘못된 limit은 422다.

프로젝트·이미지·묶음·초안·검수 목록은 (created_at DESC,id DESC), 기준 버전은 (version_no DESC,id DESC), OCR은 (order ASC,id ASC), 근거는 id ASC로 정렬한다. 커서는 마지막 정렬값·리소스 종류·actor UUID·project/run/OCR 문맥·정규화 필터·limit·만료가 담긴 서명 opaque 문자열이다. HMAC-SHA256 서명을 검증하고 CURSOR_SECRET은 32바이트 이상 난수로 CSRF_SECRET과 분리한다. 최초 수명은 24시간, 최대 2048자이며 키 교체 시 기존 cursor는 무효다. 다른 사용자·프로젝트·필터·limit으로 재사용하거나 서명/만료가 틀리면 400 CURSOR_INVALID다. 매 페이지 현재 권한을 재검사한다.

이는 현재 데이터에 대한 keyset 조회이며 전체 목록의 시점 고정 스냅샷을 보장하지 않는다. 조회 도중 추가된 최신 행은 새로고침에서 보이며 역할/보관 상태 변화로 항목이 빠질 수 있다. 프런트는 UUID로 중복 표시를 막고 cursor를 다음 요청에 그대로 전달한다. 실제 SQL 계획과 인덱스 보강 여부는 10단계·구현 검증에서 판단하며 이번 테이블·인덱스 집계는 바꾸지 않는다.

export는 `contract_version:"cc-review-export-v1"`, exported_at, origin/source, 고정 입력 식별자·해시, 기준, 채택 결과·근거·OCR·추출 원문 참조, 로컬 verdict·검증 상태를 포함한다. 보안 키·세션·storage_key·DB/원격 주소는 제외한다. Content-Disposition은 서버 생성한 review-{UUID}.json이며 nosniff를 적용한다. HTTP X-Request-ID는 제공하되 다운로드 파일에 휘발성 meta를 넣지 않는다. 원문 외부 파일을 포함하지 못하면 참조·누락 사유를 표시한다. 이 파일을 외부 handoff 규격 준수 파일로 부르지 않는다. 9단계에서 원문 파일 묶음과 백업/복구를 연결한다.

health는 status=ready/degraded/not_ready, checks, limits를 제공한다. checks의 api/db/worker/ocr_runtime/model은 각각 state=ready/not_ready/unknown·checked_at/null이다. handler가 응답 가능하면 HTTP 200이며 업무 접수 가능 여부는 checks로 판단한다. 확인한 적 없는 worker heartbeat·모델 로딩·원격 추론은 unknown이다. 모델 파일 확보·로드·최근 실제 추론 성공을 같은 ready 하나로 합치지 않는다. 운영 경로·설정 비밀·DB URL을 공개하지 않는다. sample 사용 가능 여부와 일반 JSON/항목 한도도 limits에서 제공한다.

## 9. 오류·재시도·화면 상태

오류 본문은 `{error:{code,message,fields},meta}`다. code는 대문자 안정 코드, message는 표시용이며 분기 키로 쓰지 않는다. fields는 JSON Pointer→메시지 문자열 배열 객체다. 예: `/items/0/text`와 `/expected_revision`. 입력 값·비밀번호·원격 예외/경로를 그대로 재출력하지 않는다. 예상하지 못한 오류는 500 INTERNAL_ERROR와 request_id만 안전하게 표시한다.

| 상황 | 상태·코드 | UI 동작 |
|---|---|---|
| JSON 문법/중복 키 오류 | 400 INVALID_JSON | 수정 안내. 원 요청 자동 변환 금지 |
| 입력 필드/개수/타입/코드포인트 한도 | 422 VALIDATION_ERROR | fields에 연결. 초안 유지 |
| 본문/파일 크기 | 413 PAYLOAD_TOO_LARGE | 한도 안내. 더 작은 파일 선택 |
| 이미지 형식/JSON Content-Type | 415 UNSUPPORTED_MEDIA_TYPE | 지원 형식 안내 |
| 미인증·권한·접근 불가 | 401/403/404 | 7단계. 403을 모두 로그아웃으로 처리하지 않음 |
| 초안 revision/기준 변경/프로젝트 보관 | 409 각각의 안정 코드 | 최신 서버 상태를 읽고 사용자 선택. 자동 덮어쓰기 금지 |
| 같은 멱등키 다른 요청 | 409 IDEMPOTENCY_KEY_REUSED | pending 본문/키 대조. 몰래 새 키 재접수 금지 |
| 시도 제한/과부하 | 429/503 | Retry-After가 있으면 대기. 업무 실패로 확정하지 않음 |
| 클라이언트 연결 단절·취소·응답 JSON 오류 | 로컬 network_error/AbortError/invalid_response | 서버 커밋 여부 미확인. 요청 키 유지·조회/동일 요청 재전송 |

서비스 결과 항목의 안전한 error는 공통 code/message/fields에 stage·retryable·outcome_unknown을 추가할 수 있다. 이 필드를 일반 HTTP 오류의 fields 안에 숨기지 않는다. 런타임 오류의 retryable은 worker 정책 정보이며 프런트의 유료 추론 자동 반복 허용이 아니다.

실행 조회는 응답 완료 후 2초 간격으로 한 요청만 유지한다. 연결 오류는 2·4·8초, 이후 15초 상한으로 조회만 backoff하며 탭 비활성 시 늦추고 terminal/imported/로그아웃/페이지 이탈 시 중단한다. 재진입·탭 활성화 시 GET으로 현재 상태를 확인한다. AbortController·사용자/경로 generation으로 늦은 응답을 버린다. 통신 오류를 DB run.status=failed처럼 화면에 덮어쓰지 않는다. 페이지 이탈/조회 취소가 서버 실행 취소를 뜻하지 않는다.

## 10. 화면 URL·저장·새로고침 복원

| URL | 서버에서 복원할 상태 |
|---|---|
| /projects | 현재 사용자 프로젝트 목록·생성 |
| /projects/{project_id}/review/new | 프로젝트 확인 후 이미지·요구사항 입력. 미저장 File 객체는 복원 불가 |
| /projects/{project_id}/drafts/{draft_id} | 서버 초안·revision·등록 이미지. 수정/확정 상태 구분 |
| /projects/{project_id}/criteria/{criteria_version_id} | 불변 확정본·현재 초안 일치 여부·실행 가능 여부 |
| /projects/{project_id}/reviews | 이력·status/cursor query |
| /projects/{project_id}/reviews/{run_id} | run 상태를 조회해 진행/완료/실패/수입 이력 화면 선택 |
| /review/new | 기존 진입 링크 유지. 로그인 후 프로젝트 선택으로 연결 |

로그인 전 원래 주소는 허용한 내부 라우트만 저장한다. auth/me 성공 뒤 권한 있는 URL을 재조회하고 외부 주소·`//host`·임의 리다이렉트를 거부한다. URL의 project와 실제 draft/run의 project가 다르면 404다. history 뒤로 가기는 새 실행을 만들지 않는다.

초안 화면은 미저장 편집과 서버 저장 성공 revision을 구분하며 저장 완료 전에 확정/실행을 허용하지 않는다. 저장 버튼과 명시적 완료 표시를 최초 방식으로 정하고 자동 저장은 후속 UX다. unsaved 입력이 있으면 이탈 안내를 제공한다. 파일 업로드 성공 후에는 DB image_id의 표시 URL을 사용하므로 브라우저 File을 다시 요구하지 않는다. 업로드 전 새로고침은 파일 재선택을 안내하며 복원됐다고 표시하지 않는다.

pending confirm/create/retry의 키·본문·사용자 UUID·operation만 sessionStorage의 버전 있는 항목에 보관한다. 비밀번호·세션/CSRF 토큰·이미지 바이트·전체 결과는 저장하지 않는다. 사용자 전환/명시적 로그아웃에는 제거하고, 만료 후 같은 사용자 재로그인은 현재 권한을 재확인한 뒤 복구한다. sessionStorage 실패 시 메모리로 동작하되 새로고침 후 미확인 요청은 이력/버전 목록에서 확인하도록 알린다. sessionStorage는 비밀 저장소나 여러 브라우저 간 동기화 수단이 아니다.

이력 결과의 ‘기준 다시 확인’은 과거 확정본을 먼저 보여주고 ‘초안 편집’에서 실제 원 초안이 접근 가능할 때 연다. 초안 변경은 과거 결과를 삭제하지 않는다. 원 초안이 없는 수입은 9단계 재확정 경로를 사용하며 브라우저에서 가상의 서버 revision을 만들지 않는다. 사람 승인 버튼·학습 라벨 확정은 이번 UI/API 범위에 추가하지 않는다.

## 11. JSONB·상태 투영 계약

| 물리 JSONB | 초기 서비스 구조·권한 경계 |
|---|---|
| criteria_drafts.items_jsonb | 5절 5개 필드의 배열, 순서/미선택 내용 보존 |
| source_documents_jsonb | 5절 출처 배열/null. 내부 ID는 저장 계층이 프로젝트 검사 |
| criteria_items.source_item_jsonb | 확정한 선택 항목의 원래 5개 필드 객체 |
| expected_value/is_required 등 | 최초 UI 미제공은 SQL NULL. source_item에서 실제 누락과 명시 null 구분 |
| requested_config/retry_policy_jsonb | 서버 설정 allowlist의 고정 객체, 모델/전처리/정책 버전. 클라이언트 원격 주소 주입 금지 |
| error_jsonb | 9절 안전한 오류. stack trace·키·토큰·원문 개인정보 덤프 금지 |
| limitations_jsonb/next_action_jsonb | 객체/배열 또는 NULL. 현재 사실·미확인·후속 사람 확인을 분리 |
| OCR·추출·수입 원문 | 원래 구조·필드 상태·후보 보존. 응답 편의 때문에 DB 원문을 다시 쓰지 않음 |

관계 컬럼은 UUID, 상태는 관계 컬럼의 enum을 사용한다. JSON의 문자열 ID만으로 FK·권한이 확인됐다고 보지 않는다. 읽기 투영의 누락 기본값과 원문 보존을 구분한다. 예를 들어 API optional source_documents 생략/null은 의도적으로 같은 정규화 값을 쓰지만, 외부 원문에서 필드 없음과 null은 source payload/파일에 그대로 남긴다.

## 12. 내용·요청·manifest 해시

직렬화는 [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785.html)를 기준으로 한다. 객체 키는 UTF-16 코드 단위 순으로 정렬하고 배열 순서는 유지한다. 공백 없는 표현을 UTF-8로 인코딩하고 마지막 줄바꿈/BOM을 넣지 않는다. 문자열의 Unicode 정규화를 하지 않는다. 숫자는 ECMAScript 유한 binary64 표기를 따르므로 -0은 0이며, Python의 단순 sort_keys 직렬화가 일반 JCS와 같다고 가정하지 않는다. [10단계](추가설계_10_구현구조와검증명세.md)에서 rfc8785 0.1.4를 선택했으며 실제 라이브러리·정밀도 경계 실행 검증은 C09에서 수행한다.

각 지문은 `SHA256(UTF8(JCS({contract,kind,payload})))`의 소문자 64자리다. JSONB 텍스트 출력·예쁘게 출력한 JSON 파일의 바이트·HTTP 응답 전체를 해시하지 않는다. 선택 필드의 기본값·UUID 표기는 API 검증 뒤 적용한 **정규화 모델**이 기준이다. 일반 JCS에서 `{}`와 `{a:null}`은 다르며 API가 명시적으로 합친 기본값만 같은 요청이 된다.

| 저장 컬럼 | contract / kind | payload 포함·제외 |
|---|---|---|
| source_draft_hash | cc-content-jcs-v1 / criteria_draft | project_id·criteria_set_id·image_id·원문·출처·전체 items. draft UUID/revision/시각 제외 |
| criteria_hash | cc-content-jcs-v1 / criteria_snapshot | project/set/image·원문·출처·선택 항목 전체. 새 버전/항목 UUID·번호·시각·키 제외 |
| confirmation_request_hash | cc-request-jcs-v1 / confirm_criteria | 6절 actor/operation/project/body. source_draft_hash와 용도 다름 |
| review_runs.request_hash | cc-request-jcs-v1 / create_review 또는 retry_review | 6절 요청 payload. 바뀔 수 있는 서버 설정은 제외 |
| input_manifest_hash | cc-manifest-jcs-v1 / review_input | 프로젝트·이미지 UUID/실제 원본·표시 파일 해시·치수·좌표/전처리·기준 UUID/해시·고정 실행/집계/재시도 정책 |
| result_manifest_hash | cc-manifest-jcs-v1 / review_result | run·채택 attempt·상태/요약·OCR/추출 원문 파일 해시·선택 항목 결과/근거의 불변 값·연결 ID |

criteria_snapshot 항목은 선택 순서의 position=1..N, external_item_id/id, category/type, text, source_text/source, 미제공 rule/field/expected/is_required/source_asset/locator/applicability/check_scope의 null, source_item(원래 5필드)을 사용한다. 임시 selected 플래그를 별도 업무상 필수 여부로 해석하지 않는다. 전체 초안의 미선택 항목만 바꾸면 source_draft_hash는 바뀌지만 criteria_hash는 같을 수 있다. criteria_hash에는 UNIQUE를 두지 않는다.

input manifest는 접수 시 이미 존재하는 원본·표시 파일 해시와 OCR 2배 전처리 정책을 포함한다. 아직 생성하지 않은 ocr_input 파일의 해시·미실행 결과를 넣지 않는다. 이후 실제 생성한 입력/출력 파일 해시는 결과 manifest의 실제 출처로 남긴다. 현재 기기 상태·비밀·요청 시각·프런트 URL·진행률은 고정 입력 지문에 넣지 않는다.

result manifest는 결과 UUID만이 아니라 실제 verdict/처리 상태·관찰·사유·규칙/검사 버전·근거 텍스트/좌표/점수/출처·원문 파일 해시를 함께 묶는다. 배열은 항목 position, 근거 UUID, 토큰 order/UUID 등 명시한 안정 순서를 따른다. 휘발성 조회 URL·meta·updated_at·exported_at은 제외한다. 파일 자체의 SHA256은 그 파일 바이트를 그대로 해시하며 JCS 해시와 구분한다.

이 해시 규격은 우선 **새 서비스의 정규화 입력과 산출물**에 적용한다. 외부 원문이 이 JSON 수치/문자 범위를 만족하지 않으면 반올림한 source_item을 이 규격으로 봉인하지 않는다. 9단계 수입에서는 원문 파일 SHA256·실제 변환 버전·검토 필요 상태를 보존하고 외부 지문 규격을 별도로 검사한다. 외부 제공 해시를 cc-content-jcs-v1로 재명명하지 않는다.

[해시 고정 벡터](contracts/stage8-hash-vectors.json)는 입력 객체·정확한 canonical 문자열·UTF-8 바이트 수·SHA256과 동일/상이 관계를 포함한다. [요청·응답 예제](contracts/stage8-examples.json)는 가상 UUID·fixture 전용 모델/코드/해시를 사용한다. 실제 사용자·프로젝트·OCR 실행·seed가 아니다. 이번 검증은 예제의 직렬화/지문 일관성이며 서버 구현의 JCS 준수·PostgreSQL·브라우저 테스트 통과를 뜻하지 않는다.

## 13. 기존 /api 경로와 프런트 전환

| 구간 | 계약 |
|---|---|
| 전환 전 | 현 /api/reviews multipart·로컬 JSON·개발 로그인 그대로. 이번 문서는 동작을 바꾸지 않음 |
| v1 구현 검증 | 별도 /api/v1 업무 경로를 구현하고 React의 서버 초안·권한·상태·결과 어댑터를 함께 검증 |
| DB 저장 전환 | 첫 전환은 v1 쓰기를 유일한 신규 업무 경로로 사용. 기존 multipart를 자동 DB 어댑터로 바꾸지 않음 |
| 구형 쓰기 종료 | 배포 시 명시적으로 기존 POST /api/reviews를 410 LEGACY_WRITE_DISABLED로 전환. 프런트/직접 호출 사용자에게 새 경로 안내 |
| 구형 읽기 | 기존 JSON은 자동 귀속하지 않음. 개발 loopback의 명시적 legacy 읽기 모드에서만 보존하고 운영 공개 금지 |
| 운영 이력 | 9단계 dry-run·명시적 프로젝트/소유 귀속·검증 후 수입. 권한 없는 구형 조회를 운영 호환 경로로 남기지 않음 |

신규 데이터는 DB와 기존 .runtime JSON에 이중으로 업무 저장하지 않는다. 이전 UI와 새 UI가 같은 브라우저 정수 버전을 서로 다른 의미로 쓰지 않도록 API 접두부·contract_version으로 구분한다. 기존 기준 어댑터는 가능한 대안이었으며 첫 구현 경로로 채택하지 않는다. 원래 계획의 `criteria_version`은 구형 API에서 브라우저 값, v1에서 서버 값이다.

React는 입력·기준·결과 컴포넌트의 표시 책임을 재사용할 수 있지만 ReviewProvider의 메모리 확정·버전 증가·multipart 제출·재시도·원문 JSON export는 새 계약으로 교체해야 한다. 현재 client.js의 detail/error 읽기 호환은 전환 기간 유지하되 v1 성공 본문·meta·상태·UUID 누락은 invalid_response로 처리한다. health.runtime.ready 단일 판독도 checks로 교체한다. API 실패를 구형 JSON 신규 저장으로 fallback하지 않는다.

이 전환 시점·백업·되돌리기·구형 읽기의 경계는 [9단계](추가설계_09_저장과이전복구.md)에서 정했으며 접근 설정/실행 진입점은 [10단계](추가설계_10_구현구조와검증명세.md)에서 정했다. 지금 경로를 삭제하거나 공개 범위를 바꾸지 않았다.

## 14. 구현 뒤 검증할 사례

| ID | 사례 | 기대 결과 |
|---|---|---|
| API-01 | 기본 접두부·meta·코드·nullable 필드·UUID | cc-api-v1의 명시한 타입·불변/현재 값 구분 |
| API-02 | 빈/중복 키/추가 필드·잘못된 JSON 타입 | 400/422, 묵시적 변환 없음 |
| API-03 | 256 KiB·32 depth·문자/항목 수·revision 경계 | 명시한 단위로 제한·BIGINT 문자열 왕복 |
| API-04 | 파일 확장자/실제 형식·EXIF·이미지 한도 | 실제 바이트/표시 치수 검사, 고정 표시 프레임 |
| API-05 | 다른 프로젝트 image/set/draft/version 조합 | 404와 저장 없음 |
| API-06 | 두 탭 초안 수정·동일 내용 PUT·revision 소진 | 충돌/무변경/한도 정책대로 처리 |
| API-07 | 선택 0개·빈 선택 문구·미선택 보존 | 확정 거부 또는 선택 항목만 복사 |
| API-08 | 기준 확정 동시 요청·응답 유실·다른 본문 같은 키 | 같은 UUID 또는 409, 번호 중복 없음 |
| API-09 | 초안 수정 뒤 과거 확정본·기존 실행 조회 | 과거 내용 유지, 신규 실행만 CRITERIA_STALE |
| API-10 | 사용자/operation/프로젝트별 키 재사용 | 정의한 범위의 재전송/충돌 |
| API-11 | 접수 뒤 서버 설정/초안 변경 후 재전송 | 기존 UUID·현재 상태, 새 추론 없음 |
| API-12 | terminal 재검수·진행 중·수입·입력 변경 | 허용된 새 run만, 원 실행 덮어쓰기 없음 |
| API-13 | 접수 응답 202·재전송 200·Location | 접수와 완료 구분, 같은 리소스 복원 |
| API-14 | 실패/미지원/수입 항목 상태 | verdict NULL 보존·표시 review·출처/사유 표시 |
| API-15 | 채택 시도·항목 전체성·자동 요약 | 전체 집합만 complete, all_selected_v1 적용 |
| API-16 | OCR/근거 여러 페이지·채택 OCR 문맥 변경 | 중복/잘림 표시·타 시도 혼입 없음 |
| API-17 | confidence null·bbox 불명·이미 복원 좌표 | 0/가상 좌표/재축소 없음 |
| API-18 | 날짜 일부·복수 장소·원문 null·외부 스키마 없음 | 후보/미확인 유지·허위 absent/valid 없음 |
| API-19 | cursor 위조/만료/사용자·필터·limit 변경 | 400, 매 페이지 현재 권한 검사 |
| API-20 | 조회 중 신규 행·프로젝트 보관/멤버 변경 | 정의한 keyset 정책·권한·중복 제거 |
| API-21 | 큰 결과·JSON export·파일 누락 | 명시한 한도·누락 사유, 비밀/저장 키 공개 없음 |
| API-22 | health 점검 불가·worker/모델 미검증 | unknown, 준비·실추론 성공 구분 |
| API-23 | URL 재진입·초안/실행 새로고침 | 서버 UUID로 복원, 새 실행 자동 생성 없음 |
| API-24 | 업로드 전 새로고침·미저장 편집 이탈 | 재선택/미저장 안내, 복원 성공으로 위장 없음 |
| API-25 | 로그인 전 내부 복귀·외부 URL·프로젝트 불일치 | 허용 내부 경로만·404·서버 재조회 |
| API-26 | 연결 단절·Abort·늦은 응답·503·403 | DB 상태와 통신 상태 분리·generation 차단 |
| API-27 | pending 키 복원·사용자 전환·스토리지 불가 | 본문/주체 고정·토큰 미보관·수동 확인 경로 |
| API-28 | JCS 키 순서·배열·-0/지수·Unicode 경계 | 고정 canonical·바이트·SHA256 일치 |
| API-29 | 누락/null·미선택 문구 변경·내용/요청 해시 구분 | 정의한 동일/상이 관계 일치 |
| API-30 | 원본 바이트 vs 표시본 vs JCS 지문 | 목적·해시 버전·입력 구분 |
| API-31 | v1 전환·구형 쓰기·구형 읽기·수입 결과 | 단일 쓰기·410·운영 미공개·출처 분리 |
| API-32 | 로그인/프로젝트/이미지/JSON/샘플 전체 권한·CSRF | 7단계 정책이 새 경로에도 누락 없이 적용 |

위 32개는 **향후 구현 검증 명세**다. 이번에 수행한 것은 문서 참조·물리 컬럼 대응·예제 형식·해시 재계산·완료 단계 연결의 정적 검사이며 [8단계 검토 기록](verification/2026-10-17-api-design-stage8.json)에 남긴다. 실제 FastAPI·React·DB·브라우저·동시성 검증은 아직 수행하지 않았다.

## 15. 다음 단계

API·화면·JSON·해시 계약을 정리하고 [9단계 저장·이전·복구](추가설계_09_저장과이전복구.md)까지 완료했다. [추가 설계 10단계 — 구현 구조·검증 명세](추가설계_10_구현구조와검증명세.md)까지 완료했다. [C01 — DB 설정·연결·트랜잭션 계층](구현_C01_DB연결과트랜잭션.md)의 구현·격리 PostgreSQL 검증을 완료했다. [C02 — Alembic과 사용자 스키마](구현_C02_Alembic과사용자스키마.md)의 M001·격리 DB 검증도 완료했다. [C03 — 파일 스키마/M002](구현_C03_파일스키마와M002.md)도 구현·격리 DB 검증을 완료했다. [C04 — 규격·인계·OCR 스키마/M003](구현_C04_OCR스키마와M003.md)도 구현·격리 DB 검증을 완료했다. [C05 — 검수 기준 스키마/M004](구현_C05_검수기준스키마와M004.md)도 구현·격리 DB 검증을 완료했다. [C06 — 실행·결과와 전체 FK](구현_C06_실행결과와전체FK.md)도 구현·격리 DB 검증을 완료했다. [C07a — 인덱스와 조회 계획](구현_C07a_인덱스와조회계획.md)의 초안 구현·격리 DB 검증도 완료했다. [C07b — 봉인·전이와 M007](구현_C07b_봉인전이와M007.md)의 가드와 정식 M007도 구현·검증했다. [C07c — 권한과 최종 스키마](구현_C07c_권한과최종스키마.md)도 완료했다. 정식 head는 M008이다. [C08 — 인증과 계정 관리](구현_C08_인증과계정관리.md)도 완료했다. [C09 — 파일 저장과 기준 확정](구현_C09_파일저장과기준확정.md)도 구현·검증했다. [C10 — 검수 접수와 원자적 결과](구현_C10_검수접수와원자적결과.md)도 구현·검증했다. [C11 — React와 서버 상태 연결](구현_C11_React와서버상태연결.md)도 구현·계약/합성 API 브라우저 검증했다. [C12 — 실제 E2E·프록시 검증](구현_C12_실제E2E와프록시검증.md)도 실제 직접/Vite·Linux HTTPS·로컬 Paddle CPU 경로로 검증했다. [C13 — 독립 worker와 회수](구현_C13_독립Worker와회수.md)도 구현·격리 DB/프로세스·실제 로컬 Paddle CLI로 검증했다. [C14 — 수입/GC·실제 백업 복원](구현_C14_수입GC와백업복원.md)도 구현·격리 훈련했다. [C15 — Linux 배포 리허설과 전환](구현_C15_Linux배포리허설과전환.md)도 사용자 지정 로컬 범위에서 완료했다. C01~C15의 구현/격리 검증을 마쳤으며 실제 운영 서버·모델·백업 보관·공개 전환은 별도 후속이다.
