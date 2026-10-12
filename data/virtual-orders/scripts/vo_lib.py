# -*- coding: utf-8 -*-
"""가상 발주 데이터 생성 라이브러리: 이미지 렌더링 + 관찰값 추출 + 판정 엔진 + 문서(PDF/MD).

의존성: Pillow(프로젝트 requirements에 있음), PyMuPDF·fonttools(requirements에 없음, 이 폴더의 PDF 생성에만 필요).
글꼴: Windows에 설치된 Noto Sans/Serif KR(가변)과 맑은 고딕 경로를 사용한다.
"""
import hashlib, json, math, pathlib, re
from PIL import Image, ImageDraw, ImageFont
import pymupdf

NOTO_SANS = 'C:/Windows/Fonts/NotoSansKR-VF.ttf'
NOTO_SERIF = 'C:/Windows/Fonts/NotoSerifKR-VF.ttf'
MALGUN = 'C:/Windows/Fonts/malgun.ttf'
MALGUN_BD = 'C:/Windows/Fonts/malgunbd.ttf'


def sha256_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def get_font(kind, size, weight):
    f = ImageFont.truetype(NOTO_SANS if kind == 'sans' else NOTO_SERIF, size)
    ax = f.get_variation_axes()[0]
    f.set_variation_by_axes([max(ax['minimum'], min(ax['maximum'], weight))])
    return f


