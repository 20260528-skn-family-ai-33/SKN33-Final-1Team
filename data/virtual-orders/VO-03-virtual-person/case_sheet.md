# VO-03 · 모아 워치 S1 · 가상 인간 서하

- 유형: 가상인물(AI 휴먼 광고)  |  그룹 ID: VG03  |  모든 자료는 가상(실존 브랜드·인물과 무관)
- 설계 의도: 가상 인물 설정서 일치(헤어·착용 위치), AI 생성물 표시, 모델 초상·음성 허락 범위(기간·매체·지역) 대조, IP·AI 도구 증빙을 시험. 증빙만 보완하는 수정 시나리오.
- 사용 계획: 매체 instagram_feed · 게시 2026-11-16 ~ 2027-02-15 · 지역 대한민국 · 유료광고 True

## 외주에 필요한 문서 (3단계)
| 단계 | 문서 | 쪽수 | 파일 |
|---|---|---|---|
| 1 기획·요청 | 제안요청서 (RFP) | 1 | input/docs/VO-03_rfp.pdf (+ .md) |
| 1 기획·요청 | 비밀유지계약서 (NDA) | 1 | input/docs/VO-03_nda.pdf (+ .md) |
| 1 기획·요청 | 가상 인물 설정서 | 1 | input/docs/VO-03_persona.pdf (+ .md) |
| 2 계약 | 모델 초상·음성 사용 허락 기준 | 1 | input/docs/VO-03_consent.pdf (+ .md) |
| 2 계약 | AI 생성물 표시 기준 (가상 발주사 내부 기준) | 1 | input/docs/VO-03_label.pdf (+ .md) |
| 2 계약 | 외주 용역 계약서 | 2 | input/docs/VO-03_contract.pdf (+ .md) |
| 3 수행·검수 | 과업지시서 (SOW) | 1 | input/docs/VO-03_sow.pdf (+ .md) |
| 3 수행·검수 | 브랜드 가이드 | 1 | input/docs/VO-03_guide.pdf (+ .md) |
| 3 수행·검수 | 검수조서 (검수 확인서) 양식 | 1 | input/docs/VO-03_inspect.pdf (+ .md) |

## 납품 증빙 문서
| 증빙 | 버전 | 파일 | 메타 |
|---|---|---|---|
| likeness_consent | v2 | input/evidence/VO-03_likeness_consent_v2.pdf | {"start": "2026-11-01", "end": "2027-02-28", "media": ["instagram_feed"], "region": "대한민국"} |
| ip_assignment | v1 | input/evidence/VO-03_ip_assignment_v1.pdf | - |
| ai_tool_list | v1 | input/evidence/VO-03_ai_tool_list_v1.pdf | - |
| likeness_consent | v1 | input/evidence/VO-03_likeness_consent_v1.pdf | {"start": "2026-11-01", "end": "2027-01-31", "media": ["instagram_feed"], "region": "대한민국"} |

## 납품 이미지
| ID | 유형 | 의도한 편집 | 파일 |
|---|---|---|---|
| D1 | 정상 | 모든 조건·증빙 충족 | VO-03_D1.png (1080×1350) |
| D2 | 단일 오류 | AI 생성물 표시 문구 삭제(표시·글자 크기 두 조건에 영향) | VO-03_D2.png (1080×1350) |
| D3 | 복합 오류 | 가상 인물 헤어가 설정서와 다름(갈색 장발); 시계를 오른쪽 손목에 착용; 가격 199,000원 → 219,000원 | VO-03_D3.png (1080×1350) |
| D4 | 판독 불확실 | AI 표시 문구 14px·저대비; 시계를 소매로 약 60% 가림(착용 손목 판단 불확실) | VO-03_D4.png (1080×1350) |
| D5 | 증빙 문제 | 모델 허락서의 종료일(2027-01-31)이 게시 종료일(2027-02-15)보다 앞서 끝남; AI 도구·모델 버전 목록 미제출 | VO-03_D5.png (1080×1350) |
| D6 | 수정본(증빙만) | D5와 같은 이미지 파일에 허락서 연장본(v2: 종료일 2027-02-28)과 AI 도구 목록만 추가 | VO-03_D6.png (1080×1350) |

## 검사 조건(기준 정답)
| ID | 항목 | 기대값 | 출처 |
|---|---|---|---|
| H01 | 브랜드 표기 | MOA WATCH | guide 1쪽 |
| H02 | 상품명 | 모아 워치 S1 | sow 1쪽 |
| H03 | 가격 | 199,000원 | sow 1쪽 |
| H04 | 출시 기념 기간 | 11.16 ~ 12.15 | sow 1쪽 |
| H05 | AI 생성물 표시 문구 | AI로 생성된 가상 인물입니다 | label 1쪽 |
| H06 | AI 표시 글자 크기 | {'min_px': 24} | label 1쪽 |
| H07 | 가상 인물 헤어(설정서 일치) | 검은 단발 | persona 1쪽 |
| H08 | 시계 착용 손목(설정서 일치) | left | persona 1쪽 |
| H09 | 모델 초상·음성 허락 범위 | {'evidence_id': 'likeness_consent'} | consent 1쪽 |
| H10 | IP 귀속 확인서 제출 | {'evidence_id': 'ip_assignment'} | contract 2쪽 |
| H11 | AI 도구·모델 버전 목록 제출 | {'evidence_id': 'ai_tool_list'} | contract 2쪽 |
| H12 | 로고 변형 금지 | logo_variant | guide 1쪽 |
| H13 | 규격 | {'size': (1080, 1350), 'mimes': ['image/png']} | sow 1쪽 |

## 이미지별 정답 요약 (충족이 아닌 항목만)
- D1: 모두 충족
- D2: H05 AI 생성물 표시 문구 → 미충족(text_missing), H06 AI 표시 글자 크기 → 미충족(text_missing)
- D3: H03 가격 → 미충족(value_mismatch), H07 가상 인물 헤어(설정서 일치) → 미충족(attr_mismatch), H08 시계 착용 손목(설정서 일치) → 미충족(attr_mismatch)
- D4: H05 AI 생성물 표시 문구 → 확인 필요(illegible), H06 AI 표시 글자 크기 → 미충족(text_too_small), H08 시계 착용 손목(설정서 일치) → 확인 필요(occluded)
- D5: H09 모델 초상·음성 허락 범위 → 미충족(license_gap), H11 AI 도구·모델 버전 목록 제출 → 미충족(evidence_missing)
- D6: 모두 충족

## 수정 시나리오
D5 검수에서 허락 기간 부족(H09)과 AI 도구 목록 미제출(H11). D6은 이미지가 같고 증빙만 갱신 → 두 조건이 해결되고 새 오류는 없다.

```json
{
  "scenario_type": "evidence_only",
  "previous": "D5",
  "revised": "D6",
  "diff": {
    "resolved": [
      "H09",
      "H11"
    ],
    "persisting": [],
    "new_errors": [],
    "needs_review_after": []
  },
  "approval_blockers_for_revised": [],
  "image_bytes_identical": true,
  "expected_system_behavior": "이미지 해시가 같으면 문자 검사를 반복하지 않아도 되지만 증빙 버전 변경은 새 이력으로 남기고, 해결된 조건과 남은 조건을 구분한다."
}
```
