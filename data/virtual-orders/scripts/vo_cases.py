# -*- coding: utf-8 -*-
"""가상 발주 3건(F&B · 페스티벌 · 가상인물). 회사·인물·연락처·금액은 모두 가상이며 실존과 무관하다(우연한 유사성 가능)."""
from vo_lib import Ad

PNG = ['image/png']


def S(doc, page, quote):
    return {'doc': doc, 'page': page, 'quote': quote}


# ----------------------------------------------------------------- 공통 문서 템플릿
def rfp(c, extra_title, extra_items):
    return [[
        ('h1', '제안요청서 (RFP)'),
        ('p', f"{c['no']}-RFP · 발행 {c['rfp_date']} · 발주: {c['client']} · 수신: 소재 제작 외주 후보사"),
        ('h2', '1. 프로젝트 목적'), ('p', c['purpose']),
        ('h2', '2. 산출물'), ('li', c['deliverable']), ('li', f"게시 매체: {c['media_label']} · 게시 기간 {c['post_start']} ~ {c['post_end']} · 지역 {c['region']}"),
        ('h2', '3. 예산과 일정'), ('li', f"예산 {c['budget']}(VAT 별도) 이내"), ('li', f"제안서 마감 {c['proposal_due']} · 착수 {c['kickoff']} · 납품 {c['delivery_date']}"),
        ('h2', '4. 제안서 제출 항목'), ('li', '제작 방식과 사용 도구, 일정, 견적, 무상 수정 정책(횟수·기간)'),
        ('h2', extra_title), *[('li', t) for t in extra_items],
        ('h2', '6. 비밀유지'), ('li', f"브랜드 자료와 미공개 정보는 비밀유지계약서(NDA, {c['nda_date']} 체결) 이후에 제공한다."),
    ]]


def nda(c):
    return [[
        ('h1', '비밀유지계약서 (NDA)'),
        ('p', f"{c['no']}-NDA · 체결일 {c['nda_date']} · 갑 {c['client']} · 을 {c['vendor']}"),
        ('h2', '제1조 (목적)'), ('p', f"{c['project']} 검토·수행 과정에서 갑이 을에게 제공하는 비밀정보의 보호에 관한 사항을 정한다."),
        ('h2', '제2조 (비밀정보)'), ('p', '브랜드 자료, 미공개 제품·행사 정보, 마케팅 전략, 가격 정책 등 갑이 비밀로 표시하거나 성질상 비밀인 정보를 말한다.'),
        ('h2', '제3조 (의무)'), ('p', '을은 비밀정보를 제3자에게 누설하지 않으며 본 계약의 목적 범위에서 필요한 인원에게만 공개한다.'),
        ('h2', '제4조 (목적 외 사용 금지)'), ('p', '을은 비밀정보를 AI 모델의 학습·미세조정에 사용하지 않는다.'),
        ('h2', '제5조 (반환과 파기)'), ('p', '프로젝트 종료 후 14일 이내에 비밀정보와 중간 산출물을 반환하거나 파기하고 서면으로 확인한다.'),
        ('h2', '제6조 (기간)'), ('p', '체결일부터 3년.'),
        ('h2', '별첨 · 비밀정보 제공 기록'), ('li', f"{c['provided_date']} {c['provided_items']} 제공 (NDA 체결일 이후)."),
    ]]


def contract(c, special_title, special_items):
    return [[
        ('h1', '외주 용역 계약서 (1/2) 일반 조항'),
        ('p', f"{c['no']}-CON · 계약일 {c['contract_date']} · 갑 {c['client']} · 을 {c['vendor']} · 기간 {c['kickoff']} ~ {c['delivery_date']}"),
        ('h2', '제1조 (목적)'), ('p', f"{c['project']}의 제작을 갑이 을에게 위탁하고 을이 수행하는 데 필요한 사항을 정한다."),
        ('h2', '제2조 (대금)'), ('p', f"계약금액은 {c['budget']}(VAT 별도)이며 선금 50%는 착수 시, 잔금 50%는 제8조의 검수조서 서명 후 7일 이내에 지급한다."),
        ('h2', '제3조 (용역 범위)'), ('p', '용역 범위와 규격은 별첨 과업지시서에 따른다. 과업지시서는 본 계약의 일부이다.'),
        ('h2', '제4조 (지식재산권)'), ('p', '납품 결과물의 저작재산권은 잔금 지급 완료 시 갑에게 귀속한다. 을은 제3자 권리를 침해하지 않음을 보증하고 침해 주장 시 을의 책임으로 해결한다.'),
        ('h2', '제5조 (수정)'), ('p', '납품일부터 14일 이내에 무상 수정을 2회 제공한다. 초과 수정은 별도 협의한다.'),
    ], [
        ('h1', f'외주 용역 계약서 (2/2) {special_title}'),
        *[('li', t) for t in special_items],
        ('h2', '제8조 (검수)'), ('p', '갑은 납품 후 5영업일 이내에 과업지시서 기준으로 검수하고, 합격 시 갑과 을이 검수조서에 서명한다.'),
        ('h2', '제9조 (승인)'), ('p', '최종 승인은 최신 버전에 대해서만 유효하며, 승인 검토 중 새 파일이 접수되면 검토 대상은 갱신된다.'),
    ]]


def inspect_form(c, criteria, titles):
    rows = [['항목', '기준(출처)', '결과', '비고']] + [[k['label'], '; '.join(f"{titles[s['doc']]} {s['page']}쪽" for s in k['src']), '', ''] for k in criteria]
    return [[
        ('h1', '검수조서 (검수 확인서) 양식'),
        ('p', f"{c['no']}-INS · 계약 제8조 · 서명 후 잔금 50%를 지급한다. (작성 전 양식)"),
        ('h2', '1. 검수 대상'), ('p', f"{c['project']} · 납품일 {c['delivery_date']} · 납품 패키지 버전·해시를 기재한다."),
        ('h2', '2. 검수 항목표'), ('table', rows, [3, 4, 2, 3]),
        ('h2', '3. 서명'), ('p', '을(제작사) 확인 · 갑 검수 담당 · 갑 최종 승인자. 서명은 사람이 한다. 시스템·AI는 서명이나 대금 지급을 대신하지 않는다.'),
    ]]


def simple_doc(title, no, items_by_section, foot=None):
    blocks = [('h1', title), ('p', no)]
    for h, items in items_by_section:
        blocks.append(('h2', h))
        blocks += [('li', t) if not t.startswith('§') else ('p', t[1:]) for t in items]
    if foot:
        blocks.append(('foot', foot))
    return [blocks]


def evidence_pdf(title, lines):
    return [[('h1', title), ('p', '※ 시연용 가상 증빙 문서입니다.')] + [('li', ln) for ln in lines]]


