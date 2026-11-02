# 결과 내보내기 → 작업 PC 검증 → 결과보고서

진행 중인 학습과 기존 결과 파일을 수정하지 않는 **별도 후처리 도구**입니다.
학습 모듈, torch, transformers, numpy를 import하지 않으며 재설치·GPU 실행·SSH 접속을 수행하지 않습니다.
보고서는 기존 `09_RunPod_OCR_모델_비교결과서.html`의 색상·목차·카드·표·인쇄 양식을 재사용합니다.

## 1. 완료 후 RunPod에서 내보내기

현재 학습은 그대로 둡니다. 아래 코드를 RunPod에 반영하고 내보내는 작업은 **학습 완료 후** 진행합니다.
기존 Git 브랜치가 일치하는지 확인하고 새 도구를 반영하세요. 결과 파일은 Git의 `runs/` 제외 규칙에 따라 push/pull 대상이 아닙니다.

필요한 도구는 `resultlib.py`, `export_results.py`, `verify_results.py`, `build_result_report.py`, `result_report.css`입니다.
기존 `finetune_siglip*.py`, `run_experiment.py`, `encoders.py`, `benchlib.py`는 변경하지 않습니다.

RunPod도 `dev/12_runpod` 브랜치라면 학습 완료 후 `git pull --ff-only origin dev/12_runpod`로 반영합니다.
앞선 로그처럼 다른 브랜치에 있다면, 완료 후 아래 명령으로 **후처리 도구5개를 함께** 가져올 수 있습니다. 기존 학습 코드는 덮어쓰지 않습니다.

```bash
cd /workspace/SKN33-Final-1Team
git fetch origin dev/12_runpod
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/resultlib.py \
  experiments/visual_classifier_benchmark/export_results.py \
  experiments/visual_classifier_benchmark/verify_results.py \
  experiments/visual_classifier_benchmark/build_result_report.py \
  experiments/visual_classifier_benchmark/result_report.css
```

**RunPod 터미널:**

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark

python3 -B export_results.py \
  --out /workspace/result_exports/visual-results-2026-10-24.tar.gz
```

- 기본 입력은 `runs/heads-fp32`, `runs/siglip-full-fp32`, `runs/siglip-focused-fp32`입니다.
- 세 실행의 `status.json`이 모두 `complete`여야 합니다. 하나라도 실행 중/실패/누락이면 내보내지 않습니다.
- 승인 데이터 메타데이터·분할·라벨, 평가 이미지37장, 실행 환경/프로토콜/선택 파일, 개발 예측, 후보별 학습 이력, 평가 예측/지표, 원시 시간 기록, 학습 코드 원본을 포함합니다.
- SHA256을 검사하고 복사 중 결과가 바뀌면 중단합니다. 원본 실행 폴더를 수정하거나 lock을 가져가지 않습니다.
- 기본은 큰 모델 가중치를 제외한 보고서용 묶음입니다. 제외한 체크포인트의 SHA256은 manifest에 기록합니다.
- 결과는 `.tar.gz`와 `.tar.gz.sha256` 두 파일입니다. 기존 파일은 덮어쓰지 않습니다.

선택된 체크포인트도 보존하려면 파일 이름을 바꾸고 옵션을 지정합니다. 설치·재학습은 없습니다.

```bash
python3 -B export_results.py \
  --checkpoints selected \
  --out /workspace/result_exports/visual-results-with-selected-2026-10-24.tar.gz
```

`--checkpoints all`은 완료 후보의 모든 체크포인트를 포함합니다. 수GB가 될 수 있습니다.
`--no-images`는 평가 이미지를 제외합니다. 이 경우 보고서에 오류 ID·라벨은 표시하지만 이미지 대신 미포함 안내를 표시합니다.
입력 경로가 다르면 `--runs-root`와 `--bundle`을 지정합니다. 입력/코드 폴더 내부에 출력 압축 파일을 만들 수 없습니다.

## 2. 작업 PC로 다운로드

**아래는 Mac 작업 PC 터미널에서 실행합니다.** 마지막으로 제공한 RunPod SSH 주소/포트 기준입니다.

```bash
mkdir -p /Users/choejiheum/Downloads/visual-results-2026-10-24

scp -P 22024 -i ~/.ssh/id_ed25519 \
  root@63.141.33.152:/workspace/result_exports/visual-results-2026-10-24.tar.gz \
  root@63.141.33.152:/workspace/result_exports/visual-results-2026-10-24.tar.gz.sha256 \
  /Users/choejiheum/Downloads/visual-results-2026-10-24/
