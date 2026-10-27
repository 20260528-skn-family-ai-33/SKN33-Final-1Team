#!/usr/bin/env python3
"""조합 D — OWLv2+SigLIP2 자동 라벨링 → YOLO11 학습.

1) 자동 라벨: 영상마다 '지정 로고'로 OWLv2 탐지 + SigLIP2 코사인≥임계 영역을 그 브랜드 박스로 라벨.
   (blind 식별은 노이즈라 쓰지 않고, 영상별로 아는 로고로만 라벨 → 깨끗함.)
2) YOLO 데이터셋(images/labels, train/val) 구성 + data.yaml.
3) YOLO11n 학습 + 검증(mAP).

사용: python scripts/train_yolo_logos.py --fps 1.0 --label-cosine 0.75 --epochs 40 --out /workspace/yolo_ds
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

# 영상 부분문자열 → 지정 로고(= 클래스). 약한 매칭(상주·성안)은 라벨 품질 위해 제외.
PAIRS = [
    ("공공누리", "한국문화정보원"),
    ("한국문화정보원_공공저작물", "한국문화정보원"),
    ("마약이 온다", "서울마이소울"),
    ("밤이차오른다", "서울마이소울"),
    ("22초", "서울마이소울"),
    ("공연봄날", "서울마이소울"),
    ("2차팝업", "서울마이소울"),
    ("낭만과학도시 대전", "대전광역시"),
    ("서울시향", "서울시립교향악당"),
    ("서울문화재단", "서울문화재단"),
    ("한강페스티벌", "한강페스티벌"),
    ("청양", "청양군"),
    ("해맞이", "JCI"),
    ("이몽룡", "봉화군"),
    ("감염병", "보건복지부"),
    ("한국민속예술제", "세계유교문화재단"),
]
LOGO_FILE = {  # 클래스 → 로고 파일명(/workspace/video_logos)
    "한국문화정보원": "한국문화정보원.png", "서울마이소울": "서울마이소울 로고.png",
    "대전광역시": "대전광역시.jpg", "서울시립교향악당": "서울시립교향악당.png",
    "서울문화재단": "서울문화재단 로고.png", "한강페스티벌": "한강페스티벌 로고.jpg",
    "청양군": "청양군 로고.png", "JCI": "JCI 로고.png", "봉화군": "봉화군 로고.png",
    "보건복지부": "보건복지부.png", "세계유교문화재단": "세계유교문화재단 로고.png",
}
CLASSES = sorted(set(LOGO_FILE))


def ffmpeg_frames(video, out_dir, fps):
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps}", "-q:v", "2", str(out_dir / "f%06d.jpg")],
                   check=True, stdin=subprocess.DEVNULL)
    return sorted(out_dir.glob("f*.jpg"))


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def autolabel(args):
    from backend.app.logo_pipeline import LogoDetector, LogoMatcher, crop, load_rgb
    from PIL import Image

    vdir, ldir = Path(args.videos_dir), Path(args.logos_dir)
    ds = Path(args.out)
    for s in ("images/train", "images/val", "labels/train", "labels/val"):
        (ds / s).mkdir(parents=True, exist_ok=True)
    cls_id = {c: i for i, c in enumerate(CLASSES)}

    detector = LogoDetector("owlv2")
    matcher = LogoMatcher("siglip2")
    qvec = {c: matcher.embed(ldir / LOGO_FILE[c]) for c in CLASSES}
    print(f"자동 라벨링: 클래스 {len(CLASSES)}개, device={matcher.device}", flush=True)

    kept, idx = 0, 0
    for sub, brand in PAIRS:
        video = next((p for p in sorted(vdir.iterdir())
                      if sub in p.name and p.suffix.lower() in {".mp4", ".avi", ".mov", ".webm"}), None)
        if video is None or brand not in cls_id:
            continue
        work = Path(tempfile.mkdtemp(prefix="al-"))
        qv = qvec[brand]
        try:
            for fp in ffmpeg_frames(video, work, args.fps):
                boxes = detector.text_detect(fp, threshold=args.det_threshold, max_boxes=args.max_boxes)
                if not boxes:
                    continue
                image = load_rgb(fp)
                W, H = image.size
                lines = []
                for b in boxes:
                    if _cos(matcher.embed(crop(image, b.xyxy)), qv) >= args.label_cosine:
                        x0, y0, x1, y1 = b.xyxy
                        x0, y0 = max(0, x0), max(0, y0)
                        x1, y1 = min(W, x1), min(H, y1)
                        if x1 - x0 < 4 or y1 - y0 < 4:
                            continue
                        cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
                        bw, bh = (x1 - x0) / W, (y1 - y0) / H
                        lines.append(f"{cls_id[brand]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                if lines:
                    idx += 1
                    split = "val" if idx % 6 == 0 else "train"
                    name = f"img{idx:05d}"
                    image.save(ds / "images" / split / f"{name}.jpg", quality=90)
                    (ds / "labels" / split / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                    kept += 1
        finally:
            shutil.rmtree(work, ignore_errors=True)
        print(f"  {sub[:24]:<24} → {brand}", flush=True)

    (ds / "data.yaml").write_text(
        f"path: {ds}\ntrain: images/train\nval: images/val\nnc: {len(CLASSES)}\n"
        f"names: {json.dumps(CLASSES, ensure_ascii=False)}\n", encoding="utf-8")
    ntr = len(list((ds / "images/train").glob("*.jpg")))
    nval = len(list((ds / "images/val").glob("*.jpg")))
    print(f"라벨 프레임 {kept}장 (train {ntr} · val {nval}), 클래스 {len(CLASSES)}", flush=True)
    return ntr, nval


def train(args):
    from ultralytics import YOLO
    ds = Path(args.out)
    model = YOLO("yolo11n.pt")
    model.train(data=str(ds / "data.yaml"), epochs=args.epochs, imgsz=640, batch=16,
                project="/workspace/yolo_runs", name="logos", exist_ok=True, verbose=False)
    metrics = model.val(data=str(ds / "data.yaml"), project="/workspace/yolo_runs",
                        name="logos_val", exist_ok=True, verbose=False)
    print("== YOLO11 학습 결과 ==", flush=True)
    print(f"  mAP50-95 = {round(float(metrics.box.map), 4)}", flush=True)
    print(f"  mAP50    = {round(float(metrics.box.map50), 4)}", flush=True)
    print(f"  precision= {round(float(metrics.box.mp), 4)} · recall = {round(float(metrics.box.mr), 4)}", flush=True)
    (Path("/workspace") / "yolo_result.json").write_text(json.dumps({
        "map50_95": float(metrics.box.map), "map50": float(metrics.box.map50),
        "precision": float(metrics.box.mp), "recall": float(metrics.box.mr),
        "classes": CLASSES, "epochs": args.epochs}, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos-dir", default="/workspace/videos")
    ap.add_argument("--logos-dir", default="/workspace/video_logos")
    ap.add_argument("--out", default="/workspace/yolo_ds")
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--det-threshold", type=float, default=0.1)
    ap.add_argument("--max-boxes", type=int, default=8)
    ap.add_argument("--label-cosine", type=float, default=0.75)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--skip-label", action="store_true")
    args = ap.parse_args()

    if not args.skip_label:
        ntr, nval = autolabel(args)
        if ntr < 10 or nval < 2:
            sys.exit(f"[중단] 라벨 프레임이 너무 적습니다(train {ntr}, val {nval}). 임계값을 낮추세요.")
    train(args)


if __name__ == "__main__":
    main()
