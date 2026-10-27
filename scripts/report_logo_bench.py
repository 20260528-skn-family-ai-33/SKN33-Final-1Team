#!/usr/bin/env python3
"""bench_logo_models.py 결과 JSON(여러 실행)을 모델 비교 표가 든 마크다운 문서로 묶는다.

사용법:
  python scripts/report_logo_bench.py                                   # 가장 최근 결과 1개
  python scripts/report_logo_bench.py .runtime/logo-bench/*/results.json  # 여러 실행을 한 문서로
  python scripts/report_logo_bench.py <results...> --baseline /workspace/seoul*_seoulpng.json
      # --baseline: 예전 run_logo_video.py(조합 A) 결과. 같은 하네스로 다시 잰 A와 나란히 놓아 재현 확인
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = ROOT / ".runtime" / "logo-bench"
DEFAULT_OUT = ROOT / "docs" / "logo-model-benchmark.md"


def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "O" if value else "X"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"
    return str(value)


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(c if isinstance(c, str) else _fmt(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _matchers(runs: list[dict]) -> list[str]:
    """실행된 매처를 처음 나온 순서대로(조합 A의 dinov2가 보통 먼저)."""
    seen: dict[str, None] = {}
    for run in runs:
        for r in run["results"]:
            if "skipped" not in r:
                seen.setdefault(r["matcher"], None)
    return list(seen)


def _col(matcher: str, detector: str) -> str:
    return f"{detector}+{matcher}" + (" (A)" if detector == "owlv2" and matcher == "dinov2" else "")


def _by_matcher(run: dict) -> dict:
    return {r["matcher"]: r for r in run["results"] if "skipped" not in r}


def load_baseline(paths: list[Path]) -> dict:
    """{(video, query_logo 파일명): {"found": "M/N", "top": 최고 코사인}}"""
    out = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("mode") != "guided":
            continue
        tops = [p.get("top_cosine", 0.0) for p in data.get("per_frame", [])]
        out[(Path(data["video"]).name, Path(data["query_logo"]).name)] = {
            "found": f"{data['found_frames']}/{data['frames']}",
            "top": round(max(tops), 4) if tops else None, "min_cosine": data.get("min_cosine"),
            "fps": data.get("fps")}
    return out


def render(runs: list[dict], baseline: dict) -> str:
    matchers = _matchers(runs)
    detector = runs[0]["detector"]["detector"]
    cols = [_col(m, detector) for m in matchers]
    out = []

    # 1) 동영상 발견 비교
    video_rows, calib_rows, sep_rows = [], [], []
    for run in runs:
        query = Path(run["inputs"]["query_logo"]).name
        by = _by_matcher(run)
        names = [v["video"] for v in run["inputs"]["videos"]]
        for name in names:
            cells, calibs, seps = [], [], []
            for m in matchers:
                stats = next((v for v in (by.get(m) or {}).get("videos", []) if v["video"] == name), None)
                cells.append(f"{stats['found_frames']}/{stats['frames']} · {_fmt(stats['best_cosine'])}"
                             if stats else "-")
                calibs.append(f"{stats['calib_found_frames']}/{stats['frames']} · "
                              f"{'+' if stats['best_margin_over_calib'] >= 0 else ''}"
                              f"{_fmt(stats['best_margin_over_calib'])}"
                              if stats and "calib_found_frames" in stats else "-")
                seps.append(stats["separation"] if stats else None)
            calib_rows.append([f"`{name}`", f"`{query}`", *calibs])
            base = baseline.get((name, query))
            row = [f"`{name}`", f"`{query}`", f"≥{run['params']['min_cosine']}", *cells]
            if baseline:
                row.append(f"{base['found']} · {_fmt(base['top'])}" if base else "-")
            video_rows.append(row)
            sep_rows.append([f"`{name}`", f"`{query}`", *seps])
    if video_rows:
        headers = ["동영상", "쿼리 로고", "발견 기준", *cols] + (["기존 A 실행(참고)"] if baseline else [])
        out += ["## 1. 동영상 — 쿼리 로고 발견", "",
                "조합 A 테스트와 같은 **고정 기준**(min_cosine). "
                "칸 = **발견 프레임/전체 프레임 · 최고 코사인**. 발견 = 후보 영역 중 쿼리와 코사인이 기준 이상인 영역이 있는 프레임.",
                "매처마다 코사인 분포가 달라 같은 기준값이 모델 간에 공정하지 않을 수 있으므로 2번 분리도와 함께 봅니다.", "",
                _table(headers, video_rows), ""]
        thresholds = {}
        for run in runs:
            for m, r in _by_matcher(run).items():
                if "calib_threshold" in r:
                    thresholds.setdefault(Path(run["inputs"]["query_logo"]).name, {})[m] = r["calib_threshold"]
        out += ["### 1-1. 매처별 보정 기준으로 본 발견", "",
                "보정 기준 = 쿼리 로고와 **정답이 아닌 기준 로고들 사이의 최대 코사인**(매처마다 다름). "
                "'알려진 다른 어떤 로고보다도 쿼리와 더 닮은 영역'이 있을 때만 발견으로 셉니다.",
                "칸 = **발견 프레임/전체 · 최고 코사인 − 보정 기준**(+면 기준을 넘음, 클수록 확실).", "",
                _table(["쿼리 로고", *matchers],
                       [[f"`{q}`", *[ths.get(m) for m in matchers]] for q, ths in thresholds.items()]), "",
                _table(["동영상", "쿼리 로고", *cols], calib_rows), ""]
        out += ["## 2. 동영상 — 분리도(발견 영역 평균 − 나머지 영역 중앙값, 클수록 좋음)", "",
                "발견이 0이면 계산할 수 없어 `-` 입니다.", "",
                _table(["동영상", "쿼리 로고", *cols], sep_rows), ""]

    # 3) 이미지
    image_rows = []
    for run in runs:
        by = _by_matcher(run)
        for idx, name in enumerate(run["inputs"]["images"]):
            image_rows.append([f"`{name}`", f"`{Path(run['inputs']['query_logo']).name}`",
                               *[(f"{_fmt(by[m]['images'][idx]['best_cosine'])} "
                                  f"({'O' if by[m]['images'][idx]['found'] else 'X'})")
                                 if m in by and by[m].get("images") else "-" for m in matchers]])
    if image_rows:
        out += ["## 3. 이미지 — 최고 코사인(발견 여부)", "", _table(["이미지", "쿼리 로고", *cols], image_rows), ""]

    # 4) 식별(쿼리 로고 → 기준 142). 같은 쿼리는 실행이 여러 번이어도 결과가 같으므로 1번만.
    ident_rows, seen_q = [], set()
    for run in runs:
        query, expect = Path(run["inputs"]["query_logo"]).name, run["inputs"].get("expect_brand")
        if query in seen_q:
            continue
        seen_q.add(query)
        by = _by_matcher(run)
        for m in matchers:
            if m not in by:
                continue
            ident = by[m]["identify"]
            ident_rows.append([f"`{query}`", f"`{expect or '-'}`", m, f"`{ident['top1_brand']}`",
                               ident.get("expect_rank"), ident.get("top1_correct"),
                               ident.get("expect_cosine"), ident["top5"][0]["cosine"],
                               ident["margin_top1_top2"]])
    out += ["## 4. 식별 — 쿼리 로고 → 기준 142개 최근접", "",
            "정답 순위 1이 이상적. 정답이 기준셋에 없으면 순위는 `-` 이고 Top-1 코사인이 낮을수록(오인식 위험↓) 좋습니다.", "",
            _table(["쿼리 로고", "정답 브랜드", "매처", "Top-1 브랜드", "정답 순위", "Top-1 정답",
                    "정답 코사인", "Top-1 코사인", "1·2위 격차"], ident_rows), ""]

    # 5) 비용(첫 실행 기준 + 프레임 가중 평균 임베딩 시간)
    cost_rows = []
    for m in matchers:
        entries = [_by_matcher(run)[m] for run in runs if m in _by_matcher(run)]
        frames = sum((e.get("video") or {}).get("frames", 0) for e in entries)
        ms = sum((e.get("video") or {}).get("embed_ms_per_frame", 0) * (e.get("video") or {}).get("frames", 0)
                 for e in entries if (e.get("video") or {}).get("embed_ms_per_frame") is not None)
        peaks = [e["gpu_peak_mb"] for e in entries if e.get("gpu_peak_mb") is not None]
        cost_rows.append([m, f"`{entries[0]['repo_id']}`", entries[0].get("device"),
                          entries[0].get("load_s"), round(ms / frames, 1) if frames else None,
                          max(peaks) if peaks else None, (entries[0].get("resolved_commit") or "-")[:10]])
    dets = [run["detector"] for run in runs if run["detector"].get("detect_ms_per_frame") is not None]
    out += ["## 5. 비용", "", _table(["매처", "모델", "장치", "로드(s)", "임베딩 ms/프레임",
                                    "GPU 최대 메모리(MB)", "모델 커밋"], cost_rows), ""]
    if dets:
        out += [f"탐지기 `{detector}`({dets[0]['repo_id']}, {dets[0]['device']}): "
                f"프레임당 {_fmt(sum(d['detect_ms_per_frame'] for d in dets) / len(dets), 1)}ms, "
                f"후보 영역 평균 {_fmt(sum(d['mean_boxes'] for d in dets) / len(dets), 2)}개 "
                "(모든 매처가 같은 후보를 공유하므로 탐지 비용은 공통).", ""]

    # 6) 건너뛴 모델
    skipped = {r["matcher"]: r["skipped"] for run in runs for r in run["results"] if "skipped" in r}
    if skipped:
        out += ["## 6. 건너뛴 모델", ""] + [f"- `{k}`: {v}" for k, v in skipped.items()] + [""]

    # 7) 실행 기록
    out += ["## 7. 실행 기록", "",
            _table(["실행 ID", "동영상", "이미지", "쿼리 로고", "fps", "det_threshold", "min_cosine", "max_boxes"],
                   [[run["run_id"], ", ".join(f"{v['video']}({v['frames']})" for v in run["inputs"]["videos"]) or "-",
                     ", ".join(run["inputs"]["images"]) or "-", Path(run["inputs"]["query_logo"]).name,
                     run["params"]["fps"], run["params"]["det_threshold"], run["params"]["min_cosine"],
                     run["params"]["max_boxes"]] for run in runs]), ""]
    return "\n".join(out)


HEADER = """# 로고 검수 모델 성능 비교

