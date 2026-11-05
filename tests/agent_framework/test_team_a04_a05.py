"""A04/A05 adapter boundary with fake models and a mocked HTTP server; no real model call."""
from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json

import httpx
from PIL import Image, ImageOps
import pytest

from backend.agent_framework.adapters import team_a04_a05 as a04
from backend.agent_framework.composition import RoleBinding, TeamConfiguration, build_role
from backend.agent_framework.context import freeze_approved_context
from backend.agent_framework.contracts import AssetRef, Context, Criterion, InspectRequest, SourceRef, ValidateRequest
from backend.agent_framework.files import StoredFileMetadata, VerifiedFileAccess
from backend.agent_framework.invocation import invoke_role
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.validation import ReferenceValidator

ENVIRONMENT = ("CC_QWEN_BASE_URL", "CC_QWEN_MODEL", "CC_QWEN_API_KEY", "CC_QWEN_REVISION",
               "A04_A05_MODEL_BOX_FORMAT", "A04_A05_MODEL_TIMEOUT", "A04_A05_MODEL_MAX_PIXELS")
SERVER = "http://127.0.0.1:8001/v1"


def context():
    return Context(project_id="project-1", run_id="run-1", actor_id="reviewer-1",
                   document_version_id="document-v1", criteria_version_id="criteria-v1",
                   asset_version_id="poster-v1")


def criterion(criterion_id, kind, operator, expected, quote, unit=None):
    source = SourceRef(document_version_id="document-v1", page=1, block_id=f"block-{criterion_id}",
                       quote=quote, source_hash=sha256(quote.encode("utf-8")).hexdigest())
    return Criterion(criterion_id=criterion_id, text=quote, kind=kind, expected=expected,
                     operator=operator, unit=unit, source=source)


CRITERIA = (
    criterion("title", "text", "equals", "가을 문화축제", "행사명은 '가을 문화축제'로 표기한다."),
    criterion("marks", "number", "equals", "3", "초록 표시를 정확히 3개 넣는다."),
    criterion("logo", "visual", "present", "주최 로고", "주최 로고를 포함한다."),
)
FEE = criterion("fee", "number", "lte", "10000", "참가비는 10,000원 이하로 표기한다.", unit="원")


def png(image, orientation=None):
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    stream = BytesIO()
    image.save(stream, format="PNG", **options)
    return stream.getvalue()


class Catalog:
    def __init__(self, metadata):
        self.metadata = metadata

    def lookup_document(self, ctx, reference):
        raise AssertionError("A04/A05 must not read documents")

    def lookup_asset(self, ctx, reference):
        return self.metadata


class Reader:
    def __init__(self, payload):
        self.payload = payload

    def read(self, metadata, maximum):
        return self.payload[:maximum + 1]


def setup(payload=None, *, size=(40, 24), coordinate_space="display", criteria=CRITERIA):
    payload = png(Image.new("RGB", size, "white")) if payload is None else payload
    ctx = context()
    asset = AssetRef(file_id="file-poster-1", asset_version_id=ctx.asset_version_id,
                     sha256=sha256(payload).hexdigest(), width=size[0], height=size[1])
    metadata = StoredFileMetadata(file_id=asset.file_id, project_id=ctx.project_id,
        version_id=asset.asset_version_id, kind="poster", storage_key="project-1/poster.png",
        media_type="image/png", byte_size=len(payload), sha256=asset.sha256,
        width=size[0], height=size[1], coordinate_space=coordinate_space)
    request = InspectRequest(context=ctx, criteria=criteria, asset=asset,
                             evidence=freeze_approved_context(ctx, criteria))
    return VerifiedFileAccess(Catalog(metadata), Reader(payload)), request


class FakeModel:
    def __init__(self, *answers):
        self.answers, self.calls = answers, []

    def inspect(self, criteria, image):
        self.calls.append((criteria, image))
        return self.answers


def observed_answer(criterion_id, observed, verdict, bbox=None, **fields):
    return a04.ModelAnswer(criterion_id, observed, verdict, bbox, observation_status="observed", **fields)


def validate(request, inspection):
    return ReferenceValidator().validate(ValidateRequest(context=request.context, criteria=request.criteria,
        asset=request.asset, evidence=request.evidence, inspection=inspection))


def code_of(action):
    with pytest.raises(AdapterFailure) as failure:
        action()
    return failure.value.code


