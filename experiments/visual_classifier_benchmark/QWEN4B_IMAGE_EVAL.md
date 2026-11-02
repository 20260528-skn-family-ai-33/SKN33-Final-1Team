# Qwen3.5-4B 이미지 평가와 텍스트 결과 복구

## 텍스트 CSV 오류 수정

`evaluate_all_requirements.py`의 per-case 라벨 CSV가 첫 데이터셋의 열만 사용해,
agent72에 있는 `review_status`를 저장하다 실패했다. 수정 후 모든 행의 열 합집합을
사용하며 기존 리뷰 정보도 보존한다. 기본 새 실행 경로는 `*-v2`다. 코드 해시가
바뀌었으므로 기존 v1 실행에 수정된 코드로 --resume하지 않는다.

`recover_all_requirements.py`는 기존 140건 캐시를 읽고 원본 프로토콜 서명, 데이터,
리뷰 파일, 관련 의존성 해시, 원문 응답 재파싱, 재요청 선택과 후처리를 확인한다.
알려진 이전 CSV exporter 해시만 예외로 인정한다. 새 보고서는 별도 디렉터리에 쓰며
실패했던 원래 실행과 캐시를 덮어쓰지 않는다. GPU·추론·설치 없이 보고서만 복구한다.
기존 측정 시간을 재사용하므로 시간 재측정으로 표기하지 않는다.

```bash
.venv/bin/python -B recover_all_requirements.py \
  --source-run runs/qwen4b-all-requirements-structure-v1 \
  --out runs/qwen4b-all-requirements-structure-recovered-v1

bash run_all_requirements.sh preflight --profile conditions
bash run_all_requirements.sh run --profile conditions --ram-policy clean-unmapped-file --confirm-agent-partial --resume
bash run_all_requirements.sh show-results --profile conditions
```

conditions는 기존 실행이 시작되지 않은 상태이므로 새 평가가 필요하다. 기존 구조물
후보의 추론은 복구 과정에서 다시 실행하지 않는다. 40/72/28건은 텍스트 평가이며,
이미지를 보거나 최종 납품의 충족/위반 여부를 검사하는 실행이 아니다.

## 4B 이미지 비교의 입력과 모델

`evaluate_qwen4b_images.py`는 기존 `/workspace/models/qwen3.5-4b`의 전체 멀티모달
체크포인트와 이미지 processor를 오프라인으로 사용한다. 이미지 타입 메시지와
실제 pixel 입력을 전달한다. 가중치를 다시 받거나 패키지를 설치하지 않는다.
모델명에서 3.5는 계열 버전이고 4B는 파라미터 규모다.

기준 SigLIP2는 보존·복원한 `runs/siglip-ai-reviewed-v1-fp32`의 선택 체크포인트와
8개 임계값을 검증해 사용한다. 기존 원본 bundle의 개발37장과 평가37장에 같은
384px RGB/EXIF/bicubic/회색 letterbox를 적용한다. Qwen의 실제 RGB 해시와
processor grid `[1,24,24]`를 확인하며, 정답·이미지 ID·SigLIP 판정은 Qwen에게
전달하지 않는다. 고정 8라벨 프롬프트는 이전9B 이미지 실험과 같다.

두 모델은 GPU에 순차적으로 적재한다. 각각 예열3회, 이미지당3회 측정하며 첫
응답으로 정확도를 계산한다. 다른 반복은 속도와 판정 불일치를 기록하며 다수결로
성능을 올리지 않는다. 자동 재질문·JSON 수선·임계값 조정·추가 학습은 없다.
Qwen은 BF16/SDPA/greedy/non-thinking/max192 new tokens, SigLIP은 FP32다.
따라서 실제 배포 구성의 비교이며 순수한 같은 정밀도의 아키텍처 크기 비교가 아니다.

```bash
bash run_qwen4b_images.sh preflight
bash run_qwen4b_images.sh run --ram-policy clean-unmapped-file --resume
bash run_qwen4b_images.sh show-results
```

기본 출력: `runs/qwen4b-image-compare-v1`. 기존 이미지·텍스트 결과는 보존한다.
같은 설정의 중단 실행만 --resume으로 이어가고, 완료 실행은 추론을 반복하지 않는다.
코드나 설정이 바뀌면 새로운 run-dir을 사용한다. 실행 도중 GPU 모델 변경이나 다른
학습 강제 종료·CPU fallback·자동 양자화를 하지 않는다. 필요한 모델·이미지가 없거나
기존 환경이 지원하지 않으면 오류를 기록하고 중단한다.

## 지표와 시간

`comparison.csv`는 두 모델의 정확도, coverage, 보수적 Macro F1, answered-only F1,
오탐/누락, 양성/음성 미판정, 평균·중앙값·p95를 split별로 기록한다. 미판정이 있는
알려진 라벨은 정확도에서 오답이다. Macro F1의 양성 미판정은 누락으로 포함하며,
음성 미판정은 별도 coverage와 함께 읽는다. 정답 unknown은 기존 규칙대로 제외한다.

시간은 이미지 디코딩/전처리, GPU 전송, 실제 모델·분류기 또는 전체 JSON 생성,
Qwen 디코딩/파싱을 포함한다. 파일 SHA·보고서 쓰기·모델 로딩·예열·외부 OCR·통신은
제외한다. 이번 실행에서는 SigLIP도 같은 표본·반복 수로 새 측정한다. 재개 과정의
세션 수는 결과에 남기며 두 모델이 같은 순간 동시에 측정되는 것은 아니다.

`per_label.csv`, `errors.csv`, `instrument_errors.csv`, `predictions.json` 및 모델별
원문 요청/반복별 timing 파일로 상세 비교한다. Qwen이 낸0/1/null을 확률로 바꾸거나
AP로 해석하지 않는다. 8개 시각 대상의 존재 판정이며 수량·배치·최종 요구 충족 판정은
별도의 평가다. 기존37장은 반복 노출된 회귀 평가이고 독립 실사용 성능이 아니다.

사용자 요청에 따라 작성 중 로컬 실행 검증·학습·재설치 및 RunPod 직접 실행은 하지
않았다. 실제 동작과 성능은 사용자가 RunPod에서 위 명령을 실행한 결과로 확인한다.
