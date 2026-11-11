# 포스터 로고 확정·정밀 대조 API

포스터 속 원본 로고의 위치를 사람이 확정하고, 확정한 위치를 원본과 정밀 대조해 **근거**를 돌려준다.
일치·다름 판정은 내지 않는다. 최종 판단은 사람이 한다(영상 로고 2026-11-01 결정을 포스터에도 같게 적용).
포스터 기준 문구 검수(A02 → A04·A05 → A06 → A07)와는 별도 축이며, 로고 작업이 실패해도 포스터 검수는 그대로 진행된다.

- 코드: `backend/app/poster/logos/routes.py`(API), `candidates.py`(1부 후보), `compare.py`(2부 대조 래퍼), `client.py`(RunPod 워커 호출)
- 영상 로고에서 그대로 쓰는 것: 모델 4종(`backend/app/video/logos/models.py`: OWLv2·SigLIP2·BiRefNet·PP-OCRv5), 탐지 프롬프트·점수 기준,
  SigLIP2 유사도·OCR 가산점(+0.10) 규칙, 2부 대조 규칙 전체(`video/logos/compare.py`의 `reference`·`compare`), RunPod 워커·연결 설정
- 화면: `frontend/src/pages/review/PosterLogoPage.jsx`(`/review/logo?job=<id>`), 검수 화면 카드 `PosterLogoParts.jsx`, 접수·계산 `posterLogoFlow.js`

## 영상 로고와 다른 점

| 항목 | 영상 로고 | 포스터 로고 |
|---|---|---|
| 입력 | 1초 간격 프레임 | 포스터 1장(EXIF 회전 반영, 투명 부분은 흰 배경) |
| 탐지 타일 | 전체 + 타일 10개 | 전체 + 타일 10개 + **960px 격자 타일(25% 겹침)**, 긴 변 1,440px 이상일 때 |
| 후보 상한 | 60 | 120 |
| 후보 묶기 | 같은 초 안 겹침 묶기 + 3초 이내 시간 묶음 | 겹침 묶기만(같은 규칙) |
| 후보 점수 | 초별 최고 유사도의 상위 3개 평균 | 묶음 안 상자 중 그 원본과 가장 닮은 상자의 유사도 |
| 가산점 기준 문구 | 띄어쓰기로 조각 | 줄 구분 `\|`도 조각 경계로 사용 |
| 좌표 | 화면용으로 줄인 프레임 좌표 | 포스터 원본 픽셀 좌표 |
| 확정 | 후보 묶음·직접 지정(구간)·영상에 없음 | 후보(여러 개)·직접 지정(영역)·포스터에 없음 |
| 근거 | 묶음별 초별 결과·나온 초 수·대표 프레임 | 위치별 결과 1건(요소 상태·근거·OCR·색·영역 크기·선명도) |

격자 타일과 후보 상한은 포스터 정답 자료로 정한 값이 아니라 영상 설정에서 옮긴 잠정값이다.
짧은 변 60px 미만 영역은 영상 합성 변형 실험(2026-10-31)에서 정상 로고도 60%가 차이 근거에 걸렸으므로 결과에 `small`로 표시하고 사람 확인을 안내한다.

## 실행 위치·연결

- 영상 로고와 **같은 RunPod 로고 워커**(`backend/app/video/logos/worker.py`)를 쓴다. 새 환경변수는 없다
  - `VIDEO_LOGO_WORKER_URL`(없으면 API 프로세스에서 직접 추론), `VIDEO_LOGO_WORKER_TOKEN`, `VIDEO_LOGO_WORKER_TIMEOUT_SECONDS`
  - AWS_TEST 두 워커 구성(`WorkerPool`)이면 영상 작업과 같은 슬롯을 나눠 쓴다
