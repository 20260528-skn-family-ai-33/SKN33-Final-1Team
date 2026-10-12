# -*- coding: utf-8 -*-
import csv, json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from vo_lib import pdf_page_texts, sha256_file
from vo_cases import CASES

OUT = pathlib.Path(__file__).resolve().parents[1]
problems = []
P = problems.append

reg = list(csv.DictReader(open(OUT / 'registry.csv', encoding='utf-8-sig')))
for r in reg:
    f = OUT / r['path']
    if not f.exists():
        P(f'파일 없음 {r["path"]}')
    elif sha256_file(f) != r['sha256']:
        P(f'해시 불일치 {r["path"]}')
stats = {'docs': 0, 'quotes': 0, 'images': 0, 'regions': 0, 'judgments': 0, 'evidence': 0}
COMMON = {'rfp', 'nda', 'contract', 'sow', 'inspect'}
for case in CASES:
    cid, slug = case['id'], case['slug']
    cdir = OUT / slug
    inp = json.loads((cdir / 'input' / 'case_input.json').read_text(encoding='utf-8'))
    # 1. 3단계 문서 구성
    ids = {d['doc_id'] for d in inp['documents']}
    stages = {d['stage'] for d in inp['documents']}
    if not COMMON <= ids:
        P(f'{cid} 공통 문서 누락: {sorted(COMMON - ids)}')
    if len(stages) != 3:
        P(f'{cid} 3단계 문서가 모두 있어야 함: {stages}')
    # 2. 기준 근거 문구가 해당 문서·쪽에 있는지
    texts = {}
    for d in inp['documents']:
        texts[d['doc_id']] = pdf_page_texts(cdir / 'input' / 'docs' / d['pdf'])
        stats['docs'] += 1
        if len(texts[d['doc_id']]) != d['pages']:
            P(f'{cid} {d["doc_id"]} 쪽수 불일치')
    for c in case['criteria']:
        for s in c['src']:
            q = re.sub(r'\s+', '', s['quote'])
            stats['quotes'] += 1
            if s['doc'] not in texts:
                P(f'{cid} {c["id"]} 근거 문서 {s["doc"]} 없음')
            elif q not in texts[s['doc']][s['page'] - 1]:
                where = [i + 1 for i, t in enumerate(texts[s['doc']]) if q in t]
                P(f'{cid} {c["id"]} 근거 문구가 {s["doc"]} {s["page"]}쪽에 없음 (있는 쪽 {where}): {s["quote"]}')
    # 3. 증빙 PDF 내용 vs 메타
    for dv in inp['deliveries']:
        for e in dv['evidence']:
            stats['evidence'] += 1
            t = ''.join(pdf_page_texts(cdir / 'input' / 'evidence' / e['file']))
            src = next(d for d in case['deliveries'] if d['id'] == dv['delivery_id'])
            meta = next(m for eid, ver, m in src['evidence'] if eid == e['evidence_id'] and ver == e['version'])
            for k in ('start', 'end', 'region'):
                if k in meta and re.sub(r'\s+', '', str(meta[k])) not in t:
                    P(f'{cid} {dv["delivery_id"]} 증빙 {e["evidence_id"]} PDF에 메타 {k}={meta[k]} 없음')
    # 4. 관찰 좌표 · 겹침
    obs = json.loads((cdir / 'gold' / 'observations.json').read_text(encoding='utf-8'))['deliveries']
    for did, ob in obs.items():
        W, H = ob['file']['w'], ob['file']['h']
        stats['images'] += 1
        regs = ob['regions']
        stats['regions'] += len(regs)
        for r in regs:
            x0, y0, x1, y1 = r['bbox']
            if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
                P(f'{cid} {did} {r["region_id"]} 캔버스 밖 {r["bbox"]}')
        for i in range(len(regs)):
            for j in range(i + 1, len(regs)):
                a, b = regs[i]['bbox'], regs[j]['bbox']
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    P(f'{cid} {did} 문자 영역 겹침 {regs[i]["region_id"]} × {regs[j]["region_id"]}')
        for cls, objs in ob['objects'].items():
            for o in objs:
                x0, y0, x1, y1 = o['bbox']
                if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
                    P(f'{cid} {did} {o["instance_id"]} 캔버스 밖')
                for r in regs:
                    a = r['bbox']
                    if a[0] < x1 and x0 < a[2] and a[1] < y1 and y0 < a[3]:
                        P(f'{cid} {did} 문자 {r["region_id"]}({r["role"]})와 객체 {o["instance_id"]}({cls}) 겹침')
    # 5. 바이트
    sha = {d['delivery_id']: d['sha256'] for d in inp['deliveries']}
    for d in case['deliveries']:
        if d.get('copy_of') and sha[d['id']] != sha[d['copy_of']]:
            P(f'{cid} {d["id"]}는 {d["copy_of"]}와 바이트 동일해야 함')
    uniq = [d['id'] for d in case['deliveries'] if not d.get('copy_of')]
    if len({sha[i] for i in uniq}) != len(uniq):
        P(f'{cid} 서로 달라야 할 이미지의 해시가 같음')
    # 6. 판정 개수 · 설계 의도
    J = json.loads((cdir / 'gold' / 'judgments.json').read_text(encoding='utf-8'))['judgments']
    stats['judgments'] += len(J)
    if len(J) != len(case['deliveries']) * len(case['criteria']) * len(case['versions']):
        P(f'{cid} 판정 수 불일치')
    for did, exp in case['expect'].items():
        got = {j['criterion_id']: j['judgment'] for j in J if j['delivery_id'] == did}
        for crit in case['criteria']:
            want = exp.get(crit['id'], '충족')
            if got[crit['id']] != want:
                P(f'{cid} {did} {crit["id"]}({crit["label"]}): 기대 {want} / 엔진 {got[crit["id"]]}')
    for j in J:
        if j['judgment'] == '해당 없음' and not j['applicability_basis']:
            P(f'{cid} {j["delivery_id"]} {j["criterion_id"]} 해당 없음에 근거 없음')
    # 7. 수정 시나리오 합리성
    wf = json.loads((cdir / 'gold' / 'workflow.json').read_text(encoding='utf-8'))
    if wf['diff']['new_errors']:
        P(f'{cid} 수정본에 새 오류 {wf["diff"]["new_errors"]} — 시나리오 설명과 다름')
print(f"등록부 {len(reg)}행 · 문서 {stats['docs']}개 · 기준 근거 문구 {stats['quotes']}건 · 증빙 {stats['evidence']}건 · 이미지 {stats['images']}장 · 문자 영역 {stats['regions']}개 · 판정 {stats['judgments']}건")
print('문제:', len(problems))
for m in problems:
    print(' -', m)
