"""Offline processing contracts: real bytes, conservative decisions, bounded HTTP."""

import copy
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pymupdf
import pytest
from PIL import Image

from backend.creative_checker import documents, inspection, media, prompts, qwen
from backend.creative_checker.contracts import Criterion


def image_bytes(size=(120, 80), fmt="PNG"):
    stream = io.BytesIO()
    with Image.new("RGB", size, "#b22a31") as image:
        image.save(stream, fmt)
    return stream.getvalue()


def archive_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


def docx_bytes(body):
    return archive_bytes({
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "word/document.xml": f'<w:document xmlns:w="{documents.WORD_NS}"><w:body>{body}</w:body></w:document>',
    })


def criterion(**changes):
    value = {
        "id": str(uuid4()), "text": "100원 문구를 표시해야 합니다.", "category": "creative",
        "required": True, "kind": "amount", "expected": "100원", "target": "all", "asset_ids": [],
        "source": {"kind": "user", "block_id": None, "page": None, "quote": ""}, "supported": True, "notes": "",
    }
    return value | changes


def result(item, verdict="pass", observed=None, *, file_id="asset", frame_ms=None, **changes):
    observed = str(item["expected"]) if observed is None else observed
    value = {
        "criterion_id": item["id"], "execution_status": "completed", "verdict": verdict,
        "observed": observed, "expected": item["expected"], "reason": "실제 표시된 값과 기준을 대조했습니다.",
        "evidence": [{"file_id": file_id, "frame_ms": frame_ms, "bbox": [0.1, 0.1, 0.5, 0.5], "text": observed}], "limitations": [],
    }
    return value | changes


def envelope(content, *, usage=None):
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}}],
        "usage": usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def mock_client(monkeypatch, handler, *, max_calls=32):
    real_client = httpx.Client
    monkeypatch.setattr(qwen.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(qwen.time, "sleep", lambda _: None)
    return qwen.QwenClient("https://qwen.invalid/v1", "secret-sentinel", max_calls=max_calls)


def input_image(*, file_id="asset", frame_ms=None, **changes):
    return {"data": image_bytes(), "media_type": "image/png", "frame_ms": frame_ms, "file_id": file_id} | changes


def test_docx_extracts_paragraph_and_table_without_invented_pages():
    body = '<w:p><w:r><w:t>원문 문단</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>금액</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>100원</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    extracted = documents.extract_document(docx_bytes(body), "brief.docx")
    assert [block["text"] for block in extracted["blocks"]] == ["원문 문단", "금액\t100원"]
    assert extracted["blocks"][1]["table"]["rows"] == [["금액", "100원"]]
    assert all(block["page"] is None and "bbox" not in block for block in extracted["blocks"])
    assert extracted["metadata"]["page_count"] is None


def test_hwpx_extracts_actual_xml_table_in_numeric_section_order():
    paragraph = '<hp:p><hp:run><hp:t>섹션 원문</hp:t></hp:run></hp:p>'
    table = '<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:t>표 내용</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>'
    section = lambda body: f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">{body}</hs:sec>'
    data = archive_bytes({"mimetype": "application/hwp+zip", "Contents/content.hpf": "<package/>", "Contents/section10.xml": section(table), "Contents/section2.xml": section(paragraph)})
    extracted = documents.extract_document(data, "brief.hwpx")
    assert [block["text"] for block in extracted["blocks"]] == ["섹션 원문", "표 내용"]
    assert extracted["blocks"][1]["table"]["rows"] == [["표 내용"]]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/absolute", "word\\escape.xml"])
def test_document_rejects_unsafe_archive_paths(name):
    normalised = name.replace("\\", "/")
    data = archive_bytes({normalised: "x"})
    if normalised != name:
        # ZipInfo's writer normalizes Windows separators; preserve the actual hostile
        # central-directory/local-header bytes instead of testing a normalized fixture.
        data = data.replace(normalised.encode(), name.encode())
    with pytest.raises(documents.DocumentError) as error:
        documents.extract_document(data, "brief.docx")
    assert error.value.code == "unsafe_zip_path"


def test_document_rejects_actual_compressed_expansion_bomb():
    data = archive_bytes({"bomb": b"x" * (documents.MAX_DOCUMENT_BYTES + 1)})
    assert len(data) < 30_000
    with pytest.raises(documents.DocumentError) as error:
        documents.extract_document(data, "brief.docx")
    assert error.value.code == "expanded_size_limit"


def test_document_rejects_entity_expansion_and_wrong_format():
    xml = '<!DOCTYPE doc [<!ENTITY data "unsafe">]><doc>&data;</doc>'
    with pytest.raises(documents.DocumentError) as error:
        documents.extract_document(archive_bytes({"[Content_Types].xml": xml, "word/document.xml": xml}), "brief.docx")
    assert error.value.code == "invalid_xml"
    with pytest.raises(documents.DocumentError):
        documents.extract_document(b"not a zip", "brief.hwpx")
    with pytest.raises(documents.DocumentError) as error:
        documents.extract_document(b"\xd0\xcf\x11\xe0encrypted", "brief.docx")
    assert error.value.code == "encrypted_or_legacy_document"


def test_document_rejects_excess_text_without_truncation():
    with pytest.raises(documents.DocumentError) as error:
        documents.extract_document(docx_bytes('<w:p><w:r><w:t>' + "a" * 100_001 + '</w:t></w:r></w:p>'), "brief.docx")
    assert error.value.code == "character_limit"


def test_pdf_extracts_real_text_and_coordinates():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), "Original requirement 100 KRW")
        extracted = documents.extract_document(pdf.tobytes(), "brief.pdf")
    block = extracted["blocks"][0]
    assert block["text"] == "Original requirement 100 KRW" and block["page"] == 1
    assert block["bbox"][0] == 40 and len(block["bbox"]) == 4
    assert extracted["metadata"]["coordinate_space"] == "pdf_points_xyxy"