def test_model_answers_become_observations_with_request_owned_ids():
    access, request = setup()
    model = FakeModel(observed_answer("title", "가을 문화축제", "pass", (0.1, 0.1, 0.9, 0.3)),
                      observed_answer("marks", "3", "pass", (0.2, 0.4, 0.8, 0.6)),
                      observed_answer("logo", "우측 하단 주최 로고", "pass", (0.7, 0.8, 0.95, 0.95)))
    result = invoke_role("A04_A05", a04.TeamPosterInspector(model, files=access), request)
    citations = {evidence.criterion_ids[0]: evidence.evidence_id for evidence in request.evidence.evidence}
    assert result.context == request.context and not result.synthetic
    for observation in result.observations:
        assert observation.image.asset_version_id == request.asset.asset_version_id
        assert observation.image.asset_hash == request.asset.sha256
        assert observation.evidence_ids == (citations[observation.criterion_id],)
        assert (observation.observation_status, observation.observed_unit) == ("observed", None)
        assert observation.uncertainty is None
    criteria, image = model.calls[0]
    assert criteria == request.criteria
    assert (image.media_type, image.width, image.height) == ("image/png", 40, 24)
    assert validate(request, result).verdict == "pass"


def test_a06_judges_text_and_numbers_from_observed_values_not_model_verdicts():
    access, request = setup()
    result = invoke_role("A04_A05", a04.TeamPosterInspector(FakeModel(
        observed_answer("title", "가을 문화 축제", "pass", (0.1, 0.1, 0.9, 0.3)),
        observed_answer("marks", "3개", "pass", (0.2, 0.4, 0.8, 0.6)),
        observed_answer("logo", "주최 로고", "pass", (0.7, 0.8, 0.95, 0.95))), files=access), request)
    validated = validate(request, result)
    items = {item.criterion_id: item for item in validated.items}
    assert items["title"].verdict == "fail" and "value_mismatch" in items["title"].issues
    # "3개" parses as 3 with the printed unit 개, but this criterion declares no unit.
    assert items["marks"].verdict == "review" and "unit_mismatch" in items["marks"].issues
    assert validated.verdict == "fail"


@pytest.mark.parametrize(("value", "unit", "verdict", "issue"), (
    ("10,000원", None, "pass", None),
    ("10000", "원", "pass", None),
    ("12,000원", "원", "fail", "value_mismatch"),
    ("10,000", None, "review", "unit_mismatch"),
    ("10,000달러", None, "review", "unit_mismatch"),
    ("10,000원", "달러", "review", "numeric_observation_invalid"),
))
def test_numbers_keep_the_printed_value_and_unit(value, unit, verdict, issue):
    access, request = setup(criteria=(FEE,))
    result = invoke_role("A04_A05", a04.TeamPosterInspector(FakeModel(
        observed_answer("fee", value, "pass", (0.1, 0.7, 0.6, 0.8), observed_unit=unit)), files=access), request)
    [observation] = result.observations
    # The adapter passes the printed value and unit as they are; it never borrows the expected 원.
    assert (observation.observed, observation.observed_unit) == (value, unit)
    [item] = validate(request, result).items
    assert item.verdict == verdict and (issue in item.issues if issue else not item.issues)


@pytest.mark.parametrize(("answer", "verdict", "issue"), (
    (a04.ModelAnswer("title", "", "fail", observation_status="absent"), "fail", "observed_absence"),
    (a04.ModelAnswer("logo", "", "fail", observation_status="absent"), "fail", "observed_absence"),
    (a04.ModelAnswer("marks", "", "fail", observation_status="absent"), "review", "missing_observation"),
    (a04.ModelAnswer("title", "", "review", (0.1, 0.1, 0.9, 0.3), "글자가 번져 읽을 수 없습니다",
                     observation_status="unreadable"), "review", "unreadable_observation"),
    (a04.ModelAnswer("logo", "", "review", observation_status="not_checked"), "review", "observation_not_checked"),
    (a04.ModelAnswer("title", "가을 문화축제", "pass", (0.1, 0.1, 0.9, 0.3)), "review", "observation_status_unknown"),
))
def test_observation_status_reaches_a06_unchanged(answer, verdict, issue):
    access, request = setup()
    result = invoke_role("A04_A05", a04.TeamPosterInspector(FakeModel(answer), files=access), request)
    [observation] = result.observations
    # Only an observed pass/fail needs a location, so an absence keeps the model's own note.
    assert (observation.observation_status, observation.uncertainty) == (answer.observation_status, answer.uncertainty)
    items = {item.criterion_id: item for item in validate(request, result).items}
    assert items[answer.criterion_id].verdict == verdict and issue in items[answer.criterion_id].issues


