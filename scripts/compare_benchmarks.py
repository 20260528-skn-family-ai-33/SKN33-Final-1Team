"""같은 이미지·라벨·반복 조건으로 측정한 OCR 경로들의 전후 결과를 비교한다."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.benchmark_ocr import summarize
from scripts.paths import artifact_path


def comparison_signature(path, report):
    """실제 입력·라벨 스냅샷이 같은 실행만 전후 비교에 포함한다."""
    rows = []
    for row in report["runs"]:
        label = artifact_path(path.parent, f"{row['case_id']}/label.snapshot.json")
        label_hash = hashlib.sha256(label.read_bytes()).hexdigest() if label.is_file() else None
        rows.append((row["case_id"], row["variant"], row["repeat"], row["source_sha256"], row["input_sha256"], label_hash))
    return report["split"], sorted(rows)


def compare(paths):
    """완료된 실행의 성능과 오판정을 비교하되 모델 채택을 자동 결정하지 않는다."""
    reports = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    if len(reports) < 2:
        raise ValueError("At least two reports required")
    signature = comparison_signature(*reports[0])
    rows = []
    for path, report in reports:
        if report["status"] not in {"completed", "failed"}:
            raise ValueError("Cannot compare a running report")
        if comparison_signature(path, report) != signature:
            raise ValueError("Source/input hashes, labels, split, variants or repetitions differ")
        for row in report["runs"]:
            if "evaluation" in row:
                # 초기 기준선 보고서에도 새로 추가한 오불충족 지표를 원래 판정에서 계산한다.
                row["evaluation"]["counts"]["false_fail"] = sum(
                    v["expected_status"] == "pass" and v["status"] == "fail" for v in row["evaluation"]["verdicts"])
        rows.append({"run_id": report["run_id"], "pipeline": report.get("pipeline", "baseline"),
                     "summary": summarize(report["runs"]),
                     "partial_refinements": sum(r.get("refinement_status") not in {None, "completed"} for r in report["runs"])})
    return rows


def render(rows):
    """정답 초안과 승인 결과를 구분한 전후 비교 Markdown을 만든다."""
    lines = ["# OCR 처리 경로 비교", "", "같은 입력 해시·라벨 스냅샷·반복 조건을 확인했습니다. 초안 수치는 확정 정확도가 아닙니다.", "",
             "| 경로 | 전처리 | 라벨 | 성공/실행 | 필드 일치 | 누락 | CER | 오통과 | 오불충족 | 추론 중앙값(s) |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        for group in row["summary"]:
            for status, metrics in group["metrics"].items():
                if not metrics["evaluated_runs"]:
                    continue
                seconds = group["median_inference_seconds"]
                duration = f"{seconds:.3f}" if seconds is not None else "—"
                cer = f"{metrics['core_cer']:.1%}" if metrics["core_cer"] is not None else "—"
                lines.append(f"| {row['pipeline']} | {group['variant']} | {status} | {group['succeeded']}/{group['runs']} | "
                             f"{metrics['exact_fields']}/{metrics['visible_fields']} | {metrics['missing_fields']} | {cer} | "
                             f"{metrics['false_pass']}/{metrics['expected_fail']} | {metrics['false_fail']}/{metrics['expected_pass']} | {duration} |")
    for row in rows:
        lines += ["", f"- `{row['pipeline']}` 실행: `{row['run_id']}`, 일부 재인식 중단/실패: {row['partial_refinements']}건"]
    lines += ["", "오통과는 위반 기준 중 pass, 오불충족은 정상 기준 중 fail입니다. 확인 필요 증가와 실제 인식 개선을 구분해 해석합니다."]
    return "\n".join(lines) + "\n"


def main():
    """저장된 보고서만 읽고 추가 모델 추론 없이 비교 결과를 출력한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        print(render(compare(args.reports)))
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
