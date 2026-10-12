# C05 · 플로우메이트 스마트 텀블러 FM-1

- 사례군: 신규 브랜드·새 레이아웃  |  그룹 ID: G05  |  가상 자료(실존 브랜드와 무관)
- 설계 의도: 조건 15개, 4쪽 명세서(표 안 가격·뒷부분 조건·각주). 명세서와 계약서의 할인율 충돌(우선순위 규정 없음)로 기준 확정을 보류해야 함. 곡선 한영 혼합 글씨, 금지어, JPEG 규격 오류, 승인 중 새 파일 업로드(경합)를 시험.
- 사용 계획: 매체 instagram_feed · 기간 2026-12-01 ~ 2026-12-14 · 지역 대한민국 · 유료광고 True

## 문서
| 문서 | 쪽수 | 파일 |
|---|---|---|
| 신제품 광고 소재 제작 명세서 | 4 | input/docs/C05_spec.pdf (+ .md) |
| 광고 제작 용역 계약서(요약) | 2 | input/docs/C05_contract.pdf (+ .md) |
| 플로우메이트 브랜드 가이드 | 2 | input/docs/C05_guide.pdf (+ .md) |

## 납품 이미지
| ID | 유형 | 의도한 편집 | 파일 |
|---|---|---|---|
| D1 | 정상 | 할인율은 계약서 기준(30%)으로 표기. 기준 충돌이 풀리기 전까지 F03은 확인 필요 | C05_D1.png (1080×1350) |
| D2 | 단일 오류 | 브랜드 철자 오타(FLOWMATE → FLOWMAET) | C05_D2.png (1080×1350) |
| D3 | 복합 오류 | JPEG로 저장(규격은 PNG); 기간 종료일 12.14 → 12.15; 텀블러 3개 → 2개; 해시태그 삭제 | C05_D3.jpg (1080×1350) |
| D4 | 시각/개수 오류 | 텀블러 3개 → 4개; 로고 폭 90px → 약 40px; 광고 문구에 금지어 “최고” | C05_D4.png (1080×1350) |
| D5 | 판독 불확실 | 곡선 상품명을 저대비로 만들고 반사광이 일부를 가림; 가격 각주 12px·저대비 | C05_D5.png (1080×1350) |
| D6 | 수정본 | D2의 브랜드 오타 수정; 수정 중 할인율이 25%(명세서 값)로 바뀜 | C05_D6.png (1080×1350) |
| D7 | 승인 중 새 파일 | D6에서 광고 문구만 변경한 새 파일(승인 검토 중 업로드) | C05_D7.png (1080×1350) |

## 검사 조건(기준 정답)
| ID | 항목 | 기대값 | 출처 |
|---|---|---|---|
| F01 | 상품명(곡선 한영 혼합) | 플로우메이트 스마트 텀블러 FM-1 | spec 1쪽 |
| F02 | 브랜드 표기 | FLOWMATE | spec 1쪽 |
| F03 | 할인율(기준 충돌) | 충돌: 명세서 3쪽=25% 할인 / 계약서 1쪽=30% 할인 | spec 3쪽; contract 1쪽 |
| F04 | 가격 | 49,000원 | spec 2쪽 |
| F05 | 행사 기간 | 12.01 ~ 12.14 | spec 3쪽 |
| F06 | 가격 각주 | ※ 모델 및 색상에 따라 가격이 다를 수 있음 | spec 4쪽 |
| F07 | 용량 | 450ml | spec 2쪽 |
| F08 | 컬러 3종 개수 | 텀블러 == 3 | spec 3쪽 |
| F09 | 로고 변형 금지 | logo_variant | guide 1쪽 |
| F10 | 로고 최소 폭 | {'min_px': 80} | guide 1쪽 |
| F11 | 규격 | {'size': (1080, 1350), 'mimes': ['image/png']} | spec 4쪽 |
| F12 | 해시태그 | #플로우메이트 | spec 4쪽 |
| F13 | 주소(URL) | flowmate.example/fm1 | spec 4쪽 |
| F14 | 배송 문구 | 무료배송 (제주·도서산간 제외) | spec 4쪽 |
| F15 | 최상급 표현 금지 | {'words': ['최고', '1위', '최상']} | guide 2쪽 |

## 이미지별 정답 요약 (충족이 아닌 항목만)
### 기준 v1
- D1: F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved)
- D2: F02 브랜드 표기 → 미충족(value_mismatch), F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved)
- D3: F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved), F05 행사 기간 → 미충족(value_mismatch), F08 컬러 3종 개수 → 미충족(count_mismatch), F11 규격 → 미충족(spec_mismatch), F12 해시태그 → 미충족(text_missing)
- D4: F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved), F08 컬러 3종 개수 → 미충족(count_mismatch), F10 로고 최소 폭 → 미충족(logo_too_small), F15 최상급 표현 금지 → 미충족(forbidden_phrase)
- D5: F01 상품명(곡선 한영 혼합) → 확인 필요(illegible), F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved), F06 가격 각주 → 확인 필요(illegible)
- D6: F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved)
- D7: F03 할인율(기준 충돌) → 확인 필요(criterion_unresolved)

## 수정 시나리오
D6에 대한 승인 검토 중 D7이 업로드된다. 승인 요청은 STALE_VERSION으로 거절되고 D7을 새로 검수해야 한다. 할인율 기준 충돌(F03)이 풀리기 전까지는 어느 버전도 승인할 수 없다.

```json
{
  "scenario_type": "concurrent_upload",
  "approval_target": "D6",
  "concurrent_upload": "D7",
  "sha_differs": true,
  "blockers_for_target": [
    "F03"
  ],
  "blockers_for_concurrent": [
    "F03"
  ],
  "expected_system_behavior": "승인 요청은 STALE_VERSION으로 거절. 새 파일(D7)은 새로 검수. 할인율 기준 충돌(F03)이 해소되기 전에는 두 버전 모두 승인 불가."
}
```
