# C02 · 밀담 베이커리 · 버터 크루아상 세트

- 사례군: 음료·디저트 B  |  그룹 ID: G02  |  가상 자료(실존 브랜드와 무관)
- 설계 의도: 조건 12개, 표 안 숫자(정가·판매가), 가격 정합성(파생 규칙). 한영 혼합 오타(3O%), 곡선·저대비 가격, 기준 변경(v1→v2)을 시험.
- 사용 계획: 매체 instagram_feed · 기간 2026-11-01 ~ 2026-11-07 · 지역 대한민국 · 유료광고 True

## 문서
| 문서 | 쪽수 | 파일 |
|---|---|---|
| 광고 소재 제작 명세서 | 2 | input/docs/C02_spec.pdf (+ .md) |
| 광고 제작 용역 계약서(요약) | 1 | input/docs/C02_contract.pdf (+ .md) |
| 밀담 베이커리 브랜드 가이드 | 1 | input/docs/C02_guide.pdf (+ .md) |
| 기준 변경 공지 | 1 | input/docs/C02_notice.pdf (+ .md) |

## 납품 이미지
| ID | 유형 | 의도한 편집 | 파일 |
|---|---|---|---|
| D1 | 정상 | v1 기준(30%·4,200원)에서 모든 조건 충족 | C02_D1.png (1080×1350) |
| D2 | 단일 오류 | 판매가 4,200원 → 4,800원 (판매가·가격 정합성 두 조건에 영향) | C02_D2.png (1080×1350) |
| D3 | 복합 오류 | 할인율의 0을 영문 O로 교체(3O%); 기간 종료일 11.07 → 11.17; 정가 표기 삭제 | C02_D3.png (1080×1350) |
| D4 | 시각/개수 오류 | 크루아상 2개 → 3개; 계정 철자 오류(bakery → bakary) | C02_D4.png (1080×1350) |
| D5 | 판독 불확실 | 판매가를 곡선 배치·저대비로 표현; 한정 문구 14px·저대비 | C02_D5.png (1080×1350) |
| D6 | 수정본 | 변경 공지(v2) 반영: 판매가 4,500원, 할인율 25% | C02_D6.png (1080×1350) |

## 검사 조건(기준 정답)
| ID | 항목 | 기대값 | 출처 |
|---|---|---|---|
| P01 | 상품명 | 버터 크루아상 세트 | spec 1쪽 |
| P02 | 정가 | 6,000원 | spec 1쪽 |
| P03 | 판매가 | {'v1': '4,200원', 'v2': '4,500원'} | spec 1쪽; notice 1쪽 |
| P04 | 할인율 | {'v1': '30% 할인', 'v2': '25% 할인'} | spec 1쪽; notice 1쪽 |
| P05 | 가격 정합성 | price_consistency | spec 1쪽 |
| P06 | 브랜드 표기 | MILDAM BAKERY | guide 1쪽 |
| P07 | 행사 기간 | 11.01 ~ 11.07 | spec 2쪽 |
| P08 | 한정 문구 | 1인 3세트 한정 | spec 2쪽 |
| P09 | 크루아상 개수 | 크루아상 == 2 | spec 2쪽 |
| P10 | 프로필 계정 | @mildam_bakery | spec 2쪽 |
| P11 | 로고 변형 금지 | logo_variant | guide 1쪽 |
| P12 | 규격 | {'size': (1080, 1350), 'mimes': ['image/png']} | spec 2쪽 |

## 이미지별 정답 요약 (충족이 아닌 항목만)
### 기준 v1
- D1: 모두 충족
- D2: P03 판매가 → 미충족(value_mismatch), P05 가격 정합성 → 미충족(inconsistent)
- D3: P02 정가 → 미충족(text_missing), P04 할인율 → 미충족(value_mismatch), P05 가격 정합성 → 확인 필요(list_price_missing), P07 행사 기간 → 미충족(value_mismatch)
- D4: P09 크루아상 개수 → 미충족(count_mismatch), P10 프로필 계정 → 미충족(value_mismatch)
- D5: P03 판매가 → 확인 필요(illegible), P05 가격 정합성 → 확인 필요(illegible), P08 한정 문구 → 확인 필요(illegible)
- D6: P03 판매가 → 미충족(value_mismatch), P04 할인율 → 미충족(value_mismatch)
### 기준 v2
- D1: P03 판매가 → 미충족(value_mismatch), P04 할인율 → 미충족(value_mismatch)
- D2: P03 판매가 → 미충족(value_mismatch), P04 할인율 → 미충족(value_mismatch), P05 가격 정합성 → 미충족(inconsistent)
- D3: P02 정가 → 미충족(text_missing), P03 판매가 → 미충족(value_mismatch), P04 할인율 → 미충족(value_mismatch), P05 가격 정합성 → 확인 필요(list_price_missing), P07 행사 기간 → 미충족(value_mismatch)
- D4: P03 판매가 → 미충족(value_mismatch), P04 할인율 → 미충족(value_mismatch), P09 크루아상 개수 → 미충족(count_mismatch), P10 프로필 계정 → 미충족(value_mismatch)
- D5: P03 판매가 → 확인 필요(illegible), P04 할인율 → 미충족(value_mismatch), P05 가격 정합성 → 확인 필요(illegible), P08 한정 문구 → 확인 필요(illegible)
- D6: 모두 충족

## 수정 시나리오
v1 기준으로 D1~D5를 검수한 뒤 변경 공지(적용일 2026-10-29)로 기준이 v2가 된다. D1은 v2에서 새로 평가하되 v1 결과는 보존하고, D6은 v2 기준으로 검수한다.

```json
{
  "scenario_type": "criteria_change",
  "criteria_versions": [
    "v1",
    "v2"
  ],
  "revised": "D6",
  "reevaluated_under_v2": {
    "D1": {
      "P03": "미충족",
      "P04": "미충족"
    }
  },
  "v1_results_preserved_for": "D1",
  "revised_under_v2_blockers": [],
  "changed_criteria": [
    "P03",
    "P04"
  ],
  "expected_system_behavior": "기준 v2 확정 후 D1을 v2로 새로 평가하되 v1 결과는 덮어쓰지 않는다. D6은 v2에서 모든 조건 충족."
}
```