「로고검수 모델 성능검토 계획」(2026-10-23)에 따라 후보 모델을 **조합 A 테스트와 같은 동영상·쿼리 로고**로 돌린 결과입니다.
`scripts/bench_logo_models.py` 로 측정하고 `scripts/report_logo_bench.py` 로 생성했습니다.

- 모든 매처는 같은 탐지 후보(OWLv2 텍스트 탐지, 프레임당 상위 `max_boxes`개)를 공유합니다. 차이는 **동일성 대조(매처)** 에서만 나옵니다.
- 조합 A(owlv2+dinov2)도 같은 하네스로 다시 쟀고, 예전 단독 실행 결과를 「기존 A 실행」 열에 나란히 두었습니다.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", nargs="*", help="results.json 경로(생략 시 최신 1개)")
    parser.add_argument("--baseline", nargs="*", default=[], help="예전 run_logo_video.py guided 결과 JSON")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--summary", default=None, help="맨 위에 넣을 결론 마크다운 파일(선택)")
    args = parser.parse_args()

    paths = [Path(p) for p in args.results]
    if not paths:
        latest = sorted(BENCH_ROOT.glob("*/results.json"))
        if not latest:
            sys.exit(f"[중단] 결과가 없습니다: {BENCH_ROOT}/*/results.json")
        paths = [latest[-1]]
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    baseline = load_baseline([Path(p) for p in args.baseline])
    summary = Path(args.summary).read_text(encoding="utf-8").strip() + "\n\n" if args.summary else ""
    text = HEADER + "\n" + summary + render(runs, baseline)
    Path(args.out).write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"작성: {args.out}  (실행 {len(runs)}건)")


if __name__ == "__main__":
    main()
