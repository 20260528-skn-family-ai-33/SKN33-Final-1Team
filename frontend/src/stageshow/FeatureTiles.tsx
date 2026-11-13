import { Badge, Bar, Check, Roll, Row, Segmented, Stage, Text, Tooltip, type Act } from './kit'
import { Icon } from '../components/Icon.jsx'
import posterUrl from '../assets/landing-demo-poster.jpg'
import './look.css'
import './feature-tiles.css'

// 기능 소개의 세 그림: 기준 · 근거 · 판단. 각각 한 가지만 보여 주고, 화면에 들어올 때 한 번만 움직인다.
// 창 틀 없이 부품 하나를 실제 크기로 올려 그림 밖으로 흘려보낸다(그림마다 Stage 하나, 문구는 페이지가 맡는다).
// 첫 페이지의 단색 톤을 따른다: 색은 포스터 한 장뿐이고 상태는 글자와 채움으로 구분한다.

const W = 420
const H = 210
// 휴대폰 폭에서는 줄이지 않고 그 폭(354px)에 맞춘 구도로 바꿔 글자를 제 크기로 그린다
const COMPACT = { below: 400, width: 354, height: 210 }

// 01 기준: 문서에서 정리한 항목을 담당자가 하나씩 확인해 확정한다
type CriteriaState = { pending: number; done: number; locked: boolean }
const criteriaStart: CriteriaState = { pending: 0, done: 0, locked: false }
const criteriaEnd: CriteriaState = { pending: 0, done: 3, locked: true }
const CRITERIA = ['행사명과 주최', '전시 기간과 장소', '필수 로고']

async function confirmCriteria(a: Act<CriteriaState>) {
  await a.wait(500)
  for (let item = 1; item <= CRITERIA.length; item++) {
    await a.set({ pending: item })
    await a.wait(480)
    await a.set({ done: item, pending: 0 })
    await a.wait(160)
  }
  await a.wait(120)
  await a.set({ locked: true })
}

export function CriteriaTile() {
  return (
    <Stage width={W} height={H} compact={COMPACT} initial={criteriaStart} poster={criteriaEnd} script={confirmCriteria} play="once" className="cc-look ft">
      {s => <div className="ft-sheet">
        <div className="ft-sheet-head">
          <span className="ft-file"><Icon name="file" /></span>
          <Text className="ft-sheet-title">전시 포스터_요구사항.pdf</Text>
          <Badge tone={s.locked ? 'success' : 'neutral'}><Roll variant="fade">{s.locked ? '기준 확정' : '검토 중'}</Roll></Badge>
        </div>
        <div className="ft-list">
          {CRITERIA.map((label, index) => <Row key={label} pad={10}
            lead={<Check on={index < s.done} pending={index + 1 === s.pending} size={16} />} title={<Text>{label}</Text>} />)}
          <Row pad={10} lead={<Check on={false} size={16} />} title={<Bar w={118} h={6} tone={3} />} />
        </div>
      </div>}
    </Stage>
  )
}

// 02 근거: 포스터에서 읽은 문구와 그 위치를 함께 보여 준다(검수 예시와 같은 포스터의 전시 기간)
type EvidenceState = { found: boolean; read: boolean }
const evidenceStart: EvidenceState = { found: false, read: false }
const evidenceEnd: EvidenceState = { found: true, read: true }

async function readPoster(a: Act<EvidenceState>) {
  await a.wait(500)
  await a.set({ found: true })
  await a.wait(520)
  await a.set({ read: true })
}

export function EvidenceTile() {
  return (
    <Stage width={W} height={H} compact={COMPACT} initial={evidenceStart} poster={evidenceEnd} script={readPoster} play="once" className="cc-look ft">
      {(s, rect) => <>
        <img className="ft-poster" src={posterUrl} alt="" width={520} height={520} draggable={false} />
        <span className="ft-mark" data-target="date-box" data-show={s.found || undefined} />
        <Tooltip className="ft-readout" side="above" show={s.read} at={rect('date-box')} gap={9}>2025.11.19.-2026.3.22.</Tooltip>
      </>}
    </Stage>
  )
}

// 03 판단: 자동 판정은 그대로 두고, 확인이 필요한 항목은 담당자가 직접 보고 기록한다
type DecisionState = { pick: number; checked: boolean }
const decisionStart: DecisionState = { pick: 0, checked: false }
const decisionEnd: DecisionState = { pick: 1, checked: true }

async function decide(a: Act<DecisionState>) {
  await a.wait(700)
  await a.set({ pick: 1 })
  await a.wait(520)
  await a.set({ checked: true })
}

export function DecisionTile() {
  return (
    <Stage width={W} height={H} compact={COMPACT} initial={decisionStart} poster={decisionEnd} script={decide} play="once" className="cc-look ft">
      {s => <div className="ft-sheet">
        <div className="ft-list ft-list-top">
          <Row pad={10} title={<Text>주최 로고 일치</Text>}><Badge tone="warning" dot>확인 필요</Badge></Row>
        </div>
        <div className="ft-decision">
          <Text className="ft-label">담당자 판단</Text>
          <Segmented items={['미확인', '충족', '불충족']} value={s.pick} />
          <div className="ft-confirm">
            <Check on={s.checked} size={16} />
            <Text className="ft-confirm-text">현재 포스터를 직접 확인했습니다</Text>
          </div>
          <Bar w={216} h={6} tone={2} />
        </div>
      </div>}
    </Stage>
  )
}
