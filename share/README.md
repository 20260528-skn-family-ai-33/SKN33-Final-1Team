# 문서 바로 찾기

- [에이전트 흐름도](에이전트_흐름도/에이전트_흐름도.html): 역할·승인·재검수의 업무 흐름
- [에이전트 프레임워크 가이드 HTML](../docs/agent-framework/에이전트_프레임워크_가이드.html): 담당자별 수정 파일·factory·실행법·테스트 명령
- [가이드 Markdown 원본](../docs/agent-framework/에이전트_프레임워크_가이드.md)
- [프로젝트 문서 시작 페이지](../docs/agent-framework/index.html)

Git으로 받은 프로젝트에서 한글 이름의 HTML을 Chrome으로 열면 됩니다. HTML 열람에는 서버·Python·인터넷 연결이 필요 없습니다. 기존 흐름도의 `index.html`과 가이드의 영어 이름 링크는 새 이름으로 연결됩니다.

## 개발 기준

개발 정본은 프로젝트 루트의 `backend/agent_framework`, `examples/agent_framework`, `tests/agent_framework`, `docs/agent-framework`입니다. `share/에이전트_흐름도`의 코드는 이전 공유 시점의 사본입니다. 현재 개발 가이드는 `docs/agent-framework/에이전트_프레임워크_가이드.html`을 사용하세요.

기존 ZIP은 이전 공유 자료로 보존하며 새로 만들거나 갱신하지 않습니다. 현재 공유 방법은 Git입니다.

가이드 재생성은 프로젝트 루트에서 `python -m scripts.build_agent_handoff`를 실행합니다. 흐름도의 한글 이름 재생성은 `share/에이전트_흐름도` 폴더에서 `python -m scripts.build_business_flow`를 실행합니다. 자세한 문서 생성법은 프로젝트 `docs/agent-framework/README.md`를 참고하세요.