@pytest.mark.parametrize("answers", (
    (a04.ModelAnswer("unknown", "x", "pass"),),
    (a04.ModelAnswer("title", "가을 문화축제", "review"), a04.ModelAnswer("title", "가을 문화축제", "review")),
    (a04.ModelAnswer("title", "가을 문화축제", "approved"),),
    (a04.ModelAnswer("title", "가을 문화축제", "review", uncertainty=3),),
    (a04.ModelAnswer("title", "가을 문화축제", "pass", observation_status="seen"),),
    (observed_answer("marks", "3", "pass", observed_unit=3),),
))
def test_model_output_outside_the_request_is_rejected(answers):
    access, request = setup()
    assert code_of(lambda: a04.TeamPosterInspector(FakeModel(*answers), files=access).inspect(request)) == "MODEL_OUTPUT_INVALID"


def test_missing_answers_locations_and_invalid_boxes_stay_reviewable():
    access, request = setup()
    result = invoke_role("A04_A05", a04.TeamPosterInspector(FakeModel(
        observed_answer("title", "가을 문화축제", "pass"),
        observed_answer("logo", "주최 로고", "pass", (0.9, 0.2, 0.4, 0.8))), files=access), request)
    observations = {observation.criterion_id: observation for observation in result.observations}
    assert set(observations) == {"title", "logo"}
    assert all(item.image.bbox is None and item.uncertainty for item in observations.values())
    items = {item.criterion_id: item for item in validate(request, result).items}
    assert {item.verdict for item in items.values()} == {"review"}
    assert "missing_observation" in items["marks"].issues

    relaxed = a04.TeamPosterInspector(FakeModel(observed_answer("title", "가을 문화축제", "pass")),
                                      files=access, require_location=False)
    assert invoke_role("A04_A05", relaxed, request).observations[0].uncertainty is None


@pytest.mark.parametrize("orientation", range(1, 9))
def test_display_boxes_map_back_to_encoded_pixels_for_every_exif_orientation(orientation):
    encoded = Image.new("L", (40, 24), 0)
    encoded.paste(255, (4, 6, 12, 10))
    with Image.open(BytesIO(png(encoded, orientation))) as source:
        display = ImageOps.exif_transpose(source)
    left, top, right, bottom = display.getbbox()
    shown = (left / display.width, top / display.height, right / display.width, bottom / display.height)
    mapped = a04.asset_box(shown, orientation=orientation, coordinate_space="encoded")
    assert mapped == pytest.approx((4 / 40, 6 / 24, 12 / 40, 10 / 24))
    assert a04.asset_box(shown, orientation=orientation, coordinate_space="display") == pytest.approx(shown)


@pytest.mark.parametrize("box", ((0.5, 0.1, 0.4, 0.9), (0.1, 0.1, 1.2, 0.9), (0.1, 0.1, 0.5), (0, 0, float("nan"), 1)))
def test_impossible_boxes_are_not_location_claims(box):
    assert a04.asset_box(box, orientation=1, coordinate_space="display") is None


def test_model_image_is_display_oriented_and_opaque():
    transparent = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    access, request = setup(png(transparent, orientation=6), size=(20, 40))
    poster = access.read_asset(request.context, request.asset)
    image = a04.prepare_image(poster)
    with Image.open(BytesIO(image.data)) as shown:
        assert (shown.mode, shown.size, shown.getpixel((0, 0))) == ("RGB", (20, 40), (255, 255, 255))
    assert (image.width, image.height) == (20, 40)


def test_factory_is_zero_argument_and_fails_closed_until_configured(monkeypatch):
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    config = TeamConfiguration(roles={
        "A02": RoleBinding(kind="unconfigured"),
        "A04_A05": RoleBinding(kind="factory", factory="backend.agent_framework.adapters.team_a04_a05:create_inspector"),
        "A06": RoleBinding(kind="reference"), "A07": RoleBinding(kind="unconfigured")})
    adapter = build_role(config, "A04_A05")
    assert isinstance(adapter, a04.TeamPosterInspector)
    _access, request = setup()
    assert code_of(lambda: invoke_role("A04_A05", adapter, request)) == "ADAPTER_NOT_CONFIGURED"
    # The shared server setting alone configures the model; files still need the run's binding.
    monkeypatch.setenv("CC_QWEN_BASE_URL", SERVER)
    assert code_of(lambda: invoke_role("A04_A05", adapter, request)) == "FILE_ACCESS_NOT_CONFIGURED"


