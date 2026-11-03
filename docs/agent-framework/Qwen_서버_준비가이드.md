# Qwen 서버 1단계 — Pod 확인과 실행 구성

2026-11-03 · 대상 `faithful_moccasin_puma` / `bh1xgw8db1mp5w`

이번 단계는 원격 읽기 전용 점검과 로컬 실행 파일 작성이다. Git clone/pull, Pod 생성·중지·재시작, 패키지 설치, 가중치 다운로드, 추론 서버 기동은 수행하지 않았다. [점검 JSON](qwen-server-preparation-2026-11-03.json)이 당시 확인 범위를 기록한다.

## 1. 접속 포트와 현재 Pod

`213.173.99.23:20719 → 22`는 외부 TCP 20719를 컨테이너 SSH 22로 연결한다. SSH의 `-p`에는 **20719**를 쓴다. 주소와 외부 포트는 재시작 뒤 달라질 수 있으므로 RunPod Connect 탭에서 다시 확인한다.

```bash
ssh -p 20719 -i ~/.ssh/id_ed25519 root@213.173.99.23
```

현재 실제 조회 결과는 RTX 2000 Ada 16,380MiB, 사용 5,599MiB, 여유 10,352MiB였다. GPU 사용률 0%와 메모리 비점유는 다르다. 기존 `backend.app.video_logo_worker:app`과 `backend.app.paddle_ocr_server`가 실행 중이다. 이 기존 OCR 프로세스는 보존 대상이며 A02에 PaddleOCR을 다시 넣는다는 뜻이 아니다.

현재 Pod는 코드·서버 환경을 별도로 준비할 수 있다. **기존 서비스와 BF16 Qwen의 안전한 동시 구동 여유는 아직 확인되지 않았다.** 초기 실행 구성은 14GiB 이상 여유를 요구하는 보수적 정책으로, 현재 메모리 상태에서 시작을 거부한다. 이 수치는 제품의 최소 사양이나 실측 Qwen 사용량이 아니다. 기존 서비스를 계속 유지해야 하면 전용 Pod를 따로 사용하는 쪽을 권장한다. 기존 서비스를 임의 종료하거나 다른 Pod를 켜지 않는다.

## 2. 먼저 Git 작업본을 분리한다

`/workspace/SKN33-Final-1Team`은 이미 실행 중인 서비스가 참조하는 **.git 없는 파일 복사본**이다. 여기에 `git init`, checkout, 덮어쓰기, requirements 설치를 하지 않는다.

| 경로 | 용도 |
|---|---|
| `/workspace/SKN33-Final-1Team` | 기존 서비스. 보존 |
| `/workspace/creative-checker-a02/repository` | 새 Git 작업본 예정 경로 |
| `/workspace/creative-checker-a02/runtime/venv` | GPU 서버만의 Python 환경 |
| `/workspace/creative-checker-a02/runtime/cache` | A02 런타임 캐시 |
| `/workspace/creative-checker-a02/models/Qwen3.5-4B/<revision>` | 고정 모델 파일 |

서버 준비 점검 당시 로컬 브랜치는 `feat/37-a02-pdf-registration`이며 원격에 같은 이름이 없었다. 새 프레임워크·서버 준비 파일도 미커밋 상태였다. **필요한 변경을 검토·커밋·push하고 원격 커밋을 확인한 뒤 clone**해야 최신 파일을 받을 수 있다. 이 점검 이력과 이후 Git 게시를 구분한다. 다른 환경/실행 스크립트 변경이 함께 있으므로 전체 파일을 무조건 stage하지 않는다.

아래는 해당 브랜치가 원격에 준비된 다음 수행할 명령이다. 대상 경로가 이미 있으면 내용을 확인하고 재사용 여부를 결정하며 삭제·강제 초기화하지 않는다.

```bash
git clone --branch feat/37-a02-pdf-registration https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team.git /workspace/creative-checker-a02/repository
git -C /workspace/creative-checker-a02/repository rev-parse HEAD
git -C /workspace/creative-checker-a02/repository status --short
```

실행할 때에는 확인한 커밋 SHA를 기록하고 그 커밋을 유지한다. 서버가 실행 중인 디렉터리에서 무조건 pull하는 운영 방식은 사용하지 않는다. Git URL에 토큰을 넣거나 팀원에게 개인 SSH 키를 공유하지 않는다.

