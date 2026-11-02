# Qwen3.5-9B와 기존 SigLIP2의 8라벨 무학습 비교

Qwen의 가중치·분류기·임계값을 학습하지 않는다. 기존 환경의 Python과 설치된 라이브러리만 사용한다.
기존 32개 수정·12장 보강·Open Images·22장 포스터 보강 패킷과 모든 기존 run은 읽기 대상으로 보존한다.
이 작업은 학습 데이터의 승인/수정, 모델 배포, SigLIP2 재학습을 수행하지 않는다.

## 비교 기준

- 기본 비교 대상은 완료된 `runs/siglip-poster-v2-mix-fp32`의 **개발셋 선택 모델**이다. 현재 로그에서는 `siglip2_previous_partial` 84.40%다. 평가 정확도가 가장 높은 LoRA를 사후 선택하지 않는다.
- 기존 SigLIP의 개발 예측·평가 예측·임계값·측정값을 해시 검증 후 재사용한다. SigLIP을 다시 추론하지 않는다.
- Qwen에는 정답·파일명·이미지 ID·기존 SigLIP 판정을 전달하지 않는다. 원래 개발37장과 평가37장을 각각 판정하며 TRAIN 자료는 사용하지 않는다.
- 동일한 8라벨 정의와 동일한 384px RGB letterbox 입력을 사용한다. 사진·그림·실루엣·동물 마스코트를 포함하며 마이크·음표·공연 제목은 악기로 보지 않는다. 작은 후원 로고와 글자 내부 픽토그램은 제외한다.
- Qwen은 공식 instruct 모델 `Qwen/Qwen3.5-9B`, BF16·SDPA·단일 GPU·batch1이다. SigLIP은 기존 FP32다. 동일 추론 정밀도의 순수 모델 크기 비교는 아니다.
- `enable_thinking=False`, greedy decoding, 최대192토큰, 고정 프롬프트, few-shot 예시0개다. 프롬프트·모델 revision·코드·데이터·환경을 평가 전에 고정한다. 개발/평가 점수로 이 실행의 프롬프트·임계값·모델을 고르지 않는다.
- JSON에는 8개 키와 `0/1/null`만 허용한다. 코드블록·추가 설명·중복 키·문자열·bool은 파싱 실패다. 자동 재질문이나 JSON 수선은 하지 않는다.
- **정확도는 미판정/파싱 실패가 있는 모든 알려진 정답 셀을 오답으로 계산한다.** 미판정을 음성0으로 바꾸거나 분모에서 빼지 않는다. 원래 정답이 unknown인14개 평가 셀만 기존 규칙대로 제외한다.
- FP/FN은 명시적인 이진 오판만 집계하며 미판정 양성/음성은 별도 열이다. `macro_f1_covered`는 응답한 셀의 F1이므로 반드시 `coverage`와 함께 본다. coverage100%이면 기존 이진 Macro F1과 같은 기준이다.
- SigLIP의 AP는 저장된 점수로 계산한다. Qwen은 생성한 이진/미판정 결과만 제공하므로 AP를 만들지 않고 빈 값으로 남긴다. 생성 확신 표현을 확률로 사용하지 않는다.

## RunPod에 코드 받기

다음 명령은 **RunPod 터미널**에서 실행한다. 이전의 divergent branch 문제를 피하기 위해 필요한 새 파일만 원격에서 가져온다.
저장소 전체를 reset하거나 기존 환경·모델·패킷·실행 중 학습 코드를 바꾸지 않는다.

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/evaluate_qwen_zero_shot.py \
  experiments/visual_classifier_benchmark/qwen_zero_shot_report.py \
  experiments/visual_classifier_benchmark/qwen_zero_shot_prompt.json \
  experiments/visual_classifier_benchmark/run_qwen_zero_shot.sh \
  experiments/visual_classifier_benchmark/QWEN_ZERO_SHOT.md
```

## 1. 기존 환경 지원 확인

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_qwen_zero_shot.sh check-env
```

기본 Python은 이 벤치마크의 `.venv/bin/python`이다. 터미널의 `model-download-venv` 활성화 여부와 무관하다.
이미 다른 경로에 동일한 환경이 있다면 `BENCH_PYTHON=/정확한/기존/python`을 명시한다.
프로젝트 requirements의 Transformers는5.20.2지만, RunPod의 실제 import·버전·CUDA·BF16 지원은 위 출력으로 확인한다.
이 명령은 모델 로딩·추론·학습·설치를 하지 않는다. 지원 클래스/패키지가 없으면 환경을 바꾸지 않고 중단한다.
`setup_runpod.sh`, `pip install`, `pip upgrade`는 실행하지 않는다.

9B BF16 가중치는 대략18GB 규모이고 활성값·캐시·CUDA 메모리가 추가된다. 24GB GPU에서도 여유 메모리에 따라 OOM이 날 수 있다.
다른 학습을 강제 종료하거나 해상도·정밀도·양자화를 자동 변경하지 않는다. OOM이면 `failure.json`을 확인하고 메모리 상태부터 확인한다.

## 2. 모델만 다운로드

```bash
bash run_qwen_zero_shot.sh download \
  --model-dir /workspace/models/qwen3.5-9b
```

이미 설치된 `huggingface_hub`를 사용한다. 첫 실행에서 `main`을 정확한 commit SHA로 해석한 뒤 그 revision의
모델·토크나이저·processor·chat template만 받는다. `zero_shot_download_manifest.json`에 파일 크기·SHA256을 기록한다.
원격 모델은 Git에 push하지 않는다. 저장소에는 비교 코드와 프롬프트만 들어간다.
이 다운로드 명령만 네트워크를 허용하고 평가 명령은 offline/local-files-only다. 기존 HF 인증을 재사용한다.

