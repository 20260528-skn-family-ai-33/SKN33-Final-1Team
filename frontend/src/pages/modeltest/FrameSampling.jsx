import React from 'react';
import ModeltestHelp from './ModeltestHelp.jsx';

export default function FrameSampling({ sampling, onChange, disabled, agentMode }) {
  return <section id="mt-stage-frames" className="mt-sampling mt-frame-sampling" aria-labelledby="mt-sampling-title">
    <div className="mt-stage-heading"><div className="mt-title-help"><h3 id="mt-sampling-title">프레임 이미지 추출</h3><ModeltestHelp label="프레임 이미지 추출"><p>프레임 또는 시간 간격으로 샘플링합니다.</p><p>최대 추출 수에 도달하면 샘플링을 멈춥니다. 모델 추론은 추출된 프레임에 수행합니다.</p></ModeltestHelp></div><span className="mt-tag">영상 처리</span></div>
    <div className="mt-sampling-fields">
      <label>기준<select id="mt-sampling-unit" aria-label={agentMode ? '에이전트 프레임 추출 기준' : '기준'} disabled={disabled} value={sampling.unit}
        onChange={event => onChange({ ...sampling, unit: event.target.value, interval: event.target.value === 'frames' ? '30' : '1' })}>
        <option value="frames">N프레임마다</option><option value="seconds">N초마다</option></select></label>
      <label>간격<input aria-label={agentMode ? '에이전트 프레임 추출 간격' : undefined} type="number" min={sampling.unit === 'frames' ? 1 : 0.01} max={3600} step={sampling.unit === 'frames' ? 1 : 'any'} value={sampling.interval} disabled={disabled}
        onChange={event => onChange({ ...sampling, interval: event.target.value })} /></label>
      <label>최대 추출 수<input aria-label={agentMode ? '에이전트 최대 추출 수' : undefined} type="number" min={1} max={1000} step={1} value={sampling.max_frames} disabled={disabled}
        onChange={event => onChange({ ...sampling, max_frames: event.target.value })} /></label>
    </div>
  </section>;
}
