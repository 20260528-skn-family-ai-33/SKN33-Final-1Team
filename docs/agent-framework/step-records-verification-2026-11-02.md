> 이전 검증 이력: 이 보고서는 독립 A03 제거·계약 2.0.0 적용 전의 코드와 시험 결과다. 현재 구조와 최신 검증은 [A03 제거 정리](a03-retirement-2026-11-02.md) 및 [담당자 가이드](에이전트_프레임워크_가이드.html)를 따른다.

# 단계별 실행 기록 검증 — 2026-11-02

## 구현

`feat/agent-framework`의 공통 프레임워크에 A02·A03·A04_A05·A06·A07 역할별 실행 기록을 추가했다. 기존 완료 흐름 `stages`, 기계 판정, 사람 승인 계약은 유지한다.

- `StepRecord`: 실행 내 순서, 역할, 상태, UTC 기록·시작·종료 시각, 단조 시계 기반 소요 시간, 안전한 오류 코드와 공통 안내, 합성 자료 표시, 재사용 출처.
- `RunSnapshot.step_records`: 해당 실행의 역할 기록.
- `Workflow.get_steps(run_id)`와 개발 API `GET /api/framework/v1/runs/{run_id}/steps`: 본문 결과 없이 기록 조회.
- 실행 중 상태 `extracting`·`reviewing` 추가. 시작·승인 명령은 동기식이지만 조회는 별도 짧은 잠금으로 처리하여 어댑터 완료를 기다리지 않는다.
- 결과 반영과 단계 완료를 함께 공개한다. 입력 구성부터 출력 계약·출처 검증까지 통과해야 `completed`가 된다. 오류 시 후속 역할을 실행하지 않으며 자동 재시도하지 않는다.
- 재검수의 A02는 `reused`와 `source_run_id`로 나타내고 호출 시각·소요 시간을 만들지 않는다. 부모 실행의 기록은 변경하지 않는다.
- 오류 기록에 파일 바이트·원문·프롬프트·공급자 예외 문자열을 포함하지 않는다. 상위 실행의 기존 `contract_violation` 분류와 단계의 구체적인 오류 코드는 구분한다.

메모리 기록은 영속 감사 로그나 재시작 복구 기능이 아니다. 사람 승인 대기는 역할 실행 시간에 포함하지 않으며 역할 처리 완료는 검수 통과·최종 승인과 다르다. 단독 `role`·`check` 명령은 Workflow 실행 기록을 만들지 않는다.

## 실행 증거

macOS의 프로젝트 루트와 기존 `.venv`에서 실행했다.

```bash
.venv/bin/python -m pytest tests/agent_framework backend/tests/test_c09_contracts_storage.py -q
```

결과: **451 passed, 9 subtests passed**. 기존 pytest-asyncio 설정 경고 2개와 PyMuPDF SWIG 경고 5개가 있었으며 실패는 없었다.

새 `test_step_records.py`의 45개 검사는 다음을 확인한다.

- 정상 5개 역할 순서, 각 역할의 실패, 호출 결과 불명, 출력 계약 위반의 구체적 코드.
- Event로 어댑터를 멈춘 동안 별도 스레드의 스냅샷·단계 조회가 완료됨. HTTP 조회도 FastAPI TestClient로 확인.
- 중복 승인 방지, 서로 다른 실행의 동시 진행, 부모/자식 기록 분리와 A02 재사용.
- 반환 객체 변경으로 내부 기록이 바뀌지 않음, 새 Workflow에는 이전 기록이 없음.
- 비정상 상태 조합·시간·NaN/무한 소요 시간 거부, 시스템 시각 역행 시 단조 시계의 250ms 측정 유지.

```bash
.venv/bin/python -m backend.agent_framework demo --output /tmp/cc-step-records-demo-2026-11-02.json
```

CLI 종료 코드 0. 출력 파일을 다시 읽어 최초 실행의 5개 `completed` 기록과 재검수의 A02 `reused` + 나머지 4개 `completed`를 확인했다. 최초 실행은 `waiting_revision/changes_approved`, 자식 실행은 `completed/final_approved`였다. 합성 어댑터·테스트 승인 결과이며 실제 모델 실행이나 인증된 사람 승인 증거가 아니다.

JSON Schema·개발 OpenAPI를 재생성하고 산출물 일치 검사까지 통과했다. 루트 개발 안내, 흐름도, 내장 소스 뷰어와 AST 코드 지도도 갱신했다. 별도 공유 폴더와 ZIP은 이전 공유 사본으로 유지한다.

## 경계

운영 DB·Qwen·Neo4j·영속 worker·제품 인증·front-v2 통합은 수행하지 않았다. 실제 Windows·원격 CI·브라우저 화면·네트워크로 실행한 API 서버는 이번 검증 범위가 아니다. PostgreSQL 데이터·업로드·환경 파일을 변경하지 않았으며 커밋·푸시는 하지 않았다.

사용법은 [개발 안내](README.md#단계별-실행-기록)를 참고한다. 다음 예정 작업은 설정한 어댑터 조합으로 승인→검사→보고→재검수의 전체 흐름을 확인하는 단계다.
