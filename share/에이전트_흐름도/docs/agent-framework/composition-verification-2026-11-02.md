> 이전 검증 이력: 이 보고서는 독립 A03 제거·계약 2.0.0 적용 전의 코드와 시험 결과다. 현재 구조와 최신 검증은 [A03 제거 정리](a03-retirement-2026-11-02.md) 및 [담당자 가이드](에이전트_프레임워크_가이드.html)를 따른다.

# 역할별 구성·공통 호출 경계 검증 · 2026-11-02

## 이번 구현

- 역할별 `synthetic/reference/factory/unconfigured` 구성을 선언하는 `TeamConfiguration`과 JSON Schema.
- 선택한 역할만 초기화하는 `role --config`, 팩터리를 import하지 않는 `doctor --config`, 명시한 구성으로 loopback 개발 API를 시작하는 `serve --config`.
- CLI와 Workflow가 함께 사용하는 `invoke_role`: 입력·출력 재검증, 깊은 복사, 문맥·근거·판정 검사, 중첩 합성 표시 유지, 비동기 메서드 거부.
- 개발 API의 역할별 상태와 `mixed` 구성 표시. `GET /api/framework/v1/configuration`에서 선언과 초기화한 어댑터 타입을 분리해 확인.
- 담당자별 독립 factory 시작 파일과 합성/미설정 구성 예제.

## 실행 확인

- 저장소의 기존 `.venv/bin/python -m pytest tests/agent_framework -q`: **191 passed, 9 subtests passed**.
- 기존 환경의 pytest-asyncio 미설치로 관련 설정 경고 2개가 남음. 테스트 실패는 없음.
- 실제 CLI `doctor`: 종료 0, 팩터리 import 없음, 모든 역할의 `connection_verified=false` 확인.
- 실제 CLI `role A03 --config` 합성 구성: 종료 0, 근거 2개, `synthetic=true` 확인.
- 미설정 구성의 A03: 종료 1, `ADAPTER_NOT_CONFIGURED`. 합성 데이터로 대체하지 않음.
- 실제 `.venv/bin/python -m backend.agent_framework demo`: 조건 승인 → 검수 → 수정 요청 → 새 버전 → 재검수 → 최종 승인 상태 전이 확인. 승인은 합성 테스트 값이다.
- TestClient로 혼합 구성의 health/configuration, 조건 승인·최종 승인, 요청을 통한 설정 주입 거부 확인.
- serve는 테스트에서 uvicorn 호출을 대체해 `127.0.0.1`, 단일 worker, reload 비활성, 자동 실행·승인 없음 확인. 실제 서버를 기동한 검증은 아니다.
- Graphify AST 스냅샷을 변경된 14개 Python 파일로 다시 생성: 225개 노드·804개 연결. 업무 실행 흐름이나 실서비스 연결 증거가 아니다.

## 범위와 Git 공유

실제 Qwen/PDF 읽기, PostgreSQL·Neo4j 검색, 영속 저장·worker, 제품 인증·front-v2 연결은 이번 구현에 포함하지 않았다. Windows 실제 실행과 Chrome 화면 검증, 새 환경의 uv 설치, 원격 CI 실행도 수행하지 않았다.

개발 정본은 루트의 `backend/agent_framework`, `tests/agent_framework`, `examples/agent_framework`, `docs/agent-framework`, `scripts`다. 기존 `share/에이전트_흐름도`와 해당 ZIP은 이전 공유 사본으로 보존했다. 이번에는 루트 정본도 Git에 함께 포함해야 팀원이 저장소 루트에서 실행할 수 있다. 커밋·푸시는 실행하지 않았다.

HTML·코드 뷰어·ZIP은 `python -m scripts.package_agent_framework`로 재생성한다. 각 묶음의 파일 목록·해시·상대 링크 검사 결과는 MANIFEST.json에 남는다.