def test_pdf_rejects_scans_encryption_and_excess_pages():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_image(pymupdf.Rect(0, 0, 100, 100), stream=image_bytes())
        with pytest.raises(documents.DocumentError) as error:
            documents.extract_document(pdf.tobytes(), "scan.pdf")
        assert error.value.code == "scanned_pdf_unsupported"
        encrypted = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
        with pytest.raises(documents.DocumentError) as error:
            documents.extract_document(encrypted, "locked.pdf")
        assert error.value.code == "encrypted_document"
    with pymupdf.open() as pdf:
        for _ in range(51):
            pdf.new_page()
        with pytest.raises(documents.DocumentError) as error:
            documents.extract_document(pdf.tobytes(), "long.pdf")
        assert error.value.code == "page_limit"


def test_poster_prepares_full_and_three_mapped_rgb_crops(tmp_path):
    prepared = media.prepare_asset(image_bytes((1600, 2000)), "poster.png", "poster", tmp_path)
    assert [frame["role"] for frame in prepared["frames"]] == ["full", "crop", "crop", "crop"]
    assert prepared["coverage"]["complete"] is True
    assert prepared["frames"][0]["source_bbox"] == [0, 0, 1, 1]
    for frame in prepared["frames"]:
        with Image.open(frame["path"]) as image:
            assert image.mode == "RGB" and image.format == "JPEG" and max(image.size) <= 1280
        x, y, width, height = frame["source_bbox"]
        assert 0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
        assert frame["source_width"] == 1600 and frame["source_height"] == 2000


def test_poster_rejects_mime_mismatch_byte_and_pixel_limits():
    with pytest.raises(media.MediaError) as error:
        media.inspect_asset(image_bytes(), "renamed.jpg", "poster")
    assert error.value.code == "invalid_image_format"
    with pytest.raises(media.MediaError) as error:
        media.inspect_asset(b"x" * (media.MAX_IMAGE_BYTES + 1), "large.png", "poster")
    assert error.value.code == "image_size_limit"
    with pytest.raises(media.MediaError) as error:
        media.inspect_asset(image_bytes((5000, 2001)), "large.png", "poster")
    assert error.value.code == "image_pixel_limit"


