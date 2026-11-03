"""3-1단계 · 자막 줄 분류 (subtitle / background / other).

OCR가 읽은 줄마다 "자막인가, 간판·메뉴판 같은 배경 글자인가, 기타인가"를 분류한다.
배경 글자가 자막 트랙에 섞이면 엉뚱한 문구가 판정 근거가 되므로 걸러 내되,
**재현율을 우선**한다 — '배경' 확률이 충분히 높을 때만 트랙 후보에서 빼고, 애매하면 남긴다.
뺀 줄도 지우지 않고 사유와 함께 결과에 남긴다(사람이 확인할 수 있게).

설계 방침(숏폼 검수 파이프라인 문서 3-1단계):
- 직접 학습하는 유일한 경량 모델. 표 형태의 특징을 쓰는 고전 ML이라 **CPU·로컬**이 제격이다.
- **학습**: scikit-learn 로지스틱 회귀(기준) · LightGBM(비교). GPU/런팟 불필요.
- **추론(worker)**: 로지스틱 회귀 가중치를 JSON으로 내보내 **numpy만으로** 돌린다
  (worker에 sklearn을 넣지 않아도 됨 = "worker엔 가중치 파일만").
- 라벨이 쌓이기 전 첫 판은 원본의 '신뢰도 0.5 하한' 규칙으로 대체한다(rule_fallback).

입력  : ocr/frames.jsonl  — 프레임당 1줄. 아래 FrameRecord 스키마.
출력  : ocr/roles.jsonl   — 줄마다 1줄. 자막·배경·기타 확률 + kept/사유.

의존성:
  - 추론: numpy (이미 requirements.txt)
  - 학습: scikit-learn (필수, 미설치 시 안내) · lightgbm (선택, 비교용)
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np


CLASSES = ("subtitle", "background", "other")  # 자막 · 배경 · 기타
FEATURES = (
    "y_center",        # 세로 위치 (0=위, 1=아래)
    "x_center_offset", # 가로 중앙 정렬 정도 (0=정중앙)
    "height_ratio",    # 글자 높이 ÷ 프레임 높이
    "persistence",     # 같은 자리에서 이어지는 프레임 비율
    "stroke_contrast", # 외곽선·박스 배경 대비 (3단계 전처리, 없으면 0)
    "ocr_confidence",  # OCR 신뢰도
    "char_count_log",  # 글자 수 (log1p)
)
BG_DROP_THRESHOLD = 0.80   # '배경' 확률이 이 이상이고 최빈 클래스가 배경일 때만 후보에서 뺌
IOU_SAME = 0.50            # 같은 자리로 볼 bbox IoU
RULE_CONFIDENCE_FLOOR = 0.50  # 모델 없을 때 쓰는 신뢰도 하한


# ---------------------------------------------------------------------------
# 입력 스키마 (참고용 — jsonl은 아래 형태의 dict를 한 줄씩 담는다)
# ---------------------------------------------------------------------------
# FrameRecord = {
#   "video_id": "LvbDuR",              # (선택) 여러 영상을 한 파일에 담을 때 영상 구분
#   "frame_id": "t012000",
#   "time_ms": 12000,
#   "width": 1080, "height": 1920,
#   "lines": [
#     {"line_id": "t012000:paddle_003",
#      "text": "할인 50%",
#      "bbox_xyxy": [120, 1500, 960, 1600],
#      "confidence": 0.97,
#      "stroke_contrast": 0.8,        # (선택) 3단계가 넣어 주면 사용
#      "label": "subtitle"},          # (학습용에만) 정답 라벨
#     ...
#   ]
# }


def _normalized(text: str) -> str:
    """공백 제거 + NFC 정규화 — persistence 비교에서 같은 문구 판정용."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or ""))


