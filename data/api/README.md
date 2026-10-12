# 전국문화축제 표준데이터 (적재본)

- 출처: 공공데이터포털 전국문화축제표준데이터 (`api.data.go.kr`, 제공기관 코드 `B551011` 한국관광공사 등)
- 수집일: 2026-10-12
- API 호출 없이 이 파일들로 바로 조회한다. 인증키는 들어 있지 않다.

| 파일 | 내용 |
|---|---|
| `festival_standard_2026-10-12.json` | API 응답 원본 1,364건 |
| `festival_standard_2026-10-12.csv` | 원본과 같은 1,364건 (UTF-8 BOM, 엑셀에서 바로 열림) |
| `festival_standard_2026-10-12_dedup.csv` | 축제명+시작일 기준 중복 제거본 1,288건 (같은 축제는 `referenceDate`가 가장 최근인 행만 남김) |

주의: 2013~2027년 회차가 섞여 있으므로 2026년만 쓸 때는 `fstvlStartDate`로 거른다.

```python
import pandas as pd
df = pd.read_csv("data/api/festival_standard_2026-10-12_dedup.csv")
df_2026 = df[df["fstvlStartDate"].str.startswith("2026")]
```