def hull(pts):
    pts = sorted(set((round(x, 1), round(y, 1)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo = []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def bbox_of(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]


class Ad:
    """한 장의 광고 이미지를 그리면서 관찰 정답(문자 영역·객체·로고)을 함께 기록한다."""

    def __init__(self, w, h, bg=('#ffffff', '#ffffff')):
        self.w, self.h = w, h
        self.img = Image.new('RGB', (w, h), bg[0])
        top, bot = bg
        if top != bot:
            d = ImageDraw.Draw(self.img)
            t = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
            b = tuple(int(bot[i:i + 2], 16) for i in (1, 3, 5))
            for y in range(h):
                k = y / (h - 1)
                d.line([(0, y), (w, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * k) for i in range(3)))
        self.d = ImageDraw.Draw(self.img)
        self.regions, self.objects, self.logo_obs = [], {}, None
        self.fonts_used = set()

    # ---- 장식(정답 없음) ----
    def rect(self, box, fill, r=0, outline=None, width=1):
        self.d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

    def ellipse(self, box, fill, outline=None, width=1):
        self.d.ellipse(box, fill=fill, outline=outline, width=width)

    def poly(self, pts, fill, outline=None):
        self.d.polygon(pts, fill=fill, outline=outline)

    def line(self, pts, fill, width=2):
        self.d.line(pts, fill=fill, width=width)

    # ---- 문자 ----
    def text(self, rid, role, text, pos, size, color, weight=600, font='sans', anchor='mm', legibility='clear',
             spacing=0, stroke=None, arc=None, note=None):
        self.fonts_used.add('Noto Sans KR' if font == 'sans' else 'Noto Serif KR')
        f = get_font(font, size, weight)
        x, y = pos
        if arc:
            poly = self._arc_text(text, f, arc, color)
        elif spacing:
            widths = [f.getlength(c) for c in text]
            tw = sum(widths) + spacing * (len(text) - 1)
            cx = x - tw / 2 if anchor[0] == 'm' else x
            pts = []
            for c, wd in zip(text, widths):
                self.d.text((cx, y), c, font=f, fill=color, anchor='la')
                bb = self.d.textbbox((cx, y), c, font=f, anchor='la')
                pts += [(bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3])]
                cx += wd + spacing
            poly = hull(pts)
        else:
            sw, sc = (stroke or (0, None))
            self.d.text((x, y), text, font=f, fill=color, anchor=anchor, stroke_width=sw, stroke_fill=sc)
            bb = self.d.textbbox((x, y), text, font=f, anchor=anchor, stroke_width=sw)
            poly = [(bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3])]
        reg = {'region_id': rid, 'role': role, 'transcription_raw': text, 'polygon': [[round(a, 1), round(b, 1)] for a, b in poly],
               'bbox': bbox_of(poly), 'legibility': legibility, 'font_size': size, 'color': color,
               'reading_order': len(self.regions) + 1}
        if note:
            reg['note'] = note
        self.regions.append(reg)
        return reg

    def _arc_text(self, text, f, arc, color):
        cx, cy, r, start = arc['center'][0], arc['center'][1], arc['radius'], math.radians(arc['start'])
        widths = [f.getlength(c) for c in text]
        pts, cum = [], 0
        for c, wd in zip(text, widths):
            ang = start + (cum + wd / 2) / r
            px, py = cx + r * math.sin(ang), cy - r * math.cos(ang)
            hgt = f.size * 1.25
            tile = Image.new('RGBA', (int(wd + 40), int(hgt + 40)), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((tile.width / 2, tile.height / 2), c, font=f, fill=color, anchor='mm')
            rot = tile.rotate(-math.degrees(ang), resample=Image.BICUBIC, expand=True)
            self.img.paste(rot, (int(px - rot.width / 2), int(py - rot.height / 2)), rot)
            hw, hh = wd / 2, f.size * 0.55
            for sx, sy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                pts.append((px + sx * math.cos(ang) - sy * math.sin(ang), py + sx * math.sin(ang) + sy * math.cos(ang)))
            cum += wd
        self.d = ImageDraw.Draw(self.img)
        return hull(pts)


    def glare(self, box, alpha):
        ov = Image.new('RGBA', (self.w, self.h), (0, 0, 0, 0))
        ImageDraw.Draw(ov).ellipse(box, fill=(255, 255, 255, alpha))
        self.img = Image.alpha_composite(self.img.convert('RGBA'), ov).convert('RGB')
        self.d = ImageDraw.Draw(self.img)

    # ---- 객체 ----
    def obj(self, cls, iid, bbox, occlusion=0.0, occluded_by=None, attrs=None):
        self.objects.setdefault(cls, []).append({'instance_id': iid, 'bbox': [round(v) for v in bbox], 'occlusion': occlusion,
                                                  'occluded_by': occluded_by, 'attrs': attrs or {}})

    def cookie(self, iid, cx, cy, r, occ=0.0, occ_by=None):
        self.ellipse((cx - r, cy - r, cx + r, cy + r), '#8a5a2b', '#5c3a1a', 3)
        for dx, dy in ((-10, -8), (8, -12), (2, 10), (-12, 8), (14, 6)):
            self.ellipse((cx + dx - 3, cy + dy - 3, cx + dx + 3, cy + dy + 3), '#4a2d12')
        self.obj('쿠키 토핑', iid, (cx - r, cy - r, cx + r, cy + r), occ, occ_by)

    def croissant(self, iid, cx, cy, w=260, h=150):
        self.ellipse((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), '#d9953a', '#8a5a1a', 4)
        for k in (-2, -1, 0, 1, 2):
            x = cx + k * (w / 6)
            self.line([(x, cy - h / 2 + 12 + abs(k) * 6), (x + 10, cy + h / 2 - 12 - abs(k) * 6)], '#a8681e', 5)
        self.obj('크루아상', iid, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))

    def stamp(self, iid, cx, cy, r=46):
        self.ellipse((cx - r, cy - r, cx + r, cy + r), '#f4c542', '#a87c00', 4)
        pts = []
        for i in range(10):
            a = math.pi / 5 * i - math.pi / 2
            rr = r * 0.62 if i % 2 == 0 else r * 0.28
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        self.poly(pts, '#a87c00')
        self.obj('스탬프 아이콘', iid, (cx - r, cy - r, cx + r, cy + r))

    def tumbler(self, iid, cx, top, w, h, color):
        self.rect((cx - w / 2, top, cx + w / 2, top + h), color, 28, '#0b1020', 3)
        self.rect((cx - w / 2 - 6, top - 26, cx + w / 2 + 6, top + 6), '#e8ecf5', 10, '#0b1020', 3)
        self.rect((cx - w * 0.18, top + h * 0.35, cx + w * 0.18, top + h * 0.5), '#ffffff', 8)
        self.obj('텀블러', iid, (cx - w / 2 - 6, top - 26, cx + w / 2 + 6, top + h), attrs={'color': color})

    def bottle(self, cx, top, form='ref', label='#f2d21b'):
        """제품 병(C04). form: ref(좁은 병) / wide(넓은 병)"""
        if form == 'ref':
            w, h, nw = 220, 470, 90
        else:
            w, h, nw = 300, 400, 120
        top = top + (470 - h)
        self.rect((cx - w / 2, top + 70, cx + w / 2, top + h), '#bfe6a0', 40, '#3b6b1f', 4)
        self.rect((cx - nw / 2, top, cx + nw / 2, top + 90), '#bfe6a0', 20, '#3b6b1f', 4)
        self.rect((cx - nw / 2 - 8, top - 30, cx + nw / 2 + 8, top + 8), '#2f8f46', 10, '#1f5f2e', 3)
        self.rect((cx - w * 0.38, top + h * 0.42, cx + w * 0.38, top + h * 0.82), label, 16, '#8a7a00', 3)
        self.obj('제품 병', 'bottle_1', (cx - w / 2, top - 30, cx + w / 2, top + h), attrs={'form': form, 'label_color': label})


    # ---- 3건 확장용 객체 ----
    def bowl(self, cx, cy, w=520, h=190):
        self.ellipse((cx - w / 2, cy - h / 2 + 60, cx + w / 2, cy + h / 2 + 60), '#f3e9d8', '#6b4a2b', 5)
        self.ellipse((cx - w / 2 + 30, cy - h / 2 + 30, cx + w / 2 - 30, cy + h / 2 + 30), '#f6ecc8', '#c9b27a', 3)

    def mushroom(self, iid, cx, cy, r=34, occ=0.0, occ_by=None):
        self.ellipse((cx - r, cy - r * 0.8, cx + r, cy + r * 0.3), '#a67c52', '#5c3d1e', 3)
        self.rect((cx - r * 0.3, cy, cx + r * 0.3, cy + r * 0.8), '#e8d9bd', 6, '#5c3d1e', 2)
        self.obj('버섯 토핑', iid, (cx - r, cy - r * 0.8, cx + r, cy + r * 0.8), occ, occ_by)

    def sponsor(self, iid, name, box, color, occ=0.0):
        self.rect(box, color, 14, '#222222', 2)
        f = get_font('sans', 26, 800)
        self.d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), name, font=f, fill='#ffffff', anchor='mm')
        self.obj('후원사 로고', iid, box, occ, 'overlay' if occ else None, {'name': name})
        if occ:
            cx = box[0] + (box[2] - box[0]) * 0.55
            self.ellipse((cx, box[1] - 6, box[2] + 14, box[3] + 6), '#d9d9e8')

    def avatar(self, iid, name, cx, cy, r, color):
        self.ellipse((cx - r, cy - r, cx + r, cy + r), color, '#222222', 3)
        self.ellipse((cx - r * 0.32, cy - r * 0.5, cx + r * 0.32, cy + r * 0.14), '#f1d6c1', '#222222', 2)
        self.ellipse((cx - r * 0.62, cy + r * 0.2, cx + r * 0.62, cy + r * 1.05), '#f1d6c1', '#222222', 2)
        self.obj('출연자 사진', iid, (cx - r, cy - r, cx + r, cy + r), attrs={'name': name})

    def virtual_person(self, cx, cy, hair='검은 단발', occ=0.0):
        col = {'검은 단발': '#1a1a1a', '갈색 장발': '#8a5a2b'}[hair]
        self.ellipse((cx - 150, cy + 120, cx + 150, cy + 520), '#3f5f8a', '#1f2f45', 4)
        self.ellipse((cx - 85, cy - 70, cx + 85, cy + 120), '#f1d6c1', '#7a5a45', 3)
        if hair == '검은 단발':
            self.ellipse((cx - 100, cy - 110, cx + 100, cy + 40), col)
            self.rect((cx - 108, cy - 20, cx - 70, cy + 110), col, 14)
            self.rect((cx + 70, cy - 20, cx + 108, cy + 110), col, 14)
        else:
            self.ellipse((cx - 104, cy - 112, cx + 104, cy + 30), col)
            self.rect((cx - 118, cy - 20, cx - 72, cy + 330), col, 20)
            self.rect((cx + 72, cy - 20, cx + 118, cy + 330), col, 20)
        self.ellipse((cx - 38, cy + 10, cx - 20, cy + 26), '#222222')
        self.ellipse((cx + 20, cy + 10, cx + 38, cy + 26), '#222222')
        self.obj('가상 인물', 'persona_1', (cx - 150, cy - 112, cx + 150, cy + 520), occ, 'cover' if occ else None, {'hair': hair})

    def watch(self, cx, cy, wrist='left', occ=0.0):
        x = cx - 190 if wrist == 'left' else cx + 190
        self.rect((x - 20, cy + 250, x + 20, cy + 330), '#2b2b2b', 8)
        self.ellipse((x - 34, cy + 270, x + 34, cy + 338), '#e8ecf5', '#222222', 4)
        self.obj('시계', 'watch_1', (x - 34, cy + 250, x + 34, cy + 338), occ, 'sleeve' if occ else None, {'wrist': wrist})
        if occ:
            self.rect((x - 46, cy + 280, x + 46, cy + 330), '#3f5f8a', 12)

    # ---- 로고 ----
    def logo(self, spec, pos, size, variant='ref'):
        x, y = pos
        if variant == 'missing':
            self.logo_obs = {'variant': 'missing', 'bbox': None}
            return
        w, h = size, size
        col = spec['color']
        if variant == 'stretched':
            w = int(size * 1.6)
        if variant == 'recolor':
            col = '#7a3fb0'
        if variant == 'small':
            w = h = int(size * 0.45)
        self.ellipse((x, y, x + w, y + h), col)
        self.ellipse((x + w * 0.08, y + h * 0.08, x + w * 0.92, y + h * 0.92), None, '#ffffff', max(2, int(h * 0.04)))
        f = get_font('serif', int(h * 0.6), 800)
        self.d.text((x + w / 2, y + h / 2), spec['glyph'], font=f, fill='#ffffff', anchor='mm')
        self.logo_obs = {'variant': variant, 'bbox': [x, y, x + w, y + h], 'width': w, 'height': h, 'aspect': round(w / h, 2)}

    # ---- 저장 ----
    def save(self, path, fmt='png'):
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == 'png':
            self.img.save(path, 'PNG')
        else:
            self.img.save(path, 'JPEG', quality=92)
        return {'w': self.w, 'h': self.h, 'mime': 'image/png' if fmt == 'png' else 'image/jpeg',
                'size': path.stat().st_size, 'sha256': sha256_file(path)}

    def observations(self, filemeta, evidence):
        clear = None
        if self.logo_obs and self.logo_obs.get('bbox'):
            lb = self.logo_obs['bbox']
            others = [r['bbox'] for r in self.regions]
            for objs in self.objects.values():
                others += [o['bbox'] for o in objs]
            gaps = []
            for ob in others:
                dx = max(ob[0] - lb[2], lb[0] - ob[2], 0)
                dy = max(ob[1] - lb[3], lb[1] - ob[3], 0)
                gaps.append(math.hypot(dx, dy))
            clear = round(min(gaps), 1) if gaps else None
            self.logo_obs['clear_space_px'] = clear
        return {'file': filemeta, 'regions': self.regions, 'objects': self.objects, 'logo': self.logo_obs,
                'evidence': evidence, 'fonts': sorted(self.fonts_used)}


