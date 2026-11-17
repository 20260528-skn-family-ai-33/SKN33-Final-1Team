"""A06 settles file size, recorded pixel density and media type from the measured original.

Hand-set outcomes for the code comparison, not model accuracy measurements. The shared
helpers and the measured JPEG come from test_a06_typed.py.
"""
import json

import pytest

from backend.agent_framework.contracts import OriginalFile
from backend.agent_framework.validation import ReferenceValidator, validate_validated
from tests.agent_framework.test_a06_typed import JPEG, Storage, encoded, judged, measured, stored

# 2,500,000 bytes: below 2.5 MB read either way, between 2 MB (1000) and 2 MiB (1024).
FILE = JPEG.model_copy(update={'byte_size': 2_500_000, 'dpi': (300.0, 300.0)})


@pytest.mark.parametrize('field,expected,options,verdict,issues,shown', [
    ('file_size_bytes', '10', dict(kind='number', unit='MB', operator='lte'), 'pass', (),
     ('2500000', 'B', '10000000 / 10485760')),
    ('file_size_bytes', '2', dict(kind='number', unit='MB', operator='lte'), 'fail', ('value_mismatch',),
     ('2500000', 'B', '2000000 / 2097152')),
    ('file_size_bytes', '2500000', dict(kind='number', unit='bytes'), 'pass', (), ('2500000', 'B', '2500000')),
    ('file_size_bytes', '3', dict(kind='number', unit='MiB', operator='lte'), 'pass', (), ('2500000', 'B', '3145728')),
    ('file_size_bytes', '1', dict(kind='number', unit='메가바이트', operator='gte'), 'pass', (),
     ('2500000', 'B', '1000000 / 1048576')),
    ('resolution_dpi', '300', dict(kind='number', unit='dpi', operator='gte'), 'pass', (), ('300', 'dpi', '300')),
    ('resolution_dpi', '300', dict(kind='number', unit='DPI'), 'pass', (), ('300', 'DPI', '300')),
    ('resolution_dpi', '350', dict(kind='number', unit='ppi', operator='gte'), 'fail', ('value_mismatch',),
     ('300', 'ppi', '350')),
    ('mime_type', 'image/jpeg', {}, 'pass', (), ('image/jpeg', None, 'image/jpeg')),
    ('mime_type', 'image/png', {}, 'fail', ('value_mismatch',), ('image/jpeg', None, 'image/png')),
    ('mime_type', 'image/png 또는 image/jpeg', {}, 'pass', (), ('image/jpeg', None, 'image/jpeg / image/png')),
], ids=('size-under-mb', 'size-over-mb', 'size-exact-bytes', 'size-mib', 'size-korean-unit', 'dpi-at-least',
        'dpi-upper-unit', 'dpi-below', 'mime-same', 'mime-other', 'mime-either'))
def test_more_file_requirements_are_settled_from_the_uploaded_original(field, expected, options, verdict, issues, shown):
    req = measured(field, expected, original=FILE, **options)
    result = ReferenceValidator().validate(req)
    validate_validated(req, result)
    item = result.items[0]
    assert (item.verdict, item.issues) == (verdict, issues)
    # 결과에 보이는 값은 모델의 답이 아니라 측정한 파일이다
    assert (item.observed, item.observed_unit, item.comparison_expected) == shown
    assert (item.observation_status, item.image, item.evidence_ids) == ('observed', None, ('e1',))


