const IMAGE_PAGE = /^page_(\d+)_embedded_images_not_read$/;
const TEXT_PAGE = /^page_(\d+)_text_unavailable$/;
const INCOMPLETE_PAGE = /^page_(\d+)_model_output_incomplete$/;

function pageNumbers(warnings, pattern) {
  return (Array.isArray(warnings) ? warnings : []).map(warning => pattern.exec(warning))
    .filter(Boolean).map(match => Number(match[1]));
}

export const unreadImagePages = warnings => pageNumbers(warnings, IMAGE_PAGE);
export const unreadTextPages = warnings => pageNumbers(warnings, TEXT_PAGE);
export const incompleteOutputPages = warnings => pageNumbers(warnings, INCOMPLETE_PAGE);
