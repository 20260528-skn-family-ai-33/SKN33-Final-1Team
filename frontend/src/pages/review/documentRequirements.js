// 기존 입력을 보존하면서 문서에서 추출한 조건을 합친다.
export function mergeDocumentRequirements(current, document, filename) {
  if (!Array.isArray(document?.conditions) || !document.conditions.length) {
    if (Array.isArray(document?.conditions) && document.framework?.run_id
      && Array.isArray(document.pending) && document.pending.length) {
      return { requirements: current, sources: [], addedCount: 0 };
    }
    throw new Error('문서에서 요구조건을 찾지 못했습니다. 기존 입력은 유지됩니다.');
  }
  const existing = new Set(current.split(/\r?\n/).map(line => line.trim()).filter(Boolean));
  const added = [];
  const sources = [];
  const isDocx = /\.docx$/i.test(filename);
  for (const item of document.conditions) {
    if (typeof item.text !== 'string' || !item.text.trim() || /[\r\n]/.test(item.text)
      || item.text.length > 500 || !['information', 'visual'].includes(item.type)
      || !Number.isInteger(item.page) || item.page < 1 || typeof item.source !== 'string' || !item.source.trim()) {
      throw new Error('문서의 조건 응답을 확인하지 못했습니다. 기존 입력은 유지됩니다.');
    }
    if (existing.has(item.text.trim())) continue;
    existing.add(item.text.trim());
    added.push(item.text.trim());
    const provenance = item.source_kind === 'a02_qwen' ? ' · Qwen 요구사항 초안'
      : item.source_kind === 'pdf_page+paddleocr' ? ' · PDF 이미지 + OCR 판독문' : item.source_kind === 'paddleocr' ? ' · OCR 판독문' : '';
    const references = Array.isArray(item.source_references) ? item.source_references
      .filter(ref => typeof ref.source_id === 'string' && Number.isInteger(ref.page) && typeof ref.source === 'string')
      .map(({ source_id, page, source }) => ({ source_id, page, source }))
      .filter((ref, index, refs) => refs.findIndex(other => other.source_id === ref.source_id && other.page === ref.page) === index) : [];
    const pages = references.length ? [...new Set(references.map(ref => ref.page))].join('·') : item.page;
    const location = isDocx ? '본문' : `${pages}페이지`;
    const structured = typeof item.label === 'string' && typeof item.requirement === 'string'
      && typeof item.inspection === 'string' && ['필수', '권장', '선택', '명시 없음'].includes(item.level)
      ? { document_condition: { label: item.label, requirement: item.requirement, inspection: item.inspection, level: item.level,
        ...(references.length ? { sources: references } : {}) } } : {};
    sources.push({ text: item.text.trim(), type: item.type, ...structured,
      source: `${filename.slice(0, 100)} · ${location}${provenance}\n${item.source.slice(0, 350)}`.slice(0, 500) });
  }
  const requirements = [current.trim(), ...added].filter(Boolean).join(document.framework ? '\n\n' : '\n');
  if (requirements.length > 6000 || existing.size > 100) {
    throw new Error('기존 입력과 문서 조건을 합치면 6,000자 또는 100개를 넘습니다. 입력을 정리한 뒤 다시 올려 주세요.');
  }
  return { requirements, sources, addedCount: added.length };
}

// 직접 입력과 문서 항목을 함께 검수하면 전체를 한 묶음의 글로 다시 분석한다(서버가 기준에 '추가'를 받지 않아, 문서 기준에 직접 입력만 덧붙일 수 없다).
// 그때 문서 항목을 한 줄로 적는다. 고치지 않은 항목은 문서에서 읽은 허용 범위·검사 방법(document_condition.inspection)을 뒤에 붙여,
// 다시 만든 기준에서 그 내용이 빠지지 않게 한다. 사람이 문구를 고친 항목은 고친 문구만 보낸다(이전 설명이 고친 내용과 어긋날 수 있다).
export function documentRowLine(row) {
  const text = row.text.trim();
  if (row.originalText !== undefined && text !== row.originalText) return text;
  const rule = (row.document_condition?.inspection || '').split(/[\r\n]+/).map(part => part.trim()).filter(Boolean).join(' ');
  return rule && !text.includes(rule) ? `${text} ${rule}` : text;
}

// 직접 입력 줄과 고른 문서 항목을 합친 요구사항 줄들. 같은 줄은 한 번만 넣는다.
// 허용 범위를 붙여 글자 수 한도를 넘으면 문구만으로 다시 합친다(이 경우 허용 범위는 빠진다).
export function combinedRequirementLines(direct, rows, limit = 6000) {
  const build = line => [...new Set([...direct.split(/\r?\n/), ...rows.map(line)].map(item => item.trim()).filter(Boolean))];
  const full = build(documentRowLine);
  return full.join('\n').length <= limit ? full : build(row => row.text);
}