@pytest.mark.parametrize("changes,code", [({"duration": "60.1"}, "video_duration_limit"), ({"width": 1921}, "video_resolution_limit"), ({"height": 1081}, "video_resolution_limit")])
def test_video_ffprobe_checks_actual_metadata(monkeypatch, changes, code):
    stream = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
    fmt = {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1"}
    stream.update({key: value for key, value in changes.items() if key != "duration"})
    fmt.update({key: value for key, value in changes.items() if key == "duration"})
    monkeypatch.setattr(media, "_binary", lambda _: "installed-ffprobe")
    monkeypatch.setattr(media, "_run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps({"streams": [stream], "format": fmt}).encode(), b""))
    with pytest.raises(media.MediaError) as error:
        media.inspect_asset(b"\x00\x00\x00\x18ftypisom0000", "video.mp4", "video")
    assert error.value.code == code


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="installed FFmpeg and FFprobe required")
def test_real_video_keeps_decode_timestamps_and_unobserved_coverage(tmp_path):
    source = tmp_path / "synthetic.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=160x90:r=4:d=1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True, timeout=20)
    prepared = media.prepare_asset(source.read_bytes(), "synthetic.mp4", "video", tmp_path / "derived")
    assert 1 <= len(prepared["frames"]) <= media.MAX_VIDEO_FRAMES
    assert prepared["coverage"]["complete"] is False and prepared["coverage"]["continuous_exposure_checked"] is False
    assert prepared["coverage"]["uninspected_intervals"]
    assert all(0 <= frame["frame_ms"] < 1000 and frame["frame_ms"] % 250 == 0 for frame in prepared["frames"])


@pytest.mark.parametrize("expected,observed,kind", [("100", "1100", "exact_text"), ("100원", "1100원", "amount"), ("10명", "110명", "quantity"), ("2026-10-30", "2026-10-31", "date"), ("100원", "100명", "amount")])
def test_rules_never_pass_substrings_different_dates_or_units(expected, observed, kind):
    item = criterion(kind=kind, expected=expected)
    checked = inspection.validate_results([item], [result(item, observed=observed)], {"asset"})[0]
    assert checked["verdict"] == "review" and checked["observed"] == observed and checked["expected"] == expected


@pytest.mark.parametrize("kind,expected,observed", [("date", "2026-10-30", "2026년 10월 30일"), ("amount", "1,000원", "1000원"), ("quantity", "10명", "10 명")])
def test_rules_allow_full_valid_date_or_quantity_equivalence(kind, expected, observed):
    item = criterion(kind=kind, expected=expected)
    assert inspection.validate_results([item], [result(item, observed=observed)], {"asset"})[0]["verdict"] == "pass"


def test_rules_no_absence_from_ocr_and_no_unregistered_evidence():
    item = criterion()
    absent = result(item, "fail", "", evidence=[], reason="텍스트 없음")
    assert inspection.validate_results([item], [absent], {"asset"})[0]["verdict"] == "review"
    assert inspection.validate_results([item], [result(item)], {"other-file"})[0]["verdict"] == "review"


@pytest.mark.parametrize("category", ["business", "other"])
def test_noncreative_conditions_are_preserved_for_human_inspection(category):
    item = criterion(category=category)
    checked = inspection.validate_results([item], [result(item)], {"asset"})[0]
    assert checked["verdict"] == "review" and checked["execution_status"] == "unsupported"
    assert checked["criterion_text"] == item["text"]


def test_required_fail_outranks_review_and_recommendations_do_not_block():
    first, second, recommended = criterion(), criterion(), criterion(required=False)
    fail = result(first, "fail", "101원", file_id="a")
    review = result(second, "review", file_id="b")
    assert inspection.aggregate_verdict([first, second], [fail, review]) == "fail"
    assert inspection.aggregate_verdict([first, recommended], [result(first), result(recommended, "fail", "101원")]) == "pass"
    assert inspection.aggregate_verdict([recommended], [result(recommended)]) == "review"
    assert inspection.aggregate_verdict([], []) == "review"


@pytest.mark.parametrize("operator,verdicts,expected", [("all", ("pass", "fail"), "fail"), ("any", ("pass", "fail"), "pass"), ("any", ("review", "fail"), "review"), ("any", ("fail", "fail"), "fail")])
def test_and_or_groups_have_explicit_three_valued_semantics(operator, verdicts, expected):
    items = [criterion(), criterion()]
    results = [result(item, verdict, "101원" if verdict == "fail" else "100원", file_id=str(index)) for index, (item, verdict) in enumerate(zip(items, verdicts))]
    groups = [{"id": str(uuid4()), "operator": operator, "criterion_ids": [item["id"] for item in items], "condition": None, "exception": None}]
    assert inspection.aggregate_verdict(items, results, groups) == expected
    groups[0]["exception"] = "예외가 적용될 수 있음"
    assert inspection.aggregate_verdict(items, results, groups) == "review"


def test_unknown_criterion_missing_required_and_logo_similarity_stay_review():
    item = criterion()
    unknown = result(criterion())
    assert inspection.aggregate_verdict([item], [result(item), unknown]) == "review"
    assert inspection.aggregate_verdict([item], []) == "review"
    logo = criterion(kind="logo", expected="승인 로고")
    observed = result(logo, observed="승인 로고", logo_reference_ids=["reference"], reason="유사도 0.99")
    assert inspection.validate_results([logo], [observed], {"asset", "reference"})[0]["verdict"] == "review"


def test_asset_scope_checks_current_membership_not_all_assets():
    first, second = str(uuid4()), str(uuid4())
    item = criterion(asset_ids=[first, second])
    observed = result(item, scope={"asset_kind": "poster", "asset_ids": [first], "complete": True})
    assert inspection.validate_results([item], [observed], {"asset"})[0]["verdict"] == "pass"
    observed["scope"]["asset_ids"] = [str(uuid4())]
    assert inspection.validate_results([item], [observed], {"asset"})[0]["verdict"] == "review"


def test_sampled_video_cannot_pass_but_definite_frame_fail_survives_revalidation():
    item = criterion(target="video")
    passed = result(item, file_id="frame0", frame_ms=0)
    failed = result(item, "fail", "101원", file_id="frame1", frame_ms=1000)
    assert inspection.validate_results([item], [passed], {"frame0"})[0]["verdict"] == "review"
    checked = inspection.validate_results([item], [passed, failed], {"frame0", "frame1"})
    assert checked[0]["verdict"] == "fail" and len(checked[0]["observations"]) == 2
    assert inspection.aggregate_verdict([item], checked) == "fail"


def test_conflicting_same_region_and_malformed_schema_do_not_become_definite():
    item = criterion()
    checked = inspection.validate_results([item], [result(item), result(item, "fail", "101원")], {"asset"})
    assert checked[0]["verdict"] == "review"
    malformed = result(item, verdict=["pass"], execution_status={"completed": True})
    assert inspection.validate_results([item], [malformed], {"asset"})[0]["verdict"] == "review"


def test_qwen_repairs_malformed_json_once_and_tracks_usage(monkeypatch):
    item, requests = criterion(), []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope("{bad" if len(requests) == 1 else {"results": [result(item)]}))
    with mock_client(monkeypatch, handler) as client:
        output = client.inspect([item], [input_image()])
        assert output["results"][0]["verdict"] == "pass"
        assert client.stats["calls"] == 2 and client.stats["format_repairs"] == 1
        assert client.stats["usage"] == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        assert client.stats["cost"] is None and "secret-sentinel" not in json.dumps(client.stats)
    assert requests[0]["model"] == "Qwen/Qwen3.5-4B" and requests[0]["response_format"]["json_schema"]["strict"] is True
    assert any(part["type"] == "image_url" for part in requests[0]["messages"][1]["content"])


