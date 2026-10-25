# SigLIP2 임계값·확률 순위 진단

기존 `runs/siglip-full-fp32`에 저장된 개발 확률과 평가 확률만 사용합니다.
표준 라이브러리와 기존 결과 읽기 도구로 동작하며 설치·다운로드·모델 로딩·추론·학습을 하지 않습니다.
사용자 요청에 따라 로컬 실행 검수도 하지 않았습니다. 기존 모델·라벨·실험 파일과 환경을 유지합니다.

## 이번 결과의 해석

- 개발91.87%와 평가84.04% 사이7.83%p 차이만으로 과적합 원인을 확정할 수는 없습니다.
- 악기 AP는 개발0.8213→평가0.4917입니다. AP는 확률 순위 지표여서 임계값만 바꾸어 개선되지 않습니다.
- 개발 악기 양성 P118/P153의 확률0.2114/0.0188보다 음성 P011 확률0.8339가 높습니다.
  단일 임계값은 이 세 판정을 모두 맞힐 수 없습니다. 모델의 순위 오류 또는 라벨/표현 기준의 검토가 필요합니다.
- 음식 임계값0.15는 오탐에 영향을 줄 수 있으나 오류 CSV에는 정상 양성의 확률이 없습니다.
  임계값을 임의로 올리면 새 누락이 생길 수 있으므로 전체 개발 확률에서 손익을 확인해야 합니다.
- 차량·동물·사람도 높은 확률의 음성과 낮은 확률의 양성이 함께 있습니다.
  같은 데이터로 무조건 재학습하거나 개발/평가 라벨을 학습용으로 이동하지 않습니다.

## 진단과 보수적 임계값 실험

1. 승인된 기존 패킷·분할·모름 마스크, 완료된 선택과 가중치/개발 확률 해시를 확인합니다.
2. 기존 개발 점수와 AP를 저장 확률에서 재현합니다.
3. 모든 라벨에 대해0~1/0.05 간격의 임계값별 TP/FP/FN/TN을 기록합니다.
4. 알려진 개발 확률에서 단일 임계값으로 가능한 최소 오류 수를 계산합니다.
   이는 개발셋에 맞춘 낙관적 기술 통계이며 운영 성능이나 새 일반화 추정이 아닙니다.
5. 양성 확률≤음성 확률인 쌍의 개수와 라벨별 차이가 큰10쌍을 저장합니다.
   쌍의 개수는 필요한 라벨 수정 수나 모델 오류 수가 아니며, 라벨은 자동 변경하지 않습니다.
6. 실험용 임계값은 원래 값±0.20 안의0.05 간격에서 개발 정확도를 최대화합니다.
   라벨별 F1 하락≤0.02, recall 하락≤0.05를 요구하고, 동률은 F1과 원래 값과의 가까움을 우선합니다.
7. 개발 그룹을 하나씩 빼고 다시 고르는 민감도 진단을 합니다.
   그룹을 뺀 선택의80% 이상이 전체 개발 후보±0.05 안에 있어야 하며,
   그룹별 제외 예측을 합친 오류 수도 원래 임계값보다 적고 같은 F1/recall 제약을 통과해야 합니다.
   전체 개발 후보 자체도 오류가 줄어야 최종 실험값으로 채택합니다. 조건 미달이면 원래 값을 유지합니다.
8. 임계값 선택을 파일·해시로 고정한 뒤 평가 저장 확률을 재판정합니다.

**이 민감도 진단은 독립 교차검증이 아닙니다.** 모델과 원래 임계값이 이미 개발셋 전체로 선택되어 있습니다.
평가 결과를 보고 정책을 재선택하지 않습니다. 같은 평가37장의 후속 비교이며 독립 일반화 정확도가 아닙니다.
AP는 전후 동일해야 합니다. 모델 추론이나 p95를 새로 측정하지 않으며 이전 지연시간은 출처가 있는 참고값으로만 기록합니다.
새 임계값은 실험 결과이며 기존 서비스나 모델에 자동 적용하지 않습니다. 변경0개도 유효한 진단 결과입니다.

## RunPod 코드 반영

다른 브랜치에서 발생했던 pull 충돌을 피하기 위해 새 파일만 가져옵니다.
기존 `resultlib.py`, `prepare_partial_data.py`, `../poster_benchmark/benchlib.py`, `bundle_manifest.json`은 그대로 필요합니다.

```bash
cd /workspace/SKN33-Final-1Team
git fetch origin dev/12_runpod
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/audit_siglip_thresholds.py \
  experiments/visual_classifier_benchmark/run_siglip_threshold_audit.sh \
  experiments/visual_classifier_benchmark/SIGLIP_THRESHOLD_AUDIT.md
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
```

## 저장 결과 진단·재판정

```bash
bash run_siglip_threshold_audit.sh run --resume
bash run_siglip_threshold_audit.sh show-results
```

새 결과 경로는 `reviews/siglip-threshold-audit-v1`입니다.
완료 결과는 해시 확인 후 재사용하며 입력·코드·정책이 다르면 새 `--out`을 요구합니다.
이전 실험이나 검토 파일을 덮어쓰지 않습니다.

- `dev_threshold_diagnosis.csv`: 기존/후보 임계값·FP/FN, 민감도·안정성, 낙관적 최소 오류, 순위 충돌 쌍 개수
- `dev_threshold_sweep.csv`: 라벨별 임계값 곡선
- `dev_errors.csv`: 기존23개 개발 오류와 원본 경로
- `ranking_conflicts.csv`: 양성/음성 확률 순서가 뒤집힌 쌍과 이미지 경로
- `dev_review.html`, `images/`: 개발 오류 이미지와 충돌 쌍. 원본을 복사하며 평가 이미지를 포함하지 않습니다.
- `comparison.csv`, `per_label_comparison.csv`: 원래 임계값과 실험 임계값의 평가 저장 확률 재판정 결과
- `dev_summary.json`, `comparison_summary.json`: 개발/평가 AP·정확도·F1 및 재측정하지 않은 지연시간의 출처
- `protocol.json`, `selection_frozen.json`, `selection_commit.json`: 평가 전에 고정한 선택과 입력 해시

Jupyter 파일 탐색기에서 새 폴더의 `dev_review.html`을 열어 확인합니다.
고확률 오탐이 실제 라벨 오류인지, 그림/아이콘/작은 객체의 정의 차이인지는 원본을 사람이 보고 판단해야 합니다.
다음 파인튜닝에 데이터를 보강할 때는 기존 train과 새 승인 train만 사용합니다. dev/test는 보호합니다.
