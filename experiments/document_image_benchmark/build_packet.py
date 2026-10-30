#!/usr/bin/env python3
"""Build a portable, answer-free document/image benchmark packet from local assets.

This command copies inputs and prepares draft labels; it never runs a model.
Existing output artifacts are refused. Gold labels stay outside packet/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


SCHEMA = "document-image-review-v1"
DEFAULT_SOURCE = Path("/Users/choejiheum/Documents/Codex/2026-09-25/x20-x20/outputs/모닝컵_가상납품검수_예제")
DATASETS = ("strawberry-latte-qa-v1", "croissant-qa-v2", "festival-qa-v1")
VERDICTS = {"충족": "pass", "미충족": "fail", "확인 필요": "review"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def build(source_root: Path, output_root: Path) -> dict:
    source_root = source_root.resolve(strict=True)
    output_root = output_root.resolve()
    packet_dir = output_root / "packet"
    gold_path = output_root / "reviewer" / "gold.json"
    inventory_path = output_root / "data_inventory.md"
    for target in (packet_dir, gold_path, inventory_path):
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite {target}; choose a new --output-root")

    # Stage all contents in memory and validate before writing any output file.
    public_files: dict[str, bytes] = {}
    cases, labels, source_audit = [], [], []

    def add_file(relative: str, data: bytes) -> dict:
        if relative in public_files and public_files[relative] != data:
            raise ValueError(f"Conflicting public file: {relative}")
        public_files[relative] = data
        return {"path": relative, "sha256": sha(data)}

    def copy_file(path: Path, relative: str) -> dict:
        data = path.read_bytes()
        return add_file(relative, data)

    def source_ref(ref: dict, quote: str, locator: str) -> dict:
        if quote not in public_files[ref["path"]].decode("utf-8"):
            raise ValueError(f"Source quote missing: {ref['path']} {locator}")
        return {**ref, "locator": locator, "quote": quote}

    def condition(cid: str, title: str, requirement: str, reference: dict, checks: list) -> dict:
        return {"id": cid, "title": title, "requirement": requirement,
                "level": "required", "source": reference,
                "approval_status": "draft", "checks": checks}

    def unsupported(reason: str) -> dict:
        return {"kind": "unsupported", "reason": reason}

    def label(case: dict, cid: str, verdict: str | None, provenance: dict) -> None:
        labels.append({"case_id": case["id"], "condition_id": cid,
                       "image_sha256": case["image"]["sha256"], "verdict": verdict,
                       "human_verified": False, "verified_by": None, "verified_at": None,
                       "provenance": provenance})

    # Original Morning Cup document and actual intake directory, without AI declaration
    # explanatory text, evaluator answers, or the non-delivered editable source.
    doc_path = source_root / "의뢰서_및_계약특약.md"
    doc = copy_file(doc_path, "sources/morningcup/request.md")
    intake = source_root / "01_납품접수"
    intake_files = sorted(p for p in intake.iterdir() if p.is_file() and not p.name.startswith("."))
    if len(intake_files) != 1 or intake_files[0].name != "모닝컵_딸기구름라떼_최종.png":
        raise ValueError("Morning Cup intake has changed; review the draft labels before rebuilding")
    image = copy_file(intake_files[0], "images/morningcup-original.png")
    if public_files[image["path"]][:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Morning Cup intake is no longer PNG")
    dimensions = struct.unpack(">II", public_files[image["path"]][16:24])
    if dimensions != (1080, 1350):
        raise ValueError("Morning Cup dimensions changed; draft labels need review")
    delivery = [{"name": intake_files[0].name, **image}]
    add_file("sources/morningcup/intake_manifest.json", canonical({
        "directory_role": "actual_delivery_intake", "complete": True, "files": delivery,
        "notes": "Hidden filesystem metadata is excluded. Only submitted files are listed."}))
    mc = {"id": "morningcup-original-001", "group_id": "morningcup-original-001",
          "image": image, "delivery_files": delivery, "delivery_inventory_complete": True,
          "conditions": []}
    specs = [
        ("MC01", "이미지 크기", "1080 × 1350 px",
         "- 규격: 1080 × 1350 px, PNG 최종본 및 수정 가능한 편집 원본", "line:9",
         [{"kind": "image_size", "expected": [1080, 1350]}]),
        ("MC02", "최종 PNG", "최종 광고 PNG를 납품한다.",
         "- 규격: 1080 × 1350 px, PNG 최종본 및 수정 가능한 편집 원본", "line:9",
         [{"kind": "image_format", "expected": "PNG"}]),
        ("MC03", "메뉴명", "메뉴명 딸기 구름 라떼를 정확하게 표시한다.",
         "- 납품물: 신메뉴 '딸기 구름 라떼' SNS 세로형 광고 1종", "line:8;line:22",
         [{"kind": "text_contains", "expected": "딸기 구름 라떼", "normalization": "space"}]),
        ("MC04", "브랜드명", "의뢰사 모닝컵의 브랜드명을 정확하게 표시한다.",
         "- 브랜드명과 메뉴명을 정확하게 표시한다.", "line:6;line:22",
         [unsupported("문서는 모닝컵으로 명시한다. 영문 표기의 승인된 별칭 목록이 없어 브랜드 동일성을 자동 확정할 수 없다.")]),
        ("MC05", "음료 가시성과 세로형 구성", "음료 제품이 한눈에 보이는 세로형 SNS 광고를 만든다.",
         "- 음료 제품이 한눈에 보이는 세로형 SNS 광고를 만든다.", "line:23",
         [{"kind": "visual_class", "label": "food_drink", "expected": True},
          unsupported("food_drink는 포괄적 존재 분류다. 지정 음료의 정체성, 시인성 및 전체 레이아웃을 입증하지 않는다.")]),
        ("MC06", "편집 가능한 원본 제출", "문구·레이아웃·삽입 이미지를 수정할 수 있는 편집 원본을 납품한다.",
         "3. 최종 광고 PNG와 함께 문구·레이아웃·삽입 이미지를 수정할 수 있는 편집 원본을 납품한다. 편집 원본은 연결된 이미지 파일도 포함하거나, 이미지 파일의 위치와 전달 여부를 명시한다.", "line:16",
         [{"kind": "file_present", "extensions": [".svg", ".psd", ".ai", ".xcf"], "role": "editable_source"},
          unsupported("확장자만으로 실제 편집 가능성을 확인할 수 없다. 허용 목록 밖의 편집 형식도 가능하므로 알 수 없는 납품 파일이 있으면 확인 필요다.")]),
        ("MC07", "연결 이미지 제출", "PNG와 수정 가능한 원본 및 연결 이미지 파일을 함께 납품한다.",
         "- PNG와 수정 가능한 원본 및 연결 이미지 파일을 함께 납품한다.", "line:24",
         [unsupported("문서에 연결 이미지의 파일명·역할 매핑이 없다. 전체 납품 파일 목록만으로 어느 파일이 연결 소재인지 자동 확정하지 않는다.")]),
        ("MC08", "AI 신고와 외부 소재 목록", "AI 사용 신고서와 외부 소재 목록을 제출한다.",
         "- AI 사용 신고서와 외부 소재 목록을 제출한다.", "line:25",
         [unsupported("문서의 확장자만으로 AI 신고서 및 외부 소재 목록을 식별하지 않는다. 신고 내용과 소재별 근거를 별도 확인해야 한다.")]),
    ]
    for cid, title, requirement, quote, locator, checks in specs:
        mc["conditions"].append(condition(cid, title, requirement, source_ref(doc, quote, locator), checks))
    cases.append(mc)
    evaluator_path = source_root / "검수사례_및_기대결과.md"
    evaluator_hash = sha(evaluator_path.read_bytes())
    mc_verdicts = {"MC01": "pass", "MC02": "pass", "MC03": "pass", "MC04": "review",
                   "MC05": "review", "MC06": "fail", "MC07": "fail", "MC08": "review"}
    for cid, verdict in mc_verdicts.items():
        if cid in ("MC06", "MC07"):
            provenance = {"kind": "existing_evaluator_draft", "source_path": str(evaluator_path),
                          "source_sha256": evaluator_hash, "locator": "lines:14-17",
                          "note": "기존 기대결과를 재사용. 독립 사람 검증 기록 없음."}
        elif cid in ("MC01", "MC02"):
            provenance = {"kind": "deterministic_file_metadata", "source_path": str(intake_files[0]),
                          "source_sha256": image["sha256"], "note": "PNG signature and IHDR dimensions; human verification remains false."}
        else:
            provenance = {"kind": "ai_draft_visual_review", "source_path": str(intake_files[0]),
                          "source_sha256": image["sha256"], "review_date": "2026-10-29",
                          "note": "에이전트 view_image 확인. 메뉴 문구 확인; 브랜드 별칭 및 세부 시인성은 미확정."}
        label(mc, cid, verdict, provenance)
    source_audit.extend([
        {"path": str(doc_path), "sha256": sha(doc_path.read_bytes()), "public_projection": doc["path"]},
        {"path": str(evaluator_path), "sha256": evaluator_hash, "public_projection": None}])

    for dataset in DATASETS:
        dataset_root = source_root / "output" / dataset
        raw_path = dataset_root / "inputs" / "criteria.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        # Remove answer-key references and normal/single-error generation descriptions.
        projection = {key: raw[key] for key in ("dataset", "purpose", "scope", "rules", "sample_conditions") if key in raw}
        reference = add_file(f"sources/{dataset}/criteria.json", canonical(projection))
        answer_path = dataset_root / "reviewer" / "answer-key.json"
        answers = json.loads(answer_path.read_text(encoding="utf-8"))
        source_audit.extend([
            {"path": str(raw_path), "sha256": sha(raw_path.read_bytes()), "public_projection": reference["path"]},
            {"path": str(answer_path), "sha256": sha(answer_path.read_bytes()), "public_projection": None}])
        for sample in answers["samples"]:
            sample_id = sample["id"]
            case_id = f"{dataset}-{sample_id}"
            image_ref = copy_file(dataset_root / sample["file"], f"images/{dataset}/{sample_id}.png")
            if image_ref["sha256"] != sample["sha256"]:
                raise ValueError(f"Image differs from existing answer key: {case_id}")
            case = {"id": case_id, "group_id": sample["group_id"], "image": image_ref,
                    "delivery_files": [{"name": f"{sample_id}.png", **image_ref}],
                    "delivery_inventory_complete": False, "conditions": []}
            for index, rule in enumerate(raw["rules"]):
                cid = rule["id"]
                resolved = raw.get("sample_conditions", {}).get(sample_id, {}).get(cid, rule["expected"])
                requirement = rule["check"] + "\n기대값: " + json.dumps(resolved, ensure_ascii=False)
                checks = []
                if isinstance(resolved, str) and (dataset == "festival-qa-v1" or cid in ("C01", "C02", "C03")):
                    norm = "time" if dataset == "festival-qa-v1" and cid == "C03" else "price" if "원" in resolved else "space"
                    checks.append({"kind": "text_contains", "expected": resolved, "normalization": norm})
                    checks.append(unsupported("문자열 존재 검사만으로 원문에 지정된 제목/배지/상하 위치, 역할, 표시 횟수를 확정할 수 없다."))
                else:
                    if cid == "C04" or dataset == "strawberry-latte-qa-v1":
                        checks.append({"kind": "visual_class", "label": "food_drink", "expected": True})
                    checks.append(unsupported("포괄적 객체 분류로 세부 상품, 큰/미니 그룹, 정확한 개수, 색상, 재질, 컵·접시 형태 및 위치를 판정할 수 없다."))
                item = condition(cid, rule["name"], requirement,
                                 source_ref(reference, rule["check"], f"/rules/{index}/check"), checks)
                item["resolved_expected"] = resolved
                if sample_id in raw.get("sample_conditions", {}) and cid in raw["sample_conditions"][sample_id]:
                    item["expected_locator"] = f"/sample_conditions/{sample_id}/{cid}"
                case["conditions"].append(item)
                label(case, cid, VERDICTS[sample["results"][cid]], {
                    "kind": "existing_ai_draft_answer_key", "source_path": str(answer_path),
                    "source_sha256": sha(answer_path.read_bytes()),
                    "locator": f"/samples/{answers['samples'].index(sample)}/results/{cid}",
                    "note": "Existing synthetic development-set labels; agent reviewed, no independent human verification."})
            cases.append(case)

    packet = {"schema": SCHEMA, "qualification": {
        "purpose": "document_image_condition_review_development_benchmark",
        "scope": "1 original virtual delivery case and 18 synthetic regression images; not a production accuracy estimate",
        "source_approval": "draft", "human_verified_labels": 0,
        "metric_status": "draft_agreement_only", "independent_evaluation": False,
        "group_count": len({case["group_id"] for case in cases}),
        "all_condition_checks": "AND; unsupported checks require review unless another check establishes a failure",
        "inventory_policy": "Only the original intake inventory is complete. QA input images are not complete delivery inventories."},
        "files": {name: sha(data) for name, data in sorted(public_files.items())}, "cases": cases}
    packet_bytes = canonical(packet)
    gold = {"schema": "document-image-gold-v1", "packet_sha256": sha(packet_bytes),
            "qualification": "Draft agreement against existing AI labels and virtual case expectations only.",
            "source_audit": source_audit, "labels": labels}
    if len(cases) != 19 or len(labels) != 98:
        raise ValueError(f"Unexpected counts: cases={len(cases)} labels={len(labels)}")
    ids = {(c["id"], item["id"]) for c in cases for item in c["conditions"]}
    if len(ids) != len(labels) or ids != {(x["case_id"], x["condition_id"]) for x in labels}:
        raise ValueError("Condition and label identities do not match")
    for name in public_files:
        if Path(name).is_absolute() or ".." in Path(name).parts or re.search(r"answer.key|reviewer|gold", name):
            raise ValueError(f"Unsafe public file path: {name}")

    inventory = """# 문서와 이미지 검수 입력 자료

