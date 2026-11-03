"""Alignment, OCR and color evidence for paired logos; no model loading."""
import math
from pathlib import Path


def _logo_text(value):
    import unicodedata
    return ''.join(char for char in unicodedata.normalize('NFC', str(value or '')).casefold() if char.isalnum())


def _logo_hangul_text(result):
    import unicodedata
    if not isinstance(result, dict): return ''
    value = str(result.get('text') or '')
    hangul = ''.join(char for char in unicodedata.normalize('NFC', value) if '가' <= char <= '힣')
    if hangul: return hangul
    value = '\n'.join(str(word.get('text') or '') for word in result.get('ocr_words') or [] if isinstance(word, dict))
    return ''.join(char for char in unicodedata.normalize('NFC', value) if '가' <= char <= '힣')


def _edit_distance(first, second):
    previous = list(range(len(second) + 1))
    for row, char_a in enumerate(first, 1):
        current = [row]
        for column, char_b in enumerate(second, 1):
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def _delta_e_2000(first_lab, second_lab, np):
    a = first_lab.astype(np.float32).copy(); b = second_lab.astype(np.float32).copy()
    a[..., 0] *= 100 / 255; a[..., 1:] -= 128; b[..., 0] *= 100 / 255; b[..., 1:] -= 128
    l1, a1, b1 = (a[..., index] for index in range(3)); l2, a2, b2 = (b[..., index] for index in range(3))
    c1 = np.hypot(a1, b1); c2 = np.hypot(a2, b2); cbar = (c1 + c2) / 2
    g = .5 * (1 - np.sqrt(cbar ** 7 / (cbar ** 7 + 25 ** 7 + 1e-12)))
    ap1 = (1 + g) * a1; ap2 = (1 + g) * a2; cp1 = np.hypot(ap1, b1); cp2 = np.hypot(ap2, b2)
    hp1 = np.degrees(np.arctan2(b1, ap1)) % 360; hp2 = np.degrees(np.arctan2(b2, ap2)) % 360
    zero = (cp1 * cp2) < 1e-12; dh = hp2 - hp1
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh)); dh = np.where(zero, 0, dh)
    dl = l2 - l1; dc = cp2 - cp1; d_h = 2 * np.sqrt(cp1 * cp2) * np.sin(np.radians(dh / 2))
    lbar = (l1 + l2) / 2; cpbar = (cp1 + cp2) / 2; hue_sum = hp1 + hp2
    hbar = np.where(zero, hue_sum, np.where(np.abs(hp1 - hp2) <= 180, hue_sum / 2,
        np.where(hue_sum < 360, (hue_sum + 360) / 2, (hue_sum - 360) / 2)))
    t = 1 - .17 * np.cos(np.radians(hbar - 30)) + .24 * np.cos(np.radians(2 * hbar)) + .32 * np.cos(np.radians(3 * hbar + 6)) - .20 * np.cos(np.radians(4 * hbar - 63))
    theta = 30 * np.exp(-((hbar - 275) / 25) ** 2); rc = 2 * np.sqrt(cpbar ** 7 / (cpbar ** 7 + 25 ** 7 + 1e-12))
    sl = 1 + .015 * (lbar - 50) ** 2 / np.sqrt(20 + (lbar - 50) ** 2); sc = 1 + .045 * cpbar; sh = 1 + .015 * cpbar * t
    x = dl / sl; y = dc / sc; z = d_h / sh
    return np.sqrt(np.maximum(0, x * x + y * y + z * z - np.sin(np.radians(2 * theta)) * rc * y * z))


def _logo_pixels(path, np):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 30_000_000
    with Image.open(path) as source:
        if source.width * source.height > 30_000_000: raise ValueError('로고 이미지는 3,000만 픽셀 이하여야 합니다.')
        rgba = ImageOps.exif_transpose(source).convert('RGBA')
        original = rgba.size; rgba.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        return np.asarray(rgba.convert('RGB')), np.asarray(rgba.getchannel('A')), original, (rgba.width / original[0], rgba.height / original[1])


def _logo_foreground(rgb, alpha, cv2, np):
    height, width = rgb.shape[:2]
    if alpha is not None and np.any(alpha < 250): return alpha > 16
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    border = max(1, min(5, width // 20, height // 20))
    samples = np.concatenate((lab[:border].reshape(-1, 3), lab[-border:].reshape(-1, 3), lab[:, :border].reshape(-1, 3), lab[:, -border:].reshape(-1, 3)))
    background = np.median(samples, axis=0); distance = np.linalg.norm(lab - background, axis=2)
    border_distance = np.linalg.norm(samples - background, axis=1); median = np.median(border_distance); mad = np.median(np.abs(border_distance - median))
    mask = (distance > max(10.0, float(median + 4 * mad + 3))).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8); minimum = max(2, int(width * height * .00015)); kept = np.zeros_like(mask)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= minimum: kept[labels == index] = 1
    if float(kept.mean()) < .001 or float(kept.mean()) > .98: kept = np.ones_like(kept)
    return kept.astype(bool)


def _ocr_geometry(result, shape, cv2, np, scale=(1.0, 1.0)):
    height, width = shape; mask = np.zeros((height, width), np.uint8); words = (result or {}).get('ocr_words') or []
    for word in words:
        points = word.get('polygon')
        if points:
            polygon = np.asarray(points, dtype=np.float32).reshape(-1, 2)
            polygon[:, 0] *= scale[0]; polygon[:, 1] *= scale[1]
            polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1); polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
            cv2.fillPoly(mask, [np.round(polygon).astype(np.int32)], 255)
        elif word.get('bbox') and len(word['bbox']) == 4:
            x1, y1, x2, y2 = word['bbox']; x1 *= scale[0]; x2 *= scale[0]; y1 *= scale[1]; y2 *= scale[1]
            cv2.rectangle(mask, (max(0, int(x1)), max(0, int(y1))), (min(width - 1, int(x2)), min(height - 1, int(y2))), 255, -1)
    if mask.any():
        size = max(3, round(min(height, width) * .015)); size += 1 - size % 2
        mask = cv2.dilate(mask, np.ones((size, size), np.uint8))
    text = ''.join(_logo_text(word.get('text')) for word in words if str(word.get('text') or '').strip()) or _logo_text((result or {}).get('text', ''))
    scores = [float(word['score']) for word in words if isinstance(word.get('score'), (int, float)) and math.isfinite(word['score'])]
    return mask.astype(bool), text, (sum(scores) / len(scores) if scores else None), len(words)