# ---------------------------------------------------------------- 판정 엔진
def norm(s, case_sensitive=True):
    s = re.sub(r'\s+', '', s)
    return s if case_sensitive else s.lower()


def num(s):
    m = re.findall(r'\d[\d,]*', s)
    if not m:
        return None
    return int(m[0].replace(',', ''))


ILLEGIBLE = ('partial', 'ambiguous', 'unreadable')


def regions_of(obs, role):
    return [r for r in obs['regions'] if r['role'] == role]


def expected_for(crit, ver):
    e = crit.get('expected')
    return e[ver] if isinstance(e, dict) and ver in e else (e if not isinstance(e, dict) else e.get('v1'))


def judge(crit, obs, ver, usage):
    """(judgment, reason, observed, evidence_ids, applicable, basis) 를 돌려준다."""
    cid, kind = crit['id'], crit['kind']
    if crit.get('media') and not set(crit['media']) & set(usage['media']):
        return {'applicable': False, 'judgment': '해당 없음', 'reason': 'media_not_targeted',
                'basis': crit.get('na_basis', '사용 매체 밖'), 'observed': None, 'evidence': []}
    if crit.get('applies_if_role'):
        present = [r for r in regions_of(obs, crit['applies_if_role']) if r['legibility'] not in ('unreadable',)]
        if not present:
            return {'applicable': False, 'judgment': '해당 없음', 'reason': 'condition_not_triggered',
                    'basis': crit.get('na_basis', '조건 미발생'), 'observed': None, 'evidence': []}
    if crit.get('status') == 'unresolved':
        return {'applicable': True, 'judgment': '확인 필요', 'reason': 'criterion_unresolved', 'basis': None,
                'observed': '기준 충돌(미확정)', 'evidence': [], 'unresolved': True}
    exp = expected_for(crit, ver)

    def out(j, reason, observed, ev=None):
        return {'applicable': True, 'judgment': j, 'reason': reason, 'basis': None, 'observed': observed, 'evidence': ev or []}

    if kind in ('text_equals', 'text_contains'):
        rs = regions_of(obs, crit['role'])
        if not rs:
            return out('미충족', 'text_missing', None)
        r = rs[0]
        if r['legibility'] in ILLEGIBLE:
            return out('확인 필요', 'illegible', r['transcription_raw'], [r['region_id']])
        cs = crit.get('case_sensitive', True)
        a, b = norm(r['transcription_raw'], cs), norm(exp, cs)
        ok = (a == b) if kind == 'text_equals' else (b in a)
        return out('충족' if ok else '미충족', 'match' if ok else 'value_mismatch', r['transcription_raw'], [r['region_id']])
    if kind == 'text_forbidden':
        hits = [(r['region_id'], w) for r in obs['regions'] for w in crit['words'] if w in r['transcription_raw']]
        if hits:
            return out('미충족', 'forbidden_phrase', ', '.join(w for _, w in hits), [h[0] for h in hits])
        ill = [r['region_id'] for r in obs['regions'] if r['legibility'] in ILLEGIBLE]
        return out('충족', 'no_forbidden_phrase', '금지어 없음(판독 가능한 문자 기준)', [])
    if kind == 'price_consistency':
        lp, sp, ds = (regions_of(obs, x) for x in ('정가', '판매가', '할인율'))
        if not lp:
            return out('확인 필요', 'list_price_missing', '정가 표기 없음 → 할인율 임의 계산 불가')
        if not sp or not ds:
            return out('미충족', 'field_missing', None)
        if any(r[0]['legibility'] in ILLEGIBLE for r in (lp, sp, ds)):
            return out('확인 필요', 'illegible', None, [r[0]['region_id'] for r in (lp, sp, ds)])
        L, Sv, D = num(lp[0]['transcription_raw']), num(sp[0]['transcription_raw']), None
        mm = re.fullmatch(r'\s*(\d+)\s*%.*', ds[0]['transcription_raw'])
        D = int(mm.group(1)) if mm else None
        ev = [lp[0]['region_id'], sp[0]['region_id'], ds[0]['region_id']]
        if L is None or Sv is None or D is None:
            return out('확인 필요', 'unparseable', f"정가 {lp[0]['transcription_raw']} / 판매가 {sp[0]['transcription_raw']} / {ds[0]['transcription_raw']}", ev)
        calc = L * (100 - D) / 100
        ok = abs(calc - Sv) < 1
        return out('충족' if ok else '미충족', 'consistent' if ok else 'inconsistent', f'{L:,}원 × (100-{D})% = {calc:,.0f}원 / 판매가 {Sv:,}원', ev)
    if kind == 'count':
        objs = obs['objects'].get(crit['cls'], [])
        cmin = sum(1 for o in objs if o['occlusion'] < 0.5)
        cmax = sum(1 for o in objs if o['occlusion'] < 0.9)
        n, op = crit['n'], crit['op']
        f = (lambda c: c >= n) if op == '>=' else (lambda c: c == n)
        ev = [o['instance_id'] for o in objs]
        ob = f'보이는 수 {cmin}' if cmin == cmax else f'보이는 수 {cmin}~{cmax} (가림)'
        if f(cmin) and f(cmax):
            return out('충족', 'count_ok', ob, ev)
        if not f(cmin) and not f(cmax):
            return out('미충족', 'count_mismatch', ob, ev)
        return out('확인 필요', 'occluded_count', ob, ev)
    if kind == 'logo_variant':
        lg = obs['logo']
        if lg is None or lg['variant'] == 'missing':
            return out('미충족', 'logo_missing', '로고 없음')
        ok = lg['variant'] in ('ref', 'small', 'tight') and abs(lg['aspect'] - 1.0) < 0.05
        return out('충족' if ok else '미충족', 'logo_ok' if ok else 'logo_distorted', f"변형 {lg['variant']} · 가로세로비 {lg['aspect']}", ['logo'])
    if kind == 'logo_min_size':
        lg = obs['logo']
        if lg is None or lg['variant'] == 'missing':
            return out('미충족', 'logo_missing', '로고 없음')
        ok = lg['width'] >= crit['min_px']
        return out('충족' if ok else '미충족', 'size_ok' if ok else 'logo_too_small', f"로고 가로 {lg['width']}px", ['logo'])
    if kind == 'logo_clear_space':
        lg = obs['logo']
        if lg is None or lg['variant'] == 'missing':
            return out('미충족', 'logo_missing', '로고 없음')
        need = crit['ratio'] * lg['height']
        ok = lg['clear_space_px'] >= need
        return out('충족' if ok else '미충족', 'clear_ok' if ok else 'clear_violation', f"여백 {lg['clear_space_px']}px / 필요 {need:.1f}px", ['logo'])
    if kind == 'product_form':
        objs = obs['objects'].get('제품 병', [])
        if not objs:
            return out('미충족', 'product_missing', None)
        a = objs[0]['attrs']
        ok = a['form'] == 'ref' and a['label_color'] == crit['label_color']
        return out('충족' if ok else '미충족', 'form_ok' if ok else 'form_changed', f"형태 {a['form']} · 라벨 {a['label_color']}", ['bottle_1'])
    if kind == 'file_spec':
        f = obs['file']
        ok = (f['w'], f['h']) == tuple(crit['size']) and f['mime'] in crit['mimes']
        return out('충족' if ok else '미충족', 'spec_ok' if ok else 'spec_mismatch', f"{f['w']}×{f['h']} {f['mime']}", [])
    if kind == 'evidence_required':
        ok = crit['evidence_id'] in obs['evidence']
        return out('충족' if ok else '미충족', 'evidence_present' if ok else 'evidence_missing', '제출됨' if ok else '제출 목록에 없음', [])

    if kind == 'text_set':
        rs = regions_of(obs, crit['role'])
        if not rs:
            return out('미충족', 'text_missing', None)
        if any(r['legibility'] in ILLEGIBLE for r in rs):
            return out('확인 필요', 'illegible', ' / '.join(r['transcription_raw'] for r in rs), [r['region_id'] for r in rs])
        got = {norm(r['transcription_raw']) for r in rs}
        want = {norm(x) for x in crit['expected']}
        ok = got == want
        return out('충족' if ok else '미충족', 'set_match' if ok else 'set_mismatch', ' / '.join(r['transcription_raw'] for r in rs), [r['region_id'] for r in rs])
    if kind == 'logo_set':
        objs = obs['objects'].get(crit['cls'], [])
        want = set(crit['expected'])
        vmin = {o['attrs']['name'] for o in objs if o['occlusion'] < 0.5}
        vmax = {o['attrs']['name'] for o in objs if o['occlusion'] < 0.9}
        ev = [o['instance_id'] for o in objs]
        label = lambda s_: ', '.join(sorted(s_)) or '없음'
        ob = f'노출 {label(vmin)}' if vmin == vmax else f'노출 {label(vmin)} (가림 포함 {label(vmax)})'
        if vmin == want and vmax == want:
            return out('충족', 'set_ok', ob, ev)
        if vmin != want and vmax != want:
            extra, miss = sorted(vmax - want), sorted(want - vmin)
            return out('미충족', 'unapproved_or_missing', ob + (f' · 미승인 {extra}' if extra else '') + (f' · 누락 {miss}' if miss else ''), ev)
        return out('확인 필요', 'occluded_set', ob, ev)
    if kind == 'consent_for_shown':
        objs = obs['objects'].get(crit['cls'], [])
        if not objs:
            return out('충족', 'no_person_shown', '노출된 인물 없음')
        names = [o['attrs']['name'] for o in objs]
        missing = [n for n in names if (crit['evidence_prefix'] + n) not in obs['evidence']]
        ok = not missing
        return out('충족' if ok else '미충족', 'consent_ok' if ok else 'consent_missing',
                   '노출 ' + ', '.join(names) + ('' if ok else f' · 동의서 없음 {missing}'), [o['instance_id'] for o in objs])
    if kind == 'attr_equals':
        objs = obs['objects'].get(crit['cls'], [])
        if not objs:
            return out('미충족', 'object_missing', None)
        o = objs[0]
        if o['occlusion'] >= 0.5:
            return out('확인 필요', 'occluded', f"{crit['attr']}={o['attrs'].get(crit['attr'])} (가림)", [o['instance_id']])
        v = o['attrs'].get(crit['attr'])
        ok = v == crit['expected']
        return out('충족' if ok else '미충족', 'attr_match' if ok else 'attr_mismatch', f"{crit['attr']}={v}", [o['instance_id']])
    if kind == 'text_min_size':
        rs = regions_of(obs, crit['role'])
        if not rs:
            return out('미충족', 'text_missing', None)
        sz = rs[0]['font_size']
        ok = sz >= crit['min_px']
        return out('충족' if ok else '미충족', 'size_ok' if ok else 'text_too_small', f"글자 크기 {sz}px / 기준 {crit['min_px']}px 이상", [rs[0]['region_id']])
    if kind == 'license_covers':
        meta = obs.get('evidence_meta', {}).get(crit['evidence_id'])
        if meta is None:
            return out('미충족', 'evidence_missing', '허락서 없음')
        u = usage
        gaps = []
        if meta['start'] > u['post_start'] or meta['end'] < u['post_end']:
            gaps.append(f"기간 {meta['start']}~{meta['end']} 이 게시 {u['post_start']}~{u['post_end']}를 포함하지 못함")
        if not set(u['media']) <= set(meta['media']):
            gaps.append(f"매체 {meta['media']} 이 {u['media']}를 포함하지 못함")
        if meta['region'] != u['region']:
            gaps.append(f"지역 {meta['region']} != {u['region']}")
        return out('충족' if not gaps else '미충족', 'license_covers' if not gaps else 'license_gap',
                   '허락 범위가 게시 계획을 포함' if not gaps else ' · '.join(gaps))

    raise ValueError(kind)


