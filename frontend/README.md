# 바로검수 프런트엔드

React와 Vite로 만든 이미지·포스터 검수 화면입니다. 자료 입력 → 검수 항목 확인 → 기준 확정 → OCR 실행 → 결과와 근거 확인 순서로 진행합니다. 이미지는 연결된 FastAPI 서버에서 처리합니다.

## 설치와 통합 실행

Node.js `20.19 이상 20.x` 또는 `22.12 이상`이 필요합니다. 다음 명령은 저장소 루트에서 실행합니다.

**macOS / Linux · Bash**

```bash
npm --prefix frontend ci
npm --prefix frontend run build
./run.sh
```

**Windows · PowerShell**

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
& .\.venv-review\Scripts\python.exe .\start.py
```

Python 환경과 OCR 모델 설치는 [루트 README](../README.md#새-컴퓨터에서-설치)를 먼저 따라 주세요. PowerShell에서는 `npm.cmd`와 가상환경의 Python을 직접 호출하므로 `Activate.ps1` 실행이나 실행 정책 변경은 필요 없습니다. 실행 후 `http://127.0.0.1:8000`을 열고 서버 터미널을 유지합니다. 종료는 `Ctrl+C`입니다. `index.html`을 파일로 직접 열면 API에 연결되지 않습니다.

## 화면 개발

터미널 1에서 API를 실행합니다.

**macOS / Linux · Bash**

```bash
.venv-review/bin/python -m uvicorn backend.app.api:app --host 127.0.0.1 --port 8000 --reload
```

**Windows · PowerShell**

```powershell
& .\.venv-review\Scripts\python.exe -m uvicorn backend.app.api:app --host 127.0.0.1 --port 8000 --reload
```

터미널 2에서 Vite를 실행하고 표시된 주소(기본 `http://127.0.0.1:5173`)를 엽니다.

**macOS / Linux · Bash**

```bash
npm --prefix frontend run dev
```

**Windows · PowerShell**

```powershell
npm.cmd --prefix frontend run dev
```

화면은 `/api` 상대 경로로 요청합니다. 개발 서버는 이를 `http://127.0.0.1:8000`으로 전달합니다. API를 `--port 8001`로 실행했다면 Vite 실행 시 대상 주소도 지정합니다.

**macOS / Linux · Bash**

```bash
API_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix frontend run dev
```

**Windows · PowerShell**

```powershell
$env:API_PROXY_TARGET = "http://127.0.0.1:8001"
npm.cmd --prefix frontend run dev

# Ctrl+C로 개발 서버를 종료한 뒤, 기본 포트로 되돌릴 때
Remove-Item Env:API_PROXY_TARGET -ErrorAction SilentlyContinue
```

PowerShell의 환경 변수는 현재 터미널과 그 터미널에서 실행한 프로세스에 적용됩니다. 값을 바꿨다면 Vite를 다시 실행하세요.

`API_PROXY_TARGET`은 Vite 개발·미리보기 프록시에서만 사용합니다. 빌드한 화면은 FastAPI와 같은 출처에서 제공하며, 프록시 주소나 RunPod 비밀키를 브라우저 번들에 넣지 않습니다.

## 검증

**macOS / Linux · Bash**

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

**Windows · PowerShell**

```powershell
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

상태 테스트는 기준 확정·잠금, 변경 후 재확정, 이전 결과 무효화, 늦은 응답 차단, 원본 이미지 좌표 표시를 확인합니다. 전체 업로드·OCR 흐름은 API를 실행한 상태에서 확인합니다.

`package-lock.json`은 Git에 포함합니다. `node_modules/`와 `dist/`는 설치·빌드로 다시 생성하며 Git에서 제외합니다.