def logo_color_evidence(query_path, reference_path):
    import cv2
    import numpy as np
    query, query_alpha, _, _ = _logo_pixels(query_path, np)
    reference, reference_alpha, _, _ = _logo_pixels(reference_path, np)
    query_mask = _logo_foreground(query, query_alpha, cv2, np)
    reference_mask = _logo_foreground(reference, reference_alpha, cv2, np)
    query_hsv = cv2.cvtColor(query, cv2.COLOR_RGB2HSV); reference_hsv = cv2.cvtColor(reference, cv2.COLOR_RGB2HSV)
    query_sat = float(query_hsv[..., 1][query_mask].mean()) if query_mask.any() else 0.0
    reference_sat = float(reference_hsv[..., 1][reference_mask].mean()) if reference_mask.any() else 0.0
    query_gray = cv2.cvtColor(query, cv2.COLOR_RGB2GRAY); reference_gray = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY)
    def contrast(gray, mask):
        foreground = gray[mask]; background = gray[~mask]
        if len(foreground) < 16 or len(background) < 16: return None
        return float(np.median(foreground) - np.median(background))
    query_contrast = contrast(query_gray, query_mask); reference_contrast = contrast(reference_gray, reference_mask)
    monochrome = query_sat < 24 and reference_sat > 38
    inverted = monochrome and query_contrast is not None and reference_contrast is not None and query_contrast * reference_contrast < 0
    return {'candidate_is_monochrome': monochrome, 'polarity_alignment': 'inverted' if inverted else 'normal' if query_contrast is not None and reference_contrast is not None else 'unknown',
        'candidate_mean_saturation': round(query_sat, 2), 'reference_mean_saturation': round(reference_sat, 2),
        'candidate_foreground_background_contrast': round(query_contrast, 2) if query_contrast is not None else None,
        'reference_foreground_background_contrast': round(reference_contrast, 2) if reference_contrast is not None else None,
        'alignment_available': False}


# (uncertain, mismatch) boundaries; `_min` keys are worse when lower. Initial values until scripts/calibrate_logo_diff.py is run.
LOGO_DIFF_BANDS = {
    'anisotropy_ratio': (.03, .05), 'shear_degrees': (2, 3), 'crop_coverage_min': (.985, .97), 'crop_outside_max': (.01, .02),
    'delta_e2000': (6, 10), 'color_diff_ratio': (.10, .20), 'text_pixel_diff': (.12, .18),
    'symbol_edge_iou_min': (.80, .72), 'symbol_pixel_diff': (.15, .22), 'added_component_area_ratio': (.001, .002),
}
LOGO_DIFF_COLOR_PIXEL_DELTA_E = 10
# Hard-reject cutoffs measured with this function's own metrics (RunPod, video flow: XFeat on the raw crop, same holds,
# PP-OCRv5). Of 227 originals only 30 reached logo_diff (13 with reliable alignment); each cutoff sits 50% beyond the
# worst of those originals. Other diff signals remain review evidence only.
LOGO_DIFF_SAFE_REJECT_THRESHOLDS = {
    'delta_e2000_min': 32.0,
    'added_area_ratio_min': .33,
    'added_area_ratio_aligned_min': .16,
    'shape_precision_max': .55,
    'shape_precision_aligned_max': .61,
    'anisotropy_aligned_min': .05,
    'shear_degrees_min': 6.8,
    'shear_degrees_aligned_min': 3.0,
}
LOGO_DIFF_SAFE_REJECT_POLICY = {'decision_policy': 'runpod_safe_reject_v1', 'safe_reject_policy': 'logo_diff_metrics_guarded_v2',
    'safe_reject_source_original_count': 227, 'safe_reject_measured_original_count': 30, 'auto_approval_enabled': False}


def _band(value, key, bands):
    if value is None: return None
    uncertain, mismatch = bands[key]
    if key.endswith('_min'): return 'mismatch' if value < mismatch else 'uncertain' if value < uncertain else 'pass'
    return 'mismatch' if value > mismatch else 'uncertain' if value > uncertain else 'pass'


def _worst(*states):
    states = [state for state in states if state]
    return 'mismatch' if 'mismatch' in states else 'uncertain' if 'uncertain' in states else 'pass' if states else None