# ================================================================= VO-01 F&B
def ad_v01(p):
    P = dict(sale='12,000원', origin='쌀·버섯·들깨: 국내산 / 치즈: 이탈리아산', allergy='알레르기: 우유·대두·밀 함유', tagline='가을 한 그릇의 위로',
             mushrooms=5, tiny=False, bg=('#fbf3e6', '#f3e2c2'), logo='ref')
    P.update(p)
    a = Ad(1080, 1350, P['bg'])
    a.logo({'glyph': 'O', 'color': '#7a4a1e'}, (60, 60), 110, P['logo'])
    a.text('r_brand', '브랜드 표기', 'ONGI TABLE', (230, 100), 38, '#7a4a1e', 700, 'sans', 'la', spacing=6)
    a.text('r_menu', '메뉴명', '들깨 버섯 리조또 세트', (540, 300), 80, '#4a2e12', 900, 'serif')
    a.text('r_tag', '광고 문구', P['tagline'], (540, 390), 34, '#7a4a1e', 500)
    a.bowl(540, 700)
    for i, (x, y) in enumerate([(430, 700), (500, 680), (580, 690), (650, 715), (540, 740)][:P['mushrooms']], 1):
        a.mushroom(f'mushroom_{i}', x, y, 32)
    a.ellipse((800, 460, 1000, 660), '#b3341f')
    a.text('r_disc', '할인율', '20% 할인', (900, 560), 40, '#ffffff', 800)
    r = a.text('r_list', '정가', '15,000원', (330, 980), 46, '#7a7a7a', 500)
    y = (r['bbox'][1] + r['bbox'][3]) // 2
    a.line([(r['bbox'][0] - 6, y), (r['bbox'][2] + 6, y)], '#7a7a7a', 4)
    a.text('r_sale', '판매가', P['sale'], (700, 985), 96, '#b3341f', 900)
    a.text('r_period', '기간', '11.02 ~ 11.15', (540, 1075), 44, '#4a2e12', 700)
    if P['tiny']:
        a.text('r_origin', '원산지', P['origin'], (540, 1150), 12, '#e6d2ac', 400, legibility='ambiguous', note='12px·저대비')
        if P['allergy']:
            a.text('r_allergy', '알레르기', P['allergy'], (540, 1200), 12, '#e6d2ac', 400, legibility='ambiguous', note='12px·저대비')
    else:
        a.text('r_origin', '원산지', P['origin'], (540, 1150), 28, '#4a2e12', 500)
        if P['allergy']:
            a.text('r_allergy', '알레르기', P['allergy'], (540, 1200), 28, '#4a2e12', 500)
    a.text('r_store', '매장 정보', '서울 가상구 온기로 12 · 02-000-0000', (540, 1270), 28, '#7a4a1e', 500)
    return a


C1 = dict(no='VO1', client='온기식탁(가상)', vendor='소담 스튜디오(가상)', project='들깨 버섯 리조또 세트 가을 신메뉴 프로모션 소재',
          purpose='가을 신메뉴 ‘들깨 버섯 리조또 세트’의 인스타그램 유료 광고 이미지 1종을 제작한다.',
          deliverable='인스타그램 피드용 광고 이미지 1종 (1080×1350 PNG)', media_label='인스타그램 피드(국내 유료 광고)', region='대한민국',
          post_start='2026-11-02', post_end='2026-11-15', budget='1,800,000원', proposal_due='2026-10-14', kickoff='2026-10-19',
          delivery_date='2026-10-30', rfp_date='2026-10-12', nda_date='2026-10-15', contract_date='2026-10-19',
          provided_date='2026-10-16', provided_items='메뉴 원재료·원산지·알레르기 자료와 로고 원본')