def test_file_access_is_bound_per_run_through_the_context_variable():
    access, request = setup()
    inspector = a04.TeamPosterInspector(FakeModel(a04.ModelAnswer("title", "가을 문화축제", "pass", (0.1, 0.1, 0.9, 0.3))))
    assert code_of(lambda: inspector.inspect(request)) == "FILE_ACCESS_NOT_CONFIGURED"
    token = a04.FILE_ACCESS.set(access)
    try:
        result = invoke_role("A04_A05", inspector, request)
    finally:
        a04.FILE_ACCESS.reset(token)
    assert [observation.criterion_id for observation in result.observations] == ["title"]


def test_shared_qwen_settings_default_to_the_team_model_and_reject_unsafe_values():
    shared = a04.ModelSettings.from_environment({"CC_QWEN_BASE_URL": SERVER, "CC_QWEN_API_KEY": "test-key"})
    assert (shared.model, shared.revision, shared.origin) == ("Qwen/Qwen3.5-4B", "unverified", "environment")
    assert (shared.box_format, shared.timeout, shared.max_pixels) == ("normalized", 120.0, 2_000_000)
    assert "test-key" not in repr(shared) and "test-key" not in json.dumps(shared.describe())
    tuned = a04.ModelSettings.from_environment({
        "CC_QWEN_BASE_URL": SERVER, "CC_QWEN_MODEL": "Qwen/Qwen3.5-4B", "CC_QWEN_REVISION": "deployed-rev-1",
        "A04_A05_MODEL_BOX_FORMAT": "relative_1000", "A04_A05_MODEL_TIMEOUT": "90",
        "A04_A05_MODEL_MAX_PIXELS": "1500000"})
    assert tuned.describe() == {"model": "Qwen/Qwen3.5-4B", "revision": "deployed-rev-1",
                                "settings_origin": "environment", "box_format": "relative_1000", "timeout": 90.0,
                                "max_pixels": 1_500_000, "max_criteria_per_call": 32,
                                "prompt_version": a04.PROMPT_VERSION}
    assert a04.ModelSettings(base_url=SERVER, model="Qwen/Qwen3.5-4B").describe()["settings_origin"] == "code"
    assert a04.chat_completions_url(SERVER) == "http://127.0.0.1:8001/v1/chat/completions"
    assert a04.chat_completions_url("https://models.example.test") == "https://models.example.test/v1/chat/completions"
    base = {"CC_QWEN_BASE_URL": SERVER}
    for environ, code in (({}, "ADAPTER_NOT_CONFIGURED"), ({"CC_QWEN_MODEL": "Qwen/Qwen3.5-4B"}, "ADAPTER_NOT_CONFIGURED"),
                          # Removed per-role connection names never configure or mix with the shared server.
                          ({"A04_A05_MODEL_BASE_URL": SERVER, "A04_A05_MODEL_NAME": "m"}, "ADAPTER_NOT_CONFIGURED"),
                          ({"CC_QWEN_BASE_URL": "http://user:pw@host/v1"}, "ADAPTER_CONFIG_INVALID"),
                          ({**base, "CC_QWEN_REVISION": "bad\nrevision"}, "ADAPTER_CONFIG_INVALID"),
                          ({**base, "A04_A05_MODEL_BOX_FORMAT": "xywh"}, "ADAPTER_CONFIG_INVALID"),
                          ({**base, "A04_A05_MODEL_TIMEOUT": "0"}, "ADAPTER_CONFIG_INVALID"),
                          ({**base, "A04_A05_MODEL_MAX_PIXELS": "many"}, "ADAPTER_CONFIG_INVALID")):
        assert code_of(lambda: a04.ModelSettings.from_environment(environ)) == code


