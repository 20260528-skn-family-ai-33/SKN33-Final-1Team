# -*- coding: utf-8 -*-
"""가상 발주 3건을 앱의 주제(data/topics/<id>/)로 내보낸다.

- 이미지: 납품 이미지 D1~D6을 변경 없이 sample-01~06.png로 복사한다(바이트 동일).
- 기준: 확정 기준 중 'OCR로 문구를 읽어 비교할 수 있는 항목'만 text 규칙으로, 개수·외관은 manual 규칙으로 만든다.
  문구 영역은 레이아웃(정답 좌표)에서 계산한 비율 좌표이며, 판정 정답(judgments)은 읽지 않는다.
- 로고·증빙·표시 의무·최상급 표현 등 앱이 지원하지 않는 검사는 규칙에 넣지 않는다.

표준 라이브러리만 사용한다. 프로젝트 루트에서: python data/virtual-orders/scripts/make_topics.py
"""
import hashlib
import json
import pathlib
import re
import shutil

HERE = pathlib.Path(__file__).resolve().parent
VO = HERE.parent
TOPICS = VO.parent / 'topics'

CUR = r'(?<![\d,A-Za-z])\d+(?:,\d{3})*원'


def text(role, contains='[가-힣]', **extra):
    return dict(kind='text', role=role, contains=contains, **extra)


def cur(role):
    return text(role, '[0-9]', extract=CUR, normalize='currency', format=r'\d+원')


SPEC = [
    dict(slug='VO-01-fnb', topic='fnb-ongi-v1', label='온기식탁(F&B)', dataset='fnb-ongi-content-v1',
         purpose='가상 식음료 매장(온기식탁) 신메뉴 광고의 문구 검수용 개발·시연 데이터',
         scope='메뉴명·가격·할인율·기간·원산지·알레르기·매장 정보 문구만 검사. 원산지/알레르기 글자 크기, 최상급 표현, 증빙 제출, 로고·규격은 앱이 검사하지 않음.',
         rules=[('F01', text('메뉴명', join_lines=True)), ('F02', cur('정가')), ('F03', cur('판매가')), ('F04', text('할인율', '[0-9]')),
                ('F11', text('기간', '[0-9]', normalize='time_dash', format=r'\d{2}\.\d{2}-\d{2}\.\d{2}', join_lines=True)),
                ('F06', text('원산지')), ('F08', text('알레르기')), ('F12', text('매장 정보')),
                ('F13', dict(kind='manual', expected='버섯 토핑 5개 이상'))]),
    dict(slug='VO-02-festival', topic='festival-dalbit-v1', label='달빛 페스타(페스티벌)', dataset='festival-dalbit-content-v1',
         purpose='가상 음악 페스티벌(달빛누리 뮤직 페스타) 포스터의 문구 검수용 개발·시연 데이터',
         scope='행사명·일정·장소·티켓·출연진 명칭·주최주관·우천 고지 문구만 검사. 출연자 사진 동의, 후원사 로고 승인 목록, 증빙, 규격은 앱이 검사하지 않음.',
         rules=[('G01', text('행사명')), ('G02', text('행사 일정', '[0-9]', normalize='time_dash')), ('G03', text('장소')),
                ('G04', text('티켓 가격', '[0-9]')), ('G05', text('출연자명', join_lines=True)), ('G08', text('주최·주관')),
                ('G09', text('우천 고지')), ('G07', dict(kind='manual', expected='승인된 후원사 로고 3개(달빛은행·누리푸드·하늘모터스)만 노출'))]),
    dict(slug='VO-03-virtual-person', topic='virtual-seoha-v1', label='가상인물 서하', dataset='virtual-seoha-content-v1',
         purpose='가상 인간 모델 ‘서하’ 광고 이미지의 문구 검수용 개발·시연 데이터',
         scope='브랜드·상품명·가격·기간·AI 생성물 표시 문구만 검사. 가상 인물 설정 일치, 표시 글자 크기, 허락서 범위, IP·AI 도구 증빙은 앱이 검사하지 않음.',
         rules=[('H01', text('브랜드 표기', '[A-Za-z]')), ('H02', text('상품명')), ('H03', cur('가격')),
                ('H04', text('기간', '[0-9]', normalize='time_dash', format=r'\d{2}\.\d{2}-\d{2}\.\d{2}')), ('H05', text('AI 표시')),
                ('H07', dict(kind='manual', expected='가상 인물 헤어: 검은 단발')), ('H08', dict(kind='manual', expected='시계 착용 손목: 왼쪽'))]),
]


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def region_for(regs, role, W, H):
    rs = [r for r in regs if r['role'] == role]
    assert rs, f'역할 {role} 영역 없음'
    x0, y0 = min(r['bbox'][0] for r in rs), min(r['bbox'][1] for r in rs)
    x1, y1 = max(r['bbox'][2] for r in rs), max(r['bbox'][3] for r in rs)
    bw, bh = x1 - x0, y1 - y0
    px, py = 0.05 * bw + 6, 0.2 * bh + 4
    a, b = max(0, x0 - px), max(0, y0 - py)
    c, d = min(W, x1 + px), min(H, y1 + py)
    return [round(a / W, 4), round(b / H, 4), round((c - a) / W, 4), round((d - b) / H, 4)], rs


