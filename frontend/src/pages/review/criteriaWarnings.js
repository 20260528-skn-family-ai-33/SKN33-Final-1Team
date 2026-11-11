const IMAGE_PAGE = /^page_(\d+)_embedded_images_not_read$/;

// A02가 글자층만 읽고 이미지는 읽지 않은 쪽 번호. 초안 경고(run.criteria.warnings)에서 꺼낸다.
export function unreadImagePages(warnings) {
  return (Array.isArray(warnings) ? warnings : []).map(warning => IMAGE_PAGE.exec(warning))
    .filter(Boolean).map(match => Number(match[1]));
}
