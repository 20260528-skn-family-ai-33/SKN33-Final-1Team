# 지역행사 포스터 검수 스키마 v0.1 (기본 틀)

멘토링 피드백(2026-10-06)을 바탕으로 만든 **초안**입니다. 확정안이 아니라 팀이 고치며 쓰는 출발점입니다.
**JSONL 한 줄 = 케이스 하나**이고, 한 줄 안에 입력(`input`), 정답(`gold`), 라벨 정보(`label_meta`)가 함께 들어 있습니다. 멘토 메모의 "입력·출력 약 100쌍"은 이 JSONL 약 100줄에 해당합니다.

| 파일 | 내용 |
| --- | --- |
| `poster-case.schema.json` | 케이스 한 줄의 JSON Schema (2020-12) |
| `check-catalog.json` | 검수 항목 정의 16개(핵심 10 + 확장 6), 판정 4종, 이유 코드 |
| `examples/poster-cases.example.jsonl` | 예시 2줄: 김천김밥축제 포스터 원본, 오류 2개를 주입한 변형(설명용) |
| `examples/EVT-0001.pretty.json` | 위 첫 줄을 사람이 읽기 좋게 펼친 것 |
| `validate_cases.py` | 스키마 검증 + 스키마로 못 잡는 정합성 검사 |

## 구조

```
케이스 (JSONL 한 줄)
├─ schema_version · case_id · domain(regional_event) · media_type(image)
├─ source          출처·권리·(변형이면) 어떤 오류를 어디에 주입했는지
├─ input
│   ├─ image       path · 크기 · 형식 · sha256
│   └─ reference   (선택) 기준 정보: event · design_guide(컬러·폰트·로고) · required_fields
├─ gold            ← 기대 출력 = 정답
│   ├─ extraction
│   │   ├─ texts   글자 영역: id · role · 전사 · bbox · 판독 가능도 (· font · color)
│   │   ├─ objects 로고·캐릭터·일러스트 등
│   │   └─ fields  구조화 정보(제목·날짜·시간·장소·기관·문의처·입장료) + 근거 text_ids
│   └─ checks      검사별 판정: check_id · verdict · reason_code · targets · observed/expected · error
└─ label_meta      라벨 상태 · 쓴 카탈로그 버전 · 작업자 id
```

## 멘토 피드백 → 스키마 대응

| 피드백 | 반영한 곳 |
| --- | --- |
| 검수 요소: 오탈자, 날짜·시간, 폰트 | 카탈로그 `TXT-*`, `DT-*`, `FNT-*` (`priority: core`) |
| 컬러 시스템 정의 | `input.reference.design_guide.palette`, 카탈로그 `CLR-01`. 팔레트 **내용**은 아직 정하지 않았다 |
| 제목·날짜·내용을 JSON으로 구조화 | `gold.extraction.fields` (+ 글자별 `texts`) |
| 입력 자료 + 기대 출력 + 정답·라벨 | `input` / `gold` / `label_meta` |
| 무엇이 잘못됐고 어떤 결과가 나와야 하는지 | `checks[].verdict` + `error`(유형·설명·정정안) + `targets`(위치) |
| 사례를 사람이 쓴 것처럼 자연스럽게 | `source.origin`(실제 / 합성 / 변형), `perturbations`(주입한 오류의 기록) |
| 이미지·동영상 전처리 경로 분리 | `media_type`. v0.1은 `image`만 정의하고 `video`는 예약 |
| 도메인 어댑터 | 도메인마다 바뀌는 부분을 `gold.extraction.fields`와 `input.reference.event`로 모았다. F&B 등은 이 두 곳을 교체해 추가 |
| 데이터를 판매 | `source.rights`(케이스별 권리 상태), `label_meta.label_status`(AI 초안 / 사람 검토 / 합의) |

## 설계 원칙