# ---------------------------------------------------------------- 문서(PDF/MD)
PAGE_W, PAGE_H = 595, 842


class Pdf:
    def __init__(self, title):
        self.doc = pymupdf.open()
        self.title = title
        self.font = pymupdf.Font(fontfile=MALGUN)
        self.fontb = pymupdf.Font(fontfile=MALGUN_BD)

    def wrap(self, text, size, width, bold=False):
        f = self.fontb if bold else self.font
        lines, cur = [], ''
        for ch in text:
            if ch == '\n':
                lines.append(cur)
                cur = ''
                continue
            if f.text_length(cur + ch, fontsize=size) > width and cur:
                lines.append(cur)
                cur = ch.lstrip() if ch == ' ' else ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    def page(self, blocks, n, total):
        p = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        p.insert_font(fontname='mg', fontfile=MALGUN)
        p.insert_font(fontname='mgb', fontfile=MALGUN_BD)
        y, L, R = 64, 56, PAGE_W - 56
        foot = []
        for b in blocks:
            k = b[0]
            if k == 'h1':
                p.insert_text((L, y), b[1], fontname='mgb', fontsize=17)
                y += 30
            elif k == 'h2':
                y += 6
                p.insert_text((L, y), b[1], fontname='mgb', fontsize=12.5)
                y += 20
            elif k in ('p', 'li'):
                ind = 14 if k == 'li' else 0
                for i, ln in enumerate(self.wrap(b[1], 10.5, R - L - ind)):
                    if k == 'li' and i == 0:
                        p.insert_text((L, y), '·', fontname='mg', fontsize=10.5)
                    p.insert_text((L + ind, y), ln, fontname='mg', fontsize=10.5)
                    y += 16
                y += 3
            elif k == 'table':
                rows, widths = b[1], b[2]
                tot = sum(widths)
                cw = [w / tot * (R - L) for w in widths]
                for ri, row in enumerate(rows):
                    cells = [self.wrap(c, 9.5, cw[ci] - 8, bold=(ri == 0)) for ci, c in enumerate(row)]
                    hgt = max(len(c) for c in cells) * 14 + 8
                    x = L
                    for ci, c in enumerate(cells):
                        p.draw_rect(pymupdf.Rect(x, y, x + cw[ci], y + hgt), color=(0.6, 0.6, 0.6), fill=(0.93, 0.95, 0.92) if ri == 0 else None, width=0.6)
                        for li, ln in enumerate(c):
                            p.insert_text((x + 4, y + 14 + li * 14), ln, fontname='mgb' if ri == 0 else 'mg', fontsize=9.5)
                        x += cw[ci]
                    y += hgt
                y += 20
            elif k == 'foot':
                foot.append(b[1])
            if y > 760:
                raise ValueError(f'page overflow: {self.title} p{n} y={y}')
        fy = 770 - 13 * len(foot)
        for ft in foot:
            for ln in self.wrap(ft, 8.5, R - L):
                p.insert_text((L, fy), ln, fontname='mg', fontsize=8.5)
                fy += 12
        p.insert_text((L, 815), f'{self.title}', fontname='mg', fontsize=8)
        p.insert_text((R - 50, 815), f'p.{n} / {total}', fontname='mg', fontsize=8)

    def save(self, path, pages):
        for i, bl in enumerate(pages, 1):
            self.page(bl, i, len(pages))
        self.doc.set_metadata({'title': self.title, 'author': '시연용 가상 문서'})
        self.doc.subset_fonts()  # 사용한 글자만 남겨 파일 크기를 줄인다(전체 글꼴 삽입 시 PDF당 25MB)
        self.doc.save(str(path), garbage=4, deflate=True)


def pages_to_md(title, pages):
    out = [f'# {title}', '', '> 시연용 가상 문서입니다.', '']
    for i, bl in enumerate(pages, 1):
        out.append(f'<!-- page {i} -->')
        for b in bl:
            k = b[0]
            if k == 'h1':
                out.append(f'## {b[1]}')
            elif k == 'h2':
                out.append(f'### {b[1]}')
            elif k == 'p':
                out.append(b[1])
            elif k == 'li':
                out.append(f'- {b[1]}')
            elif k == 'foot':
                out.append(f'> {b[1]}')
            elif k == 'table':
                rows = b[1]
                out.append('| ' + ' | '.join(rows[0]) + ' |')
                out.append('|' + '---|' * len(rows[0]))
                for r in rows[1:]:
                    out.append('| ' + ' | '.join(r) + ' |')
            out.append('')
    return '\n'.join(out)


def pdf_page_texts(path):
    d = pymupdf.open(str(path))
    return [re.sub(r'\s+', '', pg.get_text()) for pg in d]
