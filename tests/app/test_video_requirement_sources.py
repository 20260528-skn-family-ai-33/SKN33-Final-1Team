"""영상 요구사항의 원문 인용과 표 행, 비동기 처리 결과를 검증한다. 외부 모델은 호출하지 않는다."""
import io
import json
import zipfile
from hashlib import sha256
from threading import Lock
from types import SimpleNamespace

import httpx
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


def extract_with_test_model(data, filename, choose):
    def respond(request):
        body = json.loads(request.content)
        blocks = json.loads(body["messages"][1]["content"])["sources"]
        reply = {"requirements": choose([line for block in blocks for line in block["lines"]])}
        return httpx.Response(200, json={"model": body["model"], "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(reply)}},
        ]})

    return requirements.extract(data, filename, route="runpod", transport=httpx.MockTransport(respond),
                                settings=requirements.Settings("https://test.invalid/v1/chat/completions", "test", None))


def title_requirement(line_id, item_id, end_line=None):
    return {"target": "screen_text", "label": "제목", "expected": "Festival Blue",
            "source": {"start_line": line_id, "end_line": end_line or line_id},
            "item_id": item_id, "required": True, "unresolved": False, "qualifiers": []}


def test_pdf_table_rows_keep_ids_with_wrapped_cells_and_cite_original_text():
    with pymupdf.open() as document:
        page = document.new_page()
        for y in (70, 95, 128, 161):
            page.draw_line((24, y), (510, y))
        for x in (24, 88, 200, 510):
            page.draw_line((x, 70), (x, 161))
        for x, y, text in [
            (30, 40, "Brief for the video"),
            (30, 86, "ID"), (94, 86, "Type"), (206, 86, "Requirement"),
            (30, 110, "R06"), (94, 110, "Subtitle"), (206, 110, "Show  the title as in R07"), (206, 122, "Festival Blue"),
            (30, 146, "R07"), (94, 146, "Caption"), (206, 146, "Show Friday"),
            (30, 191, "R09"), (30, 209, "Prose appendix"),
        ]:
            page.insert_text((x, y), text, fontsize=10)
        original = page.get_text("text", sort=False)
        data = document.tobytes()
    quote = "R06\nSubtitle\nShow  the title as in R07\nFestival Blue"
    assert quote in original

    def choose(lines):
        texts = [line["text"] for line in lines]
        assert "Brief for the video" in texts and texts[-2:] == ["R09", "Prose appendix"]
        row = next(line for line in lines if line["text"].startswith("R06"))
        neighbor = next(line for line in lines if line["text"].startswith("R07"))
        row_text = " ".join(row["text"].split())
        assert all(value in row_text for value in ("Subtitle", "Show the title", "Festival Blue"))
        assert "Show Friday" in neighbor["text"] and "Show Friday" not in row["text"]
        return [title_requirement(row["id"], "R06"), title_requirement(row["id"], "R07"),
                title_requirement(row["id"], "R07", neighbor["id"])]

    result = extract_with_test_model(data, "contract.pdf", choose)
    item, = result["items"]
    assert item["include"] and item["item_id"] == "R06"
    assert item["source"] == {"block": 1, "block_id": "p1-text", "quote": quote,
                              "source_hash": sha256(quote.encode()).hexdigest()}
    assert result["trace"]["sources"][0]["text_sha256"] == sha256(original.encode()).hexdigest()
    assert [(candidate["candidate_index"], candidate["code"]) for candidate in result["rejected_candidates"]] == [
        (2, "A02_ITEM_ID_UNGROUNDED"), (3, "A02_SOURCE_MISMATCH"),
    ]


def caption_contract(rows, headers=("ID / 수준", "항목", "요구 내용", "판정 기준")):
    columns = (20, 130, 260, 570, 950)
    with pymupdf.open() as document:
        page = document.new_page(width=980, height=600)
        bottom = 30 + 36 * (1 + len(rows))
        for x in columns:
            page.draw_line((x, 30), (x, bottom))
        for y in range(30, bottom + 1, 36):
            page.draw_line((columns[0], y), (columns[-1], y))
        for column, header in enumerate(headers):
            page.insert_text((columns[column] + 5, 44), header, fontsize=9, fontname="korea")
        for n, (number, level, label, expected, rule) in enumerate(rows):
            row_top = 66 + n * 36
            for column, text in enumerate((number, label, expected, rule)):
                page.insert_text((columns[column] + 5, row_top + 13), text, fontsize=9, fontname="korea")
                if column == 0:
                    page.insert_text((columns[0] + 5, row_top + 26), level, fontsize=9, fontname="korea")
        return document.tobytes()