def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(n,4)·(m,4) xyxy 상자의 IoU 행렬 (n, m). 면적 0인 쌍은 0."""
    ix = np.clip(np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1]), 0, None)
    inter = ix * iy
    area_a = np.clip(a[:, 2] - a[:, 0], 0, None) * np.clip(a[:, 3] - a[:, 1], 0, None)
    area_b = np.clip(b[:, 2] - b[:, 0], 0, None) * np.clip(b[:, 3] - b[:, 1], 0, None)
    union = area_a[:, None] + area_b[None, :] - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


# ---------------------------------------------------------------------------
# 특징 추출
# ---------------------------------------------------------------------------
def extract_lines(frames: list[dict]) -> list[dict]:
    """프레임 목록에서 줄 단위 레코드를 평탄화하고, 프레임을 가로지르는
    persistence(같은 자리·같은 문구가 이어지는 프레임 비율)까지 계산해 붙인다.

    persistence는 영상(video_id) 전체 프레임 기준이므로 추론도 영상 단위로 한 번에 넣어야 한다."""
    frames_per_video = Counter(fr.get("video_id") for fr in frames)
    lines: list[dict] = []
    for fi, fr in enumerate(frames):
        w = float(fr.get("width") or 1) or 1.0
        h = float(fr.get("height") or 1) or 1.0
        for ln in fr.get("lines", []):
            x1, y1, x2, y2 = (float(v) for v in ln["bbox_xyxy"])
            norm_bbox = [x1 / w, y1 / h, x2 / w, y2 / h]
            lines.append({
                "video_id": fr.get("video_id"),
                "frame_idx": fi,
                "frame_id": fr.get("frame_id"),
                "line_id": ln.get("line_id"),
                "text": ln.get("text", ""),
                "norm_text": _normalized(ln.get("text", "")),
                "norm_bbox": norm_bbox,
                "confidence": float(ln.get("confidence") or 0.0),
                "stroke_contrast": float(ln.get("stroke_contrast") or 0.0),
                "label": ln.get("label"),
            })

    # persistence: 같은 영상·같은 norm_text + IoU>=IOU_SAME 로 이어지는 '서로 다른 프레임' 수 / 그 영상 프레임 수.
    # 영상·문구별로 묶어 비교 대상을 줄인다(전체 줄 쌍 비교는 긴 영상에서 너무 느림).
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for ln in lines:
        ln["persistence"] = 1 / frames_per_video[ln["video_id"]]  # 빈 문구는 자기 프레임만
        if ln["norm_text"]:
            buckets[(ln["video_id"], ln["norm_text"])].append(ln)
    for group in buckets.values():
        if len(group) < 2:
            continue
        boxes = np.asarray([ln["norm_bbox"] for ln in group], dtype=np.float64)
        frame_idx = np.asarray([ln["frame_idx"] for ln in group])
        n_frames = frames_per_video[group[0]["video_id"]]
        for start in range(0, len(group), 512):  # 큰 묶음도 메모리 512×k 안에서
            same = _iou_matrix(boxes[start:start + 512], boxes) >= IOU_SAME
            for row, ln in enumerate(group[start:start + 512]):
                matched = set(frame_idx[same[row]].tolist()) | {ln["frame_idx"]}
                ln["persistence"] = len(matched) / n_frames
    return lines


def feature_vector(line: dict) -> list[float]:
    nb = line["norm_bbox"]
    y_center = (nb[1] + nb[3]) / 2.0
    x_center_offset = abs((nb[0] + nb[2]) / 2.0 - 0.5)
    height_ratio = max(0.0, nb[3] - nb[1])
    char_count = len(_normalized(line["text"]))
    return [
        y_center,
        x_center_offset,
        height_ratio,
        line.get("persistence", 0.0),
        line.get("stroke_contrast", 0.0),
        line.get("confidence", 0.0),
        math.log1p(char_count),
    ]


def feature_matrix(lines: list[dict]) -> np.ndarray:
    return np.asarray([feature_vector(l) for l in lines], dtype=np.float64)


# ---------------------------------------------------------------------------
# 모델 가중치 (로지스틱 회귀 — numpy 추론용으로 직렬화)
# ---------------------------------------------------------------------------
@dataclass
class LogRegWeights:
    features: list[str]
    classes: list[str]
    mean: list[float]      # StandardScaler.mean_
    scale: list[float]     # StandardScaler.scale_
    coef: list[list[float]]     # (n_classes, n_features)
    intercept: list[float]      # (n_classes,)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        mean = np.asarray(self.mean)
        scale = np.asarray(self.scale)
        coef = np.asarray(self.coef)
        intercept = np.asarray(self.intercept)
        z = ((X - mean) / scale) @ coef.T + intercept   # (n, n_classes)
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LogRegWeights":
        weights = cls(**json.loads(Path(path).read_text(encoding="utf-8")))
        if tuple(weights.features) != FEATURES or tuple(weights.classes) != CLASSES:
            raise ValueError(f"가중치의 특징·클래스 구성이 현재 코드와 다릅니다(다시 학습 필요): {path}")
        return weights


# ---------------------------------------------------------------------------
# 학습 (scikit-learn) — 로지스틱 회귀 기준 + (선택) LightGBM 비교
# ---------------------------------------------------------------------------
def train(labeled_frames: list[dict], *, compare_lightgbm: bool = True) -> tuple[LogRegWeights, dict]:
    """라벨(label)이 달린 프레임으로 학습. 로지스틱 회귀 가중치와 평가 지표를 돌려준다.

    평가는 문서의 완료 기준을 따른다: **자막 재현율**이 떨어지지 않고 **배경 정밀도**가 오르는지.
    판단은 교차검증 지표(logreg_cv · lightgbm_cv)로 한다. logreg_train은 학습 데이터 그대로라 낙관적이다.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import confusion_matrix
        from sklearn.pipeline import make_pipeline
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "학습에는 scikit-learn이 필요합니다. 학습 전용 환경에서 설치하세요:\n"
            "    pip install scikit-learn\n"
            "(worker 추론은 numpy만으로 됩니다 — sklearn 불필요.)"
        ) from exc

    lines = extract_lines(labeled_frames)
    labeled = [l for l in lines if l.get("label") in CLASSES]
    if not labeled:
        raise SystemExit("라벨(label: subtitle/background/other)이 있는 줄이 없습니다.")

    X = feature_matrix(labeled)
    y = np.asarray([CLASSES.index(l["label"]) for l in labeled])
    if len(set(y.tolist())) < 2:
        raise SystemExit(f"학습에는 라벨이 2종 이상 필요합니다(현재: {CLASSES[int(y[0])]}만 있음).")
    groups = [l["video_id"] for l in labeled]

    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)

    # class_weight='balanced': 소수 클래스(자막)의 재현율을 보호
    clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    clf.fit(Xs, y)

    present = sorted(set(int(v) for v in y))
    coef, intercept = _dense_coef(clf, present, n_features=X.shape[1])

    weights = LogRegWeights(
        features=list(FEATURES),
        classes=list(CLASSES),
        mean=scaler.mean_.tolist(),
        scale=scaler.scale_.tolist(),
        coef=coef.tolist(),
        intercept=intercept.tolist(),
    )

    pred = weights.predict_proba(X).argmax(axis=1)
    metrics: dict = {
        "n_labeled": int(len(labeled)),
        "logreg_train": _report(y, pred),
        "confusion_matrix": confusion_matrix(y, pred, labels=list(range(len(CLASSES)))).tolist(),
        "logreg_cv": _cross_validated(
            make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced")),
            X, y, groups),
    }

    if compare_lightgbm:
        metrics["lightgbm_cv"] = _try_lightgbm(X, y, groups)

    return weights, metrics


