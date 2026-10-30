#!/usr/bin/env python3
"""베이스라인·추가 매처 비교 — pHash / ORB+RANSAC / EVA-CLIP.

OWLv2로 프레임의 로고 영역을 제안하고(기존 조합 A와 동일), 각 크롭을 '지정 로고'와 대조해
세 방법의 최고 점수를 낸다. 딥러닝 임베딩(SigLIP2/CLIP/DINOv2)과 비교할 하한·추가 매처용.

점수 스케일(모두 0~1, 높을수록 '같은 로고'):
  pHash  = 1 - 해밍거리/비트수           (지각 해시 유사도)
  ORB    = RANSAC 인라이어 / 키포인트     (지역 특징 + 기하 검증)
  EVACLIP= 이미지 임베딩 코사인           (EVA02-CLIP)

사용: python scripts/bench_classic.py --videos-dir /workspace/videos --logos-dir /workspace/video_logos --out /workspace/bench_classic.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# (영상 부분문자열 → 지정 로고 파일) — 로고가 명확한 영상만.
PAIRS = [
    ("공공누리", "한국문화정보원.png"),
    ("낭만과학도시 대전", "대전광역시.jpg"),
    ("서울문화재단", "서울문화재단 로고.png"),
    ("마약이 온다", "서울마이소울 로고.png"),
    ("서울시향", "서울시립교향악당.png"),
    ("해맞이", "JCI 로고.png"),
    ("이몽룡", "봉화군 로고.png"),
    ("상주", "상주시 로고.jpg"),
]


def ffmpeg_frames(video, out_dir, fps=0.5):
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps}", "-q:v", "2", str(out_dir / "f%06d.jpg")],
                   check=True, stdin=subprocess.DEVNULL)
    return sorted(out_dir.glob("f*.jpg"))


def phash_score(crop, query, hash_size=16):
    import imagehash
    n = hash_size * hash_size
    h1 = imagehash.phash(crop, hash_size=hash_size)
    h2 = imagehash.phash(query, hash_size=hash_size)
    return round(1 - (h1 - h2) / n, 4)


def make_orb():
    import cv2
    return cv2.ORB_create(nfeatures=1500)


def orb_score(crop, query, orb):
    import cv2
    import numpy as np
    g1 = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(np.asarray(query), cv2.COLOR_RGB2GRAY)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(d1, d2)
    if len(matches) < 8:
        return 0.0
    src = np.float32([k1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    return round(inliers / max(len(k1), len(k2), 1), 4)


class EvaClip:
    def __init__(self, device):
        import open_clip
        import torch
        self.torch = torch
        self.model, _, self.pre = open_clip.create_model_and_transforms(
            "EVA02-B-16", pretrained="merged2b_s8b_b131k")
        self.model = self.model.to(device).eval()
        self.device = device

    def embed(self, img):
        import torch
        import numpy as np
        x = self.pre(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            v = self.model.encode_image(x)
        v = v[0].float().cpu().numpy()
        n = float((v * v).sum()) ** 0.5
        return v / n if n else v

    def score(self, crop_vec, query_vec):
        return round(float((crop_vec * query_vec).sum()), 4)


def main():
    from backend.app.logo_pipeline import LogoDetector, crop, load_rgb
    import numpy as np  # noqa

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos-dir", default="/workspace/videos")
    ap.add_argument("--logos-dir", default="/workspace/video_logos")
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--det-threshold", type=float, default=0.1)
    ap.add_argument("--max-boxes", type=int, default=10)
    ap.add_argument("--out", default="/workspace/bench_classic.json")
    args = ap.parse_args()

    vdir, ldir = Path(args.videos_dir), Path(args.logos_dir)
    detector = LogoDetector("owlv2")
    device = detector.device
    orb = make_orb()
    eva = EvaClip(device)
    print(f"EVA-CLIP device={device}", flush=True)

    results = []
    for sub, logo in PAIRS:
        video = next((p for p in sorted(vdir.iterdir())
                      if sub in p.name and p.suffix.lower() in {".mp4", ".avi", ".mov", ".webm"}), None)
        qpath = ldir / logo
        if video is None or not qpath.is_file():
            print(f"[건너뜀] {sub} / {logo}", flush=True)
            continue
        query = load_rgb(qpath)
        q_eva = eva.embed(query)
        work = Path(tempfile.mkdtemp(prefix="bc-"))
        best = {"phash": 0.0, "orb": 0.0, "evaclip": 0.0}
        try:
            for fp in ffmpeg_frames(video, work, args.fps):
                boxes = detector.text_detect(fp, threshold=args.det_threshold, max_boxes=args.max_boxes)
                if not boxes:
                    continue
                image = load_rgb(fp)
                for b in boxes:
                    c = crop(image, b.xyxy)
                    best["phash"] = max(best["phash"], phash_score(c, query))
                    best["orb"] = max(best["orb"], orb_score(c, query, orb))
                    best["evaclip"] = max(best["evaclip"], eva.score(eva.embed(c), q_eva))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        row = {"video": video.name, "logo": logo, **{k: round(v, 4) for k, v in best.items()}}
        results.append(row)
        print(f"{video.name[:34]:<34} phash={row['phash']}  orb={row['orb']}  evaclip={row['evaclip']}", flush=True)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"저장: {args.out}", flush=True)


if __name__ == "__main__":
    main()
