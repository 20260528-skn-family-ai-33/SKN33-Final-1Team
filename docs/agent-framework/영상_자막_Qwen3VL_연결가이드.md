# 영상 자막 검수 — Qwen3-VL-8B-Instruct 연결 가이드

2026-11-09 · 상태: **코드 연결 완료, 실제 서버 연결·실영상 검수는 미확인**

## 무엇이 바뀌었나

영상 자막 검수의 글자 판독을 PaddleOCR에서 **Qwen3-VL-8B-Instruct**(vLLM, OpenAI 호환 API)로 바꿨다.
로고 이미지 검수(`backend/app/video/logos/`, `backend/app/logos/`, `/api/video-logos`, 검수 중 6-B 지정 로고 대조)는 **변경하지 않았다.**

| 단계 | 기존(paddleocr) | 현재 기본(qwen3-vl) |
|---|---|---|
| 3 판독 | Paddle OCR(줄 bbox·신뢰도) | Qwen3-VL이 프레임 1장씩 읽음(문구·역할·화면 구역) |
| 3-1 줄 분류 | bbox 특징 로지스틱 회귀/규칙 | 모델이 준 역할 사용. 촬영된 글자(background)만 자막 후보에서 제외 |
| 4 트랙 | 같은 위치(IoU≥0.5)+같은 문구 | 같은 화면 구역+같은 문구(위치 bbox 없음) |
| 6 판정 | 엔진 `paddleocr` 기준(0.90/0.97) | 엔진 `qwen3-vl-video`, 신뢰도=트랙 안 다수결 비율, 기준 0.60/0.85 |

- 프롬프트·긴 변 1280px·temperature 0·max_tokens 2048은 모델 비교 때와 같은 조건이다(`backend/app/video/subtitle_vlm.py`).
- 형식 오류·일시 오류는 프레임당 한 번 더 읽고, 그래도 실패하면 그 프레임만 `failed`로 남긴다(전체 성공률 90% 미만이면 판독 단계 실패).
- Qwen3-VL이 실패해도 Paddle로 몰래 바꾸지 않는다. Paddle 경로는 `VIDEO_SUBTITLE_ENGINE=paddleocr`로 명시할 때만 쓴다.
- 근거 위치의 `bbox_xyxy`는 화면 구역(상단·가운데·하단 등)의 대략적인 사각형이다. 글자 테두리가 아니다.
- 띄어쓰기만 다른 문구는 기존 `judge.py` 규칙대로 '불일치 → 사람 확인'으로 남는다(모델들이 마침표·원 숫자 뒤 공백을 지우는 경향이 있음).

## 환경 변수 (`.env`)

```
VIDEO_SUBTITLE_ENGINE=qwen3-vl
VIDEO_SUBTITLE_VLM_URL=http://127.0.0.1:18000/v1
VIDEO_SUBTITLE_VLM_API_KEY=<vLLM --api-key 값>
VIDEO_SUBTITLE_VLM_MODEL=Qwen/Qwen3-VL-8B-Instruct
VIDEO_SUBTITLE_VLM_CONCURRENCY=8
```

영상 API(`scripts/framework_video.py`)의 legacy 프로필은 `VIDEO_` 접두 값을 그대로 넘긴다. 실제 키는 `.env`에만 두고 Git에 올리지 않는다.

## RunPod에서 서버 띄우기 (Linux)

```bash
/root/vllmenv/bin/vllm serve <Qwen3-VL-8B-Instruct 경로> \
  --served-model-name Qwen/Qwen3-VL-8B-Instruct \
  --host 127.0.0.1 --port 8000 --api-key "$(cat /root/vllm.key)" \
  --gpu-memory-utilization 0.5 --max-model-len 16384 --max-num-seqs 32 \
  --limit-mm-per-prompt '{"image":1,"video":0}'
```