## 3. 서버 전용 패키지만 설치한다

현재 기본 Python은 3.12.3, torch 2.8.0+cu128, transformers 4.56.2이고 기본 환경에 vLLM은 없다. 기존 base Python과 `/root/paddlegpu`는 그대로 둔다. 프로젝트 전체의 `uv sync`를 GPU 서버 환경에 수행할 필요는 없다. DB·인증·PDF 파싱·승인·worker는 기존 앱 측에서 맡고, 이 Pod의 새 서버는 추론만 제공한다.

후속 2단계에서 새 가상환경을 만들고 [서버 요구사항](../../deploy/qwen-a02/requirements-server.txt)을 설치한다.

```bash
python3 -m venv /workspace/creative-checker-a02/runtime/venv
/workspace/creative-checker-a02/runtime/venv/bin/python -m pip install --no-cache-dir -r /workspace/creative-checker-a02/repository/deploy/qwen-a02/requirements-server.txt
```

후보는 `vllm==0.17.1`로 고정했다. 공식 해당 tag에 Qwen3.5 모델 구현이 있는 것을 확인했으며 torch 2.10.0 등을 별도 환경에 설치하는 구성이다. **현재 GPU에서 설치·CUDA kernel·실제 모델 적재가 검증된 버전은 아니다.** 2단계에서 호환성을 시험하고 최종 패키지 목록·wheel·CUDA 정보를 기록한다. 단일 최상위 버전 고정은 전체 전이 의존성 lock을 대신하지 않는다. 검증 없이 nightly/latest로 자동 변경하지 않는다.

## 4. 모델 파일을 고정한다

모델은 `Qwen/Qwen3.5-4B`, revision은 `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`다. 공식 모델 파일 두 shard는 약 9.32GB(8.68GiB)이며 이 크기는 GPU 메모리 실측값이 아니다. [manifest](../../deploy/qwen-a02/model-manifest.json)에 모델 shard·설정·토크나이저의 공식 파일 SHA-256을 기록했다.

현재 `/workspace/models`는 없고 `/workspace/hf-cache/hub`, `/workspace/.cache/huggingface/hub`에서 확인한 모델은 BiRefNet·OWLv2·SigLIP 계열이다. **이 경로에서는 Qwen3.5-4B를 찾지 못했다.** 다른 Pod나 미조사 경로까지 없다고 판정한 것은 아니다. 과거 다른 호스트에 보존했던 가중치가 이 Pod에도 있다고 가정하지 않는다.

후속 설치 단계에서 먼저 재사용 가능한 같은 revision 파일을 확인한다. 없으면 공식 revision을 전용 모델 경로에 내려받고 모든 manifest 해시를 검증한다. 기존 캐시를 이동·삭제하지 않는다. `/workspace`는 다른 Pod에서도 같은 글로벌 볼륨을 참조하는 것으로 관측됐으므로 전용 하위 디렉터리만 사용한다. `df`의 1.0P 표시는 실제 계정 여유 공간/무료 저장 용량으로 해석하지 않으며 볼륨 과금·저장 여유는 설치 전에 확인한다.

```bash
/workspace/creative-checker-a02/runtime/venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='Qwen/Qwen3.5-4B',
    revision='851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a',
    local_dir='/workspace/creative-checker-a02/models/Qwen3.5-4B/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a',
)
PY
```

## 5. 실행 구성과 사전 확인

[server.json](../../deploy/qwen-a02/server.json)은 loopback 8015, BF16, 텍스트 전용, 최대 context 32,768 tokens, 동시 요청 1개, eager 실행으로 시작한다. 기존 8001 nginx·8791 서비스·22/8888 포트를 유지한다. A02의 최대 32,000자는 token 수와 다르므로 실제 PDF 시험에서 입력+출력 token 예산을 확인한다. 포스터 이미지를 처리하는 A04·A05에는 이 텍스트 전용 프로필을 그대로 쓰지 않는다.

다음 `plan`은 Mac에서도 실행 가능하며 GPU 조회·설치·네트워크 호출·서버 기동을 하지 않는다.

```bash
python3 deploy/qwen-a02/server.py plan
```