def test_qwen_transient_network_retries_are_capped(monkeypatch):
    item, requests = criterion(), []
    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectError("private host secret-sentinel", request=request)
        return httpx.Response(200, json=envelope({"results": [result(item)]}))
    with mock_client(monkeypatch, handler, max_calls=4) as client:
        assert client.inspect([item], [input_image()])["results"][0]["verdict"] == "pass"
        assert client.stats["calls"] == 3 and client.stats["network_retries"] == 2 and client.stats["usage_complete"] is False
    assert len(requests) == 3


def test_qwen_persistent_transient_and_call_limits_fail_explicitly(monkeypatch):
    with mock_client(monkeypatch, lambda request: httpx.Response(503), max_calls=2) as client:
        with pytest.raises(qwen.QwenLimitError) as error:
            client.inspect([criterion()], [input_image()])
        assert client.stats["calls"] == 2 and "qwen.invalid" not in str(error.value)


@pytest.mark.parametrize("change", [{"file_id": "invented"}, {"frame_ms": 123}, {"bbox": [0.9, 0.1, 0.5, 0.5]}])
def test_qwen_rejects_fabricated_evidence_after_one_repair(monkeypatch, change):
    item = criterion()
    bad = result(item)
    bad["evidence"][0].update(change)
    with mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope({"results": [bad]}))) as client:
        with pytest.raises(qwen.QwenFormatError) as error:
            client.inspect([item], [input_image()])
        assert client.stats["calls"] == 2 and client.stats["format_repairs"] == 1
        assert "secret-sentinel" not in str(error.value) and "qwen.invalid" not in str(error.value)


