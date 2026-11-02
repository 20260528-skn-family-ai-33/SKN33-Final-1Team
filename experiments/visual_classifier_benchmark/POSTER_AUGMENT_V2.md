# 검토 포스터 22장 보강과 출처 균형 학습

기존 84.40% 모델과 완료된 실험은 보존하고, `siglip-openimages-v1` 패킷에 새 포스터 22장만 덧붙인다.
이미지·8라벨 CSV·검토 근거를 `poster_augment_v2/`에 함께 제공한다. 이미지도 Git에 포함하므로 별도 재다운로드나 설치는 필요 없다.

## 이번 데이터

- 공식 게시물에서 다운로드한 원본 24장 중 22장을 AI 시각 검토 후 채택했다. 원본과 기존 letterbox 코드로 만든 384px RGB를 확인했다.
- 22장은 관련 시리즈를 묶은 18개 TRAIN 그룹이다. 기존 평가 이미지 6장은 추가하지 않았다.
- 새 라벨 176셀 중 170개를 판정했고 불확실한 6개는 `?`로 남겼다. 미확인 값을 0으로 채우지 않는다.
- 기존 32개 수정 CSV의 SHA256은 `8c229a4217f9ab3eb7b0a0219b350e07c8296aa65c533f89da3869270112d0a0`이며 바이트 그대로 포함했다.
- 기존 187장·보강 포스터 12장·승인 Open Images 315장, 합계 514장과 파일·RGB 픽셀·근접 해시를 비교했다. 중복 검출은 없었고 새 이미지의 가장 가까운 포스터 22쌍도 시각 검토했다. 모든 유사 행사 관계를 완전히 배제했다는 뜻은 아니다.
- 각 이미지의 공식 원문·원본 URL·공공누리 출처표시 조건과 검토 근거는 `annotations.json`, `review.html`에 있다.
- 서울시 원문에서 제4유형을 확인한 버스커 포스터, 보호 개발 자료와 행사 계열 관계가 불명확한 구미 로컬푸드 포스터는 제외했다. 다운로드가 완료되지 않은 인천생활문화축제 파일도 제외했다.
- `APPROVED`는 사용자가 요청한 AI 시각 검토의 학습 반영 상태다. 독립적인 사람 검수 완료를 뜻하지 않는다.

| 라벨 | 새 양성 | 새 음성 | 모름 |
| --- | ---: | ---: | ---: |
| 악기 | 9 | 13 | 0 |
| 음식·음료 | 4 | 15 | 3 |
| 동물 | 10 | 11 | 1 |
| 식물·꽃 | 9 | 12 | 1 |
| 인물 | 15 | 7 | 0 |
| 탈것 | 1 | 21 | 0 |
| 건물·구조물 | 5 | 16 | 1 |
| 불꽃놀이 | 0 | 22 | 0 |

큰 그림 건반·기타, 작은 무대의 기타·북·건반·관악기, 작은 실제 연주 사진을 포함한다.
마이크·음표·공연 문구만 있는 악기 음성, 음식 문구만 있는 야시장, 사물·색·장식을 다른 라벨로 추정하지 않는 사례도 넣었다.
마라카스처럼 이번 승인 묶음에서 충분히 확보하지 못한 종류는 계속 수집할 범위다.

## RunPod에 받기

아래는 **RunPod 터미널** 명령이다. 이전에 브랜치가 갈라졌으므로 새 파일·디렉터리만 원격에서 가져온다.
실행 중인 기존 학습은 이 새 파일을 사용하지 않으며, `.venv`·모델·기존 패킷·완료 런은 이 명령에 포함하지 않는다.

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/prepare_poster_augment.py \
  experiments/visual_classifier_benchmark/finetune_siglip_poster_mix.py \
  experiments/visual_classifier_benchmark/run_poster_augment.sh \
  experiments/visual_classifier_benchmark/POSTER_AUGMENT_V2.md \
  experiments/visual_classifier_benchmark/poster_augment_v2
```

## 패킷 생성

부모는 마지막 완료된 Open Images 실험에 사용한 `training_packets/siglip-openimages-v1`이다.
부모 manifest SHA256 `590120c1e5adb2b08a453e309063c5a4a114fb1600fc02d422ed56d2c644c66a`와 수정 CSV를 확인한다.
부모의 추가 이미지·라벨·ID·그룹·개발셋을 그대로 상속하고 새 승인 이미지 22장만 추가한다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_poster_augment.sh prepare \
  --parent-packet training_packets/siglip-openimages-v1 \
  --out training_packets/siglip-poster-v2
```

예상 결과는 `added_images: 349`, `changed_train_cells: 32`, `new_posters: 22`, 학습462장·개발37장이다.
이는 패킷 생성 후 RunPod 출력으로 확인할 값이며 현재 GPU 실행 결과가 아니다.
출력 폴더가 이미 있으면 동일 lineage·manifest·파일·상속 기록을 확인한 경우에만 재사용한다. 다른 패킷은 덮어쓰지 않는다.
부모가 다르다는 오류가 나면 임의로 해시를 고치거나 기존 패킷을 다시 만들지 말고 실제 부모 파일을 확인한다.

