// Keep selection tied to the criterion, even when the visible list changes order.
export function filterResults(results, filter = 'all', query = '') {
  const term = query.trim().toLocaleLowerCase();
  return results.filter(result => {
    if (filter !== 'all' && result.status !== filter) return false;
    if (!term) return true;
    return [result.criterion, result.reason, result.observed_text,
      ...(result.evidence || []).map(item => item.text)]
      .filter(Boolean).join(' ').toLocaleLowerCase().includes(term);
  });
}

export function selectVisibleResult(visible, criterionId) {
  return visible.find(result => result.criterion_id === criterionId) || visible[0] || null;
}

export function evidenceOverlays(results) {
  return results.flatMap(result => (result.evidence || []).flatMap(evidence => {
    const box = evidence.bbox_xyxy;
    if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite)
      || box[2] <= box[0] || box[3] <= box[1]) return [];
    return [{ id: result.criterion_id, bbox_xyxy: box, label: result.criterion,
      text: evidence.text || result.criterion }];
  }));
}
