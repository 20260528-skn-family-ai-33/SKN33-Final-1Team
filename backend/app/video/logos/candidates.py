"""1부: 영상에서 원본 로고 후보를 찾아 순위를 매긴다(사용자가 확정 화면에서 위치를 고르기 전 단계).

실험 영상 13개에서 고정한 설정을 그대로 옮겼다(검증 영상 결과로 바꾸지 않는다).
  ① 1초 간격 프레임
  ② OWLv2 후보 상자: 프롬프트 6개, 전체 화면 + 타일 10개, 점수 0.05 이상 60개, NMS 0.5
  ③ 점수 0.1 이상·화면 40% 이하만 남기고, 같은 초 안의 겹치는 상자를 하나로 묶는다(겹침·포함 + 비슷한 모양)
  ④ 시간 묶음: 3초 이내 간격, 위치·크기·모양이 이어지면 같은 묶음(화면 전환으로는 끊지 않는다)
  ⑤ 원본 로고마다 묶음 점수 = 초별 최고 SigLIP2 유사도의 상위 3개 평균(짧은 변 48px 미만 상자는 원본을 작게 줄인 사본과 비교)
  ⑥ 묶음 점수 상위 40개의 대표 상자를 OCR해 원본 문구(3글자 이상 조각)를 80% 이상 읽으면 +0.10 → 순위 점수
     원본 문구는 사람이 쓴 기준 문구, 비어 있으면 원본 로고 OCR로 대신한다(ref_text_for_bonus, 실험 설정 밖의 보완)
결과는 확정 화면(frontend/src/pages/video/LogoConfirmPage.jsx)이 읽는 data.json 형식이다.
"""
from __future__ import annotations

from pathlib import Path
import unicodedata

import numpy as np
from PIL import Image

from backend.app.video.logos import models as M

PROMPTS = ["a brand logo", "a logo", "an emblem", "a brand symbol", "a watermark", "a logo mark"]
SAVE_THR, KEEP, NMS_IOU = 0.05, 60, 0.5
MIN_OWL, MAX_AREA = 0.1, 0.4
GAP = 3
OCR_TOP, OCR_BONUS, OCR_MATCH = 40, 0.10, 0.8
DISPLAY_LONG = 1280  # 확정 화면에 보내는 프레임 긴 변
OWL_BATCH = 2  # 타일을 한 번에 넣는 수. 960px 입력은 장당 어텐션만 ~1GB라 10장을 한 번에 넣으면 16GB GPU에서 OOM


# ---------------------------------------------------------------------------
# 프레임
# ---------------------------------------------------------------------------
def sample_frames(video: Path, out: Path) -> tuple[list[dict], list[int], int]:
    """초마다 1장을 원본 해상도(full/<sec>.jpg)와 화면용(f/<sec>.jpg)으로 저장한다.
    반환: (뽑은 프레임, 못 뽑은 초, 영상 전체 초 수). 못 뽑은 초는 사용자에게 알린다."""
    import cv2
    (out / "full").mkdir(parents=True, exist_ok=True); (out / "f").mkdir(exist_ok=True)
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 30; n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    want = {int(round(sec * fps)): sec for sec in range(0, int(n / fps) + 1) if round(sec * fps) < n}; pos, frames = -1, []
    expected = sorted(want.values())
    while want:
        ok = cap.grab(); pos += 1
        if not ok:
            break
        if pos not in want:
            continue
        sec = want.pop(pos); ok, bgr = cap.retrieve()
        if not ok:
            continue
        im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)); im.save(out / "full" / f"{sec}.jpg", quality=90)
        s = DISPLAY_LONG / max(im.size)
        im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS).save(out / "f" / f"{sec}.jpg", quality=82)
        frames.append({"sec": sec, "size": list(im.size)})
    cap.release()
    got = {f["sec"] for f in frames}
    return frames, [s for s in expected if s not in got], len(expected)


# ---------------------------------------------------------------------------
# OWLv2 후보 상자
# ---------------------------------------------------------------------------
def _shrink(im):
    # 처리기가 어차피 960으로 줄이므로 미리 줄여 4K 입력의 느린 CPU 리사이즈를 피한다.
    k = 960 / max(im.size)
    return (im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS), 1 / k) if k < 1 else (im, 1.0)


