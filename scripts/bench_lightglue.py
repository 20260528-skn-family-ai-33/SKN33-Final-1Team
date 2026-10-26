#!/usr/bin/env python3
"""조합 B — LightGlue 기하 검증. OWLv2 크롭을 지정 로고와 DISK+LightGlue로 매칭한다.

지역 특징(DISK) + 학습된 매칭(LightGlue)으로 '같은 로고'인지 기하적으로 검증한다.
점수 = 매칭 개수(많을수록 같은 로고). 임베딩 코사인과 달리 위치·구조가 맞아야 매칭된다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


class LightGlueScorer:
    def __init__(self, device, n_feats=1024, side=256):
        import torch
        import kornia as K
        import kornia.feature as KF
        self.torch, self.K, self.KF = torch, K, KF
        self.device, self.n, self.side = device, n_feats, side
        self.disk = KF.DISK.from_pretrained("depth").to(device).eval()
        self.lg = KF.LightGlueMatcher("disk").to(device).eval()

    def _tensor(self, pil):
        import numpy as np
        img = pil.convert("RGB").resize((self.side, self.side))
        arr = np.asarray(img)
        t = self.K.image_to_tensor(arr, keepdim=False).float() / 255.0
        return t.to(self.device)

    def _feats(self, pil):
        t = self._tensor(pil)
        with self.torch.inference_mode():
            f = self.disk(t, self.n, pad_if_not_divisible=True)[0]
        return f.keypoints, f.descriptors

    def matches(self, crop, query_feats):
        kp2, d2 = query_feats
        kp1, d1 = self._feats(crop)
        if kp1.shape[0] < 4 or kp2.shape[0] < 4:
            return 0
        lafs1 = self.KF.laf_from_center_scale_ori(kp1[None])
        lafs2 = self.KF.laf_from_center_scale_ori(kp2[None])
        with self.torch.inference_mode():
            _, idxs = self.lg(d1, d2, lafs1, lafs2)
        return int(idxs.shape[0])

    def query_feats(self, pil):
        return self._feats(pil)


def main():
    from backend.app.logo_pipeline import LogoDetector, crop, load_rgb

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos-dir", default="/workspace/videos")
    ap.add_argument("--logos-dir", default="/workspace/video_logos")
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--det-threshold", type=float, default=0.1)
    ap.add_argument("--max-boxes", type=int, default=8)
    ap.add_argument("--out", default="/workspace/bench_lightglue.json")
    args = ap.parse_args()

    vdir, ldir = Path(args.videos_dir), Path(args.logos_dir)
    detector = LogoDetector("owlv2")
    scorer = LightGlueScorer(detector.device)
    print(f"OWLv2 + LightGlue(DISK) device={detector.device}", flush=True)

    results = []
    for sub, logo in PAIRS:
        video = next((p for p in sorted(vdir.iterdir())
                      if sub in p.name and p.suffix.lower() in {".mp4", ".avi", ".mov", ".webm"}), None)
        qpath = ldir / logo
        if video is None or not qpath.is_file():
            continue
        qf = scorer.query_feats(load_rgb(qpath))
        work = Path(tempfile.mkdtemp(prefix="lg-"))
        best = 0
        try:
            for fp in ffmpeg_frames(video, work, args.fps):
                boxes = detector.text_detect(fp, threshold=args.det_threshold, max_boxes=args.max_boxes)
                if not boxes:
                    continue
                image = load_rgb(fp)
                for b in boxes:
                    best = max(best, scorer.matches(crop(image, b.xyxy), qf))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        row = {"video": video.name, "logo": logo, "best_matches": best}
        results.append(row)
        print(f"{video.name[:34]:<34} LightGlue 최대 매칭 {best}개", flush=True)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"저장: {args.out}", flush=True)


if __name__ == "__main__":
    main()