다운로드가 중단되면 같은 명령을 다시 실행한다. 시작 시 저장한 intent revision과 HF 파일 캐시를 재사용한다.
완료된 다운로드는 manifest 검증 후 재사용한다. 다른 revision은 별도 모델 디렉터리로 받는다.

## 3. 개발·평가 실행

다른 학습이 GPU를 사용 중이면 종료될 때까지 기다린다. 아래 명령이 Qwen의 최초 모델 로딩·GPU 추론이다.

```bash
bash run_qwen_zero_shot.sh run \
  --model-dir /workspace/models/qwen3.5-9b \
  --reference-run runs/siglip-poster-v2-mix-fp32 \
  --run-dir runs/qwen3.5-9b-zero-shot-v1 \
  --split all \
  --warmups 3 --repeats 3 \
  --resume
```

개발셋 확인만 먼저 하려면 `--split dev --run-dir runs/qwen3.5-9b-zero-shot-dev-v1`로 별도 실행할 수 있다.
split/프롬프트/토큰/반복 수 등을 바꾸면 새 run-dir을 사용한다. 이미 본 평가 결과에 맞춰 프롬프트를 수정하지 않는다.

- 사전 조건: 원래 데이터 bundle, 기존 완료 run의 protocol/selection/후보·예측·측정 파일, 동일 GPU/Python/기존 패키지 버전이 필요하다.
- 데이터·모델 해시는 모델 로딩 전에 확인한다. 평가 전 고정된 선택 기록이 있어야 평가 정답을 읽는다.
- 기본 예열3회는 개발 첫 이미지로 수행한다. 이후 개발37장×3회와 평가37장×3회를 측정한다.
- 각 이미지의 **첫 응답**을 정확도에 사용한다. 나머지 반복은 지연시간과 판정 불일치를 기록한다. 다수결·정답 기반 재시도는 하지 않는다.
- 각 요청을 원문 JSON과 함께 원자적으로 저장한다. 중단 후 같은 명령의 `--resume`는 완료 요청을 재사용한다. 실행 재개 시 모델을 다시 로딩하고 예열한다.
- 모델/환경이 다르거나 기존 결과가 변했으면 임의로 계속하지 않고 중단한다. 기존 학습은 시작하지 않는다.

## 4. 결과 확인

```bash
bash run_qwen_zero_shot.sh show-results \
  --run-dir runs/qwen3.5-9b-zero-shot-v1
```

최종 완료 증거는 새 run의 `status.json`이다. 주요 결과:

| 파일 | 내용 |
| --- | --- |
| `comparison.csv` | 개발/평가별 정확도·coverage·응답셀F1·FP/FN·미판정·p95 |
| `dev/per_label_comparison.csv` | 개발 라벨별 FP/FN·미판정 비교 |
| `test/per_label_comparison.csv` | 평가 라벨별 FP/FN·미판정 비교 |
| `test/instrument_positive_scores.csv` | 악기 양성7장의 기존 점수/임계값과 Qwen 판정. Qwen 점수는 없음 |
| `dev/disagreements.csv`, `test/disagreements.csv` | 두 모델의 판정이 달라진 이미지·라벨 |
| `dev/errors.csv`, `test/errors.csv` | 같은 판정으로 함께 틀린 사례도 포함한 오류 목록 |
| `dev/qwen_predictions.json`, `test/qwen_predictions.json` | 이미지별 응답 원문·파싱 실패·반복 불일치 |
| `requests/<split>/*.json` | 모든 반복의 생성 원문·토큰 수·입력RGB해시·개별 시간 |
| `protocol.json`, `selection_frozen.json` | 모델·프롬프트·입력·정밀도·환경·기존 선택 모델의 고정 근거 |

Qwen 시간은 `decode_preprocess_ms`, `transfer_generate_ms`, `decode_parse_ms`, `total_ms`로 나눈다.
이미지 디코딩·letterbox·프롬프트 구성·processor·GPU 전송·**전체 JSON 생성**·출력 디코딩/파싱을 포함하고,
다운로드·모델 로딩·예열·OCR·네트워크·DB·결과 파일 쓰기는 제외한다.

**SigLIP p95는 같은 기존 GPU 환경에서 저장한 과거 값이며 이번에 동시 재측정한 값이 아니다.**
개발 SigLIP 시간은 없으므로 빈 값이다. 기본 Qwen 반복은3회/이미지, 기존 SigLIP은10회/이미지로 측정 수가 다르다.
비교표의 `latency_source`·`timing_n`과 함께 보고, 엄밀한 같은 시점 속도 비교로 해석하지 않는다.
동일한384px 입력 비교이므로 Qwen의 더 큰 원본 입력에서의 성능 상한을 측정한 결과도 아니다.
반복 노출된 평가37장은 독립적인 최종 일반화 평가가 아니다. 90%·p95 50ms 충족은 실행 결과로 판단한다.

## 구현 근거와 실행 범위

- [Qwen3.5-9B 공식 모델 카드](https://huggingface.co/Qwen/Qwen3.5-9B)
- [Transformers Qwen3.5 공식 문서](https://huggingface.co/docs/transformers/model_doc/qwen3_5)
- [공식 chat template의 non-thinking 처리](https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/chat_template.jinja)

이번 제공 단계에서는 코드 작성·커밋·push만 수행한다. 사용자의 지시에 따라 로컬 구문 검사·테스트·모델 로딩·추론,
모델 다운로드·GPU 평가·RunPod 접속은 실행하지 않았다. 신규 코드의 실제 동작은 위 RunPod 실행 결과로 확인한다.