```

이 단계는 모델 실행 명령이 아니라 결과 파일 복사입니다.
Pod를 교체했다면 RunPod에 표시된 실제 SSH 주소/포트에 맞춰 변경합니다.

## 3. 작업 PC에서 검증·압축 해제

작업 PC에도 이번 도구 코드가 반영되어 있어야 합니다. Python3 표준 라이브러리만 사용합니다.

```bash
cd /Users/choejiheum/SK_AI/SKN33-Final-1Team/experiments/visual_classifier_benchmark

python3 -B verify_results.py \
  /Users/choejiheum/Downloads/visual-results-2026-10-24/visual-results-2026-10-24.tar.gz \
  --extract-to /Users/choejiheum/Downloads/visual-results-2026-10-24/evidence \
  --out /Users/choejiheum/Downloads/visual-results-2026-10-24/verification.json
```

압축 해제 폴더는 새 경로여야 합니다. SHA256, 파일 목록, 중복 이름, 경로 이탈, 링크, 기본16GiB 용량 제한을 확인한 뒤 새 폴더에만 해제합니다.
검증 JSON은 원본 증거 폴더 바깥에 저장합니다.

검증 항목:

- 프로토콜/선택/완료 파일의 연결, 학습 소스 fingerprint, GPU·패키지 환경 일치
- 승인 데이터 분할·그룹 경계, 평가 이미지 ID/라벨 순서, known/unknown 마스크
- 저장 확률과 선택 임계값의 판정 일치 및 정확도·정밀도·재현율·F1·AP 재계산
- 각 이미지의 반복 수와 원시 시간 합계, 평균·중앙값·p95 재계산
- 후보별 개발 예측으로 임계값·개발 지표·최종 선택 재검증
- 이전 모델의 판정 재현 및 개선/퇴행 기록 확인

`verified_report_evidence`는 **저장 산출물 검증**입니다. GPU 재추론이나 가중치 텐서 비교를 실행했다는 뜻이 아닙니다.
가중치 미포함 시 바이트 검증은 제외되며, 포함 시 파일 SHA256을 검증합니다. 본체 파라미터 변화는 학습 때 저장한 기록과 독립 텐서 비교를 구분해 표시합니다.

## 4. 작업 PC에서 보고서 생성

```bash
python3 -B build_result_report.py \
  /Users/choejiheum/Downloads/visual-results-2026-10-24/evidence \
  --out /Users/choejiheum/Downloads/visual-results-2026-10-24/report
```

`report/report.html`을 브라우저로 엽니다. 상단의 **인쇄 · PDF 저장**으로 PDF를 저장할 수 있습니다.
출력 폴더 전체를 보존하면 SVG 그래프·CSV·검증 JSON·원시 지표 JSON도 함께 전달할 수 있습니다.
보고서 출력은 원본 증거 폴더와 분리해야 하며 기존 폴더를 덮어쓰지 않습니다.

- 기존 PC Python에 matplotlib가 있으면 정확도/p95 비교와 개발 Macro AP 학습 곡선을 SVG로 생성합니다.
- matplotlib가 없으면 표·CSV·오류 이미지 보고서를 생성하고 그래프 생략을 표시합니다. 자동 설치하지 않습니다.
- `--no-figures`는 그래프를 명시적으로 생략합니다. `--title`은 보고서 제목을 지정합니다.

보고서에는 실험 조건, 실행별 성능, 같은 실행에서 재측정한 이전 모델 대비 변화, 라벨별 FP/FN·정밀도·재현율, 개발 선택 이력, 오류 이미지와 개선/퇴행, 검증 범위와 한계가 포함됩니다.

목표는 **라벨 항목 정확도90% 이상 및 p95 50ms 이하**입니다. 공통 결과의 `all_met`에 포함된 중앙값30ms 조건은 적용하지 않습니다.
`source=prior`이면 기존 가중치 유지 또는 임계값 변경으로 명시합니다. 새 본체 학습의 성능 개선으로 바꾸어 쓰지 않습니다.
같은 평가37장을 재사용한 결과이며 OCR·네트워크·DB·모델 로딩 시간은 제외합니다.

## 로컬 검증

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_result*.py' -v
```

새 도구 테스트는 합성 완료 산출물을 사용합니다. 실제 RunPod/GPU 성능을 측정하지 않습니다.