- 같은 GPU에 영상 로고 워커·Paddle 서버가 함께 떠 있으므로 `--gpu-memory-utilization`은 남은 메모리에 맞춘다.
- 로컬 PC에서는 SSH 터널로 연결한다: `ssh -N -L 18000:127.0.0.1:8000 -p <포트> root@<RunPod IP> -i <키>`

### AWS 영상 API 전환

새 영상 이미지에 `backend.app.video.subtitle_vlm` 모듈이 있는지 확인한다. 영상 API EC2에서 `127.0.0.1:18000`을 자막용 vLLM의 loopback 포트로 연결하고, [AWS 영상 환경 설정 예시](../../deploy/agent-framework/video.aws-test.env.example)의 값을 사용한다. vLLM 인증 키는 기존 비공개 런타임 설정에 추가한다.

전환 전에 EC2에서 인증된 `/v1/models`가 설정한 모델 이름을 반환하는지 확인한다. 기존 영상 워커 터널 `18791`·`18792`는 영상 로고 처리에 사용한다. 새 API 기동 후 아래 상태와 실영상 결과를 확인한다. 오인식이 보고된 영상은 새 작업으로 제출해 `0:21–0:22` 구간을 다시 확인한다.

## 확인 방법

1. `GET /api/video-reviews/status` → `subtitle_engine: "qwen3-vl"`, `pipeline: "qwen3vl_frames_v1"`, `ready: true`
2. 영상 1편 검수 후 결과의 `runtime.engine == "qwen3-vl"`, 작업 폴더의 `ocr/vlm.raw.jsonl`(모델 원문 응답)
3. 준비 실패 시 `detail`에 연결 설정·서버·모델 이름 중 무엇이 문제인지 나온다(키·주소는 표시하지 않음).

## 계약서 → 영상 기준 초안 (영상 A02)

`POST /api/video-reviews/requirements/extract` (multipart `document`, 로그인·CSRF 필요, 동시에 1건)

- 문서 읽기 모델: 포스터 A02와 **같은** 공용 Qwen3.5-4B. `.env`의 `CC_QWEN_BASE_URL`(…/v1), `CC_QWEN_MODEL`, 키 `A02_QWEN_API_KEY`(없으면 `CC_QWEN_API_KEY`). 위 Qwen3-VL 서버와 다른 서버다.
- 입력: DOCX(본문 순서대로 문단 묶음·표를 블록으로, 표 한 행 = `셀 | 셀` 한 줄), 텍스트층 PDF(이미지 섞인 쪽 거부, OCR 없음), UTF-8 `.txt`. 10MiB·32,000자·블록 50개 이하.
- 방식: 포스터 A02와 같다. 모델은 원문 줄 ID 범위만 고르고 서버가 원문을 복사한다. 기대값·항목 ID(L01·R01·T01…)가 고른 원문에 없으면 전체를 거부한다. 요청 1회·temperature 0·재시도 없음.
- 대상 분류: `screen_text`(화면 문구) · `logo` · `subtitle_quality` → 영상 기준 초안 / `video_spec`(해상도·코덱·fps·길이·음성) · `deliverable`(제출물) · `unspecified` · 확인 대기 → 제외 목록(사유·원문 포함).
- 기준 문장: 화면 문구에 정확한 값이 있으면 `항목: 값`, 없으면 원문 문장. 응답의 `compare`는 영상 판정이 그 문장에서 화면 글자와 비교할 값이다(비어 있으면 담당자 확인).
- 응답은 `approved: false` 초안이다. 서버에 저장하지 않는다. 영상 입력 화면의 **'계약서(DOCX·PDF)에서 기준 초안 만들기'**가 기준 입력란을 채우고, 사람이 고친 뒤 검수를 시작할 때 기존처럼 기준이 고정된다.
- `GET /api/video-reviews/status`의 `requirements.configured`로 설정 여부를 본다(모델 서버 호출 없음).
- 로고 이미지 검수 파이프라인은 바꾸지 않았다. 로고 요구는 '시각 기준(담당자 확인)' 초안일 뿐이며 로고 원본 대조는 기존 로고 입력으로 따로 한다.
- 정책 `a02-video-v2`(2026-11-10): 응답 필드 순서를 `target → label → expected → source → item_id → required → unresolved → qualifiers`로 바꿨다. `video_spec`·`deliverable`의 값이 원문에 없으면(`A02_EXPECTED_UNGROUNDED`만) 초안 전체를 거부하지 않고 그 값만 비운 뒤, 항목 `reason`에 표시하고 경고 `excluded_value_not_in_source_dropped`를 남긴다. 줄 범위·ID·조건 문구·미정값·중복 검사는 그대로다. `trace.schema_sha256`로 스키마 순서를 추적한다.

