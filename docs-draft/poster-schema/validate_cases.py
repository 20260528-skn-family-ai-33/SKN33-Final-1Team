# -*- coding: utf-8 -*-
"""케이스 JSONL 검증.

1) poster-case.schema.json(JSON Schema 2020-12) 통과 여부
2) 스키마만으로 잡을 수 없는 정합성
   - text_ids·object_ids·targets가 실제로 존재하는가
   - check_id가 check-catalog.json에 있는가
   - 기준 정보가 필요한 검사(requires_reference)인데 기준이 없으면 needs_review인가
   - DT-01·DT-02·DT-03 판정이 전사한 날짜로 계산한 값과 같은가
   - 변형 케이스: fail 라벨은 perturbations로 설명되고, 주입한 오류는 pass가 아닌 라벨을 갖는가

사용: python docs-draft/poster-schema/validate_cases.py [케이스.jsonl]
의존성: jsonschema (requirements.lock.txt에 포함)
"""
import datetime
import json
import sys
from pathlib import Path

import jsonschema

HERE = Path(__file__).parent
WEEK = '월화수목금토일'


def present(case, dotted):
    node = case
    for key in dotted.split('.'):
        if not isinstance(node, dict) or node.get(key) in (None, [], {}, ''):
            return False
        node = node[key]
    return True


def calendar_verdicts(fields):
    """전사한 값만으로 DT-01·DT-02·DT-03의 기대 판정을 계산한다."""
    dates, year = fields.get('dates'), fields.get('event_year')
    out = {}
    if not dates or not (dates['start'] or dates['end']):
        return {'DT-01': 'not_applicable', 'DT-02': 'not_applicable'}
    parsed = {}
    for key in ('start', 'end'):
        try:
            parsed[key] = datetime.date.fromisoformat(dates[key]) if dates[key] else None
        except ValueError:
            parsed[key] = 'invalid'
    shown = [(parsed[k], dates[f'{k}_weekday']) for k in ('start', 'end') if dates[f'{k}_weekday']]
    if any(d == 'invalid' for d, _ in shown) or not shown:
        out['DT-01'] = 'not_applicable' if not shown else 'fail'
    else:
        out['DT-01'] = 'pass' if all(d is not None and WEEK[d.weekday()] == w for d, w in shown) else 'fail'
    s, e = parsed['start'], parsed['end']
    if 'invalid' in (s, e):
        out['DT-02'] = 'fail'
    elif s and e:
        out['DT-02'] = 'pass' if s <= e else 'fail'
    if year and isinstance(s, datetime.date):
        years = {year['value'], s.year} | ({e.year} if isinstance(e, datetime.date) else set())
        out['DT-03'] = 'pass' if len(years) == 1 else 'fail'
    return out


