> 이전 검증 이력: 이 보고서는 독립 A03 제거·계약 2.0.0 적용 전의 코드와 시험 결과다. 현재 구조와 최신 검증은 [A03 제거 정리](a03-retirement-2026-11-02.md) 및 [담당자 가이드](에이전트_프레임워크_가이드.html)를 따른다.

# PostgreSQL 실행·단계·승인 저장과 worker 검증 — 2026-11-02

## 결과와 구현 범위

기존 M008 DB를 재사용하는 AF001 추가 스키마, 인증된 프레임워크 API, 독립 LangGraph worker를 구현했다. API는 접수·승인·조회만 처리하고 모델 역할은 worker만 실행한다. 기존 사용자·프로젝트·멤버십·세션·CSRF·권한 검사·app Engine·트랜잭션 처리를 재사용한다.

public M008의 구조·권한 계약은 그대로 유지한다. 새 프레임워크의 추출·승인 대기·검수는 기존 OCR 검수 행의 제약과 다르므로 같은 DB 안의 `agent_framework` 스키마에 기록한다. 별도 업무 DB나 중복 사용자·프로젝트 테이블을 만들지 않는다.

추가 테이블은 `schema_version`, `runs`, `jobs`, `steps`, `approvals`, `events`다. 소유자 자격증명으로 실행하는 명시적 추가 마이그레이션 `migrate-postgres`만 생성할 수 있으며 API·worker 시작은 읽기 전용 구조·권한 검사를 수행한다. 승인·이벤트는 app 계정에 UPDATE·DELETE를 허용하지 않고, 끝난 단계 결과는 DB trigger로 변경을 거부한다.

공통 계약에는 PostgreSQL 접수 상태 `queued`, 타임라인의 `storage=postgres`, 복구된 결과 불명 단계의 미측정 `duration_ms=null`을 반영했다. 메모리 개발 도구는 계속 제공한다.

## 새 환경·실행 증거

프로젝트의 기존 `.venv`를 변경하지 않고 `/tmp/cc-framework-locked-20261102`에 `uv.lock` 그대로 새 가상환경을 설치했다. 새 환경에서 다음을 실행했다.

```bash
/tmp/cc-framework-locked-20261102/bin/python -m pytest tests/agent_framework backend/tests/test_c09_contracts_storage.py -q
```

결과: **492 passed, 16 deselected, 9 subtests passed**. 16개는 명시적 DB 통합 검사를 별도로 실행하기 때문에 기본 검사에서 제외됐다. PyMuPDF SWIG 경고 5개가 있었으며 실패는 없었다.

Docker의 기존 서비스와 구분하여 `cc-framework-pg-test-20261102` 전용 PostgreSQL **17.11-bookworm** 컨테이너를 생성했다. 기존 저장소의 테스트 fixture가 매번 새 무작위 `creative_checker_test_*` DB를 생성하고 그 DB만 제거하도록 사용했다. app은 제한된 역할, 마이그레이션은 별도 비관리자 DB 소유자 역할로 실행했다.

```bash
/tmp/cc-framework-locked-20261102/bin/python -m pytest tests/agent_framework/test_postgres_integration.py -m db_integration -q
```

명시적 테스트 전용 `TEST_DB_ENV_FILE`을 사용했다. 결과: **16 passed**. 실제 운영 DB·기존 사용자 계정·업로드 파일은 사용하지 않았다.

| 검증 항목 | 확인한 동작 |
|---|---|
| M008 재사용 | 추가 스키마 적용 전후 기존 public 구조·권한 fingerprint 동일 |
| 접수·재전송 | 같은 run_id·고정 입력은 같은 실행, 다른 입력은 충돌 |
| 승인·재검수 | 저장·새 phase 접수 원자 처리, 반복 승인 재실행 없음, 부모·자식 분리 |
| API 재시작 | 새 앱 인스턴스가 동일 세션으로 저장된 실행·승인 시각 조회 |
| 별도 worker 프로세스 | 새 Python CLI 프로세스가 접수된 추출 phase를 처리 |
| 동시 선점 | 두 worker 중 한 worker만 선점 |
| 안전한 재개 | 시작 표시 전 종료는 재선점, 완료된 A03 결과는 재호출 없이 재사용 |
| 실행 중 종료 | 시작 표시 후 lease 만료는 `outcome_unknown`, 늦은 결과 저장 차단 |
| heartbeat 상실 | DB가 다시 응답하더라도 이전 worker의 결과 확정 거부 |
| 권한 회수 | 실행 전·결과 커밋 직전 현재 프로젝트·계정·멤버십 재검사 |
| COMMIT 응답 유실 | 성공한 DB 커밋의 응답 유실을 주입해 자동 재호출이 없음을 확인 |
| 계약·불변성 | 커밋 경계에서 출력 scope 재검사, 완료 결과·승인 덮어쓰기 차단 |
| 구조·권한 drift | 컬럼·승인 UPDATE 권한 변조 시 readiness 거부 |
| 모델 호출 경계 | 어댑터 호출 중 DB 연결·실행 행 잠금을 보유하지 않음 |

일반 검사에는 런타임의 owner 자격증명 거부, 어댑터 없는 지연 접수·승인, 복구된 결과 불명 소요 시간 미추정 검사도 포함했다.

의존성 잠금 파일에 Alembic·argon2-cffi·email-validator 및 하위 의존성 7개 패키지를 추가했다. 기존 잠금 패키지 버전은 변경하지 않았다. 새 잠금 환경의 검증 결과를 위에 기록했으며 기존 가상환경 결과만으로 설치 검증을 대체하지 않았다.

## 문서와 정적 자료

- [PostgreSQL 프레임워크 실행가이드](에이전트_프레임워크_PostgreSQL_실행가이드.html)와 Markdown, app/owner 설정 예시를 추가했다.
- 팀원별 연결 가이드·프로젝트 시작 페이지에 새 실행 경로를 연결했다.
- JSON Schema·개발 OpenAPI·내장 소스·코드 그래프를 갱신했다. 그래프는 Python 24개 파일 대상이며 SQL migration은 AST에서 명시적으로 제외하고 흐름도 코드 근거에 포함했다.
- 관련 문서 산출물 회귀 6개가 통과했다. 브라우저 화면 렌더링 검증은 수행하지 않았다.
- PostgreSQL 통합 CI를 추가했다. 원격 CI의 실제 실행 결과는 아직 확인하지 않았다.

## 적용 경계

현재 사용하는 실제 DB에 AF001을 적용하거나 새 API·worker를 상시 배포하지 않았다. 실제 DB 적용은 owner 전용 설정과 기존 백업 절차로 대상을 확인한 후 명시적으로 수행한다. 운영 backup/restore 절차가 새 스키마까지 포함하는지도 배포 시 확인해야 한다.

모델 어댑터·Neo4j RAG/projection·front-v2·운영 프로필·실제 Windows 장치 검증은 후속 범위다. 이번 테스트의 역할 출력과 사람 승인은 합성 개발 자료다. 기존 제품 소스·운영 데이터·모델·업로드·환경 파일은 변경하지 않았다. 기존 서비스용 leader lock을 획득하지 않고 AF 작업만 처리한다. 커밋·푸시·새 ZIP 생성은 하지 않았다.
