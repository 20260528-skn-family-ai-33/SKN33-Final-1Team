# Open Images 악기 후보 다운로드

기존 Python 환경에서 Open Images의 선택한 악기 클래스만 내려받아 검토용 후보를 만든다.
Python 3.10+ 표준 라이브러리만 사용한다. 패키지 설치, 모델 로딩·학습·평가, 기존 CSV/패킷 변경을 하지 않는다.

## 다운로드 범위와 용량

- 기본값: Guitar, Piano, Drum, Violin, Cello, Flute, Saxophone, Trumpet 각 40장, 이미지 ID 중복 없이 총 320장 선택.
- `--classes`와 `--per-class`로 바꿀 수 있다. 공식 클래스 이름은 `classes` 명령으로 확인한다.
- 외부 `train` 분할의 사람이 확인한 객체 박스를 이용한다. `IsGroupOf=1` 박스는 제외한다.
- 작은 객체·가림·그림은 제외하지 않고 박스와 `IsDepiction`, `IsOccluded`, `IsTruncated` 정보를 보존한다.
- 원본 장면 전체를 받고 자동 크롭·포스터 합성·증강은 하지 않는다.
- V7 공식 다운로드 페이지에서 연결하는 V6 train 박스 CSV(2,258,447,590 bytes)와 이미지 출처 CSV(638,407,721 bytes)를 사용한다.
  최초 실행은 **약 2.90GB의 공통 메타데이터와 선택한 이미지**를 받는다. 전체 이미지 데이터셋을 받는 것은 아니다.
  메타데이터는 기본 `/workspace/datasets/.openimages-cache`에 저장해 다음 실행에서 재사용한다.
- HTTP Range를 지원하는 서버에서는 중단된 `.part` 파일부터 재개한다. 완료 파일은 저장한 크기·SHA-256을 확인해 재사용한다.
  이미지 파일은 크기 제한과 JPEG 헤더를 확인한다. 실제 디코딩·시각 검토까지 완료한 것으로 표시하지 않는다.

## 작업 PC에서 Git에 반영

코드는 로컬 저장소에 추가되어 있다. 아래 커밋·push는 사용자가 실행하는 명령이다.

```bash
cd /Users/choejiheum/SK_AI/SKN33-Final-1Team &&
git add \
  experiments/visual_classifier_benchmark/download_openimages_instruments.py \
  experiments/visual_classifier_benchmark/download_openimages_instruments.sh \
  experiments/visual_classifier_benchmark/OPENIMAGES_INSTRUMENTS.md &&
git commit -m "feat(infra): Open Images 악기 이미지 선별 다운로드 추가" &&
git push origin dev/12_runpod
```

## RunPod에 코드 받기

기존 RunPod 체크아웃에서 브랜치가 갈라졌던 이력이 있어 새 파일3개만 원격 브랜치에서 가져온다.
기존 브랜치·학습 코드·데이터·환경·결과는 이 명령의 대상이 아니다.

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/download_openimages_instruments.py \
  experiments/visual_classifier_benchmark/download_openimages_instruments.sh \
  experiments/visual_classifier_benchmark/OPENIMAGES_INSTRUMENTS.md
```

## RunPod 실행

기존 benchmark 환경을 그대로 사용한다. 설치 명령은 없다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark

bash download_openimages_instruments.sh classes

bash download_openimages_instruments.sh download \
  --classes Guitar Piano Drum Violin Cello Flute Saxophone Trumpet \
  --per-class 40 \
  --out /workspace/datasets/openimages-instruments-v1 \
  --workers 4 \
  --resume

bash download_openimages_instruments.sh show-results \
  --out /workspace/datasets/openimages-instruments-v1
```

기존 Python 경로를 직접 지정해야 한다면 아래처럼 실행한다.

```bash
BENCH_PYTHON=/workspace/model-download-venv/bin/python \
  bash download_openimages_instruments.sh download \
  --classes Guitar Piano Drum Violin Cello Flute Saxophone Trumpet \
  --per-class 40 \
  --out /workspace/datasets/openimages-instruments-v1 \
  --resume
```

이 명령도 기존 실행 파일을 선택할 뿐 환경을 만들거나 설치하지 않는다.

