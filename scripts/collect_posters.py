"""문화행사 공개 API로 포스터 후보와 기준 정보를 모은다 (포스터 100건 수집용 초안).

- 웹 페이지(HTML)는 수집하지 않는다. API 응답과, API가 알려 준 이미지 주소만 쓴다.
- 원본 응답·이미지는 Git 제외 폴더(.runtime/poster-collection/)에만 둔다. 저장소 반영·재배포 금지.
- 인증키는 환경변수로만 받고 파일·로그에 남기지 않는다.
- 결과는 '후보'다. 정답 라벨(gold)은 만들지 않고 권리 상태는 unconfirmed로 둔다.

출처
  seoul    서울 열린데이터광장 OA-15486 '서울시 문화행사 정보'(culturalEventInfo). 환경변수 SEOUL_API_KEY.
           요청 주소·응답 필드는 공개 sample 키 호출로 확인했다(2026-10-12). 키가 없으면
           probe/collect 에 --sample-key 를 주어 5건까지 시험할 수 있다.
  culture  공공데이터포털 15138937 '한국문화정보원_한눈에보는문화정보조회서비스'. 환경변수 DATA_GO_KR_SERVICE_KEY.
           요청 경로(B553457/cultureinfo/period2, detail2)만 확인했다. 요청 변수·응답 필드는 키 발급 후
           `probe culture` 로 확인해 아래 CULTURE 설정을 고치거나 --param 으로 덮어쓴다.

사용 예
  python scripts/collect_posters.py probe seoul --sample-key
  python scripts/collect_posters.py collect seoul --from 2026-10-15 --to 2026-12-31 --limit 20 --worker 안정민
  python scripts/collect_posters.py collect seoul --district 강남구 --category 축제-문화/예술 --limit 20
  python scripts/collect_posters.py probe culture
  python scripts/collect_posters.py summary
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / '.runtime' / 'poster-collection'
USER_AGENT = 'SKN33-Final-1Team poster-collection (internal evaluation)'
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SCHEMA_FORMATS = {'JPEG': ('jpeg', 'jpg'), 'PNG': ('png', 'png'), 'WEBP': ('webp', 'webp')}  # poster-case.schema.json input.image.format
VAGUE = re.compile(r'상이|별도|추후|미정|문의')  # 요금·시간이 구체값이 아니면 기준 정보로 쓰기 어렵다
RIGHTS_NOTE = '포스터 이미지의 제3자 저작권 미확인. 내부 개발·평가용으로만 사용하고 저장소 반영·재배포하지 않는다.'

SEOUL = {
    'api': '서울 열린데이터광장 OA-15486 서울시 문화행사 정보 (원천: 서울문화포털)',
    'license': '공공누리 1유형(카탈로그 표기)',
    'env': 'SEOUL_API_KEY',
    'url': 'http://openapi.seoul.go.kr:8088/{key}/json/culturalEventInfo/{start}/{end}/',
    'page_size': 1000,  # 1회 최대 요청 건수
}

CULTURE = {
    'api': '공공데이터포털 15138937 한국문화정보원_한눈에보는문화정보조회서비스',
    'license': '이용허락범위 제한 없음(카탈로그 표기)',
    'env': 'DATA_GO_KR_SERVICE_KEY',
    'base': 'https://apis.data.go.kr/B553457/cultureinfo',
    'list_op': 'period2',
    'detail_op': 'detail2',
    'page_size': 100,
    # TODO(키 발급 후): 아래 요청 변수·필드 이름은 기존 문화포털 공연전시 API 기준 추정이다.
    # `probe culture` 출력으로 확인해 고친다. 값의 {from} {to} {page} {rows} {seq} 는 실행 시 채운다.
    'list_params': {'from': '{from}', 'to': '{to}', 'cPage': '{page}', 'rows': '{rows}'},
    'detail_params': {'seq': '{seq}'},
    'item_tags': ('item', 'perforList', 'perforInfo'),
    'fields': {
        'source_id': ('seq',), 'title': ('title',), 'start_date': ('startDate',), 'end_date': ('endDate',),
        'time': (), 'venue': ('place',), 'organizer': (), 'fee': ('price',), 'contact': ('phone',),
        'url': ('url',), 'detail_page': (), 'image_url': ('imgUrl', 'thumbnail'),
        'category': ('realmName',), 'district': ('area', 'sigungu'),
    },
}


class ApiError(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def redact(text, secret):
    if not secret:
        return text
    for s in {secret, urllib.parse.quote(secret, safe='')}:
        text = text.replace(s, '***')
    return text


def http_get(url, secret=None, max_bytes=None, retries=3):
    """GET with retry on network errors and 5xx. Error messages never contain the key."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read(max_bytes + 1) if max_bytes else response.read()
                if max_bytes and len(body) > max_bytes:
                    raise ApiError(f'응답이 {max_bytes} 바이트를 넘습니다')
                return body, response.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries:
                raise ApiError(f'HTTP {e.code}') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise ApiError(redact(f'연결 실패: {e}', secret)) from None
        time.sleep(2 ** attempt)


