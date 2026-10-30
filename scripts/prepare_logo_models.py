#!/usr/bin/env python3
"""RunPod GPU Pod에서 로고 검수 모델을 받아 .runtime/logo-models/ 에 고정한다.

Paddle 경로(scripts/prepare_paddle.py)와 같은 원칙:
  - 명시적 준비 명령에서만 받는다.
  - 받은 뒤 실제 커밋 해시를 manifest.json에 적어 고정한다.
  - 이미 받은 유효한 스냅샷은 다시 받지 않는다.

사용법 (Pod 터미널):
  python scripts/prepare_logo_models.py            # 조합 A(owlv2+clip)
  python scripts/prepare_logo_models.py --combo B  # 운영 기본(owlv2+clip+lightglue; lightglue는 자동 건너뜀)
  python scripts/prepare_logo_models.py --models owlv2,clip,siglip2
  python scripts/prepare_logo_models.py --models dinov3   # 게이트: HF_TOKEN 필요

게이트 모델(dinov3 등)은 환경변수 HF_TOKEN 또는 `huggingface-cli login` 이 필요하다.
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


def prepare_one(key: str, force: bool = False) -> None:
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import GatedRepoError, HfHubHTTPError

    spec = reg.spec(key)
    target = reg.model_dir(key)
    if spec.loader != "hf":
        # LightGlue 등은 kornia가 실행 시 가중치를 받는다. HF 스냅샷 준비가 필요 없다.
        print(f"[건너뜀] {key}: {spec.loader} 로더라 실행 시 가중치를 받습니다(HF 준비 불필요).", flush=True)
        return
    if reg.is_prepared(key) and not force:
        print(f"기존 모델 확인: {key} ({spec.repo_id})", flush=True)
        return

    token = None
    if spec.gated:
        import os
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
        if not token:
            print(f"[건너뜀] {key}: 게이트 모델이라 HF_TOKEN 이 필요합니다. "
                  f"`export HF_TOKEN=...` 후 다시 실행하세요.", flush=True)
            return

    target.mkdir(parents=True, exist_ok=True)
    print(f"받는 중: {key} ← {spec.repo_id}@{spec.revision}", flush=True)
    try:
        # transformers가 쓰는 파일만 받는다(용량 절약: 대형 .bin 중복 회피).
        path = snapshot_download(
            repo_id=spec.repo_id, revision=spec.revision, local_dir=str(target), token=token,
            allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model", "*.bin",
                            "preprocessor_config.json", "tokenizer*", "vocab*", "merges*"])
    except GatedRepoError:
        print(f"[건너뜀] {key}: 저장소 접근 권한이 없습니다(HF에서 라이선스 동의 필요).", flush=True)
        return
    except HfHubHTTPError as exc:
        print(f"[실패] {key}: 다운로드 오류 {exc}", flush=True)
        return

    resolved = _resolve_commit(spec.repo_id, spec.revision, token)
    manifest = {
        "key": key, "role": spec.role, "repo_id": spec.repo_id,
        "requested_revision": spec.revision, "resolved_commit": resolved,
        "embed": spec.embed, "local_dir": str(path),
    }
    reg.manifest_path(key).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"준비 완료: {key} → {target}  (commit={resolved or '불명'})", flush=True)


def _resolve_commit(repo_id: str, revision: str, token):
    """재현성을 위해 받은 스냅샷의 실제 커밋 해시를 기록한다. 실패해도 준비는 유지."""
    try:
        from huggingface_hub import HfApi
        return HfApi().model_info(repo_id, revision=revision, token=token).sha
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default=None, help="쉼표로 구분한 모델 키 (예: owlv2,dinov2)")
    parser.add_argument("--combo", default=None, help="조합 키 (예: A)")
    parser.add_argument("--force", action="store_true", help="이미 받은 모델도 다시 받기")
    args = parser.parse_args()

    keys = reg.resolve_keys(args.models, args.combo)
    print(f"준비할 모델: {', '.join(keys)}  (대상: {reg.MODELS_ROOT})", flush=True)
    for key in keys:
        prepare_one(key, force=args.force)
    print("끝.", flush=True)


if __name__ == "__main__":
    main()
