# C10 — API/STORE 검증 범위

작성일: 2026-10-18. [구현 기록](구현_C10_검수접수와원자적결과.md)·[최종 증거](verification/2026-10-18-implementation-C10.json).

C10 완료는 검수 백엔드 범위다. 실제 PostgreSQL·파일·ASGI와 Uvicorn/HTTP/강제 프로세스 종료를 검증했으며 OCR은 명시적 합성 어댑터였다. 실제 모델 추론·브라우저·remote/worker·수입·GC·백업 복원을 완료로 표시하지 않는다. C09 검증도 전체 회귀에 포함했다.

| API ID | C10의 증거 또는 남은 범위 |
|---|---|
| API-01~08 | 기존 C09 계약/초안/확정 회귀 유지, 새 검수 UUID/meta·CSRF·원자성·strict 생성/retry 추가 |
| API-09 | 변경된 초안은 새 요청 CRITERIA_STALE. 과거 결과/고정 project_name·입력은 유지 |
| API-10 | actor/operation/key 범위, 다른 본문/다른 프로젝트 충돌, 다른 actor·create/retry 독립 |
| API-11 | 초안/설정/보관 변경 뒤 기존 성공의 같은 UUID/현재 상태 재전송, 현재 권한 재검사 |
| API-12 | terminal 서비스 실행만 새 retry, queued/running·외부 이력 거부, 명시적 새 실행 |
| API-13 | 실제 HTTP 202 queued 응답 뒤 실행, 재전송 200·Location·Idempotency-Replayed |
| API-14 | unsupported/failed 항목 verdict NULL·status review. 불완전 외부 이력은 results_complete=false. 전체 importer C14 |
| API-15 | 실제 전체 결과/채택/봉인, 중간 오류 전체 롤백. fail 우선 all_selected_v1·미지원 review |
| API-16 | 현재 권한·run/result/OCR 문맥의 키셋·근거 개수·타 run 근거 404. 대규모 외부 이력/브라우저 로딩은 후속 |
| API-17 | 실제 token ID/타입·confidence null·좌표 1회 복원·표시 프레임. 실제 OCR 품질은 C12 |
| API-18 | null/빈 OCR로 absent/resolved 생성 안 함·외부 스키마 미확보 표시. 전체 외부 규격 검증 C14 |
| API-19~20 | C09 cursor/현재 권한 회귀와 검수 이력 키셋·필터·잘못된 query 거부 추가. 페이지 중 대량 삽입 전체 매트릭스 후속 |
| API-21 | 일반 JSON/export 한도·다운로드 전 거부·nosniff·원문 미포함 사유·비밀/저장키 제외. 실제 최대 50 MiB 성능 측정 후속 |
| API-22 | 모델 파일 준비와 실제 성공한 설정의 추론 상태 구분, 미측정 worker unknown. 실제 모델/worker C12/C13 |
| API-23~24 | 서버 UUID 조회는 구현. React URL/미저장 편집은 C11/C12 미실행 |
| API-25 | 비멤버/세션 만료/비활성화·프로젝트 조합·타 run 근거 거부. 로그인 복귀 UI C11 |
| API-26~27 | 서버의 커밋 미확인·크기 오류·같은 키 복원은 확인. 브라우저 Abort/generation/sessionStorage는 C11/C12 |
| API-28~30 | C09 실제 JCS 30개 회귀. 실제 저장 graph·plan·원문 파일·result manifest 일치/내용 변경 시 지문 변경 추가 |
| API-31 | 구형 시제품 보존. 실제 전환·410·수입은 C14/C15 |
| API-32 | 새 검수 접수/retry의 세션·현재 권한·CSRF, 조회/export 권한. 새 화면 전체 동선 C11/C12 |

| STORE ID | C10의 증거 또는 남은 범위 |
|---|---|
| STORE-01~03 | C09 no-clobber/변조/한도 회귀. 결과 파일 일부 게시 실패와 기존 입력/게시 바이트 보존 추가 |
| STORE-04~05 | 실제 작업/공유 gate·DB advisory try-lock 회귀. 실제 GC apply 경합은 C14 |
| STORE-06 | 접수/결과의 실제 커밋 후 응답 유실 주입→동일 성공 복원. unresolved 결과는 보존/대조 전 추정 마감 안 함 |
| STORE-07 | 실제 HTTP 응답 뒤 동일 키/현재 상태, 완료 보존·서버 재시작 후 같은 UUID. 재추론 없음 |
| STORE-08 | OCR/추출/근거/결과/채택 그래프 중간 SQL 오류 전체 롤백·게시 파일 보존 |
| STORE-09 | C09 업로드 최종 권한 재검사 유지. 이미 접수한 실행은 뒤의 프로젝트 보관으로 무효화하지 않음 |
| STORE-10 | M007/M008 봉인/전이 회귀 + C10 전체 원자적 봉인·늦은 재저장 거부 |
| STORE-11 | BackgroundTasks 토큰/active 포인터 검사·오래된 토큰 거부. 독립 worker lease C13 |
| STORE-12 | 같은 결과 manifest/실제 graph는 성공 확인, 다른 manifest는 conflict·덮어쓰기 없음 |
| STORE-13 | remote 어댑터/재제출 없음. 유료 job의 실제 복구는 C13 |
| STORE-14~16 | 불명/미참조 바이트 보존·C09 파일 가용성 회귀. GC·정확한 바이트 복구 관리 C14 |
| STORE-17 | DB당 실행 서비스 잠금·다른 저장소의 두 번째 서비스 거부·연결 유실 시 권한 상실·중단 실행 마감 |
| STORE-18 | background 복구가 postgres_worker 행을 인수/마감하지 않음. 실제 DB worker C13 |
| STORE-19~28 | 기존 FK/봉인과 합성 지문 회귀. importer dry-run/apply·원문 투영/외부 판정 검증 C14 |
| STORE-29~32 | 백업 drain·복원 세트·세션 폐기/키 재공급 C14 미실행 |
| STORE-33 | 검수 결과 저널→primary/manifest/파일 대조와 실제 SIGKILL 재시작. backup/run/package 전체 복원 C14 |
| STORE-34~36 | 기존 코드/데이터 보존 및 macOS 프로세스 기능 시험만. 운영 rollback·RPO/RTO·전원 장애·Linux/Windows 인증 미실행 |

최종 회귀의 사례 수는 API/STORE 설계 ID 수가 아니다. 여러 파라미터/경합/기존 단계 사례를 포함한 실행 개수이며, 부분·미실행 항목을 전체 통과로 합산하지 않는다.