## 새 데이터로 학습·평가

기존 84.40% native 모델을 대조군·초기값으로 유지한다. 새 데이터에서 Attention, LoRA, 예측 보존 LoRA 3개를 비교한다.
이 실행의 `--resume`는 완료 후보를 재사용하는 기능이며 중단된 optimizer 상태를 이어받지는 않는다.
새 패킷이므로 기존 완료 런의 학습을 다시 시작하거나 기존 체크포인트를 덮어쓰지 않는다.

```bash
bash run_poster_augment.sh train \
  --packet training_packets/siglip-poster-v2 \
  --run-dir runs/siglip-poster-v2-mix-fp32 \
  --sampling equal_sources \
  --epochs 16 --min-epochs 16 \
  --lora-rank 8 --lora-alpha 8 --tail-blocks 2 \
  --lora-lr 3e-5 --head-lr 3e-5 \
  --probe-lr 1e-3 --preserve-weight 1.0 \
  --microbatch 2 --resume
```

- 원본 384px 단일 입력·FP32·기존 masked BCE·약한 색상 증강·optimizer와 기존 v2 개발 BCE 모니터를 재사용한다. 새 패키지는 설치하지 않는다.
- 출처는 원본/추가 포스터와 Open Images 두 집합으로 나눈다. 각 epoch에서 모든 적격 원본을 한 번씩 방문하고 작은 집합을 재표집해 두 출처의 이미지 방문 수를 같게 한다. 기본 LoRA에서 포스터147·Open Images315의 고유 이미지를 각315회, 합계630회 방문한다.
- 630회는 **고유 이미지 수가 아닌 방문 수**다. 같은 샘플을 반복한 것이며 새 데이터로 집계하지 않는다. Attention은 기존 규칙대로 악기 정답이 알려진 포스터만 적격이다.
- 균형은 epoch의 이미지 방문 수 기준이며 각 minibatch의 1:1이나 라벨 셀의 손실 가중치 1:1을 의미하지 않는다. 기존 masked loss와 미확인 셀 제외 규칙을 유지한다.
- 개발 BCE·AP와 라벨별 회귀 보호 조건으로 체크포인트·임계값·모델을 먼저 선택한다. 선택 후 같은37장으로 정확도·p95·라벨별 FP/FN을 비교한다. epoch0 대체도 명시적으로 허용한다.
- 새22장과 출처 균형을 함께 바꾸는 실험이다. 데이터 추가와 sampler 각각의 인과 효과를 분리한 결과는 아니다. `--sampling uniform` 대조를 추가하려면 별도 새 run-dir을 사용한다.
- LoRA는 native 가중치로 병합하고 병합 전후 개발 예측을 확인한다. p95는 RunPod 실측으로 판단한다.

```bash
bash run_poster_augment.sh show-results \
  --run-dir runs/siglip-poster-v2-mix-fp32
```

주요 파일은 `dev_candidates.csv`, `dev_per_label.csv`, `comparison.csv`, `per_label_comparison.csv`,
`instrument_positive_scores.csv`, 각 후보의 `history.json`·`checkpoint_selection.json`·`parameter_audit.json`이다.
history에는 고유 이미지 수·출처별 방문 수·샘플 순서 해시도 남는다.

## SCP로 묶음을 옮기는 대안

Git으로 이미지까지 가져왔다면 이 과정은 필요 없다. 로컬에서 제공한 `siglip-poster-v2-assets.tar.gz`를 사용하는 경우에만 진행한다.

작업 PC:

```bash
scp -P 22024 -i ~/.ssh/id_ed25519 \
  "/Users/choejiheum/Documents/Codex/2026-09-25/x20-x20/outputs/모닝컵_가상납품검수_예제/output/siglip-poster-v2-assets.tar.gz" \
  root@63.141.33.152:/workspace/
```

RunPod에서 위 코드 파일을 받은 뒤:

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
tar --no-same-owner --no-same-permissions \
  -xzf /workspace/siglip-poster-v2-assets.tar.gz -C .
```

이후 동일한 `prepare`·`train` 명령을 사용한다. 압축 해제만으로 학습 패킷이 생기지는 않는다.

## 현재 실행 확인 범위

이미지 다운로드·원본/384px 시각 검토·파일/픽셀/근접 해시 비교·CSV 스키마와 보강 묶음 확인을 수행했다.
신규 학습 코드의 로컬 구문 검사·테스트·모델 로딩·추론·학습과 RunPod 직접 실행은 하지 않았다.
새 학습 패킷의 실제 생성과 GPU 학습·평가는 사용자가 위 명령을 실행한 결과로 확인한다.
90%·p95 50ms 달성을 보장하지 않으며, 기존37장은 반복 노출된 평가셋이다. 새 최종 일반화 평가는 별도 자료가 필요하다.