def _report(y: np.ndarray, pred: np.ndarray) -> dict:
    from sklearn.metrics import classification_report
    return classification_report(y, pred, labels=list(range(len(CLASSES))),
                                 target_names=CLASSES, output_dict=True, zero_division=0)


def _cross_validated(model, X: np.ndarray, y: np.ndarray, groups: list) -> dict:
    """교차검증 예측으로 클래스별 지표를 낸다. 영상이 3개 이상이면 영상 단위로 나눠
    같은 자막이 학습·검증에 동시에 들어가지 않게 하고, 아니면 줄 단위 층화 분할(낙관적)로 대신한다."""
    from sklearn.model_selection import GroupKFold, StratifiedKFold, cross_val_predict

    n_videos = len(set(groups))
    if n_videos >= 3:
        cv, scheme, fit_groups = GroupKFold(n_splits=min(5, n_videos)), "video_group_kfold", groups
    else:
        counts = np.bincount(y)
        n_splits = int(min(5, counts[counts > 0].min()))
        if n_splits < 2:
            return {"note": "클래스당 표본이 적어 교차검증 생략"}
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=0)
        scheme, fit_groups = "line_stratified_kfold(영상 1~2개라 낙관적)", None
    try:
        pred = cross_val_predict(model, X, y, cv=cv, groups=fit_groups)
    except ValueError as exc:  # 어떤 분할에 클래스가 하나만 남는 경우 등
        return {"note": f"교차검증 실패: {exc}"}
    return {"scheme": scheme, "folds": cv.get_n_splits(), "report": _report(y, pred)}


