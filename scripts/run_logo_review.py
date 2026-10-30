#!/usr/bin/env python3
"""로고 검수 1장 실행(조합 A). 영상 프레임(또는 포스터) 안의 로고를 찾고 대조한다.

두 가지 방식:
  1) 특정 로고 찾기 (--query-logo 지정):
     올린 기준 로고를 OWLv2로 대상 이미지에서 찾고(위치), 각 상자를 크롭해 DINO로 '정말 같은지' 확인한다.
     → 발주처가 "이 로고가 들어갔는지" 확인하는 실제 흐름.
  2) 어느 브랜드인지 식별 (--query-logo 없음):
     대상 이미지 전체를 DINO로 임베딩해 142개 기준 로고 중 가장 가까운 브랜드를 돌려준다(위치 없음).

사용법 (Pod 터미널, 모델·임베딩 준비 후):
  python scripts/run_logo_review.py --image frame.jpg --query-logo data/logos/regions_logos/lg__경기도__고양시/logo.png
  python scripts/run_logo_review.py --image frame.jpg            # 브랜드 식별
  python scripts/run_logo_review.py --frames-dir .runtime/video-reviews/<id>/frames --query-logo <로고.png>

결과는 JSON 으로 stdout에 출력한다(--out 으로 파일 저장 가능).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def iter_images(image: str | None, frames_dir: str | None) -> list[Path]:
    if image:
        return [Path(image)]
    if frames_dir:
        directory = Path(frames_dir)
        return sorted(p for p in directory.iterdir()
                      if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    return []


def review_image(path: Path, detector, matcher, index, query_logo, threshold: float,
                 query_vec=None) -> dict:
    from backend.app.logo_pipeline import crop

    if query_logo is not None:
        boxes = detector.guided_detect(path, query_logo, threshold=threshold)
        vecs = matcher.embed_many([crop(path, box.xyxy) for box in boxes]) if boxes else []
        found = []
        for box, vec in zip(boxes, vecs):
            best = index.nearest(vec, topk=1)[0] if index else None
            # query_cosine: 올린 쿼리 로고와의 1:1 동일성(정말 같은 로고인지)
            qcos = round(sum(x * y for x, y in zip(vec, query_vec)), 4) if query_vec else None
            found.append({"xyxy": box.xyxy, "detect_score": box.score,
                          "query_cosine": qcos, "match": best})
        return {"image": str(path), "mode": "guided", "boxes": found}

    # 브랜드 식별(위치 없음)
    top = index.nearest(matcher.embed(path), topk=5) if index else []
    return {"image": str(path), "mode": "identify", "top": top}


def main() -> None:
    from backend.app.logo_pipeline import LogoDetector, LogoMatcher, ReferenceIndex

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="대상 이미지 1장")
    source.add_argument("--frames-dir", help="프레임 폴더(일괄)")
    parser.add_argument("--query-logo", default=None, help="찾을 기준 로고 PNG(지정 시 위치 탐지)")
    parser.add_argument("--detector", default="owlv2")
    parser.add_argument("--matcher", default="dinov2")
    parser.add_argument("--threshold", type=float, default=0.9, help="OWLv2 탐지 임계값")
    parser.add_argument("--no-index", action="store_true", help="기준 임베딩 대조를 건너뜀")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    images = iter_images(args.image, args.frames_dir)
    if not images:
        sys.exit("[중단] 처리할 이미지가 없습니다.")

    matcher = LogoMatcher(args.matcher)
    index = None if args.no_index else ReferenceIndex(args.matcher)
    detector = LogoDetector(args.detector) if args.query_logo else None
    query = Path(args.query_logo) if args.query_logo else None

    query_vec = matcher.embed(query) if query else None
    results = [review_image(path, detector, matcher, index, query, args.threshold, query_vec)
               for path in images]
    payload = {"detector": args.detector, "matcher": args.matcher,
               "device": matcher.device, "count": len(results), "results": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"저장: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
