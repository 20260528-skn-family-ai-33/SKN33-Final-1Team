# Creative Checker 사용 안내 챗봇

로그인하면 공통 헤더의 Creative Checker 로고 옆에 **사용 안내**가 표시됩니다. 버튼을 누르면 아래에 패널이 열립니다. 소개와 포스터 검수 시작 방법은 화면에서 바로 제공하고 자유 질문은 FastAPI를 통해 OpenAI에 보냅니다.

## 로컬에서 미리보기

1. 프레임워크 API가 읽는 **비공개 API 환경 파일**의 기존 `creative_checker_openapi`를 사용합니다. 변경 코드로 API를 다시 시작합니다. Docker 실행기에서는 이 워크트리에서 API 이미지를 먼저 다시 빌드합니다. 서버가 키 없이 시작되면 자유 질문에서 미설정 안내를 표시하고 기본 안내 두 항목을 이용할 수 있습니다.
2. Vite의 `API_PROXY_TARGET`과 `FRAMEWORK_API_PROXY_TARGET`을 프레임워크 API로 지정합니다. 아래는 API가 포트 8012인 Bash 예입니다. PowerShell에서는 같은 값을 `$env:API_PROXY_TARGET`, `$env:FRAMEWORK_API_PROXY_TARGET`에 할당한 뒤 npm 명령을 실행합니다. 기존 `AUTH_ALLOWED_ORIGINS`에 브라우저에서 사용하는 실제 Vite 주소가 포함되어 있어야 합니다.

   ```sh
   npm --prefix frontend ci
   API_PROXY_TARGET=http://127.0.0.1:8012 FRAMEWORK_API_PROXY_TARGET=http://127.0.0.1:8012 npm --prefix frontend run dev -- --port 5173 --strictPort
   ```

3. [챗봇 미리보기](http://127.0.0.1:5173/help-chat-preview.html)를 열고, 같은 주소의 서비스에 로그인한 뒤 미리보기로 돌아옵니다. `dev:ui`에는 인증과 API 프록시가 없습니다. 기본 배포 빌드에는 미리보기 페이지가 포함되지 않습니다.

## 프런트 연결

`HelpChatProvider`는 `AuthProvider` 아래, 검수 화면 초기화 영역 바깥에서 로그인 계정당 챗봇을 한 번 마운트합니다. 공통 `SiteHeader`가 로고 옆에 슬롯을 제공하며, 화면을 이동하면 패널은 닫히고 같은 로그인 계정의 대화는 유지됩니다. 로그아웃이나 계정 변경 시 진행 중인 브라우저 요청과 대화가 초기화됩니다. 서버에 이미 접수된 호출은 완료될 수 있습니다.

독립 미리보기의 기본 위치는 우측 하단(`bottom-right`)입니다. `mountHelpChat`의 `position`에 `bottom-left` 또는 `header`를 넘길 수 있습니다. `header`를 쓰면 `container`에 지정한 헤더 슬롯에서 버튼이 열립니다. 패널 스타일은 Shadow DOM 안에 있습니다.

화면 테마: 챗봇을 넣는 화면이 `--cc-help-*` CSS 변수(글자색·강조색·선·모서리·글꼴)를 주면 패널이 그 톤을 따릅니다. 변수가 없으면 기본 색을 씁니다. 받는 변수는 `help-chat.css` 맨 아래 묶음에, Creative Checker 화면의 값은 `src/styles/optimus-theme.css`에 있습니다.

## API

`POST /api/framework/v1/help/chat`은 기존 세션 쿠키와 `GET /api/framework/v1/csrf`에서 받은 `X-CSRFToken`을 확인합니다. JSON 요청의 `question`은 최대 500자이고 `history`는 생략하거나 최근 3회 `{ question, reply }`를 전달합니다. 성공 응답은 `{ reply }`입니다. 같은 계정은 API 프로세스당 1분 10회, 동시 1건으로 제한되며 OpenAI 호출 제한 시간은 20초입니다.

OpenAI Responses API에 `gpt-5.6-luna`를 지정하고 `store: false`를 전달합니다. FAQ 질문 목록과 현재·이전 질문을 모델에 보내 답변 항목을 선택하게 합니다. 서버는 [FAQ 카탈로그](../../../backend/agent_framework/help_chat_faq.json)의 답변 또는 고정 안내 문구를 반환합니다. [FAQ 초안](../../../docs/agent-framework/챗봇_FAQ_초안.md)과 카탈로그의 답변이 일치하는지는 백엔드 테스트에서 확인합니다. 챗봇은 기존 포스터 문구 수정과 같은 서버 키를 사용합니다. 키를 프런트 설정에 노출하지 않습니다. 실제 모델 연결 확인에는 해당 모델 권한이 있는 API 프로젝트 키와 API 서버의 OpenAI HTTPS 연결이 필요합니다.
