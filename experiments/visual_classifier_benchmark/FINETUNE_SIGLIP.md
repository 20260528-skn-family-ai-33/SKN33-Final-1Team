# SigLIP 2 이미지 본체 파인튜닝

기존 `.venv`, `/workspace/models/siglip2-base-384`, 승인 라벨/분할을 그대로 사용합니다.
설치 명령이나 새 다운로드는 없습니다. 기존 `encoders.py`, `run_experiment.py`, 기존 평가 결과는 수정하지 않습니다.

## 실제 학습 범위

- SigLIP의 **비전 타워 전체 + 8개 라벨 분류층**을 학습합니다. 텍스트 타워는 사용하지 않습니다.
- 원본 사전학습 비전 가중치와 기존 `runs/heads-fp32`의 개발 선택 분류층에서 각 후보를 동일하게 시작합니다.
- 학습113장·개발37장·평가37장, 모름 제외861/283/282개 판정, 기존 SHA256/그룹 검증을 유지합니다.
- 전체 포스터를 보존한384px bicubic letterbox 및 기존 정규화를 그대로 사용합니다.
- 학습에만 brightness/contrast=.08, saturation=.05 ColorJitter를 사용합니다. crop/flip 등 위치 변형은 없습니다.
- FP32, AdamW, head LR1e-3, weight decay1e-4, gradient clip1, microbatch2/누적8장입니다.
- 본체 LR1e-5/3e-5 × positive weight 없음/학습 known 양·음성 수의 비율 = 총4개 후보입니다.
- 최대10epoch, 개발 Macro AP가3회 연속 개선되지 않으면 조기 종료합니다.
- 후보별 체크포인트는 epoch1 이상 개발 Macro AP 최대로 정합니다. 개발 라벨별F1로 임계값을 고릅니다.
- 최종 후보는 개발 Macro F1 → 개발 정확도 → 개발 Macro AP → 작은LR → 가중치없음 순으로 선택합니다.
- 모든 선택/해시를 고정한 다음 평가셋을 읽습니다. 같은37장은 이미 노출된 후속 평가이며 새 독립 일반화 성능이 아닙니다.
- 각 vision block/embeddings/pooler/분류층의 실제 가중치 변화와 대표 gradient를 확인합니다.

## RunPod에서 실행

다른 실험이 실행 중인지 먼저 확인합니다. 이미 실행 중이면 중복 시작하지 않습니다.
기존 환경에 다음 두 새 파일만 추가하면 됩니다: `finetune_siglip.py`, `run_finetune.sh`.
Git 업데이트 절차는 [README.md](README.md)의 기존 환경 사용 항목을 따릅니다.

기존 자동 실행의 상태/결과만 볼 때는 아래 읽기 명령을 사용합니다.

```bash
cat /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/runs/siglip-full-fp32/status.json
cat /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/runs/siglip-full-fp32/comparison.csv
```

사용자가 새 실험을 직접 시작할 경우 별도 run 폴더를 지정합니다. 패키지 설치는 없습니다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_finetune.sh --run-dir runs/siglip-full-manual-fp32
```

완료 후보의 체크포인트는 재사용하며, 중단된 후보는 초기 가중치부터 다시 수행합니다.
같은 코드/데이터/GPU/패키지/인수에서만 재개합니다.

```bash
bash run_finetune.sh --run-dir runs/siglip-full-manual-fp32 --resume
```

코드/인수가 달라졌다면 새 run 폴더가 필요합니다. 선택 JSON 생성 도중 중단돼 commit이 없으면 안전하게 실패합니다.

## 비교 결과

파인튜닝 후 **기존 고정 본체 모델도 같은 GPU에서 다시 측정**합니다.
기존 고정 모델은 원래37장의 판정과 완전히 일치해야 합니다.

- 두 모델 FP32/배치1, 각20회예열 + 37장×10회 = 모델별370회 측정입니다.
- 디코딩·letterbox·정규화·H2D·비전인코더·분류층·D2H·동기화를 포함합니다.
- OCR/API/DB/모델 로딩·파일 저장은 제외합니다. 다른 모델을 동시에 실행하지 않습니다.
- 목표 판정은 라벨별 정확도≥90%와 p95≤50ms입니다. 달성을 보장하지 않습니다.
- `comparison.csv`, `comparison_summary.json`이 최종 비교입니다.
- `candidates/*`에 학습 로그, 개발 예측, 실제 변경 증거와 best.safetensors가 있습니다.
- `selection_frozen.json`, `selection_commit.json`은 평가 전 선택 증거입니다.
- `siglip2_frozen/`, `siglip2_full_finetune/`에 원시 평가 예측/타이밍을 저장합니다.
- `status.json`이complete이고 마지막 이벤트가full_finetune_complete여야 완료입니다.
- 기존 데이터·모델·기준선 파일은 실행 전후 모두 무결성을 다시 확인합니다.

```bash
cat runs/siglip-full-fp32/comparison.csv
```

## 로컬 코드 검증

`tests/test_finetune.py`는 합성 CPU 데이터로 모름 마스크·누적 손실·실제 본체 가중치 변화·체크포인트 복원·개발셋 선택을 검증합니다.
실제 학습 성공이나 CUDA 속도 증거는 RunPod 산출물로 확인해야 합니다.