def _dense_coef(clf, present: list[int], n_features: int):
    """sklearn LogisticRegression의 coef_/intercept_를 3클래스 밀집 (coef, intercept)로 맞춘다.

    학습에 없던 클래스는 아주 큰 음수 intercept로 눌러(softmax에서 사실상 0) 제외한다.
    2클래스만 있으면 sklearn은 (1, F) 이진 형태이므로, 양성=given logit·음성=0으로 복원한다
    (이진 로지스틱 P(pos)=sigmoid(z)와 정확히 일치. 음성을 -logit으로 두면 sigmoid(2z)가 되어
    확률이 과장되므로 0으로 둔다)."""
    SUPPRESS = -30.0
    coef = np.zeros((len(CLASSES), n_features))
    intercept = np.full(len(CLASSES), SUPPRESS)
    if clf.coef_.shape[0] == 1:  # 이진 분류: present=[neg, pos] (오름차순)
        neg, pos = present[0], present[1]
        coef[pos] = clf.coef_[0]
        intercept[pos] = float(clf.intercept_[0])
        coef[neg] = 0.0
        intercept[neg] = 0.0
    else:
        for row, cls_idx in enumerate(clf.classes_):
            coef[int(cls_idx)] = clf.coef_[row]
            intercept[int(cls_idx)] = float(clf.intercept_[row])
    return coef, intercept


def _try_lightgbm(X: np.ndarray, y: np.ndarray, groups: list) -> dict:
    """LightGBM은 비교(검증)용. 설치돼 있을 때만 로지스틱 회귀와 같은 분할로 교차검증한다."""
    try:
        import lightgbm as lgb
    except ImportError:
        return {"available": False, "note": "lightgbm 미설치 — 비교 생략"}
    model = lgb.LGBMClassifier(n_estimators=200, class_weight="balanced", verbosity=-1)
    return {"available": True, **_cross_validated(model, X, y, groups)}


# ---------------------------------------------------------------------------
# 추론 (numpy only)
# ---------------------------------------------------------------------------
def classify(frames: list[dict], weights: LogRegWeights | None) -> list[dict]:
    """줄마다 역할 레코드를 만든다. 모델이 없으면 신뢰도 0.5 하한 규칙으로 대체.
    persistence 때문에 한 영상의 프레임을 일부만 잘라 넣으면 안 된다(전부 한 번에)."""
    lines = extract_lines(frames)
    if not lines:
        return []
    if weights is None:
        return [_rule_fallback(l) for l in lines]

    proba = weights.predict_proba(feature_matrix(lines))
    out: list[dict] = []
    for l, p in zip(lines, proba):
        probs = {c: round(float(v), 4) for c, v in zip(CLASSES, p)}
        top = int(np.argmax(p))
        drop = (top == CLASSES.index("background") and probs["background"] >= BG_DROP_THRESHOLD)
        out.append({
            "frame_id": l["frame_id"],
            "line_id": l["line_id"],
            "text": l["text"],
            "role": CLASSES[top],
            "probs": probs,
            "kept": not drop,  # 재현율 우선: 확실한 배경만 제외, 애매하면 남김
            "reason": "배경 글자로 제외" if drop else "트랙 후보 유지",
        })
    return out


def _rule_fallback(line: dict) -> dict:
    """모델 없을 때(첫 판): OCR 신뢰도 0.5 이상이면 자막 후보로 남긴다."""
    keep = line.get("confidence", 0.0) >= RULE_CONFIDENCE_FLOOR
    return {
        "frame_id": line["frame_id"],
        "line_id": line["line_id"],
        "text": line["text"],
        "role": "subtitle" if keep else "other",
        "probs": None,
        "kept": keep,
        "reason": "신뢰도 하한 통과" if keep else f"신뢰도 < {RULE_CONFIDENCE_FLOOR} 제외",
    }


