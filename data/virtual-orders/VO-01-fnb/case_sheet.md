# VO-01 · 온기식탁 · 들깨 버섯 리조또 세트

- 유형: F&B (식음료 매장 신메뉴)  |  그룹 ID: VG01  |  모든 자료는 가상(실존 브랜드·인물과 무관)
- 설계 의도: 원산지·알레르기·최상급 표현·가격 정합성 등 식품 광고에 필요한 표시와 증빙을 시험. 원산지·알레르기 글자 크기 기준은 가상 발주사 내부 기준.
- 사용 계획: 매체 instagram_feed · 게시 2026-11-02 ~ 2026-11-15 · 지역 대한민국 · 유료광고 True

## 외주에 필요한 문서 (3단계)
| 단계 | 문서 | 쪽수 | 파일 |
|---|---|---|---|
| 1 기획·요청 | 제안요청서 (RFP) | 1 | input/docs/VO-01_rfp.pdf (+ .md) |
| 1 기획·요청 | 비밀유지계약서 (NDA) | 1 | input/docs/VO-01_nda.pdf (+ .md) |
| 1 기획·요청 | 메뉴 정보서 (원재료·원산지·알레르기) | 1 | input/docs/VO-01_menu.pdf (+ .md) |
| 1 기획·요청 | 프로모션 조건서 | 2 | input/docs/VO-01_promo.pdf (+ .md) |
| 2 계약 | 표시·광고 기준서 (가상 발주사 내부 기준) | 1 | input/docs/VO-01_label.pdf (+ .md) |
| 2 계약 | 외주 용역 계약서 | 2 | input/docs/VO-01_contract.pdf (+ .md) |
| 3 수행·검수 | 과업지시서 (SOW) | 1 | input/docs/VO-01_sow.pdf (+ .md) |
| 3 수행·검수 | 브랜드 로고 가이드 | 1 | input/docs/VO-01_guide.pdf (+ .md) |
| 3 수행·검수 | 검수조서 (검수 확인서) 양식 | 1 | input/docs/VO-01_inspect.pdf (+ .md) |

## 납품 증빙 문서
| 증빙 | 버전 | 파일 | 메타 |
|---|---|---|---|
| origin_allergen_sheet | v1 | input/evidence/VO-01_origin_allergen_sheet_v1.pdf | - |
| photo_license | v1 | input/evidence/VO-01_photo_license_v1.pdf | - |

## 납품 이미지
| ID | 유형 | 의도한 편집 | 파일 |
|---|---|---|---|
| D1 | 정상 | 모든 조건·증빙 충족 | VO-01_D1.png (1080×1350) |
| D2 | 단일 오류 | 판매가 12,000원 → 13,000원 (판매가·가격 정합성에 영향) | VO-01_D2.png (1080×1350) |
| D3 | 복합 오류 | 알레르기 문구 삭제; 치즈 원산지 이탈리아산 → 프랑스산; 광고 문구에 근거 없는 최상급 “최고” | VO-01_D3.png (1080×1350) |
| D4 | 판독 불확실 | 원산지·알레르기를 12px·저대비로 표기(사람도 확정 어려움) | VO-01_D4.png (1080×1350) |
| D5 | 증빙 누락 | 이미지는 정상이나 원산지·알레르기 확인서가 제출 목록에 없음 | VO-01_D5.png (1080×1350) |
| D6 | 수정본 | D3의 지적 4건(알레르기·치즈 원산지·최상급 표현)을 모두 수정 | VO-01_D6.png (1080×1350) |

## 검사 조건(기준 정답)
| ID | 항목 | 기대값 | 출처 |
|---|---|---|---|
| F01 | 메뉴명 | 들깨 버섯 리조또 세트 | promo 1쪽 |
| F02 | 정가 | 15,000원 | promo 1쪽 |
| F03 | 판매가 | 12,000원 | promo 1쪽 |
| F04 | 할인율 | 20% 할인 | promo 1쪽 |
| F05 | 가격 정합성 | price_consistency | promo 1쪽 |
| F06 | 원산지 표기 | 쌀·버섯·들깨: 국내산 / 치즈: 이탈리아산 | menu 1쪽 |
| F07 | 원산지 글자 크기 | {'min_px': 26} | label 1쪽 |
| F08 | 알레르기 표기 | 알레르기: 우유·대두·밀 함유 | menu 1쪽 |
| F09 | 알레르기 글자 크기 | {'min_px': 26} | label 1쪽 |
| F10 | 근거 없는 최상급 표현 금지 | {'words': ['최고', '1위', '유일', '최초']} | label 1쪽 |
| F11 | 행사 기간 | 11.02 ~ 11.15 | promo 1쪽 |
| F12 | 매장 정보 | 서울 가상구 온기로 12 · 02-000-0000 | promo 1쪽 |
| F13 | 버섯 토핑 개수 | 버섯 토핑 >= 5 | promo 2쪽 |
| F14 | 로고 변형 금지 | logo_variant | guide 1쪽 |
| F15 | 규격 | {'size': (1080, 1350), 'mimes': ['image/png']} | sow 1쪽 |
| F16 | 원산지·알레르기 확인서 제출 | {'evidence_id': 'origin_allergen_sheet'} | contract 2쪽 |
| F17 | 촬영물 이용 확인서 제출 | {'evidence_id': 'photo_license'} | contract 2쪽 |

## 이미지별 정답 요약 (충족이 아닌 항목만)
- D1: 모두 충족
- D2: F03 판매가 → 미충족(value_mismatch), F05 가격 정합성 → 미충족(inconsistent)
- D3: F06 원산지 표기 → 미충족(value_mismatch), F08 알레르기 표기 → 미충족(text_missing), F09 알레르기 글자 크기 → 미충족(text_missing), F10 근거 없는 최상급 표현 금지 → 미충족(forbidden_phrase)
- D4: F06 원산지 표기 → 확인 필요(illegible), F07 원산지 글자 크기 → 미충족(text_too_small), F08 알레르기 표기 → 확인 필요(illegible), F09 알레르기 글자 크기 → 미충족(text_too_small)
- D5: F16 원산지·알레르기 확인서 제출 → 미충족(evidence_missing)
- D6: 모두 충족

## 수정 시나리오
D3 지적(알레르기 누락·치즈 원산지 오기·최상급 표현) → D6 수정본. 재검수에서 모두 해결, 새 오류 없음.

```json
{
  "scenario_type": "fix",
  "previous": "D3",
  "revised": "D6",
  "diff": {
    "resolved": [
      "F06",
      "F08",
      "F09",
      "F10"
    ],
    "persisting": [],
    "new_errors": [],
    "needs_review_after": []
  },
  "approval_blockers_for_revised": [],
  "image_bytes_identical": false,
  "expected_system_behavior": "이전 지적의 해결 여부와 새 오류를 항목별로 구분해 기록하고, 남은 차단 조건이 없을 때만 승인 준비로 넘어간다."
}
```