V1_CRIT = [
    {'id': 'F01', 'label': '메뉴명', 'kind': 'text_equals', 'role': '메뉴명', 'expected': '들깨 버섯 리조또 세트', 'src': [S('promo', 1, '메뉴명은 “들깨 버섯 리조또 세트”로 표기한다.')]},
    {'id': 'F02', 'label': '정가', 'kind': 'text_equals', 'role': '정가', 'expected': '15,000원', 'src': [S('promo', 1, '정가 15,000원')]},
    {'id': 'F03', 'label': '판매가', 'kind': 'text_equals', 'role': '판매가', 'expected': '12,000원', 'src': [S('promo', 1, '판매가 12,000원')]},
    {'id': 'F04', 'label': '할인율', 'kind': 'text_equals', 'role': '할인율', 'expected': '20% 할인', 'src': [S('promo', 1, '할인율 20% 할인')]},
    {'id': 'F05', 'label': '가격 정합성', 'kind': 'price_consistency', 'src': [S('promo', 1, '판매가는 정가에 할인율을 적용한 금액과 일치해야 한다.')]},
    {'id': 'F06', 'label': '원산지 표기', 'kind': 'text_equals', 'role': '원산지', 'expected': '쌀·버섯·들깨: 국내산 / 치즈: 이탈리아산', 'src': [S('menu', 1, '원산지는 “쌀·버섯·들깨: 국내산 / 치즈: 이탈리아산”으로 표기한다.')]},
    {'id': 'F07', 'label': '원산지 글자 크기', 'kind': 'text_min_size', 'role': '원산지', 'min_px': 26, 'src': [S('label', 1, '원산지 표기는 26px 이상으로 한다.')]},
    {'id': 'F08', 'label': '알레르기 표기', 'kind': 'text_equals', 'role': '알레르기', 'expected': '알레르기: 우유·대두·밀 함유', 'src': [S('menu', 1, '알레르기 유발 원료는 “알레르기: 우유·대두·밀 함유”로 표기한다.')]},
    {'id': 'F09', 'label': '알레르기 글자 크기', 'kind': 'text_min_size', 'role': '알레르기', 'min_px': 26, 'src': [S('label', 1, '알레르기 표기는 26px 이상으로 한다.')]},
    {'id': 'F10', 'label': '근거 없는 최상급 표현 금지', 'kind': 'text_forbidden', 'words': ['최고', '1위', '유일', '최초'], 'src': [S('label', 1, '“최고”, “1위”, “유일”, “최초” 등 근거 없는 최상급 표현은 쓰지 않는다.')]},
    {'id': 'F11', 'label': '행사 기간', 'kind': 'text_equals', 'role': '기간', 'expected': '11.02 ~ 11.15', 'src': [S('promo', 1, '행사 기간은 “11.02 ~ 11.15”로 표기한다.')]},
    {'id': 'F12', 'label': '매장 정보', 'kind': 'text_equals', 'role': '매장 정보', 'expected': '서울 가상구 온기로 12 · 02-000-0000', 'src': [S('promo', 1, '매장 정보는 “서울 가상구 온기로 12 · 02-000-0000”으로 표기한다.')]},
    {'id': 'F13', 'label': '버섯 토핑 개수', 'kind': 'count', 'cls': '버섯 토핑', 'op': '>=', 'n': 5, 'src': [S('promo', 2, '버섯 토핑이 5개 이상 보이게 한다.')]},
    {'id': 'F14', 'label': '로고 변형 금지', 'kind': 'logo_variant', 'src': [S('guide', 1, '로고는 가로세로 비율과 색을 변경하지 않는다.')]},
    {'id': 'F15', 'label': '규격', 'kind': 'file_spec', 'size': (1080, 1350), 'mimes': PNG, 'src': [S('sow', 1, '규격은 1080×1350 PNG로 한다.')]},
    {'id': 'F16', 'label': '원산지·알레르기 확인서 제출', 'kind': 'evidence_required', 'evidence_id': 'origin_allergen_sheet', 'src': [S('contract', 2, '원산지·알레르기 확인서를 납품과 함께 제출한다.')]},
    {'id': 'F17', 'label': '촬영물 이용 확인서 제출', 'kind': 'evidence_required', 'evidence_id': 'photo_license', 'src': [S('contract', 2, '촬영물과 이미지의 이용 권리 확인서를 납품과 함께 제출한다.')]},
]
V1_DOCS = [
    {'id': 'rfp', 'stage': 1, 'title': '제안요청서 (RFP)', 'pages': rfp(C1, '5. 표시 규정 준수', ['원산지와 알레르기 정보를 소재에 명확히 표기한다.', '근거 없는 최상급·과장 표현을 쓰지 않는다. (관련 법령 확인 필요)'])},
    {'id': 'nda', 'stage': 1, 'title': '비밀유지계약서 (NDA)', 'pages': nda(C1)},
    {'id': 'menu', 'stage': 1, 'title': '메뉴 정보서 (원재료·원산지·알레르기)', 'pages': simple_doc('메뉴 정보서', 'VO1-MENU · 온기식탁 · 2026-10-16', [
        ('1. 메뉴', ['메뉴명: 들깨 버섯 리조또 세트 (리조또 + 샐러드 + 음료)']),
        ('2. 원산지와 알레르기', ['원산지는 “쌀·버섯·들깨: 국내산 / 치즈: 이탈리아산”으로 표기한다.', '알레르기 유발 원료는 “알레르기: 우유·대두·밀 함유”로 표기한다.']),
    ], '※ 원산지·알레르기 표시 의무의 범위는 매장·가맹 규모에 따라 다를 수 있어 법령 확인이 필요하다.')},
    {'id': 'promo', 'stage': 1, 'title': '프로모션 조건서', 'pages': [[
        ('h1', '프로모션 조건서 (1/2) 가격'),
        ('p', 'VO1-PROMO · 온기식탁 · 2026-10-16'),
        ('li', '메뉴명은 “들깨 버섯 리조또 세트”로 표기한다.'),
        ('table', [['항목', '표기', '비고'], ['정가', '15,000원', '취소선으로 표기'], ['판매가', '12,000원', '큰 글씨로 표기'], ['할인율', '20% 할인', '배지 안에 표기']], [2, 3, 4]),
        ('li', '판매가는 정가에 할인율을 적용한 금액과 일치해야 한다.'),
        ('li', '행사 기간은 “11.02 ~ 11.15”로 표기한다.'),
        ('li', '매장 정보는 “서울 가상구 온기로 12 · 02-000-0000”으로 표기한다.'),
    ], [
        ('h1', '프로모션 조건서 (2/2) 시각'),
        ('li', '버섯 토핑이 5개 이상 보이게 한다.'),
        ('li', '음식 사진을 대신하는 일러스트는 실제 메뉴와 다른 재료를 그리지 않는다.'),
    ]]},
    {'id': 'label', 'stage': 2, 'title': '표시·광고 기준서 (가상 발주사 내부 기준)', 'pages': simple_doc('표시·광고 기준서', 'VO1-LABEL · 온기식탁 내부 기준 · 2026-10-16', [
        ('1. 글자 크기', ['원산지 표기는 26px 이상으로 한다.', '알레르기 표기는 26px 이상으로 한다.']),
        ('2. 표현', ['“최고”, “1위”, “유일”, “최초” 등 근거 없는 최상급 표현은 쓰지 않는다.']),
    ], '※ 위 수치는 가상 발주사의 내부 기준이며 법정 기준이 아니다.')},
    {'id': 'contract', 'stage': 2, 'title': '외주 용역 계약서', 'pages': contract(C1, '특약 (표시·증빙)', [
        '제6조 (증빙) 을은 원산지·알레르기 확인서를 납품과 함께 제출한다.',
        '제7조 (증빙) 을은 촬영물과 이미지의 이용 권리 확인서를 납품과 함께 제출한다.'])},
    {'id': 'sow', 'stage': 3, 'title': '과업지시서 (SOW)', 'pages': simple_doc('과업지시서 (SOW)', 'VO1-SOW · 계약서 별첨 1', [
        ('1. 산출물', ['인스타그램 피드용 광고 이미지 1종.', '규격은 1080×1350 PNG로 한다.']),
        ('2. 증빙', ['원산지·알레르기 확인서, 촬영물 이용 확인서를 납품과 함께 제출한다.']),
        ('3. 일정', ['착수 2026-10-19 · 시안 2026-10-26 · 납품 2026-10-30']),
    ])},
    {'id': 'guide', 'stage': 3, 'title': '브랜드 로고 가이드', 'pages': simple_doc('온기식탁 로고 가이드', 'VO1-GUIDE', [('로고', ['로고는 가로세로 비율과 색을 변경하지 않는다.', '브랜드명은 항상 대문자 ONGI TABLE로 쓴다.'])])},
]
V1_EVID = {
    'origin_allergen_sheet': ('원산지·알레르기 확인서', lambda m: evidence_pdf('원산지·알레르기 확인서', ['메뉴: 들깨 버섯 리조또 세트', '원산지: 쌀·버섯·들깨 국내산 / 치즈 이탈리아산', '알레르기: 우유·대두·밀 함유', '확인: 온기식탁 주방 책임자(가상) · 2026-10-16'])),
    'photo_license': ('촬영물·이미지 이용 확인서', lambda m: evidence_pdf('촬영물·이미지 이용 확인서', ['일러스트와 배경은 제작사 자체 제작물(가상)', '상업적 이용 허용 · 게시 기간 내 사용 가능', '확인: 소담 스튜디오(가상) · 2026-10-28'])),
}
VO1 = {
    'id': 'VO-01', 'slug': 'VO-01-fnb', 'group': 'VG01', 'name': '온기식탁 · 들깨 버섯 리조또 세트', 'category': 'F&B (식음료 매장 신메뉴)', 'ctx': C1,
    'seed_note': '원산지·알레르기·최상급 표현·가격 정합성 등 식품 광고에 필요한 표시와 증빙을 시험. 원산지·알레르기 글자 크기 기준은 가상 발주사 내부 기준.',
    'usage': {'media': ['instagram_feed'], 'period': '2026-11-02 ~ 2026-11-15', 'region': '대한민국', 'paid': True, 'post_start': '2026-11-02', 'post_end': '2026-11-15'},
    'canvas': (1080, 1350, 'png'), 'brand_logo': {'glyph': 'O', 'color': '#7a4a1e'}, 'docs': V1_DOCS, 'criteria': V1_CRIT, 'versions': ['v1'],
    'render': ad_v01, 'evidence_docs': V1_EVID,
    'deliveries': [
        {'id': 'D1', 'kind': '정상', 'params': {}, 'evidence': [('origin_allergen_sheet', 'v1', {}), ('photo_license', 'v1', {})], 'edits': ['모든 조건·증빙 충족']},
        {'id': 'D2', 'kind': '단일 오류', 'params': {'sale': '13,000원', 'bg': ('#fbf1e0', '#f1dcb4')}, 'evidence': [('origin_allergen_sheet', 'v1', {}), ('photo_license', 'v1', {})], 'edits': ['판매가 12,000원 → 13,000원 (판매가·가격 정합성에 영향)']},
        {'id': 'D3', 'kind': '복합 오류', 'params': {'allergy': None, 'origin': '쌀·버섯·들깨: 국내산 / 치즈: 프랑스산', 'tagline': '서울 최고의 리조또', 'bg': ('#f9efe0', '#efd9b0')},
         'evidence': [('origin_allergen_sheet', 'v1', {}), ('photo_license', 'v1', {})], 'edits': ['알레르기 문구 삭제', '치즈 원산지 이탈리아산 → 프랑스산', '광고 문구에 근거 없는 최상급 “최고”']},
        {'id': 'D4', 'kind': '판독 불확실', 'params': {'tiny': True, 'mushrooms': 5}, 'evidence': [('origin_allergen_sheet', 'v1', {}), ('photo_license', 'v1', {})], 'edits': ['원산지·알레르기를 12px·저대비로 표기(사람도 확정 어려움)']},
        {'id': 'D5', 'kind': '증빙 누락', 'params': {'bg': ('#fcf4e8', '#f4e4c6')}, 'evidence': [('photo_license', 'v1', {})], 'edits': ['이미지는 정상이나 원산지·알레르기 확인서가 제출 목록에 없음']},
        {'id': 'D6', 'kind': '수정본', 'params': {'bg': ('#fbf2e4', '#f2e0bb')}, 'evidence': [('origin_allergen_sheet', 'v1', {}), ('photo_license', 'v1', {})], 'revision_of': 'D3', 'edits': ['D3의 지적 4건(알레르기·치즈 원산지·최상급 표현)을 모두 수정']},
    ],
    'expect': {'D1': {}, 'D2': {'F03': '미충족', 'F05': '미충족'}, 'D3': {'F06': '미충족', 'F08': '미충족', 'F09': '미충족', 'F10': '미충족'},
               'D4': {'F06': '확인 필요', 'F07': '미충족', 'F08': '확인 필요', 'F09': '미충족'}, 'D5': {'F16': '미충족'}, 'D6': {}},
    'scenario': {'type': 'fix', 'prev': 'D3', 'new': 'D6', 'text': 'D3 지적(알레르기 누락·치즈 원산지 오기·최상급 표현) → D6 수정본. 재검수에서 모두 해결, 새 오류 없음.'},
}


