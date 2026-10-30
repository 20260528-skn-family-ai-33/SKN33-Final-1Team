#!/usr/bin/env python3
"""기준 로고 142개를 매처로 임베딩해 .runtime/logo-index/<matcher>/ 에 저장한다.

data/logos/regions_logos/<brand>/logo.png 를 모두 읽어
알파를 흰 배경에 합성 → 임베딩 → L2 정규화 → embeddings.npy + brands.json 로 저장한다.
검수 때마다 다시 계산하지 않도록 서버/Pod 기동 시 1회만 돌린다(성능검토 계획 §6-2).

사용법 (Pod 터미널, 모델 준비 후):
  python scripts/precompute_logo_embeddings.py --matcher dinov2
  python scripts/precompute_logo_embeddings.py --matcher dinov2 --seed-dir data/logos/regions_logos
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import logo_models as reg  # noqa: E402


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def collect_logos(seed_dir: Path) -> list[tuple[str, Path]]:
    """(brand_id, logo.png 경로) 목록을 폴더명 기준으로 정렬해 돌려준다(<brand>/logo.png 구조)."""
    pairs = []
    for logo in sorted(seed_dir.glob("*/logo.png")):
        pairs.append((logo.parent.name, logo))
    return pairs


def collect_flat(flat_dir: Path) -> list[tuple[str, Path]]:
    """평평한 폴더의 이미지들을 (파일명 stem = 브랜드, 경로)로 모은다."""
    pairs = []
    for img in sorted(flat_dir.iterdir()):
        if img.is_file() and img.suffix.lower() in IMAGE_EXTS:
            pairs.append((img.stem, img))
    return pairs


def main() -> None:
    import numpy as np
    from backend.app.logo_pipeline import LogoMatcher

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matcher", default="dinov2", help="동일성 임베딩 모델 키")
    parser.add_argument("--seed-dir", default=str(reg.LOGO_SEED_ROOT), help="기준 로고 시드 폴더(<brand>/logo.png)")
    parser.add_argument("--flat-dir", default=None, help="평평한 로고 폴더(파일명=브랜드). 지정 시 seed-dir 대신 사용")
    parser.add_argument("--index-name", default=None, help="인덱스 저장 이름(기본=matcher)")
    parser.add_argument("--batch", type=int, default=16, help="임베딩 배치 크기")
    args = parser.parse_args()

    if args.flat_dir:
        flat = Path(args.flat_dir)
        pairs = collect_flat(flat)
        source_desc = str(flat)
    else:
        seed_dir = Path(args.seed_dir)
        pairs = collect_logos(seed_dir)
        source_desc = f"{seed_dir}/*/logo.png"
    if not pairs:
        sys.exit(f"[중단] 기준 로고를 찾지 못했습니다: {source_desc}")
    print(f"기준 로고 {len(pairs)}개 임베딩({args.matcher}) 시작…", flush=True)

    matcher = LogoMatcher(args.matcher)
    brands, vectors = [], []
    for start in range(0, len(pairs), args.batch):
        chunk = pairs[start:start + args.batch]
        vectors.extend(matcher.embed_many([path for _, path in chunk]))
        brands.extend([brand for brand, _ in chunk])
        print(f"  {min(start + args.batch, len(pairs))} / {len(pairs)}", flush=True)

    matrix = np.asarray(vectors, dtype=np.float32)
    out_dir = reg.index_dir(args.index_name or args.matcher)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "embeddings.npy", matrix)
    (out_dir / "brands.json").write_text(
        json.dumps({"matcher": args.matcher, "index": args.index_name or args.matcher,
                    "dim": int(matrix.shape[1]), "count": len(brands), "brands": brands},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"저장 완료: {out_dir}  (shape={matrix.shape})", flush=True)


if __name__ == "__main__":
    main()
