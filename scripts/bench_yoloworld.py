#!/usr/bin/env python3
"""조합 C — YOLO-World(탐지) + SigLIP2(대조). OWLv2 대신 YOLO-World로 로고 영역을 찾는다.

열린어휘 프롬프트('logo' 등)로 프레임에서 로고 후보를 탐지하고, 각 크롭을 지정 로고와 코사인 대조.
OWLv2(조합 A/기존)와 같은 영상·같은 매처(SigLIP2)로 비교한다.
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


def _cos(a, b):
    return round(sum(x * y for x, y in zip(a, b)), 4)


def main():
    from ultralytics import YOLOWorld
    from backend.app.logo_pipeline import LogoMatcher, crop, load_rgb

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos-dir", default="/workspace/videos")
    ap.add_argument("--logos-dir", default="/workspace/video_logos")
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--conf", type=float, default=0.01, help="YOLO-World 신뢰도 하한(낮게)")
    ap.add_argument("--min-cosine", type=float, default=0.55)
    ap.add_argument("--out", default="/workspace/bench_yoloworld.json")
    args = ap.parse_args()

    vdir, ldir = Path(args.videos_dir), Path(args.logos_dir)
    model = YOLOWorld("yolov8s-world.pt")
    model.set_classes(["logo", "emblem", "brand symbol", "watermark", "logo mark"])
    matcher = LogoMatcher("siglip2")
    print(f"YOLO-World + SigLIP2(device={matcher.device})", flush=True)

    results = []
    for sub, logo in PAIRS:
        video = next((p for p in sorted(vdir.iterdir())
                      if sub in p.name and p.suffix.lower() in {".mp4", ".avi", ".mov", ".webm"}), None)
        qpath = ldir / logo
        if video is None or not qpath.is_file():
            continue
        qv = matcher.embed(qpath)
        work = Path(tempfile.mkdtemp(prefix="yw-"))
        best, found, dets = 0.0, 0, 0
        try:
            for fp in ffmpeg_frames(video, work, args.fps):
                res = model.predict(source=str(fp), conf=args.conf, verbose=False, device=0, max_det=10)
                boxes = res[0].boxes.xyxy.cpu().numpy().tolist() if len(res) else []
                dets += len(boxes)
                if not boxes:
                    continue
                image = load_rgb(fp)
                frame_best = 0.0
                for b in boxes:
                    cos = _cos(matcher.embed(crop(image, [float(x) for x in b])), qv)
                    frame_best = max(frame_best, cos)
                best = max(best, frame_best)
                if frame_best >= args.min_cosine:
                    found += 1
        finally:
            shutil.rmtree(work, ignore_errors=True)
        row = {"video": video.name, "logo": logo, "detector": "yolo-world",
               "detections": dets, "found_frames": found, "best_cosine": round(best, 4)}
        results.append(row)
        print(f"{video.name[:34]:<34} 탐지 {dets}개 · best_cos={row['best_cosine']} · 발견 {found}", flush=True)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"저장: {args.out}", flush=True)


if __name__ == "__main__":
    main()