# ================================================================= VO-02 페스티벌
def ad_v02(p):
    P = dict(ticket='1일권 55,000원 · 2일권 98,000원', names=['라이네', '새벽결', '오로라정원'], sponsors=[('달빛은행', '#1f4e8c', 0), ('누리푸드', '#a63d2f', 0), ('하늘모터스', '#2f6b4f', 0)],
             tiny=False, bg=('#1d1b3a', '#3b2a5c'), logo='ref')
    P.update(p)
    a = Ad(1080, 1350, P['bg'])
    a.logo({'glyph': 'D', 'color': '#f0b429'}, (60, 60), 110, P['logo'])
    a.text('r_title', '행사명', '달빛누리 뮤직 페스타', (540, 260), 76, '#fff3c4', 900, 'serif')
    a.text('r_date', '행사 일정', '11.21(토) ~ 11.22(일)', (540, 380), 56, '#f0b429', 800)
    a.text('r_place', '장소', '달빛공원 야외무대', (540, 460), 44, '#ffffff', 600)
    people = [('라이네', 270, '#8fb8de'), ('새벽결', 540, '#c9a0dc'), ('오로라정원', 810, '#9ad1b0')]
    for i, (nm, x, col) in enumerate(people, 1):
        a.avatar(f'artist_{i}', nm, x, 700, 100, col)
        a.text(f'r_artist_{i}', '출연자명', P['names'][i - 1], (x, 850), 40, '#ffffff', 700)
    a.text('r_ticket', '티켓 가격', P['ticket'], (540, 970), 38, '#f0e6ff', 600)
    sp = P['sponsors']
    slots = {3: [(110, 370), (410, 670), (710, 970)], 4: [(62, 282), (307, 527), (552, 772), (797, 1017)]}[len(sp)]
    for i, ((nm, col, occ), (x0, x1)) in enumerate(zip(sp, slots), 1):
        a.sponsor(f'sponsor_{i}', nm, (x0, 1060, x1, 1130), col, occ)
    if P['tiny']:
        a.text('r_host', '주최·주관', '주최 달빛시 문화재단 · 주관 누리기획', (540, 1195), 14, '#4a3a72', 400, legibility='ambiguous', note='14px·저대비')
    else:
        a.text('r_host', '주최·주관', '주최 달빛시 문화재단 · 주관 누리기획', (540, 1195), 30, '#e6dcff', 500)
    a.text('r_rain', '우천 고지', '우천 시에도 공연은 진행됩니다', (540, 1265), 28, '#e6dcff', 500)
    return a


C2 = dict(no='VO2', client='달빛시 문화재단(가상)', vendor='밤하늘 디자인랩(가상)', project='달빛누리 뮤직 페스타 2026 홍보 포스터',
          purpose='‘달빛누리 뮤직 페스타 2026’의 인스타그램 홍보 포스터 1종을 제작한다.',
          deliverable='인스타그램 피드용 홍보 포스터 1종 (1080×1350 PNG)', media_label='인스타그램 피드(국내 홍보)', region='대한민국',
          post_start='2026-11-09', post_end='2026-11-22', budget='4,500,000원', proposal_due='2026-10-07', kickoff='2026-10-12',
          delivery_date='2026-11-04', rfp_date='2026-10-05', nda_date='2026-10-08', contract_date='2026-10-12',
          provided_date='2026-10-09', provided_items='라인업 자료·후원사 로고 원본·행사 일정')
