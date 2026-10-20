# C14 검증 대응표

작성일: 2026-10-19. 실제 PostgreSQL 17.11·제한된 app/owner·실제 파일·PostgreSQL 17 도구 컨테이너·새 template0 DB/루트를 사용했다. OCR은 합성 adapter이며 실제 원격 요청은 0회다. 최종 실행 수치는 [증거 JSON](verification/2026-10-19-implementation-C14.json)에 기록했다.

기본 Python 670개·추가 서브테스트 7개, C14 집중 PostgreSQL 37개, 전체 PostgreSQL core 1,322개, 실제 직접/Vite 브라우저 2개가 통과했다. C14 신규 사례는 기본 25개와 DB 37개로 총 62개이며 집중 검사/core 중복은 합산하지 않는다. 이번 DB 연계 고유 사례는 1,324개다. 일상 DB/OID·기본 ACL은 불변이며 임시 DB는 모두 정리했다. 보호된 기존 migration·계약·증거·프런트·legacy/launcher 104개는 시작 SHA와 일치한다.

[실제 복원 훈련](verification/C14/restore-drill.json)은 사용자 테이블 28개·등록 파일 11개, 서비스/수입 manifest 각 1개를 포함한다. 기존 세션 1개 폐기·두 키 변경·원 쿠키 거부·새 로그인/쓰기/합성 검수·재시작 조회가 통과했다. 최종 core 측정은 백업 0.9794초·복원 검증 1.0062초이며 운영 SLA로 해석하지 않는다.

| 설계 ID | 검증 | 소스/범위 |
|---|---|---|
| STORE-01~06·14~15 | 작업/DB/gate 잠금·24시간 유예·미참조 확인·모든 state의 DB 행 보호·원문/소유 불명 보존 | test_gc_dry_run_quarantine_and_seven_day_delete_are_separate, test_gc_preserves_busy_registered_and_unowned_files 5개 |
| STORE-04~05·14 | 격리 link/unlink와 삭제 중단 뒤 기록된 파일만 재개 | test_gc_interruption_resumes_only_its_recorded_owned_files 2개 |
| STORE-16 | FILE_UNAVAILABLE 503·missing 감사·일치 바이트만 복구·원 해시 유지 | test_missing_file_503_and_exact_byte_repair_preserve_image_and_hash |
| STORE-19·21~22 | dry-run 업무 쓰기 없음·원본 바이트 불변·새 UUID에도 같은 지문·다른 결정은 새 지문 | test_dry_run_has_no_business_writes_and_same_fingerprint_with_new_ids, 단위 지문/고정 stage9 벡터 |
| STORE-20·25 | 경로/링크/이름/한도·JSON 정밀도/중복/NUL·다중 포스터·제공자 hash mismatch | test_c14_operations.py, test_ambiguous_source_is_intake_only_needs_review 6개 |
| STORE-23 | 입력/intake/계획 변조·동시 apply·완료 중복·재검증 | test_apply_rejects_source_changes_and_staging_changes, test_frozen_plan_resource_ids_cannot_be_edited_behind_the_fingerprint, test_same_package_concurrent_apply_and_confirmed_rollback_revalidation |
| STORE-24·27 | 과거 completed/pass/local-admin 보존·로컬 verdict/requester/시각 NULL·불완전 집합 표시 | test_import_preserves_source_without_creating_local_verdict_or_requester 2개, test_partial_source_results_remain_partial_without_invented_passes |
| STORE-26·28 | 그래프 전체 롤백·failed 재검증 감사·COMMIT 응답 유실 조정·완료 불변 | test_graph_transaction_is_atomic_and_unknown_commit_is_reconciled 2개. 기존 M007/M008 제약도 전체 회귀 |
| STORE-19~28 | 실제 owner 관리 CLI dry-run/apply·비밀 출력 없음 | test_real_management_cli_dry_run_and_apply |
| STORE-29 | 실행 중 서비스·active 시도·drain 상한에서 complete 생성 없음 | test_active_service_or_unfinished_inference_does_not_create_a_complete_backup |
| STORE-29 | 웹 밖의 DB 쓰기도 캡처 중 차단·health 점검 응답 | test_owner_table_locks_block_writes_outside_the_web_process, test_maintenance_http_reports_not_ready_and_refuses_login |
| STORE-30 | dump/파일/manifest/완료 레코드 누락/변조·degraded 거부 | test_corrupt_or_partial_backup_is_never_a_restore_candidate 4개, test_missing_registered_payload_is_degraded_and_not_automatically_restorable |
| STORE-31~33·36 | 실제 전체 dump/새 DB·파일 복원·행 SHA·M008 권한/구조·전체 그래프/해시·세션/새 키·이미지/JSON·새 검수·재시작 | test_actual_dump_restore_new_target_full_graph_sessions_keys_and_restart |
| STORE-31 | 알려진 CHECK 표기 차이 외 predicate/function/권한 변화는 거부 | test_restore_rendering_repair_rejects_every_unrelated_schema_change 3개. 실제 복원 뒤 기존 byte-exact 검사 통과 |
| STORE-32 | 키 재사용·기존 대상·중단 복원·검증 전 runtime 시작 거부 | test_restore_rejects_old_security_keys_and_populated_target, test_interrupted_restore_keeps_fresh_target_held, 단위 restore hold |
| STORE-29~33 | 실제 backup CLI·DB 연결 불가 상태의 오프라인 세트 검사·비밀 출력 없음 | test_real_backup_cli_and_offline_inspection_do_not_expose_credentials |
| STORE-34~35 | 원본 DB/파일 보존·봉인/수입 완료 임의 DELETE 없음 | 원본 snapshot 전후 대조·기존 migration/증거/프런트/legacy SHA 확인. 운영 cutover/undo는 이번 범위 아님 |

소스: [기본 검사](../../backend/tests/test_c14_operations.py), [수입/GC/관리 CLI](../../backend/tests/test_c14_import_gc_integration.py), [실제 백업/복원](../../backend/tests/test_c14_backup_integration.py). 기존 인증·파일·worker·원자성·가드 검증은 core suite로 함께 실행한다.

macOS의 기본 파일시스템은 NFC/대소문자 이름을 OS에서 합칠 수 있어 두 이름이 공존하지 않는 경우 단위 테스트의 디렉터리 목록을 합성 주입한다. 실제 링크/경로/원문·DB·파일 복원 검증과 이 합성 이름 충돌 벡터를 구분한다. GC의 24시간/7일은 테스트 소유 파일 시각/내부 테스트 시각으로 가속하며 CLI가 유예를 줄이는 옵션을 제공하지 않는다.

실제 측정은 작은 합성 자료의 로컬 훈련이다. 대용량 운영 성능·정확도·RPO/RTO SLA·원격 보관 암호화·다른 호스트/OS의 복원 완료로 확대하지 않는다.
