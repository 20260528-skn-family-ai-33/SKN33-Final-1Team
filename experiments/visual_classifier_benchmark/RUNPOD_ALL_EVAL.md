# RunPod 전체 데이터 평가 준비

`inventory_runpod_eval.py`는 런팟에서 기존 데이터의 경로·분할·라벨 셀 수를 살피는
표준 라이브러리 도구다. GPU, 추론, 재학습, 설치 없이 사용자가 직접 실행한다.
새 보고서 폴더에만 출력하며 기존 데이터·환경·모델·실험 결과는 변경하지 않는다.

```bash
.venv/bin/python -B inventory_runpod_eval.py \
  --out reviews/runpod-eval-inventory-v1
```

`datasets.csv`와 `inventory.json`을 생성하며 표준 출력에는 원문·라벨 내용 없이 요약만
표시한다. 경로와 모델 존재 여부는 보고서에 포함된다. 동일 출력 경로는 덮어쓰지 않는다.

원본 번들의 train/dev/test, 각 `training_packets/*`의 train/dev, 기존 Qwen 40건·72건·28건,
`/workspace/datasets`의 이미지 디렉터리와 CSV 메타데이터를 따로 기록한다. 숨김 폴더와
symlink는 추적하지 않으며 탐색 깊이·항목·메타데이터 크기에 제한이 있다. `partial` 또는
`unavailable`은 전수 확인을 의미하지 않는다. 파일 경로만 확인하고 이미지 디코딩·해시,
모델 무결성, 라벨·승인 독립 검증은 수행하지 않는다. 크기 제한 안의 JSON·CSV
메타데이터는 파일 SHA-256을 기록하지만, 이미지나 모델 가중치 해시는 계산하지 않는다.

동일 이미지가 여러 패킷에 복제되어 있으므로 각 행의 수치를 합치지 않는다. 별도 평가
설계에서는 패킷 lineage와 원본·RGB·그룹 식별자를 검토해 중복·누출을 확인해야 한다.
원본 113/37/37은 각각 학습·개발·반복 노출된 평가 데이터다. 추가 이미지도 패킷에서
학습 데이터다. 정답 미확인 칸은 unknown으로 유지하고, 없는 라벨을 0으로 채우지 않는다.
Open Images의 미검토 후보는 학습 또는 평가용 승인 자료로 간주하지 않는다.

Qwen 40/72/28은 요구사항 **텍스트**의 합성 개발 자료이며 사람 최종 승인 여부가 다르다.
이미지의 SigLIP2 8라벨 성능과 합산하거나 동일 정확도라고 부르지 않는다. 원본 37장도
여러 차례 노출되어 독립 일반화 성능으로 볼 수 없다.

전체 데이터의 실제 위치·정답 상태와 보존된 체크포인트가 확인되기 전에는 새 평가의
모델·범위·분모를 확정할 수 없다. 무라벨 이미지에서는 추론값만 기록할 수 있다.

## 요구사항 텍스트 평가

기존 Qwen 4B와 검증 실패 시 최대 1회 재요청을 사용한다. `structure`는 기존 후보,
`conditions`는 새 중복·모호성 프롬프트다. 각 프로필은 별도 결과 폴더를 사용한다.
40·72·28건을 따로 채점하고 UNREVIEWED H010/H032의 모든 정답 칸을 제외한다.
원문과 생성 결과는 `predictions.json`에 저장되므로 공유 전에 내용을 확인한다.

```bash
bash run_all_requirements.sh preflight --profile structure
bash run_all_requirements.sh run --profile structure --ram-policy clean-unmapped-file --confirm-agent-partial --resume
bash run_all_requirements.sh show-results --profile structure
```

기본 `--review-file`은 `/workspace/qwen-eval-agent-70-pending-2.json`이며 기존에
사용자가 업로드한 파일을 읽는다. 없거나 해시가 맞지 않으면 평가가 중단된다.
데이터가 synthetic이고 이미 검토에 사용되었음을 결과에 남긴다. 이 스크립트는
별도 미승인 요구사항 문서 전체를 자동으로 찾아 정답으로 간주하지 않는다.

## 이미지 평가

`run_all_images.sh`는 보존된 `siglip-ai-reviewed-v1-fp32`의 선택 가중치와 기존
임계값을 확인하고 이미지별 추론한다. 원본의 train/dev/test와 검증 가능한 각
training packet의 split을 **따로** 평가한다. 동일 이미지의 파일 SHA가 같으면
추론을 재사용하되 출처별 결과는 남긴다. `/workspace/datasets`,
`/workspace/query_logos`, `/workspace/video_logos`의 정답 없는 이미지는 예측만
하며 정답 추정은 하지 않는다. 다른 경로, 숨김 데이터, 비디오와 추가 라벨셋은
자동 평가를 보장하지 않는다. 기존 임계값을 다시 조정하지 않는다.

```bash
bash run_all_images.sh preflight
bash run_all_images.sh run --resume
bash run_all_images.sh show-results
```

출력 경로는 `runs/siglip-all-data-eval-v1`이다. `inventory.json`, `errors.json`,
`metrics.csv`, `per_label.csv`, `predictions.json`, `summary.json`이 남는다.
체크포인트나 필수 데이터가 없으면 preflight가 중단된다. 개별 패킷 검증 오류·이미지
디코딩 실패 등은 `errors.json` 및 `complete_with_errors`로 표시한다. 파일 SHA가 같은
중복은 같은 출처/split 내에서 중복 집계하지 않고, 교차 packet 점수는 합산하지 않는다.
제공된 이미지 경로만 탐색하므로 다른 위치는 목록 확인 후 명시적으로 추가해야 한다.

이미지 시간은 고유 파일마다 한 번 측정한 decode/전처리/전송/추론 시간이다. 워밍업이
없고 중단 후 재개 시 여러 측정 세션이 섞일 수 있어 이전 370회 반복 p95와 직접 비교할
수 없다. 골드 양성이 없는 라벨은 F1=None으로 표시하며 양성이 있는 라벨만의 macro와
양성 없는 라벨을 0으로 간주한 8라벨 macro를 별도 표시한다. 원본 test37과 train/dev는
미노출 실사용 정확도가 아니다. 사용자 요청으로 작성 중 로컬 코드 검증, 재학습,
재설치와 RunPod 직접 실행은 하지 않았다.
