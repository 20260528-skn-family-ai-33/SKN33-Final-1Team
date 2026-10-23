# data/videos — 관광 동영상 검수용 베이스 시드

영상 검수(영상 트랙)의 **베이스 영상**을 여기에 둔다. 출처는 한국관광공사
**관광 동영상 서비스**(공공데이터포털 15162188, `B551011/VideoService`).
전부 **드론 풍경/관광지·유산 영상**이라, 자막·로고·워터마크·광고고지가 거의 없다.

## 이 영상의 용도 (중요)
- **A층 "정상/네거티브 샘플"** 로만 적합: 깨끗한 영상에서 **거짓 위반이 안 나는지**(오탐 0)와
  디코드→씬→OCR 프런트엔드가 안 깨지는지 확인용.
- **검수 성능(위반 탐지) 벤치마크로는 부족.** 위반 요소가 없기 때문.
  성능 측정은 이 영상들 위에 자막·로고(`data/logos`)·워터마크·광고고지를
  **합성 주입한 라벨셋(B층)** 으로 해야 한다.

## 폴더/파일
```
data/videos/
  <contentId>.mp4              # 받은 영상 (HLS 리먹스, git 제외)
  tour_videos_manifest.csv     # 매니페스트 (git 추적) — 출처·sha256·라이선스
  README.md
```

## git (용량 정책)
- **영상 파일(mp4/mov/webm/ts/m3u8)은 git 제외** (`.gitignore`). 매니페스트·README만 추적.
  → `data/poster_sources_*.csv`, `data/logos/README.md`와 같은 "파일은 밖, 매니페스트만 git" 패턴.
- 재현은 매니페스트의 `content_id`로 `scripts/fetch_tour_videos.py` 를 다시 돌려 복원한다.

## 받는 법
```bash
# 키: 프로젝트 루트 .env 의 TOUR_VIDEO_API_KEY (공공데이터포털 serviceKey)
# HLS 라 ffmpeg 필요 (격리 설치 권장: pip install imageio-ffmpeg)
python scripts/fetch_tour_videos.py probe  --rows 10
python scripts/fetch_tour_videos.py fetch  --ffmpeg <ffmpeg.exe> --scan 25 --count 8
```
- `orgFileSize`(상세의 원본 마스터 크기 ~850MB)는 전 항목이 비슷해 **정렬 기준이 못 된다.**
  실제 받아지는 HLS 리먹스본은 개당 **~20~30MB**.

## 라이선스
- 데이터셋: **이용허락범위 제한 없음**. 항목별 `cpyrhtDivCd`(예: `Type1`=공공누리 1유형, 출처표시)
  를 매니페스트에 기록한다. 공개 관광 영상이며 사용자 업로드와 달리 **파기 대상 아님**(영구 레퍼런스).
