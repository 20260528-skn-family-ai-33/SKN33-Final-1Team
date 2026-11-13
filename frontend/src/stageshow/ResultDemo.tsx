import { Avatar, Badge, Bar, Button, Collapse, Row, Stage, Text, Window, type Act } from './kit'
import { Icon } from '../components/Icon.jsx'
import posterUrl from '../assets/sample-poster.png'
import './look.css'
import './result-demo.css'

// 소개 화면 히어로: 검수 결과 화면에서 위반 항목을 누르고 근거를 확대해 보는 흐름.
// 예시 자료(빛의 기록 포스터와 기준 3개)는 '샘플로 체험하기'가 불러오는 것과 같다.
// 실제 화면을 조작하지 않는 그림이다: a.click()은 누르는 모습만 그리고 a.set()이 장면을 바꾼다.

type S = { open: 1 | 3; zoomed: boolean }

// 결과 화면은 첫 항목이 펼쳐진 채로 열린다
const initial: S = { open: 1, zoomed: false }
// 움직임을 줄인 환경과 미리보기에서 보이는 한 장: 위반 항목이 펼쳐지고 근거가 확대된 상태
const POSTER: S = { open: 3, zoomed: true }

// 포스터 보기 영역(440×288) 안에서 포스터가 놓이는 자리. 확대하면 근거 상자의 왼쪽이 아래 버튼 글자와 같은 선에 온다.
const FIT = { x: 124, y: 16, w: 192, h: 256 }
const ZOOM = { x: -7, y: -356, w: 483, h: 644 }

type Tone = 'pass' | 'review' | 'fail'
type Mark = { n: number; id: string; tone: Tone; at: [number, number, number, number] }
// 근거 상자: 포스터 크기에 대한 비율(왼쪽, 위, 너비, 높이)
const MARKS: Mark[] = [
  { n: 1, id: '01', tone: 'pass', at: [0.073, 0.14, 0.64, 0.15] },
  { n: 2, id: '02', tone: 'review', at: [0.81, 0.822, 0.1, 0.076] },
  { n: 3, id: '03', tone: 'fail', at: [0.077, 0.815, 0.407, 0.08] },
]
const markBox = (mark: Mark, zoomed: boolean) => {
  const p = zoomed ? ZOOM : FIT
  return { left: p.x + mark.at[0] * p.w, top: p.y + mark.at[1] * p.h, width: mark.at[2] * p.w, height: mark.at[3] * p.h }
}

type Detail = { rule: string; read: string; reason: string; where: string }
const ROWS: { n: 1 | 2 | 3; id: string; title: string; tone: 'success' | 'warning' | 'danger'; label: string; detail?: Detail }[] = [
  { n: 1, id: '01', title: '전시 제목 표기', tone: 'success', label: '충족',
    detail: { rule: '전시 제목에 ‘빛의 기록’을 표시합니다.', read: '빛의 기록', reason: '제목 문구가 기준과 일치합니다.', where: '상단 전시 제목' } },
  { n: 2, id: '02', title: '주최 로고 일치', tone: 'warning', label: '확인 필요' },
  { n: 3, id: '03', title: '전시 기간 일치', tone: 'danger', label: '위반',
    detail: { rule: '전시 기간을 ‘11.20–11.28’로 표시합니다.', read: '11.20–11.29', reason: '종료일이 기준보다 하루 늦게 표기됐습니다.', where: '하단 전시 기간' } },
]

const FILTERS = [['전체', 3], ['충족', 1], ['확인 필요', 1], ['위반', 1]] as const

// 커서가 쉬는 자리: 누를 것이 없는 빈 면
const REST = { wide: { x: 838, y: 141 }, compact: { x: 206, y: 39 } }
const TOOLS_REST = { x: 300, y: 482 }
const aim = (row: number) => ({ at: `row-${row}`, x: 0.56, y: 0.62 })

async function script(a: Act<S>) {
  await a.wait(1100)

  // 1. 위반 항목을 누르면 그 항목의 기준·읽은 문구·판정 이유가 펼쳐지고 포스터의 근거도 따라 바뀐다
  await a.move(aim(3))
  await a.wait(220)
  await a.click()
  await a.set({ open: 3, zoomed: a.layout === 'compact' })
  a.mark('open')

  if (a.layout === 'compact') {
    await a.move('rest')
    await a.wait(3400)
  } else {
    await a.wait(1500)

    // 2. 선택한 근거를 확대해 포스터의 날짜를 직접 본다
    await a.move({ at: 'zoom', x: 0.9, y: 0.7 })
    await a.wait(200)
    await a.click()
    await a.set({ zoomed: true })
    a.mark('zoom')
    await a.move('tools-rest')
    await a.wait(2400)

    // 3. 화면에 맞춰 되돌린다
    await a.move({ at: 'fit', x: 0.86, y: 0.72 })
    await a.wait(160)
    await a.click()
    await a.set({ zoomed: false })
    await a.wait(650)
  }

  // 첫 항목으로 돌아간다: 처음 화면으로 돌아가는 이유가 화면에 보인다
  await a.move(aim(1))
  await a.wait(160)
  await a.click()
  await a.set({ open: 1, zoomed: false })
  await a.wait(450)
  await a.move('rest')
  await a.wait(500)
}