def main():
    for s in SPEC:
        case_dir = VO / s['slug']
        gold = json.loads((case_dir / 'gold' / 'criteria_gold.json').read_text(encoding='utf-8'))
        crit = {c['id']: c for c in gold['criteria']}
        obs = json.loads((case_dir / 'gold' / 'observations.json').read_text(encoding='utf-8'))['deliveries']
        d1 = obs['D1']
        W, H = d1['file']['w'], d1['file']['h']
        rules = []
        for n, (cid, insp) in enumerate(s['rules'], 1):
            c = crit[cid]
            rule = {'id': f'C{n:02d}', 'name': c['label'], 'source_criterion': cid}
            if insp['kind'] == 'manual':
                rule['expected'] = insp['expected']
                rule['check'] = '자동 검사기를 연결하지 않은 항목이다. 사람이 확인해야 한다.'
                rule['inspection'] = {'kind': 'manual'}
            else:
                region, rs = region_for(d1['regions'], insp['role'], W, H)
                # 의도한 문자만 이 영역에 들어오는지(다른 문자 영역의 중심이 섞이지 않는지) 확인
                rx, ry, rw, rh = region
                inside = [r['region_id'] for r in d1['regions']
                          if rx <= (r['bbox'][0] + r['bbox'][2]) / 2 / W <= rx + rw and ry <= (r['bbox'][1] + r['bbox'][3]) / 2 / H <= ry + rh
                          and re.search(insp.get('contains', '.'), r['transcription_raw'])]
                assert sorted(inside) == sorted(r['region_id'] for r in rs), f"{s['slug']} {cid}: 영역에 다른 문자가 섞임 {inside}"
                inspection = {k: v for k, v in insp.items() if k != 'role'}
                inspection['region'] = region
                rule['expected'] = c['expected'] if isinstance(c['expected'], str) else c['expected']
                if cid == 'G05':
                    rule['expected'] = ' '.join(c['expected'])
                rule['check'] = '근거 문구: ' + c['sources'][0]['quote']
                rule['inspection'] = inspection
            rules.append(rule)
        out = TOPICS / s['topic']
        if out.exists():
            shutil.rmtree(out)
        (out / 'images').mkdir(parents=True)
        for i in range(1, 7):
            src = case_dir / 'input' / 'deliveries' / f"{gold['case_id']}_D{i}.png"
            dst = out / 'images' / f'sample-{i:02d}.png'
            shutil.copyfile(src, dst)
            assert sha(src) == sha(dst)
        data = {'dataset': s['dataset'], 'purpose': s['purpose'], 'scope': s['scope'], 'rules': rules,
                'common': '가상 브랜드·가상 행사·가상 인물의 합성 광고 이미지이며 실제 광고나 업무 자료가 아니다. 문구 영역은 이 이미지 레이아웃에 맞춘 설정이다.',
                'format_note': f"실제 크기는 개별 파일 메타데이터로 기록한다({W}×{H} PNG). 원본은 data/virtual-orders/{s['slug']}에 있다. sample-01~06은 D1~D6을 변경 없이 복사한 것이다.",
                'output_example': {'sample_id': 'sample-XX', 'results': [{'rule_id': 'C01', 'verdict': '충족 | 미충족 | 확인 필요', 'observation': '관찰한 내용', 'evidence': '이미지 내 위치'}]}}
        (out / 'criteria.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"{s['topic']}: 규칙 {len(rules)}개(text {sum(1 for r in rules if r['inspection']['kind'] == 'text')}, manual {sum(1 for r in rules if r['inspection']['kind'] == 'manual')}), 이미지 6장")


if __name__ == '__main__':
    main()
