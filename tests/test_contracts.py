"""Compatibility boundaries preserve identifiers, versions, and state meanings."""
import hashlib
import json
from pathlib import Path

import pytest

from app.contracts import (ITEM_EXECUTION_ALIASES, ITEM_EXECUTION_STATUSES, RUN_STATUSES,
                           SUPPORTED_VERDICTS, normalize_item_execution_status, normalize_rules)
from app.inspector import inspect_text_rules
from app.topics import DEFAULT_TOPIC, TopicCatalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('key', ['id', 'rule_id'])
def test_rule_input_normalization_supports_both_formats_without_mutating_input(key):
    source = [{key: 'C01', 'expected': '딸기라떼'}]
    normalized = normalize_rules(source)
    assert normalized == [{'rule_id': 'C01', 'expected': '딸기라떼'}]
    assert source == [{key: 'C01', 'expected': '딸기라떼'}]
    assert normalize_rules(source, keep_legacy_id=True)[0]['id'] == 'C01'
    result = inspect_text_rules([{'text': '딸기라떼', 'bbox': [.1, .1, .5, .1], 'confidence': 1}], source)[0]
    assert result['rule_id'] == 'C01'
    assert result['verdict'] == '충족'


@pytest.mark.parametrize('rules,reason', [
    ([{'id': 'C01', 'rule_id': 'C02'}], 'conflicting'),
    ([{'id': 'C01'}, {'rule_id': 'C01'}], 'duplicate'),
    ([{'id': ''}], 'nonempty'),
    ([{'rule_id': None, 'id': 'C01'}], 'nonempty'),
    ([{'rule_id': ' C01 '}], 'nonempty'),
    ([{}], 'nonempty'),
])
def test_invalid_or_ambiguous_rule_ids_are_rejected(rules, reason):
    with pytest.raises(ValueError, match=reason):
        normalize_rules(rules)
    with pytest.raises(ValueError, match=reason):
        inspect_text_rules([], rules)


@pytest.mark.parametrize('topic,condition', [
    (DEFAULT_TOPIC, None), ('festival-v1', None), ('croissant-v2', 'sample-04'),
])
def test_topic_normalization_preserves_original_criteria_hash_and_file_bytes(topic, condition):
    catalog = TopicCatalog(ROOT / 'data/criteria.json', ROOT / 'data/samples', ROOT / 'data/topics')
    _, path, _ = catalog.entry(topic)
    raw = path.read_bytes()
    version_input = raw if topic == DEFAULT_TOPIC else raw + json.dumps([topic, condition]).encode()
    current = catalog.criteria(topic, condition)
    assert current['version'] == hashlib.sha256(version_input).hexdigest()[:16]
    assert path.read_bytes() == raw
    assert all(rule['rule_id'] == rule['id'] for rule in current['rules'])


@pytest.mark.parametrize('key', ['id', 'rule_id'])
def test_mcp_tool_directly_accepts_canonical_and_legacy_rules(monkeypatch, key):
    from app import inspector
    from app.mcp_server import inspect_image
    monkeypatch.setattr(inspector, 'read_ocr', lambda _: {
        'lines': [{'text': '딸기라떼', 'bbox': [.1, .1, .5, .1], 'confidence': 1}],
        'method': 'test-only'})
    result = inspect_image('test-only.png', [{key: 'C01', 'expected': '딸기라떼'}])
    assert result['results'][0]['rule_id'] == 'C01'
    assert result['results'][0]['execution_status'] == 'succeeded'


def test_invalid_mcp_rules_are_rejected_before_reading_image(monkeypatch):
    from app import inspector
    from app.mcp_server import inspect_image
    def unexpected_read(_):
        pytest.fail('ambiguous IDs must be rejected before invoking OCR')
    monkeypatch.setattr(inspector, 'read_ocr', unexpected_read)
    with pytest.raises(ValueError, match='duplicate'):
        inspect_image('test-only.png', [{'rule_id': 'C01'}, {'id': 'C01'}])


def test_current_runtime_enums_match_shared_document_contract():
    contract = json.loads((ROOT / 'docs/planning/contract-standard-v1.json').read_text())
    domains = {domain['id']: domain for domain in contract['state_domains']}
    for domain, values in [('item_execution', ITEM_EXECUTION_STATUSES),
                           ('run', RUN_STATUSES), ('verdict', SUPPORTED_VERDICTS)]:
        assert {entry['value'] for entry in domains[domain]['values'] if entry['supported']} == values
    assert domains['item_execution']['aliases'] == ITEM_EXECUTION_ALIASES
    assert normalize_item_execution_status('completed') == 'succeeded'
    assert 'completed' in RUN_STATUSES and 'succeeded' not in RUN_STATUSES
