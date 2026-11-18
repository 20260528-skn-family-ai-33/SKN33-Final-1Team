"""시각 기준 판정 근거 후보(Qwen 객체 목록 + OWLv2): 결합 규칙과 A04/A05 연결. 실제 모델·워커는 부르지 않는다."""
from __future__ import annotations

import json

import httpx
import pytest

from backend.agent_framework import visual_objects as V
from backend.agent_framework.adapters import team_a04_a05 as a04
from backend.agent_framework.invocation import invoke_role
from tests.agent_framework.test_team_a04_a05 import CRITERIA, chat_response, client, model_image, row, setup


@pytest.fixture(autouse=True)
def _no_worker(monkeypatch):
    for name in ("A04_VISUAL_OBJECTS", "A04_VISUAL_OBJECT_THRESHOLD", "VIDEO_LOGO_WORKER_URL"):
        monkeypatch.delenv(name, raising=False)


def test_combine_keeps_every_qwen_box_and_adds_only_owl_boxes_below_iou_limit():
    qwen = [{"label": "사람", "category": "person", "bbox": (0.1, 0.1, 0.3, 0.5)}]
    owl = [{"label": "person", "score": 0.4, "bbox": (0.11, 0.1, 0.31, 0.5)},       # 같은 사람(겹침 큼) → 버림
           {"label": "building", "score": 0.12, "bbox": (0.6, 0.1, 0.9, 0.4)},       # 겹치지 않음 → 더함
           {"label": "tree", "score": 0.3, "bbox": (0.25, 0.1, 0.45, 0.5)}]          # IoU 약 0.17 → 더함
    merged = V.combine(qwen, owl)
    assert [(m["object_id"], m["source"], m["label"]) for m in merged] == [
        ("o1", "qwen", "사람"), ("o2", "owlv2", "tree"), ("o3", "owlv2", "building")]
    assert merged[0]["category"] == "person" and "category" not in merged[1]


def test_combine_drops_invalid_boxes_and_respects_the_maximum():
    qwen = [{"label": "", "bbox": (0, 0, 1, 1)}, {"label": "x", "bbox": (0.5, 0.5, 0.4, 0.6)},
            {"label": "y", "bbox": (0, 0, 2, 1)}]
    owl = [{"label": "logo", "score": 0.2, "bbox": (i / 100, 0, i / 100 + 0.005, 0.01)} for i in range(90)]
    merged = V.combine(qwen, owl, maximum=5)
    assert len(merged) == 5 and all(m["source"] == "owlv2" for m in merged)


def test_owl_pixels_become_normalized_boxes():
    boxes = V.owl_to_normalized([{"label": "tree", "score": 0.2, "bbox": [20, 10, 100, 50]},
                                 {"label": "bad", "score": 0.2, "bbox": [100, 10, 20, 50]}], 200, 100)
    assert boxes == [{"label": "tree", "score": 0.2, "bbox": (0.1, 0.1, 0.5, 0.5)}]


@pytest.mark.parametrize(("value", "expected"), (("", "auto"), ("off", "off"), ("LOCAL", "local"), ("??", "off")))
def test_mode_defaults_to_auto_and_unknown_values_turn_it_off(value, expected):
    assert V.mode({"A04_VISUAL_OBJECTS": value}) == expected


@pytest.mark.parametrize(("value", "expected"), (("", 0.1), ("0.15", 0.15), ("1.5", 0.1), ("x", 0.1)))
def test_threshold_is_bounded(value, expected):
    assert V.threshold({"A04_VISUAL_OBJECT_THRESHOLD": value}) == expected


def objects_response(objects):
    content = json.dumps({"objects": objects}, ensure_ascii=False)
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}


def recording_server(objects, observations):
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if payload["response_format"]["json_schema"]["name"] == "PosterObjects":
            return httpx.Response(200, json=objects_response(objects))
        return httpx.Response(200, json=chat_response(observations))
    return seen, handler


ANSWERS = [row("title", "가을 문화축제", "pass", [0.1, 0.1, 0.9, 0.3]), row("marks", "3", "pass", [0.2, 0.4, 0.8, 0.6]),
           row("logo", "우측 하단 주최 로고", "pass", [0.7, 0.8, 0.95, 0.95])]


def hinted(payload):
    text = payload["messages"][1]["content"][0]["text"]
    if "VISUAL_OBJECTS_JSON:\n" not in text:
        return None
    return json.loads(text.split("VISUAL_OBJECTS_JSON:\n", 1)[1].split("\nCRITERIA_JSON:\n", 1)[0])


