"""scripts/collect_posters.py 의 변환·저장 로직 검증 (네트워크 호출 없음).

서울 API 응답 행은 공개 sample 키로 받은 실제 응답(2026-10-12)에서 필요한 필드만 옮겼다.
"""
import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('collect_posters', ROOT / 'scripts' / 'collect_posters.py')
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

SEOUL_ROW = {
    'CODENAME': '콘서트', 'GUNAME': '강동구',
    'TITLE': '2026 카즈미 타테이시 트리오 내한공연-크리스마스, 재즈를 만나다-(서울)',
    'DATE': '2026-12-24~2026-12-24', 'PLACE': '강동아트센터 대극장 한강', 'ORG_NAME': '기타',
    'USE_FEE': 'VIP석 88,000원 / R석 77,000원 / S석 66,000원 / A석 55,000원',
    'INQUIRY': '070-8680-8477 / 02-337-3103', 'ORG_LINK': 'https://tickets.interpark.com/goods/26010350',
    'MAIN_IMG': 'https://culture.seoul.go.kr/cmmn/file/getImage.do?atchFileId=401a984b98ff4af8a57122219ee0d591&thumb=Y',
    'STRTDATE': '2026-12-24 00:00:00.0', 'END_DATE': '2026-12-24 00:00:00.0', 'IS_FREE': '유료',
    'HMPG_ADDR': 'https://culture.seoul.go.kr/culture/culture/cultureEvent/view.do?cultcode=158770&menuNo=200008',
    'PRO_TIME': '19:30',
}


def image_bytes(fmt, size=(30, 40), mode='RGB'):
    buf = io.BytesIO()
    im = Image.new(mode, size)
    if fmt == 'GIF':
        im = im.convert('P')
    im.save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize('raw,expected', [
    ('2026-12-24 00:00:00.0', '2026-12-24'), ('20261009', '2026-10-09'), ('2026.10.09', '2026-10-09'),
    ('2026-02-30', None), ('', None), (None, None),
])
def test_norm_date(raw, expected):
    assert cp.norm_date(raw) == expected


@pytest.mark.parametrize('raw,expected', [('19:30', '19:30'), ('9:05', '09:05'), ('평일 10:00~18:00', None), ('24:00', None), (None, None)])
def test_norm_time_only_accepts_single_clock_time(raw, expected):
    assert cp.norm_time(raw) == expected


def test_seoul_image_url_drops_thumbnail_flag():
    url = cp.seoul_full_image(SEOUL_ROW['MAIN_IMG'])
    assert 'thumb' not in url and 'atchFileId=401a984b98ff4af8a57122219ee0d591' in url


def test_seoul_row_maps_to_reference_event():
    rec = cp.seoul_record(SEOUL_ROW)
    assert rec['source_id'] == '158770'
    assert cp.reference_event(rec) == {
        'name': SEOUL_ROW['TITLE'], 'start_date': '2026-12-24', 'end_date': '2026-12-24', 'start_time': '19:30',
        'venues': ['강동아트센터 대극장 한강'], 'admission_fee': SEOUL_ROW['USE_FEE'],
        'contact': SEOUL_ROW['INQUIRY'], 'url': SEOUL_ROW['ORG_LINK'],
    }  # ORG_NAME '기타'는 기준 정보가 아니라서 뺀다


def test_reference_event_fits_poster_schema():
    jsonschema = pytest.importorskip('jsonschema')
    schema = json.loads((ROOT / 'docs-draft/poster-schema/poster-case.schema.json').read_text(encoding='utf-8'))
    sub = {'$schema': schema.get('$schema'), '$defs': schema['$defs'], '$ref': '#/$defs/reference'}
    jsonschema.validate({'event': cp.reference_event(cp.seoul_record(SEOUL_ROW))}, sub)


def test_vague_fee_and_time_are_flagged():
    rec = {**cp.seoul_record(SEOUL_ROW), 'fee': '각 공연 / 축제별 상이', 'time': '각 공연 / 축제별 상이'}
    event = cp.reference_event(rec)
    q = cp.quality(rec, event)
    assert q['vague_fields'] == ['fee', 'time'] and 'start_time' in q['missing_reference_fields']


def test_overlaps_date_window():
    rec = {'start_date': '2026-09-18', 'end_date': '2026-11-29'}
    assert cp.overlaps(rec, '2026-10-12', None)
    assert cp.overlaps(rec, '2026-10-12', '2026-10-31')
    assert not cp.overlaps(rec, '2026-12-01', None)
    assert not cp.overlaps({'start_date': None}, '2026-10-12', None)


def test_parse_xml_items_and_errors():
    ok = b'<response><header><resultCode>00</resultCode></header><body><items><item><seq>1</seq><title>A</title></item></items><totalCount>1</totalCount></body></response>'
    assert cp.parse_xml_items(ok, ('item',)) == (1, [{'seq': '1', 'title': 'A'}])
    gateway = b'<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg><returnAuthMsg>x</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>'
    with pytest.raises(cp.ApiError, match='SERVICE_KEY'):
        cp.parse_xml_items(gateway, ('item',))
    with pytest.raises(cp.ApiError, match='DTD'):
        cp.parse_xml_items(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', ('item',))


def test_redact_hides_raw_and_url_encoded_key():
    assert cp.redact('key=ab/c+d and ab%2Fc%2Bd', 'ab/c+d') == 'key=*** and ***'


def test_store_keeps_schema_formats_and_converts_gif(tmp_path):
    store = cp.Store(tmp_path)
    jpeg = store.save_image(image_bytes('JPEG'))
    assert jpeg['format'] == 'jpeg' and jpeg['sha256'] == jpeg['original_sha256'] and (tmp_path / jpeg['path']).exists()
    gif = store.save_image(image_bytes('GIF'))
    assert gif['format'] == 'png' and gif['original_format'] == 'GIF'
    assert (tmp_path / gif['original_path']).exists() and gif['sha256'] != gif['original_sha256']
    assert (gif['width'], gif['height']) == (30, 40)


def test_store_dedupes_across_runs_but_not_dry_runs(tmp_path):
    store = cp.Store(tmp_path)
    store.preview({'candidate_id': 'seoul:1', 'image': {'url': 'u', 'downloaded': False}})
    assert 'seoul:1' not in cp.Store(tmp_path).ids
    store.add({'candidate_id': 'seoul:2', 'image': {'original_sha256': 'abc', 'sha256': 'abc'}})
    reloaded = cp.Store(tmp_path)
    assert 'seoul:2' in reloaded.ids and 'abc' in reloaded.hashes


def test_param_must_be_key_value(capsys):
    with pytest.raises(SystemExit):
        cp.main(['collect', 'culture', '--param', 'rows'])
    assert 'KEY=VALUE' in capsys.readouterr().err
