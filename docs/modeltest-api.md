# 모델 테스트 화면과 Pod API

`/modeltest`는 이미지·영상 검수의 단계별 Hugging Face 모델을 설정하는 테스트 화면이다. 기본 프런트엔드와 v1 프런트엔드에서 같은 화면을 사용한다. 기존 실행 방법으로 로컬 백엔드와 프런트엔드를 시작하고 `/modeltest`로 접근한다. 기본 연결 방식은 SSH이며 HTTP API 방식도 선택할 수 있다.

**SSH 방식에는 Pod에서 실행할 검수 스크립트가 포함된다.** 로컬 백엔드가 SSH 연결·파일 전송·실행·결과 수신을 맡으므로, Pod에 공개 FastAPI 서버나 HTTP 포트를 따로 마련할 필요가 없다. HTTP 방식을 선택할 때만 아래 `modeltest.v1` API를 갖춘 서버가 필요하며 기존 OCR 워커와는 호환되지 않는다.

## SSH로 사용하기

1. RunPod의 CUDA GPU·PyTorch·python3가 준비된 Pod를 사용한다. 노트북에는 OpenSSH 클라이언트와 개인키가 있어야 한다.
2. 처음 접속하는 서버는 터미널에서 RunPod의 접속 명령으로 호스트 키 지문을 확인한다. 앱은 `StrictHostKeyChecking=yes`를 사용하며 호스트 키를 자동 승인하거나 변경된 키를 덮어쓰지 않는다. 암호화된 개인키는 먼저 ssh-agent에 등록한다.
3. `/modeltest`의 `SSH 명령`에 RunPod Connect의 명령을 붙여넣고 `연결 확인`을 누른다.
4. 준비가 필요하면 `Pod 실행환경 준비`를 누른다. Pod의 `~/.cache/creative-checker-modeltest/venv`에 전용 가상환경을 만들고 필요한 패키지를 설치한다. 기존 PyTorch는 재사용하며 글로벌 패키지와 모델은 바꾸지 않는다.
5. 파일과 단계별 모델을 지정한 후 `검수 실행`을 누른다. 파일과 실행 스크립트를 Pod로 전송하고, Pod에서 모델 가중치를 내려받아 GPU로 실행한다.

환경 준비를 다시 누르면 기존 파일을 지우지 않고 가상환경 설정과 pip를 복구한다. `/workspace`가 FUSE 볼륨이면 가상환경 생성과 패키지 설치에 필요한 파일 작업이 거부될 수 있어, 실행환경은 홈의 로컬 캐시 디렉터리에 만든다. 이전 `/workspace/.creative-checker-modeltest/venv`는 삭제하거나 재사용하지 않는다. 검수 자료·결과는 기존 `/workspace/.creative-checker-modeltest/runs`에 보관한다. 컨테이너 디스크가 초기화되면 실행환경 준비를 다시 수행해야 한다.

시스템 PyTorch와 같은 버전·CUDA 사용 가능 여부를 먼저 확인한 뒤 패키지를 설치한다. 가상환경이 깨져 있어도 시스템 CUDA PyTorch가 정상이라면 복구 버튼을 사용할 수 있다. 실패 시 생성·복구, PyTorch·CUDA 확인, 패키지 설치, 실행환경 확인 중 실패한 단계와 실제 오류 출력의 끝부분(최대 6,000자)을 화면에 표시한다. URL 인증정보와 Hugging Face 토큰은 가린다.

두 명령 형식을 받는다. 실제 RunPod Connect에서 복사한 사용자·IP·포트·키 경로로 바꿔 입력한다.

```text
ssh root@203.0.113.10 -p 12345 -i ~/.ssh/id_ed25519
ssh pod-identifier@ssh.runpod.io -i ~/.ssh/id_ed25519
```

첫 번째가 SSH over exposed TCP 방식이다. TCP에서는 1 MiB 청크의 원본 바이트를 직접 전달해 Base64 변환과 전송량 증가를 피한다. 두 번째 기본 SSH는 SCP/SFTP를 지원하지 않으므로, 앱은 터미널의 echo와 canonical 모드를 끄고 청크 단위의 인코딩된 데이터를 전달한다. 두 방식 모두 파일 크기와 SHA-256을 대조한 뒤 추론을 시작한다. 큰 영상에는 TCP 방식을 권장한다. 기본 SSH의 실제 RunPod 게이트웨이 동작과 전송 성능은 실접속 검증이 필요하다.

