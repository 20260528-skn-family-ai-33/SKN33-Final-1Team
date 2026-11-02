# Creative Checker 팀 공유 묶음

1. ZIP 전체를 압축 해제하세요. 압축 프로그램 안에서 HTML만 열지 마세요.
2. 이 폴더의 `에이전트_흐름도.html`을 Chrome으로 여세요. macOS·Windows 공통입니다.
3. 폴더 구조를 유지하세요. HTML·스타일·그래프·코드 뷰어가 오프라인으로 열립니다.

전체 계획, 업무 흐름, Graphify AST 코드 지도, 개발 가이드, 공통 프레임워크,
합성 예제, 스키마, 테스트와 재생성 스크립트를 포함합니다.
전체 제품 서버/front-v2/운영 DB/실모델은 포함하지 않습니다.
실제 모델·검색·영속 저장·인증 연결은 후속 개발 범위입니다.

개발 명령은 `docs/agent-framework/guide.html`을 보세요.
`pyproject.toml`·`uv.lock`은 저장소 정본을 그대로 담았습니다.
MANIFEST.json의 SHA-256으로 각 파일의 무결성을 확인할 수 있습니다.
작성자의 로컬 Codex 채팅은 배포하지 않으며 계획에 출처 ID만 기록했습니다.

## 통합문서 반영 업무 흐름 · 2026-11-02

이 폴더의 `에이전트_흐름도.html`은 통합문서에 맞춘 업무 흐름을 바로 보여줍니다. `업무 흐름 / 역할과 연결 / 상태와 버전 / 문서 반영 기준` 탭을 사용하세요. 실제 API 실행이나 승인을 수행하는 화면은 아닙니다.

- 현재 코드 연결 지도: `docs/agent-framework/flow.html`
- 참조 원문 사본: `docs/reference/creative-checker-master-2026-10-30.html`
- 흐름 내용 정본: `docs/agent-framework/business-flow.json`
- 화면 템플릿: `scripts/templates/business-flow.html.in`
- 이 폴더에서 재생성: `python -m scripts.build_business_flow`

2026-10-30 통합 초안의 업무·승인·버전 원칙을 반영하되, 후속 합의인 PDF·포스터 / Qwen / PostgreSQL·Neo4j RAG / 벡터DB 제외를 유지했습니다. 문서와 코드가 다른 직접 최종 승인·재검수 기준 재승인 정책은 별도로 표시합니다.

이 폴더 바깥에 있는 기존 ZIP은 수정 전 배포 사본입니다. 폴더를 수정했다고 기존 ZIP이 자동 갱신되지는 않습니다.