def test_visual_criteria_get_merged_qwen_and_owl_candidates_before_inspection():
    access, request = setup(size=(200, 100))
    seen, handler = recording_server([{"label": "주최 로고", "category": "logo", "bbox": [0.7, 0.8, 0.95, 0.95]}], ANSWERS)
    inspector = a04.TeamPosterInspector(client(handler), files=access)
    owl_calls = []

    def owl(picture):
        owl_calls.append(picture.size)
        return {"size": list(picture.size), "objects": [
            {"label": "logo", "score": 0.3, "bbox": [141, 81, 189, 95]},      # 같은 로고 → 버림
            {"label": "person", "score": 0.15, "bbox": [10, 10, 50, 90]}]}    # 새 후보 → 더함
    inspector.owl_detector = owl
    result = invoke_role("A04_A05", inspector, request)
    assert owl_calls == [(200, 100)]
    assert [p["response_format"]["json_schema"]["name"] for p in seen] == ["PosterObjects", "PosterInspection"]
    assert hinted(seen[1]) == [
        {"label": "주최 로고", "category": "logo", "source": "qwen", "object_id": "o1", "bbox": [0.7, 0.8, 0.95, 0.95]},
        {"label": "person", "source": "owlv2", "score": 0.15, "object_id": "o2", "bbox": [0.05, 0.1, 0.25, 0.9]}]
    assert {o.criterion_id: o.verdict for o in result.observations} == {"title": "pass", "marks": "pass", "logo": "pass"}


def test_candidates_use_the_box_format_the_model_was_told():
    model = client(lambda r: None, box_format="relative_1000")
    payload = model.build_payload(CRITERIA, {"type": "image_url", "image_url": {"url": "data:,"}}, 200, 100,
                                  [{"object_id": "o1", "label": "x", "source": "qwen", "bbox": [0.1, 0.2, 0.3, 0.4]}])
    assert hinted(payload)[0]["bbox"] == [100, 200, 300, 400]
    assert "integers 0..1000" in payload["messages"][1]["content"][0]["text"]


def test_no_visual_criterion_means_no_extra_calls():
    access, request = setup(size=(200, 100), criteria=CRITERIA[:2])
    seen, handler = recording_server([], ANSWERS[:2])
    inspector = a04.TeamPosterInspector(client(handler), files=access)
    inspector.owl_detector = lambda picture: pytest.fail("OWLv2 must not run without a visual criterion")
    invoke_role("A04_A05", inspector, request)
    assert [p["response_format"]["json_schema"]["name"] for p in seen] == ["PosterInspection"]


def test_switching_off_restores_the_previous_single_call(monkeypatch):
    monkeypatch.setenv("A04_VISUAL_OBJECTS", "off")
    access, request = setup(size=(200, 100))
    seen, handler = recording_server([], ANSWERS)
    inspector = a04.TeamPosterInspector(client(handler), files=access)
    inspector.owl_detector = lambda picture: pytest.fail("OWLv2 must not run when switched off")
    invoke_role("A04_A05", inspector, request)
    assert len(seen) == 1 and hinted(seen[0]) is None


def test_candidate_failures_never_block_the_inspection():
    access, request = setup(size=(200, 100))
    calls = []

    def handler(req):
        payload = json.loads(req.content)
        calls.append(payload["response_format"]["json_schema"]["name"])
        if payload["response_format"]["json_schema"]["name"] == "PosterObjects":
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]})
        return httpx.Response(200, json=chat_response(ANSWERS))
    inspector = a04.TeamPosterInspector(client(handler), files=access)

    def broken(picture):
        raise RuntimeError("worker down")
    inspector.owl_detector = broken
    result = invoke_role("A04_A05", inspector, request)
    assert calls == ["PosterObjects", "PosterInspection"] and len(result.observations) == 3


def test_owl_only_candidates_still_reach_the_model():
    access, request = setup(size=(200, 100))
    seen, handler = recording_server([], ANSWERS)
    inspector = a04.TeamPosterInspector(client(handler), files=access)
    inspector.owl_detector = lambda picture: {"objects": [{"label": "QR code", "score": 0.5, "bbox": [0, 0, 20, 20]}]}
    invoke_role("A04_A05", inspector, request)
    assert [item["label"] for item in hinted(seen[1])] == ["QR code"]


def test_without_a_worker_url_auto_mode_skips_owl(monkeypatch):
    from backend.app.poster import objects as O
    from PIL import Image
    assert O.request_objects(Image.new("RGB", (10, 10))) is None


def test_openai_route_model_never_builds_candidates():
    assert a04.OpenAIVisionModel.visual_objects is False and a04.OpenAICompatibleVisionModel.visual_objects is True


def test_qwen_object_list_drops_bad_boxes_and_reads_relative_coordinates():
    _, handler = recording_server([{"label": "탑", "category": "building", "bbox": [100, 200, 300, 400]},
                                   {"label": "깨진 상자", "category": "object", "bbox": [1, 2, 3]}], [])
    objects = client(handler, box_format="relative_1000").list_objects(model_image())
    assert objects == [{"label": "탑", "category": "building", "bbox": pytest.approx((0.1, 0.2, 0.3, 0.4))}]