접수 후 `queued`에는 SSH 연결, 파일 전송, Pod 프로세스 시작 시간이 포함된다. 화면은 이를 `SSH 연결 중`, `Pod에 파일 전송 중`, `Pod 실행 준비 중`으로 구분하고 전송 바이트 수를 표시한다. 실행 시작 후에는 `preparation`의 연결·전송·시작 구간 측정값을 보여준다. 전송 시간은 로컬에서 마지막 바이트를 쓴 시점까지이며 이후 원격 체크섬 확인 등은 시작 구간에 포함된다. 모델 다운로드·로딩은 Pod 실행이 시작된 뒤 해당 단계의 시간에 포함된다. 고정된 40초 대기나 재시도 지연은 없다.

명령은 로컬 셸에 그대로 전달하지 않는다. `ssh`, 사용자@주소, `-p`, `-i`만 해석해서 인수 배열로 실행한다. `ProxyCommand`, 포트 포워딩과 추가 원격 명령은 받지 않는다. 공백이 있는 키 경로는 따옴표로 감싼다. 개인키 본문은 브라우저나 Pod로 전송하지 않는다.

로컬 SSH 경로는 loopback 클라이언트·호스트와 허용 출처, 전용 요청 헤더를 확인한다. v1 프로필에서는 기존 CSRF 검증도 적용한다. 운영 프로필이나 외부 공개 백엔드에서는 SSH 경로를 제공하지 않는다.

### SSH 실행기의 지원 범위

아래 네 모델 ID는 전용 로더와 처리 함수로 연결한다. URL 입력도 같은 ID로 정규화된다. 목록에 있다고 Pod 추론 품질까지 검증된 것은 아니다.

| 모델 | 선택할 단계 | 결과 |
| --- | --- | --- |
| `google/owlv2-base-patch16` | 로고 추출 | `a brand logo` 질의로 탐지한 픽셀 좌표와 점수. 임계값 0.2 |
| `google/siglip2-base-patch16-224` | 시각 요소 추출 | 입력한 후보 문구별 이미지 유사도 `label_scores` |
| `vismatch/xfeat` | 원본 로고와 대조 | 로고별·레퍼런스별 특징점 수, 매칭 수, 기하 검증 통과 수와 대응 좌표 |
| `Qwen/Qwen3.5-4B` | 모든 모델 단계 | 조건·문구·시각 설명·대조 결과 생성. 로고 추출은 좌표 JSON 파싱 |

SigLIP2를 선택하면 후보 문구 입력란이 표시된다. 한 줄에 하나씩 1~32개, 문구당 200자까지 입력한다. API에서는 해당 단계에 `"labels": ["a red background", "a person holding a product"]`를 보낸다. 공백을 제거한 중복은 받지 않는다. 각 logit에 sigmoid를 적용한 독립 점수이며 합계가 1인 분류 확률이나 검수 통과 확률이 아니다. 문구 OCR과 자유로운 설명 생성은 하지 않는다. 조건 대조도 필요하면 후속 시각 요소 대조 단계에 Qwen을 지정한다.

XFeat에는 탐지 후 잘라낸 로고와 원본 레퍼런스를 전달한다. 긴 변 최대 1,024px, 각 변 최소 32px로 전처리하고 최대 1,024개 특징점과 상호 최근접 매칭(코사인 유사도 0.82)을 사용한다. RANSAC 호모그래피 오차 3px, 통과 매칭 8개 이상·비율 0.5 이상일 때 `candidate_match`, 그 외는 `uncertain`이다. 이는 보정되지 않은 참고 기준이다. 단순하거나 작은 로고는 특징점이 부족할 수 있으며 불일치로 단정하지 않는다. 대응 좌표는 잘라낸 로고와 레퍼런스 각각의 원래 크기를 기준으로 최대 64쌍을 반환한다.