같은 설정으로 재실행할 수 있다. 클래스·수량·seed를 바꾸면 새 `--out`을 지정한다.
다운로드 실패나 동일 바이너리 중복은 `incomplete`와 종료 코드2로 보고한다. 성공한 이미지는 다시 받지 않으며 같은 명령으로 실패분을 재시도한다.
중복이 남으면 클래스·seed를 조정한 별도 출력을 만든다. 다른 이미지를 자동으로 추가해 고정한 선택 집합을 바꾸지 않는다.

## 출력과 라벨 검토

| 파일 | 내용 |
| --- | --- |
| `images/*.jpg` | 선택한 클래스의 이미지만 저장 |
| `selection.json` | 고정 설정·선택 ID·원본 메타데이터·입력 CSV 취득 기록 |
| `selected_images.csv` | 클래스별 할당과 함께 포함된 다른 선택 대상 클래스 |
| `selected_boxes.csv` | 정규화한 객체 위치와 그림·가림 등의 속성 |
| `download_manifest.json` | 취득 결과·SHA-256·저자·License·원본 URL·미승인 및 디코딩 미검사 상태 |
| `additional_labels.csv` | 기존 NEW_FIELDS 순서의 8라벨 초안. 전부 `UNREVIEWED` |
| `review.html` | 이미지·클래스·저자·원본 출처를 보는 검토용 목록 |
| `download_summary.json` | 수량·실패·중복·학습 미실행 등의 요약 |

`musical_instrument=1`은 Open Images의 사람이 확인한 박스에서 만든 **초안**이다.
나머지 7개 라벨은 없다고 간주하지 않고 `?`로 둔다. 마이크·스피커·음표만 있는 이미지를 악기 양성으로 넣지 않는다.
우리 기준으로 8개 라벨과 이용 조건을 확인한 뒤 `approval`, `reviewer`, `reason`을 기록한다.
재개할 때 사람이 편집했을 수 있는 `additional_labels.csv`는 덮어쓰지 않고 재생성한 행은 `additional_labels.generated.csv`에 쓴다.
처음에 실패한 이미지가 나중에 복구되면 재생성 CSV에서 추가 성공 행만 기존 CSV에 반영한다.

이 출력만으로는 기존 `build-data`에 전달할 review 한 벌이 완성되지 않는다.
승인 후 기존 review에 추가 행과 이미지를 병합하는 작업을 별도로 수행한다.
**기존32셀 수정이 담긴 `train_corrections.csv`를 유지**하고 기존12장도 남긴다면 해당 추가 라벨을 함께 유지한다.
이미지 단위의 임시 group_id는 Open Images ID이므로 같은 저자·장면의 다른 이미지까지 식별한 것은 아니다.
기존 이미지와의 바이너리·픽셀·동일 장면 중복과 분할 누출은 학습 패킷 생성 전에도 확인한다.

Open Images 객체 사진은 실제 포스터와 분포가 다르다.
다운로드 완료는 학습 완료·90% 달성·개별 이미지의 학습 이용 허용 확인을 뜻하지 않는다.
이 다운로드 처리에서는 기존 개발/평가셋을 읽거나 쓰지 않는다.

## 공식 출처

- V7 다운로드·CSV 명세: https://storage.googleapis.com/openimages/web/download_v7.html
- 클래스 목록: https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv
- 공식 다운로더와 공개 S3 객체의 구조: https://github.com/openimages/dataset/blob/master/downloader.py
- 라이선스 설명: https://storage.googleapis.com/openimages/web/factsfigures_v7.html#licenses

주석은 CC BY 4.0, 이미지는 CC BY 2.0으로 수록되지만 공식 사이트도 이미지별 라이선스 확인을 요구한다.
`License` 열은 원본의 기록을 보존하는 것이며 코드가 개별 이용 권한을 확인했다는 뜻이 아니다.

## 검증 상태

사용자 지정에 따라 새 코드의 로컬 실행·구문 검증·테스트와 RunPod 직접 실행은 하지 않았다.
공식 클래스 목록·CSV 헤더·배포 URL·Content-Length를 읽어 코드와 절차에 반영했다.
