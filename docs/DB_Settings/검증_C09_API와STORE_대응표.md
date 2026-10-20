# C09 — API/STORE 설계 ID별 검증 범위

작성일: 2026-10-18. [구현 기록](구현_C09_파일저장과기준확정.md)·[실행 증거](verification/2026-10-18-implementation-C09.json)를 함께 본다.

이 표의 완료는 **C09 백엔드 범위**다. 설계의 32개 API·36개 STORE 전체, 화면·검수·수입·운영 복원을 통과했다는 뜻이 아니다. 이전 단계의 봉인/권한 테스트도 전체 회귀에 포함한다. COMMIT 응답 유실은 실제 DB 커밋 전/후의 오류 주입으로 시험했고 네트워크 장비에서 응답 패킷을 제거한 시험은 아니다.

| API ID | 상태 | 실제 증거와 남은 범위 |
|---|---|---|
| API-01 | C09 완료 | 새 API/meta/UUID/nullable DTO, 실제 HTTP/재시작. 검수 DTO C10 |
| API-02 | C09 완료 | strict 입력, 중복 JSON 키/추가 필드/잘못된 타입 거부 |
| API-03 | C09 완료 | JSON byte/depth, 문자열 revision 최대값·변경 거부/같은 내용 허용 |
| API-04 | C09 완료 | 실제 PNG/JPEG/WEBP, 투명·EXIF·애니메이션·픽셀/용량 한도 |
| API-05 | C09 완료 | image/set/source/draft/version/filter의 다른 프로젝트 조합 404 |
| API-06 | C09 완료 | 동일 PUT 무변경·이전 revision 충돌·BIGINT 최대값 |
| API-07 | C09 완료 | 선택 0/빈 문구 거부, 미선택 보존·선택만 불변 복사 |
| API-08 | C09 완료 | 같은 키 동시 확정의 단일 UUID, 다른 키 번호 직렬화·응답 후 재전송 |
| API-09 | 부분 | 수정 후 과거 기준/해시 보존·draft_is_current. run stale 검사는 C10 |
| API-10 | 부분 | actor/key의 전역 범위·다른 프로젝트 충돌·다른 actor 독립. run operation C10 |
| API-11 | 미실행 | 검수 접수/재전송 C10 |
| API-12 | 미실행 | terminal 재검수 C10 |
| API-13 | 미실행 | 검수 202/200·Location C10 |
| API-14 | 미실행 | 결과별 verdict/출처 C10/C14 |
| API-15 | 미실행 | 채택 시도·전체 항목/요약 C10 |
| API-16 | 미실행 | OCR/근거 페이지 C10 |
| API-17 | 부분 | 업로드 표시 프레임/좌표만 확인. OCR confidence/bbox C10 |
| API-18 | 미실행 | 추출/스키마 검증 C10 |
| API-19 | C09 완료 | 위조·만료·actor/종류/limit 문맥 불일치, 현재 권한 검사 |
| API-20 | 부분 | 키셋 페이지 중복 없음·보관 필터. 페이지 사이 대량 삽입/멤버 변경의 전체 매트릭스 미실행 |
| API-21 | 부분 | 파일 누락/변조 503·저장키 비공개. 큰 검수 결과/export C10 |
| API-22 | 부분 | db/storage 준비와 worker/model unknown 분리. 실제 worker/model C13 |
| API-23 | 미실행 | URL 화면 재진입 C11/C12 |
| API-24 | 미실행 | 미저장 편집 UI C11/C12 |
| API-25 | 부분 | 서버의 프로젝트 불일치/비멤버 404. 로그인 복귀 URL UI C11/C12 |
| API-26 | 부분 | 서버 오류/실제 스트리밍 한도·temp 정리. 화면 Abort/generation C11/C12 |
| API-27 | 미실행 | 브라우저 pending 키 복원 C11/C12 |
| API-28 | C09 완료 | 실제 rfc8785: stage8 20개+stage9 10개 canonical/UTF-8 길이/SHA256 일치 |
| API-29 | 부분 | 전체 초안/선택 스냅샷/요청 해시, SQL NULL·source_item 보존. 전체 화면/수입 매트릭스 후속 |
| API-30 | C09 완료 | 원본 그대로·표시 PNG 각각의 바이트 해시와 JCS 내용 해시 분리 |
| API-31 | 미실행 | 운영 전환·410·legacy import C14/C15 |
| API-32 | 부분 | C09 경로의 세션/CSRF/실제 권한·viewer/editor·업로드 중 권한 변경. JSON export/샘플 새 UI C10~C12 |

