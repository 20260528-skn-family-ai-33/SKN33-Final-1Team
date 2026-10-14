# 포스터 라벨링과 전처리

여러 포스터의 새 비교 작업은 [OCR 기준선 비교 도구](ocr-benchmark.md)를 사용합니다. `scripts.benchmark_ocr`는 실행별 폴더와 고정 분할을 사용하며 웹과 같은 **정규화 원본 좌표**를 보존합니다. 아래 기존 김천 샘플 CLI의 **EXIF 역변환 후 저장 픽셀 좌표**와 구분합니다. 첫 실제 측정은 [기준선 보고서](ocr-baseline-2026-10-13.md)에 있습니다.

Paddle 단독 경로는 `--pipeline paddle-only --variants rgb_2x`로 비교합니다. 전체 이미지에서 Paddle 자체 탐지·인식을 수행하며, `paddle`(EasyOCR 탐지 + Paddle 재인식)과 구분합니다. 기존 이미지·라벨·이전 결과는 보존하고 매 실행마다 새 결과를 만듭니다. 설치·운영 판정·실측의 한계는 [Paddle 단독 보고서](ocr-paddle-only-2026-10-13.md)를 따릅니다.

원본 포스터는 `data/img/`에서 읽습니다. Git에는 재현 가능한 스크립트와 `data/labels/gimcheon_gimbap_2026/`의 작은 JSON 초안만 추가합니다. 변형 이미지, crop, OCR 결과와 검증 보고서는 `artifacts/`에 생성하며 Git에서 제외합니다. 모델, 가상환경, 과거 Apple Vision 결과는 이 데이터 파이프라인에 포함하지 않습니다.

모든 명령은 저장소 루트에서 실행합니다. Python 설치와 의존성 구성은 루트 README를 따릅니다.

## 라벨 초안 확인

```bash
python -m scripts.run_sample --labels-only
```

기본 출력은 `artifacts/gimcheon_gimbap_2026/`입니다. `annotations.json`, `event_reference.json`, `reference.png`, `annotation_preview.png`, `core_annotation_preview.png`, `crops/`, `validation.json`을 생성합니다. `project_inventory.json`은 `data/img/` 바로 아래 파일들의 서명·확장자·해시를 조사한 결과입니다.

라벨 12개는 원본 600×848 픽셀 좌표의 사각형 초안입니다. 행사명, 날짜, 장소와 눈으로 확인한 일부 문구를 담으며, 읽기 어려운 작은 글씨는 `null`입니다. `human_verified=false`, `use_for_training=false`, `review_status=needs_human_review`를 유지합니다. 사람의 확인 전에는 학습 정답이나 성능 평가의 정답으로 사용하지 않습니다. 이미지에 나타나지 않은 주최자·연락처·이용료 등도 `null`로 남깁니다.

고정 라벨은 김천 포스터의 SHA-256과 일치할 때만 생성됩니다. 다른 포스터에 같은 정답이 잘못 적용되면 작업을 중단합니다. 버전 관리되는 JSON의 `crop` 경로는 생성 산출물에서 해석합니다. crop 이미지 자체는 저장소에 복제하지 않습니다.

## 실제 OCR과 전처리 비교

```bash
python -m backend.app.ocr_runtime --download-models
python -m scripts.run_sample
```

모델 다운로드는 명시적인 준비 단계입니다. OCR 엔진은 EasyOCR이며 로컬 장치를 **CUDA → MPS → CPU** 순서로 확인합니다. RunPod 설정은 루트 실행 안내를 따릅니다. 원본과 RGB 2배, 회색조 2배, CLAHE 2배, 이진화 2배 입력을 비교합니다. 일부만 실행하려면 다음처럼 지정합니다.

```bash
python -m scripts.run_sample --variants original rgb_2x
```

OCR 결과는 `ocr/<variant>.json`, 엔진의 원시 결과는 `ocr/<variant>.raw.json`입니다. `preprocessing_manifest.json`에는 실제 사용 장치, 입력 크기, 해시, 성공·오류 이력이 기록됩니다. 전처리 좌표를 원본 픽셀 좌표로 환산하며 EXIF 방향 변환도 역변환합니다.

`event_auto.json`은 OCR 텍스트만으로 추출한 결과입니다. 사람이 보는 라벨 문구로 자동 결과를 채우지 않습니다. `comparison.json`의 CER와 핵심 필드 4개 일치 수는 이 한 이미지와 미승인 초안을 비교하는 진단 자료입니다. 기본 자동 추출 입력은 `rgb_2x`이며, 진단 점수가 높은 입력을 일반적인 최적 설정으로 간주하지 않습니다. OCR에서 누락되거나 틀린 문구는 수동 확인이 필요합니다.

같은 출력 폴더에서 일부 variant만 다시 실행하면 기존 다른 variant의 OCR 파일도 비교에 포함될 수 있습니다. 실행별 결과를 분리하려면 새 `--output artifacts/<run-name>` 경로를 사용합니다. `--labels-only`는 이미 있는 OCR 파일을 삭제하거나 갱신하지 않습니다.

## 다른 이미지 전처리

```bash
python -m scripts.preprocess --input ./data/img/다른포스터.png --output ./artifacts/another-poster
python -m scripts.inventory --output ./artifacts/project_inventory.json
```

`prepare_labels`, `compare_ocr`, `validate_sample`, `run_sample`은 김천 포스터 샘플용입니다. 다른 이미지에는 `preprocess`부터 사용하고 별도 라벨을 검토하여 작성합니다. 전처리와 앱의 프런트엔드 빌드는 별개입니다.

산출물은 `data/img/` 안에 쓸 수 없고, 입력 이미지를 포함하는 폴더를 출력으로 지정할 수도 없습니다. 생성 파일이 symlink를 통해 출력 폴더 밖으로 빠져나가면 중단합니다. 원본 파일명과 파일 내용은 바꾸지 않으며 NFC 정규화는 표시·경로 검색에만 사용합니다. 원본이 PNG인데 확장자가 JPG인 경우에도 실제 파일 서명으로 읽습니다.

저장소 내부 원본 경로는 `path_base=project_root`와 상대경로로 기록합니다. 다른 위치의 원본은 개인 절대경로를 저장하지 않고 `path_base=external_input`과 파일명만 기록합니다. 그 경우 원본 검증 시 `--input`을 다시 지정해야 합니다. `validate_sample --project <repository-root>`로 기준 저장소를 명시할 수 있습니다.