def test_qwen_noncreative_partition_never_sends_business_claims(monkeypatch):
    creative, business, other = criterion(), criterion(category="business"), criterion(category="other")
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope({"results": [result(creative)]}))
    with mock_client(monkeypatch, handler) as client:
        output = client.inspect([creative, business, other], [input_image()])["results"]
        assert [item["criterion_id"] for item in output] == [creative["id"], business["id"], other["id"]]
        assert all(item["execution_status"] == "unsupported" and item["verdict"] == "review" for item in output[1:])
        assert client.stats["calls"] == 1
    text = requests[0]["messages"][1]["content"][0]["text"]
    assert business["id"] not in text and other["id"] not in text


def test_qwen_only_noncreative_requires_no_model_call(monkeypatch):
    def forbidden(request):
        pytest.fail("Noncreative criteria must never reach the model")
    with mock_client(monkeypatch, forbidden) as client:
        output = client.inspect([criterion(category="business")], [input_image()])
        assert output["results"][0]["execution_status"] == "unsupported" and client.stats["calls"] == 0


def test_qwen_poster_full_and_crops_share_one_real_request(monkeypatch):
    item, calls = criterion(), []
    images = [input_image(file_id=f"view-{index}", role="full" if index == 0 else "crop", source_bbox=[0, 0, 1, 1]) for index in range(4)]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=envelope({"results": [result(item, file_id="view-2")]}))
    with mock_client(monkeypatch, handler) as client:
        output = client.inspect([item], images)
        assert output["results"][0]["evidence"][0]["file_id"] == "view-2" and client.stats["calls"] == 1
    assert sum(part["type"] == "image_url" for part in calls[0]["messages"][1]["content"]) == 4


def test_qwen_extraction_100k_keeps_all_blocks_and_uuid_sources(monkeypatch):
    blocks = [{"id": f"b{index}", "page": None, "kind": "paragraph", "text": "100원" + "가" * (25_000 - len("100원"))} for index in range(4)]
    def handler(request):
        text = json.loads(request.content)["messages"][1]["content"][0]["text"]
        block = json.loads(text.split("INPUT_DATA_JSON:\n", 1)[1])["blocks"][0]
        item = criterion(id="local-id", text="100원", source={"kind": "document", "block_id": block["id"], "page": block["page"], "quote": "100원"})
        return httpx.Response(200, json=envelope({"items": [item], "groups": []}))
    with mock_client(monkeypatch, handler, max_calls=4) as client:
        extracted = client.extract_criteria(blocks)
        assert client.stats["calls"] == 4
    assert {item["source"]["block_id"] for item in extracted["items"]} == {block["id"] for block in blocks}
    assert len({UUID(item["id"]) for item in extracted["items"]}) == 4
    assert all(Criterion.model_validate(item) and item["supported"] is False for item in extracted["items"])


def test_qwen_extraction_rejects_invented_source_after_repair(monkeypatch):
    item = criterion(id="local", text="100원", source={"kind": "document", "block_id": "b1", "page": None, "quote": "invented"})
    with mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope({"items": [item], "groups": []}))) as client:
        with pytest.raises(qwen.QwenFormatError):
            client.extract_criteria([{"id": "b1", "page": None, "text": "100원"}])
        assert client.stats["calls"] == 2


def test_qwen_report_accepts_only_grounded_draft_and_summary(monkeypatch):
    checked = [result(criterion(), "review")]
    grounding = prompts.report_grounding(checked)
    response = {"summary": "\n".join(grounding["grounded_summary_lines"]), "change_request_draft": {"items": grounding["allowed_draft_items"], "message": grounding["draft_message"]}}
    with mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope(response))) as client:
        assert client.report(checked) == response


def test_qwen_report_rejects_new_claims_in_free_prose(monkeypatch):
    checked = [result(criterion(), "review")]
    response = {"summary": "확실하게 통과했습니다.", "change_request_draft": {"items": [], "message": ""}}
    with mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope(response))) as client:
        with pytest.raises(qwen.QwenFormatError):
            client.report(checked)
        assert client.stats["calls"] == 2


def test_qwen_rejects_input_model_logos_and_limits_before_http(monkeypatch):
    def forbidden(request):
        pytest.fail("Invalid input must not reach HTTP")
    with mock_client(monkeypatch, forbidden) as client:
        with pytest.raises(qwen.QwenInputError):
            client.inspect([criterion()], [input_image()], logos=[input_image(file_id=f"logo{index}") for index in range(5)])
        assert client.stats["calls"] == 0
    with pytest.raises(qwen.QwenInputError):
        qwen.QwenClient("https://user:secret-sentinel@qwen.invalid", "key")
    with pytest.raises(qwen.QwenInputError):
        qwen.QwenClient("https://qwen.invalid", "key", model="another-model")