서버 API 키는 별도의 비밀값으로 준비해 `VLLM_API_KEY`에 설정한다. 앱 worker의 `A02_QWEN_API_KEY`와 같은 값을 사용하며 Git·명령 인자·문서에 실값을 남기지 않는다. RunPod 계정 API 키나 HF 토큰을 서버 인증 키로 재사용하지 않는다.

설치·모델 검증·VRAM 확보가 끝난 다음, 서버 전용 Python으로 실행한다. `check`가 기존 listener·VRAM·가상환경·패키지 버전·키·모델 파일을 검사하고 `serve`는 같은 검사와 전체 파일 해시 검증을 통과해야만 foreground 서버를 시작한다. 어떤 프로세스도 자동 중지하지 않으며 모델을 자동 다운로드/재시작하지 않는다.

```bash
/workspace/creative-checker-a02/runtime/venv/bin/python /workspace/creative-checker-a02/repository/deploy/qwen-a02/server.py check --verify-model
/workspace/creative-checker-a02/runtime/venv/bin/python /workspace/creative-checker-a02/repository/deploy/qwen-a02/server.py serve
```

기존 GPU 프로세스는 단계 1에서 중지하지 않았다. VRAM이 확보되지 않으면 `GPU_MEMORY_NOT_AVAILABLE`로 실패하는 것이 현재 구성의 정상 동작이다. TOCTOU를 완전히 제거하는 GPU 예약 장치는 아니므로 기동 직전에도 실행 담당을 조율한다.

## 6. SSH 터널과 A02 연결 위치

작업 PC의 별도 터미널에서 접속을 유지한다. 새 공개 HTTP 포트는 열지 않는다.

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18015:127.0.0.1:8015 -p 20719 -i ~/.ssh/id_ed25519 root@213.173.99.23
```

| 구간 | 주소 |
|---|---|
| SSH | `213.173.99.23:20719` → 컨테이너 22 |
| GPU 서버 내부 | `127.0.0.1:8015` |
| 작업 PC tunnel | `127.0.0.1:18015` |
| A02 endpoint | `http://127.0.0.1:18015/v1/chat/completions` |

Linux EC2 worker로 옮기면 그 worker 호스트에서 터널을 유지하거나 인증된 내부 경로를 별도로 구성한다. Mac의 loopback endpoint를 EC2가 그대로 사용할 수는 없다.

[worker-config.example.json](../../deploy/qwen-a02/worker-config.example.json)은 현재 프레임워크 계약을 따르는 A02-only 실모델 예제다. API와 worker에 동일한 구성을 사용하며 app 전용 DB 설정·전용 PDF root는 기존 [A02 연결 가이드](A02_조건추출_연결가이드.html)를 따른다. GPU Pod에는 DB owner/app 암호·기준 승인 권한을 넘기지 않는다. 기존 PDF mock 설정을 덮어쓰지 않는다.

이번에는 이 파일을 적용하거나 Qwen HTTP 요청을 실행하지 않았다. JSON Schema·비사고 모드·샘플링·정확한 model 이름 응답·타임아웃/결과 불명 처리는 후속 3단계 실호출에서 확인한다. 단순 모델 목록/health 성공이 A02 추출 품질 검증은 아니다.

## 7. 다음 작업의 순서

1. 필요한 프레임워크/서버 파일을 검토·커밋·push하고 원격 commit을 확인한다.
2. 전용 Git 작업본·가상환경을 만들고 저장 여유를 확인한다.
3. 고정 revision의 모델을 준비·해시 검증하고 기존 GPU 작업의 유지 여부에 맞춰 VRAM/Pod를 확정한다.
4. Qwen 서버를 기동한 뒤 3단계 프로토콜 시험, A02 PDF 3종 추출, DB 보완·사람 승인·재조회 순서로 진행한다.

이번 파일은 **서버 준비 1단계**다. 앞서 완료한 합성 7단계의 기준을 실제 사람 승인으로 바꾸지 않으며 그 진행 기록을 새 단계 번호로 덮어쓰지 않는다.

근거: [Qwen 공식 고정 모델](https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a), [vLLM 공식 v0.17.1 Qwen3.5 구현](https://github.com/vllm-project/vllm/blob/v0.17.1/vllm/model_executor/models/qwen3_5.py), [RunPod 저장소 안내](https://docs.runpod.io/pods/storage/types).