def norm_date(value):
    """'2026-12-24 00:00:00.0', '20261224', '2026.12.24' -> '2026-12-24'."""
    digits = re.sub(r'\D', '', value or '')[:8]
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
    except ValueError:
        return None


def norm_time(value):
    """Only a single clock time ('19:30', '9:00') maps to the schema; ranges and prose stay raw."""
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', value or '')
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f'{int(m[1]):02d}:{m[2]}'


def seoul_full_image(url):
    # MAIN_IMG 는 thumb=Y 가 붙어 온다. 떼면 같은 해상도에서 압축이 덜 된 원본을 준다(2026-10-12 확인).
    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k.lower() != 'thumb']
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def seoul_record(row):
    detail = row.get('HMPG_ADDR') or ''
    m = re.search(r'cultcode=(\d+)', detail)
    fallback = hashlib.sha1(f"{row.get('TITLE')}|{row.get('DATE')}".encode()).hexdigest()[:12]
    return {
        'source_id': m[1] if m else fallback, 'title': row.get('TITLE'),
        'start_date': norm_date(row.get('STRTDATE')), 'end_date': norm_date(row.get('END_DATE')),
        'time': row.get('PRO_TIME'), 'venue': row.get('PLACE'), 'organizer': row.get('ORG_NAME'),
        'fee': row.get('USE_FEE'), 'contact': row.get('INQUIRY'),
        'url': row.get('ORG_LINK') or detail, 'detail_page': detail,
        'image_url': seoul_full_image(row.get('MAIN_IMG')),
        'category': row.get('CODENAME'), 'district': row.get('GUNAME'),
    }


def culture_record(item):
    rec = {}
    for name, candidates in CULTURE['fields'].items():
        rec[name] = next((item[c] for c in candidates if item.get(c)), None)
    rec['start_date'], rec['end_date'] = norm_date(rec['start_date']), norm_date(rec['end_date'])
    return rec


def reference_event(rec):
    """Map a normalized record onto poster-case.schema.json input.reference.event (only values that fit)."""
    event = {
        'name': rec.get('title'), 'start_date': rec.get('start_date'), 'end_date': rec.get('end_date'),
        'start_time': norm_time(rec.get('time')),
        'venues': [rec['venue']] if rec.get('venue') else None,
        'organizer': rec.get('organizer') if rec.get('organizer') not in (None, '', '기타') else None,
        'admission_fee': rec.get('fee'), 'contact': rec.get('contact'), 'url': rec.get('url'),
    }
    return {k: v for k, v in event.items() if v}


def quality(rec, event, image=None):
    vague = [k for k in ('fee', 'time') if rec.get(k) and VAGUE.search(rec[k])]
    wanted = ('name', 'start_date', 'end_date', 'start_time', 'venues', 'admission_fee', 'contact')
    out = {'vague_fields': vague, 'missing_reference_fields': [k for k in wanted if k not in event]}
    if image:
        out['short_side'] = min(image['width'], image['height'])
    return out


def overlaps(rec, start, end):
    s, e = rec.get('start_date'), rec.get('end_date') or rec.get('start_date')
    if not s:
        return False
    return (end is None or s <= end) and (start is None or e >= start)


def parse_xml_items(body, item_tags):
    if b'<!DOCTYPE' in body or b'<!ENTITY' in body:
        raise ApiError('DTD가 들어 있는 XML 응답은 처리하지 않습니다')
    root = ET.fromstring(body)
    err = root.findtext('.//errMsg')
    if err:
        raise ApiError(f"공공데이터포털 오류: {err} ({root.findtext('.//returnAuthMsg') or ''})")
    code = root.findtext('.//resultCode')
    if code not in (None, '', '00', '0', '0000'):
        raise ApiError(f"API 오류 {code}: {root.findtext('.//resultMsg') or ''}")
    items = [{child.tag: (child.text or '').strip() for child in node} for node in root.iter() if node.tag in item_tags]
    total = (root.findtext('.//totalCount') or '').strip()
    return (int(total) if total.isdigit() else None), items