# ---------------------------------------------------------------------------
# 파일 입출력
# ---------------------------------------------------------------------------
def _read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_jsonl(path: str | Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_predict(input_jsonl: str, output_jsonl: str, model_json: str | None) -> int:
    frames = _read_jsonl(input_jsonl)
    weights = LogRegWeights.load(model_json) if model_json else None
    rows = classify(frames, weights)
    _write_jsonl(output_jsonl, rows)
    return len(rows)


# ---------------------------------------------------------------------------
# 합성 데이터 (라벨이 아직 없을 때 end-to-end 동작 확인용)
# ---------------------------------------------------------------------------
def demo_frames(n_frames: int = 30, seed: int = 0) -> list[dict]:
    """자막(하단 중앙·큰 글자·오래 유지)과 배경(가장자리·작은 글자·짧게)을 섞은 합성 프레임."""
    rng = random.Random(seed)
    W, H = 1080, 1920
    frames = []
    for fi in range(n_frames):
        lines = []
        # 자막: 하단 중앙, 여러 프레임 유지
        lines.append(_demo_line(f"t{fi:06d}:sub", "할인 50% 2월 행사",
                                 x=rng.randint(180, 240), y=1500, w=720, h=110,
                                 conf=rng.uniform(0.9, 0.99), contrast=0.85, label="subtitle"))
        # 배경(간판): 상단/가장자리, 작게, 매 프레임 조금씩 이동
        lines.append(_demo_line(f"t{fi:06d}:bg", "○○식당",
                                 x=rng.randint(40, 900), y=rng.randint(100, 400),
                                 w=rng.randint(120, 220), h=rng.randint(40, 70),
                                 conf=rng.uniform(0.4, 0.8), contrast=0.2, label="background"))
        if rng.random() < 0.3:  # 기타: 저신뢰 파편
            lines.append(_demo_line(f"t{fi:06d}:oth", "·", x=rng.randint(0, 1000),
                                    y=rng.randint(0, 1900), w=30, h=30,
                                    conf=rng.uniform(0.1, 0.4), contrast=0.1, label="other"))
        frames.append({"frame_id": f"t{fi:06d}", "time_ms": fi * 1000,
                        "width": W, "height": H, "lines": lines})
    return frames


def _demo_line(line_id, text, x, y, w, h, conf, contrast, label):
    return {"line_id": line_id, "text": text, "bbox_xyxy": [x, y, x + w, y + h],
            "confidence": round(conf, 3), "stroke_contrast": contrast, "label": label}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="3-1 자막 줄 분류 (학습=sklearn / 추론=numpy)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train", help="라벨된 frames.jsonl로 학습 → 가중치 JSON 저장")
    p_train.add_argument("--labeled", required=True, help="라벨(label 필드) 포함 frames.jsonl")
    p_train.add_argument("--out", required=True, help="저장할 가중치 JSON 경로")
    p_train.add_argument("--no-lightgbm", action="store_true", help="LightGBM 비교 생략")

    p_pred = sub.add_parser("predict", help="frames.jsonl → roles.jsonl")
    p_pred.add_argument("--input", required=True, help="ocr/frames.jsonl")
    p_pred.add_argument("--output", required=True, help="ocr/roles.jsonl")
    p_pred.add_argument("--model", help="가중치 JSON (없으면 신뢰도 0.5 하한 규칙)")

    p_demo = sub.add_parser("demo", help="합성 데이터로 학습→추론 전체 흐름 확인")

    args = parser.parse_args(argv)

    if args.cmd == "train":
        frames = _read_jsonl(args.labeled)
        weights, metrics = train(frames, compare_lightgbm=not args.no_lightgbm)
        weights.save(args.out)
        print(f"가중치 저장: {args.out}")
        print(json.dumps(metrics, ensure_ascii=False, indent=2))

    elif args.cmd == "predict":
        n = run_predict(args.input, args.output, args.model)
        mode = "모델" if args.model else "규칙(신뢰도 0.5 하한)"
        print(f"{n}개 줄 분류({mode}) → {args.output}")

    elif args.cmd == "demo":
        frames = demo_frames()
        weights, metrics = train(frames)
        cv = metrics["logreg_cv"]
        if "report" in cv:
            print(f"[logreg 교차검증 · {cv['scheme']}] 자막 재현율:",
                  round(cv["report"]["subtitle"]["recall"], 3),
                  "| 배경 정밀도:", round(cv["report"]["background"]["precision"], 3))
        # 영상 전체를 넣고 분류한 뒤 첫 프레임만 보여 준다(persistence가 영상 전체 기준)
        roles = [r for r in classify(frames, weights) if r["frame_id"] == frames[0]["frame_id"]]
        print("예시 분류(첫 프레임):", json.dumps(roles, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