def chat_response(observations, finish_reason="stop"):
    content = json.dumps({"observations": observations}, ensure_ascii=False)
    return {"choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}]}


def row(criterion_id, observed, verdict, bbox=None, *, status="observed", unit=None, uncertainty=None):
    return {"criterion_id": criterion_id, "observation_status": status, "observed": observed, "observed_unit": unit,
            "verdict": verdict, "bbox": bbox, "uncertainty": uncertainty}


def model_image(width=200, height=100):
    return a04.ModelImage(png(Image.new("RGB", (width, height), "white")), "image/png", width, height)


def client(handler, **settings):
    return a04.OpenAICompatibleVisionModel(a04.ModelSettings(**{"base_url": SERVER, "model": "m", **settings}),
                                           transport=httpx.MockTransport(handler))


def sent_criteria(request):
    text = json.loads(request.content)["messages"][1]["content"][0]["text"]
    return [row["criterion_id"] for row in json.loads(text.split("CRITERIA_JSON:\n", 1)[1])]


def test_human_supplement_reaches_model_with_the_original_pending_source():
    from datetime import datetime, timezone
    from backend.agent_framework.contracts import CriteriaValueSource
    quote='관람시간은 10:00로 확정합니다.'
    supplement=CriteriaValueSource(record_id='reply-1',purpose='synthetic_test',reference='담당자 회신',quote=quote,
        quote_sha256=sha256(quote.encode()).hexdigest(),submitted_by='reviewer-1',submitted_at=datetime.now(timezone.utc),synthetic=True)
    c=criterion('time','text','contains','10:00','관람시간은 확인 중입니다.').model_copy(
        update={'human_added':True,'value_source':supplement})
    seen=[]
    def handler(request):
        text=json.loads(request.content)['messages'][1]['content'][0]['text']
        seen.extend(json.loads(text.split('CRITERIA_JSON:\n',1)[1]))
        return httpx.Response(200,json=chat_response([row('time','10:00','pass',None)]))
    client(handler).inspect((c,),model_image())
    assert seen[0]['expected']=='10:00' and seen[0]['text']==c.text
    assert seen[0]['supplement']=={'quote':quote,'reference':supplement.reference}


@pytest.mark.parametrize(("box_format", "raw_box"), (("normalized", [0.1, 0.2, 0.5, 0.6]),
                                                     ("relative_1000", [100, 200, 500, 600]),
                                                     ("pixel", [20, 20, 100, 60])))
def test_client_sends_one_image_with_a_strict_schema_and_normalizes_boxes(box_format, raw_box):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=chat_response([row("title", "가을 문화축제", "pass", raw_box)]))

    [answer] = client(handler, model="Qwen/Qwen3.5-4B", api_key="test-key",
                      box_format=box_format).inspect(CRITERIA[:1], model_image())
    assert (answer.criterion_id, answer.observed, answer.verdict, answer.uncertainty) == ("title", "가을 문화축제", "pass", None)
    assert (answer.observation_status, answer.observed_unit) == ("observed", None)
    assert answer.bbox == pytest.approx((0.1, 0.2, 0.5, 0.6))
    [request] = seen
    assert str(request.url) == "http://127.0.0.1:8001/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    payload = json.loads(request.content)
    assert (payload["model"], payload["temperature"]) == ("Qwen/Qwen3.5-4B", 0)
    assert payload["response_format"]["json_schema"]["strict"] is True
    answer_schema = payload["response_format"]["json_schema"]["schema"]["$defs"]["AnswerSchema"]
    assert {"observation_status", "observed_unit"} <= set(answer_schema["required"])
    content = payload["messages"][1]["content"]
    assert [part["type"] for part in content] == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert sent_criteria(request) == ["title"]


def test_client_downscales_to_its_pixel_budget_and_batches_criteria():
    seen = []

    def handler(request):
        seen.append(request)
        rows = [row(key, "", "review", status="unknown", uncertainty="보이지 않음") for key in sent_criteria(request)]
        return httpx.Response(200, json=chat_response(rows))

    answers = client(handler, max_pixels=5_000, max_criteria_per_call=2).inspect(CRITERIA, model_image(200, 100))
    assert [sent_criteria(request) for request in seen] == [["title", "marks"], ["logo"]]
    assert [answer.criterion_id for answer in answers] == ["title", "marks", "logo"]
    url = json.loads(seen[0].content)["messages"][1]["content"][1]["image_url"]["url"]
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as sent:
        assert sent.size == (100, 50)


