# Mac·Windows 공통 실행

2026-11-03 · 데스크톱 개발 환경 · 기존 front-v2 유지

개발자 PC는 Mac과 Windows를 지원하는 방향으로 통일한다. 공통 API·worker는 **Docker Desktop의 Linux 컨테이너**, 파일은 **Linux named volume**, Qwen 추론은 **RunPod Linux 서버**를 사용한다. Windows에서 Python 경로만 바꿔 POSIX 파일 저장소가 실행된다고 안내하지 않는다.

기존 저장소의 descriptor/no-follow·권한·해시·원자적 파일·프로세스 잠금 검증을 유지한다. Windows 네이티브 저장소를 새로 구현하거나 해당 검사를 생략하지 않았다. Mac의 기존 네이티브 실행도 유지하며 팀 공통 경로는 이 가이드를 우선한다. 모바일·태블릿은 범위에 없다.

## 1. 각 PC에서 준비할 도구

- Python 3.12 또는 3.13, Git
- Docker Desktop 실행 및 **Linux containers** 엔진 선택. Windows의 WSL2 기반 Docker Desktop을 사용할 수 있다.
- RunPod 연결에 쓸 OpenSSH Client와 **각 팀원 본인의 SSH 키**. 본인 공개 키가 RunPod 접속에 등록돼 있어야 한다. 다른 사람의 개인 키를 복사하지 않는다.

Mac은 터미널, Windows는 PowerShell에서 프로젝트 루트로 이동한다. 로컬 Python에는 프로젝트 전체 패키지를 설치할 필요 없이 공통 실행기가 Docker를 호출한다. 앱 의존성은 이미지 내부에서 저장소의 `pyproject.toml`·`uv.lock`으로 설치한다. vLLM/GPU 패키지는 RunPod 전용 환경에만 설치한다.

## 2. 이미지 준비 — Mac

```bash
python3 scripts/framework_desktop.py doctor
python3 scripts/framework_desktop.py build
python3 scripts/framework_desktop.py demo
```

## 3. 이미지 준비 — Windows PowerShell

```powershell
py -3.12 scripts/framework_desktop.py doctor
py -3.12 scripts/framework_desktop.py build
py -3.12 scripts/framework_desktop.py demo
```

`doctor`는 Docker Linux 엔진 여부만 확인한다. `demo`는 합성 입력·메모리 저장 개발 시험이며 실모델/DB 연결의 증거가 아니다. 이미지 준비는 명시적 `build`에서만 실행한다. Docker 빌드 컨텍스트는 전용 `.dockerignore`로 제한하고 환경 파일·가상환경·가중치를 이미지에 넣지 않는다.

## 4. 기존 개발 DB의 앱 설정

새 비밀 설정 파일을 만든다. 이미 있으면 덮어쓰지 않고 오류로 종료한다.

Mac:

```bash
python3 scripts/framework_desktop.py init-config
```

Windows:

```powershell
py -3.12 scripts/framework_desktop.py init-config
```

생성된 `.runtime/framework-desktop.env`에 **기존 M008 개발 DB**의 host·port·DB 이름·app 계정과 로그인/CSRF 설정을 입력한다. 실제 `.env`를 덮어쓰지 않는다. owner/admin 암호가 들어 있으면 API/worker 실행기는 거부한다.

PC의 PostgreSQL 또는 PC에 포트가 공개된 PostgreSQL 컨테이너를 사용하면 `DB_HOST=host.docker.internal`과 실제 공개 포트를 사용한다. 원본 설정이 `localhost/127.0.0.1/::1`이면 실행기가 컨테이너용 임시 파일에서만 host 주소로 바꾼다. 같은 Docker network의 DB 서비스 이름을 쓸 때는 `--network 실제네트워크이름`을 추가한다. 외부 DB hostname은 유지한다. Linux 호스트 실행에서는 Docker host-gateway 별칭을 추가하며, Mac/Windows Docker Desktop은 자체 별칭을 사용한다.

이 명령은 DB를 새로 만들거나 M008을 자동 bootstrap하지 않는다. DB 담당자는 기존 개발 DB에 AF003이 준비됐는지 확인한다. 추가 마이그레이션이 필요할 때만 별도 owner 파일로 명시적으로 실행한다.