V2_CRIT = [
    {'id': 'G01', 'label': '행사명', 'kind': 'text_equals', 'role': '행사명', 'expected': '달빛누리 뮤직 페스타', 'src': [S('overview', 1, '행사명은 “달빛누리 뮤직 페스타”로 표기한다.')]},
    {'id': 'G02', 'label': '행사 일정(요일 포함)', 'kind': 'text_equals', 'role': '행사 일정', 'expected': '11.21(토) ~ 11.22(일)', 'src': [S('overview', 1, '행사 일정은 “11.21(토) ~ 11.22(일)”로 표기한다.')]},
    {'id': 'G03', 'label': '장소', 'kind': 'text_equals', 'role': '장소', 'expected': '달빛공원 야외무대', 'src': [S('overview', 1, '장소는 “달빛공원 야외무대”로 표기한다.')]},
    {'id': 'G04', 'label': '티켓 가격', 'kind': 'text_equals', 'role': '티켓 가격', 'expected': '1일권 55,000원 · 2일권 98,000원', 'src': [S('overview', 1, '티켓 가격은 “1일권 55,000원 · 2일권 98,000원”으로 표기한다.')]},
    {'id': 'G05', 'label': '출연진 명칭', 'kind': 'text_set', 'role': '출연자명', 'expected': ['라이네', '새벽결', '오로라정원'], 'src': [S('artist', 1, '표기할 출연진은 라이네, 새벽결, 오로라정원이다.')]},
    {'id': 'G06', 'label': '출연자 사진 동의', 'kind': 'consent_for_shown', 'cls': '출연자 사진', 'evidence_prefix': 'consent:', 'src': [S('artist', 1, '출연자의 사진은 서면 동의서가 있는 경우에만 사용한다.')]},
    {'id': 'G07', 'label': '후원사 로고(승인 목록)', 'kind': 'logo_set', 'cls': '후원사 로고', 'expected': ['달빛은행', '누리푸드', '하늘모터스'], 'src': [S('sponsor', 1, '노출할 후원사는 달빛은행, 누리푸드, 하늘모터스이며 승인되지 않은 후원사 로고는 쓰지 않는다.')]},
    {'id': 'G08', 'label': '주최·주관 표기', 'kind': 'text_equals', 'role': '주최·주관', 'expected': '주최 달빛시 문화재단 · 주관 누리기획', 'src': [S('overview', 1, '주최·주관은 “주최 달빛시 문화재단 · 주관 누리기획”으로 표기한다.')]},
    {'id': 'G09', 'label': '우천 고지', 'kind': 'text_equals', 'role': '우천 고지', 'expected': '우천 시에도 공연은 진행됩니다', 'src': [S('overview', 2, '“우천 시에도 공연은 진행됩니다”를 하단에 넣는다.')]},
    {'id': 'G10', 'label': '현수막 신고번호(현수막 한정)', 'kind': 'text_equals', 'role': '신고번호', 'expected': '신고번호 가상-2026-0001', 'media': ['banner_offline'],
     'na_basis': '현수막에만 적용되는 조건이며 이번 사용 계획의 매체는 인스타그램 피드이므로 적용 대상이 아님(현수막 게시 계획서)', 'src': [S('banner', 1, '현수막에는 게시 신고번호를 표기한다.')]},
    {'id': 'G11', 'label': '로고 변형 금지', 'kind': 'logo_variant', 'src': [S('guide', 1, '행사 로고는 가로세로 비율과 색을 변경하지 않는다.')]},
    {'id': 'G12', 'label': '규격', 'kind': 'file_spec', 'size': (1080, 1350), 'mimes': PNG, 'src': [S('sow', 1, '규격은 1080×1350 PNG로 한다.')]},
    {'id': 'G13', 'label': '후원사 로고 사용 승인서 제출', 'kind': 'evidence_required', 'evidence_id': 'sponsor_approval', 'src': [S('contract', 2, '후원사 로고 사용 승인서를 납품과 함께 제출한다.')]},
    {'id': 'G14', 'label': '폰트·음원 라이선스 확인서 제출', 'kind': 'evidence_required', 'evidence_id': 'font_license', 'src': [S('contract', 2, '사용한 폰트와 이미지의 라이선스 확인서를 납품과 함께 제출한다.')]},
]
V2_DOCS = [
    {'id': 'rfp', 'stage': 1, 'title': '제안요청서 (RFP)', 'pages': rfp(C2, '5. 행사 홍보 준수 사항', ['출연자 사진은 서면 동의가 있는 경우에만 사용한다.', '후원사 로고는 승인된 목록만 노출한다.', '현수막은 옥외광고물 관련 허가·신고 절차를 별도로 확인한다. (관련 법령 확인 필요)'])},
    {'id': 'nda', 'stage': 1, 'title': '비밀유지계약서 (NDA)', 'pages': nda(C2)},
    {'id': 'overview', 'stage': 1, 'title': '행사 개요서', 'pages': [[
        ('h1', '행사 개요서 (1/2) 표기'),
        ('p', 'VO2-OVERVIEW · 달빛시 문화재단 · 2026-10-09'),
        ('li', '행사명은 “달빛누리 뮤직 페스타”로 표기한다.'),
        ('li', '행사 일정은 “11.21(토) ~ 11.22(일)”로 표기한다.'),
        ('li', '장소는 “달빛공원 야외무대”로 표기한다.'),
        ('li', '티켓 가격은 “1일권 55,000원 · 2일권 98,000원”으로 표기한다.'),
        ('li', '주최·주관은 “주최 달빛시 문화재단 · 주관 누리기획”으로 표기한다.'),
    ], [
        ('h1', '행사 개요서 (2/2) 안내'),
        ('li', '“우천 시에도 공연은 진행됩니다”를 하단에 넣는다.'),
        ('li', '안전 사유로 일부 순서가 조정될 수 있다. (본 포스터에는 표기하지 않는다.)'),
    ]]},
    {'id': 'artist', 'stage': 2, 'title': '출연자 이미지·명칭 사용 허락 기준', 'pages': simple_doc('출연자 이미지·명칭 사용 허락 기준', 'VO2-ARTIST · 2026-10-09', [
        ('1. 출연진', ['표기할 출연진은 라이네, 새벽결, 오로라정원이다.']),
        ('2. 사진', ['출연자의 사진은 서면 동의서가 있는 경우에만 사용한다.', '동의서에는 사용 매체·기간을 적는다.']),
    ], '※ 출연자는 모두 가상 인물이며 사진은 도식으로 대신한다.')},
    {'id': 'sponsor', 'stage': 2, 'title': '후원사 로고 사용 기준', 'pages': simple_doc('후원사 로고 사용 기준', 'VO2-SPONSOR · 2026-10-09', [
        ('1. 승인 목록', ['노출할 후원사는 달빛은행, 누리푸드, 하늘모터스이며 승인되지 않은 후원사 로고는 쓰지 않는다.']),
        ('2. 사용 규칙', ['후원사 로고는 하단 띠에 같은 크기로 배치한다.']),
    ])},
    {'id': 'banner', 'stage': 2, 'title': '현수막 게시 계획서', 'pages': simple_doc('현수막 게시 계획서', 'VO2-BANNER · 2026-10-09', [
        ('1. 계획', ['현수막에는 게시 신고번호를 표기한다.', '수량·위치·기간·크기는 관할 기관 기준을 확인해 신고한다. (확인 필요)']),
    ], '※ 이번 납품은 인스타그램 포스터이며 현수막은 별도 발주이다.')},
    {'id': 'contract', 'stage': 2, 'title': '외주 용역 계약서', 'pages': contract(C2, '특약 (출연자·후원사·라이선스)', [
        '제6조 (승인서) 을은 후원사 로고 사용 승인서를 납품과 함께 제출한다.',
        '제7조 (라이선스) 을은 사용한 폰트와 이미지의 라이선스 확인서를 납품과 함께 제출한다.'])},
    {'id': 'sow', 'stage': 3, 'title': '과업지시서 (SOW)', 'pages': simple_doc('과업지시서 (SOW)', 'VO2-SOW · 계약서 별첨 1', [
        ('1. 산출물', ['인스타그램 피드용 홍보 포스터 1종.', '규격은 1080×1350 PNG로 한다.']),
        ('2. 증빙', ['출연자 사진 동의서(출연자별), 후원사 로고 사용 승인서, 폰트·이미지 라이선스 확인서를 제출한다.']),
        ('3. 일정', ['착수 2026-10-12 · 시안 2026-10-26 · 납품 2026-11-04']),
    ])},
    {'id': 'guide', 'stage': 3, 'title': '행사 로고 가이드', 'pages': simple_doc('달빛누리 행사 로고 가이드', 'VO2-GUIDE', [('로고', ['행사 로고는 가로세로 비율과 색을 변경하지 않는다.'])])},
]
V2_EVID = {
    'consent:라이네': ('출연자 사진 사용 동의서 (라이네)', lambda m: evidence_pdf('출연자 사진 사용 동의서', ['출연자: 라이네(가상)', '사용 매체: 인스타그램 · 기간 2026-11-09 ~ 2026-11-22', '서명: 가상 서명'])),
    'consent:새벽결': ('출연자 사진 사용 동의서 (새벽결)', lambda m: evidence_pdf('출연자 사진 사용 동의서', ['출연자: 새벽결(가상)', '사용 매체: 인스타그램 · 기간 2026-11-09 ~ 2026-11-22', '서명: 가상 서명'])),
    'consent:오로라정원': ('출연자 사진 사용 동의서 (오로라정원)', lambda m: evidence_pdf('출연자 사진 사용 동의서', ['출연자: 오로라정원(가상)', '사용 매체: 인스타그램 · 기간 2026-11-09 ~ 2026-11-22', '서명: 가상 서명'])),
    'sponsor_approval': ('후원사 로고 사용 승인서', lambda m: evidence_pdf('후원사 로고 사용 승인서', ['승인 후원사: 달빛은행, 누리푸드, 하늘모터스(가상)', '승인일: 2026-10-20 · 승인자: 각사 담당자(가상)'])),
    'font_license': ('폰트·이미지 라이선스 확인서', lambda m: evidence_pdf('폰트·이미지 라이선스 확인서', ['폰트: Noto Sans KR / Noto Serif KR (SIL OFL 1.1 — 원문 확인 필요)', '이미지: 제작사 자체 제작 도식(가상)', '확인: 밤하늘 디자인랩(가상) · 2026-11-03'])),
}
ALL_CONSENT = [(f'consent:{n}', 'v1', {}) for n in ('라이네', '새벽결', '오로라정원')]
BASE_EV2 = ALL_CONSENT + [('sponsor_approval', 'v1', {}), ('font_license', 'v1', {})]
VO2 = {
    'id': 'VO-02', 'slug': 'VO-02-festival', 'group': 'VG02', 'name': '달빛누리 뮤직 페스타 2026', 'category': '페스티벌(행사 홍보)', 'ctx': C2,
    'seed_note': '출연진 명칭·출연자 사진 동의·후원사 로고 승인 목록 대조·주최 표기를 시험. 현수막 신고번호는 현수막 한정 조건(해당 없음).',
    'usage': {'media': ['instagram_feed'], 'period': '2026-11-09 ~ 2026-11-22', 'region': '대한민국', 'paid': False, 'post_start': '2026-11-09', 'post_end': '2026-11-22'},
    'canvas': (1080, 1350, 'png'), 'brand_logo': {'glyph': 'D', 'color': '#f0b429'}, 'docs': V2_DOCS, 'criteria': V2_CRIT, 'versions': ['v1'],
    'render': ad_v02, 'evidence_docs': V2_EVID,
    'deliveries': [
        {'id': 'D1', 'kind': '정상', 'params': {}, 'evidence': BASE_EV2, 'edits': ['모든 조건·증빙 충족(현수막 신고번호는 해당 없음)']},
        {'id': 'D2', 'kind': '단일 오류', 'params': {'ticket': '1일권 59,000원 · 2일권 98,000원', 'bg': ('#1b1a38', '#392858')}, 'evidence': BASE_EV2, 'edits': ['1일권 가격 55,000원 → 59,000원']},
        {'id': 'D3', 'kind': '복합 오류', 'params': {'names': ['라이네', '새벽겹', '오로라정원'], 'sponsors': [('달빛은행', '#1f4e8c', 0), ('누리푸드', '#a63d2f', 0), ('가상통신', '#6b2f6b', 0)], 'bg': ('#1c1a39', '#3a2a5a')},
         'evidence': BASE_EV2, 'edits': ['출연자 이름 오타(새벽결 → 새벽겹)', '승인되지 않은 후원사 로고(가상통신) 노출', '승인된 후원사(하늘모터스) 누락']},
        {'id': 'D4', 'kind': '판독 불확실', 'params': {'tiny': True, 'sponsors': [('달빛은행', '#1f4e8c', 0.6), ('누리푸드', '#a63d2f', 0), ('하늘모터스', '#2f6b4f', 0)]}, 'evidence': BASE_EV2,
         'edits': ['주최·주관 14px·저대비', '후원사 로고 1개를 약 60% 가림(노출 여부 불확실)']},
        {'id': 'D5', 'kind': '증빙 누락', 'params': {'bg': ('#1d1b3b', '#3c2b5e')}, 'evidence': [('consent:라이네', 'v1', {}), ('consent:새벽결', 'v1', {}), ('sponsor_approval', 'v1', {}), ('font_license', 'v1', {})],
         'edits': ['오로라정원 사진은 노출됐으나 출연자 동의서가 제출 목록에 없음']},
        {'id': 'D6', 'kind': '수정본', 'params': {'bg': ('#1d1a3a', '#3b295b')}, 'evidence': BASE_EV2, 'revision_of': 'D3', 'edits': ['D3의 지적 3건(이름 오타·미승인 후원사·후원사 누락) 수정']},
    ],
    'expect': {'D1': {'G10': '해당 없음'}, 'D2': {'G04': '미충족', 'G10': '해당 없음'}, 'D3': {'G05': '미충족', 'G07': '미충족', 'G10': '해당 없음'},
               'D4': {'G07': '확인 필요', 'G08': '확인 필요', 'G10': '해당 없음'}, 'D5': {'G06': '미충족', 'G10': '해당 없음'}, 'D6': {'G10': '해당 없음'}},
    'scenario': {'type': 'fix', 'prev': 'D3', 'new': 'D6', 'text': 'D3 지적(이름 오타·미승인 후원사·후원사 누락) → D6 수정본. 재검수에서 모두 해결, 새 오류 없음.'},
}


