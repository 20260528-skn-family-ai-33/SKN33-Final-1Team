"""Print saved SigLIP/Qwen development and test metrics, read-only."""

import json
import math
import sys
import unicodedata
from pathlib import Path


def read(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def value(number, percent=False):
    if not isinstance(number, (int, float)) or not math.isfinite(number):
        return "미기록"
    return f"{number * 100:.2f}%" if percent else f"{number:.4f}"


def table(title, rows):
    def width(text):
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    widths = [max(width(row[i]) for row in rows) for i in range(len(rows[0]))]
    separator = "-+-".join("-" * size for size in widths)
    print(f"\n===== {title} =====\n")
    for row in rows:
        print(" | ".join(text + " " * (widths[i] - width(text)) if i == 0
                         else " " * (widths[i] - width(text)) + text
                         for i, text in enumerate(row)))
        print(separator)


def training(name, folder):
    selected = read(folder / "selection.json", {}).get("selected", {})
    history = read(folder / "history.json", [])
    records = sorted((r for r in history if r["epoch"] > 0), key=lambda r: r["epoch"])
    if selected.get("epoch") == 0:
        records.insert(0, selected)
    if not records:
        print(f"\n{name}: 완료된 학습 기록이 없습니다. 경로: {folder}")
        return
    if selected and selected["epoch"] not in [r["epoch"] for r in records]:
        print(f"\n{name}: 선택 회차와 학습 기록이 일치하지 않습니다.")
        return
    headings = ["지표"]
    for record in records:
        epoch = record["epoch"]
        label = ("학습 전 조각" if selected.get("kind") == "tile" else "학습 전 원본") if epoch == 0 else f"{epoch}회차"
        headings.append(label + (" · 선택 모델" if epoch == selected.get("epoch") else ""))
    rows = [headings, ["학습 loss"] + [value(r.get("train_loss")) for r in records]]
    metrics = [{**r.get("dev_metrics", {}),
                "loss": r.get("dev_loss", r.get("dev_metrics", {}).get("loss"))} for r in records]
    for label, key, percent in [("개발셋 loss", "loss", False),
                                ("개발셋 정확도", "accuracy", True),
                                ("개발셋 Macro F1", "macro_f1", True)]:
        rows.append([label] + [value(m.get(key), percent) for m in metrics])
    table(f"{name} — 회차별 개발셋 결과", rows)
    if not selected:
        print("선택 미확정: selection.json이 없습니다.")


def main(root):
    siglip = root / "run-tiles-001"
    qwen = root / "run-qwen-tiles-001"
    training("SigLIP2 고해상도 조각 학습", siglip)
    training("Qwen 고해상도 조각 LoRA 학습", qwen)
    results = []
    for name, folder, keys in [
        ("SigLIP", siglip, [("기존", "baseline"), ("선택", "selected")]),
        ("Qwen", qwen, [("원본", "qwen_baseline"), ("선택", "qwen_selected")]),
    ]:
        saved = read(folder / "evaluation.json", {}).get("results", {})
        for suffix, key in keys:
            results.append((f"{name} {suffix}", saved.get(key, {})))
    rows = [["지표"] + [name for name, _ in results]]
    for label, key, percent in [("평가셋 loss", "loss", False),
                                ("평가셋 정확도", "accuracy", True),
                                ("평가셋 Macro F1", "macro_f1", True)]:
        rows.append([label] + [value(m.get(key), percent) for _, m in results])
    rows.append(["평가한 라벨 수"] + [str(m.get("known_cells", "미기록")) for _, m in results])
    table("저장된 최종 평가셋 결과", rows)
    print("회차별 표는 개발셋, 마지막 표는 최종 평가셋입니다.")
    print("모델별 loss 계산 방식이 달라 loss 크기만으로 두 모델의 우열을 판단하지 마세요.")
    print("미기록은 파일 또는 해당 지표가 없다는 뜻입니다.")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else
         Path("/workspace/highres-poster-20261028"))