19개 이미지 사례와 98개 조건을 준비했다. 모델 추론과 RunPod 실행은 수행하지 않았다.

| 자료 | 사례 | 조건 | 출처와 용도 |
|---|---:|---:|---|
| 모닝컵 기존 의뢰서와 실제 접수 PNG | 1 | 8 | 원래 가상 납품 사례. PNG 메타데이터와 제출 목록 확인 |
| 딸기라떼 QA v1 | 6 | 30 | 기존 합성 개발·시연 세트 |
| 크루아상 QA v2 | 6 | 30 | 기존 합성 개발·시연 세트. 샘플별 C04 큰/미니 수량 적용 |
| 페스티벌 QA v1 | 6 | 30 | 기존 합성 개발·시연 세트 |

독립 원본 그룹은 4개다. 모든 정답은 사람 확정이 없으므로 `human_verified:false`다. 수치는 개발용 초안 정답과의 일치율이며 운영 정확도나 독립 일반화 성능이 아니다.

`packet/`에는 입력 문서·조건·이미지·실제 접수 목록만 있다. `reviewer/gold.json`은 별도 평가용이며 모델 입력으로 전달하지 않는다. 원본 조건 JSON의 생성 구조와 정답 파일 안내는 공개 조건 투영에서 제외했다. 조건 문구, 기대값 및 크루아상 샘플별 기대 수량은 그대로 보존했다. 원본 파일 해시와 정답 출처는 private gold의 source_audit에 있다.