- 워커 작업 종류: 후보 찾기 `poster_candidates`(새로 추가, 포스터 JPEG 품질 95 + 원본 로고), 정밀 대조 `compare`(영상과 같음)
- API는 영상 로고 API와 같은 앱에 붙는다
  - 로컬 레거시 앱(`backend/app/api.py`): 저장 `.runtime/poster-logos/<id>/`
  - 영상 서비스 앱(`backend/app/video/framework_service.py`): 저장 `<영상 저장소>/poster-logos/[<사용자>/]<id>/`
  - 프런트 개발 서버는 `/api/poster-logos`를 `VIDEO_API_PROXY_TARGET`(영상 API)으로 보낸다(`frontend/vite.config.js`)
- 접속 경계: 영상 로고와 같다(loopback 프록시 + 허용 호스트 + 로그인 세션, AWS_TEST는 사용자별 폴더)

### 반영할 때 필요한 일(이 문서 작성 시점에 수행하지 않음)

1. RunPod Pod의 로고 워커 폴더에 `backend/app/poster/`와 수정된 `backend/app/video/logos/worker.py`를 복사하고 워커를 재시작한다.
   이전 워커는 `poster_candidates`를 422로 거절하며, 그 문구가 화면의 작업 오류로 보인다
2. 영상 API(로컬 8013 또는 AWS 영상 API)를 새 코드로 재시작한다
3. AWS CloudFront에 `/api/poster-logos`, `/api/poster-logos/*` → 영상 API 경로를 영상 로고 경로와 같은 방식으로 추가한다
   (`docs/agent-framework/AWS_구축_설정_인수인계_20261110.md`의 경로 표). 화면 경로 `/review/logo`의 새로고침 허용도 확인한다

## 흐름

1. 자료 입력 화면의 "원본 로고 첨부"에 로고(최대 12장, JPG·PNG·WEBP·SVG)와 로고마다 기준 문구(선택, 줄은 `|`)를 넣는다.
   SVG는 브라우저에서 PNG(긴 변 1,024px)로 바꿔 보낸다
2. 기준을 승인하고 포스터를 등록하면(검수 시작 / 승인 기준에 포스터 등록) 등록된 포스터 실행 번호로 로고 작업을 접수한다.
   수정본 재검수는 이전 실행의 로고 작업에서 원본 로고·기준 문구를 가져와 새 포스터로 다시 접수한다. 같은 실행 번호에 작업이 있으면 다시 만들지 않는다
3. 포스터 검수 화면의 "포스터 로고 검수" 카드에서 후보 찾기 진행률을 보고, 끝나면 "로고 위치 확정하기"로 이동한다
4. 확정 화면에서 원본 로고마다 후보(상위 3개 + 더 보기, 포스터 위 박스)를 고르거나 직접 지정하거나 "포스터에 없음"으로 표시하고 저장한다
5. "저장한 N개 로고 정밀 대조" → 위치별 근거 화면

## API

- `GET /api/poster-logos/status`: 워커 준비 상태(영상 로고와 같은 값)
- `GET /api/poster-logos?run_id=<포스터 검수 실행>`: 연결된 작업(최근 20개). `status`, `progress`, `stage_detail`, `candidate_count`, `logo_count`, `compare_status` 등
- `POST /api/poster-logos`(multipart): `poster`(JPG·PNG·WEBP, 20MiB·1,000만 화소·한 변 8,192px 이하), `logos`(1~12장, 각 8MB 이하),
  `logo_texts`(선택, JSON 문자열 배열), `title`, `run_id`(선택, UUID) → `{"id", "status": "queued"}`
- `GET /api/poster-logos/<id>`: 작업 상태(`queued` → `running` → `candidates_ready` | `failed`)
- `GET /api/poster-logos/<id>/data.json`: 후보
  `{"size": [W, H], "image": "poster.jpg", "refs": [{name, img, ocr: {후보 번호: [OCR, 문구 일치]}, bonus_text, bonus_basis}],
    "candidates": [{"box", "owl", "rep": [원본별 대표 상자], "score": [원본별 유사도], "rank": [원본별 순위 점수]}]}`