```bash
python3 scripts/framework_desktop.py migrate --env-file .runtime/framework-owner.env --revision AF003
```

Windows에서는 앞부분만 `py -3.12`로 바꾼다. owner 파일은 해당 일회성 컨테이너에만 읽기 전용으로 전달하며 API/worker에 넘기지 않는다.

## 5. PDF 저장소 초기화와 DB 확인

처음 사용할 **새 Linux 볼륨**에만 한 번 초기화한다. 같은 저장소가 이미 있으면 초기화 오류가 나는 것이 정상이며 재초기화·삭제하지 않는다.

Mac:

```bash
python3 scripts/framework_desktop.py init-pdf
python3 scripts/framework_desktop.py check --env-file .runtime/framework-desktop.env
```

Windows:

```powershell
py -3.12 scripts/framework_desktop.py init-pdf
py -3.12 scripts/framework_desktop.py check --env-file .runtime/framework-desktop.env
```

기본 볼륨 이름은 `cc-framework-data`, 내부 PDF 경로는 `/data/pdf`다. 기존 파일을 자동 이전하지 않는다. `--volume 별도이름`을 사용하면 초기화·API·worker 명령 모두 동일한 이름을 지정한다. PDF payload를 Windows NTFS나 Mac 폴더에 bind mount하여 POSIX 검사를 우회하지 않는다.

DB와 원본 파일 저장소는 같은 실행 환경의 세트다. 같은 DB에 여러 PC의 서로 다른 로컬 볼륨을 섞으면 다른 PC가 등록한 파일을 읽을 수 없다. 팀이 공유하는 DB/파일 환경은 공통 API·worker와 동일 저장소를 사용해야 한다. 이 실행기는 PC 간 파일 동기화나 운영 데이터 마이그레이션 기능이 아니다.

## 6. API와 worker — 처음에는 mock으로 확인

Mac의 첫 터미널:

```bash
python3 scripts/framework_desktop.py api --env-file .runtime/framework-desktop.env
```

Mac의 두 번째 터미널:

```bash
python3 scripts/framework_desktop.py worker --env-file .runtime/framework-desktop.env
```

Windows의 첫 PowerShell:

```powershell
py -3.12 scripts/framework_desktop.py api --env-file .runtime/framework-desktop.env
```

Windows의 두 번째 PowerShell:

```powershell
py -3.12 scripts/framework_desktop.py worker --env-file .runtime/framework-desktop.env
```

브라우저는 두 OS 모두 `http://localhost:8012/docs`다. 기존 DB 로그인·세션·CSRF·프로젝트 권한을 사용한다. 기본 팀 설정은 `team_config.pdf-mock.json`이며 실모델 호출을 하지 않는다. 창을 닫거나 Ctrl+C로 실행을 종료해도 named volume의 payload는 보존한다.

컨테이너 내부 API만 명시적으로 `0.0.0.0:8012`에 bind하고 PC에는 `127.0.0.1:8012`만 공개한다. 기존 네이티브 `postgres-serve` 기본값은 `127.0.0.1`을 유지한다. `--port`로 PC 공개 포트를 바꾸면 비밀 설정의 허용 Origin도 같은 포트로 맞춘다. 프레임워크 API를 실행하는 것이 front-v2 제품 화면 전체를 새로 배포하는 것은 아니다.

## 7. RunPod SSH 터널 — 각 PC에서 실행

Mac의 별도 터미널:

```bash
python3 scripts/framework_desktop.py tunnel --ssh-host 213.173.99.23 --ssh-port 20719
```

Windows의 별도 PowerShell:

```powershell
py -3.12 scripts/framework_desktop.py tunnel --ssh-host 213.173.99.23 --ssh-port 20719
```

기본 키 경로는 각 OS의 사용자 홈 아래 `.ssh/id_ed25519`다. 다른 키를 쓸 때는 `--identity "키 파일 전체 경로"`를 지정한다. 공백·한글·Windows 드라이브 문자를 별도 인자로 전달하며 셸 문자열로 실행하지 않는다. RunPod 재시작 후에는 Connect 탭의 host/port를 다시 확인한다.

