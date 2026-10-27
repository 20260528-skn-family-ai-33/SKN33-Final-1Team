#!/usr/bin/env python3
"""로고 검수 후보 모델을 같은 동영상·이미지로 한 번에 돌려 비교용 결과 JSON을 만든다.

「로고검수 모델 성능검토 계획」의 모델별 성능 테스트용 하네스. 공정한 비교를 위해:
  - 프레임은 한 번만 추출하고, 탐지(OWLv2 텍스트 탐지) 영역도 한 번만 구해 모든 매처가 같은 후보를 쓴다.
  - 매처(dinov2·dinov3·siglip2·clip …)만 바꿔 가며 같은 지표를 잰다. 조합 A(owlv2+dinov2)도 여기서 다시 잰다.

측정 항목(매처별):
  video    : 발견 프레임 수·시각, 최고/평균 코사인, 분리도(발견 영역 코사인 − 나머지 영역 코사인 중앙값), 지연
  images   : 이미지별 최고 코사인·발견 여부
  identify : 쿼리 로고 → 기준 142개 최근접. 정답 브랜드 순위·Top-1·1·2위 격차(--expect-brand 지정 시)
  cost     : 모델 로드 시간, 프레임당 임베딩 시간, GPU 최대 메모리

준비 안 된 매처(manifest 없음, 게이트 모델 등)는 건너뛰고 이유를 결과에 적는다.

사용법 (Pod 터미널, 모델 준비 후):
  python scripts/prepare_logo_models.py --models owlv2,dinov2,dinov3,siglip2,clip
  python scripts/bench_logo_models.py --video clip.mp4 --image poster.jpg \\
      --query-logo data/logos/regions_logos/lg__서울특별시/서울시_로고.png --expect-brand lg__서울특별시
  python scripts/report_logo_bench.py            # 최신 결과 → docs/logo-model-benchmark.md
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import statistics
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import logo_models as reg  # noqa: E402

BENCH_ROOT = ROOT / ".runtime" / "logo-bench"
DEFAULT_MATCHERS = [k for k, s in reg.REGISTRY.items() if s.role == "match"]


def _dot(a, b) -> float:
    return round(sum(x * y for x, y in zip(a, b)), 4)


def _sync(device: str) -> None:
    if device == "cuda":
        import torch
        torch.cuda.synchronize()


def _gpu_reset(device: str) -> None:
    if device == "cuda":
        import torch
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def _gpu_peak_mb(device: str):
    if device != "cuda":
        return None
    import torch
    return round(torch.cuda.max_memory_allocated() / 2**20, 1)


def detect_all(detector, sources: list[tuple[str, Path]], threshold: float, max_boxes: int) -> dict:
    """모든 프레임·이미지의 후보 영역을 한 번만 구한다. {source_id: {"boxes": [...], "ms": float}}"""
    out = {}
    for sid, path in sources:
        start = time.perf_counter()
        boxes = detector.text_detect(path, threshold=threshold, max_boxes=max_boxes)
        _sync(detector.device)
        out[sid] = {"boxes": boxes, "ms": (time.perf_counter() - start) * 1000}
    return out


def crops_for(sources: list[tuple[str, Path]], detections: dict) -> dict:
    """후보 영역 크롭을 미리 만들어 모든 매처가 같은 픽셀을 쓰게 한다."""
    from backend.app.logo_pipeline import crop, load_rgb
    out = {}
    for sid, path in sources:
        boxes = detections[sid]["boxes"]
        image = load_rgb(path) if boxes else None
        out[sid] = [crop(image, b.xyxy) for b in boxes]
    return out


def build_index(matcher, seed_dir: Path, batch: int = 16):
    """기준 로고 임베딩. 선계산 파일이 있으면 읽고, 없으면 즉석 계산한다(매처별)."""
    import numpy as np
    directory = reg.index_dir(matcher.key)
    if (directory / "embeddings.npy").is_file() and (directory / "brands.json").is_file():
        meta = json.loads((directory / "brands.json").read_text(encoding="utf-8"))
        return np.load(directory / "embeddings.npy"), meta["brands"]
    pairs = [(p.parent.name, p) for p in sorted(seed_dir.glob("*/logo.png"))]
    vectors = []
    for start in range(0, len(pairs), batch):
        vectors.extend(matcher.embed_many([p for _, p in pairs[start:start + batch]]))
    return np.asarray(vectors, dtype=np.float32), [b for b, _ in pairs]


def identify(matrix, brands, query_vec, expect: str | None) -> dict:
    import numpy as np
    scores = matrix @ np.asarray(query_vec, dtype=matrix.dtype)
    order = np.argsort(-scores)
    top = [{"brand": brands[i], "cosine": round(float(scores[i]), 4)} for i in order[:5]]
    result = {"top5": top, "top1_brand": top[0]["brand"],
              "margin_top1_top2": round(top[0]["cosine"] - top[1]["cosine"], 4) if len(top) > 1 else None}
    if expect:
        if expect in brands:
            rank = int(np.where(order == brands.index(expect))[0][0]) + 1
            result.update({"expect_brand": expect, "expect_rank": rank, "top1_correct": rank == 1,
                           "expect_cosine": round(float(scores[brands.index(expect)]), 4)})
        else:
            result.update({"expect_brand": expect, "expect_rank": None,
                           "note": "기준셋에 없는 브랜드"})
    return result


def calibrated_threshold(matrix, brands, query_vec, expect: str | None) -> float:
    """매처별 '다른 로고' 상한: 쿼리와 정답이 아닌 기준 로고들 사이의 최대 코사인.
    매처마다 코사인 분포가 달라 고정 기준(0.55 등)은 모델 간에 공정하지 않으므로,
    '알려진 다른 어떤 로고보다도 쿼리와 더 닮았다'를 발견 조건으로 쓰기 위한 값."""
    import numpy as np
    scores = matrix @ np.asarray(query_vec, dtype=matrix.dtype)
    negatives = [float(s) for s, b in zip(scores, brands) if b != expect]
    return round(max(negatives), 4)


def video_stats(name: str, frames: list[tuple], ms: list[float], min_cos: float, calib: float) -> dict:
    """frames = [(time_s, [영역 코사인...]), ...] → 동영상 지표.
    분리도 = 발견 영역 평균 코사인 − 나머지 영역 코사인 중앙값(고정 기준)."""
    tops = [max(c) if c else 0.0 for _, c in frames]
    hit = [x for _, c in frames for x in c if x >= min_cos]
    rest = [x for _, c in frames for x in c if x < min_cos]
    times = [t for t, c in frames if any(x >= min_cos for x in c)]
    calib_times = [t for t, c in frames if any(x > calib for x in c)]
    median_rest = statistics.median(rest) if rest else None
    n = len(frames)
    return {
        "video": name, "frames": n, "found_frames": len(times), "found_times": times,
        "found_ratio": round(len(times) / n, 4) if n else 0.0,
        "calib_found_frames": len(calib_times), "calib_found_times": calib_times,
        "best_cosine": round(max(tops), 4) if tops else 0.0,
        "best_margin_over_calib": round(max(tops) - calib, 4) if tops else None,
        "mean_top_cosine": round(statistics.fmean(tops), 4) if tops else 0.0,
        "median_other_cosine": round(median_rest, 4) if median_rest is not None else None,
        "separation": (round(statistics.fmean(hit) - median_rest, 4)
                       if hit and median_rest is not None else None),
        "embed_ms_per_frame": round(statistics.fmean(ms), 1) if ms else None,
    }


def bench_matcher(key: str, args, query: Path, frames, images, crops) -> dict:
    from backend.app.logo_pipeline import LogoMatcher

    spec = reg.spec(key)
    entry = {"matcher": key, "repo_id": spec.repo_id, "note": spec.note}
    if not reg.is_prepared(key):
        entry["skipped"] = (f"모델 미준비{'(게이트 모델: HF_TOKEN 필요)' if spec.gated else ''}. "
                            f"python scripts/prepare_logo_models.py --models {key}")
        return entry
    try:
        entry["resolved_commit"] = reg.read_manifest(key).get("resolved_commit")
    except (OSError, ValueError):
        pass

    device_hint = None
    t0 = time.perf_counter()
    try:
        matcher = LogoMatcher(key)
    except Exception as exc:  # transformers 버전 미지원 등
        entry["skipped"] = f"로드 실패: {type(exc).__name__}: {exc}"
        return entry
    device_hint = matcher.device
    _gpu_reset(device_hint)
    entry["device"] = matcher.device
    entry["load_s"] = round(time.perf_counter() - t0, 2)

    query_vec = matcher.embed(query)

    def score_source(sid):
        pieces = crops[sid]
        start = time.perf_counter()
        vecs = matcher.embed_many(pieces) if pieces else []
        _sync(matcher.device)
        ms = (time.perf_counter() - start) * 1000
        return [_dot(v, query_vec) for v in vecs], ms

    # ---- 식별(쿼리 로고 → 기준 142) + 매처별 보정 기준 ----
    matrix, brands = build_index(matcher, Path(args.seed_dir))
    entry["identify"] = identify(matrix, brands, query_vec, args.expect_brand)
    calib = calibrated_threshold(matrix, brands, query_vec, args.expect_brand)
    entry["calib_threshold"] = calib

    # ---- 동영상(guided: 쿼리 로고 1:1 대조) ----
    if frames:
        per_video: dict[str, dict] = {}
        for (sid, _), (video_name, time_s) in zip(frames, args._frame_meta):
            cosines, ms = score_source(sid)
            if args.dump_hits:  # 육안 확인용: 고정 기준 또는 보정 기준을 넘은 영역 크롭을 저장
                for i, c in enumerate(cosines):
                    if c >= args.min_cosine or c > calib:
                        target = Path(args.dump_hits) / key
                        target.mkdir(parents=True, exist_ok=True)
                        crops[sid][i].save(target / f"{Path(video_name).stem}_t{time_s}_b{i}_{c:.3f}.jpg")
            acc = per_video.setdefault(video_name, {"frames": [], "ms": []})
            acc["frames"].append((time_s, sorted(cosines, reverse=True)))
            acc["ms"].append(ms)
        entry["videos"] = [video_stats(name, acc["frames"], acc["ms"], args.min_cosine, calib)
                           for name, acc in per_video.items()]
        entry["video"] = video_stats("(전체)", [f for acc in per_video.values() for f in acc["frames"]],
                                     [m for acc in per_video.values() for m in acc["ms"]],
                                     args.min_cosine, calib)
        # 나중에 기준을 바꿔 다시 집계할 수 있도록 프레임별 영역 코사인을 남긴다.
        entry["frame_cosines"] = {name: [[t, [round(x, 4) for x in c]] for t, c in acc["frames"]]
                                  for name, acc in per_video.items()}

    # ---- 이미지(포스터·스틸컷) ----
    if images:
        rows = []
        for sid, path in images:
            cosines, ms = score_source(sid)
            best = max(cosines) if cosines else 0.0
            rows.append({"image": path.name, "boxes": len(cosines), "best_cosine": round(best, 4),
                         "found": best >= args.min_cosine, "calib_found": best > calib,
                         "embed_ms": round(ms, 1)})
        entry["images"] = rows

    entry["gpu_peak_mb"] = _gpu_peak_mb(matcher.device)

    del matcher
    _gpu_reset(device_hint)
    return entry


def main() -> None:
    from backend.app.logo_pipeline import LogoDetector

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", action="append", default=[], help="입력 동영상(여러 번 지정 가능)")
    parser.add_argument("--image", action="append", default=[], help="입력 이미지(여러 번 지정 가능)")
    parser.add_argument("--query-logo", required=True, help="찾을 기준 로고")
    parser.add_argument("--expect-brand", default=None, help="쿼리 로고의 정답 브랜드 폴더명(식별 채점용)")
    parser.add_argument("--matchers", default=",".join(DEFAULT_MATCHERS), help="비교할 매처 키(쉼표)")
    parser.add_argument("--detector", default="owlv2")
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--det-threshold", type=float, default=0.1)
    parser.add_argument("--min-cosine", type=float, default=0.6)
    parser.add_argument("--max-boxes", type=int, default=10)
    parser.add_argument("--max-frames", type=int, default=0, help="동영상당 최대 프레임(0=전부, 점검용)")
    parser.add_argument("--seed-dir", default=str(reg.LOGO_SEED_ROOT))
    parser.add_argument("--out-dir", default=None, help="결과 폴더(기본 .runtime/logo-bench/<시각>)")
    parser.add_argument("--dump-hits", default=None,
                        help="고정/보정 기준을 넘은 영역 크롭을 저장할 폴더(육안 확인용)")
    args = parser.parse_args()

    query = Path(args.query_logo)
    missing = [p for p in [query, *map(Path, args.video), *map(Path, args.image)] if not p.is_file()]
    if missing:
        sys.exit(f"[중단] 파일을 찾을 수 없습니다: {', '.join(map(str, missing))}")
    matchers = [k.strip() for k in args.matchers.split(",") if k.strip()]
    for k in matchers:
        if reg.spec(k).role != "match":
            sys.exit(f"[중단] {k} 는 match 모델이 아닙니다.")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else BENCH_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="logo-bench-"))
    try:
        from run_logo_video import extract_frames, resolve_ffmpeg  # 같은 폴더의 스크립트 재사용

        frames, frame_meta, video_meta = [], [], []
        if args.video:
            ffmpeg = resolve_ffmpeg()
            for vi, video in enumerate(map(Path, args.video)):
                got = extract_frames(ffmpeg, video, work / f"v{vi}", args.fps)
                if args.max_frames:
                    got = got[:args.max_frames]
                video_meta.append({"video": video.name, "frames": len(got)})
                for time_s, path in got:
                    frames.append((f"v{vi}:{time_s}", path))
                    frame_meta.append((video.name, time_s))
        args._frame_meta = frame_meta
        images = [(f"i{ii}", Path(p)) for ii, p in enumerate(args.image)]
        print(f"프레임 {len(frames)}장 · 이미지 {len(images)}장 · 매처 {matchers}", flush=True)

        detector_info, crops = {"detector": args.detector, "repo_id": reg.spec(args.detector).repo_id}, {}
        if frames or images:
            t0 = time.perf_counter()
            detector = LogoDetector(args.detector)
            det_load_s = round(time.perf_counter() - t0, 2)
            detections = detect_all(detector, frames + images, args.det_threshold, args.max_boxes)
            det_ms = [d["ms"] for d in detections.values()]
            detector_info.update({
                "device": detector.device, "load_s": det_load_s,
                "detect_ms_per_frame": round(statistics.fmean(det_ms), 1),
                "mean_boxes": round(statistics.fmean(len(d["boxes"]) for d in detections.values()), 2)})
            del detector
            crops = crops_for(frames + images, detections)
            print(f"탐지 완료: 평균 {detector_info['mean_boxes']}개/장, "
                  f"{detector_info['detect_ms_per_frame']}ms/장", flush=True)
        else:
            print("입력 영상·이미지 없음 → 식별(기준 142 대조)만 측정", flush=True)

        results = []
        for key in matchers:
            print(f"── {key} …", flush=True)
            entry = bench_matcher(key, args, query, frames, images, crops)
            results.append(entry)
            if "skipped" in entry:
                print(f"   건너뜀: {entry['skipped']}", flush=True)
            else:
                v = entry.get("video") or {}
                ident = entry["identify"]
                print(f"   발견 {v.get('found_frames', '-')}/{v.get('frames', '-')}  "
                      f"보정발견(>{entry['calib_threshold']}) {v.get('calib_found_frames', '-')}  "
                      f"최고 {v.get('best_cosine', '-')}  분리도 {v.get('separation', '-')}  "
                      f"식별 Top-1 {ident['top1_brand']} (정답순위 {ident.get('expect_rank', '-')})",
                      flush=True)

        payload = {
            "run_id": run_id, "created_at": datetime.now().isoformat(timespec="seconds"),
            "inputs": {"videos": video_meta, "images": [p.name for _, p in images],
                       "query_logo": str(query), "expect_brand": args.expect_brand},
            "params": {"fps": args.fps, "det_threshold": args.det_threshold,
                       "min_cosine": args.min_cosine, "max_boxes": args.max_boxes,
                       "max_frames": args.max_frames},
            "detector": detector_info, "results": results,
        }
        out = out_dir / "results.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"저장: {out}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