XFeat 가중치는 지정한 `vismatch/xfeat` revision의 `xfeat.pt`를 `weights_only=True`로 읽는다. 실행 코드는 [공식 XFeat 구현](https://github.com/verlab/accelerated_features/tree/e92685f57f8318b18725c5c8c0bd28c7fe188d9a)을 해당 커밋으로 고정해 Torch Hub에서 받는다. 최초 실행에는 GitHub와 Hugging Face 접근이 필요하다. 전체 VisMatch 패키지는 설치하지 않는다. OpenCV headless·NumPy·tqdm을 실행환경 준비 패키지에 포함한다.

이 변경을 반영할 때는 기존 원격 작업 상태를 먼저 확인한 뒤 로컬 백엔드를 재시작하고 `/modeltest`에서 다시 연결한다. 누락 패키지가 표시되면 `Pod 실행환경 준비`를 실행한다. 로컬 저장 기능이 적용되기 전의 화면에만 남아 있는 파일·모델 설정은 먼저 기록한다.

- 생성 모델: Transformers의 `AutoModelForImageTextToText`, `AutoModelForCausalLM`에 등록된 모델. 이미지 추출·스캔 PDF·로고 대조에는 이미지 입력을 지원하는 모델을 지정한다. 텍스트 모델은 문서 텍스트 조건 추출과 추출 결과의 조건 대조에 사용할 수 있다.
- 로고 추출: Grounding DINO 계열 또는 정해진 JSON 좌표 형식을 반환하는 이미지 생성 응답 모델. 로고를 실제로 잘라낸 이미지와 원본 로고를 후속 대조 모델에 전달한다. Grounding DINO의 기본 임계값은 box 0.35, text 0.25이며 프로젝트 데이터로 보정된 성능 수치가 아니다.
- 문서: PDF, DOCX, HWPX, UTF-8 TXT·MD. PDF는 문서당 40페이지까지 페이지 이미지로 처리한다. DOCX·HWPX는 본문 텍스트만 읽는다. 이미지·표의 시각 배치가 필요한 문서는 PDF로 변환한다. DOC·HWP 바이너리 형식은 이 실행기에서 받지 않는다.
- 영상: PyAV로 실제 타임스탬프를 읽고 N프레임 또는 N초마다 추출한다. 긴 변 1,920px 이하의 프레임을 사용한다. 동일 장면도 설정한 간격의 표본을 유지한다.
- 모델별 최대 생성 토큰은 호출당 1,536이다. 원문 출력과 단계 상태를 표시하며, 모델이 반환한 판단을 확정된 정답으로 취급하지 않는다. 한 번에 한 모델을 GPU에 올리고 모델 변경 시 이전 모델을 해제한다. 선택한 모델이 VRAM에 맞는지는 별도 확인이 필요하다.
- Transformers 모델은 Safetensors 가중치와 `trust_remote_code=False`를 사용한다. XFeat에 한해 위의 고정된 코드와 텐서 가중치를 사용한다. 모든 Hugging Face 저장소를 지원하지 않으며, 알려진 네 모델의 단계 불일치는 실행 전에 차단한다. 그 외 모델의 로딩 호환성은 실행 시 확인한다. 비공개·접근 제한 모델은 Pod 자체의 Hugging Face 인증이 별도로 필요하다.

`Pod 실행환경 준비`에서 설치하는 패키지는 `backend/app/modeltest_remote.py`의 `PACKAGES`에 정의한다. 실제 모델 설치·추론을 로컬 노트북에서 수행하지 않는다. 원격 작업 파일은 `/workspace/.creative-checker-modeltest/runs/<요청 ID>`에 남는다(`/workspace`가 없으면 원격 홈 아래). 자동 삭제하지 않는다.

SSH 접속 정보는 로컬 서버 메모리에 유지한다. 같은 요청 ID와 파일은 새 추론을 만들지 않는다. 연결이 끊기면 원격 결과 파일을 다시 조회한다. **새로고침·조회 일시정지·브라우저 닫기는 작업 취소가 아니다.** 새 실행은 독립된 프로세스 세션에서 계속 진행하며 결과를 Pod에 기록한다. 화면은 저장한 실행 ID로 조회를 복원하고 자동 재접수하지 않는다. 백엔드를 재시작했다면 같은 Pod에 다시 연결한 후 `이전 작업 조회`를 누른다.

`검수 강제 중단`은 자료 전송이 끝나 Pod 실행이 시작된 뒤 사용할 수 있다. 확인 후 해당 작업의 기록된 PID, 프로세스 시작 시각, runner·job 경로를 대조하고 전용 프로세스 그룹에 SIGTERM을 보낸다. 2초 뒤에도 실행 중이면 SIGKILL을 보낸다. 이전 버전의 세션 분리가 없는 작업은 해당 PID만 대상으로 한다. PID가 재사용됐거나 다른 프로세스라면 신호를 보내지 않는다. 완료된 단계와 파일은 유지한다. Pod 자체와 다른 작업은 중단하지 않는다. SSH 연결이 끊겼으면 중단을 보장할 수 없어 상태를 다시 조회해야 한다.

### 결과 화면과 브라우저 저장

- 상단의 라이트·다크 버튼으로 모델 테스트 화면의 테마를 전환한다. 같은 브라우저·출처의 localStorage에 선택을 저장하며, 저장소를 사용할 수 없어도 현재 화면의 테마 전환은 가능하다.
- 단계별 상태와 결과 상세에 소요 시간을 표시하고, `소요 시간` 탭에서 단계별 시간과 모델·리비전별 합계를 확인한다. 모델 로딩에는 다운로드·모델 전환, 처리에는 전처리·추론·결과 변환이 포함된다. 선택적 SigLIP2 이미지 유사도 계산도 별도 모델로 집계한다. 단계 시간에는 파일 처리·저장·전송이 추가되므로 모델 시간 합계와 다를 수 있다.
- SSH 실행기는 단계의 `started_at`(Unix 초), `elapsed_seconds`, `model_timings`를 반환한다. 각 모델 기록은 `model_id`, `revision`, `load_seconds`, `inference_seconds`, `calls`를 갖는다. 동일 모델·리비전의 여러 단계 기록은 화면에서 합산한다. 실패한 호출도 시도한 시간과 호출 수에 포함한다. 실행 중인 모델은 `active_phase`와 `active_started_at`으로 경과 시간을 표시하며, 완료값은 Pod의 monotonic 시계로 확정한다. 실행 중 표시와 강제중단 시 `timing_estimated` 값은 추정치다. 비정상 종료 시에는 마지막 기록 시점까지만 보존한다.
- 과거 결과나 시간 데이터를 제공하지 않는 HTTP API 응답은 `미측정`으로 표시하며 기존 결과를 읽는 데 영향을 주지 않는다. 새 계측은 로컬 백엔드를 재시작한 뒤 새로 접수한 SSH 실행부터 적용된다. 진행 중이거나 완료된 원격 스크립트에는 소급 적용되지 않는다.
- 모델 선택 상자와 직접 입력란을 함께 제공한다. 등록 모델 선택은 입력란에 반영되고 모델 ID·HF 링크 직접 입력도 유지된다. RunPod 연결 영역은 연결 여부와 관계없이 접고 펼칠 수 있다.
- 조건 직접 입력의 펼침 상태는 내용과 별도로 저장한다. 텍스트를 모두 지워도 열린 상태를 유지한다.
- 첨부 원본 File, 단계별 설정, 조건, 프레임 설정, 선택한 검수 유형을 같은 브라우저·출처의 IndexedDB에 저장한다. 큰 파일은 첨부가 바뀔 때만 다시 저장한다. 저장 중·완료·실패 상태를 표시하고 `저장된 첨부·설정 지우기`로 초기화한다. 브라우저 저장소 삭제·용량 제한·시크릿 모드에서는 복원이 제한될 수 있다. localhost와 127.0.0.1, 서로 다른 포트는 별도 출처다.
- 마지막 작업의 실행 ID·연결 식별자·실행 설정도 저장해 조회를 복원한다. SSH 명령·개인키 경로·HTTP 인증 토큰은 저장하지 않는다. HTTP 인증이 필요한 작업은 다시 연결한 후 조회한다.
- 단계별 결과에서 프레임 번호(0부터 시작)·초 단위를 선택하면 분석한 프레임과 로고 좌표를 표시한다. 좌표는 리사이즈된 분석 프레임의 픽셀 기준이며 원본 영상 해상도와 다를 수 있다. 선택한 로고의 크롭과 원본 레퍼런스를 나란히 보여준다. 탭·단계를 오갈 때 단계별 프레임·로고·레퍼런스 선택을 유지한다. 이 화면 선택은 현재 페이지 메모리에 보관한다. 서버가 생성한 미리보기만 요청하며, 예전 결과에 미리보기 정보가 없으면 안내를 표시한다.
- XFeat는 특징점 수, 매칭 수, 기하 검증 통과 수·비율, 판정과 매칭 특징점의 평균 코사인 유사도를 보여준다. `SigLIP2 이미지 코사인 유사도도 계산`을 선택하면 `logo_compare` 설정에 `image_similarity: true`를 보낸다. XFeat 처리 후 SigLIP2를 한 번 로드해 크롭·원본의 이미지 임베딩 코사인을 추가한다. 두 코사인 값은 계산 대상이 다르며 일치 확률이 아니다. 별도 검증 없이 확실·불일치 임계값으로 변환하지 않는다.
- 문서 조건·OCR·시각 설명은 읽기 쉬운 텍스트, SigLIP2 후보별 점수는 문구와 막대로 제공한다. 전체 JSON 보기와 내려받기도 유지한다.

## 화면에서 설정하는 항목

- 이미지: 포스터 이미지 1개와 문서 1개 이상 필수. 조건 추출, 문구 추출, 시각 요소 추출, 문구 대조, 시각 요소 대조의 5개 모델 단계.
- 영상: 영상 1개 필수. 문서와 원본 로고 이미지 각각 최대 10개 선택. 조건 추출, 프레임 추출, 로고·문구·시각 요소 추출, 각각의 대조로 총 8단계. 모델은 7개 단계에 지정한다.
- 영상만 첨부한 초기 상태에서는 로고·문구·시각 요소 추출을 선택한다. 대조를 켜면 선행 추출을 요구한다. 문구·시각 대조에는 문서 조건 추출 또는 직접 입력한 조건이 필요하고, 로고 대조에는 레퍼런스가 필요하다.
- 프레임 추출: N프레임마다 또는 N초마다, 최대 1,000장. 프레임 간격은 정수, 시간 간격은 양의 실수. 최대 수는 영상 앞부분에서 추출을 멈추는 상한이며 영상 전체를 균등 분할하는 옵션은 아니다.
- 모델: `Qwen/Qwen3.5-4B`, `https://huggingface.co/Qwen/Qwen3.5-4B`, `/tree/revision` 링크 허용. `Qwen3.5-4B`처럼 소유자 없는 값은 거부한다. 형식 검사와 실제 모델 존재·작업 호환성 검사는 별개다. 모델 예시는 추천 또는 호환성 검증 결과가 아니다.
- 문서 형식: PDF, DOCX, DOC, HWP, HWPX, TXT, MD. 이미지: JPG, JPEG, PNG, WEBP, BMP, TIF, TIFF. 영상: MP4, MOV, WEBM, MKV, AVI, M4V. 실제 첨부 허용 형식은 이 목록과 Pod의 `formats` 응답의 교집합이다. 브라우저 미리보기 지원과 서버 디코딩 지원은 다르다.

## HTTP 방식의 연결과 인증

주소는 `https://<pod-id>-<port>.proxy.runpod.net` 또는 개발용 `http://localhost:<port>` / `http://127.0.0.1:<port>`로 입력한다. 경로, 쿼리, URL 내 인증정보는 받지 않는다. HTTPS 화면에서 로컬 HTTP 접근은 브라우저의 혼합 콘텐츠·로컬 네트워크 정책에도 영향을 받는다.

선택적인 토큰은 **Pod API가 검증하는 토큰**이며 RunPod 계정 관리 API 키가 아니다. 요청의 `Authorization: Bearer ...`로만 사용하고 토큰은 메모리에만 보관한다. 기존 서비스 쿠키는 전송하지 않는다. 파일과 설정은 위의 IndexedDB 정책에 따라 복원하며 토큰은 다시 입력해야 한다. Pod에서 실행 중인 작업은 계속될 수 있다.

Pod는 프런트엔드의 실제 출처(예: `http://127.0.0.1:5173`, `http://localhost:5173`)에 대한 CORS와 OPTIONS를 처리해야 한다. GET·POST, `Authorization`, `Idempotency-Key`, `Content-Type`을 허용하고 오류 응답에도 CORS 헤더를 붙인다. v1의 외부 연결 CSP는 `/modeltest` 문서 응답에만 적용한다. 다른 페이지에서 이동할 때는 전체 페이지 탐색을 사용해야 해당 CSP가 적용된다.

## GET /api/modeltest/health

네트워크 연결만으로 실행을 허용하지 않는다. `contract`, `ready`, 지원 단계, 형식, 업로드 제한을 확인한다. 모델 목록은 선택 사항이며 단계별 입력의 선택 목록에 표시된다. 예시의 형식·한도는 서버가 실제로 지원하는 값으로 변경한다. 지원하지 않는 종류의 형식은 빈 배열로, 한도는 0으로 반환할 수 있다.

```json
{
  "contract": "modeltest.v1",
  "ready": true,
  "cancel": true,
  "stages": ["conditions", "logo_extract", "text_extract", "visual_extract", "logo_compare", "text_compare", "visual_compare"],
  "models": [{"id": "owner/model", "stages": ["text_extract"]}],
  "formats": {
    "image": [".jpg", ".jpeg", ".png", ".webp"],
    "video": [".mp4", ".mov", ".webm"],
    "document": [".pdf", ".txt"],
    "reference": [".jpg", ".jpeg", ".png", ".webp"]
  },
  "limits": {
    "image_bytes": 20971520,
    "video_bytes": 524288000,
    "document_bytes": 20971520,
    "reference_bytes": 20971520,
    "total_bytes": 629145600
  }
}
```

클라이언트 자체 상한은 이미지·문서·레퍼런스 각 20 MiB, 영상 500 MiB이며 서버가 더 작은 상한을 선언하면 그 값을 적용한다. 프록시 업로드 제한을 고려해 서버의 실제 `limits`를 설정해야 한다. 모델 준비가 완료되지 않았으면 `ready: false`를 반환한다.

## GET /api/modeltest/runs/:id/previews/:name

결과의 `preview` 파일명을 받아 `{"mime":"image/jpeg","data":"<base64>"}`를 반환한다. SSH에서는 로컬 연결 경로 아래 같은 하위 경로를 사용한다. 요청한 작업이 속한 Pod에서 생성된 프레임·로고 크롭·포스터·레퍼런스 미리보기만 읽으며 파일명 허용 목록과 경로를 검사한다. 긴 변 1,280px, JPEG 최대 1.5 MiB다. 새 검수는 기존 SSH 결과 스트림으로 미리보기도 전달한다(작업당 Base64 기준 최대 32 MiB). 로컬 백엔드의 최근 미리보기 캐시는 최대 64 MiB·1,024개로 제한한다. 캐시에 있으면 이미지마다 새 SSH 연결·Python 실행 없이 응답한다. 예전 결과, 캐시에서 제거된 이미지, 백엔드 재시작 후에는 최초 조회에 SSH가 필요하며 그 결과를 캐시한다.

화면은 선택한 프레임·크롭·레퍼런스만 가져오고 작업별 최근 32장을 메모리에 보관한다. 탭·단계 이동에도 같은 작업의 캐시를 재사용한다. HTTP 사용자 정의 API에서도 이 경로를 제공해야 이미지 결과를 표시할 수 있다.

결과 객체의 `references`에는 `{index, preview, width, height}`, 이미지 검수의 `media`에는 같은 메타데이터를 둔다. `frames.output.frames`와 각 단계의 `output.items`에는 `index`, `timestamp_seconds`, `preview`, `width`, `height`를 포함한다. 로고 목록에는 `logo_index`, `bbox`, `preview`를, 대조 결과에는 `logo_index`, `reference_index`를 포함해 올바른 크롭·원본 쌍에 연결한다. 기존 JSON 출력에는 미리보기가 없을 수 있다.

## POST /api/modeltest/runs

`multipart/form-data`로 다음 필드를 받는다. 모델 추론이 끝날 때까지 업로드 요청을 유지하지 않고, 접수 후 202와 실행 객체를 반환한다.

| 필드 | 내용 |
| --- | --- |
| `config` | 아래 JSON 문자열 |
| `media` | 이미지 또는 영상 원본 1개 |
| `documents` | 반복되는 문서 파일 필드 |
| `references` | 반복되는 원본 로고·레퍼런스 이미지 필드 |

```json
{
  "contract": "modeltest.v1",
  "flow": "video",
  "requirements": "행사명을 표시해야 합니다.",
  "stages": [
    {"id": "text_extract", "model_id": "owner/ocr-model", "revision": "main"},
    {"id": "text_compare", "model_id": "owner/judge-model"}
  ],
  "sampling": {"unit": "frames", "interval": 30, "max_frames": 120},
  "files": {
    "media": {"name": "sample.mp4", "size": 12345, "type": "video/mp4"},
    "documents": [],
    "references": []
  }
}
```

이미지 요청에는 `sampling`이 없다. 선택한 모델 단계만 `stages`에 포함된다. `conditions` 출력과 직접 입력 `requirements`를 모두 후속 대조에 전달한다. 파일 메타데이터는 참고용이며 서버에서 실제 바이트·형식·크기·모델·의존 관계를 재검증한다. 모델 로딩은 서버 정책에 따르며 입력된 저장소 코드를 자동으로 신뢰하거나 실행하지 않는다.

요청마다 `Idempotency-Key` UUID 헤더를 보낸다. **서버는 같은 키와 같은 요청을 다시 받아도 새 추론을 만들지 않고 기존 실행을 반환해야 한다.** 같은 키와 다른 요청은 409로 거부한다. 연결 단절·시간 초과·5xx 때 클라이언트는 자동 재제출하지 않으며, 사용자가 `같은 요청으로 접수 확인`을 누르면 동일한 키와 파일을 재전송한다. 이 동작을 지원하지 않는 서버는 이 계약과 호환되지 않는다.

## GET /api/modeltest/runs/{id}

접수·조회·선택적인 취소 응답은 모두 다음 실행 객체를 반환한다. ID는 영숫자·밑줄·하이픈으로 1~128자다.

```json
{
  "contract": "modeltest.v1",
  "id": "run-123",
  "status": "running",
  "stages": [
    {"id": "frames", "status": "completed", "output": {"frames": [{"index": 0, "timestamp_seconds": 0}, {"index": 30, "timestamp_seconds": 1.001}]}},
    {"id": "text_extract", "status": "running", "model_id": "owner/ocr-model"},
    {"id": "text_compare", "status": "queued", "model_id": "owner/judge-model"}
  ]
}
```

- 실행 상태: `queued`, `running`, `completed`, `failed`, `cancelled`.
- 단계 상태: 위 상태와 `skipped`. 요청에 포함된 모든 단계는 첫 응답부터 들어 있어야 하며 영상에는 모델을 지정하지 않는 `frames` 단계도 있어야 한다. 응답 배열 순서는 실행 순서다.
- 실행이 `completed`면 모든 요청 단계도 `completed`여야 한다. 불완전한 응답을 완료로 표시하지 않는다. 비교 결과의 불일치는 실행 실패와 별개로 `output`에 판정·근거를 담는다.
- 단계별 `output`은 자유 형식 JSON이다. 반환된 내용과 전체 응답을 화면에서 보고 다운로드한다. 실패 설명은 `error` 문자열로 반환한다. 토큰·서버 내부 경로는 포함하지 않는다.
- `output.frames[].timestamp_seconds`가 있으면 해당 시점으로 첨부 영상을 이동할 수 있다. 원본 영상과 결과가 대응할 때만 이동 버튼을 표시한다. VFR 영상은 실제 타임스탬프를 반환한다.
- 조회는 2초 간격이며 네트워크 오류 시 일시정지한다. 조회 재개는 같은 작업 ID를 사용한다. 자동 재추론은 없다.

## POST /api/modeltest/runs/{id}/cancel

선택 기능. health의 `cancel: true`일 때만 버튼을 표시한다. 서버는 작업 취소를 처리하고 동일한 실행 객체를 반환한다. 아직 취소가 반영되지 않았다면 실제 상태를 반환하고 조회를 통해 최종 `cancelled`를 확인한다. 조회 일시정지와 작업 취소는 별개다.

## 범위가 제한된 오프라인 검사

프런트엔드 입력·요청 규격: `cd frontend` 후 `node --test src/pages/modeltest/modeltest.test.js`.
SSH 명령·전송·단계 연결: 저장소 루트에서 `python -m pytest backend/tests/test_modeltest_ssh.py`. 실제 SSH 대신 로컬 자식 프로세스의 입출력과 가짜 모델을 사용한다.
실제 Pod 연결·패키지 설치·모델 다운로드·추론·브라우저 렌더링은 이 검사에 포함되지 않는다.

참고: [RunPod SSH 방식](https://docs.runpod.io/pods/configuration/use-ssh), [RunPod HTTP 서비스 노출](https://www.runpod.io/articles/guides/deploy-fastapi-applications-gpu-cloud), [Hugging Face 저장소 ID 규칙](https://huggingface.co/docs/huggingface_hub/en/package_reference/utilities#huggingface_hub.utils.validate_repo_id), [멀티모달 모델 입력](https://huggingface.co/docs/transformers/chat_templating_multimodal).
