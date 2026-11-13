# Rehearsal: ResultDemo · FeatureTiles (2026-11-12)

## ResultDemo — 검수 결과 화면

Gold: `hero-demo`(커서가 한 가지 일을 하고 결과가 손 아래에 나타나는 히어로).
가져온 것은 한 여백으로 앉힌 창이 아래로 흘러 나가는 구도, 이름 붙인 대상(`data-target`)으로만 움직이는 커서,
처음 화면으로 돌아오는 이유가 화면에 보이는 반복. 바꾼 것은 내용 전부(제품의 실제 결과 화면과 샘플 포스터 ‘빛의 기록’),
넓은 창 크기(1040px, 실제 크기), 좁은 화면에서 창 틀을 버리고 요구사항 카드 하나만 남긴 구도.

Beats: 1 첫 항목이 펼쳐진 결과 화면 … 2 위반 항목을 누름 → 기준·읽은 문구·판정 이유가 펼쳐지고 포스터의 근거 상자가 바뀜 …
3 ‘선택 근거 확대’ → 날짜가 확대됨(머무름 2.4초) … 4 ‘화면에 맞춤’ → 첫 항목을 다시 누름. 반복 약 13초(좁은 구도 약 9초).

Captured: 넓은 구도 1440(1152 실제 크기), 좁은 구도 1440·390, `--states`(넓은 구도), 움직임 줄임(넓은 구도 한 장).

Gate:
- 넓은 구도 `rehearsal passed`
- 좁은 구도 1440 `rehearsal passed`
- 좁은 구도 390 `rehearsal passed`

Film:
- `hard edge: img.rd-poster is cut by div.rd-view's edge while it moves` → 확대 장면 → 그대로 둠. 이미지 보기 영역 안에서 포스터를 확대하는 제품 동작 자체라 영역 가장자리에서 잘리는 것이 맞다(`data-crop`).
- `handoff shift: div.rd-rows replaces an identical element …`(좁은 구도) → 첫 측정 때 넓은 구도에서 좁은 구도로 바뀌며 목록이 한 번 다시 그려지는 순간 → 그대로 둠. 화면에 나타나기 전(`data-placing`)에 끝난다.

## FeatureTiles — 기준 · 근거 · 판단

Gold: `static-card`(위에 고정하고 아래로 흘린 한 장, 창 틀 없음). 가져온 것은 구도와 "실제 문구는 이야기에 필요한 것만".
바꾼 것은 내용(요구사항 목록의 확인 표시, 포스터의 전시 기간 상자와 읽은 문구, 담당자 판단 고르기),
첫 페이지 톤에 맞춘 단색·6px 모서리, 휴대폰 폭(354px)용 구도.

Beats: 각 그림이 화면에 들어올 때 한 번 — 확인 표시가 차례로 채워지고 ‘기준 확정’ / 상자가 나타나고 읽은 문구 / 판단이 ‘충족’으로 옮겨지고 확인 표시.

Captured: 1440(420 실제 크기), 422(좁은 구도 354).

Gate: 1440 `rehearsal passed` · 좁은 구도 `rehearsal passed`

## Review

별도의 검토자(새 눈) 검토는 하지 않았다. 촬영 사진은 만든 사람이 직접 보고 고쳤다.

## Not checked

- 실제 휴대폰·태블릿 기기, 터치 입력, Safari·Firefox(Chrome에서만 확인. `text-box: trim-both`를 모르는 브라우저에서는 배지·버튼 글자가 1~2px 낮게 앉을 수 있다)
- 어두운 테마(쓰지 않음)
- 넓은 구도의 ResultDemo는 지금 어느 화면에도 놓여 있지 않다(로그인 화면은 좁은 구도). `/stageshow`에서만 확인했다.
- 소개 화면의 3D 도해(`components/LayerFigure.jsx`)와 점 구체(`components/PixelField.jsx`)는 Stageshow 장면이 아니라 이 점검 대상이 아니다. 화면 사진으로만 확인했다.