export function ResultDemo() {
  return (
    <Stage width={1152} height={592} compact={{ below: 720, width: 358, height: 470 }} initial={initial} poster={POSTER} script={script}
      cursor={{ at: REST }} fidelity="realistic" className="cc-look rd">
      {(s, _rect, layout) => {
        const wide = layout === 'wide'
        // 검수 요구사항 카드: 넓은 화면에서는 창 안의 오른쪽 카드, 좁은 화면에서는 이 카드 하나만 남긴다
        const list = <div className="rd-card rd-list">
          <div className="rd-head">
            <Text className="rd-head-title">검수 요구사항</Text>
            <Badge>3개 항목</Badge>
          </div>
          {wide && <div className="rd-filters">
            {FILTERS.map(([label, count], index) => <span key={label} className="rd-filter" data-on={index === 0 || undefined}>
              {label}<b>{count}</b>
            </span>)}
          </div>}
          <div className="rd-rows">
            {ROWS.map(row => <div key={row.id}>
              <Row data-target={`row-${row.n}`} selected={s.open === row.n} lead={<span className="rd-idx">{row.id}</span>} title={<Text>{row.title}</Text>}>
                <Badge tone={row.tone} dot>{row.label}</Badge>
              </Row>
              {row.detail && <Collapse open={s.open === row.n}>
                <div className="rd-detail-wrap" data-tone={row.tone}><div className="rd-detail">
                  <span className="rd-term">검수 기준</span>
                  <span className="rd-value">{row.detail.rule}</span>
                  <span className="rd-term">이미지에서 읽은 문구</span>
                  <span className="rd-value"><span className="rd-read">{row.detail.read}</span></span>
                  {wide && <>
                    <span className="rd-term">판정 이유</span>
                    <span className="rd-value">{row.detail.reason}</span>
                    <span className="rd-term">근거</span>
                    <span className="rd-value"><span className="rd-where"><Icon name="search" />{row.detail.where}</span></span>
                  </>}
                  {!wide && <span className="rd-crop" data-part={row.n === 1 ? 'title' : 'date'} data-crop style={{ backgroundImage: `url(${posterUrl})` }}>
                    <span className="rd-mark" data-tone={row.n === 1 ? 'pass' : 'fail'} data-on />
                  </span>}
                </div></div>
              </Collapse>}
            </div>)}
            {/* 좁은 화면: 목록이 아래로 이어진다는 것만 보이는 자리 줄 */}
            {!wide && [96, 72, 104].map(width => <Row key={width} lead={<Bar w={14} h={6} tone={2} />} title={<Bar w={width} h={6} tone={3} />}>
              <Bar w={44} h={20} r={10} tone={1} />
            </Row>)}
          </div>
        </div>
        return <>
          {wide ? <Window title="빛의 기록 · 전시 포스터" toolbar={<Avatar name="담당자" size={22} />} className="rd-window" body={{ className: 'rd-body' }}>
            <div className="rd-card rd-image">
              <div className="rd-head">
                <span className="rd-head-icon"><Icon name="image" /></span>
                <Text className="rd-head-title">검수 대상 이미지</Text>
              </div>
              {/* 보기 영역은 확대한 포스터를 일부러 자른다 */}
              <div className="rd-view" data-crop>
                <img className="rd-poster" src={posterUrl} alt="" width={ZOOM.w} height={ZOOM.h} draggable={false}
                  style={{ transform: s.zoomed ? `translate(${ZOOM.x}px, ${ZOOM.y}px) scale(1)` : `translate(${FIT.x}px, ${FIT.y}px) scale(${FIT.w / ZOOM.w})` }} />
                {MARKS.map(mark => <span key={mark.id} className="rd-mark" data-tone={mark.tone} data-on={s.open === mark.n || undefined}
                  data-off={(s.zoomed && s.open !== mark.n) || undefined} style={markBox(mark, s.zoomed)}>
                  <span className="rd-mark-tag">{mark.id}</span>
                </span>)}
              </div>
              <div className="rd-tools">
                <Button size="large" data-target="fit">화면에 맞춤</Button>
                <Button size="large" data-target="zoom" icon={<Icon name="search" />}>선택 근거 확대</Button>
              </div>
            </div>
            {list}
            {/* 창 아래로 이어지는 다음 구역: 자리만 잡는 줄이라 화면 밖으로 잘려도 된다 */}
            <div className="rd-below">
              <Bar w={132} h={8} tone={4} />
              <Bar w={418} h={6} tone={2} />
              <Bar w={356} h={6} tone={2} />
            </div>
          </Window> : list}
          <span data-target="rest" className="rd-spot" style={{ left: REST[layout].x, top: REST[layout].y }} />
          <span data-target="tools-rest" className="rd-spot" style={{ left: TOOLS_REST.x, top: TOOLS_REST.y }} />
        </>
      }}
    </Stage>
  )
}