### Qwen3.5-4B 서버 설정 (필수)

응답 형식 엔진이 JSON 공백을 무제한 허용하면, 모델이 `"start_line":` 뒤에 공백만 8192토큰까지 찍고 끝나지 않는다(`QWEN_OUTPUT_INCOMPLETE`). 서버에 다음을 붙인다. 요청 단위 옵션으로는 적용되지 않았다.

```
--structured-outputs-config '{"backend": "guidance", "disable_any_whitespace": true}'
```

`backend`를 지정하지 않으면 vLLM이 설정을 거부한다. 포스터 A02 서버도 같은 엔진이라 긴 문서에서 같은 문제가 날 수 있다(미확인).

### 계약서 19부 실험 (2026-11-10, 공유 A40, Qwen3.5-4B)

수치는 **원문 문자열·ID 일치 점수**다. 값(공백 제거)과 항목 ID가 모델이 고른 줄 안에 있는지만 셌고, 대상 분류·필수 여부·미정 여부·조건 문구는 평가하지 않았다. 빈 기대값은 자동 일치로 셌다(19부 응답 중 빈 값 29개, 그중 검수 대상 12개).

| 조건 | 문서 통과 | 문자열·ID 일치 | 주된 실패 |
|---|---|---|---|
| 공백 허용(설정 전) | 0/5에서 중단 | — | 공백 반복으로 응답 미완성 |
| v1 순서(`source` 먼저) + 공백 금지 | 4/19 | 27/47(3부 표본) | 표 첫 행(L2–L3)을 앞 항목에서 베낌 |
| v2 순서(값→줄→ID) | 3/19 | 282/329 | 제출물(E) 값 요약(E 다수, 비E 3건: cv-007 R02·cv-009 R01·cv-015 L02) |
| v2 순서 + 제출물 항목 제외(재실행 아닌 계산) | 16/19 | — | 비E 근거 줄 오선택 |

- 줄 ID를 먼저 쓰게 하는 순서(ID 맨 앞)는 모델이 원문을 보기 전에 `REQ001` 같은 ID를 지어내 2/3부가 실패했다.
- 응답 형식 강제 없이 보내면 줄 ID를 `1` 같은 숫자로 써서 비교할 수 없었다.
- 위 v2 결과는 v2 코드 반영 **전** 실험 스크립트(스키마만 재배열)로 얻은 값이다. 반영 후 실제 `extract()` 재실행, 화면 동작, 운영 서버 설정 적용은 미확인이다.

## 남은 일

- 실제 RunPod 서버와 연결해 실영상 검수 1회 확인
- 계약서 R01~R05 기준의 통과·불합격 판정 정확도 측정(현재 근거는 화면 글자 판독 성능 비교뿐)
- AWS 시험 구성(`--aws-test`)은 기존 워커 슬롯 준비와 Qwen3-VL 서버 준비를 모두 요구한다. 터널 주소 추가 필요
- 영상 A02: v2 코드로 19부 `extract()` 재실행, 의미 정확도(대상 분류·필수·미정·조건 문구) 정답표 대조, 화면 동작 확인, 운영 Qwen 서버에 공백 금지 설정 반영
- 영상 A02: `video_spec`(T01~T05)을 영상 메타데이터(ffprobe)와 자동 대조하는 단계는 아직 없음(제외 목록으로만 표시)