모닝컵 신고서의 의도적 누락 설명 및 평가자 기대결과는 공개 입력에서 제외했다. 브랜드 `모닝컵`의 영문 별칭은 승인되지 않았으며, 연결 이미지 파일명·역할 매핑도 원문에 없다. 연결 이미지 조건은 자동 판정을 보류한다. 원본 제출은 파일 확장자로 1차 확인하되 실제 편집 가능성은 별도 확인한다. 가격, 딸기 수량 등 원문에 없는 조건은 추가하지 않았다. 문서의 모든 계약 조항·법률 효력·소재 권리를 검사하는 데이터셋은 아니다.

김천 계약은 기존 20조건 JSON과 DOCX 내부 REF-01 이미지가 존재한다. 다만 reference_only인 미승인 계약 초안이며 실제 납품 사례가 아니므로 이번 기본 패킷에서 제외했다. 내장 이미지(720×1018, SHA-256 5c0a6e800d603c827a9e11f08c7604c1bb23bd60228bcd2ed23a48fa95828e8e)와 루트 포스터(800×1131)는 서로 다른 파일이다. 크루아상 v1은 v2와 동일 원본 그룹이라 중복을 줄이기 위해 제외했다.

`embedding-inputs`의 972개 이미지 후보는 명세서와 납품물의 정답쌍 972개가 아니다. 개발 명세서·서비스 요구사항·스키마는 납품 검수 기준으로 사용하지 않았다.

빌더는 기존 출력이 있으면 덮어쓰지 않는다. 다른 경로에 다시 빌드한 뒤 원본 해시와 패킷 해시를 비교할 수 있다. 이 문서는 입력 목록과 경계를 기록하며 모델 실행 완료를 의미하지 않는다.
"""
    for relative, data in sorted(public_files.items()):
        exclusive(packet_dir / relative, data)
    exclusive(packet_dir / "packet.json", packet_bytes)
    exclusive(gold_path, canonical(gold))
    exclusive(inventory_path, inventory.encode("utf-8"))
    # Read back the actual copied files, rather than trusting source-only checks.
    for relative, expected in packet["files"].items():
        if sha((packet_dir / relative).read_bytes()) != expected:
            raise ValueError(f"Copy readback mismatch: {relative}")
    return {"cases": len(cases), "conditions": len(labels), "public_files": len(public_files),
            "groups": packet["qualification"]["group_count"], "packet_sha256": sha(packet_bytes),
            "packet": str(packet_dir / "packet.json"), "gold": str(gold_path), "inventory": str(inventory_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    output = args.output_root or args.source_root / "output/document-image-review-2026-10-29-v1"
    print(json.dumps(build(args.source_root, output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
