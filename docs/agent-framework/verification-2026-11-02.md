# 공통 개발 프레임워크 검증 기록

- 날짜: 2026-11-02 (Asia/Seoul)
- 작업 브랜치: `feat/agent-framework`
- 시작 커밋: `87a3b9e` (`feat/29-front-v2`에서 분기)
- 대상: 역할별 독립 개발 계약과 개발용 실행기. 실제 제품 통합 완료 기록이 아니다.
- 상태: 로컬 작업 파일이며 커밋·푸시하지 않았다.

## 실행한 검사

저장소 루트의 기존 `.venv`를 사용했다. 이번 작업에서 의존성 설치·잠금 파일 갱신은 하지 않았다.

```sh
.venv/bin/python -m scripts.export_agent_framework
.venv/bin/python -m pytest tests/agent_framework -q --tb=short
.venv/bin/python -m backend.agent_framework demo --output /tmp/agent-framework-demo-20261102.json
.venv/bin/python -m backend.agent_framework role A03 --input examples/agent_framework/A03.json --synthetic --output /tmp/agent-framework-A03-20261102.json
git diff --check
```

결과: **104개 테스트와 9개 하위 테스트 통과**, `git diff --check` 통과.

- 공통 JSON 계약 및 모델 생성 우회 출력 재검증.
- 잘못된 프로젝트·문서·조건·이미지 버전, 위조 인용·해시, 과거 이력의 현재 관측 사용 차단.
- 조건 누락·중복·불확실 관측·지원하지 않는 비교·수량 모순·검색 지연/실패 처리.
- 조건 승인, 수정 요청 승인, 새 버전 전체 재검수, 최종 승인 및 예외 승인 제한.
- 동시/중복 승인, 새 실행 ID 충돌, 승인 자동 승계 방지.
- 모델·검색 미설정 및 결과 불명 시 합성 대체·자동 재호출 없음.
- 모든 역할의 독립 CLI, factory 교체, 합성 입력 표시 전파, 오류 시 기존 출력 파일 보존.
- FastAPI TestClient를 통한 접수·조회·승인·재검수와 오류 응답. 서버 포트는 열지 않았다.
- 17개 JSON Schema, 15개 합성 예제 JSON, 개발 OpenAPI의 생성 결과와 저장된 파일 일치.

demo는 2개 합성 실행에서 6개 동작을 수행했다. 수정 전 실행은 `changes_approved`, 수정 후 실행은 `final_approved`로 끝났으며 두 실행 모두 `synthetic=true`다. 승인자는 스크립트 입력이므로 실제 사용자의 승인 기록으로 사용할 수 없다.

## 검증 범위와 남은 연결

현재 환경에는 `pytest-asyncio` 플러그인이 없어 기존 `asyncio_mode`, `asyncio_default_fixture_loop_scope` 설정에 대한 경고 2개가 발생했다. 이번 검사는 동기 테스트이며 모두 통과했다. 잠금 파일로 새 환경을 설치한 결과로 확대 해석하지 않는다.

실제 Qwen·PDF·이미지 처리, PostgreSQL·Neo4j 쿼리, MCP 전송, 영속 worker 복구, 제품 인증 및 front-v2 API 연결은 이번 검증에 포함하지 않는다. 모델 정확도·검색 품질·배포 완료를 검증한 것이 아니다.

기존 front-v2, `backend/app`, `backend/creative_checker`, 마이그레이션, 실행 스크립트, 의존성 파일은 변경하지 않았다. 업무 DB와 환경설정에 접근하거나 서비스를 기동하지 않았다. 이전에 작성한 계획 MD·HTML은 그대로 보존했다.

Ubuntu·Windows용 GitHub Actions 검사를 추가했지만 아직 원격 실행하지 않았다. Windows 장치 실행과 제품 전체 회귀 테스트는 이번 로컬 검증 결과에 포함하지 않는다.
