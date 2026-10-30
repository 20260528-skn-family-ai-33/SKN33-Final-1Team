# 재구축 계약

Issue #29의 합의를 실행 가능한 기능으로 연결한다. 단일 제품 진입점은 `backend.creative_checker.api:create_app`와 `frontend/src/main.tsx`다. 구형 파일은 이 진입점에서 import하지 않으며 이관·제거 여부는 별도로 기록한다. 기존 작업 폴더의 미커밋 변경은 이 worktree에 복사하지 않는다.

## 책임
- API: HTTP 세션·권한, 업로드, 조건·수정·승인 명령, 조회. 모델은 API 프로세스에서 적재하지 않는다.
- worker: PostgreSQL 작업 claim/lease, LangGraph checkpoint, 실제 MCP 호출, A06 검증, A07 초안 연결. MCP 프로세스가 Qwen을 호출하고 별도 projection 프로세스가 outbox를 Neo4j에 반영한다.
- PostgreSQL: 사용자/프로젝트/버전/조건/실행/근거/승인 원본. Neo4j는 관계 탐색용 파생 데이터다.
- 파일 저장: 로컬 개발 디렉터리 또는 배포 S3. 파일 내용은 덮어쓰지 않는다. 권한 확인 후 조회한다.

## 공통 HTTP 계약
API prefix `/api`. 모든 시간은 ISO 8601 UTC, ID는 UUID 문자열. 오류 `{error:{code,message},request_id}`. 목록은 `{items:[...]}`. 인증은 HttpOnly 세션 쿠키와 변경 요청의 `X-CSRF-Token`이다. 로그인/세션 조회 응답에 `csrf_token`을 포함한다. 인증 불가 401, 프로젝트 외부 객체 404, 역할 부족 403, 현재 상태와 충돌 409, 입력 오류 422. 실제 실패를 성공 응답으로 치환하지 않는다.

### 계정·프로젝트
- POST `/api/auth/login` `{email,password}` → `{user:{id,email,name,is_admin},csrf_token}`
- GET `/api/auth/me` → 동일 응답. POST `/api/auth/logout`.
- POST `/api/auth/password` `{current_password,new_password}`.
- GET/POST `/api/admin/users`(운영자): 생성 `{email,name,password,is_admin:false}`.
- GET/POST `/api/projects`: 생성 `{name,description}` → project. 작성자는 approver.
- GET `/api/projects/{id}` → `{id,name,description,role,archived,current_document_id,current_criteria_id,graph_status,...}`.
- GET/PUT `/api/projects/{id}/members`: 목록, 변경 `{user_id,role:approver|editor|viewer}`. 승인자만 관리.
- POST `/api/projects/{id}/archive` `{archived}`.

### 입력·조건
- GET/POST `/api/projects/{id}/documents`: multipart `file`, 등록 후 문서 추출 작업 생성. 목록 document `{id,project_id,version,filename,file_id,status,blocks,criteria,errors,created_at}`.
- GET `/api/projects/{id}/criteria` → `{items:[{id,version,status,document_id,items,groups,approved_by,approved_at}]}`.
- POST `/api/projects/{id}/criteria` `{document_id,items,groups}` → 변경 불가 새 기준 버전. POST `/api/criteria/{id}/approve`.
- criterion `{id,text,category:creative|business|other,required,kind:exact_text|date|amount|quantity|logo|layout|color|unsupported,expected,target:all|poster|video,asset_ids:[],source:{kind:document|user,block_id,page,quote},supported,notes}`. expected는 JSON 값. group `{id,operator:all|any,criterion_ids,condition,exception}`. 미확정 condition/exception은 확인 대상으로 보존하며 임의 충족 처리하지 않는다.
- GET/POST `/api/projects/{id}/logos`: multipart `file,name`; POST `/api/logos/{id}/approve`.
- GET/POST `/api/projects/{id}/assets`: multipart `file,name,kind:poster|video`; asset `{id,project_id,name,kind,versions:[{id,version,file_id,filename,metadata,created_at}],needs_review}`.
- POST `/api/assets/{id}/versions`: multipart `file,change_request_id?`.
- GET `/api/files/{id}`: 원본/파생 자료의 권한 확인 후 내용 반환(Range 영상 지원).

### 검사·업무
- GET/POST `/api/projects/{id}/runs`: 생성 `{asset_version_id,criteria_version_id?,previous_run_id?}`, `Idempotency-Key` 필수. current approved criteria와 approved logo 버전, 모델 식별자·선언 revision·처리 코드/프롬프트 해시를 snapshot에 고정한다. 코드·설정 불일치 시 `inference_profile_changed`로 실패하며 새 실행이 필요하다.
- GET `/api/runs/{id}` → `{id,project_id,asset_id,asset_version_id,criteria_version_id,state:queued|running|waiting_review|completed|failed|cancel_requested|cancelled,stage,verdict:pass|fail|review|null,progress:{completed,total},results:[],report,errors:[],events:[],snapshot,created_at}`.
- result `{criterion_id,execution_status:completed|failed|unsupported,verdict:pass|fail|review,observed,expected,reason,evidence:[{file_id,frame_ms,bbox:[x,y,w,h],text}],limitations:[]}`. bbox는 0~1 좌표. 관측 원문과 비교값은 구분한다.
- POST `/api/runs/{id}/cancel`, POST `/api/runs/{id}/retry` (`Idempotency-Key`). retry는 동일 run의 attempt로 분리한다.
- GET/POST `/api/runs/{id}/change-requests`: 생성 `{items:[{criterion_id,message}],message}` → draft. POST `/api/change-requests/{id}/approve`.
- POST `/api/runs/{id}/approve` `{exception_reason?,exception_criterion_ids:[]}` → 최종 approval. 실행 실패/미검사를 정상 완료로 변경하지 않는다.
- GET `/api/projects/{id}/history` → 이벤트/승인 이력.
- GET `/api/projects/{id}/graph` → `{status,synced_at,nodes:[{id,label,kind}],edges:[{source,target,kind}]}`. GET `/api/projects/{id}/impact?criterion_id=...` → 실제 Neo4j 조회 결과.
- GET `/api/health/live`, GET `/api/health/ready`(비밀·내부 URL 미노출).

## 구현 경계
빈 구현·성공을 가장한 mock fallback 금지. 본 API와 UI를 함께 연결한다. 파일·요청 payload·로그에 인증값을 남기지 않는다. 새 run은 전체 적용 기준을 대상으로 하며 영상 샘플링 범위와 미관측 구간을 결과에 남긴다. 로고 기준이 없거나 좌표/근거가 검증되지 않으면 확정적인 통과·위반으로 만들지 않는다.
