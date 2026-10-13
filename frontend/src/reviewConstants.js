// 검수 화면에서 함께 사용하는 단계와 판정 표시 문구를 정의한다.
export const labels = { input: '자료 입력', criteria: '기준 확인', running: '검수 실행', results: '결과 확인' };
export const statuses = { pass: '충족', fail: '위반', review: '확인 필요' };
export const stageLabels = { queued: '검수 순서를 기다리고 있어요.', preprocessing: '이미지를 읽기 좋은 크기로 준비하고 있어요.', ocr: '이미지에서 문구와 위치를 읽고 있어요.', review: '확정한 기준과 추출 문구를 비교하고 있어요.', completed: '검수를 완료했어요.', failed: '검수를 완료하지 못했어요.' };
