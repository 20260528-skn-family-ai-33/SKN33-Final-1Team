"""영상 요구사항 PDF: 글자층만 읽는다. 스캔 쪽은 거부하고 이미지가 섞인 쪽은 쪽 번호를 알린다. 모델 호출 없음."""
from threading import Lock
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.video import requirements
from backend.app.video.review import video_review_router


def pdf(*pages):
    with pymupdf.open() as document:
        for text, image in pages:
            page = document.new_page()
            if text:
                page.insert_text((30, 40), text)
            if image:
                page.insert_image(pymupdf.Rect(20, 60, 120, 160), stream=b"P6\n1 1\n255\n\xff\x00\x00")
        return document.tobytes()


def test_page_with_text_and_images_is_read_and_reported():
    data = pdf(("Brand name must be shown", False), ("Logo must stay on screen", True))
    kind, sources, image_pages = requirements._read(data, "brief.pdf")
    assert kind == "pdf" and image_pages == (2,)
    assert [source.block_id for source in sources] == ["p1-text", "p2-text"]
    assert "Logo must stay on screen" in sources[1].text
    assert requirements.read_sources(data, "brief.pdf") == (kind, sources)
    assert requirements.pdf_sources(data) == sources


def test_scanned_page_without_text_layer_is_still_rejected():
    with pytest.raises(requirements.DocumentError) as error:
        requirements.pdf_sources(pdf(("", True)))
    assert error.value.code == "PDF_TEXT_UNAVAILABLE"


def test_rejected_video_candidates_preserve_valid_items_and_sources():
    sources = requirements.pdf_sources(pdf(("R01: Festival", False)))
    index, _ = requirements.index_sources(sources)
    base = {"target": "screen_text", "label": "제목", "expected": "Festival",
            "source": {"start_line": "p1-text:L1", "end_line": "p1-text:L1"},
            "item_id": "R01", "required": True, "unresolved": False, "qualifiers": []}
    reply = requirements.VideoReply.model_validate({"requirements": [base, {**base, "item_id": "R99"}, base]})
    items, _, rejected = requirements.materialize(reply, index)
    assert [item["item_id"] for item in items] == ["R01"]
    assert [(row["candidate_index"], row["code"], row["source"]["block"]) for row in rejected] == [
        (2, "A02_ITEM_ID_UNGROUNDED", 1), (3, "A02_DUPLICATE_REQUIREMENT", 1),
    ]


@pytest.mark.parametrize("failed", [False, True])
def test_video_document_job_responds_before_analysis_and_scopes_results_to_owner(tmp_path, monkeypatch, failed):
    path = "/api/video-reviews/requirements/extractions"
    app = FastAPI()
    app.include_router(video_review_router(
        root=tmp_path, inference_lock=Lock(),
        authenticate=lambda request: (SimpleNamespace(user_id=request.headers["x-test-owner"]), False),
    ))
    result = {"items": [{"include": True, "text": "자막 확인"}], "approved": False}
    events = []

    def extract(data, filename):
        assert events == [202, "response-sent"]
        assert (data, filename) == (b"subtitle", "brief.txt")
        if failed:
            raise requirements.DocumentError("QWEN_OUTCOME_UNKNOWN", "모델 응답을 확인하지 못했습니다.", 504)
        return result

    async def trace_response(scope, receive, send):
        async def capture(message):
            await send(message)
            if scope.get("path") == path and scope.get("method") == "POST":
                if message["type"] == "http.response.start":
                    events.append(message["status"])
                elif message["type"] == "http.response.body" and not message.get("more_body"):
                    events.append("response-sent")

        await app(scope, receive, capture)

    monkeypatch.setattr(requirements, "extract", extract)
    with TestClient(trace_response, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        accepted = client.post(path, files={"document": ("brief.txt", b"subtitle")}, headers={"x-test-owner": "owner"})
        assert accepted.status_code == 202 and accepted.json()["status"] == "queued"
        assert accepted.headers["cache-control"] == "private, no-store"
        job_path = path + "/" + accepted.json()["id"]
        hidden = client.get(job_path, headers={"x-test-owner": "another-user"})
        assert hidden.status_code == 404
        response = client.get(job_path, headers={"x-test-owner": "owner"})
        assert response.headers["cache-control"] == "private, no-store"
        job = response.json()
        assert job["id"] == accepted.json()["id"]
        if failed:
            assert job["status"] == "failed" and job["error"]["code"] == "QWEN_OUTCOME_UNKNOWN"
        else:
            assert job["status"] == "completed" and job["result"] == result
