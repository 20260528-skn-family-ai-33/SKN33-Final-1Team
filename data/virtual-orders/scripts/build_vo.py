# -*- coding: utf-8 -*-
import copy, csv, json, pathlib, shutil, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from vo_lib import Ad, Pdf, pages_to_md, judge, sha256_file
import vo_cases
from vo_cases import CASES, inspect_form

OUT = pathlib.Path(__file__).resolve().parents[1]
TODAY = '2026-10-06'
FONT_IMG = 'Noto Sans KR / Noto Serif KR (SIL OFL 1.1 — 글꼴 파일의 license 문구로 재확인 필요)'
FONT_PDF = '맑은 고딕(Windows 시스템 글꼴, 외부 배포 조건 미확인)'
STAGE = {1: '1 기획·요청', 2: '2 계약', 3: '3 수행·검수'}


def jdump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def fname(eid):
    return eid.replace(':', '-')


def build_case(case, registry):
    cid, slug = case['id'], case['slug']
    cdir = OUT / slug
    if cdir.exists():
        shutil.rmtree(cdir)
    idir, gdir = cdir / 'input', cdir / 'gold'
    for sub in ('docs', 'deliveries', 'evidence'):
        (idir / sub).mkdir(parents=True, exist_ok=True)
    gdir.mkdir(parents=True, exist_ok=True)

    def reg(path, typ, fonts, area='input', stage='', note=''):
        p = pathlib.Path(path)
        registry.append({'asset_id': f'{cid}:{p.name}', 'case_id': cid, 'group_id': case['group'], 'area': area, 'type': typ, 'doc_stage': stage,
                         'path': str(p.relative_to(OUT)).replace('\\', '/'), 'sha256': sha256_file(p), 'bytes': p.stat().st_size,
                         'real_or_synthetic': 'synthetic', 'created': TODAY, 'rights_holder': '팀(SKN33 1Team) · 가상 자료', 'fonts': fonts,
                         'internal_review': 'allowed', 'training': 'allowed' if area == 'input' else 'denied(정답은 모델 입력 금지)', 'evaluation': 'allowed', 'demo': 'allowed',
                         'external_transmission': 'denied(승인 전)', 'public_redistribution': 'unknown', 'sensitivity': '없음(가상)', 'note': note})

    # ---------- 문서
    docs = list(case['docs']) + [{'id': 'inspect', 'stage': 3, 'title': '검수조서 (검수 확인서) 양식', 'pages': inspect_form(case['ctx'], case['criteria'], {d['id']: d['title'].split(' (')[0] for d in case['docs']})}]
    docinfo = []
    for d in docs:
        pdfp = idir / 'docs' / f"{cid}_{d['id']}.pdf"
        title = f"{case['name']} · {d['title']}"
        Pdf(title).save(pdfp, d['pages'])
        mdp = idir / 'docs' / f"{cid}_{d['id']}.md"
        mdp.write_text(pages_to_md(title, d['pages']), encoding='utf-8')
        reg(pdfp, 'document_pdf', FONT_PDF, stage=STAGE[d['stage']])
        reg(mdp, 'document_md', '-', stage=STAGE[d['stage']])
        docinfo.append({'doc_id': d['id'], 'title': d['title'], 'stage': STAGE[d['stage']], 'pdf': pdfp.name, 'md': mdp.name, 'pages': len(d['pages']), 'sha256': sha256_file(pdfp)})
    case['_docs'] = docs

    # ---------- 증빙 문서(종류·버전별 1회)
    ev_files = {}

    def ensure_ev(eid, ver, meta):
        key = (eid, ver)
        if key in ev_files and ev_files[key]['meta'] != meta:
            raise ValueError(f'{cid}: 같은 증빙 버전 {key}에 서로 다른 메타가 지정됨 {ev_files[key]["meta"]} vs {meta}')
        if key not in ev_files:
            title, fn = case['evidence_docs'][eid]
            p = idir / 'evidence' / f"{cid}_{fname(eid)}_{ver}.pdf"
            Pdf(title).save(p, fn(meta))
            reg(p, 'evidence_pdf', FONT_PDF, note=f'가상 증빙 · 버전 {ver}' + (f" · 메타 {json.dumps(meta, ensure_ascii=False)}" if meta else ''))
            ev_files[key] = {'evidence_id': eid, 'version': ver, 'file': p.name, 'sha256': sha256_file(p), 'meta': meta}
        return ev_files[key]

    # ---------- 납품 이미지
    deliveries, obs_all, byid = [], {}, {}
    for i, d in enumerate(case['deliveries'], 1):
        path = idir / 'deliveries' / f"{cid}_{d['id']}.png"
        evs = [ensure_ev(eid, ver, meta) for eid, ver, meta in d['evidence']]
        ev_ids = [e['evidence_id'] for e in evs]
        ev_meta = {e['evidence_id']: e['meta'] for e in evs if e['meta']}
        if d.get('copy_of'):
            shutil.copyfile(byid[d['copy_of']]['path'], path)
            ob = copy.deepcopy(obs_all[d['copy_of']])
            ob['file'] = dict(ob['file'])
            fm = ob['file']
        else:
            ad = case['render'](d.get('params', {}))
            fm = ad.save(path, 'png')
            ob = ad.observations(fm, ev_ids)
        ob['evidence'], ob['evidence_meta'] = ev_ids, ev_meta
        obs_all[d['id']] = ob
        byid[d['id']] = {'path': path}
        reg(path, 'delivery_image', FONT_IMG, note='; '.join(d['edits']))
        deliveries.append({'delivery_id': d['id'], 'file': path.name, 'kind': d['kind'], 'submitted_order': i, 'sha256': fm['sha256'], 'width': fm['w'], 'height': fm['h'],
                           'mime': fm['mime'], 'bytes': fm['size'], 'evidence': [{'evidence_id': e['evidence_id'], 'version': e['version'], 'file': e['file']} for e in evs],
                           'parent': d.get('revision_of'), 'byte_identical_to': d.get('copy_of')})

    jdump(idir / 'case_input.json', {'case_id': cid, 'group_id': case['group'], 'name': case['name'], 'category': case['category'], 'synthetic': True, 'usage_plan': case['usage'],
                                     'documents': docinfo, 'deliveries': deliveries})

    # ---------- 정답
    crit_gold = [dict({k: v for k, v in c.items() if k != 'src'}, sources=c['src']) for c in case['criteria']]
    judgments = []
    for ver in case['versions']:
        for d in case['deliveries']:
            for c in case['criteria']:
                r = judge(c, obs_all[d['id']], ver, case['usage'])
                judgments.append({'case_id': cid, 'delivery_id': d['id'], 'criteria_version': ver, 'criterion_id': c['id'], 'label': c['label'], 'applicable': r['applicable'],
                                  'judgment': r['judgment'], 'reason_code': r['reason'], 'observed': r['observed'], 'evidence_ids': r['evidence'], 'applicability_basis': r['basis']})
    jdump(gdir / 'criteria_gold.json', {'case_id': cid, 'versions': case['versions'], 'criteria': crit_gold})
    jdump(gdir / 'observations.json', {'case_id': cid, 'note': '문자·객체·로고는 렌더링 시점의 실제 좌표에서 기록한 관찰 정답. legibility는 설계 의도(clear/partial/ambiguous).', 'deliveries': obs_all})
    jdump(gdir / 'judgments.json', {'case_id': cid, 'judgments': judgments})

    J = {(j['delivery_id'], j['criteria_version'], j['criterion_id']): j['judgment'] for j in judgments}

    def diff(prev, new):
        res, per, newe, unk = [], [], [], []
        for c in case['criteria']:
            a, b = J[(prev, 'v1', c['id'])], J[(new, 'v1', c['id'])]
            if a == '미충족' and b == '충족':
                res.append(c['id'])
            elif a == '미충족' and b == '미충족':
                per.append(c['id'])
            elif a != '미충족' and b == '미충족':
                newe.append(c['id'])
            if b == '확인 필요':
                unk.append(c['id'])
        return {'resolved': res, 'persisting': per, 'new_errors': newe, 'needs_review_after': unk}
    sc = case['scenario']
    sha = {d['delivery_id']: d['sha256'] for d in deliveries}
    blockers = [c['id'] for c in case['criteria'] if J[(sc['new'], 'v1', c['id'])] in ('미충족', '확인 필요')]
    wf = {'case_id': cid, 'scenario_type': sc['type'], 'description': sc['text'], 'previous': sc['prev'], 'revised': sc['new'], 'diff': diff(sc['prev'], sc['new']),
          'approval_blockers_for_revised': blockers, 'image_bytes_identical': sha[sc['prev']] == sha[sc['new']]}
    wf['expected_system_behavior'] = ('이미지 해시가 같으면 문자 검사를 반복하지 않아도 되지만 증빙 버전 변경은 새 이력으로 남기고, 해결된 조건과 남은 조건을 구분한다.' if sc['type'] == 'evidence_only'
                                      else '이전 지적의 해결 여부와 새 오류를 항목별로 구분해 기록하고, 남은 차단 조건이 없을 때만 승인 준비로 넘어간다.')
    jdump(gdir / 'workflow.json', wf)
    return {'case': case, 'deliveries': deliveries, 'obs': obs_all, 'judgments': judgments, 'wf': wf, 'docinfo': docinfo, 'ev': list(ev_files.values())}


