"""A04/A05 시각 기준의 판정 근거: Qwen 객체 목록 + OWLv2 일반 객체 상자를 합친 후보 목록.

근거 시험(팀 시험 2026-11-17~18, 포스터 50장·정답 객체 920개, 정답은 AI 판정자 5개가 만든 것):
  Qwen3.5-4B 단독 F1 64.5(정밀도 81.8·재현율 53.3), OWLv2 단독 F1 60.9,
  결합(Qwen 상자는 모두 두고, 어떤 Qwen 상자와도 IoU 0.3 미만인 OWLv2 상자만 더함, OWLv2 문턱 0.1) F1 69.3·재현율 81.5.
이 모듈은 그 결합 규칙만 담는다. 모델 호출은 하지 않는다.
  - Qwen 객체 목록: adapters/team_a04_a05.py OpenAICompatibleVisionModel.list_objects
  - OWLv2 상자: backend/app/poster/objects.py(RunPod 로고 워커 kind="poster_objects" 또는 같은 프로세스)

후보는 판정이 아니다. A04/A05 모델에 '확인할 곳'으로만 넘기고, 판정은 모델이 이미지를 보고 한다.
좌표는 모두 모델 이미지(EXIF 회전을 반영한 화면 방향) 기준 0~1 정규화 [x1, y1, x2, y2]다.

설정(서버 환경변수):
  A04_VISUAL_OBJECTS            auto(기본) | local | off
                                auto  = VIDEO_LOGO_WORKER_URL이 있으면 RunPod 워커로 OWLv2를 돌리고, 없으면 OWLv2는 건너뛴다
                                local = 이 프로세스에서 OWLv2를 올린다(GPU Pod 안에서 백엔드를 띄운 경우)
                                off   = 객체 후보를 만들지 않는다(이전 동작)
  A04_VISUAL_OBJECT_THRESHOLD   OWLv2 점수 문턱, 기본 0.1(시험의 결합 문턱. 틀린 상자를 줄이려면 0.15~0.2)
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import os

# 시험의 '기본 어휘 37개'(person, logo, building, tree, food, lantern 등) 원본 목록은 아직 받지 못했다.
# 아래는 시험 정답의 13개 분류와 보고서에 적힌 예시 단어로 만든 잠정 목록이다. 원본을 받으면 이 목록을 바꾼다.
OWL_VOCABULARY = (
    "person", "face", "animal", "bird", "plant", "tree", "flower", "food", "drink", "fruit",
    "building", "bridge", "tower", "vehicle", "car", "boat", "musical instrument", "drum",
    "cartoon character", "mascot", "logo", "emblem", "QR code", "icon", "lantern", "balloon",
    "tent", "firework", "decoration", "star", "heart", "map", "mountain", "sun", "moon", "cup", "bottle",
)
OWL_THRESHOLD = 0.1
OWL_NMS_IOU = 0.5
OWL_MAX_BOXES = 100
COMBINE_IOU = 0.3          # 어떤 Qwen 상자와도 이 값 미만으로 겹치는 OWLv2 상자만 더한다
MAX_OBJECTS = 80           # A04/A05 요청에 싣는 후보 상한(요청 길이 제한)
CATEGORIES = ("person", "animal", "plant", "food", "building", "vehicle", "object", "instrument",
              "character", "logo", "qr_code", "icon", "decoration")
MODES = ("auto", "local", "off")


def mode(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    value = environ.get("A04_VISUAL_OBJECTS", "").strip().lower() or "auto"
    return value if value in MODES else "off"


def threshold(environ: Mapping[str, str] | None = None) -> float:
    environ = os.environ if environ is None else environ
    try:
        value = float(environ.get("A04_VISUAL_OBJECT_THRESHOLD", "") or OWL_THRESHOLD)
    except ValueError:
        return OWL_THRESHOLD
    return value if math.isfinite(value) and 0 < value < 1 else OWL_THRESHOLD


def normalized_box(values) -> tuple[float, float, float, float] | None:
    """0~1 [x1, y1, x2, y2]로 쓸 수 있는 상자만 돌려준다. 경계의 소수점 오차만 잘라 준다."""
    try:
        if len(values) != 4:
            return None
        box = []
        for value in values:
            value = float(value)
            if not math.isfinite(value) or not -1e-6 <= value <= 1 + 1e-6:
                return None
            box.append(min(max(value, 0.0), 1.0))
    except (TypeError, ValueError):
        return None
    x1, y1, x2, y2 = box
    return (x1, y1, x2, y2) if x1 < x2 and y1 < y2 else None


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def combine(qwen: Sequence[Mapping], owl: Sequence[Mapping], *, limit: float = COMBINE_IOU,
            maximum: int = MAX_OBJECTS) -> list[dict]:
    """시험의 결합 규칙: Qwen 후보는 모두 두고, 어떤 Qwen 후보와도 IoU limit 미만인 OWLv2 후보만 점수 순으로 더한다.

    qwen 항목: {"label", "category", "bbox"}  owl 항목: {"label", "score", "bbox"}  (bbox는 정규화 x1,y1,x2,y2)
    돌려주는 항목: {"object_id", "label", "category"?, "source": "qwen"|"owlv2", "score"?, "bbox"}
    """
    kept: list[dict] = []
    for item in qwen:
        box = normalized_box(item.get("bbox"))
        label = str(item.get("label") or "").strip()[:80]
        if box is None or not label:
            continue
        kept.append({"label": label, "category": item.get("category"), "source": "qwen", "bbox": box})
    qwen_boxes = [item["bbox"] for item in kept]
    extra = []
    for item in owl:
        box = normalized_box(item.get("bbox"))
        if box is None:
            continue
        if all(iou(box, other) < limit for other in qwen_boxes):
            extra.append({"label": str(item.get("label") or "")[:80], "source": "owlv2",
                          "score": round(float(item.get("score") or 0.0), 3), "bbox": box})
    extra.sort(key=lambda item: -item["score"])
    merged = (kept + extra)[:maximum]
    for index, item in enumerate(merged, start=1):
        item["object_id"] = f"o{index}"
        item["bbox"] = [round(value, 4) for value in item["bbox"]]
        if item.get("category") is None:
            item.pop("category", None)
    return merged


def owl_to_normalized(boxes: Sequence[Mapping], width: int, height: int) -> list[dict]:
    """워커가 돌려준 픽셀 상자를 모델 이미지 기준 정규화 상자로 바꾼다. 잘못된 상자는 버린다."""
    out = []
    for item in boxes:
        try:
            x1, y1, x2, y2 = (float(v) for v in item["bbox"])
        except (KeyError, TypeError, ValueError):
            continue
        box = normalized_box((x1 / width, y1 / height, x2 / width, y2 / height))
        if box is not None:
            out.append({"label": item.get("label"), "score": item.get("score"), "bbox": box})
    return out