# ================================================================= VO-03 가상인물
def ad_v03(p):
    P = dict(hair='검은 단발', wrist='left', price='199,000원', ai_label='AI로 생성된 가상 인물입니다', tiny=False, watch_occ=0.0, bg=('#e9eef7', '#cfd9ee'), logo='ref')
    P.update(p)
    a = Ad(1080, 1350, P['bg'])
    a.logo({'glyph': 'M', 'color': '#3f5f8a'}, (60, 60), 100, P['logo'])
    a.text('r_brand', '브랜드 표기', 'MOA WATCH', (190, 90), 38, '#1f2f45', 700, 'sans', 'la', spacing=6)
    a.text('r_name', '상품명', '모아 워치 S1', (540, 270), 84, '#1f2f45', 800)
    a.virtual_person(540, 560, P['hair'])
    a.watch(540, 560, P['wrist'], P['watch_occ'])
    a.text('r_price', '가격', P['price'], (900, 1010), 56, '#1f2f45', 800)
    a.text('r_period', '기간', '11.16 ~ 12.15', (900, 1085), 36, '#3f5f8a', 600)
    if P['ai_label']:
        if P['tiny']:
            a.text('r_ai', 'AI 표시', P['ai_label'], (540, 1180), 14, '#c7d2e8', 400, legibility='ambiguous', note='14px·저대비')
        else:
            a.rect((200, 1145, 880, 1215), '#ffffff', 30, '#3f5f8a', 3)
            a.text('r_ai', 'AI 표시', P['ai_label'], (540, 1180), 30, '#1f2f45', 600)
    return a


