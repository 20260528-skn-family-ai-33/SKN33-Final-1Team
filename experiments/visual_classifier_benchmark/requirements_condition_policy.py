"""Conservative output-only condition audit and exact-duplicate removal.

No expected answers, case IDs, source keyword rules, or semantic corrections are
used. A source span is required before two entries can be treated as identical.
The original response, assembled object, and parsed object are never modified.
"""
from copy import deepcopy
import json
import time

from requirements_json_contract import parse
from resultlib import LABELS


OBJECT_FIELDS = frozenset(LABELS) | {'other_visual'}
IDENTITY_FIELDS = ('type', 'field', 'target', 'operator', 'value', 'source')


def _valid(parsed):
    return isinstance(parsed, dict) and all(
        parsed.get(name) is True for name in ('json_valid', 'schema_valid', 'source_valid'))


def _typed(value):
    # Do not conflate bool/int, normalize whitespace, or shorten target phrases.
    return (type(value).__name__, value)


def _source_spans(result, item_count):
    """Return only uniquely identified, structurally valid item trace spans."""
    found = {}
    for entry in result.get('assembly_trace', []):
        if not isinstance(entry, dict) or entry.get('collection') != 'items':
            continue
        index = entry.get('index')
        if type(index) is not int or not 0 <= index < item_count:
            continue
        found.setdefault(index, []).append(entry)
    spans = {}
    for index, entries in found.items():
        if len(entries) != 1:
            continue
        entry = entries[0]
        start, end = entry.get('source_start'), entry.get('source_end')
        if type(start) is int and type(end) is int and 0 <= start < end:
            spans[index] = (start, end)
    return spans


def audit_response(result):
    """Inspect validated output; semantic pair findings are signals, not edits."""
    parsed = result.get('parsed')
    audit = dict(eligible=_valid(parsed), exact_duplicate_count=0,
                 exact_duplicates=[], redundant_inclusion_pairs=[],
                 conflicting_object_fields=[], unresolved_review_count=0,
                 missing_source_trace_indices=[])
    if not audit['eligible']:
        return audit
    obj = parsed['value']
    items = obj['items']
    spans = _source_spans(result, len(items))
    audit['missing_source_trace_indices'] = [i for i in range(len(items)) if i not in spans]
    audit['unresolved_review_count'] = sum(
        item['reason'] != 'non_requirement' for item in obj['needs_review'])
    seen = {}
    for index, item in enumerate(items):
        if index not in spans:
            continue
        identity = (spans[index], tuple(_typed(item[name]) for name in IDENTITY_FIELDS))
        if identity in seen:
            audit['exact_duplicates'].append(dict(
                index=index, kept_index=seen[identity], reason='exact_same_source_and_condition'))
        else:
            seen[identity] = index
    audit['exact_duplicate_count'] = len(audit['exact_duplicates'])
    for left_index, left in enumerate(items):
        if left_index not in spans or left['type'] != 'visual':
            continue
        for right_index in range(left_index + 1, len(items)):
            right = items[right_index]
            if (right_index not in spans or right['type'] != 'visual' or
                    spans[left_index] != spans[right_index] or
                    _typed(left['target']) != _typed(right['target']) or
                    left['source'] != right['source']):
                continue
            if (left['field'] in OBJECT_FIELDS and right['field'] in OBJECT_FIELDS and
                    left['field'] != right['field']):
                audit['conflicting_object_fields'].append(dict(
                    indices=[left_index, right_index], target=left['target'],
                    fields=[left['field'], right['field']], source_span=list(spans[left_index])))
            for count_index, count, required_index, required in (
                (left_index, left, right_index, right),
                (right_index, right, left_index, left),
            ):
                if (count['operator'] in ('at_least', 'exactly') and
                        type(count['value']) is int and count['value'] > 0 and
                        required['operator'] == 'required'):
                    audit['redundant_inclusion_pairs'].append(dict(
                        count_index=count_index, required_index=required_index,
                        target=count['target'], count_field=count['field'],
                        required_field=required['field'], source_span=list(spans[left_index]),
                        auto_removed=False))
    return audit


def normalize_exact_duplicates(result, text):
    """Return a copied/revalidated parsed result plus an explicit removal trace.

    Removed/kept indices refer to the original items array. Counts implying
    inclusion, differing fields, ambiguous targets and review entries are never
    removed or rewritten. Invalid responses pass through unchanged as copies.
    """
    started = time.perf_counter()
    before = audit_response(result)
    parsed = deepcopy(result.get('parsed'))
    removed = []
    after = deepcopy(before)
    if before['eligible']:
        # Check the supplied text against trace offsets before allowing deletion.
        items = parsed['value']['items']
        spans = _source_spans(result, len(items))
        grounded = {index for index, (start, end) in spans.items()
                    if end <= len(text) and text[start:end] == items[index]['source']}
        removed = [deepcopy(entry) for entry in before['exact_duplicates']
                   if entry['index'] in grounded and entry['kept_index'] in grounded]
        if removed:
            deleted = {entry['index'] for entry in removed}
            kept = [index for index in range(len(items)) if index not in deleted]
            obj = deepcopy(parsed['value'])
            obj['items'] = [deepcopy(items[index]) for index in kept]
            candidate = parse(json.dumps(obj, ensure_ascii=False), text)
            if not _valid(candidate):
                raise ValueError('Exact duplicate normalization failed contract validation')
            parsed = candidate
            remap = {old: new for new, old in enumerate(kept)}
            traces = []
            for trace in result.get('assembly_trace', []):
                entry = deepcopy(trace)
                if isinstance(entry, dict) and entry.get('collection') == 'items':
                    index = entry.get('index')
                    if type(index) is not int or index not in remap:
                        continue
                    entry['index'] = remap[index]
                traces.append(entry)
            after = audit_response(dict(parsed=parsed, assembly_trace=traces))
    return dict(parsed=parsed, removed=removed, audit_before=before, audit_after=after,
                postprocess_ms=(time.perf_counter() - started) * 1000)