PC loopback 18015 → RunPod 내부 8015로 연결한다. Docker API/worker는 `host.docker.internal:18015`로 PC의 터널에 접근한다. 설정 JSON의 loopback endpoint는 읽기 전용 임시 사본에서만 변환하며 원본 파일은 수정하지 않는다. 새 공개 RunPod HTTP 포트를 열지 않는다.

SSH 접속 후 RunPod 안에서 수행하는 설치·`export`·서버 기동은 Mac·Windows 모두 **동일한 Linux Bash 명령**이다. 이전 [Qwen 서버 준비 가이드](Qwen_서버_준비가이드.md)를 따른다. Windows PowerShell에 RunPod의 Bash 명령을 직접 붙여 넣지 않는다. `scp`·SSH 클라이언트 실행에는 Windows SSH 서버(sshd)를 켤 필요가 없다.

## 8. 실제 Qwen 모드

RunPod 서버와 터널을 준비한 뒤 `.runtime/framework-desktop.env`의 `A02_QWEN_API_KEY`에 서버의 `VLLM_API_KEY`와 같은 비밀값을 넣는다. 비밀값을 채팅이나 Git에 올리지 않는다.

Mac:

```bash
python3 scripts/framework_desktop.py api --env-file .runtime/framework-desktop.env --config deploy/qwen-a02/worker-config.example.json
python3 scripts/framework_desktop.py worker --env-file .runtime/framework-desktop.env --config deploy/qwen-a02/worker-config.example.json
```

Windows:

```powershell
py -3.12 scripts/framework_desktop.py api --env-file .runtime/framework-desktop.env --config deploy/qwen-a02/worker-config.example.json
py -3.12 scripts/framework_desktop.py worker --env-file .runtime/framework-desktop.env --config deploy/qwen-a02/worker-config.example.json
```

API와 worker는 각각 별도 창에서 같은 팀 설정·DB·볼륨을 사용한다. 설정을 바꿨으면 기존 창을 종료하고 새 구성으로 접수한다. 이전 구성으로 이미 접수한 job을 임의로 다른 worker에 재실행하지 않는다. 현재 RunPod의 VRAM 조건과 실제 JSON Schema/응답 호환성은 서버 준비·실호출 단계에서 별도로 검증해야 한다. 이 OS 호환 작업은 실제 Qwen 추론을 수행하지 않는다.

## 9. 기존 포스터 저장소를 연결할 때

`registered_poster` 설정에는 기존 M008 제품이 사용 중인 **같은 Docker engine의 Linux 이미지 볼륨**을 명시한다.

```bash
python3 scripts/framework_desktop.py worker --env-file .runtime/framework-desktop.env --config examples/agent_framework/team_config.poster-review.template.json --poster-volume EXISTING_IMAGE_VOLUME --poster-subdir images
```

위 볼륨 이름과 하위 경로는 실제 제품 설정으로 바꾼다. 볼륨 루트 자체가 기존 image store이면 `--poster-subdir .`을 사용한다. 프레임워크는 이미지 저장소를 초기화·복구·동기화하거나 이미지 업로드 서버를 추가하지 않는다. 같은 이름의 새 빈 볼륨을 기존 저장소로 가장하지 않는다. 기본 poster inspector는 아직 미연결이므로 실제 모델 결과를 반환하지 않는다. [포스터 검수 가이드](포스터_검수재검수_가이드.html)의 담당 어댑터 연결이 필요하다.

## 10. 확인한 범위

현재 기록은 [공통 실행 검증 JSON](desktop-runtime-verification-2026-11-03.json)을 따른다. Windows/Linux 공통 실행기 검사와 Linux 컨테이너 빌드·데모·named volume 무결성/잠금·API/worker 통합 검사를 구성했다. Mac Docker Desktop의 실제 실행과 Windows/Linux 원격 CI는 통과했다. Windows Docker Desktop 실기기 관통 실행과 실제 Qwen 호출은 아직 검증하지 않았다. 컨테이너 방식의 지원 구성을 Windows 네이티브 POSIX 저장소 구현 완료라고 설명하지 않는다.

근거: [Docker Desktop 네트워크](https://docs.docker.com/desktop/features/networking/), [Windows Docker Desktop 설치](https://docs.docker.com/desktop/setup/install/windows-install/), [Windows OpenSSH](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).