1. **전사와 판정을 분리한다.** `extraction`은 포스터에 적힌 대로 옮긴다. 오타가 있어도 고치지 않는다. 오류인지 아닌지는 `checks`에서만 판정한다. 변형 예시(`EVT-0001-v1`)의 `사명대사공윈`이 그 예다.
2. **`null`은 "포스터에 없다"는 뜻이다.** `fields`는 모든 키를 반드시 적게 해서 "없음"과 "라벨링 안 함"을 구분한다.
3. **구조화한 값에는 근거를 붙인다.** 모든 값은 `text_ids`로 글자 영역 좌표와 연결된다. 오류 위치 표시와 평가에 그대로 쓸 수 있다.
4. **요약은 저장하지 않고 계산한다.** 전체 결과는 `check-catalog.json`의 `derived_overall_rule`로 계산한다(아래 "참고한 스키마" 참조).
5. **판정은 4종, 이유는 코드로 나눈다.** `pass / fail / needs_review / not_applicable`은 앱의 충족 / 미충족 / 확인 필요 / 해당 없음과 맞춘 것이다. `needs_review`는 `reason_code`(기준 정보 필요, 사람 판단, 판독 신뢰 낮음, 이미지만으로 불가, 권장 정보 누락)를 반드시 적는다.
6. **검수 항목의 정의는 케이스 밖에 둔다.** `check-catalog.json`을 따로 두고 버전을 매겨, 항목을 바꿔도 이미 만든 케이스의 의미가 흔들리지 않게 한다.
7. **라벨링한 검사만 `checks`에 넣는다.** 빠진 검사는 "정답 없음"이라 평가에서 뺀다.

## 참고한 ChatGPT 스키마와의 차이

멘토님이 받은 `message (13).txt`는 **참고만** 했고 구조는 따르지 않았습니다.

| 항목 | ChatGPT 스키마 | 이 틀 |
| --- | --- | --- |
| 추출값 | 값만 나열(`extracted_data`) | 글자별 좌표·판독 가능도 + 값마다 근거 id |
| 검수 항목 | 케이스마다 48개를 문자열로 나열, 정의 없음 | 카탈로그로 분리, 16개로 시작 |
| 판정 | PASS / FAIL / REVIEW / NEEDS_VERIFICATION / NOT_DETERMINABLE | 4종 + `reason_code` (REVIEW·NEEDS_VERIFICATION·NOT_DETERMINABLE의 구분은 이유 코드로 보존) |
| 요약·종합 판정 | 값으로 저장 | 저장하지 않고 계산 |
| 오류 위치·정정안 | 없음 | `targets` + `error.correction` |
| 기준 정보 | 항목마다 `reference` 문자열 | `input.reference`로 분리, 검사별 `requires_reference` |
| 오류 주입 | 없음 | `source.perturbations` |
| 정답 vs 모델 출력 | 구분 없음 | 이 스키마는 정답만. 모델 출력은 별도 스키마로 분리 예정 |

해당 파일을 확인해 보니 요약이 목록과 맞지 않았습니다. 목록은 48개(PASS 13 · NEEDS_VERIFICATION 19 · REVIEW 14 · NOT_DETERMINABLE 2)인데 `inspection_summary`는 46개(3 · 15 · 8 · 20)로 적혀 있었습니다. 또 PASS 13개 가운데 판정 방법(`method`)이 적힌 것은 3개뿐이었습니다. 요약을 저장하면 이런 불일치가 생기므로 계산하도록 했고, 판정마다 `observed`와 `targets`를 남기게 했습니다.

**이번 틀에서 뺀 것:** 법규(허위 행사 정보, 저작권·상표권, 필수 고지), 이미지 품질(해상도·압축), 이용자 안내(우천 시, 접근성). 외부 기준 데이터나 범위 확정이 먼저 필요해서입니다. 카탈로그에 항목을 추가하면 확장됩니다.

## 예시 2건

