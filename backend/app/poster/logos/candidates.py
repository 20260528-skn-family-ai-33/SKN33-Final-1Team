"""1부: 포스터 한 장에서 원본 로고 후보를 찾아 원본 로고마다 순위를 매긴다(사용자가 위치를 확정하기 전 단계).

영상 로고 1부(backend/app/video/logos/candidates.py)의 모델·탐지 프롬프트·유사도·OCR 가산점 규칙을 그대로 쓰고,
포스터에 맞게 아래만 바꿨다.
  ① 프레임 대신 포스터 한 장: 시간 묶음(3초 이내 연결)과 초별 상위 3개 평균이 없다.
     겹치는 상자를 하나로 묶는 규칙(겹침·포함 + 비슷한 모양)은 영상의 '같은 초 안 묶기'와 같고,
     후보 점수 = 묶음 안 상자 중 그 원본 로고와 가장 닮은 상자의 SigLIP2 유사도(대표 상자도 그 상자)
  ② 촘촘한 타일: 포스터는 해상도가 높고 주최·후원 로고가 아래 띠에 작게 모인다. 영상과 같은 전체 화면 + 타일 10개에
     960px 격자 타일(25% 겹침)을 더해 긴 변이 큰 포스터에서도 작은 로고가 OWLv2 입력(960px)에서 줄어들지 않게 한다
  ③ 후보 상한 60 → 120: 포스터 한 장에는 로고·문구 상자가 영상 한 초보다 많다
  ④ 가산점 기준 문구의 줄 구분(|)을 띄어쓰기로 바꿔 줄마다 조각으로 비교한다
좌표는 포스터 원본(EXIF 회전을 반영한 화면 방향) 픽셀이다. 확정 화면·정밀 대조가 같은 좌표를 쓴다.
②③의 값은 평가셋으로 정한 값이 아니라 영상 설정에서 옮긴 잠정값이다. 포스터 정답 자료로 보정하기 전까지 바꿀 수 있다.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from backend.app.video.logos import candidates as V
from backend.app.video.logos import models as M

GRID_TILE = 960        # OWLv2 입력 한 변. 이 크기 이하의 타일은 줄이지 않고 들어간다
GRID_OVERLAP = 0.25    # 격자 타일 겹침(타일 경계에 걸린 로고를 이웃 타일이 온전히 보게)
GRID_MIN_SIDE = 1440   # 긴 변이 이보다 짧은 포스터는 영상 타일 10개로 충분하다
KEEP = 120
SMALL_BOX = 48         # 짧은 변이 이보다 작은 상자는 원본을 작게 줄인 사본과 비교한다(영상과 같음)


# ---------------------------------------------------------------------------
# OWLv2 후보 상자
# ---------------------------------------------------------------------------
def grid_tiles(w: int, h: int, tile: int = GRID_TILE, overlap: float = GRID_OVERLAP) -> list[tuple[int, int, int, int]]:
    """포스터를 tile×tile 격자로 겹쳐 자른다. 마지막 타일이 가장자리에 닿도록 시작점을 고르게 나눈다."""
    if max(w, h) < GRID_MIN_SIDE:
        return []
    step = tile * (1 - overlap)

    def starts(n: int) -> list[int]:
        if n <= tile:
            return [0]
        k = math.ceil((n - tile) / step)
        return sorted({round(i * (n - tile) / k) for i in range(k + 1)})

    tw, th = min(tile, w), min(tile, h)
    return [(x, y, x + tw, y + th) for y in starts(h) for x in starts(w)]


def detect(image: Image.Image, progress=lambda done, total: None) -> list[dict]:
    """전체 화면 + 영상 타일 10개 + 격자 타일에서 찾은 상자를 NMS로 합쳐 점수 순 상위 KEEP개를 돌려준다."""
    import torch
    from torchvision.ops import nms
    w, h = image.size
    tiles = V._tiles(w, h) + grid_tiles(w, h)
    total = 1 + len(tiles)
    (bb, sb), = V._text_detect([image]); boxes, scores = [bb], [sb]
    progress(1, total)
    for i in range(0, len(tiles), V.OWL_BATCH):
        part = tiles[i:i + V.OWL_BATCH]
        for (bt, st), t in zip(V._text_detect([image.crop(t) for t in part]), part):
            boxes.append(bt + torch.tensor([t[0], t[1], t[0], t[1]])); scores.append(st)
        progress(1 + i + len(part), total)
    B, S = torch.cat(boxes), torch.cat(scores)
    if not len(S):
        return []
    keep = nms(B, S, V.NMS_IOU); keep = keep[S[keep].argsort(descending=True)[:KEEP]]
    return [{"bbox": [round(v, 1) for v in B[i].tolist()], "score": round(float(S[i]), 4)} for i in keep]


# ---------------------------------------------------------------------------
# 원본 로고·후보 유사도와 순위
# ---------------------------------------------------------------------------
def _ref_embeddings(refs: list[dict]) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """원본 + 짧은 변 20·28·40·56px로 줄인 사본(작은 상자용) 임베딩. 영상 1부와 같다."""
    full, multi = [], []
    for r in refs:
        im = V.on_white(r["image"])
        variants = [im] + [im.resize((max(1, round(im.width * s / min(im.size))), max(1, round(im.height * s / min(im.size)))), Image.BOX)
                           for s in (20, 28, 40, 56) if s < min(im.size)]
        e = M.siglip_embed(variants); full.append(e[0]); multi.append(e)
    return full, multi


def _merge(boxes: list[dict], E: np.ndarray) -> list[dict]:
    """겹치는 상자(IoU 0.5 이상, 또는 작은 쪽의 80% 이상이 겹치고 모양 유사도 0.8 이상)를 하나로 묶는다. 구성원은 모두 남긴다."""
    kept = []
    for b in sorted(boxes, key=lambda b: -b["owl"]):
        host = next((k for k in kept if V._iou(k["bbox"], b["bbox"]) >= .5
                     or (V._inter(k["bbox"], b["bbox"]) / max(1, min(V._area(k["bbox"]), V._area(b["bbox"]))) >= .8
                         and float(E[k["emb"]] @ E[b["emb"]]) >= .8)), None)
        if host:
            host["members"].append(b)
        else:
            kept.append({**b, "members": [b]})
    return kept


def bonus_text(text: str) -> str:
    """가산점 비교용 기준 문구: 줄 구분(|)을 띄어쓰기로 바꿔 줄마다 조각이 되게 한다."""
    return " ".join(text.replace("|", " ").split())


def build_candidates(image: Image.Image, refs: list[dict], progress=lambda p, d: None) -> dict:
    """image = 포스터(RGB, 화면 방향), refs = [{"name", "image": PIL, "text"}] → 확정 화면용 data.

    반환: {"size": [W, H], "refs": [{name, ocr: {후보 번호: [OCR 글자, 문구 일치]}, bonus_text, bonus_basis}],
          "candidates": [{"box", "owl", "rep": [원본별 대표 상자], "score": [원본별 유사도], "rank": [원본별 순위 점수]}]}
    """
    image = image.convert("RGB"); w, h = image.size
    progress(2, "원본 로고 임베딩")
    ref_full, ref_ms = _ref_embeddings(refs)
    found = detect(image, lambda done, total: progress(5 + 55 * done / max(1, total), f"후보 탐지 타일 {done}/{total}"))
    boxes = [{"bbox": b["bbox"], "owl": b["score"]} for b in found
             if b["score"] >= V.MIN_OWL and V._area(b["bbox"]) <= V.MAX_AREA * w * h]
    progress(62, f"후보 {len(boxes)}개 유사도 계산")
    E = M.siglip_embed([V._pad_crop(image, b["bbox"]) for b in boxes]) if boxes else np.zeros((0, 1152), np.float32)
    for i, b in enumerate(boxes):
        b["emb"] = i
        b["sim"] = [round(float(max(ref_ms[r] @ E[i]) if V._short(b["bbox"]) < SMALL_BOX else ref_full[r] @ E[i]), 4)
                    for r in range(len(refs))]
    groups = _merge(boxes, E)
    candidates = []
    for g in groups:
        best = [max(g["members"], key=lambda m: m["sim"][ri]) for ri in range(len(refs))]
        candidates.append({"box": [round(v, 1) for v in g["bbox"]], "owl": round(float(g["owl"]), 4),
                           "rep": [[round(v, 1) for v in m["bbox"]] for m in best],
                           "score": [m["sim"][ri] for ri, m in enumerate(best)]})
    # 원본마다 유사도 상위 OCR_TOP개 후보의 대표 상자를 OCR해 원본 문구 조각을 80% 이상 읽으면 +0.10(같은 상자는 한 번만 읽는다)
    progress(70, "후보 OCR")
    bonus_refs = [V.ref_text_for_bonus(r) for r in refs]
    cache, ocr_hits = {}, []
    for ri in range(len(refs)):
        ref_text, hits = bonus_text(bonus_refs[ri][0]), {}
        for ci in sorted(range(len(candidates)), key=lambda c: -candidates[c]["score"][ri])[:V.OCR_TOP]:
            box = candidates[ci]["rep"][ri]; key = ",".join(str(v) for v in box)
            if key not in cache:
                cache[key] = V._ocr_text(image, box)
            hits[ci] = [cache[key], V.seg_match(cache[key], ref_text) >= V.OCR_MATCH if ref_text else False]
        ocr_hits.append(hits)
        progress(70 + 25 * (ri + 1) / max(1, len(refs)), f"후보 OCR {ri + 1}/{len(refs)}")
    for ci, c in enumerate(candidates):
        c["rank"] = [round(c["score"][ri] + (V.OCR_BONUS if ocr_hits[ri].get(ci, [None, False])[1] else 0), 4)
                     for ri in range(len(refs))]
    return {"size": [w, h],
            "refs": [{"name": r["name"], "ocr": {str(ci): v for ci, v in ocr_hits[ri].items()},
                      "bonus_text": bonus_refs[ri][0], "bonus_basis": bonus_refs[ri][1]} for ri, r in enumerate(refs)],
            "candidates": candidates}