def _text_detect(images):
    import torch
    proc, model = M.owl(); small = [_shrink(im) for im in images]
    inputs = proc(text=[PROMPTS] * len(images), images=[s for s, _ in small], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model(**inputs)
    res = proc.image_processor.post_process_object_detection(out, threshold=SAVE_THR, target_sizes=[(s.height, s.width) for s, _ in small])
    return [(r["boxes"].float().cpu() * k, r["scores"].float().cpu()) for r, (_, k) in zip(res, small)]


def _tiles(w, h):
    out = []
    if w >= h:
        tw, th = int(w * .6), int(h * .6)
        for x in (0, w - tw):
            for y in (0, h - th):
                out.append((x, y, x + tw, y + th))
        bh, bw = int(h * .22), int(w * .4)
        for y in (0, h - bh):
            for x in (0, (w - bw) // 2, w - bw):
                out.append((x, y, x + bw, y + bh))
    else:
        tw, th = int(w * .6), int(h * .4)
        for x in (0, w - tw):
            for y in (0, (h - th) // 2, h - th):
                out.append((x, y, x + tw, y + th))
        bh, bw = int(h * .15), int(w * .6)
        for y in (0, h - bh):
            for x in (0, w - bw):
                out.append((x, y, x + bw, y + bh))
    return out


def detect(image: Image.Image) -> list[dict]:
    """전체 화면 + 타일 10개에서 찾은 상자를 NMS로 합쳐 점수 순 상위 60개를 돌려준다."""
    import torch
    from torchvision.ops import nms
    w, h = image.size
    (bb, sb), = _text_detect([image]); boxes, scores = [bb], [sb]
    tl = _tiles(w, h)
    found = [r for i in range(0, len(tl), OWL_BATCH) for r in _text_detect([image.crop(t) for t in tl[i:i + OWL_BATCH]])]
    for (bt, st), t in zip(found, tl):
        boxes.append(bt + torch.tensor([t[0], t[1], t[0], t[1]])); scores.append(st)
    B, S = torch.cat(boxes), torch.cat(scores)
    if not len(S):
        return []
    keep = nms(B, S, NMS_IOU); keep = keep[S[keep].argsort(descending=True)[:KEEP]]
    return [{"bbox": [round(v, 1) for v in B[i].tolist()], "score": round(float(S[i]), 4)} for i in keep]


# ---------------------------------------------------------------------------
# 묶기·순위
# ---------------------------------------------------------------------------
def _area(b): return max(0, b[2] - b[0]) * max(0, b[3] - b[1])
def _inter(a, b): return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
def _iou(a, b): i = _inter(a, b); return i / (_area(a) + _area(b) - i + 1e-9)
def _short(b): return min(b[2] - b[0], b[3] - b[1])


def _pad_crop(im, b):
    x1, y1, x2, y2 = b; px, py = (x2 - x1) * .1, (y2 - y1) * .1
    c = im.crop((max(0, x1 - px), max(0, y1 - py), min(im.width, x2 + px), min(im.height, y2 + py))); c.thumbnail((384, 384)); return c


def on_white(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA"); bg = Image.new("RGBA", im.size, "white"); bg.alpha_composite(im); return bg.convert("RGB")


def _norm(t): return "".join(c for c in unicodedata.normalize("NFC", t).casefold() if c.isalnum())


def _lcs_ratio(q, r):
    if not q or not r:
        return 0.0
    best = 0
    for i in range(len(r)):
        for j in range(i + best + 1, len(r) + 1):
            if r[i:j] in q:
                best = j - i
            else:
                break
    return best / len(r)


def seg_match(text: str, ref_text: str) -> float:
    """OCR 글자가 원본 문구 조각(3글자 이상)을 얼마나 이어서 읽었는지(0~1)."""
    q = _norm(text); segs = [s for s in (_norm(s) for s in ref_text.split()) if len(s) >= 3] or [_norm(ref_text)]
    return max(_lcs_ratio(q, s) for s in segs)


def _ocr_text(im, b) -> str:
    x1, y1, x2, y2 = b; px, py = (x2 - x1) * .1, (y2 - y1) * .1
    c = im.crop((max(0, x1 - px), max(0, y1 - py), min(im.width, x2 + px), min(im.height, y2 + py)))
    k = min(1.0, 2000 / max(c.size))
    if k < 1:
        c = c.resize((round(c.width * k), round(c.height * k)), Image.LANCZOS)
    if min(c.size) < 8:
        return ""
    return " ".join(t for t, _, _ in M.ocr_predict(np.asarray(c.convert("RGB"))))


def ref_text_for_bonus(r: dict) -> tuple[str, str | None]:
    """가산점 기준 문구와 그 출처. 사람이 쓴 기준 문구가 우선이고, 비어 있으면 원본 로고 OCR(점수 0.5 이상 줄)로 대신한다.
    원본 OCR이 잘못 읽어도 후보가 그 글자와 맞을 일이 드물어 가산점이 안 붙을 뿐이다(기준 문구를 비운 것과 같다)."""
    if r["text"]:
        return r["text"], "input"
    im = on_white(r["image"]); k = min(1.0, 2000 / max(im.size))
    if k < 1:
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    text = " ".join(t for t, _, s in M.ocr_predict(np.asarray(im)) if s >= .5) if min(im.size) >= 8 else ""
    return text, "ocr" if text.strip() else None


def build_candidates(frames_dir: Path, frames: list[dict], refs: list[dict], progress=lambda p, d: None) -> dict:
    """refs = [{"name", "image": PIL, "text"}] → 확정 화면용 data(refs, groups, size, seconds)."""
    # 원본 임베딩: 원본 + 짧은 변 20·28·40·56px로 줄인 사본(작은 상자용)
    ref_full, ref_ms = [], []
    for r in refs:
        im = on_white(r["image"])
        variants = [im] + [im.resize((max(1, round(im.width * s / min(im.size))), max(1, round(im.height * s / min(im.size)))), Image.BOX)
                           for s in (20, 28, 40, 56) if s < min(im.size)]
        e = M.siglip_embed(variants); ref_full.append(e[0]); ref_ms.append(e)
    boxes, thumbs, chunks = [], [], []
    for i, f in enumerate(frames):
        im = Image.open(frames_dir / "full" / f"{f['sec']}.jpg").convert("RGB"); w, h = im.size
        for b in detect(im):
            if b["score"] < MIN_OWL or _area(b["bbox"]) > MAX_AREA * w * h:
                continue
            boxes.append({"sec": f["sec"], "bbox": b["bbox"], "owl": b["score"]}); thumbs.append(_pad_crop(im, b["bbox"]))
        if len(thumbs) >= 256:
            chunks.append(M.siglip_embed(thumbs)); thumbs = []
        progress(5 + 60 * (i + 1) / max(1, len(frames)), f"후보 탐지 {i + 1}/{len(frames)}초")
    chunks.append(M.siglip_embed(thumbs))
    E = np.concatenate([c for c in chunks if len(c)]) if boxes else np.zeros((0, 1152), np.float32)
    for i, b in enumerate(boxes):
        b["emb"] = i
        b["sim"] = [round(float(max(ref_ms[r] @ E[i]) if _short(b["bbox"]) < 48 else ref_full[r] @ E[i]), 4) for r in range(len(refs))]
    # 같은 초 안의 겹치는 상자(겹침·포함 + 비슷한 모양)를 하나로, 구성원은 모두 남긴다
    reps = []
    for sec in sorted({b["sec"] for b in boxes}):
        kept = []
        for b in sorted([b for b in boxes if b["sec"] == sec], key=lambda b: -b["owl"]):
            host = next((k for k in kept if _iou(k["bbox"], b["bbox"]) >= .5 or (_inter(k["bbox"], b["bbox"]) / max(1, min(_area(k["bbox"]), _area(b["bbox"]))) >= .8
                         and float(E[k["emb"]] @ E[b["emb"]]) >= .8)), None)
            if host:
                host["members"].append(b)
            else:
                kept.append({**b, "members": [b]})
        reps += kept
    # 시간 묶음(위치·크기·모양 연속, 3초 이내). 화면 전환으로는 끊지 않는다: 자막·구석 로고는 배경이 바뀌어도 남는다.
    groups = []
    for r in sorted(reps, key=lambda r: r["sec"]):
        best, best_s = None, .75
        for g in groups:
            last = g[-1]; dt = r["sec"] - last["sec"]
            if not 0 < dt <= GAP:
                continue
            a, b = last["bbox"], r["bbox"]; ratio = max(_area(a), _area(b)) / max(1, min(_area(a), _area(b)))
            ca, cb = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            near = _iou(a, b) >= .3 or (abs(ca[0] - cb[0]) + abs(ca[1] - cb[1])) < .5 * max(a[2] - a[0], a[3] - a[1])
            s = float(E[last["emb"]] @ E[r["emb"]])
            if ratio < 2.5 and near and s >= best_s:
                best, best_s = g, s
        (best.append(r) if best else groups.append([r]))
    members = [[(m["sec"], m["bbox"], m["sim"]) for r in g for m in r["members"]] for g in groups]
    progress(70, "후보 OCR")
    bonus_refs = [ref_text_for_bonus(r) for r in refs]
    # 원본마다 묶음 점수 상위 40개의 대표 상자를 OCR한다(같은 상자는 한 번만)
    scale = DISPLAY_LONG / max(frames[0]["size"]) if frames else 1.0
    cache, ocr_hits, frame_cache = {}, [], {}
    for ri, r in enumerate(refs):
        scored = []
        for gi, mem in enumerate(members):
            per = {}
            for sec, b, sim in mem:
                per[sec] = max(per.get(sec, 0), sim[ri])
            top = sorted(per.values(), reverse=True)[:3]; best = max(mem, key=lambda m: m[2][ri])
            scored.append((sum(top) / len(top), gi, best[0], best[1]))
        hits = {}
        for _, gi, sec, b in sorted(scored, key=lambda x: -x[0])[:OCR_TOP]:
            key = f"{sec}:{b}"
            if key not in cache:
                if sec not in frame_cache:
                    frame_cache[sec] = Image.open(frames_dir / "full" / f"{sec}.jpg").convert("RGB")
                cache[key] = _ocr_text(frame_cache[sec], b)
            ref_text = bonus_refs[ri][0]
            hits[gi] = [cache[key], seg_match(cache[key], ref_text) >= OCR_MATCH if ref_text.strip() else False]
        ocr_hits.append(hits)
        progress(70 + 25 * (ri + 1) / max(1, len(refs)), f"후보 OCR {ri + 1}/{len(refs)}")
    out_groups = []
    for gi, mem in enumerate(members):
        rep = {}; best = [None] * len(refs); pcs = [dict() for _ in refs]; pbox = [dict() for _ in refs]
        for sec, b, sim in mem:
            rep.setdefault(sec, b)
            for ri, x in enumerate(sim):
                if x > pcs[ri].get(sec, -1):
                    pcs[ri][sec] = x; pbox[ri][sec] = b
                if best[ri] is None or x > best[ri][0]:
                    best[ri] = (x, sec, b)
        score = [round(sum(sorted(pc.values(), reverse=True)[:3]) / len(sorted(pc.values(), reverse=True)[:3]), 4) for pc in pcs]
        rank = [round(score[ri] + (OCR_BONUS if ocr_hits[ri].get(gi, [None, False])[1] else 0), 4) for ri in range(len(refs))]
        out_groups.append({"secs": sorted(rep), "box": [{str(k): [round(c * scale, 1) for c in v] for k, v in pb.items()} for pb in pbox],
                           "score": score, "rank": rank, "rep": [[b[1], [round(c * scale, 1) for c in b[2]]] for b in best]})
    size = frames[0]["size"] if frames else [0, 0]
    return {"size": [round(size[0] * scale), round(size[1] * scale)], "seconds": len(frames),
            "refs": [{"name": r["name"], "ocr": {str(gi): v for gi, v in ocr_hits[ri].items()},
                      "bonus_text": bonus_refs[ri][0], "bonus_basis": bonus_refs[ri][1]} for ri, r in enumerate(refs)], "groups": out_groups}
