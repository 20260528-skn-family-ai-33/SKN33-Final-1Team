#!/usr/bin/env python3
"""로고 guided 검수(운영) — 보고서 「로고 검수 모델 비교」(2026-10-26) 결론 파이프라인.

지정 로고 1장을 쿼리로, 영상·이미지·프레임에서 OWLv2 탐지 → CLIP/SigLIP2 1차 대조 →
애매 구간만 LightGlue 2차 이중검수 → 확실/확인필요/미검출로 판정한다.

추론 위치는 RUN_TARGET이 정한다:
  RUN_TARGET=local  (기본, Pod 안)  이 프로세스에서 모델을 올려 돌린다.
  RUN_TARGET=runpod                 프레임만 LOGO_RUNPOD_ENDPOINT_ID로 보낸다(서버 배포 형태).

사용법 (Pod 터미널, 모델 준비 후: python scripts/prepare_logo_models.py --combo A):
  python scripts/run_logo_guided.py --video clip.mp4 --query-logo 로고.png
  python scripts/run_logo_guided.py --video clip.mp4 --query-logo 로고.png --matcher siglip2 --fps 2
  python scripts/run_logo_guided.py --image frame.jpg --query-logo 로고.png --no-verify
  python scripts/run_logo_guided.py --frames-dir .runtime/video-reviews/<id>/frames --query-logo 로고.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _frame_paths(frames_dir: Path) -> list[Path]:
    return sorted(p for p in frames_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})


def _overrides(args) -> dict:
    names = ("cosine_certain", "cosine_present", "lightglue_min_matches", "det_threshold", "max_boxes")
    return {name: getattr(args, name) for name in names if getattr(args, name) is not None}


def main() -> None:
    from backend.app.logo_review import VERDICT_LABELS, summarize
    from backend.app.logo_runtime import review_frames, review_video

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--video", help="입력 동영상(mp4/mov/webm/avi)")
    source.add_argument("--image", help="대상 이미지 1장")
    source.add_argument("--frames-dir", help="프레임 폴더(일괄)")
    parser.add_argument("--query-logo", required=True, help="검사할 지정 로고 1장(쿼리)")
    parser.add_argument("--matcher", default="clip", choices=["clip", "siglip2"], help="1차 대조 모델(로컬 모드만)")
    parser.add_argument("--no-verify", action="store_true", help="LightGlue 이중검수 끄기(조합 A)")
    parser.add_argument("--fps", type=float, default=1.0, help="영상 샘플 프레임 수/초")
    parser.add_argument("--cosine-certain", type=float, default=None, help="확실 임계값(기본 0.85)")
    parser.add_argument("--cosine-present", type=float, default=None, help="애매 하한(기본 0.60)")
    parser.add_argument("--lightglue-min-matches", type=int, default=None, help="이중검수 통과 매칭 수(기본 20)")
    parser.add_argument("--det-threshold", type=float, default=None, help="OWLv2 탐지 임계값(기본 0.10)")
    parser.add_argument("--max-boxes", type=int, default=None, help="프레임당 상위 탐지 수(기본 10)")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    query = Path(args.query_logo)
    if not query.is_file():
        sys.exit(f"[중단] 쿼리 로고를 찾을 수 없습니다: {query}")
    verify = not args.no_verify
    overrides = _overrides(args)

    if args.video:
        payload = review_video(args.video, query, fps=args.fps, matcher=args.matcher,
                               verify=verify, overrides=overrides)
    else:
        if args.image:
            paths = [Path(args.image)]
        else:
            paths = _frame_paths(Path(args.frames_dir))
        if not paths:
            sys.exit("[중단] 처리할 이미지가 없습니다.")
        reviews = review_frames(paths, query, matcher=args.matcher, verify=verify, overrides=overrides)
        per_frame = [{**review, "frame": str(path), "time_s": index}
                     for index, (path, review) in enumerate(zip(paths, reviews))]
        payload = {"source": args.image or args.frames_dir, "query_logo": str(query),
                   "summary": summarize(per_frame), "per_frame": per_frame}

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"저장: {args.out}")
    summary = payload["summary"]
    verdict = summary["verdict"]
    print(f"\n=== 판정: {VERDICT_LABELS[verdict]} ({verdict}) ===")
    print(f"  '{query.name}'  최고 코사인 {summary['best_cosine']}  "
          f"확실 {summary['detected_frames']}프레임 · 확인필요 {summary['review_frames']}프레임")
    if summary.get("detected_times"):
        print(f"  확실 시각(초): {summary['detected_times']}")
    if summary.get("review_times"):
        print(f"  확인필요 시각(초): {summary['review_times']}")


if __name__ == "__main__":
    main()
