"""숏폼 6-A 근거 관계도 변환(result.json → 노드·관계). 모델·영상 없이 순수 함수만 본다."""
from __future__ import annotations

import pytest

from backend.app.video.disclosure import judge_disclosure
from backend.app.video.disclosure import validate_input
from backend.app.video.evidence_graph import GraphNotReady
from backend.app.video.evidence_graph import UnknownRule
from backend.app.video.evidence_graph import build_evidence_graph
from backend.app.video.frames import plan_times

DURATION = 12000
BOX = [100, 1500, 980, 1600]


def make_job(texts: dict[int, str], *, info=None, duration=DURATION, status="completed"):
    """texts: time_ms → 그 프레임에서 읽힌 문구. 파이프라인이 저장하는 result.json과 같은 모양."""
    info = validate_input(info or {"kind": "endorsement", "title": "신상 리뷰"})
    frames = [{**p, "ocr_status": "ok", "width": 1080, "height": 1920, "same_as": None} for p in plan_times(duration)]
    lines = [{"frame_id": f["frame_id"], "time_ms": f["time_ms"], "text": texts[f["time_ms"]], "bbox_xyxy": BOX}
             for f in frames if f["time_ms"] in texts]
    public = [{**f, "lines": [{"line_id": f"{line['frame_id']}:p0", "text": line["text"], "confidence": .97,
                               "bbox_xyxy": line["bbox_xyxy"], "role": "subtitle", "kept": True, "reason": ""}
                              for line in lines if line["frame_id"] == f["frame_id"]]} for f in frames]
    return {"id": "11111111-1111-4111-8111-111111111111", "status": status, "created_at": "2026-11-11T00:00:00Z",
            "runtime": {"pipeline": "test"}, "media": {"sha256": "ab" * 32, "duration_ms": duration},
            "disclosure_input": info, "frames": public,
            "disclosure": judge_disclosure(info, frames, lines, duration)}


def by_kind(graph, kind):
    return [node for node in graph["nodes"] if node["kind"] == kind]


def edge_kinds(graph):
    return sorted((edge["source"].split(":")[0], edge["kind"], edge["target"].split(":")[0]) for edge in graph["edges"])


def test_ambiguous_and_credit_match_the_design_mock():
    """12초 영상 · 5.0초 '협찬받았습니다'(모호) · 11.6초 '협찬: ○○'(제외) → R1 관계도 노드 11 · 관계 12."""
    graph = build_evidence_graph(make_job({5000: "협찬받았습니다", 11600: "협찬: 김천시"}), "R1")
    assert graph["answered_by"] == "result_json" and graph["warnings"] == [] and not graph["truncated"]
    assert graph["counts"]["nodes"] == 11 and graph["counts"]["edges"] == 12
    assert edge_kinds(graph) == sorted([
        ("run", "inspects", "video"), ("run", "judged", "verdict"),
        ("video", "has_frame", "frame"), ("video", "has_frame", "frame"),
        ("observation", "located_at", "frame"), ("observation", "located_at", "frame"),
        ("observation", "evidence", "verdict"), ("observation", "excluded", "verdict"),
        ("verdict", "applies_rule", "rule"), ("rule", "based_on", "guideline"),
        ("verdict", "absent", "phrase"), ("verdict", "review_requested", "human"),
    ])
    assert {edge["kind"] for edge in graph["edges"] if edge["dashed"]} == {"excluded", "absent", "review_requested"}
    observed = {node["phrase_class"]: node for node in by_kind(graph, "observation")}
    assert observed["ambiguous"]["label"] == "협찬받았습니다" and observed["excluded"]["count"] == 1
    assert observed["ambiguous"]["occurrences"] == [{"frame_id": "t005000", "time_ms": 5000, "bbox_xyxy": BOX}]
    [verdict] = graph["verdicts"]
    assert verdict["status"] == "review" and graph["disclosure"]["status"] == "review"
    # 근거 사슬: 영상 → 프레임 → 관측 → 판정 → 규칙 → 지침
    assert [node.split(":")[0] for node in verdict["chain"]] == ["video", "frame", "observation", "verdict", "rule", "guideline"]
    assert verdict["chain"][1] == "frame:t005000" and verdict["chain"][2] == observed["ambiguous"]["id"]
    ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])


