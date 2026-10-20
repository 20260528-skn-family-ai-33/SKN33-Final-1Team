# C13 검증 대응표

작성일: 2026-10-19. 격리 PostgreSQL 17.11·실제 파일 잠금·독립 프로세스 시험이며 테스트 전용 합성 OCR과 실제 Paddle을 구분한다.

| ID | 검증 사항 | 테스트/증거 |
|---|---|---|
| W-01 | 앱 역할만 받는 명시적 worker·웹 프로필, 개발 로그인 유지 | test_c13_worker.py의 profile/description 검사 |
| W-02 | 웹 queued 응답·같은 키 재전송, 웹에서 추론/BackgroundTasks 없음 | test_web_only_admits_and_replays_worker_jobs |
| W-03 | 두 worker의 단일 인수와 완전한 결과/다운로드 | test_two_workers_claim_once_then_commit_complete_graph |
| W-04 | project/run SKIP LOCKED와 available_at, 실행 설정/프로필 분리 | test_skip_locked_projects_and_available_time, test_mismatched_config_and_background_queue_are_never_claimed |
| W-05 | 추론 중 독립 heartbeat, DB 행 잠금 미보유 | test_heartbeat_runs_while_inference_has_no_db_transaction |
| W-06 | 잠금 대기 뒤 실제 DB 시각으로 만료 판단, 만료 lease 연장 거부 | test_lease_is_checked_after_waited_lock_using_actual_db_time |
| W-07 | 실행 전 회수·5/30초 대기·3회 상한·새 토큰·최초 시작 유지 | test_expired_predispatch_requeues_with_fresh_token_and_three_attempt_cap |
| W-08 | 호출/제출/unknown 이후 자동 재추론/유료 제출 금지 | test_expired_after_dispatch_never_resubmits 3개. submitted는 합성 job ID이며 실제 원격 호출 아님 |
| W-09 | 잘못된/옛 토큰과 만료 계획의 결과 저장 차단·채택 없음 | test_current_token_wrong_token_and_late_result_are_fenced, test_expired_plan_cannot_publish_any_database_result |
| W-10 | 인수·dispatch 커밋 미확인에서 OCR 미호출 | test_unconfirmed_claim_never_executes_or_claims_again, test_unconfirmed_dispatch_never_calls_ocr_or_automatically_resubmits |
| W-11 | 결과 커밋 응답 유실 후 동일 manifest로 성공 확인·추론 1회 | test_result_commit_ack_loss_reconciles_without_second_ocr |
| W-12 | heartbeat 한 번 실패 후 권한 재획득 금지 | test_failed_heartbeat_never_reacquires_authority |
| W-13 | 웹/worker 저장소 공유·background 배타 시작 거부·복구 busy 건너뛰기 | test_live_storage_owners_coexist_and_background_exclusive_is_rejected |
| W-14 | 실제 HTTP 접수 뒤 웹 종료, worker 2개 경쟁, 웹 재시작 조회 | test_web_stopped_worker_completes_and_web_restart_reads_same_run |
| W-15 | 소유한 worker SIGKILL: 호출 전만 새 시도, 호출 후 unknown·자동 실행 없음 | test_sigkill_recovers_only_predispatch_without_automatic_ocr_replay 2개 |
| W-16 | SIGTERM 후 heartbeat 유지·현재 작업 완료·다음 queued 미인수 | test_sigterm_drains_active_work_and_does_not_claim_next_run |
| W-17 | 실제 배포용 CLI·고정 설정 파일·웹 모델 미로딩·로컬 CPU 포스터 1장 | test_production_worker_cli_frozen_description_and_one_real_paddle_poster. 실제 CPU 포스터 1장·18 OCR/1 항목·attempt 1회 completed |
| W-18 | M008/기존 migration·C01~C12 증거·프런트 원본 불변, 일상 DB 보호, 임시 DB 정리 | C13 baseline SHA256·verify_v1 before/after. 기본 645개·하위 7개, core 1,285개·브라우저 2개·실제 worker OCR 1개 통과 |

테스트 소스: [단위](../../backend/tests/test_c13_worker.py), [DB 통합](../../backend/tests/test_c13_worker_integration.py), [별도 프로세스](../../backend/tests/test_c13_process_integration.py), [실제 Paddle](../../backend/tests/test_c13_real_ocr_integration.py). 실행/제약은 [C13 구현 기록](구현_C13_독립Worker와회수.md)을 따른다.

프로세스 장애 시험에서만 APP_ENV=test로 synthetic adapter·1초 lease/0.1초 heartbeat를 주입한다. 실제 worker CLI에는 이 단축 설정을 노출하지 않으며 기본 정책은 90초/15초다. 실 원격 조회/제출·Linux Paddle worker·전체 백업 복원·운영 공개는 이 대응표로 검증됐다고 주장하지 않는다.

통합 고유 1,288개이며 C13 전용 suite 20개의 재실행을 core에 중복 합산하지 않는다. 모든 실제 DB suite의 일상 DB 보호 대조는 일치했고 임시 DB 잔존은 0개다. CI는 정의/문법 확인이며 원격 실행이 아니다.