HASH-11은 API 정수 한도 밖의 **직렬화 전용 binary64 경계 벡터**다. 이 벡터의 raw_input_json만 binary64로 읽어 공식 직렬화기와 비교한다. API가 큰 정수를 허용하도록 바꾸지 않았다. 수입 벡터 10개의 JCS 일치는 수입 apply 구현/실행 증거가 아니다.

| STORE ID | 상태 | 실제 증거와 남은 범위 |
|---|---|---|
| STORE-01 | 부분 | 용량/디코딩 실패·기존 파일 보존·parser temp 닫기. 수신 도중 강제 종료 전체 매트릭스 후속 |
| STORE-02 | 부분 | 첫 payload/DB BEGIN 전 독립 프로세스 강제 종료·재시작 not_committed·바이트 보존. 유예 후 격리/GC C14 |
| STORE-03 | C09 완료 | 다른 staging inode/바이트의 최종 경로 충돌 거부·기존 바이트 불변 |
| STORE-04 | 부분 | 실제 공유/배타 gate·서비스/작업 잠금. GC apply 경합 C14 |
| STORE-05 | 부분 | 실제 PostgreSQL advisory try-lock busy→unresolved. COMMIT 도중 프로세스/GC 전체 경합 C14 |
| STORE-06 | C09 완료 | 실제 커밋 뒤 응답 유실 주입→성공 복원, 미커밋→보존, unresolved→보존·503 |
| STORE-07 | 부분 | 기준 확정의 실제 HTTP 재시작 후 같은 키/UUID 복원. 검수 실행 키 C10 |
| STORE-08 | 부분 | 이미지 그래프 중간 DB 실패 전체 롤백·게시 파일 보존. OCR/결과 C10 |
| STORE-09 | C09 완료 | 파일 준비 뒤 프로젝트 보관·멤버 철회·계정 비활성화, 최종 저장 거부 |
| STORE-10 | 기존 회귀 | M007/M008의 미봉인 커밋·봉인 후 자식 변경 금지 검증 유지 |
| STORE-11 | 미실행 | worker token/lease C13 |
| STORE-12 | 미실행 | 결과 manifest 재저장 C10/C13 |
| STORE-13 | 미실행 | 유료 원격 제출 미확인 C13 |
| STORE-14 | 부분 | 미참조/불명 파일 보존·DB 불명 시작 차단. 유예/격리 C14 |
| STORE-15 | 미실행 | GC 자체 없음. DB 파일 행의 GC 제외 규칙 C14 |
| STORE-16 | 부분 | 누락·변조·링크 오류의 503, GET 후 DB 메타데이터 불변. 정확한 바이트 복구/관리 상태 전이 C14 |
| STORE-17 | 부분 | 두 서비스의 동일 저장소 동시 시작 거부. BackgroundTasks 실행 중단 처리는 C10 |
| STORE-18 | 미실행 | background/worker 프로필 C10/C13 |
| STORE-19 | 미실행 | legacy dry-run C14 |
| STORE-20 | 부분 | 로컬 업로드의 루트/경로/심볼릭·하드 링크/FIFO·한도. 수입 경로/NFC/다중 포스터 C14 |
| STORE-21 | 부분 | 수입 JCS 고정 벡터 10개만. 실제 멱등 수입 C14 |
| STORE-22 | 부분 | 수입 지문 벡터만. 실제 변경/덮어쓰기 거부 C14 |
| STORE-23 | 미실행 | 동시 import apply C14 |
| STORE-24 | 미실행 | import ID/FK/결과 대응 C14 |
| STORE-25 | 부분 | HTTP 정밀도/NUL 거부만. 수입 원문/스키마/hash 대응 C14 |
| STORE-26 | 기존 회귀/부분 | M007 import/봉인 관계 제약 유지. 실제 importer C14 |
| STORE-27 | 미실행 | 외부 requester/판정 수입 C14 |
| STORE-28 | 미실행 | import 재검증/완료 불변 C14 |
| STORE-29 | 미실행 | 백업 drain C14 |
| STORE-30 | 미실행 | 복원 세트/manifest C14 |
| STORE-31 | 미실행 | 새 DB/루트 복원 C14 |
| STORE-32 | 미실행 | 복원 세션 폐기/키 재공급 C14 |
| STORE-33 | 부분 | 업로드 저널/실제 DB·파일 대조만. 백업 복원 run/package C14 |
| STORE-34 | 미실행 | RPO/구형 코드 롤백 C14/C15 |
| STORE-35 | 미실행 | completed 수입 취소 C14 |
| STORE-36 | 부분 | macOS 실제 fsync/잠금·프로세스 종료 확인. Linux/Windows 운영·전원 장애·RPO/RTO 미인증 |
