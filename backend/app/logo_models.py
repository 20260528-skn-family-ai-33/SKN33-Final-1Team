"""로고 검수 후보 모델 레지스트리와 캐시 경로.

운영 구성은 보고서 「로고 검수 모델 비교」(2026-10-26)의 결론 = **guided(지정 로고 1:1) + 이중검수**를 따른다.
  - detect: OWLv2 — 올린 로고와 '로고 같은' 영역을 찾는다.
  - match : CLIP(기본) / SigLIP2(정확도 우선) — 영역 크롭을 지정 로고와 코사인 1:1 대조(1차).
  - verify: LightGlue — 코사인이 애매한 구간만 구조·위치를 기하 검증(2차 이중검수).
DINOv2·EVA-CLIP·pHash·ORB·YOLO-World 등은 벤치(2026-10-23 계획)에서 쓴 비교군이라 레지스트리에
남겨 두되 운영 조합에는 넣지 않는다.

- 실제 추론은 RunPod GPU Pod에서만 한다(로컬 PC는 GPU 없음).
- HF 모델은 scripts/prepare_logo_models.py 로 .runtime/logo-models/<key>/ 에 받고 manifest.json을 남긴다.
- Paddle 경로(scripts/prepare_paddle.py)와 같은 원칙: 고정 revision, 다운로드 후 manifest 기록,
  실행 중에는 새 모델을 임의로 받지 않는다(HF_HUB_OFFLINE로 강제 가능).
- LightGlue는 kornia가 실행 시 가중치를 받으므로(loader="kornia") HF 준비 절차를 타지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 받은 모델 스냅샷과 선계산한 기준 로고 임베딩을 두는 곳(Paddle과 같은 .runtime 아래).
MODELS_ROOT = Path(os.getenv("LOGO_MODELS_DIR", str(PROJECT_ROOT / ".runtime" / "logo-models"))).expanduser()
INDEX_ROOT = Path(os.getenv("LOGO_INDEX_DIR", str(PROJECT_ROOT / ".runtime" / "logo-index"))).expanduser()
# 기준 로고 시드(원본 PNG): data/logos/regions_logos/<brand>/logo.png
LOGO_SEED_ROOT = Path(os.getenv("LOGO_SEED_DIR", str(PROJECT_ROOT / "data" / "logos" / "regions_logos"))).expanduser()


@dataclass(frozen=True)
class ModelSpec:
    key: str            # 레지스트리 키이자 캐시 폴더 이름
    role: str           # "detect"(위치 탐지) | "match"(동일성 임베딩) | "verify"(기하 이중검수)
    repo_id: str        # Hugging Face 저장소(loader="kornia"면 설명용)
    revision: str = "main"   # 받은 뒤 실제 커밋 해시를 manifest에 적어 고정한다
    embed: str = "backbone"  # match 전용: "backbone"(CLS/pooler) | "image_features"(CLIP/SigLIP)
    gated: bool = False      # True면 HF 로그인/HF_TOKEN 필요
    loader: str = "hf"       # "hf"(prepare_logo_models로 받음) | "kornia"(실행 시 자체 다운로드)
    note: str = ""


# key 순서는 안정적으로 둔다(manifest·로그 재현성).
REGISTRY: dict[str, ModelSpec] = {
    # --- 운영 조합(보고서 2026-10-26 결론) ---
    "owlv2": ModelSpec(
        "owlv2", "detect", "google/owlv2-base-patch16-ensemble",
        note="탐지기 최상(OWLv2 > YOLO-World). '로고 같은' 영역을 찾는다. 학습 불필요"),
    "clip": ModelSpec(
        "clip", "match", "openai/clip-vit-large-patch14", embed="image_features",
        note="1차 대조 기본값. 지정 로고에서 코사인 0.9±. 운영 권장(정확도 최우선이면 siglip2)"),
    "siglip2": ModelSpec(
        "siglip2", "match", "google/siglip2-so400m-patch14-384", embed="image_features",
        note="1차 대조 정확도 우선안. CLIP과 동급(0.9±)"),
    "lightglue": ModelSpec(
        "lightglue", "verify", "kornia:DISK+LightGlue", loader="kornia",
        note="이중검수(조합 B): DISK 지역특징 + LightGlue 기하 매칭. 코사인 애매 구간만 2차로 돈다. "
             "점수=매칭 개수(진짜 로고 38~223). kornia가 실행 시 가중치를 받는다(HF 준비 불필요)"),

    # --- 비교군(벤치에서만 씀, 운영 조합 미포함; --models 로 선택) ---
    "dinov2": ModelSpec(
        "dinov2", "match", "facebook/dinov2-base",
        note="임베딩 중 약함(보고서 2026-10-26). 운영 미채택, 비교군으로만 유지"),
    "dinov3": ModelSpec(
        "dinov3", "match", "facebook/dinov3-vitb16-pretrain-lvd1689m", gated=True,
        note="게이트 모델: HF_TOKEN 필요 + transformers 최신. 미측정"),
}

# 보고서 「로고 검수 모델 비교」(2026-10-26)의 운영 조합. guided(지정 로고 1:1) 전제.
COMBOS: dict[str, list[str]] = {
    "A": ["owlv2", "clip"],               # 주력: OWLv2 탐지 + CLIP 1차 대조
    "B": ["owlv2", "clip", "lightglue"],  # 운영 기본: + LightGlue 기하 이중검수(오탐 방어)
}


def spec(key: str) -> ModelSpec:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"알 수 없는 모델 키: {key!r} (가능: {', '.join(REGISTRY)})") from None


def resolve_keys(models: str | None, combo: str | None) -> list[str]:
    """--models owlv2,dinov2 또는 --combo A 를 모델 키 목록으로 바꾼다(중복 제거, 순서 유지)."""
    keys: list[str] = []
    if combo:
        if combo not in COMBOS:
            raise KeyError(f"알 수 없는 조합: {combo!r} (가능: {', '.join(COMBOS)})")
        keys += COMBOS[combo]
    if models:
        keys += [k.strip() for k in models.split(",") if k.strip()]
    if not keys:
        keys = list(COMBOS["A"])
    seen: dict[str, None] = {}
    for k in keys:
        spec(k)  # 존재 검증
        seen.setdefault(k, None)
    return list(seen)


def model_dir(key: str) -> Path:
    return MODELS_ROOT / key


def manifest_path(key: str) -> Path:
    return model_dir(key) / "manifest.json"


def read_manifest(key: str) -> dict:
    return json.loads(manifest_path(key).read_text(encoding="utf-8"))


def is_prepared(key: str) -> bool:
    """manifest가 있고 그 안의 repo_id가 레지스트리와 일치하면 준비된 것으로 본다."""
    path = manifest_path(key)
    if not path.is_file():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return manifest.get("repo_id") == spec(key).repo_id


def index_dir(matcher_key: str) -> Path:
    """선계산한 기준 로고 임베딩(매처별로 다름)을 두는 곳."""
    return INDEX_ROOT / matcher_key
