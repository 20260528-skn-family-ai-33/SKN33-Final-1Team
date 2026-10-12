# C04 · 맑은결 주방세제 레몬

- 사례군: 일반 소비재  |  그룹 ID: G04  |  가상 자료(실존 브랜드와 무관)
- 설계 의도: 조건 10개. 브랜드 가이드가 로고 여백·제품 외형을 규정. 문구 사용 시에만 시험 성적서가 필요한 조건부 증빙, 이미지는 그대로 증빙만 추가하는 수정 시나리오를 시험.
- 사용 계획: 매체 instagram_feed · 기간 2026-11-03 ~ 2026-11-30 · 지역 대한민국 · 유료광고 True

## 문서
| 문서 | 쪽수 | 파일 |
|---|---|---|
| 광고 소재 제작 명세서 | 2 | input/docs/C04_spec.pdf (+ .md) |
| 광고 제작 용역 계약서(요약) | 1 | input/docs/C04_contract.pdf (+ .md) |
| 맑은결 브랜드 가이드 | 2 | input/docs/C04_guide.pdf (+ .md) |

## 납품 이미지
| ID | 유형 | 의도한 편집 | 파일 |
|---|---|---|---|
| D1 | 정상 | 모든 조건·증빙 충족 | C04_D1.png (1080×1080) |
| D2 | 단일 오류 | 로고 가로 1.6배 늘림(여백은 유지되도록 브랜드 글자 위치 조정) | C04_D2.png (1080×1080) |
| D3 | 복합 오류 | 브랜드 글자를 로고에 바짝 붙여 최소 여백 위반; 용량 500mL → 550mL; 효능 문구 삭제(→ 시험 성적서 조건은 해당 없음) | C04_D3.png (1080×1080) |
| D4 | 시각/개수 오류 | 제품 병이 넓은 형태로 바뀜; 라벨 색 노랑 → 파랑 | C04_D4.png (1080×1080) |
| D5 | 판독 불확실 | 용량 표기 16px·저대비; 시험 성적서 미제출(문구는 사용 중) | C04_D5.png (1080×1080) |
| D6 | 수정본(증빙만) | D5와 같은 이미지 파일에 시험 성적서만 추가 | C04_D6.png (1080×1080) |

## 검사 조건(기준 정답)
| ID | 항목 | 기대값 | 출처 |
|---|---|---|---|
| M01 | 상품명 | 맑은결 주방세제 레몬 | spec 1쪽 |
| M02 | 용량 | 500mL | spec 1쪽 |
| M03 | 브랜드 표기 | MALGEUNGYEOL | spec 1쪽 |
| M04 | 효능 문구 | 피부 자극 테스트 완료 | spec 1쪽 |
| M05 | 로고 변형 금지 | logo_variant | guide 1쪽 |
| M06 | 로고 최소 여백 | {'ratio': 0.25} | guide 1쪽 |
| M07 | 제품 외형 | {'label_color': '#f2d21b'} | guide 2쪽 |
| M08 | 규격 | {'size': (1080, 1080), 'mimes': ['image/png']} | spec 2쪽 |
| M09 | 소재 라이선스 증빙 | {'evidence_id': 'license_icons'} | contract 1쪽 |
| M10 | 시험 성적서(문구 사용 시) | {'evidence_id': 'test_report'} | contract 1쪽 |

## 이미지별 정답 요약 (충족이 아닌 항목만)
### 기준 v1
- D1: 모두 충족
- D2: M05 로고 변형 금지 → 미충족(logo_distorted)
- D3: M02 용량 → 미충족(value_mismatch), M04 효능 문구 → 미충족(text_missing), M06 로고 최소 여백 → 미충족(clear_violation), M10 시험 성적서(문구 사용 시) → 해당 없음(condition_not_triggered)
- D4: M07 제품 외형 → 미충족(form_changed)
- D5: M02 용량 → 확인 필요(illegible), M10 시험 성적서(문구 사용 시) → 미충족(evidence_missing)
- D6: M02 용량 → 확인 필요(illegible)

## 수정 시나리오
D5 검수에서 시험 성적서 미제출(미충족)과 용량 판독 불확실(확인 필요). D6은 이미지가 같고 증빙만 추가됨 → 성적서 조건은 해결되지만 용량 확인 필요는 그대로 남는다.

```json
{
  "scenario_type": "evidence_only",
  "previous": "D5",
  "revised": "D6",
  "image_bytes_identical": true,
  "diff": {
    "resolved": [
      "M10"
    ],
    "persisting": [],
    "new_errors": [],
    "needs_review_after": [
      "M02"
    ]
  },
  "expected_system_behavior": "이미지 해시가 같으므로 문자 검사를 다시 하지 않아도 되지만, 증빙 버전 변경은 새 이력으로 남긴다. 성적서 조건은 해결, 용량 판독 불확실은 그대로 사람 확인 대상."
}
```
