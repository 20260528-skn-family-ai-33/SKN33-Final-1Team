# 영상 로고 확정·정밀 대조 API

영상 속 원본 로고의 위치를 사용자가 확정하고, 확정한 묶음을 원본과 정밀 대조해 **근거**를 돌려준다.
묶음에 대한 일치·다름 판정은 내지 않는다. 최종 판단은 사람이 한다(2026-11-01 결정).

- 코드: `backend/app/video/logos/routes.py`(API), `video/logos/candidates.py`(1부 후보), `video/logos/compare.py`(2부 대조), `video/logos/models.py`(모델)
- 실행 위치: 로컬 백엔드는 업로드·프레임 추출·저장·화면 데이터를 맡고, 모델이 필요한 두 단계(1부 후보 찾기, 2부 정밀 대조)는
  RunPod Pod의 HTTP 워커(`backend/app/video/logos/worker.py`)에 맡긴다. 의존성(Pod)은 `requirements-logo.txt`, `requirements-paddle.txt`
  - `VIDEO_LOGO_WORKER_URL`(로컬 서버 환경변수)이 있으면 워커로 보내고, 없으면 그 프로세스에서 직접 추론한다(백엔드를 Pod에서 띄운 경우)
  - `VIDEO_LOGO_WORKER_TOKEN`(선택): 워커와 로컬에 같은 값을 두면 `X-Worker-Token` 헤더로 확인한다
  - `VIDEO_LOGO_WORKER_TIMEOUT_SECONDS`(선택, 기본 7200): 작업 하나의 최대 대기 시간

### RunPod 워커 연결

1. Pod(저장소 폴더, 모델 venv)에서 워커를 띄운다. 127.0.0.1에만 연다
   `uvicorn backend.app.video_logo_worker:app --host 127.0.0.1 --port 8791`
   OCR 모델 파일은 Pod에서 `python scripts/prepare_paddle.py`로 `.runtime/paddle-models`에 준비한다
2. 로컬 PC에서 SSH 터널을 연다(Pod 주소·포트는 재시작하면 바뀐다)
   `ssh -i ~/.ssh/skn33_final -p <포트> -N -L 8791:127.0.0.1:8791 root@<Pod 주소>`
3. 로컬 백엔드를 `VIDEO_LOGO_WORKER_URL=http://127.0.0.1:8791`로 띄운다. `GET /api/video-logos/status`가 워커의 준비 상태를 돌려준다

같은 워커가 **동영상 검수의 자막 OCR**도 맡는다. `VIDEO_LOGO_WORKER_URL`이 있으면 `/api/video-reviews`의 프레임 OCR을 워커로 보내고
(같은 모델·설정, 결과 형식 동일, 모델 식별자 확인), 없으면 로컬 Paddle 환경(CPU)에서 읽는다. 로컬 CPU는 프레임당 약 20초라 1~2분 영상도 40분 이상 걸린다.
자막 OCR은 Pod에서 torch와 CUDA 라이브러리가 겹치지 않게 따로 만든 GPU Paddle 환경으로 돈다:
`python3 -m venv /root/paddlegpu` → `pip install paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/` → `pip install paddleocr==3.7.0 paddlex==3.7.2`,
워커를 `VIDEO_OCR_PYTHON=/root/paddlegpu/bin/python`(기본 `VIDEO_OCR_DEVICE=gpu`)으로 띄운다.
로고 후보 OCR과 정밀 대조 OCR도 같은 환경을 쓴다. `VIDEO_OCR_PYTHON`이 있으면 워커가 그 환경에 상주 OCR 프로세스(`backend.app.paddle_ocr_server`)를
한 번 띄워 두고 crop을 한 장씩 보낸다(모델 해시 확인, 설정은 자막 검사와 같음, 죽으면 다음 호출에서 다시 띄움). 없으면 워커 안에서 CPU로 읽는다.