C3 = dict(no='VO3', client='모아 전자(가상)', vendor='픽셀 서사 스튜디오(가상)', project='MOA WATCH S1 가상 인간 ‘서하’ 광고 이미지',
          purpose='스마트워치 ‘모아 워치 S1’의 가상 인간 모델 ‘서하’ 광고 이미지 1종을 제작한다.',
          deliverable='인스타그램 피드용 광고 이미지 1종 (1080×1350 PNG)', media_label='인스타그램 피드(국내 유료 광고)', region='대한민국',
          post_start='2026-11-16', post_end='2027-02-15', budget='6,000,000원', proposal_due='2026-10-10', kickoff='2026-10-16',
          delivery_date='2026-11-09', rfp_date='2026-10-08', nda_date='2026-10-12', contract_date='2026-10-16',
          provided_date='2026-10-13', provided_items='제품 이미지·브랜드 가이드·가상 인물 설정서')
V3_CRIT = [
    {'id': 'H01', 'label': '브랜드 표기', 'kind': 'text_equals', 'role': '브랜드 표기', 'expected': 'MOA WATCH', 'case_sensitive': True, 'src': [S('guide', 1, '브랜드명은 항상 대문자 MOA WATCH로 쓴다.')]},
    {'id': 'H02', 'label': '상품명', 'kind': 'text_equals', 'role': '상품명', 'expected': '모아 워치 S1', 'src': [S('sow', 1, '상품명은 “모아 워치 S1”로 표기한다.')]},
    {'id': 'H03', 'label': '가격', 'kind': 'text_equals', 'role': '가격', 'expected': '199,000원', 'src': [S('sow', 1, '가격은 “199,000원”으로 표기한다.')]},
    {'id': 'H04', 'label': '출시 기념 기간', 'kind': 'text_equals', 'role': '기간', 'expected': '11.16 ~ 12.15', 'src': [S('sow', 1, '출시 기념 기간은 “11.16 ~ 12.15”로 표기한다.')]},
    {'id': 'H05', 'label': 'AI 생성물 표시 문구', 'kind': 'text_equals', 'role': 'AI 표시', 'expected': 'AI로 생성된 가상 인물입니다', 'src': [S('label', 1, '광고 이미지 하단에 “AI로 생성된 가상 인물입니다”를 표시한다.')]},
    {'id': 'H06', 'label': 'AI 표시 글자 크기', 'kind': 'text_min_size', 'role': 'AI 표시', 'min_px': 24, 'src': [S('label', 1, '표시 문구는 24px 이상으로 한다.')]},
    {'id': 'H07', 'label': '가상 인물 헤어(설정서 일치)', 'kind': 'attr_equals', 'cls': '가상 인물', 'attr': 'hair', 'expected': '검은 단발', 'src': [S('persona', 1, '서하의 헤어는 검은 단발이다.')]},
    {'id': 'H08', 'label': '시계 착용 손목(설정서 일치)', 'kind': 'attr_equals', 'cls': '시계', 'attr': 'wrist', 'expected': 'left', 'src': [S('persona', 1, '제품 시계는 왼쪽 손목에 착용한다.')]},
    {'id': 'H09', 'label': '모델 초상·음성 허락 범위', 'kind': 'license_covers', 'evidence_id': 'likeness_consent', 'src': [S('consent', 1, '허락 기간·매체·지역은 광고 게시 계획을 포함해야 한다.')]},
    {'id': 'H10', 'label': 'IP 귀속 확인서 제출', 'kind': 'evidence_required', 'evidence_id': 'ip_assignment', 'src': [S('contract', 2, '가상 인물과 결과물의 IP 귀속 확인서를 납품과 함께 제출한다.')]},
    {'id': 'H11', 'label': 'AI 도구·모델 버전 목록 제출', 'kind': 'evidence_required', 'evidence_id': 'ai_tool_list', 'src': [S('contract', 2, '사용한 AI 도구와 모델 버전 목록을 납품과 함께 제출한다.')]},
    {'id': 'H12', 'label': '로고 변형 금지', 'kind': 'logo_variant', 'src': [S('guide', 1, '로고는 가로세로 비율과 색을 변경하지 않는다.')]},
    {'id': 'H13', 'label': '규격', 'kind': 'file_spec', 'size': (1080, 1350), 'mimes': PNG, 'src': [S('sow', 1, '규격은 1080×1350 PNG로 한다.')]},
]
V3_DOCS = [
    {'id': 'rfp', 'stage': 1, 'title': '제안요청서 (RFP)', 'pages': rfp(C3, '5. AI 활용 범위', [
        '허용: 가상 인간(AI 휴먼) 이미지 생성, AI 배경 생성.', '사전 서면 협의 필요: 실존 모델의 얼굴·음성 데이터를 기반으로 한 가상 인간 (모델 허락서 제출).',
        '금지: 실존 인물(유명인 포함) 닮은꼴 생성, 발주사 자료의 모델 학습 사용.', 'AI 생성물 표시 의무를 지킨다. (관련 법령 확인 필요)'])},
    {'id': 'nda', 'stage': 1, 'title': '비밀유지계약서 (NDA)', 'pages': nda(C3)},
    {'id': 'persona', 'stage': 1, 'title': '가상 인물 설정서', 'pages': simple_doc('가상 인물 설정서 (캐릭터 시트)', 'VO3-PERSONA · 모아 전자 · 2026-10-13', [
        ('1. 서하(SEOHA)', ['서하의 헤어는 검은 단발이다.', '제품 시계는 왼쪽 손목에 착용한다.', '서하의 얼굴은 실존 모델 한가상(가명)의 얼굴 데이터를 기반으로 한다.']),
    ], '※ 실존 모델 데이터를 사용하므로 초상·음성 허락서가 필요하다.')},
    {'id': 'consent', 'stage': 2, 'title': '모델 초상·음성 사용 허락 기준', 'pages': simple_doc('모델 초상·음성 사용 허락 기준', 'VO3-CONSENT · 2026-10-13', [
        ('1. 허락서', ['허락 기간·매체·지역은 광고 게시 계획을 포함해야 한다.', '게시 계획: 인스타그램 피드 · 2026-11-16 ~ 2027-02-15 · 대한민국.']),
        ('2. 확인', ['허락서의 범위가 부족하면 납품 검수에서 보완을 요청한다.']),
    ])},
    {'id': 'label', 'stage': 2, 'title': 'AI 생성물 표시 기준 (가상 발주사 내부 기준)', 'pages': simple_doc('AI 생성물 표시 기준', 'VO3-LABEL · 모아 전자 내부 기준 · 2026-10-13', [
        ('1. 표시', ['광고 이미지 하단에 “AI로 생성된 가상 인물입니다”를 표시한다.', '표시 문구는 24px 이상으로 한다.']),
    ], '※ 표시 방법·크기의 법정 기준은 「인공지능 발전과 신뢰 기반 조성 등에 관한 기본법」 및 관련 가이드라인을 원문으로 확인해야 한다. 위 수치는 내부 기준이다.')},
    {'id': 'contract', 'stage': 2, 'title': '외주 용역 계약서', 'pages': contract(C3, '특약 (IP·AI 도구·허락서)', [
        '제6조 (IP) 을은 가상 인물과 결과물의 IP 귀속 확인서를 납품과 함께 제출한다.',
        '제7조 (AI 도구) 을은 사용한 AI 도구와 모델 버전 목록을 납품과 함께 제출한다.',
        '제7조의2 (허락서) 을은 실존 모델 데이터를 사용하는 경우 초상·음성 사용 허락서를 납품과 함께 제출한다.'])},
    {'id': 'sow', 'stage': 3, 'title': '과업지시서 (SOW)', 'pages': simple_doc('과업지시서 (SOW)', 'VO3-SOW · 계약서 별첨 1', [
        ('1. 산출물', ['인스타그램 피드용 광고 이미지 1종.', '규격은 1080×1350 PNG로 한다.', '상품명은 “모아 워치 S1”로 표기한다.', '가격은 “199,000원”으로 표기한다.', '출시 기념 기간은 “11.16 ~ 12.15”로 표기한다.']),
        ('2. 증빙', ['모델 초상·음성 허락서, IP 귀속 확인서, AI 도구·모델 버전 목록을 납품과 함께 제출한다.']),
        ('3. 일정', ['착수 2026-10-16 · 시안 2026-10-30 · 납품 2026-11-09']),
    ])},
    {'id': 'guide', 'stage': 3, 'title': '브랜드 가이드', 'pages': simple_doc('모아 워치 브랜드 가이드', 'VO3-GUIDE', [('표기', ['브랜드명은 항상 대문자 MOA WATCH로 쓴다.', '로고는 가로세로 비율과 색을 변경하지 않는다.'])])},
]
V3_EVID = {
    'likeness_consent': ('모델 초상·음성 사용 허락서', lambda m: evidence_pdf('모델 초상·음성 사용 허락서', [
        '모델: 한가상(가명) · 대상: 가상 인간 ‘서하’의 얼굴·음성 데이터', f"허락 기간: {m['start']} ~ {m['end']}", f"허락 매체: {', '.join(m['media'])} · 허락 지역: {m['region']}", '서명: 가상 서명'])),
    'ip_assignment': ('IP 귀속 확인서', lambda m: evidence_pdf('IP 귀속 확인서', ['대상: 가상 인물 서하와 광고 이미지', '귀속: 잔금 지급 완료 시 모아 전자(가상)에 귀속', '확인: 픽셀 서사 스튜디오(가상) · 2026-11-06'])),
    'ai_tool_list': ('AI 도구·모델 버전 목록', lambda m: evidence_pdf('AI 도구·모델 버전 목록', ['이미지 생성: 가상 도구 v1.0 (시연용 가상 값)', '배경 생성: 가상 도구 v1.0', '확인: 픽셀 서사 스튜디오(가상) · 2026-11-06'])),
}
CONSENT_OK = {'start': '2026-11-01', 'end': '2027-02-28', 'media': ['instagram_feed'], 'region': '대한민국'}
CONSENT_SHORT = {'start': '2026-11-01', 'end': '2027-01-31', 'media': ['instagram_feed'], 'region': '대한민국'}
EV3_OK = [('likeness_consent', 'v2', CONSENT_OK), ('ip_assignment', 'v1', {}), ('ai_tool_list', 'v1', {})]
VO3 = {
    'id': 'VO-03', 'slug': 'VO-03-virtual-person', 'group': 'VG03', 'name': '모아 워치 S1 · 가상 인간 서하', 'category': '가상인물(AI 휴먼 광고)', 'ctx': C3,
    'seed_note': '가상 인물 설정서 일치(헤어·착용 위치), AI 생성물 표시, 모델 초상·음성 허락 범위(기간·매체·지역) 대조, IP·AI 도구 증빙을 시험. 증빙만 보완하는 수정 시나리오.',
    'usage': {'media': ['instagram_feed'], 'period': '2026-11-16 ~ 2027-02-15', 'region': '대한민국', 'paid': True, 'post_start': '2026-11-16', 'post_end': '2027-02-15'},
    'canvas': (1080, 1350, 'png'), 'brand_logo': {'glyph': 'M', 'color': '#3f5f8a'}, 'docs': V3_DOCS, 'criteria': V3_CRIT, 'versions': ['v1'],
    'render': ad_v03, 'evidence_docs': V3_EVID,
    'deliveries': [
        {'id': 'D1', 'kind': '정상', 'params': {}, 'evidence': EV3_OK, 'edits': ['모든 조건·증빙 충족']},
        {'id': 'D2', 'kind': '단일 오류', 'params': {'ai_label': None, 'bg': ('#e8edf6', '#cdd8ed')}, 'evidence': EV3_OK, 'edits': ['AI 생성물 표시 문구 삭제(표시·글자 크기 두 조건에 영향)']},
        {'id': 'D3', 'kind': '복합 오류', 'params': {'hair': '갈색 장발', 'wrist': 'right', 'price': '219,000원', 'bg': ('#e7ecf5', '#ccd6ec')}, 'evidence': EV3_OK,
         'edits': ['가상 인물 헤어가 설정서와 다름(갈색 장발)', '시계를 오른쪽 손목에 착용', '가격 199,000원 → 219,000원']},
        {'id': 'D4', 'kind': '판독 불확실', 'params': {'tiny': True, 'watch_occ': 0.6}, 'evidence': EV3_OK, 'edits': ['AI 표시 문구 14px·저대비', '시계를 소매로 약 60% 가림(착용 손목 판단 불확실)']},
        {'id': 'D5', 'kind': '증빙 문제', 'params': {'bg': ('#eaeff8', '#d0daef')}, 'evidence': [('likeness_consent', 'v1', CONSENT_SHORT), ('ip_assignment', 'v1', {})],
         'edits': ['모델 허락서의 종료일(2027-01-31)이 게시 종료일(2027-02-15)보다 앞서 끝남', 'AI 도구·모델 버전 목록 미제출']},
        {'id': 'D6', 'kind': '수정본(증빙만)', 'copy_of': 'D5', 'evidence': [('likeness_consent', 'v2', CONSENT_OK), ('ip_assignment', 'v1', {}), ('ai_tool_list', 'v1', {})], 'revision_of': 'D5',
         'edits': ['D5와 같은 이미지 파일에 허락서 연장본(v2: 종료일 2027-02-28)과 AI 도구 목록만 추가']},
    ],
    'expect': {'D1': {}, 'D2': {'H05': '미충족', 'H06': '미충족'}, 'D3': {'H03': '미충족', 'H07': '미충족', 'H08': '미충족'},
               'D4': {'H05': '확인 필요', 'H06': '미충족', 'H08': '확인 필요'}, 'D5': {'H09': '미충족', 'H11': '미충족'}, 'D6': {}},
    'scenario': {'type': 'evidence_only', 'prev': 'D5', 'new': 'D6', 'text': 'D5 검수에서 허락 기간 부족(H09)과 AI 도구 목록 미제출(H11). D6은 이미지가 같고 증빙만 갱신 → 두 조건이 해결되고 새 오류는 없다.'},
}

CASES = [VO1, VO2, VO3]