def logo_diff_pair(query_path, reference_path, homography, *, candidate_roi=None, monochrome_policy='review', ocr_query=None, ocr_reference=None, preview_path=None, include_match_preview=False, thresholds=None):
    import cv2
    import numpy as np
    bands = {**LOGO_DIFF_BANDS, **(thresholds or {})}
    if set(bands) != set(LOGO_DIFF_BANDS) or any(len(pair) != 2 for pair in bands.values()): raise ValueError('logo_diff 임계값 구성을 확인해 주세요.')
    policy_metrics = {'thresholds_calibrated': False, 'monochrome_policy': monochrome_policy, **LOGO_DIFF_SAFE_REJECT_POLICY,
        'safe_reject_thresholds': LOGO_DIFF_SAFE_REJECT_THRESHOLDS, 'safe_reject_evidence': []}
    base = {'decision': 'uncertain', 'reason_codes': [], 'uncertain_reason_codes': [], 'regions': {}, 'metrics': policy_metrics, 'human_review_required': True}
    if monochrome_policy not in ('review', 'allow_monochrome'): raise ValueError('단색 로고 정책을 확인해 주세요.')
    if homography is None: return {**base, 'uncertain_reason_codes': ['ALIGNMENT_FAILED'], 'note': 'XFeat/RANSAC homography를 얻지 못해 비교를 보류했습니다.'}
    try: matrix = np.asarray(homography, dtype=np.float64).reshape(3, 3)
    except (TypeError, ValueError): return {**base, 'uncertain_reason_codes': ['ALIGNMENT_FAILED'], 'note': 'XFeat homography 형식이 올바르지 않습니다.'}
    if not np.isfinite(matrix).all() or abs(float(np.linalg.det(matrix))) < 1e-10:
        return {**base, 'uncertain_reason_codes': ['ALIGNMENT_FAILED'], 'note': 'XFeat homography가 유효하지 않아 비교를 보류했습니다.'}
    query, query_alpha, query_original_size, query_scale = _logo_pixels(query_path, np)
    reference, reference_alpha, reference_original_size, reference_scale = _logo_pixels(reference_path, np)
    matrix = np.diag([reference_scale[0], reference_scale[1], 1.0]) @ matrix @ np.linalg.inv(np.diag([query_scale[0], query_scale[1], 1.0]))
    qh, qw = query.shape[:2]; rh, rw = reference.shape[:2]
    if min(query_original_size) < 48 or min(reference_original_size) < 32: return {**base, 'uncertain_reason_codes': ['LOW_RESOLUTION'], 'note': '추출 로고 또는 기준 이미지의 짧은 변이 해상도 하한보다 작습니다.'}
    aligned = cv2.warpPerspective(query, matrix, (rw, rh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    valid = cv2.warpPerspective(np.full((qh, qw), 255, np.uint8), matrix, (rw, rh), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT) > 0
    reference_mask = _logo_foreground(reference, reference_alpha, cv2, np)
    query_mask = _logo_foreground(query, query_alpha, cv2, np)
    aligned_query_mask = cv2.warpPerspective(query_mask.astype(np.uint8), matrix, (rw, rh), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT) > 0
    candidate_roi_mask = np.ones((qh, qw), dtype=np.uint8)
    if candidate_roi is not None:
        try:
            roi_x1, roi_y1, roi_x2, roi_y2 = (float(value) for value in candidate_roi)
            roi_x1 = max(0, int(math.floor(roi_x1 * query_scale[0]))); roi_y1 = max(0, int(math.floor(roi_y1 * query_scale[1])))
            roi_x2 = min(qw, int(math.ceil(roi_x2 * query_scale[0]))); roi_y2 = min(qh, int(math.ceil(roi_y2 * query_scale[1])))
            if roi_x2 <= roi_x1 or roi_y2 <= roi_y1: raise ValueError('candidate ROI is empty')
            # Keep a small detector-boundary margin for genuine extra strokes while excluding most scene pixels.
            margin = max(2, int(round(min(roi_x2 - roi_x1, roi_y2 - roi_y1) * .05)))
            roi_x1 = max(0, roi_x1 - margin); roi_y1 = max(0, roi_y1 - margin)
            roi_x2 = min(qw, roi_x2 + margin); roi_y2 = min(qh, roi_y2 + margin)
            candidate_roi_mask.fill(0); candidate_roi_mask[roi_y1:roi_y2, roi_x1:roi_x2] = 1
        except (TypeError, ValueError, OverflowError):
            return {**base, 'uncertain_reason_codes': ['ALIGNMENT_UNCERTAIN'], 'note': '로고 검출 영역이 올바르지 않아 추가 요소 검사를 보류했습니다.'}
    aligned_candidate_roi = cv2.warpPerspective(candidate_roi_mask, matrix, (rw, rh), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT) > 0
    text_mask, reference_text, reference_ocr_score, reference_word_count = _ocr_geometry(ocr_reference, (rh, rw), cv2, np, reference_scale)
    _, query_text, query_ocr_score, query_word_count = _ocr_geometry(ocr_query, (qh, qw), cv2, np, query_scale)
    text_mask &= reference_mask; symbol_mask = reference_mask & ~text_mask
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY); aligned_gray = cv2.cvtColor(aligned, cv2.COLOR_RGB2GRAY)
    def correlation(a, b, mask):
        av = a[mask].astype(np.float32); bv = b[mask].astype(np.float32)
        if len(av) < 16 or float(av.std()) < 1e-4 or float(bv.std()) < 1e-4: return 0.0
        return float(np.corrcoef(av, bv)[0, 1])
    pre_scope = reference_mask & valid
    pre_reference_sat = float(cv2.cvtColor(reference, cv2.COLOR_RGB2HSV)[..., 1][pre_scope].mean()) if pre_scope.any() else 0.0
    pre_query_sat = float(cv2.cvtColor(aligned, cv2.COLOR_RGB2HSV)[..., 1][pre_scope].mean()) if pre_scope.any() else 0.0
    pre_corr = correlation(ref_gray, aligned_gray, pre_scope); pre_inverse_corr = correlation(ref_gray, 255 - aligned_gray, pre_scope)
    pre_monochrome = pre_query_sat < 24 and pre_reference_sat > 38
    pre_polarity = 'inverted' if pre_monochrome and pre_inverse_corr > pre_corr + .10 else 'normal'
    ecc_candidate_gray = 255 - aligned_gray if pre_polarity == 'inverted' else aligned_gray
    ecc_mask = (valid & cv2.dilate(reference_mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)).astype(np.uint8) * 255
    ecc = {'status': 'not_converged', 'correlation': None, 'translation_px': None, 'rotation_degrees': None, 'scale': None}
    try:
        warp = np.eye(2, 3, dtype=np.float32)
        ecc_correlation, warp = cv2.findTransformECC(ref_gray, ecc_candidate_gray, warp, cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-5), ecc_mask, 5)
        if not math.isfinite(float(ecc_correlation)) or not np.isfinite(warp).all():
            return {**base, 'uncertain_reason_codes': ['ALIGNMENT_UNCERTAIN'], 'metrics': {**base['metrics'], 'ecc': {'status': 'non_finite'}}, 'note': '제한 ECC 정렬이 유효한 수치를 반환하지 않아 판정을 보류했습니다.'}
        dx, dy = float(warp[0, 2]), float(warp[1, 2]); sx = math.hypot(float(warp[0, 0]), float(warp[1, 0])); sy = math.hypot(float(warp[0, 1]), float(warp[1, 1]))
        rotation = math.degrees(math.atan2(float(warp[1, 0]), float(warp[0, 0])))
        ecc.update(correlation=round(float(ecc_correlation), 5), translation_px=[round(dx, 2), round(dy, 2)], rotation_degrees=round(rotation, 3), scale=[round(sx, 5), round(sy, 5)])
        if abs(dx) > max(4, rw * .015) or abs(dy) > max(4, rh * .015) or abs(sx - 1) > .02 or abs(sy - 1) > .02 or abs(rotation) > 1.5 or ecc_correlation < .50:
            return {**base, 'uncertain_reason_codes': ['ALIGNMENT_UNCERTAIN'], 'metrics': {**base['metrics'], 'ecc': ecc}, 'note': '제한 ECC 보정이 허용 범위를 벗어나 정렬 근거가 불충분합니다.'}
        aligned = cv2.warpAffine(aligned, warp, (rw, rh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
        valid = cv2.warpAffine(valid.astype(np.uint8), warp, (rw, rh), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT) > 0
        aligned_query_mask = cv2.warpAffine(aligned_query_mask.astype(np.uint8), warp, (rw, rh), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT) > 0
        aligned_candidate_roi = cv2.warpAffine(aligned_candidate_roi.astype(np.uint8), warp, (rw, rh), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT) > 0
        ecc['status'] = 'refined'
    except cv2.error:
        pass
    aligned_gray = cv2.cvtColor(aligned, cv2.COLOR_RGB2GRAY)
    foreground_points = cv2.findNonZero(reference_mask.astype(np.uint8)); crop_outside = None
    crop_coverage = float((reference_mask & valid).sum() / max(1, reference_mask.sum()))
    if foreground_points is not None:
        points = foreground_points.reshape(-1, 2)[::max(1, len(foreground_points) // 20000)].astype(np.float32).reshape(-1, 1, 2)
        try:
            projected = cv2.perspectiveTransform(points, np.linalg.inv(matrix)).reshape(-1, 2)
            crop_outside = float(np.mean((projected[:, 0] < 0) | (projected[:, 0] >= qw) | (projected[:, 1] < 0) | (projected[:, 1] >= qh)))
        except (cv2.error, np.linalg.LinAlgError): crop_outside = 1.0
    # Local Jacobian separates anisotropic scaling/shear from ordinary uniform resizing and rotation.
    x, y = qw / 2, qh / 2; den = matrix[2, 0] * x + matrix[2, 1] * y + matrix[2, 2]
    if abs(den) < 1e-10: return {**base, 'uncertain_reason_codes': ['ALIGNMENT_FAILED'], 'note': 'homography 중심 투영이 유효하지 않습니다.'}
    u = matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2]; v = matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2]
    jacobian = np.array([[(matrix[0, 0] * den - u * matrix[2, 0]) / den**2, (matrix[0, 1] * den - u * matrix[2, 1]) / den**2],
                         [(matrix[1, 0] * den - v * matrix[2, 0]) / den**2, (matrix[1, 1] * den - v * matrix[2, 1]) / den**2]])
    col_x, col_y = jacobian[:, 0], jacobian[:, 1]; scale_x, scale_y = float(np.linalg.norm(col_x)), float(np.linalg.norm(col_y))
    axis_angle = math.degrees(math.acos(float(np.clip(np.dot(col_x, col_y) / max(1e-12, scale_x * scale_y), -1, 1))))
    anisotropy = max(scale_x, scale_y) / max(1e-12, min(scale_x, scale_y)) - 1
    ratio_state = _worst(_band(anisotropy, 'anisotropy_ratio', bands), _band(abs(90 - axis_angle), 'shear_degrees', bands))
    crop_state = _worst(_band(crop_coverage, 'crop_coverage_min', bands), _band(crop_outside, 'crop_outside_max', bands))
    hsv_reference = cv2.cvtColor(reference, cv2.COLOR_RGB2HSV); hsv_query = cv2.cvtColor(aligned, cv2.COLOR_RGB2HSV)
    color_scope = reference_mask & valid
    reference_sat = float(hsv_reference[..., 1][color_scope].mean()) if color_scope.any() else 0.0
    query_sat = float(hsv_query[..., 1][color_scope].mean()) if color_scope.any() else 0.0
    def foreground_background_contrast(gray):
        background_scope = valid & ~reference_mask
        foreground = gray[color_scope]; background = gray[background_scope]
        if len(foreground) < 16 or len(background) < 16: return None
        return float(np.median(foreground) - np.median(background))
    reference_contrast = foreground_background_contrast(ref_gray); query_contrast = foreground_background_contrast(aligned_gray)
    corr = correlation(ref_gray, aligned_gray, color_scope); inverse_corr = correlation(ref_gray, 255 - aligned_gray, color_scope)
    monochrome = query_sat < 24 and reference_sat > 38
    polarity = 'inverted' if monochrome and inverse_corr > corr + .10 else 'normal'
    def refine_region(mask):
        selected = mask & valid
        if not mask.any(): return {'status': 'no_region', 'correlation': None, 'translation_px': None, 'scale': None, 'rotation_degrees': None, 'shear_degrees': None}, aligned, valid
        ys, xs = np.where(selected)
        if len(xs) < 32: return {'status': 'insufficient_support', 'correlation': None, 'translation_px': None, 'scale': None, 'rotation_degrees': None, 'shear_degrees': None}, aligned, valid
        x1, x2 = int(xs.min()), int(xs.max()); y1, y2 = int(ys.min()), int(ys.max())
        region_width, region_height = x2 - x1 + 1, y2 - y1 + 1
        if min(region_width, region_height) < 6: return {'status': 'insufficient_support', 'correlation': None, 'translation_px': None, 'scale': None, 'rotation_degrees': None, 'shear_degrees': None}, aligned, valid
        warp = np.eye(2, 3, dtype=np.float32)
        alignment_gray = 255 - aligned_gray if polarity == 'inverted' else aligned_gray
        alignment_mask = cv2.dilate(selected.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & valid
        try:
            score, warp = cv2.findTransformECC(ref_gray, alignment_gray, warp, cv2.MOTION_AFFINE,
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 1e-5), alignment_mask.astype(np.uint8) * 255, 3)
        except cv2.error:
            return {'status': 'not_converged', 'correlation': None, 'translation_px': None, 'scale': None, 'rotation_degrees': None, 'shear_degrees': None}, aligned, valid
        if not math.isfinite(float(score)) or not np.isfinite(warp).all():
            return {'status': 'non_finite', 'correlation': None, 'translation_px': None, 'scale': None, 'rotation_degrees': None, 'shear_degrees': None}, aligned, valid
        sx = math.hypot(float(warp[0, 0]), float(warp[1, 0])); sy = math.hypot(float(warp[0, 1]), float(warp[1, 1]))
        rotation = math.degrees(math.atan2(float(warp[1, 0]), float(warp[0, 0])))
        angle = math.degrees(math.acos(float(np.clip(np.dot(warp[:, 0], warp[:, 1]) / max(1e-12, sx * sy), -1, 1))))
        shear = abs(90 - angle)
        center = np.array([(x1 + x2) / 2, (y1 + y2) / 2], dtype=np.float64)
        displacement = warp[:, :2].astype(np.float64) @ center + warp[:, 2].astype(np.float64) - center
        shift = [round(float(value), 2) for value in displacement]
        alignment = {'status': 'refined', 'correlation': round(float(score), 5), 'translation_px': shift,
            'scale': [round(sx, 5), round(sy, 5)], 'rotation_degrees': round(rotation, 3), 'shear_degrees': round(shear, 3)}
        allowed_shift = max(2.0, min(region_width, region_height) * .05)
        if (score < .50 or float(np.linalg.norm(displacement)) > allowed_shift or abs(sx - 1) > .02 or abs(sy - 1) > .02 or abs(rotation) > 1.5 or shear > 2):
            alignment['status'] = 'outside_tolerance'
            return alignment, aligned, valid
        region_aligned = cv2.warpAffine(aligned, warp, (rw, rh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
        region_valid = cv2.warpAffine(valid.astype(np.uint8), warp, (rw, rh), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT) > 0
        return alignment, region_aligned, region_valid
    text_alignment, text_aligned, text_valid = refine_region(text_mask)
    symbol_alignment, symbol_aligned, symbol_valid = refine_region(symbol_mask)
    def metrics_for(mask, candidate, candidate_valid):
        selected = mask & candidate_valid
        if int(selected.sum()) < 8: return {'pixel_diff_ratio': None, 'edge_iou': None, 'delta_e2000': None, 'color_diff_ratio': None, 'pixels': int(selected.sum())}
        candidate_gray = cv2.cvtColor(candidate, cv2.COLOR_RGB2GRAY)
        shape_gray = 255 - candidate_gray if polarity == 'inverted' else candidate_gray
        gray_diff = cv2.absdiff(ref_gray, shape_gray)
        edge_reference = cv2.Canny(ref_gray, 70, 150) > 0; edge_query = cv2.Canny(shape_gray, 70, 150) > 0
        pixel_diff = float((gray_diff[selected] > 36).mean())
        union = int((edge_reference[selected] | edge_query[selected]).sum()); intersection = int((edge_reference[selected] & edge_query[selected]).sum())
        edge_iou = intersection / union if union else 1.0
        indices = np.flatnonzero(selected); indices = indices[::max(1, len(indices) // 20000)]
        reference_lab = cv2.cvtColor(reference, cv2.COLOR_RGB2LAB).reshape(-1, 3)
        candidate_lab = cv2.cvtColor(candidate, cv2.COLOR_RGB2LAB).reshape(-1, 3)
        delta = _delta_e_2000(reference_lab[indices], candidate_lab[indices], np).reshape(-1)
        # The changed-pixel share keeps a small recolored symbol from being averaged away by the mean ΔE.
        return {'pixel_diff_ratio': round(pixel_diff, 5), 'edge_iou': round(float(edge_iou), 5), 'delta_e2000': round(float(delta.mean()), 3),
                'color_diff_ratio': round(float((delta > LOGO_DIFF_COLOR_PIXEL_DELTA_E).mean()), 5), 'pixels': int(selected.sum())}
    text_metrics = metrics_for(text_mask, text_aligned, text_valid) if text_mask.any() else None
    symbol_metrics = metrics_for(symbol_mask, symbol_aligned, symbol_valid) if symbol_mask.any() else None
    whole_metrics = metrics_for(reference_mask, aligned, valid)
    added_mask = aligned_query_mask & ~reference_mask & valid & aligned_candidate_roi
    added_mask = cv2.morphologyEx(added_mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(added_mask, 8)
    foreground_area = max(1, int(reference_mask.sum()))
    minimum_area = max(12, int(foreground_area * bands['added_component_area_ratio'][0]))
    added_components = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, component_count) if stats[i, cv2.CC_STAT_AREA] >= minimum_area]
    added_area_ratio = float(added_mask.sum() / foreground_area)
    candidate_foreground = aligned_query_mask & valid & aligned_candidate_roi
    reference_foreground = reference_mask & valid
    true_positive = int((candidate_foreground & reference_foreground).sum())
    shape_precision = true_positive / max(1, int(candidate_foreground.sum()))
    shape_recall = true_positive / max(1, int(reference_foreground.sum()))
    shape_f1 = 2 * shape_precision * shape_recall / max(1e-12, shape_precision + shape_recall)
    margin_state = _worst(*(_band(area / foreground_area, 'added_component_area_ratio', bands) for area in added_components)) or 'pass'
    def color_state(metrics):
        return _worst(_band(metrics['delta_e2000'], 'delta_e2000', bands), _band(metrics['color_diff_ratio'], 'color_diff_ratio', bands)) if metrics else None
    text_color_state = color_state(text_metrics); symbol_color_state = color_state(symbol_metrics)
    text_geometry_bad = text_alignment['status'] == 'outside_tolerance'
    symbol_geometry_bad = symbol_alignment['status'] == 'outside_tolerance'
    reference_hangul = _logo_hangul_text(ocr_reference); query_hangul = _logo_hangul_text(ocr_query)
    hangul_available = bool(reference_hangul and query_hangul)
    hangul_same_length = hangul_available and len(reference_hangul) == len(query_hangul)
    hangul_different = hangul_available and reference_hangul != query_hangul
    ocr_same_length_mismatch = hangul_different and hangul_same_length
    edit_distance = _edit_distance(reference_hangul, query_hangul) if hangul_available else None
    text_pixel_diff = text_metrics.get('pixel_diff_ratio') if text_metrics else None
    text_pixel_state = _band(text_pixel_diff, 'text_pixel_diff', bands) if reference_word_count else None
    if ocr_same_length_mismatch:
        text_state = 'mismatch'
    elif hangul_different:
        # OCR often reads only part of a genuine video logo. A length change is
        # therefore review evidence, never a hard rejection.
        text_state = 'uncertain'
    elif hangul_available:
        text_state = text_pixel_state
    elif reference_word_count or query_word_count:
        text_state = 'uncertain'
    else:
        text_state = text_pixel_state
    # A local ECC fit outside its tolerance is weak alignment evidence, not by itself proof
    # that the source logo is wrong. Keep it in the review band.
    if text_geometry_bad: text_state = 'uncertain'
    symbol_shape_state = _worst(_band(symbol_metrics['edge_iou'], 'symbol_edge_iou_min', bands), _band(symbol_metrics['pixel_diff_ratio'], 'symbol_pixel_diff', bands)) if symbol_metrics else None
    if symbol_geometry_bad: symbol_shape_state = 'uncertain'
    raw_checks = {'RATIO': ratio_state, 'CROP': crop_state, 'TEXT': text_state, 'COLOR': _worst(text_color_state, symbol_color_state, 'mismatch' if monochrome else None),
                  'ADDED': _worst(symbol_shape_state, margin_state)}
    candidate_mismatch_reasons = [code for code, state in raw_checks.items() if state == 'mismatch']
    borderline = [code for code, state in raw_checks.items() if state == 'uncertain']
    monochrome_color_change = monochrome
    monochrome_override = monochrome_color_change and monochrome_policy == 'allow_monochrome'
    monochrome_review = monochrome_color_change and monochrome_policy == 'review'
    if monochrome_override: borderline = [code for code in borderline if code != 'COLOR']
    ocr_uncertain = not hangul_available or not hangul_same_length
    region_alignment_uncertain = (text_mask.any() and text_alignment['status'] != 'refined') or (symbol_mask.any() and symbol_alignment['status'] != 'refined')

    review_only_color = monochrome_review or monochrome_override
    anisotropy_state = _band(anisotropy, 'anisotropy_ratio', bands)
    shear_state = _band(abs(90 - axis_angle), 'shear_degrees', bands)
    crop_coverage_state = _band(crop_coverage, 'crop_coverage_min', bands)
    crop_outside_state = _band(crop_outside, 'crop_outside_max', bands)
    alignment_reliable = ecc['status'] == 'refined' and not region_alignment_uncertain
    safe_thresholds = LOGO_DIFF_SAFE_REJECT_THRESHOLDS
    safe_reject_evidence = []
    def record_safe_reject(code, rule, value=None, threshold=None):
        evidence = {'code': code, 'rule': rule}
        if value is not None: evidence['value'] = round(float(value), 6)
        if threshold is not None: evidence['threshold'] = threshold
        safe_reject_evidence.append(evidence)

    # OCR is the only text hard-reject rule from the experiment: compare Hangul
    # only, require both reads to be non-empty and equally long, and ignore score.
    if ocr_same_length_mismatch:
        record_safe_reject('TEXT', 'hangul_diff_same_length', threshold='equal character count')

    color_delta = whole_metrics.get('delta_e2000') if whole_metrics else None
    if not review_only_color and color_delta is not None and color_delta > safe_thresholds['delta_e2000_min']:
        record_safe_reject('COLOR', 'mean_delta_e2000_gt', color_delta, safe_thresholds['delta_e2000_min'])

    # Foreground masks change under grayscale/polarity changes. Under the default
    # monochrome review policy, do not let those masks create SHAPE/ADDED rejects.
    if not monochrome_review:
        if added_area_ratio > safe_thresholds['added_area_ratio_min']:
            record_safe_reject('ADDED', 'added_area_ratio_gt', added_area_ratio, safe_thresholds['added_area_ratio_min'])
        elif alignment_reliable and added_area_ratio > safe_thresholds['added_area_ratio_aligned_min']:
            record_safe_reject('ADDED', 'added_area_ratio_aligned_gt', added_area_ratio, safe_thresholds['added_area_ratio_aligned_min'])
        if shape_precision < safe_thresholds['shape_precision_max']:
            record_safe_reject('SHAPE', 'shape_precision_lt', shape_precision, safe_thresholds['shape_precision_max'])
        elif alignment_reliable and shape_precision < safe_thresholds['shape_precision_aligned_max']:
            record_safe_reject('SHAPE', 'shape_precision_aligned_lt', shape_precision, safe_thresholds['shape_precision_aligned_max'])

    shear_degrees = abs(90 - axis_angle)
    if shear_degrees > safe_thresholds['shear_degrees_min']:
        record_safe_reject('RATIO', 'shear_degrees_gt', shear_degrees, safe_thresholds['shear_degrees_min'])
    elif alignment_reliable and shear_degrees > safe_thresholds['shear_degrees_aligned_min']:
        record_safe_reject('RATIO', 'shear_degrees_aligned_gt', shear_degrees, safe_thresholds['shear_degrees_aligned_min'])
    if alignment_reliable and anisotropy > safe_thresholds['anisotropy_aligned_min']:
        record_safe_reject('RATIO', 'anisotropy_aligned_gt', anisotropy, safe_thresholds['anisotropy_aligned_min'])

    reasons = list(dict.fromkeys(item['code'] for item in safe_reject_evidence))
    unconfirmed_mismatches = [code for code in candidate_mismatch_reasons if code not in reasons and not (code == 'COLOR' and review_only_color)]
    if hangul_different and not hangul_same_length: borderline.append('TEXT')
    if ocr_uncertain: borderline.append('TEXT')
    if monochrome_review and 'COLOR' not in reasons: borderline.append('COLOR')
    if 'ADDED' not in reasons and added_area_ratio > safe_thresholds['added_area_ratio_aligned_min']: borderline.append('ADDED')
    if 'SHAPE' not in reasons and shape_precision < safe_thresholds['shape_precision_aligned_max']: borderline.append('SHAPE')
    if monochrome_review and (added_area_ratio > safe_thresholds['added_area_ratio_aligned_min'] or shape_precision < safe_thresholds['shape_precision_aligned_max']):
        borderline.extend(['ADDED', 'SHAPE'])
    borderline = list(dict.fromkeys(borderline + unconfirmed_mismatches))
    # logo_diff is a reject-only gate: a candidate that misses every hard cutoff
    # is still review-needed, never auto-approved as the original.
    decision = 'mismatch' if reasons else 'uncertain'
    def region_decision(states, confirmed_codes=(), missing=False):
        if any(code in confirmed_codes for code in states): return 'mismatch'
        near = [state for state in states.values() if state in ('mismatch', 'uncertain')]
        return 'uncertain' if missing or near else 'match'
    def region_confirmed(states, confirmed_codes):
        return [code for code in states if code in confirmed_codes]
    def region_uncertain(states, confirmed_codes):
        return [code for code, state in states.items() if code not in confirmed_codes and state in ('mismatch', 'uncertain')]
    shape_review_state = 'mismatch' if 'SHAPE' in reasons else 'uncertain' if shape_precision < safe_thresholds['shape_precision_aligned_max'] else 'pass'
    whole_states = {'RATIO': ratio_state, 'CROP': crop_state, 'SHAPE': shape_review_state}
    text_states = {'TEXT': text_state}
    symbol_states = {'SHAPE': symbol_shape_state, 'ADDED': margin_state}
    color_state_all = _band(color_delta, 'delta_e2000', bands) if color_delta is not None else None
    whole_confirmed = [code for code in ('RATIO', 'SHAPE') if code in reasons]
    text_confirmed = ['TEXT'] if 'TEXT' in reasons else []
    symbol_confirmed = ['ADDED'] if 'ADDED' in reasons else []
    regions = {
        'whole': {'decision': region_decision(whole_states, whole_confirmed, missing=ecc['status'] != 'refined'), 'reason_codes': region_confirmed(whole_states, whole_confirmed), 'uncertain_reason_codes': region_uncertain(whole_states, whole_confirmed),
                  'ratio_scale_x': round(scale_x, 6), 'ratio_scale_y': round(scale_y, 6), 'anisotropy_percent': round(anisotropy * 100, 3), 'shear_degrees': round(shear_degrees, 3), 'crop_coverage': round(crop_coverage, 5), 'crop_outside_fraction': round(crop_outside, 5) if crop_outside is not None else None,
                  'shape_precision': round(shape_precision, 6), 'added_area_ratio': round(added_area_ratio, 6)},
        'text': {'decision': region_decision(text_states, text_confirmed, missing=ocr_uncertain or (text_mask.any() and text_alignment['status'] not in ('refined', 'outside_tolerance'))),
                 'reason_codes': region_confirmed(text_states, text_confirmed), 'uncertain_reason_codes': region_uncertain(text_states, text_confirmed), 'alignment': text_alignment, 'reference_text': reference_text, 'candidate_text': query_text,
                 'hangul_reference_text': reference_hangul, 'hangul_candidate_text': query_hangul, 'hangul_character_count': len(reference_hangul), 'edit_distance': edit_distance,
                 'ocr_confidence': {'reference': reference_ocr_score, 'candidate': query_ocr_score}, **(text_metrics or {'status': 'no_text_region'})},
        'symbol': {'decision': region_decision(symbol_states, symbol_confirmed, missing=symbol_mask.any() and symbol_alignment['status'] not in ('refined', 'outside_tolerance')) if symbol_metrics is not None else 'match',
                   'reason_codes': region_confirmed(symbol_states, symbol_confirmed), 'uncertain_reason_codes': region_uncertain(symbol_states, symbol_confirmed), 'alignment': symbol_alignment,
                   'shape_precision': round(shape_precision, 6), 'shape_recall': round(shape_recall, 6), **(symbol_metrics or {'status': 'no_symbol_region'})},
        'color': {'decision': 'mismatch' if 'COLOR' in reasons else 'uncertain' if monochrome_review or (not monochrome_override and color_state_all in ('mismatch', 'uncertain')) else 'match',
                  'reason_codes': ['COLOR'] if 'COLOR' in reasons else [], 'uncertain_reason_codes': ['COLOR'] if 'COLOR' not in reasons and (monochrome_review or (not monochrome_override and color_state_all in ('mismatch', 'uncertain'))) else [],
                  'mean_delta_e2000': round(float(color_delta), 3) if color_delta is not None else None, 'threshold': safe_thresholds['delta_e2000_min'],
                  'candidate_is_monochrome': monochrome, 'polarity_alignment': polarity},
        'margin': {'decision': region_decision({'ADDED': margin_state}, symbol_confirmed), 'reason_codes': region_confirmed({'ADDED': margin_state}, symbol_confirmed), 'uncertain_reason_codes': region_uncertain({'ADDED': margin_state}, symbol_confirmed),
                   'added_component_areas': added_components, 'added_component_area_ratios': [round(area / foreground_area, 5) for area in added_components],
                   'added_area_ratio': round(added_area_ratio, 6), 'minimum_component_area': minimum_area},
    }
    checks = {code: 'mismatch' if code in reasons else 'uncertain' if state in ('mismatch', 'uncertain') else state
              for code, state in raw_checks.items()}
    if review_only_color: checks['COLOR'] = 'uncertain' if monochrome_review else 'pass'
    checks['SHAPE'] = 'mismatch' if 'SHAPE' in reasons else 'uncertain' if symbol_shape_state in ('mismatch', 'uncertain') or shape_precision < safe_thresholds['shape_precision_aligned_max'] else 'pass'
    saved_preview = bool(preview_path and (decision != 'match' or monochrome_review or monochrome_override or include_match_preview))
    region_overlay_preview = None
    if saved_preview:
        from PIL import Image
        overlay = cv2.addWeighted(reference, .5, aligned, .5, 0); overlay[~valid] = reference[~valid]
        Image.fromarray(overlay).save(preview_path, format='PNG')
        region_overlay = reference.copy()
        if text_mask.any(): region_overlay[text_mask] = (region_overlay[text_mask].astype(np.float32) * .42 + np.array([30, 110, 255], np.float32) * .58).astype(np.uint8)
        if symbol_mask.any(): region_overlay[symbol_mask] = (region_overlay[symbol_mask].astype(np.float32) * .42 + np.array([25, 190, 100], np.float32) * .58).astype(np.uint8)
        region_path = Path(preview_path).with_name(Path(preview_path).stem + '-regions.png')
        Image.fromarray(region_overlay).save(region_path, format='PNG')
        region_overlay_preview = region_path.name
    return {'decision': decision, 'reason_codes': reasons, 'uncertain_reason_codes': borderline,
        'candidate_mismatch_reason_codes': candidate_mismatch_reasons, 'checks': checks, 'regions': regions,
        'metrics': {'ecc': ecc, 'thresholds_calibrated': False, 'monochrome_policy': monochrome_policy, **LOGO_DIFF_SAFE_REJECT_POLICY,
                    'safe_reject_thresholds': safe_thresholds, 'safe_reject_evidence': safe_reject_evidence,
                    'alignment_reliable': alignment_reliable,
                    'confirmed_mismatch_reasons': reasons, 'unconfirmed_mismatch_reasons': unconfirmed_mismatches,
                    'monochrome_color_override_applied': monochrome_override,
                    'thresholds': {**{key: {'uncertain': pair[0], 'mismatch': pair[1]} for key, pair in bands.items()},
                        'region_translation_ratio': .05, 'region_translation_min_px': 2, 'region_scale_deviation': .02, 'region_rotation_degrees': 1.5, 'region_shear_degrees': 2,
                        'color_pixel_delta_e2000': LOGO_DIFF_COLOR_PIXEL_DELTA_E,
                        'safe_reject': safe_thresholds},
                    'color_diff_ratio': whole_metrics['color_diff_ratio'],
                    'mean_delta_e2000': whole_metrics['delta_e2000'], 'pixel_diff_ratio': whole_metrics['pixel_diff_ratio'], 'edge_iou': whole_metrics['edge_iou'],
                    'shape_precision': round(shape_precision, 6), 'shape_recall': round(shape_recall, 6), 'shape_f1': round(shape_f1, 6),
                    'added_area_ratio': round(added_area_ratio, 6), 'inlier_foreground_coverage': round(crop_coverage, 5),
                    'added_component_count': len(added_components), 'ocr': {
                        'reference_hangul': reference_hangul, 'candidate_hangul': query_hangul,
                        'reference_hangul_length': len(reference_hangul), 'candidate_hangul_length': len(query_hangul),
                        'same_length_difference': ocr_same_length_mismatch, 'confidence_used_for_rejection': False},
                    'color': {
                        'reference_mean_saturation': round(reference_sat, 2), 'candidate_mean_saturation': round(query_sat, 2),
                        'reference_foreground_background_contrast': round(reference_contrast, 2) if reference_contrast is not None else None,
                        'candidate_foreground_background_contrast': round(query_contrast, 2) if query_contrast is not None else None,
                        'grayscale_correlation': round(corr, 5), 'inverse_grayscale_correlation': round(inverse_corr, 5), 'polarity_alignment': polarity,
                        'candidate_is_monochrome': monochrome, 'human_review_required': monochrome_review}},
        'human_review_required': decision == 'uncertain', 'overlay_preview': Path(preview_path).name if saved_preview else None,
        'region_overlay_preview': region_overlay_preview,
        'note': ('이 함수의 지표로 측정한 원본 최악값보다 50% 바깥의 기준만 자동 오답으로 제외합니다. 나머지는 정답 승인도 자동으로 하지 않고 사람 검토에 남깁니다. ' +
                 ('단색화·극성 반전은 기본 정책에 따라 검토 대상으로 남기고, 마스크 기반 형태·추가 요소 컷도 적용하지 않습니다. ' if monochrome_review else
                  '단색 허용 정책은 색상 차이만 제외하며 다른 안전 기준은 적용합니다. ' if monochrome_override else '') +
                 '기준을 정한 원본은 logo_diff까지 도달한 30개뿐이고, 처음 보는 영상으로는 아직 검증하지 않았습니다.')}