워커 API(로컬 백엔드만 부른다): `GET /health`, `POST /jobs`(`kind`: `candidates` 프레임+원본 로고 / `compare` 원본 로고+crop들 / `ocr_frames` 자막 프레임, 이미지는 base64),
`GET /jobs/<id>`(`status`, `progress`, `detail`, `result`). 한 번에 한 작업씩 돌고, 끝난 결과는 1시간 뒤 지운다.
제출은 다시 보내지 않고, 상태 조회만 터널이 잠깐 끊겨도 다시 시도한다. 워커가 재시작되면 진행 중이던 작업은 실패로 끝난다
- 접속 경계: 동영상 검수(`/api/video-reviews`)와 같다. localhost 접속만 받고, 로그인 기능이 켜져 있으면 세션도 확인한다
- 저장 위치: `.runtime/video-logos/<id>/`
- OCR 모델: 자막 검사와 같은 모델을 쓴다. 탐지 PP-OCRv5_server_det + 한국어 인식 korean_PP-OCRv5_mobile_rec,
  모델 파일 `.runtime/paddle-models`(해시 확인, `scripts/prepare_paddle.py`로 준비), 설정은 `paddle_video_worker.build_model`을 함께 쓴다.
  읽는 방식은 실험 그대로: 후보 상자·영상 crop·원본 로고를 잘라(crop은 짧은 변 192px 이상으로 키워) 따로 읽는다

## 흐름

1. `POST /api/video-logos`(multipart): `video`, `logos`(1~12장), `logo_texts`(선택, 원본 로고마다 기준 문구 JSON 배열. 줄은 `|`로 구분), `title`,
   `review_id`(선택, 같은 영상으로 함께 접수한 동영상 검수 작업 id. 화면에서 묶어 보여 주는 데만 쓴다)
   → `{"id", "status": "queued"}`. 백그라운드에서 1부 후보 찾기를 한다
2. `GET /api/video-logos/<id>`: `status`가 `candidates_ready`가 될 때까지 확인한다(`stage`, `progress`, `stage_detail`)
   - 프레임 누락: 읽지 못한 초가 있으면 `missing_seconds`(초 목록)와 `warnings`(안내 문구)를 함께 준다.
     `data.json`에도 `missing`(초 목록)이 들어가고, `seconds`는 영상 전체 초 수다. 확정 화면은 누락 안내와 타임라인 표시(빗금)를 보여 준다
3. 확정 화면은 작업 하나를 세트 하나처럼 읽는다. base = `/api/video-logos/<id>`
   - `GET {base}/index.json`, `GET {base}/video/data.json`, `GET {base}/video/f/<sec>.jpg`, `GET {base}/refs/<i>.png`
4. `POST /api/video-logos/<id>/compare`(JSON): 확정 화면이 내려받는 정답 값의 배열
   `[{"logo": <원본 로고 번호(업로드 순서, 0부터)>, "reference": "<원본 로고 이름>", "decision": "done", "crops": [{"source": "candidate"|"manual", "group", "sec", "bbox", "intervals"}]}]`
   원본 로고는 `logo` 번호로 찾는다(파일 이름이 같은 로고도 구분). 다시 실행하면 이전 결과(compare.json)를 지우고 새로 만든다
   `decision`이 `done`인 것만 대조한다. bbox는 data.json의 `size` 기준 좌표다
5. `GET /api/video-logos/<id>/compare`: `compare_status`, `compare_progress`, `bundles`, `warnings`
   - 초별 결과(`per_sec`)에 그 초에 읽은 OCR 원문(`ocr`)이 들어간다
   - 서버가 대조 중에 재시작되면 `compare_status`는 `failed`가 된다(다시 실행)
   - 묶음마다 `skipped`: 대조하지 못한 초와 이유(`frame_missing` 프레임 없음, `crop_too_small` 자른 영역 4px 미만)
   - `warnings`: 프레임이 없어 빠진 초, 영역이 너무 작아 빠진 초의 수를 사용자에게 알리는 문구
   - 대표 프레임 crop: `GET /api/video-logos/<id>/compare/rep/<logo>/<crop>.jpg`

## 화면 (프런트)

- 동영상 검수(`/video`) 업로드 카드의 "원본 로고"에 이미지(최대 12장)와 로고마다 기준 문구(선택)를 넣으면,
  동영상 검수를 접수한 뒤 같은 영상으로 로고 작업을 만든다(`review_id` 연결). 로고 접수가 실패해도 동영상 검수는 진행한다
- 동영상 검수 결과 화면의 "영상 로고 검수" 카드: 후보 찾기 진행률, 프레임 누락 안내, "로고 위치 확정하기", 정밀 대조가 끝나면 "정밀 대조 근거 보기"
  (`GET /api/video-logos?review_id=<id>`로 읽는다)
- 확정 화면(`/video/logo?job=<id>`, 로그인 필요): 실험·검증·테스트 때 쓰던 확정 화면 그대로
  (재생하며 박스 고르기, 순위 상위 3개, 직접 지정 여러 영역·이어서 지정, 연결 후보, 노출 타임라인).
  원본 로고마다 "확정 저장"(작업별로 브라우저에 임시 저장) → "정밀 대조 실행"(확정한 원본 로고만 보낸다)
