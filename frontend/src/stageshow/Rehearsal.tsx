import { ResultDemo } from './ResultDemo'
import { CriteriaTile, DecisionTile, EvidenceTile } from './FeatureTiles'

// 개발 서버 전용(/stageshow): 소개·로그인 화면에 쓰는 장면을 실제 쓰이는 너비로 늘어놓는다.
// 촬영 도구(Stageshow의 film.mjs)로 점검할 때 쓴다. 소개 화면의 첫 구역은 창 높이에 따라 늘어나
// 촬영 도중 장면 위치가 바뀌므로, 장면만 모은 이 화면에서 찍는다. 배포 빌드에는 들어가지 않는다.
const slot = { background: '#ecebe7' }

export default function Rehearsal() {
  return <main style={{ display: 'grid', gap: 40, padding: 32, background: '#fafaf8' }}>
    <section data-scene="result" style={{ width: 'min(1152px, 100%)' }}><ResultDemo /></section>
    <section data-scene="result-compact" style={{ width: 'min(358px, 100%)' }}><ResultDemo /></section>
    <section data-scene="tiles" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(0, 420px))', gap: 36 }}>
      <div style={slot}><CriteriaTile /></div>
      <div style={slot}><EvidenceTile /></div>
      <div style={slot}><DecisionTile /></div>
    </section>
  </main>
}
