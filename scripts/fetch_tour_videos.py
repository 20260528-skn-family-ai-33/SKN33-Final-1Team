#!/usr/bin/env python3
"""한국관광공사 관광 동영상(VideoService) 수집 도구.

공공데이터포털 15162188 (B551011/VideoService) 에서 관광 동영상을 조회하고,
검수용 베이스 영상을 "용량 작은 순"으로 몇 개 전체 다운로드해 sha256 매니페스트로 고정한다.

스키마(실측):
  videoList       → contentId, koTitle/enTitle, koKeyword, lDongRegnCd, lclsSystm1~3,
                    cpyrhtDivCd, thumbnailurl ...            (영상 URL·용량 없음)
  videoDetailInfo → orgCdnUrl(HLS .m3u8), orgFileSize, orgFrameRate, orgAspectRatio,
                    orgCodec, koShtngPlace, shtngDrone ...   (여기서 URL·용량이 나온다)

- API 키는 프로젝트 루트 .env 의 TOUR_VIDEO_API_KEY 에서 읽는다(화면에 절대 출력하지 않음).
- 영상은 HLS 이므로 ffmpeg(--ffmpeg 또는 env FFMPEG_BIN)로 -c copy 리먹스해 mp4 로 저장.
- 영상 파일은 data/videos/ 에 저장(= git 제외), 매니페스트(CSV)만 git 추적.
- 라이선스: 데이터셋 "이용허락범위 제한 없음". 항목 cpyrhtDivCd(Type1=공공누리 1유형) 를 함께 기록.

사용법:
  python scripts/fetch_tour_videos.py probe  [--rows 10] [--keyword ..] [--regn ..]
  python scripts/fetch_tour_videos.py detail --id <contentId>
  python scripts/fetch_tour_videos.py fetch  --ffmpeg <ffmpeg.exe> [--scan 60] [--count 8]
         [--max-mb 150] [--keyword ..] [--regn ..]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
VIDEO_DIR = ROOT / "data" / "videos"
MANIFEST = VIDEO_DIR / "tour_videos_manifest.csv"

BASE = "https://apis.data.go.kr/B551011/VideoService"
COMMON = {"MobileOS": "ETC", "MobileApp": "creativechecker", "_type": "json"}
PLACEHOLDER = "여기에_serviceKey_붙여넣기"
URL_KEYS = ("movieurl", "orgCdnUrl", "url", "videoUrl", "videourl", "mp4Url")

MANIFEST_FIELDS = [
    "content_id", "title", "ko_keyword", "shtng_place", "lcls1",
    "org_filesize_mb", "frame_rate", "aspect", "codec", "shtng_drone",
    "cpyrht_div_cd", "hls_url", "thumbnail_url", "file", "bytes", "sha256",
    "license", "collected_at", "source",
]


def read_api_key() -> str:
    """Read TOUR_VIDEO_API_KEY from .env without ever printing its value."""
    if not ENV_FILE.exists():
        sys.exit(f"[중단] .env 가 없습니다: {ENV_FILE}")
    key = ""
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if s.startswith("TOUR_VIDEO_API_KEY="):
            key = s.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not key or key == PLACEHOLDER:
        sys.exit("[중단] .env 의 TOUR_VIDEO_API_KEY 가 비어있거나 placeholder 입니다.")
    return key


def call_json(op: str, params: dict, key: str, timeout: int = 30) -> dict:
    qs = urlencode({**COMMON, **params}, doseq=True)
    sk = key if "%" in key else quote(key, safe="")  # Decoding 키면 1회 인코딩
    url = f"{BASE}/{op}?serviceKey={sk}&{qs}"
    req = Request(url, headers={"User-Agent": "creativechecker-video-collector"})
    raw = urlopen(req, timeout=timeout).read().decode("utf-8", errors="replace")  # noqa: S310
    if raw.lstrip().startswith("<"):
        raise SystemExit(f"[API 에러] XML 응답(키/활용신청 확인):\n{raw[:600]}")
    data = json.loads(raw)
    header = (data.get("response", {}) or {}).get("header", {}) or {}
    code = header.get("resultCode")
    if code not in (None, "0000", "00"):
        raise SystemExit(f"[API 에러] resultCode={code} resultMsg={header.get('resultMsg')}")
    return data


def extract_items(data: dict) -> list[dict]:
    body = (data.get("response", {}) or {}).get("body", {}) or {}
    items = body.get("items") or {}
    if isinstance(items, str) or not items:
        return []
    item = items.get("item")
    if item is None:
        return []
    return item if isinstance(item, list) else [item]


def field(it: dict, *keys: str) -> str:
    for k in keys:
        v = it.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def title_of(it: dict) -> str:
    return field(it, "koTitle", "title", "enTitle")


def url_of(it: dict) -> str:
    return field(it, *URL_KEYS)


def parse_mb(s: str):
    """'858.04 MB' / '1.2 GB' / '123MB' → float MB. 모르면 None."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)?", (s or "").upper())
    if not m or not m.group(1):
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    unit = (m.group(2) or "MB").rstrip("B") or "M"
    return {"K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024}.get(unit, 1.0) * val


def sha256_file(p: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b); n += len(b)
    return h.hexdigest(), n


def list_videos(args, key) -> list[dict]:
    params = {"numOfRows": getattr(args, "scan", None) or args.rows,
              "pageNo": getattr(args, "page", 1), "arrange": "A"}
    if args.keyword:
        params["keyword"] = args.keyword
    if args.regn:
        params["lDongRegnCd"] = args.regn
    return extract_items(call_json("videoList", params, key))


def detail_item(content_id: str, key: str):
    d = extract_items(call_json("videoDetailInfo",
                                {"contentId": content_id, "numOfRows": 10, "pageNo": 1}, key))
    return d[0] if d else None


def cmd_probe(args, key):
    items = list_videos(args, key)
    print(f"조회 {len(items)}건")
    if items:
        print("[목록 항목 키]:", ", ".join(sorted(items[0].keys())))
    print("-" * 90)
    for i, it in enumerate(items, 1):
        print(f"{i:>2}. cid={field(it,'contentId'):<8} | {title_of(it):<28.28} | "
              f"cpyrht={field(it,'cpyrhtDivCd'):<7} | thumb={'Y' if field(it,'thumbnailurl') else '-'}")
    print("-" * 90)
    print("NOTE: 영상 URL·용량은 목록에 없음 → videoDetailInfo(contentId).")


def cmd_detail(args, key):
    it = detail_item(args.id, key)
    if not it:
        print("상세 항목 없음."); return
    print("[상세 항목 키]:", ", ".join(sorted(it.keys())))
    for k in sorted(it.keys()):
        print(f"  {k} = {str(it[k])[:140]}")
    print("-" * 60)
    print("감지 URL:", url_of(it) or "(없음)", "| 용량:", field(it, "orgFileSize"))


def _ffmpeg_pull(ffmpeg: str, url: str, dst: Path, timeout: int = 900) -> bool:
    """HLS(.m3u8) → mp4 리먹스(-c copy). 실패 시 bsf 없이 1회 재시도.
    임시 파일에 받은 뒤 성공했을 때만 dst로 옮겨, 중단·타임아웃 시 반쪽 mp4가 남지 않게 한다."""
    part = dst.with_name(dst.stem + ".part.mp4")
    base = [ffmpeg, "-y", "-loglevel", "error", "-i", url, "-c", "copy"]
    try:
        for extra in (["-bsf:a", "aac_adtstoasc"], []):
            try:
                r = subprocess.run(base + extra + [str(part)], timeout=timeout, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                print(f"    ffmpeg 타임아웃({timeout}s)"); return False
            if r.returncode == 0 and part.exists() and part.stat().st_size > 0:
                part.replace(dst)
                return True
        return False
    finally:
        part.unlink(missing_ok=True)


def load_existing_ids() -> set[str]:
    if not MANIFEST.exists():
        return set()
    with MANIFEST.open(encoding="utf-8", newline="") as f:
        return {row["content_id"] for row in csv.DictReader(f)}


def resolve_ffmpeg(arg):
    """--ffmpeg → FFMPEG_BIN → PATH의 ffmpeg → imageio-ffmpeg 순으로 자동 탐색."""
    cand = arg or os.environ.get("FFMPEG_BIN")
    if cand:
        if not Path(cand).exists():
            sys.exit(f"[중단] 지정한 ffmpeg 경로가 없습니다: {cand}")
        return cand
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def cmd_fetch(args, key):
    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    if not ffmpeg:
        sys.exit("[중단] ffmpeg를 찾을 수 없습니다. `pip install imageio-ffmpeg` 하거나 --ffmpeg 로 경로를 지정하세요.")
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    items = list_videos(args, key)
    if not items:
        sys.exit("[중단] 목록이 비었습니다.")
    print(f"스캔 {len(items)}건의 상세에서 용량 수집 중…")

    cands = []  # (mb, cid, list_item, detail_item, url)
    for it in items:
        cid = field(it, "contentId")
        if not cid:
            continue
        det = detail_item(cid, key)
        if not det:
            continue
        url = url_of(det)
        mb = parse_mb(field(det, "orgFileSize"))
        if not url or mb is None:
            continue
        cands.append((mb, cid, it, det, url))
        time.sleep(0.15)

    cands.sort(key=lambda x: x[0])
    if args.max_mb:
        cands = [c for c in cands if c[0] <= args.max_mb]
    print(f"용량 파악 {len(cands)}건. 작은 순 하위 {args.count}개 다운로드"
          + (f" (≤{args.max_mb}MB)" if args.max_mb else "") + ".")

    done = load_existing_ids()
    rows, picked = [], 0
    for mb, cid, it, det, url in cands:
        if picked >= args.count:
            break
        if cid in done:
            continue
        dst = VIDEO_DIR / f"{cid}.mp4"
        print(f"[받는 중] cid={cid} ~{mb:.0f}MB  '{title_of(it)}'")
        if not _ffmpeg_pull(ffmpeg, url, dst):
            print(f"    실패 → 건너뜀")
            continue
        digest, nbytes = sha256_file(dst)
        row = {
            "content_id": cid,
            "title": title_of(it),
            "ko_keyword": field(det, "koKeyword")[:200],
            "shtng_place": field(det, "koShtngPlace", "enShtngPlace"),
            "lcls1": field(it, "lclsSystm1"),
            "org_filesize_mb": round(mb, 1),
            "frame_rate": field(det, "orgFrameRate"),
            "aspect": field(det, "orgAspectRatio"),
            "codec": field(det, "orgCodec"),
            "shtng_drone": field(det, "shtngDrone"),
            "cpyrht_div_cd": field(it, "cpyrhtDivCd"),
            "hls_url": url,
            "thumbnail_url": field(it, "thumbnailurl", "thumbnailUrl"),
            "file": f"videos/{dst.name}",
            "bytes": nbytes,
            "sha256": digest,
            "license": "데이터셋 제한없음 / 항목 cpyrhtDivCd 참조",
            "collected_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "source": "data.go.kr/15162188 B551011/VideoService",
        }
        append_manifest(row)  # 한 개씩 바로 기록 — 도중에 끊겨도 받은 영상은 매니페스트에 남는다
        rows.append(row)
        picked += 1
        print(f"    OK {dst.name}  {nbytes/1_000_000:.1f}MB  sha256={digest[:12]}…")

    if not rows:
        sys.exit("[중단] 새로 받은 영상이 없습니다.")
    total = sum(r["bytes"] for r in rows) / 1_000_000
    print(f"\n완료: {len(rows)}개 / {total:.1f}MB. 매니페스트: {MANIFEST}")


def append_manifest(row: dict) -> None:
    new = not MANIFEST.exists()
    with MANIFEST.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser(description="관광 동영상 수집")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    d = sub.add_parser("detail")
    f = sub.add_parser("fetch")
    for x in (p, f):
        x.add_argument("--rows", type=int, default=10)
        x.add_argument("--page", type=int, default=1)
        x.add_argument("--keyword", default=None)
        x.add_argument("--regn", default=None, help="법정동 시도 코드 lDongRegnCd")
    d.add_argument("--id", required=True, help="contentId")
    f.add_argument("--scan", type=int, default=60, help="용량 확인용 스캔 건수")
    f.add_argument("--count", type=int, default=8, help="내려받을 영상 수(작은 순)")
    f.add_argument("--max-mb", type=float, default=None, dest="max_mb", help="이 용량(MB) 이하만")
    f.add_argument("--ffmpeg", default=None, help="ffmpeg 실행파일 경로")
    args = ap.parse_args()
    key = read_api_key()
    {"probe": cmd_probe, "detail": cmd_detail, "fetch": cmd_fetch}[args.cmd](args, key)


if __name__ == "__main__":
    main()