- **`EVT-0001`**: 제공받은 김천김밥축제 포스터. `texts` 12개, `objects` 10개, 검사 13개. 기준 정보(`reference`)가 없어 기준이 필요한 검사는 `needs_review(needs_reference)`입니다. 사람 검토 전의 AI 초안(`draft_ai`)입니다.
- **`EVT-0001-v1`**: 위 포스터에 오류 2개(요일 `금→토`, 장소명 오타)를 주입한 **설명용** 케이스입니다. 오류가 `perturbations`와 `fail` 라벨에 어떻게 대응하는지 보여 주며, 이미지는 만들지 않았습니다(`sha256: null`).

## 아직 정하지 못한 것

멘토 메모에서 확인이 필요하다고 표시된 것 — 스키마에서는 **가정하지 않았습니다**.

| 항목 | 스키마에 미치는 영향 |
| --- | --- |
| `주간사항`의 의미 | 필드를 만들지 않았다. 의미를 확인한 뒤 추가 |
| `pre, pdd`의 의미 | 스키마와 무관하게 보류 |
| 쇼핑몰 숏폼이 2차 구현인지 확장 사례인지 | `media_type: video`만 예약. 영상 입력 구조는 미정 |
| 데이터 판매의 대상(데이터셋 vs 검수 결과) | 권리·라벨 상태 필드만 준비. 판매 단위(케이스 묶음, 버전)는 미정 |
| 지도학습(추가 학습)을 할지, 정답으로 평가만 할지 | 학습이면 train/val/test 분할 필드가 필요한데 아직 없다 |
| "구축 사이즈"가 가리키는 범위 | 100쌍 = 100줄로 가정하지 않았다. 변형 케이스를 건수에 포함할지 협의 필요 |

우리 쪽에서 정해야 할 것:

- **데이터 출처와 권리.** 예시의 실제 포스터는 권리가 확인되지 않았습니다. 데이터를 판매할 계획이면 처음부터 합성하는 쪽이 안전합니다(`source.rights`로 케이스마다 추적).
- **폰트·컬러 정답을 어떻게 만들지.** 합성 포스터는 생성기가 정확한 값을 줄 수 있지만(`attr_source: generator`), 실제 포스터는 이미지에서 폰트를 판별하기 어려워 사람 라벨이 필요합니다.
- **컬러 시스템의 내용**(팔레트·허용 오차)과 **평가 지표**. 지표는 필드별 일치율, 검사별 정밀도·재현율 같은 후보만 있고 정해진 것이 없습니다.
- 대비(`CLR-02`)처럼 **장식 글자(외곽선·그림자)를 어떻게 측정할지**.

## 이 틀이 하지 않는 것

모델 출력 저장과 GPT·Claude·Gemini 비교, As-Is / To-Be 검수 시간 측정(두 가지는 별도 스키마로 분리해 `case_id`로 연결할 계획), 영상, 학습 분할, 한국어 외 포스터.

## 다음 단계 제안 (멘토 순서 기준)

1. 카탈로그 `core` 10개 중 이번에 검수할 범위 확정
2. 데이터 확보 가능성과 권리 확인 → 실제 vs 합성 결정
3. 이 스키마 보완(위 미정 항목 반영)
4. 5~10건 소규모 JSONL과 변형 케이스, 사람 검토로 `human_reviewed` 전환
5. 규모 협의 → 모델 비교용 `run-result` 스키마 → 도입 전후 시간 측정 스키마

## 사용법

```bash
.venv/Scripts/python.exe docs-draft/poster-schema/validate_cases.py
.venv/Scripts/python.exe docs-draft/poster-schema/validate_cases.py 내_케이스.jsonl
```

검사하는 것: 스키마 통과, id 참조 존재, 날짜·요일 계산 일치, 기준 정보가 없는데 `pass`로 적은 검사, 변형 케이스의 오류 주입과 라벨 일치. `jsonschema`는 `requirements.lock.txt`에 이미 포함되어 있습니다.

파일은 모두 커밋하지 않은 로컬 초안입니다(`docs-draft/`).
