# C03 · 가온동 골목마켓 가을 페스타

- 사례군: 매장·행사 홍보  |  그룹 ID: G03  |  가상 자료(실존 브랜드와 무관)
- 설계 의도: 조건 10개. 필수 문구가 명세서 마지막 페이지에 있고, 각주 예외(요일 필수)와 매체 한정 조건(현수막 전용 → 해당 없음)이 있음. 규격 오류와 구버전 재제출을 시험.
- 사용 계획: 매체 instagram_feed · 기간 2026-11-10 ~ 2026-11-15 · 지역 대한민국 · 유료광고 False

## 문서
| 문서 | 쪽수 | 파일 |
|---|---|---|
| 행사 홍보 소재 제작 명세서 | 3 | input/docs/C03_spec.pdf (+ .md) |
| 소재 제작 용역 계약서(요약) | 1 | input/docs/C03_contract.pdf (+ .md) |
| 가온동 골목마켓 로고 가이드 | 1 | input/docs/C03_guide.pdf (+ .md) |

## 납품 이미지
| ID | 유형 | 의도한 편집 | 파일 |
|---|---|---|---|
| D1 | 정상 | 모든 조건 충족(주최 표기는 현수막 전용이라 해당 없음) | C03_D1.png (1080×1080) |
| D2 | 단일 오류 | 요일 오기 (토 → 금) | C03_D2.png (1080×1080) |
| D3 | 복합 오류 | 장소 중앙광장 → 시청광장; 우천 고지 삭제; 문의처 삭제; 캔버스 1080×1350(규격 위반) | C03_D3.png (1080×1350) |
| D4 | 시각/개수 오류 | 스탬프 3개 → 4개; 로고 가로 1.6배 늘림 | C03_D4.png (1080×1080) |
| D5 | 판독 불확실 | 문의처 16px·저대비; 우천 고지 14px·저대비 | C03_D5.png (1080×1080) |
| D6 | 수정본 | D3의 4개 지적(장소·우천 고지·문의처·규격)을 모두 수정; 기간 글자 강조색을 약간 바꿈(정상본 D1과 다른 파일로 구분) | C03_D6.png (1080×1080) |
| D7 | 구버전 재제출 | D2와 바이트가 동일한 파일을 D6 이후에 다시 제출 | C03_D7.png (1080×1080) |

## 검사 조건(기준 정답)
| ID | 항목 | 기대값 | 출처 |
|---|---|---|---|
| E01 | 행사명 | 가온동 골목마켓 가을 페스타 | spec 1쪽 |
| E02 | 행사 기간(요일 포함) | 11.14(토) ~ 11.15(일) | spec 1쪽; spec 1쪽 |
| E03 | 장소 | 가온동 중앙광장 | spec 1쪽 |
| E04 | 혜택 문구 | 입장 무료 · 스탬프 3개 모으면 기념품 | spec 2쪽 |
| E05 | 스탬프 아이콘 개수 | 스탬프 아이콘 == 3 | spec 2쪽 |
| E06 | 우천 고지(필수 문구) | 우천 시 일정이 변경될 수 있습니다 | spec 3쪽 |
| E07 | 문의처 | 문의 02-000-0000 | spec 2쪽 |
| E08 | 주최 표기(현수막 한정) | 주최 가온동 상인회 | spec 3쪽 |
| E09 | 행사 로고 변형 금지 | logo_variant | guide 1쪽 |
| E10 | 규격 | {'size': (1080, 1080), 'mimes': ['image/png', 'image/jpeg']} | spec 2쪽 |

## 이미지별 정답 요약 (충족이 아닌 항목만)
### 기준 v1
- D1: E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted)
- D2: E02 행사 기간(요일 포함) → 미충족(value_mismatch), E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted)
- D3: E03 장소 → 미충족(value_mismatch), E06 우천 고지(필수 문구) → 미충족(text_missing), E07 문의처 → 미충족(text_missing), E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted), E10 규격 → 미충족(spec_mismatch)
- D4: E05 스탬프 아이콘 개수 → 미충족(count_mismatch), E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted), E09 행사 로고 변형 금지 → 미충족(logo_distorted)
- D5: E06 우천 고지(필수 문구) → 확인 필요(illegible), E07 문의처 → 확인 필요(illegible), E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted)
- D6: E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted)
- D7: E02 행사 기간(요일 포함) → 미충족(value_mismatch), E08 주최 표기(현수막 한정) → 해당 없음(media_not_targeted)

## 수정 시나리오
D3 지적 → D6 수정본 제출(해결). 이후 D2와 해시가 같은 D7이 다시 제출되면 구버전 재제출로 탐지해 승인 대상에서 제외한다.

```json
{
  "scenario_type": "old_version_resubmitted",
  "previous": "D3",
  "revised": "D6",
  "diff": {
    "resolved": [
      "E03",
      "E06",
      "E07",
      "E10"
    ],
    "persisting": [],
    "new_errors": [],
    "needs_review_after": []
  },
  "resubmitted": "D7",
  "resubmitted_sha_equals": {
    "D2": true
  },
  "latest_valid_version": "D6",
  "approval_eligible_resubmitted": false,
  "expected_system_behavior": "해시가 과거 버전(D2)과 같은 파일은 구버전 재제출로 표시하고 승인 대상에서 제외한다. 최신 유효 버전은 D6."
}
```