@pytest.mark.parametrize(("response", "code", "retryable"), (
    (httpx.Response(503), "MODEL_HTTP_ERROR", True),
    (httpx.Response(400), "MODEL_HTTP_ERROR", False),
    (httpx.Response(200, json=chat_response([], finish_reason="length")), "MODEL_OUTPUT_INVALID", False),
    (httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}),
     "MODEL_OUTPUT_INVALID", False),
    (httpx.Response(200, json=chat_response([row("marks", "3", "pass")])), "MODEL_OUTPUT_INVALID", False),
    (httpx.Response(200, json=chat_response([row("title", "가을 문화축제", "pass", status="seen")])),
     "MODEL_OUTPUT_INVALID", False),
    (httpx.Response(200, json=chat_response([{"criterion_id": "title", "observed": "가을 문화축제", "verdict": "pass",
                                              "bbox": None, "uncertainty": None}])), "MODEL_OUTPUT_INVALID", False),
))
def test_client_failures_are_explicit_and_never_retried(response, code, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    with pytest.raises(AdapterFailure) as failure:
        client(handler).inspect(CRITERIA[:1], model_image())
    assert (failure.value.code, failure.value.retryable, failure.value.outcome_unknown) == (code, retryable, False)
    assert len(calls) == 1


@pytest.mark.parametrize(("error", "code", "unknown"), ((httpx.ConnectError("refused"), "MODEL_UNAVAILABLE", False),
                                                       (httpx.ReadTimeout("slow"), "MODEL_OUTCOME_UNKNOWN", True)))
def test_transport_errors_separate_unsent_requests_from_unknown_outcomes(error, code, unknown):
    def handler(request):
        raise error

    with pytest.raises(AdapterFailure) as failure:
        client(handler).inspect(CRITERIA[:1], model_image())
    assert (failure.value.code, failure.value.outcome_unknown) == (code, unknown)


@pytest.mark.parametrize('mode', ('at_limit', 'over_limit', 'http_error', 'read_error'))
def test_client_bounds_streaming_response_and_closes_it_without_retry(mode):
    content = json.dumps(chat_response([row('title', '가을 문화축제', 'pass')])).encode()
    consumed, calls, closed = [], [], []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            consumed.append(a04.MAX_RESPONSE_BYTES)
            yield content.ljust(a04.MAX_RESPONSE_BYTES, b' ')
            if mode == 'read_error':
                raise httpx.ReadTimeout('interrupted response')
            if mode == 'over_limit':
                consumed.append(1)
                yield b' '
                raise AssertionError('must stop reading as soon as the limit is exceeded')

        def close(self):
            closed.append(True)

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if mode == 'http_error' else 200, stream=Stream())

    if mode == 'at_limit':
        answer, = client(handler).inspect(CRITERIA[:1], model_image())
        assert answer.observed == '가을 문화축제'
    else:
        with pytest.raises(AdapterFailure) as failure:
            client(handler).inspect(CRITERIA[:1], model_image())
        code = {'over_limit': 'MODEL_OUTPUT_INVALID', 'http_error': 'MODEL_HTTP_ERROR', 'read_error': 'MODEL_OUTCOME_UNKNOWN'}[mode]
        assert failure.value.code == code
        assert failure.value.outcome_unknown == (mode == 'read_error')
        assert failure.value.retryable == (mode == 'http_error')
    assert len(calls) == 1 and closed == [True]
    assert consumed == ([] if mode == 'http_error' else [a04.MAX_RESPONSE_BYTES, 1] if mode == 'over_limit' else [a04.MAX_RESPONSE_BYTES])


def test_adapter_and_client_together_return_a_contract_inspection():
    access, request = setup(size=(200, 100))

    def handler(_request):
        return httpx.Response(200, json=chat_response([
            row("title", "가을 문화축제", "pass", [0.1, 0.1, 0.9, 0.3]),
            row("marks", "3", "pass", [0.2, 0.4, 0.8, 0.6]),
            row("logo", "", "review", status="unreadable", uncertainty="로고가 흐립니다")]))

    result = invoke_role("A04_A05", a04.TeamPosterInspector(client(handler), files=access), request)
    assert {observation.criterion_id: observation.observation_status for observation in result.observations} == {
        "title": "observed", "marks": "observed", "logo": "unreadable"}
    validated = validate(request, result)
    assert {item.criterion_id: item.verdict for item in validated.items} == {"title": "pass", "marks": "pass", "logo": "review"}
    assert validated.verdict == "review"
