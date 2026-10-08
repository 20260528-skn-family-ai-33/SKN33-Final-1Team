"""Current inspection contracts and read compatibility for legacy payloads."""

ITEM_EXECUTION_STATUSES = frozenset({'succeeded', 'failed', 'not_run'})
ITEM_EXECUTION_ALIASES = {'completed': 'succeeded'}
RUN_STATUSES = frozenset({'running', 'completed', 'partial', 'failed'})
SUPPORTED_VERDICTS = frozenset({'충족', '미충족', '확인 필요', None})


def rule_id(rule: dict) -> str:
    """Resolve the public rule ID without silently overriding conflicting fields."""
    if not isinstance(rule, dict):
        raise ValueError('rule must be an object')
    identifiers = [rule[key] for key in ('rule_id', 'id') if key in rule]
    if not identifiers or any(not isinstance(value, str) or not value.strip()
                              or value != value.strip() for value in identifiers):
        raise ValueError('rule_id must be a nonempty string without surrounding whitespace')
    if len(set(identifiers)) != 1:
        raise ValueError('conflicting rule_id and legacy id')
    return identifiers[0]


def normalize_rules(rules: list[dict], *, keep_legacy_id: bool = False) -> list[dict]:
    """Copy rules into the canonical schema; callers retain original file bytes."""
    if not isinstance(rules, list):
        raise ValueError('rules must be a list')
    normalized = []
    seen = set()
    for rule in rules:
        identifier = rule_id(rule)
        if identifier in seen:
            raise ValueError('duplicate rule_id within criteria')
        seen.add(identifier)
        item = dict(rule)
        item['rule_id'] = identifier
        if keep_legacy_id:
            item['id'] = identifier
        else:
            item.pop('id', None)
        normalized.append(item)
    return normalized


def normalize_item_execution_status(value: str) -> str:
    """The completed alias applies to an item only, never to run.status."""
    if not isinstance(value, str):
        raise ValueError('invalid execution status')
    value = ITEM_EXECUTION_ALIASES.get(value, value)
    if value not in ITEM_EXECUTION_STATUSES:
        raise ValueError('invalid execution status')
    return value
