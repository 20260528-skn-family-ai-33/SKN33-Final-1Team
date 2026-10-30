"""guided(지정 로고 1:1) + 이중검수 오케스트레이터 — 보고서 「로고 검수 모델 비교」(2026-10-26) §6.

검수 전제: 들어가야 할 로고가 정해져 있다(발주처가 CI를 지정). 그 로고를 쿼리로 넣어 1:1로 대조한다.
영상 속 로고를 자동 식별하는 blind는 노이즈가 커서 운영에 쓰지 않는다.

한 프레임 파이프라인:
  ① 탐지   OWLv2 텍스트 탐지로 '로고 같은' 영역을 제안한다(image-guided는 영상에서 노이즈가 많아 쓰지 않음).
  ② 1차 대조 각 영역 크롭을 지정 로고와 CLIP/SigLIP2 코사인으로 1:1 대조한다.
  ③ 2차 이중검수 코사인이 애매한(present~certain) 영역만 LightGlue 기하 검증을 돌린다.
  ④ 판정   확실(detected) / 확인 필요(review) / 미검출(absent).

임계값(§6)은 라벨링된 평가셋으로 정밀 보정하기 전의 잠정값이다. 환경변수로 덮어쓸 수 있다.

이 모듈은 모델을 들고 프레임을 판정하는 '순수 로직'이다. 모델을 로컬(Pod)에서 돌릴지 RunPod로 보낼지는
backend/app/logo_runtime.py 가 정한다. ffmpeg 프레임 추출 유틸(sample_frames)도 여기 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from . import logo_models as reg
from .logo_pipeline import LogoDetector, LogoMatcher, LogoVerifier, crop, load_rgb

# 판정 코드 → 사람이 읽는 한국어 라벨. 우선순위: detected > review > absent.
VERDICTS = ("detected", "review", "absent")
VERDICT_LABELS = {"detected": "확실", "review": "확인 필요", "absent": "미검출"}


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


@dataclass
class ReviewConfig:
    """§6 임계값과 탐지 파라미터. 환경변수 LOGO_* 로 덮어쓸 수 있다(평가셋 보정 전 잠정값)."""
    cosine_certain: float = field(default_factory=lambda: _env_float("LOGO_COSINE_CERTAIN", 0.85, 0.0, 1.0))
    cosine_present: float = field(default_factory=lambda: _env_float("LOGO_COSINE_PRESENT", 0.60, 0.0, 1.0))
    lightglue_min_matches: int = field(default_factory=lambda: _env_int("LOGO_LIGHTGLUE_MIN", 20, 1, 10_000))
    det_threshold: float = field(default_factory=lambda: _env_float("LOGO_DET_THRESHOLD", 0.10, 0.0, 1.0))
    max_boxes: int = field(default_factory=lambda: _env_int("LOGO_MAX_BOXES", 10, 1, 100))
    verify_budget: int = field(default_factory=lambda: _env_int("LOGO_VERIFY_BUDGET", 3, 0, 50))

    # 요청별 오버라이드 허용 범위(범위 밖 값은 무시하고 기본값 유지).
    _BOUNDS = {"cosine_certain": (0.0, 1.0), "cosine_present": (0.0, 1.0),
               "lightglue_min_matches": (1, 10_000), "det_threshold": (0.0, 1.0),
               "max_boxes": (1, 100), "verify_budget": (0, 50)}

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self._BOUNDS}

    @classmethod
    def from_overrides(cls, overrides: dict | None = None) -> "ReviewConfig":
        """env 기본값 위에 요청이 준 값을 범위 안에서만 덮어쓴다(검수 호출자/워커 공용)."""
        config = cls()
        for name, (low, high) in cls._BOUNDS.items():
            value = (overrides or {}).get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
                setattr(config, name, type(getattr(config, name))(value))
        return config


def _best_verdict(verdicts) -> str:
    for level in VERDICTS:  # detected 가 하나라도 있으면 detected, 다음 review, 모두 아니면 absent
        if level in verdicts:
            return level
    return "absent"


class GuidedReviewer:
    """지정 로고 1장을 쿼리로 들고, 프레임마다 ①~④를 돌려 판정한다.

    verify=False 이거나 verifier가 없으면 이중검수를 생략한다(조합 A: 애매 구간은 바로 '확인 필요').
    verifier가 있으면 조합 B(운영 기본): 애매 구간만 LightGlue 2차로 확정/보류를 가른다.
    """

    def __init__(self, detector: LogoDetector, matcher: LogoMatcher,
                 verifier: LogoVerifier | None = None, config: ReviewConfig | None = None):
        self.detector = detector
        self.matcher = matcher
        self.verifier = verifier
        self.config = config or ReviewConfig()
        self.query_vec: list[float] | None = None
        self.query_feats = None

    def set_query(self, query_logo) -> None:
        """검사할 로고(쿼리)를 한 번만 임베딩/특징 추출해 둔다. 프레임마다 재사용한다."""
        self.query_vec = self.matcher.embed(query_logo)
        self.query_feats = self.verifier.query_features(query_logo) if self.verifier else None

    def review_image(self, image) -> dict:
        """프레임 1장 판정. image는 경로나 PIL 이미지. 상위 영역 몇 개와 프레임 판정을 돌려준다."""
        if self.query_vec is None:
            raise RuntimeError("set_query(query_logo)를 먼저 호출하세요.")
        cfg = self.config
        boxes = self.detector.text_detect(image, threshold=cfg.det_threshold, max_boxes=cfg.max_boxes)
        if not boxes:
            return {"verdict": "absent", "top_cosine": 0.0, "boxes": []}

        rgb = load_rgb(image)  # 상자마다 파일을 다시 열지 않는다
        crops = [crop(rgb, box.xyxy) for box in boxes]
        vectors = self.matcher.embed_many(crops)
        cosines = [round(sum(x * y for x, y in zip(vec, self.query_vec)), 4) for vec in vectors]
        order = sorted(range(len(boxes)), key=lambda i: cosines[i], reverse=True)

        budget = cfg.verify_budget if self.verifier else 0
        reviewed = []
        for i in order:
            cosine = cosines[i]
            entry = {"xyxy": boxes[i].xyxy, "detect_score": boxes[i].score,
                     "cosine": cosine, "lightglue": None, "verdict": "absent"}
            if cosine >= cfg.cosine_certain:
                entry["verdict"] = "detected"              # 확실 — 단일로 종결
            elif cosine >= cfg.cosine_present:
                if budget > 0:                             # 애매 — 코사인 높은 쪽부터 이중검수
                    budget -= 1
                    matches = self.verifier.count_matches(crops[i], self.query_feats)
                    entry["lightglue"] = matches
                    entry["verdict"] = "detected" if matches >= cfg.lightglue_min_matches else "review"
                else:
                    entry["verdict"] = "review"            # 검증기 없음/예산 소진 → 사람·VLM 확인
            reviewed.append(entry)

        return {"verdict": _best_verdict([e["verdict"] for e in reviewed]),
                "top_cosine": cosines[order[0]], "boxes": reviewed[:5]}


# ---------------------------------------------------------------------------
# 조립 헬퍼 — 레지스트리 조합으로 Reviewer를 만든다
# ---------------------------------------------------------------------------
def build_reviewer(matcher_key: str = "clip", *, verify: bool = True,
                   config: ReviewConfig | None = None, device: str | None = None) -> GuidedReviewer:
    """조합 B(verify=True) 기본. 모델을 GPU에 올려 Reviewer를 조립한다(Pod에서 1회).

    detect는 OWLv2 고정, match는 레지스트리의 match 모델(clip/siglip2), verify는 LightGlue.
    """
    spec = reg.spec(matcher_key)
    if spec.role != "match":
        raise ValueError(f"{matcher_key} 는 match 모델이 아닙니다(role={spec.role}).")
    detector = LogoDetector("owlv2", device=device)
    matcher = LogoMatcher(matcher_key, device=device)
    verifier = LogoVerifier(device=device) if verify else None
    return GuidedReviewer(detector, matcher, verifier, config=config)


# ---------------------------------------------------------------------------
# 영상 집계 — 프레임별 판정을 로고 등장 요약으로
# ---------------------------------------------------------------------------
def summarize(per_frame: list[dict]) -> dict:
    """프레임별 결과 목록을 영상 1편 요약으로 접는다.

    per_frame 각 항목은 review_image 결과에 time_s(또는 frame_id)를 더한 것이다.
    영상 판정: 한 프레임이라도 detected면 detected, 아니면 review 있으면 review, 모두 아니면 absent.
    """
    verdicts = [f.get("verdict", "absent") for f in per_frame]
    overall = _best_verdict(verdicts)
    detected_times = [f.get("time_s") for f in per_frame if f.get("verdict") == "detected"]
    review_times = [f.get("time_s") for f in per_frame if f.get("verdict") == "review"]
    best_cosine = max((f.get("top_cosine", 0.0) for f in per_frame), default=0.0)
    return {
        "verdict": overall, "verdict_label": VERDICT_LABELS[overall],
        "frames": len(per_frame),
        "detected_frames": verdicts.count("detected"), "review_frames": verdicts.count("review"),
        "detected_times": [t for t in detected_times if t is not None],
        "review_times": [t for t in review_times if t is not None],
        "best_cosine": round(best_cosine, 4),
    }


# ---------------------------------------------------------------------------
# ffmpeg 프레임 추출(검수 호출자가 영상 → 프레임으로 쪼갤 때)
# ---------------------------------------------------------------------------
def sample_frames(video: Path, out_dir: Path, fps: float = 1.0) -> list[tuple[float, Path]]:
    """영상을 fps 간격으로 JPEG 프레임으로 뽑는다. (시각초, 경로) 목록을 돌려준다.

    FastAPI 서버의 정밀 표본(video_frames.py)과 달리, CLI·간이 검수용 단순 추출이다.
    """
    import shutil
    import subprocess
    from .video_frames import resolve_ffmpeg

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg를 찾을 수 없습니다. `pip install imageio-ffmpeg` 하거나 FFMPEG_BIN을 지정하세요.")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f*.jpg"):
        old.unlink()
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
                    "-vf", f"fps={fps}", "-q:v", "2", str(out_dir / "f%06d.jpg")],
                   check=True, stdin=subprocess.DEVNULL)
    frames = sorted(out_dir.glob("f*.jpg"))
    return [(round(i / fps, 2), path) for i, path in enumerate(frames)]
