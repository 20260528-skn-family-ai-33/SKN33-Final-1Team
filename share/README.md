# 팀 공유 파일

- [HTML 시작 페이지](../docs/agent-framework/index.html)
- [공통 프레임워크 ZIP](agent-framework-2026-11-02.zip)
- [ZIP SHA-256](agent-framework-2026-11-02.zip.sha256)

Git으로 받은 경우 `docs/agent-framework/index.html`을 Chrome에서 엽니다.
ZIP은 **전체 압축을 해제한 뒤** 최상위 `index.html`을 엽니다. macOS와 Windows에 같은 파일을 전달합니다. HTML 열람에는 Python·서버·인터넷이 필요 없습니다.

문서, 업무 흐름, Graphify 코드 지도·라이선스, 공통 프레임워크 코드, 합성 예제·계약, 테스트와 의존성 잠금 파일을 포함합니다. 전체 제품 앱이나 실제 모델·DB는 포함하지 않습니다. 작성자 로컬 채팅은 공유하지 않으며 출처 ID만 유지합니다.

## 재생성

개발 정본은 저장소 루트의 `backend/agent_framework`, `docs/agent-framework`, `tests/agent_framework`입니다. 이전에 올린 한국어 이름 ZIP·압축 해제 폴더는 당시 사본으로 보존합니다. 새 구성 기능은 위 영어 이름 ZIP에서 확인하며, 팀 개발 시에는 루트 정본도 함께 Git에 포함해야 합니다. 공유 폴더 안 사본을 수정해도 루트 코드에 반영되지 않습니다.

저장소 루트에서 실행합니다. Python 표준 라이브러리만 필요합니다.

```sh
python -m scripts.package_agent_framework
```

기존 문서·공통 코드를 수정한 뒤 이 명령을 실행하면 HTML 소스 스냅샷과 ZIP이 함께 갱신됩니다. Graphify 코드 구조가 바뀐 경우에는 해당 생성기로 AST 지도를 먼저 갱신합니다. ZIP은 원본의 배포 사본이며 직접 편집하지 않습니다.

## Git에 함께 포함할 경로

- `backend/agent_framework/`, `tests/agent_framework/`, `examples/agent_framework/`
- `docs/agent-framework/`, `docs/planning/pdf-qwen-rag-workflow-plan-2026-11-02.md`, 동일 이름의 `.html`
- `scripts/build_agent_flow_html.py`, `scripts/build_agent_code_graph.py`, `scripts/package_agent_framework.py`, `scripts/export_agent_framework.py`, `scripts/templates/`
- `.gitattributes`, `.github/workflows/agent-framework.yml`, `README.md`, `PRODUCT.md`, `share/`

기존 `pyproject.toml`, `uv.lock`, `backend/__init__.py`도 ZIP에 정본 그대로 포함합니다. 원본과 배포 파일이 같은 커밋에 들어가도록 확인하세요. 이 작업에서는 커밋·푸시를 실행하지 않았습니다.

## 검증 범위

생성기는 Windows 파일명 규칙, 대소문자까지 일치하는 상대 링크, 외부 자동 로딩 자원, 한글·공백 경로로의 복사, ZIP CRC와 파일 해시를 검사합니다. 실제 Windows Chrome 렌더링을 대신하는 검증은 아닙니다.

`.gitattributes`는 공유 대상의 LF 줄바꿈을 고정해 Windows Git checkout에서 내장 소스 해시가 바뀌는 것을 방지합니다. ZIP은 텍스트 변환 없이 바이너리로 취급합니다.
