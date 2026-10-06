'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const state = { criteria: null, asset: null, run: null, samples: [], history: [], busy: false, selected: null, sampleId: null, topics: [], topicId: 'strawberry-latte', conditionId: null };
  const make = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
  const display = value => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const short = value => String(value || '—').slice(0, 12);
  const unwrap = (value, key) => value && value[key] !== undefined ? value[key] : value;
  const statusText = value => ({ completed: '검사 완료', complete: '검사 완료', success: '검사 완료', succeeded: '검사 완료', partial: '일부 검사 완료', not_run: '미실행', failed: '검사 실패', error: '검사 실패', running: '검사 중', pending: '대기 중', queued: '대기 중' }[value] || value || '상태 미상');
  function notify(message, type = 'error') { $('notice').textContent = message; $('notice').dataset.type = type; $('notice').hidden = !message; }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes((options.method || 'GET').toUpperCase())) {
      const cookie = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
      const token = cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : document.querySelector('meta[name="csrf-token"]')?.content;
      if (token) headers.set('X-CSRFToken', token);
    }
    if (options.body && !(options.body instanceof FormData)) { headers.set('Content-Type', 'application/json'); options.body = JSON.stringify(options.body); }
    const response = await fetch(path, { ...options, headers });
    const raw = await response.text();
    let data;
    try { data = raw ? JSON.parse(raw) : {}; } catch { throw new Error(response.ok ? '서버 응답을 읽을 수 없습니다.' : `서버 오류 (${response.status})`); }
    if (!response.ok) {
      const detail = data.detail || data.error || data.message;
      throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(x => x.msg || display(x)).join(' · ') : detail?.message || detail?.code || (detail ? display(detail) : `요청을 처리하지 못했습니다 (${response.status}).`));
    }
    return data;
  }
  function controls() {
    const ready = !!state.criteria?.confirmed;
    const matchingAsset = state.asset && (state.asset.topic_id || 'strawberry-latte') === state.topicId && (state.asset.condition_id || null) === state.conditionId;
    $('confirm-criteria').disabled = state.busy || !state.criteria || ready || !state.criteria.can_confirm;
    $('confirm-criteria').textContent = ready ? '확정됨' : '이 기준으로 확정';
    $('run-button').disabled = state.busy || !ready || !matchingAsset;
    $('file-input').disabled = state.busy || !state.criteria?.can_confirm;
    $('refresh-history').disabled = state.busy;
    document.querySelectorAll('.sample-button,.history-item,.choice-button').forEach(button => { button.disabled = state.busy; });
    $('run-hint').textContent = state.busy ? '요청을 처리하고 있습니다. 잠시 기다려 주세요.' : !state.criteria?.can_confirm ? '샘플 또는 발주 조건을 선택해 주세요.' : !ready ? '검사 기준을 먼저 확인하고 확정해 주세요.' : !matchingAsset ? '현재 기준으로 검사할 이미지를 선택해 주세요.' : '선택한 파일과 확정 기준으로 검사합니다.';
    $('run-button').classList.toggle('is-loading', state.busy);
  }
  async function action(fn) {
    if (state.busy) return;
    state.busy = true; notify(''); controls();
    try { await fn(); } catch (error) { notify(error.message || '요청 중 오류가 발생했습니다.'); }
    finally { state.busy = false; controls(); }
  }
  function renderCriteria() {
    const c = state.criteria;
    $('criteria-list').replaceChildren();
    (c.rules || []).forEach(rule => {
      const row = make('div', undefined, 'criterion-row');
      row.append(make('span', rule.id, 'criterion-code'), make('span', rule.name, 'criterion-name'), make('span', display(rule.expected), 'criterion-value'));
      row.title = rule.check || ''; $('criteria-list').append(row);
    });
    $('criteria-status').textContent = c.confirmed ? '확정된 기준' : '확인 후 확정';
    $('criteria-status').classList.toggle('confirmed', !!c.confirmed);
    $('criteria-version').textContent = '기준 버전 ' + display(c.version);
    $('criteria-context').textContent = c.topic_label + (c.condition_id ? ' · 발주 ' + c.condition_id.slice(-2) + ' · 사전 등록된 기대 수량 적용' : '') + (c.historical ? ' · 저장 당시 기준 (다시 검사하려면 샘플을 선택하세요)' : '');
    $('condition-picker').hidden = !c.condition_options?.length;
    $('condition-list').replaceChildren();
    (c.condition_options || []).forEach(option => {
      const button = make('button', option.label, 'choice-button'); button.type = 'button'; button.disabled = state.busy;
      button.append(make('small', option.expected)); button.setAttribute('aria-pressed', String(state.conditionId === option.id));
      button.addEventListener('click', () => action(async () => { await loadTopic(state.topicId, option.id); }));
      $('condition-list').append(button);
    });
  }
  function criteriaUrl(topicId = state.topicId, conditionId = state.conditionId) {
    const params = new URLSearchParams({ topic_id: topicId });
    if (conditionId) params.set('condition_id', conditionId);
    return '/api/criteria?' + params;
  }
  function renderTopics() {
    $('topic-list').replaceChildren();
    state.topics.forEach(topic => {
      const button = make('button', topic.label, 'choice-button'); button.type = 'button'; button.disabled = state.busy;
      button.setAttribute('aria-pressed', String(state.topicId === topic.id));
      button.addEventListener('click', () => action(() => loadTopic(topic.id)));
      $('topic-list').append(button);
    });
  }
  function resetAsset() {
    state.asset = null; state.sampleId = null; clearRun();
    $('asset-image').removeAttribute('src'); $('image-stage').hidden = true; $('image-empty').hidden = false;
    $('image-name').textContent = '검사 대상 이미지'; $('image-version').textContent = '파일 버전 —';
    $('asset-info').textContent = '선택한 이미지가 없습니다.'; $('file-input').value = '';
  }
  async function loadTopic(topicId, conditionId = null) {
    const [criteria, samples] = await Promise.all([api(criteriaUrl(topicId, conditionId)), api('/api/samples?topic_id=' + encodeURIComponent(topicId))]);
    state.topicId = topicId; state.conditionId = conditionId; state.criteria = criteria; state.samples = samples;
    resetAsset(); renderTopics(); renderCriteria(); renderSamples(); controls();
  }
  function safeImageUrl(value) {
    if (typeof value !== 'string') return '';
    try { const url = new URL(value, location.href); return url.origin === location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; }
  }
  function renderSamples() {
    $('sample-list').replaceChildren();
    state.samples.forEach((sample, index) => {
      const button = make('button', undefined, 'sample-button'); button.type = 'button';
      const label = '샘플 ' + String(index + 1).padStart(2, '0');
      button.setAttribute('aria-label', label + ' 선택'); button.setAttribute('aria-pressed', String(state.sampleId === sample.id));
      const url = safeImageUrl(sample.url);
      if (url) { const img = make('img'); img.src = url; img.alt = ''; img.loading = 'lazy'; button.append(img); }
      button.append(make('span', label)); button.addEventListener('click', () => action(async () => {
        const result = await api('/api/assets/from-sample', { method: 'POST', body: { sample_id: sample.id, topic_id: state.topicId } });
        state.conditionId = result.condition_id || null;
        state.criteria = await api(criteriaUrl()); renderCriteria();
        setAsset(unwrap(result, 'asset')); state.sampleId = sample.id; renderSamples(); controls();
      })); $('sample-list').append(button);
    });
    if (!state.samples.length) $('sample-list').append(make('p', '등록된 샘플이 없습니다. 이미지를 직접 올려 주세요.', 'muted'));
  }
  function setAsset(asset, preserveRun = false) {
    if (!asset || !asset.id) throw new Error('파일 정보에 식별자가 없습니다. 다시 선택해 주세요.');
    state.asset = asset; state.sampleId = null;
    $('image-name').textContent = asset.name || '검사 대상 이미지';
    $('image-version').textContent = '파일 ' + short(asset.id) + (asset.version != null ? ' · 버전 ' + asset.version : '') + ' · SHA ' + short(asset.sha256);
    $('asset-info').textContent = (asset.name || '선택 이미지') + ' · ' + (asset.width && asset.height ? `${asset.width} × ${asset.height}px` : '규격 확인 중') + ' · 파일 ' + short(asset.id);
    const url = safeImageUrl(asset.url);
    $('image-stage').hidden = !url; $('image-empty').hidden = !!url;
    $('image-stage').style.aspectRatio = asset.width && asset.height ? `${asset.width} / ${asset.height}` : '1';
    $('asset-image').alt = (asset.name || '검사 대상') + ' 원본';
    if (url) $('asset-image').src = url; else $('asset-image').removeAttribute('src');
    if (!preserveRun) clearRun();
    controls();
  }
  function clearRun() {
    state.run = null; state.selected = null; clearOverlay();
    $('results').replaceChildren();
    const empty = make('div', undefined, 'results-empty'); empty.append(make('span', 'READY TO INSPECT', 'mini-label'), make('p', '선택한 이미지로 검사를 실행해 주세요.')); $('results').append(empty);
    $('result-summary').hidden = true; $('result-count').textContent = '아직 실행하지 않음';
    $('run-status').textContent = '실행 전'; $('run-status').className = 'pill';
    $('trace-list').replaceChildren(make('li', '검사 실행 후 기록을 확인할 수 있습니다.', 'muted'));
    $('run-meta').hidden = true; document.querySelectorAll('.history-item').forEach(b => b.removeAttribute('aria-current'));
  }
  function clearOverlay() {
    state.selected = null; $('evidence-overlay').replaceChildren(); $('clear-selection').disabled = true;
    $('image-caption').textContent = '표시된 영역은 검사 결과가 제공한 근거입니다.';
    document.querySelectorAll('.result-card').forEach(card => { card.classList.remove('selected'); card.querySelector('.result-trigger')?.setAttribute('aria-expanded', 'false'); const detail = card.querySelector('.result-detail'); if (detail) detail.hidden = true; });
  }
  function selectResult(result, card) {
    const wasSelected = state.selected === result.rule_id;
    clearOverlay(); if (wasSelected) return;
    state.selected = result.rule_id; card.classList.add('selected'); card.querySelector('.result-trigger').setAttribute('aria-expanded', 'true'); card.querySelector('.result-detail').hidden = false;
    let count = 0;
    for (const evidence of Array.isArray(result.evidence) ? result.evidence : []) {
      const b = evidence.bbox;
      if (!Array.isArray(b) || b.length !== 4 || !b.every(Number.isFinite)) continue;
      const [x, y, width, height] = b;
      if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1.001 || y + height > 1.001) continue;
      const box = make('div', undefined, 'evidence-box'); box.style.left = `${x * 100}%`; box.style.top = `${y * 100}%`; box.style.width = `${Math.min(width, 1 - x) * 100}%`; box.style.height = `${Math.min(height, 1 - y) * 100}%`;
      box.append(make('span', result.rule_id + (count ? ' · ' + (count + 1) : ''))); $('evidence-overlay').append(box); count++;
    }
    $('clear-selection').disabled = false;
    $('image-caption').textContent = (result.name || result.rule_id) + ' · ' + (count ? `근거 영역 ${count}개. 상세 관찰은 오른쪽 결과를 확인하세요.` : '제공된 이미지 영역이 없습니다. 관찰 내용과 확인 필요 사유를 확인하세요.');
  }
  function resultLabel(result) {
    const failed = ['failed', 'failure', 'error', '실패'].includes(result.execution_status);
    if (failed) return ['검사 실패', ''];
    if (result.verdict === '충족') return ['충족', 'pass'];
    if (result.verdict === '미충족') return ['미충족', 'fail'];
    if (result.verdict === '확인 필요') return ['확인 필요', 'review'];
    return [result.execution_status === 'running' ? '검사 중' : '미판정', ''];
  }
  function renderRun(run) {
    state.run = run; clearOverlay();
    $('run-status').textContent = statusText(run.status); $('run-status').className = 'pill';
    const results = Array.isArray(run.results) ? run.results : [];
    const executed = results.filter(r => ['succeeded', 'completed'].includes(r.execution_status)).length;
    $('result-count').textContent = '검사 실행 ' + executed + ' / ' + results.length + '개';
    const summary = new Map(); results.forEach(r => { const label = resultLabel(r)[0]; summary.set(label, (summary.get(label) || 0) + 1); });
    $('result-summary').replaceChildren(...Array.from(summary, ([label, n]) => make('span', `${label} ${n}`))); $('result-summary').hidden = !results.length;
    $('results').replaceChildren();
    results.forEach((result, index) => {
      const card = make('div', undefined, 'result-card'), trigger = make('button', undefined, 'result-trigger'); trigger.type = 'button'; trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-controls', 'result-detail-' + index);
      const [label, cls] = resultLabel(result); trigger.append(make('span', result.rule_id || '—', 'criterion-code'), make('strong', result.name || state.criteria?.rules?.find(r => r.id === result.rule_id)?.name || '검사 항목'), make('span', label, 'verdict ' + cls));
      const detail = make('div', undefined, 'result-detail'); detail.id = 'result-detail-' + index; detail.hidden = true;
      const dl = make('dl'); [['실행 상태', statusText(result.execution_status)], ['기대값', result.expected], ['관찰값', result.observed], ['검사 방법', result.method || run.method]].forEach(([key, value]) => dl.append(make('dt', key), make('dd', display(value)))); detail.append(dl, make('p', result.reason || '추가 사유가 제공되지 않았습니다.', 'result-reason'));
      (Array.isArray(result.evidence) ? result.evidence : []).forEach(e => { if (e.text) detail.append(make('p', e.text, 'evidence-text')); });
      trigger.addEventListener('click', () => selectResult(result, card)); card.append(trigger, detail); $('results').append(card);
    });
    if (!results.length) $('results').append(make('p', run.error || '반환된 조건별 결과가 없습니다. 실행 기록을 확인해 주세요.', 'muted'));
    renderTrace(run); renderHistory();
  }
  function renderTrace(run) {
    $('run-meta').hidden = false;
    $('run-meta').textContent = '실행 ' + display(run.id) + ' · 기준 ' + display(run.criteria_version) + ' · 파일 ' + display(run.asset_id || state.asset?.id) + (run.method ? ' · ' + display(run.method) : '');
    $('trace-list').replaceChildren();
    const trace = Array.isArray(run.trace) ? run.trace : run.trace ? [run.trace] : [];
    trace.forEach(entry => {
      const li = make('li');
      if (typeof entry === 'string') li.textContent = entry;
      else { const title = entry.method || entry.step || entry.name || entry.event || entry.tool || entry.node || '실행 기록'; li.append(make('strong', display(title)), make('span', display(entry.message || entry.detail || entry.output || [entry.status === 'succeeded' ? '성공' : entry.status === 'failed' ? '실패' : entry.status, entry.tool, entry.device ? entry.device.toUpperCase() : '', entry.fallback_reason, entry.duration_ms !== undefined ? entry.duration_ms + 'ms' : '', entry.protocol_version ? '프로토콜 ' + entry.protocol_version : ''].filter(Boolean).join(' · ')))); }
      $('trace-list').append(li);
    });
    if (!trace.length) $('trace-list').append(make('li', '서버가 세부 실행 기록을 제공하지 않았습니다.'));
    if (run.error) $('trace-list').append(make('li', '오류 · ' + display(run.error)));
  }
  function renderHistory() {
    $('history-list').replaceChildren();
    state.history.forEach(run => {
      const button = make('button', undefined, 'history-item'); button.type = 'button'; button.disabled = state.busy; button.setAttribute('aria-current', String(state.run?.id === run.id));
      const info = make('span'); info.append(make('strong', (run.topic_label || '딸기라떼') + ' · ' + (run.asset_name || run.asset?.name || '검사 ' + short(run.id))));
      const time = run.created_at ? new Date(run.created_at) : null;
      info.append(make('small', (time && !Number.isNaN(time.getTime()) ? time.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) + ' · ' : '') + '기준 ' + display(run.criteria_version) + ' · ' + short(run.id)));
      button.append(info, make('span', statusText(run.status), 'pill'));
      button.addEventListener('click', () => action(async () => {
        const loaded = unwrap(await api('/api/runs/' + encodeURIComponent(run.id)), 'run');
        let asset = loaded.asset || run.asset;
        if (!asset && loaded.asset_url) asset = { id: loaded.asset_id, name: loaded.asset_name, url: loaded.asset_url, width: loaded.width, height: loaded.height, sha256: loaded.sha256 };
        if (asset) {
          await loadTopic(asset.topic_id || 'strawberry-latte', asset.condition_id || null);
          if (loaded.criteria_snapshot && loaded.criteria_version !== state.criteria.version) {
            state.criteria = { ...state.criteria, ...loaded.criteria_snapshot, historical: true, confirmed: false, can_confirm: false };
          }
          renderCriteria(); setAsset(asset, true); state.sampleId = asset.sample_id || null; renderSamples();
        }
        else if (state.asset?.id !== loaded.asset_id) { state.asset = null; $('image-stage').hidden = true; $('image-empty').hidden = false; $('image-name').textContent = '저장된 검사 이미지'; $('image-version').textContent = '파일 ' + display(loaded.asset_id) + ' · 이미지 주소 없음'; $('asset-info').textContent = '이 이력의 이미지를 다시 검사하려면 파일을 선택해 주세요.'; }
        renderRun(loaded); $('run-status').scrollIntoView({ behavior: 'smooth', block: 'center' });
      })); $('history-list').append(button);
    });
    if (!state.history.length) $('history-list').append(make('p', '아직 저장된 검사가 없습니다.', 'muted'));
  }
  async function loadHistory() { const value = await api('/api/runs'); state.history = Array.isArray(value) ? value : value.runs || value.items || []; renderHistory(); }
  async function upload(file) {
    if (!file || state.busy) return;
    await action(async () => { const form = new FormData(); form.append('file', file); form.append('topic_id', state.topicId); if (state.conditionId) form.append('condition_id', state.conditionId); setAsset(unwrap(await api('/api/assets', { method: 'POST', body: form }), 'asset')); renderSamples(); });
    $('file-input').value = '';
  }
  $('file-input').addEventListener('change', event => upload(event.target.files[0]));
  const drop = document.querySelector('.upload');
  ['dragenter', 'dragover'].forEach(type => drop.addEventListener(type, e => { e.preventDefault(); if (!state.busy) drop.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(type => drop.addEventListener(type, e => { e.preventDefault(); drop.classList.remove('dragover'); }));
  drop.addEventListener('drop', e => upload(e.dataTransfer?.files?.[0]));
  $('clear-selection').addEventListener('click', clearOverlay);
  $('confirm-criteria').addEventListener('click', () => action(async () => {
    state.criteria = await api('/api/criteria/confirm', { method: 'POST', body: { version: state.criteria.version, topic_id: state.topicId, condition_id: state.conditionId } }); renderCriteria();
    notify(state.criteria.confirmed ? '검사 기준을 확정했습니다. 이미지를 선택하고 검사를 실행하세요.' : '기준 확정 상태를 확인하지 못했습니다.', state.criteria.confirmed ? 'success' : 'error');
  }));
  $('run-button').addEventListener('click', () => action(async () => {
    clearRun();
    $('run-label').textContent = '검사 중'; $('run-status').textContent = '실제 검사 중'; $('run-status').className = 'pill busy';
    clearOverlay();
    try {
      const key = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      const run = unwrap(await api('/api/runs', { method: 'POST', body: { asset_id: state.asset.id, criteria_version: state.criteria.version, topic_id: state.topicId, idempotency_key: key } }), 'run');
      renderRun(run);
      try { await loadHistory(); } catch { notify('검사 결과는 받았지만 저장 이력을 다시 불러오지 못했습니다. 새로고침으로 확인해 주세요.'); }
    } catch (error) { $('run-status').textContent = '실행 요청 실패'; $('run-status').className = 'pill'; throw error; }
    finally { $('run-label').textContent = '검사 실행'; }
  }));
  $('refresh-history').addEventListener('click', () => action(loadHistory));
  $('asset-image').addEventListener('load', () => { const img = $('asset-image'); if (img.naturalWidth && img.naturalHeight) $('image-stage').style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`; });
  $('asset-image').addEventListener('error', () => { $('image-stage').hidden = true; $('image-empty').hidden = false; notify('이미지를 표시하지 못했습니다. 파일을 다시 선택하거나 저장 경로를 확인해 주세요.'); });
  async function init() {
    state.busy = true; controls();
    const results = await Promise.allSettled([
      api('/api/health').then(health => { $('health-title').textContent = health.ocr_available ? (health.ocr_engine || '로컬 OCR 사용 가능') : 'OCR 준비 필요'; $('health-description').textContent = health.ocr_available ? ['이미지의 문구를 로컬에서 읽어 비교합니다. 객체 개수·크기·외관은 사람의 확인이 필요합니다.', health.ocr_fallback_reason].filter(Boolean).join(' ') : (health.note || '현재 문구 판독 도구를 사용할 수 없습니다. 검사 결과의 실패 사유를 확인해 주세요.'); }),
      loadTopic(state.topicId),
      api('/api/topics').then(value => { state.topics = value; renderTopics(); }),
      loadHistory()
    ]);
    const failed = results.filter(r => r.status === 'rejected');
    if (failed.length) { notify('일부 데이터를 불러오지 못했습니다. ' + failed.map(r => r.reason.message).join(' · ')); if (results[0].status === 'rejected') { $('health-title').textContent = '서버 연결 확인 필요'; $('health-description').textContent = '로컬 서버를 실행한 주소에서 이 화면을 열어 주세요.'; } if (results[1].status === 'rejected') { $('criteria-list').replaceChildren(make('p', '검사 기준을 불러오지 못했습니다.', 'muted')); $('criteria-status').textContent = '연결 오류'; } if (results[3].status === 'rejected') $('history-list').replaceChildren(make('p', '저장 이력을 불러오지 못했습니다.', 'muted')); }
    state.busy = false; controls();
  }
  init();
})();
