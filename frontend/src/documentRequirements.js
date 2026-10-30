// 기존 입력은 보존하고 PDF 조건을 합친다. 한도 초과 시 조용히 자르지 않는다.
export function mergeDocumentRequirements(current, document, filename) {
  if (!Array.isArray(document?.conditions) || !document.conditions.length) {
    throw new Error('PDF에서 요구조건을 찾지 못했습니다. 기존 입력은 유지됩니다.');
  }
  const existing = new Set(current.split(/\r?\n/).map(line => line.trim()).filter(Boolean));
  const added = [];
  const sources = [];
  for (const item of document.conditions) {
    if (typeof item.text !== 'string' || !item.text.trim() || /[\r\n]/.test(item.text)
      || item.text.length > 500 || !['information', 'visual'].includes(item.type)
      || !Number.isInteger(item.page) || item.page < 1 || typeof item.source !== 'string' || !item.source.trim()) {
      throw new Error('PDF 조건 응답을 확인하지 못했습니다. 기존 입력은 유지됩니다.');
    }
    if (existing.has(item.text.trim())) continue;
    existing.add(item.text.trim());
    added.push(item.text.trim());
    const provenance = item.source_kind === 'pdf_page+paddleocr' ? ' · PDF 이미지 + OCR 판독문' : item.source_kind === 'paddleocr' ? ' · OCR 판독문' : '';
    const references = Array.isArray(item.source_references) ? item.source_references
      .filter(ref => typeof ref.source_id === 'string' && Number.isInteger(ref.page) && typeof ref.source === 'string')
      .map(({ source_id, page, source }) => ({ source_id, page, source }))
      .filter((ref, index, refs) => refs.findIndex(other => other.source_id === ref.source_id && other.page === ref.page) === index) : [];
    const pages = references.length ? [...new Set(references.map(ref => ref.page))].join('·') : item.page;
    const structured = typeof item.label === 'string' && typeof item.requirement === 'string'
      && typeof item.inspection === 'string' && ['필수', '권장', '선택', '명시 없음'].includes(item.level)
      ? { document_condition: { label: item.label, requirement: item.requirement, inspection: item.inspection, level: item.level,
        ...(references.length ? { sources: references } : {}) } } : {};
    sources.push({ text: item.text.trim(), type: item.type, ...structured,
      source: `${filename.slice(0, 100)} · ${pages}페이지${provenance}\n${item.source.slice(0, 350)}`.slice(0, 500) });
  }
  const requirements = [current.trim(), ...added].filter(Boolean).join('\n');
  if (requirements.length > 6000 || existing.size > 100) {
    throw new Error('기존 입력과 PDF 조건을 합치면 6,000자 또는 100개를 넘습니다. 입력을 정리한 뒤 다시 올려 주세요.');
  }
  return { requirements, sources, addedCount: added.length };
}