- `GET /api/poster-logos/<id>/poster.jpg`(화면용, 긴 변 2,000px 이하), `GET .../refs/<i>.png`
- `POST /api/poster-logos/<id>/compare`(JSON):
  `[{"logo": <원본 번호>, "decision": "done"|"none", "crops": [{"source": "candidate", "candidate": <후보 번호>} | {"source": "manual", "bbox": [x1, y1, x2, y2]}]}]`
  - 후보는 서버에 저장한 그 원본 로고의 대표 상자(`rep`)를 8% 넓혀 자른다. 화면이 보낸 좌표는 쓰지 않는다
  - 직접 지정 bbox는 포스터 원본 픽셀 좌표이며 넓히지 않는다. 한 번에 위치 40개까지
  - `none`은 "포스터에 없음"을 사람이 확인한 기록으로 결과에 남는다
- `GET /api/poster-logos/<id>/compare`: `compare_status`, `compare_progress`, `compare_error`,
  `locations`(위치별 `outcome`·`findings`·`kinds`·`elements`·`ocr`·`color`·`size`·`sharp`·`small`·`rank`), `absent`, `skipped`, `warnings`
- `GET /api/poster-logos/<id>/compare/crop/<logo>/<crop>.jpg`: 대조한 영역

서버가 재시작되면 진행 중이던 후보 찾기·정밀 대조는 실패로 표시된다(다시 접수·실행).

## 확인 상태(2026-11-11)

로컬 Windows에서 **모델을 가짜로 바꾼** 점검만 했다(OWLv2·SigLIP2·BiRefNet·OCR 대신 고정 상자·작은 회색조 임베딩·전경 전체·빈 OCR, 로컬 .venv에 torchvision이 없어 NMS도 대체).
점검 스크립트와 작업 폴더는 프로젝트 밖(scratchpad)에 두었다.

- 백엔드: 새 모듈 컴파일·import, 워커 작업 종류 4개 등록. 실제 `detect`의 타일 좌표 변환·진행률(1,950×2,600 포스터에서 OWL 입력 23장 = 전체 1 + 타일 10 + 격자 12),
  겹친 상자 묶기(상자 6개 → 후보 5개), 원본별 순위, 접수 → 후보 → 대조 → 근거 API 흐름, 후보 8% 넓힘·직접 지정 그대로 자르기,
  4px 미만 위치 건너뜀·경고, 포스터에 없음 기록, 잘못된 대조 요청 7종과 SVG 로고·초과 해상도·잘못된 run_id 422, 거절된 접수의 폴더 미잔존, 재시작 시 실행 중 표시 실패 전환
- 화면(Vite 개발 서버 + 가짜 API): 확정 화면의 후보 박스·카드·선택·저장·로고 전환·포스터에 없음·직접 지정(포인터 이벤트)·브라우저 저장 복원,
  정밀 대조 실행 → 근거 화면(결과 표시·근거·요소 상태·원본/대조 영역 이미지·포스터에 없음 목록), 자료 입력의 기준 문구 칸·SVG 선택,
  접수 함수의 SVG → PNG 변환(1,024×512)·같은 실행 중복 접수 방지·수정본용 이전 원본 로고 복원
- 기존 회귀: 영상 서비스·구조 호환 pytest 25개, 프런트 단위 101개 통과, 배포 빌드 성공(결과물은 scratchpad에만)
- 점검에서 고친 것: 근거 문구의 '영상'을 '포스터'로 표시, 후보 표시 개수 선택 줄바꿈

확인하지 않은 것: 실제 모델로 찾은 후보 품질·대조 결과, RunPod 워커의 `poster_candidates` 실행, 포스터 검수 화면(프레임워크 실행)의 로고 작업 카드 표시와 등록 시 자동 접수,
AWS·영상 API 반영, Mac 실행. 포스터 로고 정답 자료와 임계값 보정, 로고 근거를 포스터 검수 결과(A06·A07 보고서)에 넣는 연결은 후속 범위다.