def test_full_graph_covers_every_stored_rule_without_changing_verdicts():
    job = make_job({t: "#광고" for t in range(0, DURATION, 200)})
    graph = build_evidence_graph(job)
    assert [v["rule"] for v in graph["verdicts"]] == list(job["disclosure"]["rules"])
    assert {v["rule"]: v["status"] for v in graph["verdicts"]} == {
        name: rule["status"] for name, rule in job["disclosure"]["rules"].items()}
    # 같은 문구는 관측 하나로 묶고 위치는 상한까지만 싣는다
    [observation] = by_kind(graph, "observation")
    assert observation["count"] == job["disclosure"]["rules"]["R1"]["counts"]["allowed"]
    assert len(observation["occurrences"]) == 20 and observation["occurrences_truncated"]
    assert not by_kind(graph, "phrase_class")  # 허용 문구가 있으면 '0건' 노드는 없다
    targets = {edge["target"] for edge in graph["edges"] if edge["kind"] == "evidence"}
    assert {"verdict:R1", "verdict:R3a", "verdict:R3b", "verdict:R3c"} <= targets
    assert {node["field"] for node in by_kind(graph, "input")} == {"kind", "title", "caption"}
    internal = {v["rule"] for v in graph["verdicts"] if v["internal_standard"]}
    assert internal == {"R2", "R3a", "R3b", "R3c"}
    for name in job["disclosure"]["rules"]:  # 규칙별 조회는 전체 관계도의 부분집합
        part = build_evidence_graph(job, name)
        assert {n["id"] for n in part["nodes"]} <= {n["id"] for n in graph["nodes"]}
        assert all(name in edge["rules"] or edge["kind"] == "inspects" for edge in part["edges"])


def test_no_disclosure_at_all_is_violation_with_absent_phrase():
    graph = build_evidence_graph(make_job({}))
    assert graph["disclosure"]["status"] == "fail" and not by_kind(graph, "observation")
    r1 = next(v for v in graph["verdicts"] if v["rule"] == "R1")
    assert r1["chain"] == [f"run:{graph['job_id']}", "verdict:R1", "rule:R1", "guideline:ftc-endorsement"]
    absent = {edge["source"] for edge in graph["edges"] if edge["kind"] == "absent"}
    assert {"verdict:R1", "verdict:R3a", "verdict:R3b", "verdict:R3c"} == absent


def test_self_advertisement_has_no_verdicts():
    graph = build_evidence_graph(make_job({0: "#광고"}, info={"kind": "self"}))
    assert not graph["applicable"] and graph["verdicts"] == [] and graph["disclosure"]["status"] == "not_applicable"
    assert {node["kind"] for node in graph["nodes"]} == {"run", "video"}


def test_lexicon_change_is_reported_not_rejudged():
    job = make_job({5000: "협찬받았습니다"})
    job["disclosure"]["rules"]["R1"]["counts"]["ambiguous"] = 0  # 저장 당시 사전에서는 무관이었던 문구
    graph = build_evidence_graph(job, "R1")
    assert graph["warnings"] == ["PHRASE_LEXICON_CHANGED"] and graph["verdicts"][0]["status"] == "review"


def test_endpoint_serves_stored_results_without_ffmpeg_or_models(tmp_path):
    """저장된 result.json만 읽는 조회라 ffmpeg·OCR 없이도 답한다(영상 파이프라인 시험과 별개)."""
    import json
    import threading

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.video.review import video_review_router

    done, running = make_job({5000: "협찬받았습니다", 11600: "협찬: 김천시"}), make_job({}, status="running")
    running["id"] = "22222222-2222-4222-8222-222222222222"
    for job in (done, running):
        (tmp_path / job["id"]).mkdir()
        (tmp_path / job["id"] / "result.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    app = FastAPI()
    app.include_router(video_review_router(root=tmp_path, inference_lock=threading.Lock(), readonly=True))
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    url = f"/api/video-reviews/{done['id']}/evidence-graph"
    assert client.get(url, params={"rule": "R1"}).json() == build_evidence_graph(done, "R1")
    assert client.get(url).json()["counts"]["nodes"] > 11
    assert client.get(url, params={"rule": "R9"}).status_code == 404
    refused = client.get(f"/api/video-reviews/{running['id']}/evidence-graph")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "evidence_graph_not_ready"
    assert client.get("/api/video-reviews/33333333-3333-4333-8333-333333333333/evidence-graph").status_code == 404
    outsider = TestClient(app, base_url="http://127.0.0.1", client=("10.0.0.5", 12345))
    assert outsider.get(url).status_code == 404  # 기존 영상 API와 같은 접근 경계


def test_refuses_unfinished_jobs_and_unknown_rules():
    with pytest.raises(GraphNotReady):
        build_evidence_graph(make_job({}, status="running"))
    with pytest.raises(GraphNotReady):
        build_evidence_graph({"id": "x", "status": "completed"})  # 6-A가 없던 옛 기록
    with pytest.raises(UnknownRule):
        build_evidence_graph(make_job({}), "R9")
