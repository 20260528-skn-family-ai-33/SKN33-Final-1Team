#!/usr/bin/env python3
"""영상 폴더 일괄 검수 — blind(기준셋 N개 중 식별) + guided(영상별 지정 로고 1:1)를 함께.

프레임마다 OWLv2 탐지를 '한 번'만 하고, 그 크롭 임베딩으로 두 가지를 동시에 계산한다:
  - blind : 크롭을 기준 인덱스(videoset)와 대조해 가장 가까운 로고(식별)
  - guided: 크롭을 '이 영상에 들어가야 할 로고'(PAIRING)와 1:1 대조(검증)
영상에 맞는 로고를 못 찾으면 guided는 건너뛴다(blind만).

사용법:
  python scripts/review_batch.py --matcher siglip2 --index videoset_siglip2 \
      --videos-dir /workspace/videos --logos-dir /workspace/video_logos --out-dir /workspace/batch_siglip2
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

# 영상 파일명 부분문자열 → /workspace/video_logos 의 로고 파일(검증용 쿼리).
PAIRING = {
    "공공누리": "한국문화정보원.png",
    "한국문화정보원": "한국문화정보원.png",
    "마약이 온다": "서울마이소울 로고.png",
    "밤이차오른다": "서울마이소울 로고.png",
    "서울달": "서울마이소울 로고.png",
    "22초": "서울마이소울 로고.png",
    "공연봄날": "서울마이소울 로고.png",
    "2차팝업": "서울마이소울 로고.png",
    "서울시향": "서울시립교향악당.png",
    "서울문화재단": "서울문화재단 로고.png",
    "서울어텀": "서울문화재단 로고.png",
    "한강페스티벌": "한강페스티벌 로고.jpg",
    "상주": "상주시 로고.jpg",
    "청양": "청양군 로고.png",
    "대전": "대전광역시.jpg",
    "성안이즈백": "청주시 로고.jpg",
    "DUR": "한국의약품안전관리원 로고.png",
    "먹는 약": "한국의약품안전관리원 로고.png",
    "감염병": "보건복지부.png",
    "한국민속예술제": "세계유교문화재단 로고.png",
}


def resolve_ffmpeg() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def extract_frames(ffmpeg, video, out_dir, fps):
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps}", "-q:v", "2", str(out_dir / "f%06d.jpg")],
                   check=True, stdin=subprocess.DEVNULL)
    frames = sorted(out_dir.glob("f*.jpg"))
    return [(round(i / fps, 2), p) for i, p in enumerate(frames)]


def find_query(video_name, logos_dir):
    for key, fname in PAIRING.items():
        if key in video_name:
            p = logos_dir / fname
            if p.is_file():
                return p
    return None


def main():
    from backend.app.logo_pipeline import LogoDetector, LogoMatcher, ReferenceIndex, crop, load_rgb

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matcher", default="siglip2")
    ap.add_argument("--index", required=True, help="blind 기준 인덱스 이름(videoset_siglip2 등)")
    ap.add_argument("--videos-dir", required=True)
    ap.add_argument("--logos-dir", default="/workspace/video_logos")
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--det-threshold", type=float, default=0.1)
    ap.add_argument("--min-cosine", type=float, default=0.55)
    ap.add_argument("--max-boxes", type=int, default=10)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    ffmpeg = resolve_ffmpeg()
    videos_dir, logos_dir, out_dir = Path(args.videos_dir), Path(args.logos_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = sorted(p for p in videos_dir.iterdir()
                    if p.suffix.lower() in {".mp4", ".avi", ".mov", ".webm"})

    detector = LogoDetector("owlv2")
    matcher = LogoMatcher(args.matcher)
    index = ReferenceIndex(args.matcher, index_name=args.index)
    print(f"[{args.matcher}] 영상 {len(videos)}편 검수 시작 (blind index={args.index})", flush=True)

    for video in videos:
        query_path = find_query(video.name, logos_dir)
        query_vec = matcher.embed(query_path) if query_path else None
        work = Path(tempfile.mkdtemp(prefix="rev-"))
        try:
            frames = extract_frames(ffmpeg, video, work, args.fps)
            blind = {}
            g_found, g_best = [], 0.0
            for t, fp in frames:
                boxes = detector.text_detect(fp, threshold=args.det_threshold, max_boxes=args.max_boxes)
                if not boxes:
                    continue
                image = load_rgb(fp)
                vecs = matcher.embed_many([crop(image, b.xyxy) for b in boxes])
                for v in vecs:
                    best = index.nearest(v, topk=1)[0]
                    if best["cosine"] >= args.min_cosine:
                        agg = blind.setdefault(best["brand"], {"count": 0, "best": 0.0, "times": []})
                        agg["count"] += 1
                        agg["best"] = max(agg["best"], best["cosine"])
                        if t not in agg["times"]:
                            agg["times"].append(t)
                if query_vec is not None:
                    cos = max(round(sum(x * y for x, y in zip(v, query_vec)), 4) for v in vecs)
                    g_best = max(g_best, cos)
                    if cos >= args.min_cosine:
                        g_found.append(t)
            ranked = sorted(blind.items(), key=lambda kv: kv[1]["best"], reverse=True)
            result = {
                "video": video.name, "matcher": args.matcher, "frames": len(frames),
                "blind_top": [{"brand": b, **v} for b, v in ranked[:5]],
                "guided_query": query_path.name if query_path else None,
                "guided_best": round(g_best, 4), "guided_found": len(g_found), "guided_times": g_found,
            }
            (out_dir / (video.stem + ".json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            top = ranked[0] if ranked else None
            print(f"[{args.matcher}] {video.name[:40]:<40} "
                  f"blind1={top[0]+' '+str(top[1]['best']) if top else '-'} | "
                  f"guided({query_path.name if query_path else '없음'})="
                  f"{result['guided_best']}×{result['guided_found']}", flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print(f"[{args.matcher}] 완료 → {out_dir}", flush=True)


if __name__ == "__main__":
    main()