def integrity(case, catalog):
    problems = []
    texts = {t['id'] for t in case['gold']['extraction']['texts']}
    objects = {o['id'] for o in case['gold']['extraction'].get('objects', [])}
    if len(texts) != len(case['gold']['extraction']['texts']):
        problems.append('texts의 id가 중복됨')
    if len(objects) != len(case['gold']['extraction'].get('objects', [])):
        problems.append('objects의 id가 중복됨')
    known = texts | objects

    def refs(label, value):
        for ref in value or []:
            if ref not in known:
                problems.append(f'{label}: 존재하지 않는 id {ref}')

    fields = case['gold']['extraction']['fields']
    for key in ('event_name', 'event_year', 'dates', 'time', 'contact', 'admission_fee'):
        if fields.get(key):
            refs(f'fields.{key}', fields[key].get('text_ids'))
            refs(f'fields.{key}', fields[key].get('object_ids'))
    for key in ('venues', 'slogans'):
        for item in fields.get(key, []):
            refs(f'fields.{key}', item['text_ids'])
    for org in fields['organizers']:
        refs('fields.organizers', org.get('text_ids'))
        refs('fields.organizers', org.get('object_ids'))
    for t in case['gold']['extraction']['texts']:
        x, y, w, h = t['bbox']
        if x + w > 1.0001 or y + h > 1.0001:
            problems.append(f'{t["id"]}: bbox가 이미지를 벗어남')

    cat = {c['check_id']: c for c in catalog['checks']}
    calc = calendar_verdicts(fields)
    seen = set()
    for chk in case['gold']['checks']:
        cid = chk['check_id']
        if cid in seen:
            problems.append(f'{cid}: 같은 검사가 두 번 있음')
        seen.add(cid)
        if cid not in cat:
            problems.append(f'{cid}: 카탈로그에 없는 check_id')
            continue
        refs(f'checks.{cid}.targets', chk.get('targets'))
        missing = [p for p in cat[cid]['requires_reference'] if not present(case, p)]
        if missing and chk['verdict'] != 'needs_review':
            problems.append(f'{cid}: 기준 정보({", ".join(missing)})가 없는데 {chk["verdict"]}로 판정함')
        if missing and chk.get('reason_code') not in (None, 'needs_reference'):
            problems.append(f'{cid}: 기준 정보가 없으면 reason_code는 needs_reference여야 함')
        if cid in calc and chk['verdict'] != calc[cid]:
            problems.append(f'{cid}: 판정 {chk["verdict"]} ≠ 전사값으로 계산한 {calc[cid]}')

    src = case['source']
    verdicts = {c['check_id']: c['verdict'] for c in case['gold']['checks']}
    injected = {cid for p in src.get('perturbations', []) for cid in p['check_ids']}
    for p in src.get('perturbations', []):
        if p['target_id'] not in known:
            problems.append(f'perturbation 대상 {p["target_id"]}이 존재하지 않음')
    if src['origin'] == 'synthetic_variant':
        for cid in verdicts:
            if verdicts[cid] == 'fail' and cid not in injected:
                problems.append(f'{cid}: fail인데 perturbations로 설명되지 않음')
        for cid in injected:
            if verdicts.get(cid, 'pass') == 'pass':
                problems.append(f'{cid}: 오류를 주입했는데 라벨이 없거나 pass임')
    elif src.get('perturbations'):
        problems.append('원본 케이스에는 perturbations가 있으면 안 됨')
    return problems


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else HERE / 'examples' / 'poster-cases.example.jsonl'
    schema = json.loads((HERE / 'poster-case.schema.json').read_text(encoding='utf-8'))
    catalog = json.loads((HERE / 'check-catalog.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    raw = path.read_bytes()
    if raw.startswith(b'\xef\xbb\xbf'):
        print('BOM이 있습니다. BOM 없는 UTF-8로 저장하세요.')
        return 1
    bad, seen_ids = 0, set()
    lines = raw.decode('utf-8').split('\n')
    if lines and lines[-1] == '':
        lines.pop()  # 마지막 줄바꿈 뒤의 빈 조각
    for n, line in enumerate(lines, 1):
        if not line.strip():
            print(f'{n}행: 빈 줄(JSONL은 한 줄에 케이스 하나)')
            bad += 1
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f'{n}행: JSON 오류 {exc}')
            bad += 1
            continue
        errors = [f'{"/".join(map(str, e.absolute_path)) or "(루트)"}: {e.message}' for e in validator.iter_errors(case)]
        if not errors:
            errors = integrity(case, catalog)
            if case['case_id'] in seen_ids:
                errors.append('case_id 중복')
            seen_ids.add(case['case_id'])
            if case['label_meta']['catalog_version'] != catalog['catalog_version']:
                errors.append(f'catalog_version {case["label_meta"]["catalog_version"]} ≠ 카탈로그 {catalog["catalog_version"]}')
        print(f'{n}행 {case.get("case_id", "?")}: ' + ('OK' if not errors else f'{len(errors)}건 문제'))
        for e in errors:
            print('   -', e)
        bad += bool(errors)
    print('결과:', '모두 통과' if not bad else f'{bad}행에 문제')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
