# 에이전트 흐름도 · 팀 열람용

이 폴더의 index.html 또는 에이전트_흐름도.html을 Chrome에서 엽니다. 작은 노드·확대/이동·오른쪽 상세 UI를 유지하며 macOS·Windows 공통 상대 링크를 사용합니다. HTML은 서버 없이 열람합니다.

2026-11-02 통합 정리본을 반영해 독립 A03을 제거했습니다. 네 구성 역할은 A02·A04_A05·A06·A07이며 공통 승인 입력·근거 준비는 별도 에이전트가 아닙니다. 관계 조회는 PG→Neo4j→PG 공통 기능의 후속 목표입니다.

개발·실행 정본은 프로젝트 루트의 backend/agent_framework, examples/agent_framework, tests/agent_framework, docs/agent-framework입니다. 여기의 코드는 HTML 열람·코드 근거용 사본입니다. 기존 제품·인증·DB 전체를 독립 배포한 폴더가 아니므로 worker도 프로젝트 루트에서 실행합니다.

담당자별 연결·실행법은 docs/agent-framework/에이전트_프레임워크_가이드.html, PostgreSQL은 별도 실행가이드, 최신 검증은 docs/agent-framework/a03-retirement-2026-11-02.md를 확인합니다. 계약 2.0.0이며 구버전 설정·실행을 자동 이관하지 않습니다. ZIP은 갱신하지 않았고 Git으로 공유합니다.

브라우저 실제 렌더링과 Windows 기기 실행은 미검증입니다.