def case_sheet(r):
    c = r['case']
    L = [f"# {c['id']} · {c['name']}", '', f"- 유형: {c['category']}  |  그룹 ID: {c['group']}  |  모든 자료는 가상(실존 브랜드·인물과 무관)",
         f"- 설계 의도: {c['seed_note']}", f"- 사용 계획: 매체 {', '.join(c['usage']['media'])} · 게시 {c['usage']['period']} · 지역 {c['usage']['region']} · 유료광고 {c['usage']['paid']}", '',
         '## 외주에 필요한 문서 (3단계)', '| 단계 | 문서 | 쪽수 | 파일 |', '|---|---|---|---|']
    for d in sorted(r['docinfo'], key=lambda x: x['stage']):
        L.append(f"| {d['stage']} | {d['title']} | {d['pages']} | input/docs/{d['pdf']} (+ .md) |")
    L += ['', '## 납품 증빙 문서', '| 증빙 | 버전 | 파일 | 메타 |', '|---|---|---|---|']
    for e in r['ev']:
        L.append(f"| {e['evidence_id']} | {e['version']} | input/evidence/{e['file']} | {json.dumps(e['meta'], ensure_ascii=False) if e['meta'] else '-'} |")
    L += ['', '## 납품 이미지', '| ID | 유형 | 의도한 편집 | 파일 |', '|---|---|---|---|']
    for d, dd in zip(c['deliveries'], r['deliveries']):
        L.append(f"| {d['id']} | {d['kind']} | {'; '.join(d['edits'])} | {dd['file']} ({dd['width']}×{dd['height']}) |")
    L += ['', '## 검사 조건(기준 정답)', '| ID | 항목 | 기대값 | 출처 |', '|---|---|---|---|']
    for k in c['criteria']:
        e = k.get('expected')
        if k['kind'] == 'count':
            e = f"{k['cls']} {k['op']} {k['n']}"
        elif k['kind'] in ('logo_set',):
            e = ', '.join(k['expected'])
        elif k['kind'] == 'text_set':
            e = ', '.join(k['expected'])
        elif e is None:
            e = {k0: v for k0, v in k.items() if k0 in ('size', 'mimes', 'ratio', 'min_px', 'evidence_id', 'words', 'attr', 'cls', 'evidence_prefix')} or k['kind']
        L.append(f"| {k['id']} | {k['label']} | {e} | {'; '.join(s['doc'] + ' ' + str(s['page']) + '쪽' for s in k['src'])} |")
    L += ['', '## 이미지별 정답 요약 (충족이 아닌 항목만)']
    for d in r['deliveries']:
        bad = [j for j in r['judgments'] if j['delivery_id'] == d['delivery_id'] and j['judgment'] != '충족']
        L.append(f"- {d['delivery_id']}: " + (', '.join(f"{j['criterion_id']} {j['label']} → {j['judgment']}({j['reason_code']})" for j in bad) or '모두 충족'))
    L += ['', '## 수정 시나리오', r['wf']['description'], '', '```json', json.dumps({k: v for k, v in r['wf'].items() if k not in ('case_id', 'description')}, ensure_ascii=False, indent=2), '```', '']
    return '\n'.join(L)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    registry, results = [], []
    for case in CASES:
        results.append(build_case(case, registry))
    for r in results:
        (OUT / r['case']['slug'] / 'case_sheet.md').write_text(case_sheet(r), encoding='utf-8')
        for f in sorted((OUT / r['case']['slug'] / 'gold').glob('*.json')):
            registry.append({'asset_id': f"{r['case']['id']}:{f.name}", 'case_id': r['case']['id'], 'group_id': r['case']['group'], 'area': 'gold(평가 전용)', 'type': 'gold_json', 'doc_stage': '',
                             'path': str(f.relative_to(OUT)).replace('\\', '/'), 'sha256': sha256_file(f), 'bytes': f.stat().st_size, 'real_or_synthetic': 'synthetic', 'created': TODAY,
                             'rights_holder': '팀(SKN33 1Team) · 가상 자료', 'fonts': '-', 'internal_review': 'allowed', 'training': 'denied(정답은 모델 입력 금지)', 'evaluation': 'allowed', 'demo': 'allowed',
                             'external_transmission': 'denied(승인 전)', 'public_redistribution': 'unknown', 'sensitivity': '없음(가상)', 'note': '서비스 입력·RAG 코퍼스에서 제외'})
    with open(OUT / 'registry.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(registry[0].keys()))
        w.writeheader()
        w.writerows(registry)
    rows = [j for r in results for j in r['judgments']]
    with open(OUT / 'labels.csv', 'w', encoding='utf-8-sig', newline='') as f:
        keys = ['case_id', 'delivery_id', 'criteria_version', 'criterion_id', 'label', 'applicable', 'judgment', 'reason_code', 'observed', 'applicability_basis']
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for j in rows:
            w.writerow({k: j.get(k) for k in keys})
    return results


if __name__ == '__main__':
    main()
    print('built', len(CASES))
