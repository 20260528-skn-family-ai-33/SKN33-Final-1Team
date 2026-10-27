# 런팟 모델 검수 연결

로컬 검수 API에서 OCR은 기존 설정을 사용하고, 런팟에서 Qwen 3.5 4B의 조건 추출과 SigLIP2의 8개 이미지 범주 판정을 추가한다. 텍스트 실험의 `structure` 프롬프트, 비샘플링, 검증 실패 시 최대 1회 재요청을 사용한다. 기준별로 별도 요청하므로 기존 평가와 입력 구성이 달라지며 기존 점수가 재현된다고 가정할 수 없다.

이미지 분류기는 범주별 가능성만 제공한다. 특정 대상·수량·위치·조건 충족 여부는 자동 확정하지 않는다. 불명확한 조건은 확인 필요로 남긴다. 기존 37장 평가셋과 텍스트 합성 평가셋은 독립적인 실서비스 성능 증거가 아니다.

이 연결은 현재 `/api/reviews` 로컬 검수 경로에만 적용한다. 별도의 PostgreSQL `/api/v1` 검수 워커, PDF에서 조건을 작성하는 기존 기능, OCR 모델은 변경하지 않는다. 모델 파일과 학습 결과는 Git에 포함하지 않는다.

## 런팟

런팟 터미널에서 기존 `experiments/visual_classifier_benchmark/.venv`와 두 모델 파일을 사용한다. `REVIEW_MODELS_TOKEN`은 32자 이상의 영문·숫자 비밀 문자열로 정하고 외부에 공개하지 않는다. 다음 명령은 모델을 첫 요청 때만 읽는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
read -rsp '모델 서버 비밀 문자열: ' REVIEW_MODELS_TOKEN; echo
export REVIEW_MODELS_TOKEN
.venv/bin/python -B serve_review_models.py --port 8765
```

서버는 런팟의 `127.0.0.1:8765`에서만 수신하며 사용자 데이터를 요청 사이에 저장하지 않는다. 해당 터미널은 켜 둔다. 작업 PC의 별도 터미널에서 SSH 터널을 유지한다.

```bash
ssh -N -L 8765:127.0.0.1:8765 -p 22024 -i ~/.ssh/id_ed25519 root@63.141.33.152
```

접속 주소와 포트는 Pod 재시작 후 달라질 수 있으므로 RunPod의 현재 SSH 접속 정보와 비교한다. 서버 포트를 인터넷에 직접 공개하지 않는다.

## 작업 PC

다른 터미널에서, 동일한 비밀 문자열을 입력하고 프로젝트의 로컬 검수 앱을 실행한다. `run.sh`가 읽는 프론트엔드 빌드는 현재 작업 사본을 반영한 뒤 별도로 준비해야 한다.

```bash
cd /Users/choejiheum/SK_AI/SKN33-Final-1Team
read -rs 'REVIEW_MODELS_TOKEN?모델 서버 비밀 문자열: '; echo
export REVIEW_MODELS_TOKEN
export REVIEW_MODELS_URL=http://127.0.0.1:8765
./run.sh
```

미설정 상태에서는 기존 OCR 검수만 수행하며 결과 화면에 모델이 연결되지 않았다고 표시한다. 연결을 설정한 상태에서 모델 서버가 실패하면 검수 실행이 실패로 표시되고, 모델 결과가 있는 것처럼 처리하지 않는다. 최초 모델 적재·여러 항목의 텍스트 추론을 포함한 전체 속도는 미측정이다.

결과는 항목별 요구조건 초안과 이미지 범주를 보여 준다. `REVIEW_MODELS_URL`은 숫자 loopback 주소만 허용하고 프록시와 리디렉션은 사용하지 않는다. 이 기능은 아직 실제 RunPod 연동이나 UI 실행 검증이 완료되지 않았다.