class Store:
    """Accumulates candidates across runs; dedupes by candidate id and original image hash."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'candidates.jsonl'
        self.ids, self.hashes = set(), set()
        for cand in self.load():
            self._remember(cand)

    def load(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines() if line.strip()]

    def _append(self, name, obj):
        with (self.root / name).open('a', encoding='utf-8') as f:
            f.write(json.dumps(obj, ensure_ascii=False) + '\n')

    def _remember(self, cand):
        self.ids.add(cand['candidate_id'])
        sha = (cand.get('image') or {}).get('original_sha256')
        if sha:
            self.hashes.add(sha)

    def add(self, cand):
        self._append('candidates.jsonl', cand)
        self._remember(cand)

    def preview(self, cand):
        # --dry-run 결과는 따로 둔다. 후보 목록에 섞이면 다음 실제 수집에서 건너뛰게 된다.
        self._append('dry-run.jsonl', cand)

    def fail(self, **entry):
        self._append('failures.jsonl', {'at': now_iso(), **entry})

    def save_raw(self, source, label, body, ext):
        folder = self.root / 'raw' / source
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}.{ext}"
        path.write_bytes(body)
        return path.relative_to(self.root).as_posix()

    def save_image(self, body):
        """Keep the original bytes; formats the schema lacks (GIF, BMP...) also get a PNG copy from frame 1."""
        original_sha = hashlib.sha256(body).hexdigest()
        im = Image.open(io.BytesIO(body))
        im.load()
        folder = self.root / 'images'
        folder.mkdir(exist_ok=True)
        stem = original_sha[:16]
        info = {'original_format': im.format, 'original_sha256': original_sha, 'bytes': len(body),
                'frames': getattr(im, 'n_frames', 1)}
        if im.format in SCHEMA_FORMATS:
            fmt, ext = SCHEMA_FORMATS[im.format]
            path = folder / f'{stem}.{ext}'
            path.write_bytes(body)
            sha = original_sha
        else:
            original = folder / f'{stem}.orig.{(im.format or "bin").lower()}'
            original.write_bytes(body)
            info['original_path'] = original.relative_to(self.root).as_posix()
            im.seek(0)
            alpha = im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info)
            buf = io.BytesIO()
            im.convert('RGBA' if alpha else 'RGB').save(buf, 'PNG')
            fmt, path = 'png', folder / f'{stem}.png'
            path.write_bytes(buf.getvalue())
            sha = hashlib.sha256(buf.getvalue()).hexdigest()
        return {'path': path.relative_to(self.root).as_posix(), 'format': fmt, 'width': im.width,
                'height': im.height, 'sha256': sha, **info}


# ---------- sources ----------

def seoul_key(args):
    if args.sample_key:
        return 'sample'
    key = os.environ.get(SEOUL['env'])
    if not key:
        raise SystemExit(f"환경변수 {SEOUL['env']} 가 없습니다. 열린데이터광장 인증키를 넣거나 --sample-key 로 5건만 시험하세요.")
    return key


def seoul_pages(store, key, max_pages):
    size = 5 if key == 'sample' else SEOUL['page_size']  # sample 키는 1~5건만 허용
    start, pages = 1, 0
    while pages < max_pages:
        url = SEOUL['url'].format(key=urllib.parse.quote(key, safe=''), start=start, end=start + size - 1)
        body, _ = http_get(url, secret=key)
        raw = store.save_raw('seoul', f'p{start}', body, 'json')
        data = json.loads(body)
        block = data.get('culturalEventInfo') or {}
        result = block.get('RESULT') or data.get('RESULT') or {}
        if result.get('CODE') == 'INFO-200':
            return
        if result.get('CODE') != 'INFO-000':
            raise ApiError(f"서울 API 오류 {result.get('CODE')}: {result.get('MESSAGE')}")
        rows = block.get('row') or []
        yield raw, [seoul_record(r) for r in rows], rows
        pages += 1
        start += size
        if key == 'sample' or not rows or start > block.get('list_total_count', 0):
            return


def culture_key():
    key = os.environ.get(CULTURE['env'])
    if not key:
        raise SystemExit(f"환경변수 {CULTURE['env']} 가 없습니다. 공공데이터포털 서비스키를 넣으세요.")
    return key if '%' in key else urllib.parse.quote(key, safe='')  # 인코딩 키·디코딩 키 모두 허용


def culture_url(op, template, key, **values):
    params = {k: v.format(**values) for k, v in template.items() if v != ''}
    return f"{CULTURE['base']}/{op}?serviceKey={key}&{urllib.parse.urlencode(params)}"


def culture_pages(store, key, args, rows):
    params = {**CULTURE['list_params'], **dict(args.param)}
    frm = (args.date_from or date.today().isoformat()).replace('-', '')
    to = (args.date_to or '').replace('-', '')
    for page in range(1, args.max_pages + 1):
        url = culture_url(CULTURE['list_op'], params, key, **{'from': frm, 'to': to, 'page': page, 'rows': rows})
        body, _ = http_get(url, secret=key)
        raw = store.save_raw('culture', f'p{page}', body, 'xml')
        total, items = parse_xml_items(body, CULTURE['item_tags'])
        yield raw, [culture_record(i) for i in items], items
        if not items or len(items) < rows or (total is not None and page * rows >= total):
            return


def culture_detail(store, key, item):
    seq = culture_record(item).get('source_id')
    if not seq:
        return item
    body, _ = http_get(culture_url(CULTURE['detail_op'], CULTURE['detail_params'], key, seq=seq), secret=key)
    store.save_raw('culture', f'detail-{seq}', body, 'xml')
    _, detail = parse_xml_items(body, CULTURE['item_tags'])
    return {**item, **{k: v for k, v in (detail[0] if detail else {}).items() if v}}


# ---------- commands ----------

def cmd_probe(args):
    """Fetch one small page, save the raw response, and print the field names actually returned."""
    store = Store(args.out)
    if args.source == 'seoul':
        raw, recs, rows = next(seoul_pages(store, seoul_key(args), 1), (None, [], []))
    else:
        key = culture_key()
        raw, recs, rows = next(culture_pages(store, key, args, 3), (None, [], []))
        if rows:
            detailed = culture_detail(store, key, rows[0])
            print('상세 응답 필드:', sorted(set(detailed) - set(rows[0])) or '(목록과 같음)')
    print('원본 응답:', store.root / raw if raw else '(없음)')
    print('목록 응답 필드:', sorted({k for r in rows for k in r}) or '(항목 없음 — 요청 변수를 확인하세요)')
    if recs:
        print('첫 항목 매핑 결과:')
        print(json.dumps({'record': recs[0], 'reference.event': reference_event(recs[0])}, ensure_ascii=False, indent=2))


def cmd_collect(args):
    store = Store(args.out)
    stats = Counter()
    start = args.date_from or date.today().isoformat()
    if args.source == 'seoul':
        key = seoul_key(args)
        pages = seoul_pages(store, key, args.max_pages)
    else:
        key = culture_key()
        pages = culture_pages(store, key, args, CULTURE['page_size'])
    meta = SEOUL if args.source == 'seoul' else CULTURE
    collected = 0
    for raw, recs, rows in pages:
        for rec, row in zip(recs, rows):
            stats['seen'] += 1
            cid = f"{args.source}:{rec.get('source_id')}"
            if cid in store.ids:
                stats['skip_already_collected'] += 1
                continue
            if not overlaps(rec, start, args.date_to):
                stats['skip_out_of_range'] += 1
                continue
            if (args.district and rec.get('district') not in args.district) or (args.category and rec.get('category') not in args.category):
                stats['skip_filtered'] += 1
                continue
            if args.source == 'culture' and not args.no_detail:
                try:
                    rec = culture_record(culture_detail(store, key, row))
                except ApiError as e:
                    store.fail(candidate_id=cid, stage='detail', error=str(e))
            event = reference_event(rec)
            q = quality(rec, event)
            if q['vague_fields'] and not args.include_vague:
                stats['skip_vague_reference'] += 1
                continue
            if not rec.get('image_url'):
                stats['skip_no_image'] += 1
                continue
            image = None
            if not args.dry_run:
                time.sleep(args.delay)
                try:
                    body, ctype = http_get(rec['image_url'], max_bytes=MAX_IMAGE_BYTES)
                    if not ctype.startswith('image/'):
                        raise ApiError(f'이미지가 아닌 응답: {ctype}')
                    if hashlib.sha256(body).hexdigest() in store.hashes:
                        stats['skip_duplicate_image'] += 1
                        continue
                    if args.min_short_side and min(Image.open(io.BytesIO(body)).size) < args.min_short_side:
                        stats['skip_low_resolution'] += 1
                        continue
                    image = {'url': rec['image_url'], **store.save_image(body)}
                except (ApiError, OSError) as e:
                    store.fail(candidate_id=cid, stage='image', url=rec['image_url'], error=str(e))
                    stats['fail_image'] += 1
                    continue
                q = quality(rec, event, image)
            (store.preview if args.dry_run else store.add)({
                'candidate_id': cid, 'collected_at': now_iso(), 'worker': args.worker, 'status': 'candidate',
                'source': {'origin': 'real_public', 'api': meta['api'], 'record_id': rec.get('source_id'),
                           'detail_page': rec.get('detail_page'), 'raw_response': raw},
                'rights': {'status': 'unconfirmed', 'license': meta['license'], 'note': RIGHTS_NOTE},
                'reference': {'event': event},
                'reference_raw': {k: rec.get(k) for k in ('title', 'start_date', 'end_date', 'time', 'venue', 'organizer', 'fee', 'contact', 'url', 'category', 'district')},
                'image': image if image else {'url': rec['image_url'], 'downloaded': False},
                'quality': q,
            })
            collected += 1
            stats['collected'] += 1
            if collected >= args.limit:
                break
        if collected >= args.limit:
            break
        time.sleep(args.delay)
    print(json.dumps(dict(stats), ensure_ascii=False))
    print('미리보기:' if args.dry_run else '후보 목록:', store.root / ('dry-run.jsonl' if args.dry_run else 'candidates.jsonl'))


def cmd_summary(args):
    cands = Store(args.out).load()
    print(f'후보 {len(cands)}건')
    for title, key in [('출처', lambda c: c['candidate_id'].split(':')[0]), ('작업자', lambda c: c.get('worker') or '-'),
                       ('상태', lambda c: c.get('status')), ('분류', lambda c: c['reference_raw'].get('category') or '-'),
                       ('지역', lambda c: c['reference_raw'].get('district') or '-'),
                       ('이미지 형식', lambda c: (c.get('image') or {}).get('original_format') or '미다운로드'),
                       ('기준값 모호', lambda c: ','.join(c['quality']['vague_fields']) or '없음')]:
        print(f'- {title}:', dict(Counter(key(c) for c in cands).most_common()))
    sides = [c['quality']['short_side'] for c in cands if 'short_side' in c['quality']]
    if sides:
        print(f'- 짧은 변(px): 최소 {min(sides)} · 중앙값 {sorted(sides)[len(sides) // 2]} · 최대 {max(sides)}')


def key_value(text):
    if '=' not in text:
        raise argparse.ArgumentTypeError('KEY=VALUE 형식이어야 합니다')
    return tuple(text.split('=', 1))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors='replace')  # 콘솔 코드페이지에 없는 문자가 제목에 있어도 멈추지 않게
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default=str(DEFAULT_OUT), help='저장 위치 (기본: .runtime/poster-collection)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('probe', 'collect'):
        p = sub.add_parser(name)
        p.add_argument('source', choices=['seoul', 'culture'])
        p.add_argument('--sample-key', action='store_true', help='서울 API 공개 sample 키로 5건만 시험')
        p.add_argument('--from', dest='date_from', help='이 날짜 이후에 끝나는 행사 (YYYY-MM-DD, 기본: 오늘)')
        p.add_argument('--to', dest='date_to', help='이 날짜 이전에 시작하는 행사 (YYYY-MM-DD)')
        p.add_argument('--param', action='append', default=[], type=key_value, metavar='KEY=VALUE', help='culture 목록 요청 변수 덮어쓰기. 값이 비면 제거')
        p.add_argument('--max-pages', type=int, default=30)
    collect = sub.choices['collect']
    collect.add_argument('--limit', type=int, default=20, help='이번 실행에서 새로 모을 후보 수')
    collect.add_argument('--worker', help='분담 기록용 작업자 이름')
    collect.add_argument('--district', action='append', help='지역(서울: GUNAME 예 강남구). 여러 번 지정 가능')
    collect.add_argument('--category', action='append', help='분류(서울: CODENAME 예 축제-문화/예술). 여러 번 지정 가능')
    collect.add_argument('--include-vague', action='store_true', help="요금·시간이 '상이' 등 모호한 행사도 포함 (REQ-01 사례용)")
    collect.add_argument('--min-short-side', type=int, help='이미지 짧은 변 최소 px. 팀 기준이 정해지면 지정')
    collect.add_argument('--no-detail', action='store_true', help='culture 상세 조회 생략')
    collect.add_argument('--dry-run', action='store_true', help='이미지를 받지 않고 후보만 기록')
    collect.add_argument('--delay', type=float, default=1.0, help='요청 사이 대기 초')
    sub.add_parser('summary')
    args = parser.parse_args(argv)
    try:
        {'probe': cmd_probe, 'collect': cmd_collect, 'summary': cmd_summary}[args.command](args)
    except ApiError as e:
        raise SystemExit(f'중단: {e}') from None


if __name__ == '__main__':
    main()
