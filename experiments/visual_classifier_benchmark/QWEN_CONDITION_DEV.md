# Qwen 4B 중복·모호성 처리 개발 비교

## 변경 범위

`structure`는 기존 구조물 프롬프트, `conditions`는 이를 기반으로 중복 조건과
모호한 대상의 처리를 보강한 별도 프롬프트다. 기존 B-4 및 structure 프롬프트,
28건 정답 초안과 저장된 결과는 유지한다. 가중치 변경이나 추가 학습은 없다.

모호한 부분만 `needs_review`로 남기고 같은 문장의 명확한 조건은 추출하도록 한다.
단어를 코드로 특정 라벨로 강제 변환하지 않는다. 양수 최소·정확 수량과 그 포함
표현은 한 조건으로 출력하게 하되, 최대 수량·0개·독립된 위치 및 색상 조건은
무조건 합치지 않는다.

별도 후처리는 검증된 응답에서 원문 시작·끝 위치와 type, field, target,
operator, value, source가 모두 같은 항목만 제거한다. 원본 응답은 보존하고
삭제 index와 보존 index를 기록한다. 수량+required, 서로 다른 필드, 대상 표현
축약 및 모호성 판단에는 자동 수정을 적용하지 않는다.

## RunPod 실행

기존 `.venv`와 `/workspace/models/qwen3.5-4b`를 사용한다. 설치·다운로드 없음.
기존 `runs/qwen4b-requirements-retry-v1`의 B-4 모델·프롬프트 기록을 확인한다.
다음 명령은 학습이 아니라 두 프롬프트의 새 추론·평가다.

```bash
bash run_condition_dev.sh preflight
bash run_condition_dev.sh run --ram-policy clean-unmapped-file --confirm-draft --resume
bash run_condition_dev.sh show-results
bash run_condition_dev.sh show-errors
```

결과 기본 경로: `runs/qwen4b-condition-dev-v1`. 기존 structure 결과 경로로
덮어쓰지 않는다. `--resume`는 같은 코드·데이터·모델·설정의 캐시만 재사용한다.
코드를 바꾼 뒤에는 새 run-dir을 지정한다. 같은 완료 실행을 다시 호출하면 추론을
반복하지 않는다. 기존 28건은 이미 노출된 개발 회귀 사례이며 독립 평가셋이 아니다.

## 비교 행과 진단

- `first`: 첫 응답 그대로.
- `final`: JSON·스키마·출처 검증 실패에만 최대 1회 재요청한 선택 응답.
- `normalized`: final에 exact 중복 제거 후처리를 적용한 응답. 별도 모델 호출 없음.

두 프롬프트 모두 세 단계를 표시하므로 프롬프트 개선과 후처리 효과를 구분할 수 있다.
final 시간은 재요청을 포함하고 normalized 시간은 실제 후처리 시간까지 더한다.
모델 로딩·웜업·보고서 저장 시간은 제외한다. 토큰 예산은 그대로 유지하며 입력을
잘라서 통과시키지 않는다. 재요청 입력이 예산을 넘으면 skip으로 기록한다.

`comparison.csv`: 라벨 정확도/F1, 조건 일치율/항목 F1, 검토 요청 TP/FP/FN,
중복 항목 수, 수량+포함 중복 의심 수, 상충 객체 필드 수, 미확인 정답 칸의 양성
출력 수 및 p95. 미확인 정답 칸의 출력은 **오탐률이 아닌 검토 신호**다.

`condition_audit.csv`: 사례별 누락/추가 조건, 중복과 검토 요청 오류.
`unknown_label_predictions.csv`: 정확도/F1에서 제외된 칸에 양성을 출력한 목록.
`predictions.json`: 원본 first/retry, 선택 응답, 후처리 결과와 삭제 내역.

정답 초안은 synthetic=true, human_verified=false다. D009/D010/D027 등의 대상
표현 기준은 자동 변경하지 않는다. 정확도/F1이 높아도 모호성·조건 오류가 남을 수
있으므로 조건 지표와 audit를 같이 보고, 최종 성능은 사람이 검토한 미노출 사례로
확인한다. 이번 구현은 사용자 요청에 따라 로컬 테스트·검증을 실행하지 않았다.