@pytest.mark.parametrize('field,expected,options,original', [
    # 1000과 1024 두 해석의 결론이 다르면 정하지 않는다: 2.5MB는 2,500,000 또는 2,621,440바이트
    ('file_size_bytes', '2.5', dict(kind='number', unit='MB'), FILE),
    ('file_size_bytes', '2.4', dict(kind='number', unit='MB', operator='lte'), FILE),
    ('file_size_bytes', '10', dict(kind='number', operator='lte'), FILE),                  # 단위 없음
    ('file_size_bytes', '10', dict(kind='number', unit='MB', operator='lte'), JPEG),       # 크기를 재지 않은 실행
    ('resolution_dpi', '300', dict(kind='number', unit='dpi'), JPEG),                     # 파일에 기록된 해상도 없음
    ('resolution_dpi', '300', dict(kind='number', unit='dpi'),
     FILE.model_copy(update={'dpi': (300.0, 150.0)})),                                     # 가로·세로 해상도가 다름
    ('resolution_dpi', '30', dict(kind='number', unit='cm'), FILE),
    ('mime_type', 'application/pdf', {}, FILE),
    ('mime_type', 'image/tiff', {}, FILE),
    ('file_name', 'poster_final.jpg', {}, FILE),
    ('compression', '무손실', {}, FILE),
], ids=('size-readings-split', 'size-readings-split-lte', 'size-no-unit', 'size-not-measured', 'dpi-not-recorded',
        'dpi-axes-differ', 'dpi-other-unit', 'mime-pdf', 'mime-tiff', 'file-name', 'compression'))
def test_what_the_original_cannot_settle_still_stays_with_a_person(field, expected, options, original):
    verdict, issues = judged(measured(field, expected, original=original, observed='3000', **options))
    assert verdict == 'review' and 'typed_attribute_review_required' in issues


def test_a_difference_the_source_may_allow_is_not_a_confirmed_failure():
    allowed = measured('file_size_bytes', '2', original=FILE, kind='number', unit='MB', operator='lte',
                       allowance='허용 범위: 인쇄소와 협의하면 더 큰 파일도 가능하다.')
    assert judged(allowed) == ('review', ('qualifier_interpretation_required',))


@pytest.mark.parametrize('content,media_type,dpi', [
    (encoded('RGB', (40, 30), 'JPEG', dpi=(300, 300)), 'image/jpeg', (300.0, 300.0)),
    # PNG는 미터당 점으로 저장해 300이 299.9994로 읽힌다
    (encoded('RGB', (40, 30), 'PNG', dpi=(300, 300)), 'image/png', (300.0, 300.0)),
    (encoded('RGB', (40, 30), 'JPEG', dpi=(300, 150)), 'image/jpeg', (300.0, 150.0)),
    # 단위 없는 JFIF 머리글은 해상도를 기록하지 않은 것으로 본다
    (encoded('RGB', (40, 30), 'JPEG'), 'image/jpeg', None),
    (encoded('RGB', (40, 30), 'WEBP'), 'image/webp', None),
], ids=('jpeg-300', 'png-300', 'jpeg-axes-differ', 'jpeg-no-unit', 'webp-none'))
def test_byte_size_and_recorded_density_are_measured_from_the_verified_bytes(content, media_type, dpi):
    from backend.agent_framework.poster_review import measure_original
    result = measure_original(Storage(content), *stored(content, media_type, 40, 30))
    assert (result.byte_size, result.dpi) == (len(content), dpi)


def test_facts_measured_before_size_and_density_keep_their_stored_shape():
    stored_before = JPEG.model_dump(mode='json')
    assert set(stored_before) == {'contract_version', 'file_id', 'sha256', 'format', 'width', 'height', 'color_mode'}
    again = OriginalFile.model_validate(stored_before)
    assert (again.byte_size, again.dpi) == (None, None)
    assert json.dumps(again.model_dump(mode='json'), sort_keys=True) == json.dumps(stored_before, sort_keys=True)
    full = FILE.model_dump(mode='json')
    assert (full['byte_size'], full['dpi']) == (2_500_000, [300.0, 300.0])
    assert OriginalFile.model_validate(full) == FILE
    for bad in ({'byte_size': -1}, {'dpi': [0, 300]}, {'dpi': [300]}):
        with pytest.raises(ValueError):
            OriginalFile.model_validate({**stored_before, **bad})
