# SigLIP2와 Qwen4B의 저장 예측 보완성 분석

`runs/qwen4b-image-compare-v1`에서 저장한 두 모델의 첫 응답을 비교한다.
원본 run은 읽기 전용으로 사용한다. 모델·CUDA·이미지 디코더를 불러오지 않으며,
재추론·설치·다운로드·임계값 보정은 하지 않는다.

입력 status가 complete이고 protocol 서명이 맞는지, manifest에 기록된 모든 결과
파일의 해시가 같은지 확인한다. 전체 예측은 원시 첫 요청과 대조하고 Qwen 원문을
기존 JSON 파서로 재해석한다. 두 모델의 image ID·경로·파일 해시·정답과 unknown
라벨이 일치해야 한다. 저장된 정확도도 재계산하여 비교한다.

## 실행

```bash
bash run_image_complementarity.sh run
bash run_image_complementarity.sh show-results
```

기본 출력은 `reviews/qwen4b-siglip-complementarity-v1`이며 기존 디렉터리는
덮어쓰지 않는다. 다시 읽을 때는 show-results를 사용한다. 새 분석이 필요하면
`--out reviews/다른-이름`을 지정한다. 다른 완료 비교는 --source-run으로 지정한다.

## 분석 단위와 파일

한 행은 이미지 한 장 전체가 아니라 **알려진 정답이 있는 이미지×라벨 한 칸**이다.
예를 들어 같은 포스터에서 악기는 Qwen만 맞고 차량은 SigLIP만 맞을 수 있다.

- `both_correct.csv`: 둘 다 정답
- `siglip_only_correct.csv`: SigLIP만 정답
- `qwen_only_correct.csv`: Qwen만 정답
- `both_wrong.csv`: 둘 다 오답; 미판정은 오답으로 포함
- `unknown_gold_cells.csv`: 정답 미확인으로 채점에서 제외한 칸
- `comparison.csv`: dev/test별 네 범주의 수와 각 정확도
- `per_label.csv`: 각 라벨의 보완성, 오류와 상한
- `per_image.csv`: 이미지별 알려진 라벨 정답/오류 수; unknown 수 별도
- `all_known_cells.csv`: 전체 상세 행과 SigLIP 확률/기존 임계값/거리,
  Qwen 판정/파싱 오류, 반복 판정 불일치, 원시 첫 요청 위치
- `summary.json`, `source_manifest.json`, `status.json`: 의미·출처·해시·완료 기록

## 상한의 의미

`oracle_accuracy_upper_bound`는 정답을 알고 매 칸 더 나은 모델을 고르면 맞힐 수
있는 이론적 상한이다. 계산은 `1 - both_wrong / known_cells`이다. 이는 실제 결합
모델의 정확도나 달성한 운영 성능이 아니다. `oracle_gain_over_siglip`은 Qwen만
맞은 칸, `oracle_gain_over_qwen`은 SigLIP만 맞은 칸이다.

출력으로 오류의 겹침과 어떤 라벨에서 보완 가능성이 있는지 분석할 수 있다.
이 평가37장의 정답을 보고 라벨별 모델이나 routing 임계값을 고른 뒤 독립 성능으로
보고하지 않는다. 실제 선택 규칙은 개발 자료에서 정하고 별도 미노출 자료로 확인한다.
기존 평가37장은 반복 노출된 회귀 자료이며 시간 재측정이나 결합 p95 예측은 없다.

사용자 요청에 따라 코드 작성 중 로컬 검증·추론·설치 및 런팟 직접 실행은 수행하지
않았다. 제공된 결과를 사용자가 런팟에서 읽는 실행만 안내한다.