def test_caption_table_recovers_original_wording_and_keeps_model_only_requirements():
    exact = "자막 글자가 일치해야 함."
    rows = [
        ("R01", "필수", "정보 자막", "여름 음악회", exact),
        ("R02", "필수", "제목 자막", "별빛 정원", exact),
        ("R03", "권장", "자막 가독성", "자막 글자 크기·대비·노출 시간이 읽기에 충분.",
         "사람이 가독성 검토. 정량 합격 점수로 사용하지 않음."),
        ("R04", "권장", "표기 일관성", "숫자·날짜·단위·영문 표기가 일관.", "표기 혼용 여부를 사람이 검토."),
        ("T01", "필수", "프레임 속도", "25 fps", "파일 정보를 확인."),
        ("R05", "필수", "안내 자막", "비상 안내", "마지막 화면 하단에 표시."),
    ]
    data = caption_contract(rows)

    def choose(lines):
        # Preserve the deployed Qwen wire format and the original numbered line anchors.
        assert all(set(line) == {"id", "text"} for line in lines)
        assert [line["id"] for line in lines] == [f"p1-text:L{n}" for n in (1, 2, 3, 4, 5, 10, 15, 20, 25, 30)]
        by_id = {line["text"].split()[0]: line["id"] for line in lines}

        def candidate(number, claimed_id, expected, target="screen_text"):
            return {**title_requirement(by_id[number], claimed_id), "expected": expected, "target": target}

        duplicate = candidate("R02", "R02", "별빛 정원")
        return [
            candidate("R02", "REQ01", "별빛 정원"),
            candidate("R02", "R02", "모델이 지어낸 문구"),
            duplicate, duplicate,
            candidate("T01", "T01", "25 fps", "video_spec"),
            candidate("R05", "R05", "비상 안내"),
        ]

    result = extract_with_test_model(data, "contract.pdf", choose)
    assert result["trace"]["prompt_sha256"] == "88bf7b9c56daa5577b6b8e5479f176e14524111a87ad119e3944f8d2d55b7dd0"
    assert {(item["candidate_index"], item["code"]) for item in result["rejected_candidates"]} == {
        (1, "A02_ITEM_ID_UNGROUNDED"), (2, "A02_EXPECTED_UNGROUNDED"),
    }
    assert len(result["items"]) == len(rows)
    by_id = {item["item_id"]: item for item in result["items"]}
    assert set(by_id) == {row[0] for row in rows}
    page_text = requirements.pdf_sources(data)[0].text
    for number, level, label, expected, rule in rows[:4]:
        item = by_id[number]
        assert item["label"] == label and item["required"] == (level == "필수") and item["include"]
        assert item["extraction_method"] == "pdf_table_cells" and item["qualifiers"] == [rule]
        if number in {"R01", "R02"}:
            assert item["target"] == "screen_text" and item["expected"] == expected
        else:
            assert item["target"] == "subtitle_quality" and not item["compare"]
        quote = item["source"]["quote"]
        assert quote in page_text and all(cell in quote for cell in (number, level, label, expected, rule))
        assert item["source"]["source_hash"] == sha256(quote.encode()).hexdigest()
    assert by_id["T01"]["target"] == "video_spec" and not by_id["T01"]["include"]
    assert by_id["R05"]["label"] == "제목" and by_id["R05"]["expected"] == "비상 안내"
    assert all("extraction_method" not in by_id[key] for key in ("T01", "R05"))
    assert result["counts"]["criteria"] == 5


@pytest.mark.parametrize(("headers", "wording", "rule"), [
    (("ID / 수준", "항목", "납품 또는 규격", "판정 기준"), "여름 음악회", "자막 글자가 일치해야 함."),
    (("ID / 수준", "항목", "요구 내용", "판정 기준"), "문구 미정", "자막 글자가 일치해야 함."),
])
def test_uncertain_table_wording_is_not_promoted_to_a_caption_without_model_evidence(headers, wording, rule):
    data = caption_contract([
        ("R01", "필수", "제목 자막", wording, rule),
        ("T01", "필수", "프레임 속도", "25 fps", "파일 정보를 확인."),
    ], headers=headers)

    def choose(lines):
        tech = next(line["id"] for line in lines if line["text"].startswith("T01"))
        return [{**title_requirement(tech, "T01"), "expected": "25 fps", "target": "video_spec"}]

    result = extract_with_test_model(data, "contract.pdf", choose)
    assert [item["item_id"] for item in result["items"]] == ["T01"] and result["criteria"] == []


def test_docx_table_and_prose_keep_their_own_lines():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as document:
        document.writestr("word/document.xml", """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
              <w:p><w:r><w:t>Brief for the video</w:t></w:r></w:p>
              <w:tbl><w:tr><w:tc><w:p><w:r><w:t>R06</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>Show Festival Blue</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
              <w:p><w:r><w:t>Prose appendix</w:t></w:r></w:p>
            </w:body></w:document>""")
    quote = "R06 | Show Festival Blue"

    def choose(lines):
        assert [line["text"] for line in lines] == ["Brief for the video", quote, "Prose appendix"]
        return [title_requirement(lines[1]["id"], "R06")]

    result = extract_with_test_model(stream.getvalue(), "contract.docx", choose)
    item, = result["items"]
    assert item["source"]["quote"] == quote and item["source"]["source_hash"] == sha256(quote.encode()).hexdigest()


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

    def extract(data, filename, route="default"):
        assert events == [202, "response-sent"] and route == "default"
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