- 정밀 대조 근거(`/video/logo?job=<id>&view=compare`): 묶음마다 원본·대표 프레임, 결과별 초 수, 초별 결과 띠(대조 못한 초는 빗금),
  자주 나온 근거, 결과가 바뀌는 초, 초별 표. 판정은 표시하지 않는다

## 1부: 후보 찾기 (실험 영상 13개에서 고정한 설정)

1초 간격 프레임 → OWLv2(프롬프트 6개, 전체 화면 + 타일 10개, NMS 0.5) → 점수 0.1 이상·화면 40% 이하
→ 같은 초 겹침 묶기 → 시간 묶음(3초 이내, 위치·크기·모양 연속) → 원본마다 묶음 점수(초별 최고 SigLIP2 유사도 상위 3개 평균)
→ 상위 40개 OCR, 기준 문구를 80% 이상 읽으면 +0.10 = 순위 점수(`rank`).
기준 문구가 비어 있으면 원본 로고 OCR(점수 0.5 이상 줄)을 대신 쓴다. 쓴 문구와 출처는 `refs[].bonus_text`, `refs[].bonus_basis`(`input`/`ocr`/`null`).

## 2부: 정밀 대조

묶음에 속한 모든 초(후보 묶음은 그 묶음의 초, 직접 지정은 정한 구간)마다 확정한 상자로 자른다(후보 상자는 8% 넓힘).

1. 배경 분리 M3c: 테두리 색 거리 + Otsu 마스크를 BiRefNet 범위 안으로 제한
2. 원본을 요소로 나눔: 기준 문구와 맞는 OCR 줄 = 주 문구·보조 문구, 나머지 전경 = 심볼(글자가 곧 그림이면 로고 전체)
3. 요소 판정: 빠짐(글자·심볼), 원본에 없는 문구·요소(추가), 위치 다름·크기 비율 다름·외곽 비율 다름(배치, 전경 상자 가로세로 비율 |log| 0.3 초과), 심볼 모양 다름(가림·변형).
   보조 문구 높이 8px 미만, 심볼 높이 20px 미만, 전경은 있는데 못 읽은 글자는 `비교 불가`
   기준 문구가 비어 있으면 글자 요소 없이 로고 전체로 보되, 원본을 영상 전경 높이로 줄여 같은 크기에서 읽은 OCR 줄과 비교해
   원본에만 있는 줄은 빠짐, 영상에만 있는 줄은 추가로 낸다(근거에 "같은 크기 원본 OCR 기준" 표시). 줄인 원본의 OCR 잡음은
   원본 원래 크기 OCR에도 나오는 줄만 남겨 거른다. 기준 문구를 넣으면 지금처럼 기준 문구로 비교한다
4. 초마다 `outcome`: `match` / `diff`(근거 있음) / `unknown`(비교 불가 있음)
5. 색(`color`): 유채색끼리만 비교(흰색·검은색 단색은 `mono`). `hue`(색상 차이 도) 22 이상이거나 `lostc`(빠진 유채색) 0.15 이상이면
   '색' 근거로 `outcome`에 넣는다(2026-11-03, 심볼 안쪽 무늬 빠짐 대응. 경계값은 적은 사례로 정해 보정 필요). `newc`는 판정에 쓰지 않는다

묶음 `evidence`: `n_secs`, `outcomes`(결과별 초 수), `findings`(근거와 나온 초 수), `representative`(가장 크고 선명한 초), `changes`(결과가 바뀌는 초).

## 알려진 한계 (검증 영상·테스트 영상에서 확인)

- 정상 로고를 다름으로 잡는 경우가 많다(검증 영상 정상 44개 중 대표 프레임 일치 16개). 원인: OCR이 심볼·작은 글자를 읽은 "원본에 없는 문구",
  심볼 모양 점수(0.6 기준에 아슬아슬), 박스 테두리에 걸친 배경 덩어리, 화면 전환·흐림 프레임
- 배경 분리가 실패하면(어두운 배경 위 짙은 색 로고, 어두워지는 장면) 그 초의 근거가 틀린다. 분리 신뢰도 게이트는 아직 없다
- 실험(검증 영상 결과 포함)은 HF에서 받은 같은 이름의 모델을 기본 설정으로 돌렸다. 자막 검사 설정(탐지 긴 변 1280px 제한, 탐지 임계값 0.3·0.6)으로
  바꿨으므로 읽는 결과가 실험과 다를 수 있다
- 색은 `outcome`에 들어가지 않는다. 색만 바뀐 로고는 근거의 `color.hue`로 확인한다
