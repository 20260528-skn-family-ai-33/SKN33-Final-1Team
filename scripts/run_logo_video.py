#!/usr/bin/env python3
"""동영상에서 로고를 찾는다. 두 가지 방식:

1) guided (권장, --query-logo 지정):
   OWLv2 텍스트 탐지로 프레임마다 '로고 같은' 영역을 제안하고, 각 영역을 올린 기준 로고와
   1:1 코사인으로 대조해 '이 로고가 영상 어디·언제 나오나'를 본다.
   찾을 로고를 알 때(실제 검수: "이 영상에 OO 로고가 들어갔나")의 정답 경로.
   (OWLv2 image-guided 탐지는 노이즈가 많아 쓰지 않는다.)

2) blind (--query-logo 없음):
   OWLv2 텍스트 탐지로 '로고 같은' 영역을 찾고, 142개 기준 로고와 대조해 식별을 시도한다.
   찾을 로고가 기준셋 안에 있을 때만 쓸모. 유사 로고가 많으면 변별이 약하다.

흐름: 영상 → ffmpeg로 일정 간격(기본 1초) 프레임 추출 → (위 방식) → 프레임별 결과 + 요약.

사용법 (Pod 터미널, 모델 준비 후):
  python scripts/run_logo_video.py --video clip.avi --query-logo 로고.png          # guided
  python scripts/run_logo_video.py --video clip.avi --query-logo 로고.png --min-cosine 0.7 --fps 2
  python scripts/run_logo_video.py --video clip.mp4                                # blind (142 식별)
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def resolve_ffmpeg() -> str:
    cand = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if cand:
        return cand
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("[중단] ffmpeg를 찾을 수 없습니다. 설치하거나 FFMPEG_BIN을 지정하세요.")


def extract_frames(ffmpeg: str, video: Path, out_dir: Path, fps: float) -> list[tuple[float, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-vf", f"fps={fps}", "-q:v", "2", str(out_dir / "f%06d.jpg")],
                   check=True, stdin=subprocess.DEVNULL)
    frames = sorted(out_dir.glob("f*.jpg"))
    return [(round(i / fps, 2), path) for i, path in enumerate(frames)]


def _cosine(a, b) -> float:
    return round(sum(x * y for x, y in zip(a, b)), 4)


def embed_boxes(matcher, frame: Path, boxes) -> list:
    """프레임의 탐지 상자들을 크롭해 한 번의 배치로 임베딩한다(상자마다 GPU 호출하지 않도록)."""
    from backend.app.logo_pipeline import crop, load_rgb
    if not boxes:
        return []
    image = load_rgb(frame)  # 상자마다 파일을 다시 열지 않는다
    return matcher.embed_many([crop(image, b.xyxy) for b in boxes])


def main() -> None:
    from backend.app.logo_pipeline import LogoDetector, LogoMatcher, ReferenceIndex

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, help="입력 동영상(mp4/mov/webm/avi)")
    parser.add_argument("--query-logo", default=None, help="찾을 기준 로고(지정 시 guided 모드)")
    parser.add_argument("--fps", type=float, default=1.0, help="초당 샘플 프레임 수(기본 1)")
    parser.add_argument("--detector", default="owlv2")
    parser.add_argument("--matcher", default="dinov2")
    parser.add_argument("--index", default=None, help="blind 식별에 쓸 인덱스 이름(기본=matcher)")
    parser.add_argument("--det-threshold", type=float, default=0.1, help="OWLv2 텍스트 탐지 임계값")
    parser.add_argument("--min-cosine", type=float, default=0.6,
                        help="발견/식별로 셀 코사인 하한(guided: 쿼리 대조, blind: 142 대조)")
    parser.add_argument("--max-boxes", type=int, default=10, help="프레임당 상위 탐지 수")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    video = Path(args.video)
    if not video.is_file():
        sys.exit(f"[중단] 동영상을 찾을 수 없습니다: {video}")
    query = Path(args.query_logo) if args.query_logo else None
    if query and not query.is_file():
        sys.exit(f"[중단] 쿼리 로고를 찾을 수 없습니다: {query}")

    ffmpeg = resolve_ffmpeg()
    work = Path(tempfile.mkdtemp(prefix="logo-video-"))
    try:
        print(f"프레임 추출({args.fps}fps)…", flush=True)
        frames = extract_frames(ffmpeg, video, work / "frames", args.fps)
        if not frames:
            sys.exit("[중단] 프레임을 추출하지 못했습니다. 동영상을 확인하세요.")
        print(f"프레임 {len(frames)}장. {'guided' if query else 'blind'} 검수 시작…", flush=True)

        detector = LogoDetector(args.detector)
        matcher = LogoMatcher(args.matcher)
        per_frame = []

        if query:  # ---- guided: OWLv2로 로고 영역 제안 → 쿼리 로고와 1:1 코사인 대조 ----
            # OWLv2 image-guided는 노이즈가 많아, 텍스트 탐지로 '로고 같은' 영역을 제안하고
            # 각 영역을 올린 쿼리 로고와 직접 비교한다(1:1이라 1:142보다 변별이 쉽다).
            query_vec = matcher.embed(query)
            found_times, best_cos = [], 0.0
            for time_s, path in frames:
                boxes = detector.text_detect(path, threshold=args.det_threshold, max_boxes=args.max_boxes)
                vecs = embed_boxes(matcher, path, boxes)
                scored = sorted(
                    ({"xyxy": b.xyxy, "detect_score": b.score, "cosine": _cosine(v, query_vec)}
                     for b, v in zip(boxes, vecs)),
                    key=lambda d: d["cosine"], reverse=True)
                hits = [d for d in scored if d["cosine"] >= args.min_cosine]
                top = round(scored[0]["cosine"], 4) if scored else 0.0
                if hits:
                    found_times.append(time_s)
                    best_cos = max(best_cos, hits[0]["cosine"])
                per_frame.append({"time_s": time_s, "top_cosine": top,
                                  "found": len(hits), "detections": scored[:3]})
                print(f"  t={time_s:>6}s  영역 {len(boxes)}개 · 최고 코사인 {top}"
                      + (f"  ← 발견 {len(hits)}" if hits else ""), flush=True)
            payload = {"video": str(video), "mode": "guided", "query_logo": str(query),
                       "fps": args.fps, "frames": len(frames), "device": matcher.device,
                       "min_cosine": args.min_cosine,
                       "found_frames": len(found_times), "found_times": found_times,
                       "best_cosine": round(best_cos, 4), "per_frame": per_frame}
            summary_line = (f"\n=== 요약 ===\n  '{query.name}' 로고: {len(frames)}장 중 "
                            f"{len(found_times)}장에서 발견(코사인≥{args.min_cosine}), 최고 {round(best_cos, 4)}\n"
                            f"  발견 시각(초): {found_times}")
        else:  # ---- blind: 기준 인덱스와 대조 식별 ----
            index = ReferenceIndex(args.matcher, index_name=args.index)
            summary = {}
            for time_s, path in frames:
                boxes = detector.text_detect(path, threshold=args.det_threshold, max_boxes=args.max_boxes)
                hits = []
                for b, vec in zip(boxes, embed_boxes(matcher, path, boxes)):
                    best = index.nearest(vec, topk=1)[0]
                    hits.append({"xyxy": b.xyxy, "detect_score": b.score,
                                 "brand": best["brand"], "cosine": best["cosine"]})
                    if best["cosine"] >= args.min_cosine:
                        agg = summary.setdefault(best["brand"], {"count": 0, "best_cosine": 0.0, "times": []})
                        agg["count"] += 1
                        agg["best_cosine"] = max(agg["best_cosine"], best["cosine"])
                        if time_s not in agg["times"]:
                            agg["times"].append(time_s)
                per_frame.append({"time_s": time_s, "detections": hits})
                print(f"  t={time_s:>6}s  탐지 {len(boxes)}개", flush=True)
            ranked = sorted(summary.items(), key=lambda kv: kv[1]["best_cosine"], reverse=True)
            payload = {"video": str(video), "mode": "blind", "fps": args.fps, "frames": len(frames),
                       "device": matcher.device, "min_cosine": args.min_cosine,
                       "summary": [{"brand": b, **v} for b, v in ranked], "per_frame": per_frame}
            summary_line = "\n=== 식별 요약(코사인 높은 순) ===\n" + (
                "\n".join(f"  {b}  best={v['best_cosine']}  횟수={v['count']}  시각={v['times']}"
                          for b, v in ranked)
                if ranked else f"  (min-cosine {args.min_cosine} 이상 식별 없음)")

        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"저장: {args.out}")
        print(summary_line)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
