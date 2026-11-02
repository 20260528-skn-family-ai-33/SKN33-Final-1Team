"""2부: 사용자가 확정한 로고 위치(묶음)를 원본 로고와 정밀 대조한다. 판정은 내지 않고 근거를 모은다.

실험(2026-10-31~11-01)에서 정한 규칙을 그대로 옮겼다. 묶음에 속한 모든 초를 각각 비교한다.
  ① 배경 분리 M3c: 테두리 색과의 거리 + Otsu(색 반전과 무관) 마스크를 BiRefNet이 잡은 범위 안으로 제한
     긴 가는 선(테두리·드래그로 걸친 구분선)과 아주 작은 점은 지우고 작은 글자는 남긴다
  ② 원본 로고를 요소로 나눈다: 원본 문구(사람이 쓴 기준 문구, | 로 줄 구분)와 맞는 OCR 줄이 글자 요소
     (가장 큰 줄 = 주 문구, 나머지 = 보조 문구), 나머지 전경은 하나의 심볼. 글자가 곧 그림인 로고는 '로고 전체'
  ③ 영상 쪽: 주 문구(없으면 심볼·전경)를 기준점으로 원본 요소 위치를 옮겨 놓고 요소마다 판정
     글자: 어디서든 읽히면 있음 / 예상 높이 8px 미만이면 비교 불가 / 그 자리에 전경은 있는데 못 읽으면 비교 불가 / 없으면 빠짐
     심볼: 자리에 전경이 30% 미만이면 다른 곳에서 비슷한 모양을 찾아 '위치 다름', 없으면 '빠짐'
           높이 20px 미만이면 비교 불가, 아니면 모양 F1 0.6 미만이면 '모양 다름'
     추가: 원본 문구에 없는 OCR 줄, 어느 요소 자리와도 떨어진 전경 덩어리
     기준 문구가 없으면: 원본을 영상 전경 높이로 줄여 같은 크기에서 읽은 OCR 줄과 비교해 빠짐·추가를 낸다(same_size_text)
     크기 비율: 심볼이 로고의 15% 이상·높이 20px 이상일 때 심볼:주 문구 높이 비가 1.3배 넘게 다르면 '크기 비율 다름'
  ④ 초마다 match / diff(이름 붙은 근거) / unknown(비교 불가가 있음)
  ⑤ 색: 유채색 화소끼리만 비교(흰색·검은색 단색은 통과). 위 판정에는 섞지 않고 근거로만 함께 낸다.
묶음은 판정 없이 초별 결과, 근거와 나온 초 수, 대표 프레임(가장 크고 선명한 초), 결과가 바뀌는 초를 낸다.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from PIL import Image

from . import video_logo_models as M

CAND_PAD = 0.08        # 후보 상자는 탐지기 상자라 빠듯할 수 있어 상하좌우 8% 넓힌다(직접 지정 상자는 그대로)
SMALL_TEXT_PX = 8
SMALL_SYMBOL_PX = 20
SYMBOL_F1 = 0.6
RATIO_LIMIT = 1.3


# ---------------------------------------------------------------------------
# 배경 분리
# ---------------------------------------------------------------------------
def clean(m: np.ndarray) -> np.ndarray:
    """긴 가는 선(테두리·드래그로 걸친 구분선)과 아주 작은 점을 지운다. 작은 글자는 남긴다."""
    import cv2
    m = m.astype(np.uint8); n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8); h, w = m.shape; keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, bw, bh, ar = st[i]
        line = min(bw, bh) <= max(2, .03 * min(w, h)) and (bw > .6 * w or bh > .6 * h)
        keep[i] = ar >= max(3, .00005 * w * h) and not line
    return keep[lab]


def fg_mask(rgb: Image.Image) -> np.ndarray:
    """테두리 색과의 거리 + Otsu. 색 반전·배경색과 무관하게 전경을 고른다(M0)."""
    import cv2
    a = np.asarray(rgb.convert("RGB")).astype(np.float32)
    b = np.concatenate([a[:2].reshape(-1, 3), a[-2:].reshape(-1, 3), a[:, :2].reshape(-1, 3), a[:, -2:].reshape(-1, 3)])
    d = np.abs(a - np.median(b, axis=0)).max(axis=2)
    t, _ = cv2.threshold(np.clip(d, 0, 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return clean(d > max(t, 25))


def birefnet_mask(img: Image.Image) -> np.ndarray:
    import cv2
    import torch
    tf, model, dtype = M.birefnet()
    x = tf(img.convert("RGB")).unsqueeze(0).to(model.device, dtype)
    with torch.no_grad():
        p = model(x)[-1].sigmoid().float().cpu()[0, 0].numpy()
    return clean(cv2.resize(p, img.size, interpolation=cv2.INTER_LINEAR) > .5)


def separate(img: Image.Image) -> np.ndarray:
    """M3c: M0 전경을 BiRefNet 범위(5px 넓힘) 안으로 제한한다."""
    import cv2
    region = cv2.dilate(birefnet_mask(img).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return clean(fg_mask(img) & region)


def bbox(m: np.ndarray):
    ys, xs = np.nonzero(m)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else (0, 0, m.shape[1], m.shape[0])


def trim_ref(rf: Image.Image) -> Image.Image:
    """원본 로고를 전경 둘레(긴 변의 4%, 최소 4px)만 남기고 자른다."""
    b = bbox(fg_mask(rf)); m = max(4, round(.04 * max(b[2] - b[0], b[3] - b[1])))
    return rf.crop((max(0, b[0] - m), max(0, b[1] - m), min(rf.width, b[2] + m), min(rf.height, b[3] + m)))


# ---------------------------------------------------------------------------
# 문자
# ---------------------------------------------------------------------------
def norm(t: str) -> str:
    return "".join(c for c in t.upper() if c.isalnum() or "가" <= c <= "힣")


def sub_dist(tok: str, ref: str) -> int:
    """tok과 ref의 어느 부분 문자열 사이의 최소 편집 거리."""
    prev = [0] * (len(ref) + 1)
    for i, c in enumerate(tok, 1):
        cur = [i] + [0] * len(ref)
        for j, r in enumerate(ref, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (c != r))
        prev = cur
    return min(prev)


def match_line(t: str, lines: list[str]):
    """t가 속한 원본 줄 번호(부분 편집 거리가 짧은 쪽 길이의 절반 이하), 없으면 None."""
    best = None
    for i, l in enumerate(lines):
        d = sub_dist(t, l) if len(t) <= len(l) else sub_dist(l, t); k = min(len(t), len(l))
        if d <= k / 2 and (best is None or d / max(1, k) < best[1]):
            best = (i, d / max(1, k))
    return None if best is None else best[0]


def ocr_boxes(img: Image.Image) -> list[dict]:
    """OCR 줄과 상자(img 좌표). 짧은 변 192px 이상·긴 변 1600px 이하로 키워 읽는다."""
    f = max(1.0, 192 / min(img.size)); f = min(f, 1600 / max(img.size)) if max(img.size) * f > 1600 else f
    im = img.resize((round(img.width * f), round(img.height * f)), Image.LANCZOS) if f != 1 else img
    out = []
    for t, p, s in M.ocr_predict(np.asarray(im.convert("RGB"))):
        p = p / f; n = norm(t)
        if len(n) >= 2 and s >= .5:
            out.append({"t": n, "raw": t, "box": [p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()]})
    return out


def ref_lines(ref_text: str) -> list[str]:
    return [norm(l) for l in ref_text.split("|") if norm(l)]


def _near(a: str, pool: list[str]) -> bool:
    return any(sub_dist(a, b) <= len(a) / 2 or sub_dist(b, a) <= len(b) / 2 for b in pool)


def same_size_text(q_lines: list[str], ref: dict, mq: np.ndarray) -> list[tuple[str, str]]:
    """기준 문구가 없을 때의 문자 비교(실험 diff_real과 같은 방식): 원본을 영상 전경 높이로 줄여 같은 크기에서 읽고,
    원본에만 있는 줄은 빠짐, 영상에만 있는 줄은 추가로 낸다. 같은 크기라 작은 글자·장식 글꼴은 양쪽이 똑같이 못 읽는다.
    실험은 줄인 원본의 OCR 잡음(심볼에서 읽힌 글자)을 사람이 쓴 문구로 걸렀는데, 여기서는 원본 원래 크기 OCR로 거른다."""
    if not ref["auto"] or not mq.any():
        return []
    bq, br = bbox(mq), bbox(ref["mask"]); k = (bq[3] - bq[1]) / max(1, br[3] - br[1]); rf = ref["image"]
    small = rf.resize((max(8, round(rf.width * k)), max(8, round(rf.height * k))), Image.LANCZOS)
    r_lines = [o["t"] for o in ocr_boxes(small) if _near(o["t"], ref["auto"])]
    cur = "".join(ref["auto"]); out = []
    for l in r_lines:
        if not _near(l, q_lines):
            out.append(("빠짐", f"원본 문구 '{l}' 없음(같은 크기 원본 OCR 기준)"))
    for l in q_lines:
        if not _near(l, r_lines) and sub_dist(l, cur) > len(l) / 2:
            out.append(("추가", f"원본에 없는 문구 '{l}'(같은 크기 원본 OCR 기준)"))
    return out


# ---------------------------------------------------------------------------
# 요소
# ---------------------------------------------------------------------------
def _groups(mask, gap):
    import cv2
    k = max(1, int(gap)); d = cv2.dilate(mask.astype(np.uint8), np.ones((k, k), np.uint8))
    n, lab = cv2.connectedComponents(d); out = []
    for i in range(1, n):
        m = (lab == i) & mask
        if m.sum():
            out.append({"box": list(bbox(m)), "mask": m, "area": int(m.sum())})
    return sorted(out, key=lambda g: -g["area"])


def _boxed_f1(ma, mb, size=96, tol=2) -> float:
    import cv2
    def nm(m):
        x1, y1, x2, y2 = bbox(m); return cv2.resize(m[y1:y2, x1:x2].astype(np.uint8), (size, size), interpolation=cv2.INTER_AREA) > 0
    a, b = nm(ma), nm(mb); k = np.ones((2 * tol + 1, 2 * tol + 1), np.uint8)
    p = (a & (cv2.dilate(b.astype(np.uint8), k) > 0)).sum() / max(1, a.sum()); r = (b & (cv2.dilate(a.astype(np.uint8), k) > 0)).sum() / max(1, b.sum())
    return float(2 * p * r / max(1e-9, p + r))


def _expand(b, f, W=None, H=None):
    w, h = b[2] - b[0], b[3] - b[1]; o = [b[0] - w * f, b[1] - h * f, b[2] + w * f, b[3] + h * f]
    return [max(0, o[0]), max(0, o[1]), min(W, o[2]) if W else o[2], min(H, o[3]) if H else o[3]]


def reference(image: Image.Image, ref_text: str) -> dict:
    """원본 로고 1장을 요소(주 문구·보조 문구·심볼, 또는 로고 전체)로 나눈다."""
    from .video_logo_candidates import on_white
    rf = trim_ref(on_white(image)); mr = fg_mask(rf); H, W = mr.shape; lines = ref_lines(ref_text); texts = []
    full = ocr_boxes(rf)
    for o in full:
        i = match_line(o["t"], lines)
        if i is not None:
            texts.append({**o, "ref_t": lines[i]})
    els = []
    if texts:
        hmax = max(t["box"][3] - t["box"][1] for t in texts)
        els = [{"type": "main" if t["box"][3] - t["box"][1] >= .8 * hmax else "sub", "name": t["ref_t"], "box": t["box"]} for t in texts]
        tm = np.zeros_like(mr)
        for t in texts:
            x1, y1, x2, y2 = [int(round(v)) for v in _expand(t["box"], .08, W, H)]; tm[y1:y2, x1:x2] = True
        # 글자가 아닌 전경은 하나의 심볼이다(점·잎·획처럼 떨어진 조각도 한 심볼)
        sym = np.zeros_like(mr)
        for g in _groups(mr & ~tm, .05 * H):
            if g["area"] >= .03 * mr.sum():
                sym |= g["mask"]
        if sym.any():
            els.append({"type": "symbol", "name": "심볼", "box": list(bbox(sym)), "mask": sym, "share": float(sym.sum() / mr.sum())})
    if not els or not any(e["type"] in ("main", "symbol") for e in els):
        els = [{"type": "whole", "name": "로고 전체", "box": list(bbox(mr)), "mask": mr}]
    # auto: 원본 원래 크기 OCR 줄. 기준 문구가 없을 때 같은 크기 비교의 잡음 거르기에만 쓴다
    return {"image": rf, "mask": mr, "elements": els, "lines": lines, "auto": [] if lines else [o["t"] for o in full]}


def compare(q: Image.Image, ref: dict) -> dict:
    """영상 crop 1장 대 원본 요소. 반환: outcome(match/diff/unknown), findings, elements, color."""
    mq = separate(q); H, W = mq.shape; qocr = ocr_boxes(q); els, lines = ref["elements"], ref["lines"]; cur = "".join(lines)
    found = {}
    for o in qocr:
        for i, e in enumerate(els):
            if e["type"] in ("main", "sub") and i not in found and match_line(o["t"], [e["name"]]) is not None:
                found[i] = o["box"]; o["used"] = True; break
    anc = next((i for i, e in enumerate(els) if e["type"] == "main" and i in found), None)
    qg = _groups(mq, .05 * H) if mq.any() else []
    if anc is not None:
        rb, qb = els[anc]["box"], found[anc]; s = (qb[3] - qb[1]) / max(1, rb[3] - rb[1])
    else:
        rb = next((e["box"] for e in els if e["type"] in ("symbol", "whole")), list(bbox(ref["mask"])))
        qb = list(bbox(mq)) if mq.any() else [0, 0, W, H]
        if any(e["type"] in ("main", "sub") for e in els):  # 글자를 못 읽으면 전경 전체끼리 맞춘다
            rb = list(bbox(ref["mask"]))
        s = min((qb[2] - qb[0]) / max(1, rb[2] - rb[0]), (qb[3] - qb[1]) / max(1, rb[3] - rb[1]))
    rc, qc = ((rb[0] + rb[2]) / 2, (rb[1] + rb[3]) / 2), ((qb[0] + qb[2]) / 2, (qb[1] + qb[3]) / 2)
    mp = lambda b: [(b[0] - rc[0]) * s + qc[0], (b[1] - rc[1]) * s + qc[1], (b[2] - rc[0]) * s + qc[0], (b[3] - rc[1]) * s + qc[1]]
    findings, status = [], []
    for i, e in enumerate(els):
        eb = mp(e["box"]); st = "present"
        if e["type"] in ("main", "sub"):
            if i not in found:
                x1, y1, x2, y2 = [int(round(v)) for v in _expand(eb, .1, W, H)]
                region = mq[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else np.zeros((1, 1), bool)
                if eb[3] - eb[1] < SMALL_TEXT_PX:
                    st = "not_comparable_small"
                elif region.size and region.mean() > .08:
                    st = "not_comparable_unread"
                else:
                    st = "missing"; findings.append(("빠짐", f"{'주 문구' if e['type'] == 'main' else '보조 문구'} '{e['name']}' 없음"))
        else:
            x1, y1, x2, y2 = [int(round(v)) for v in _expand(eb, .15, W, H)]
            region = np.zeros_like(mq); region[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = True
            share = (mq & region).sum() / max(1, e["mask"].sum() * s * s)
            if share < .3:
                away = lambda b: b[2] < x1 or b[0] > x2 or b[3] < y1 or b[1] > y2
                if next((g for g in qg if away(g["box"]) and _boxed_f1(g["mask"], e["mask"]) >= .55), None):
                    st = "moved"; findings.append(("배치", f"{e['name']} 위치가 원본과 다름"))
                else:
                    st = "missing"; findings.append(("빠짐", f"{e['name']} 없음"))
            elif eb[3] - eb[1] < SMALL_SYMBOL_PX:
                st = "not_comparable_small"
            else:
                own = np.zeros_like(mq)  # 심볼 자리에 닿는 전경 덩어리만 비교한다(사각형 안 전부가 아니라)
                for g in qg:
                    gb = g["box"]
                    if not (gb[2] < x1 or gb[0] > x2 or gb[3] < y1 or gb[1] > y2) and (g["mask"] & region).sum() >= .5 * g["area"]:
                        own |= g["mask"]
                f1 = _boxed_f1(own if own.any() else mq & region, e["mask"])
                if f1 < SYMBOL_F1:
                    st = "changed"; findings.append(("가림·변형", f"{e['name']} 모양이 원본과 다름(F1 {f1:.2f})"))
        status.append({"type": e["type"], "name": e["name"], "status": st, "box": [round(float(v), 1) for v in eb]})
    for o in (qocr if lines else []):  # 기준 문구가 없으면 아래 같은 크기 원본 OCR과 비교한다
        if not o.get("used") and match_line(o["t"], lines) is None and sub_dist(o["t"], cur) > len(o["t"]) / 2:
            findings.append(("추가", f"원본에 없는 문구 '{o['raw']}'"))
    if not lines:
        findings += same_size_text([o["t"] for o in qocr], ref, mq)
    mapped = [_expand(mp(e["box"]), .2, W, H) for e in els]
    for g in qg:
        b = g["box"]
        if g["area"] >= .06 * mq.sum() and all(b[2] < m[0] or b[0] > m[2] or b[3] < m[1] or b[1] > m[3] for m in mapped):
            if not any(not (o["box"][2] < b[0] or o["box"][0] > b[2] or o["box"][3] < b[1] or o["box"][1] > b[3]) for o in qocr):
                findings.append(("추가", f"원본에 없는 요소 (위치 {[int(v) for v in b]})"))
    sy = next((i for i, e in enumerate(els) if e["type"] == "symbol"), None)
    if sy is not None and anc is not None and status[sy]["status"] == "present" and els[sy].get("share", 0) >= .15 \
            and status[sy]["box"][3] - status[sy]["box"][1] >= SMALL_SYMBOL_PX:
        rr = (els[sy]["box"][3] - els[sy]["box"][1]) / max(1, els[anc]["box"][3] - els[anc]["box"][1])
        x1, y1, x2, y2 = [int(round(v)) for v in _expand(mp(els[sy]["box"]), .15, W, H)]; sub = mq[y1:y2, x1:x2]
        if sub.any():
            qr = (bbox(sub)[3] - bbox(sub)[1]) / max(1, found[anc][3] - found[anc][1])
            if abs(np.log(qr / rr)) > np.log(RATIO_LIMIT):
                findings.append(("배치", f"심볼:문구 크기 비율 다름 (원본 {rr:.2f}, 영상 {qr:.2f})"))
    outcome = "diff" if findings else "unknown" if any(x["status"].startswith("not_comparable") for x in status) else "match"
    names = sorted({f[1] for f in findings})
    return {"outcome": outcome, "findings": names, "kinds": sorted({f[0] for f in findings}), "elements": status,
            "ocr": [o["raw"] for o in qocr], "color": color(q, mq, ref["image"], ref["mask"])}


def color(q: Image.Image, mq: np.ndarray, rf: Image.Image, mr: np.ndarray, size=96) -> dict:
    """유채색 화소끼리만 비교한다. 유채색이 전경의 8% 미만이면 단색(흰색·검은색 사용)으로 본다.
    hue: 둘 다 유채색인 겹침 화소의 색상 차이 중앙값(도), newc/lostc: 무채색→유채색 / 유채색→무채색 비율."""
    import cv2
    def chroma(rgb):
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV); return (hsv[..., 1] > 70) & (hsv[..., 2] > 60), hsv[..., 0].astype(np.float32) * 2
    a = np.asarray(q.convert("RGB")); core = cv2.erode(mq.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0; core = core if core.sum() >= 20 else mq
    share = float(chroma(a)[0][core].mean()) if core.sum() else 0.0
    out = {"mono": share < .08, "hue": None, "newc": None, "lostc": None}
    if out["mono"] or not mq.any():
        return out
    def boxed(img, m):
        x1, y1, x2, y2 = bbox(m)
        return (cv2.resize(np.asarray(img.convert("RGB"))[y1:y2, x1:x2], (size, size), interpolation=cv2.INTER_AREA),
                cv2.resize(m[y1:y2, x1:x2].astype(np.uint8), (size, size), interpolation=cv2.INTER_NEAREST) > 0)
    (qa, qm), (ra, rm) = boxed(q, mq), boxed(rf, mr); both = qm & rm
    if both.sum() < 20:
        return out
    cq, hq = chroma(qa); cr, hr = chroma(ra); bb = both & cq & cr
    if bb.sum() >= 10:
        d = np.abs(hq[bb] - hr[bb]); out["hue"] = round(float(np.median(np.minimum(d, 360 - d))), 1)
    if (both & cq).sum() >= 10:
        out["newc"] = round(float((both & cq & ~cr).sum() / (both & cq).sum()), 3)
    if (both & cr).sum() >= 10:
        out["lostc"] = round(float((both & cr & ~cq).sum() / (both & cr).sum()), 3)
    return out


# ---------------------------------------------------------------------------
# 묶음
# ---------------------------------------------------------------------------
def crop_at(frame: Image.Image, bbox_display, display_size, source: str) -> Image.Image:
    """확정 화면 좌표(display_size 기준) 상자를 원본 프레임에서 자른다. 후보 상자는 8% 넓힌다."""
    k = frame.width / display_size[0]; x1, y1, x2, y2 = [c * k for c in bbox_display]
    if source == "candidate":
        px, py = (x2 - x1) * CAND_PAD, (y2 - y1) * CAND_PAD; x1, y1, x2, y2 = x1 - px, y1 - py, x2 + px, y2 + py
    return frame.crop((max(0, round(x1)), max(0, round(y1)), min(frame.width, round(x2)), min(frame.height, round(y2))))


def bundle_seconds(crop: dict, groups: list[dict] | None) -> list[int]:
    """묶음에 속한 초: 후보는 그 묶음의 모든 초, 직접 지정은 사용자가 정한 구간."""
    if crop.get("source") == "candidate" and groups is not None and crop.get("group") is not None:
        return sorted(int(s) for s in groups[crop["group"]]["secs"])
    return sorted({s for a, b in crop.get("intervals", [[crop["sec"], crop["sec"]]]) for s in range(int(a), int(b) + 1)})


def bundle_evidence(per: list[dict]) -> dict:
    """초별 결과 → 묶음 근거(판정 없음). per = [{sec, size, outcome, findings, sharp, color}]."""
    if not per:
        return {"n_secs": 0, "outcomes": {}, "findings": [], "representative": None, "changes": []}
    rep = max(per, key=lambda p: (p["size"][0] * p["size"][1], p["sharp"]))
    return {"n_secs": len(per), "outcomes": dict(Counter(p["outcome"] for p in per)),
            "findings": [[f, c] for f, c in Counter(f for p in per for f in p["findings"]).most_common()],
            "representative": {"sec": rep["sec"], "outcome": rep["outcome"], "findings": rep["findings"], "color": rep["color"]},
            "changes": [per[i]["sec"] for i in range(1, len(per)) if per[i]["outcome"] != per[i - 1]["outcome"]]}


def sharpness(img: Image.Image) -> float:
    import cv2
    return float(cv2.Laplacian(cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())
