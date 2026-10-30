import React from 'react';

export default function ImageResize({ label, value = {}, onChange, disabled }) {
  value = { width: '', height: '', percent: '', ...value };
  return <>
    <fieldset className="mt-similarity-resize" disabled={disabled}><legend>{label} 다운사이징</legend>
      <div className="mt-sampling-fields"><label>입력 방식<select aria-label={`${label} 크기 입력 방식`} value={value.mode || 'pixels'} onChange={event => onChange({ ...value, mode: event.target.value })}><option value="pixels">크기 (px)</option><option value="percent">원본 대비 비율 (%)</option></select></label>
        {value.mode === 'percent' ? <label>원본 대비 비율 (%)<input aria-label={`${label} 축소 비율`} type="number" min={0.01} max={100} step="any" placeholder="100 (원본)" value={value.percent ?? ''} onChange={event => onChange({ ...value, percent: event.target.value })} /></label> : <>
          <label>가로 (px)<input aria-label={`${label} 가로`} type="number" min={1} max={8192} step={1} placeholder="원본" value={value.width ?? ''} onChange={event => onChange({ ...value, width: event.target.value })} /></label>
          <span aria-hidden="true">×</span><label>세로 (px)<input aria-label={`${label} 세로`} type="number" min={1} max={8192} step={1} placeholder="원본" value={value.height ?? ''} onChange={event => onChange({ ...value, height: event.target.value })} /></label>
        </>}
        <button type="button" className="btn secondary small" onClick={() => onChange({ ...value, width: '', height: '', percent: '' })}>원본 크기 사용</button>
      </div>
    </fieldset>
    <p className="mt-hint">{label === '대조 이미지' && '모든 대조 이미지에 같은 설정을 적용합니다. '}{value.mode === 'percent' ? '각 이미지의 원본 가로·세로를 같은 비율로 실제 축소합니다. 50%는 각 변의 절반이며, 계산한 크기는 정수 px로 반올림하고 최소 1px을 유지합니다. 비워 두거나 100%를 입력하면 원본 크기를 사용합니다.' : '비워 두면 원본 크기를 사용합니다. 가로·세로를 입력하면 실제 축소하며, 원본보다 큰 크기는 사용할 수 없습니다. 가로·세로 비율은 입력값을 따릅니다.'} 모델별 입력 전처리는 이후 적용됩니다.</p>
  </>;
}
