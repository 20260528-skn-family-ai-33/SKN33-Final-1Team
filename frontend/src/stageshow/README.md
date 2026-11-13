# Stageshow 장면

소개·로그인 화면에 쓰는 "제품 화면 그림"을 모아 둔 폴더다. 실제 화면을 조작하지 않는 그림이며
(`a.click()`은 누르는 모습만 그리고 `a.set()`이 장면을 바꾼다), API를 부르지 않는다.

| 파일 | 쓰는 곳 | 내용 |
|---|---|---|
| `ResultDemo.tsx` · `result-demo.css` | 로그인 화면의 예시 그림 | 검수 결과 화면: 위반 항목을 누르면 기준·읽은 문구가 펼쳐지고 근거를 확대한다. 넓은 구도(1152px)와 좁은 구도(358px)가 있고, 지금은 로그인 화면에서 좁은 구도만 보인다. |
| `FeatureTiles.tsx` · `feature-tiles.css` | 소개 화면의 기능 소개 세 칸 | 기준 확정 · 포스터에서 읽은 자리 · 담당자 판단. 화면에 들어올 때 한 번만 움직인다. |
| `look.css` | 모든 장면 | 제품 화면의 색·글꼴·모서리 값을 kit 토큰(`--ss-*`)에 맞춘 룩 |
| `Rehearsal.tsx` | 개발 서버의 `/stageshow` | 장면을 실제 쓰이는 너비로 늘어놓은 점검용 화면(배포 빌드에는 없음) |
| `kit/` | 장면 내부 | 무대·커서·창 틀·움직임 부품 |

움직임을 줄인 환경(`prefers-reduced-motion`)에서는 각 장면의 `poster` 한 장만 보이고,
화면 밖으로 나가면 재생을 멈춘다.

## kit의 출처

`kit/`은 [benjitaylor/skills](https://github.com/benjitaylor/skills)의 `stageshow/kit`(커밋 `299140f`, MIT, © 2026 Benji Taylor)에서
필요한 부분만 옮긴 것이다. 라이선스 전문은 `kit/LICENSE`.

- 옮긴 것: 무대(`stage` `act` `clock` `cursor` `keys` `measure` …), 창 틀(`window`), `motion/` 전체, `parts/`·`controls/` 일부
- 뺀 것: 휴대폰·메시지 틀(`phone` `ios` `messages`), 차트·코드, 다른 회사 로고 묶음(`brands/`), `lucide-react`에 기대는 `Icon`·`Field`·`Chip`
- 바꾼 것: `index.ts` 세 개에서 뺀 부품의 내보내기만 지웠다. 아이콘은 프로젝트의 `components/Icon.jsx`를 쓴다.

추가로 설치한 패키지는 없다.

## 점검 방법

장면을 고치면 개발 서버를 띄우고 Stageshow의 촬영 도구(`scripts/film.mjs`, 원본 저장소에 있음)로 확인한다.
도구는 `playwright-core`와 설치된 Chrome을 쓴다.

```sh
node film.mjs "http://127.0.0.1:5173/stageshow" --select "[data-scene=result] .rd" --gate --width 1440
node film.mjs "http://127.0.0.1:5173/stageshow" --select "[data-scene=result-compact] .rd" --gate --width 390
node film.mjs "http://127.0.0.1:5173/stageshow" --select "[data-scene=tiles] .ft" --gate --width 1440
```

마지막 줄이 `rehearsal passed`여야 한다. 최근 점검 기록은 `REHEARSAL.md`.
