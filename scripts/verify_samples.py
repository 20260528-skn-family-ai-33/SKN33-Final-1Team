"""Run development samples through the live API; no answer-key lookup.

This writes real confirmation/upload/run records to the local application.
The output is a measurement report, not an accuracy claim.
"""
import json
import argparse
import http.cookiejar
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

BASE = 'http://127.0.0.1:' + os.environ.get('CHECKER_PORT', '8000')
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
csrf_token = None

def request(path, payload=None):
    headers = {'Content-Type': 'application/json'}
    if payload is not None and csrf_token:
        headers['X-CSRFToken'] = csrf_token
    req = urllib.request.Request(BASE + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    with opener.open(req, timeout=180) as response:
        return json.load(response)

parser = argparse.ArgumentParser(description=__doc__)
group = parser.add_mutually_exclusive_group()
group.add_argument('--all-topics', action='store_true')
group.add_argument('--topic', default='strawberry-latte')
args = parser.parse_args()
csrf_token = request('/api/csrf')['csrf_token']
topics = request('/api/topics') if args.all_topics else [{'id': args.topic}]
report = []
for topic in topics:
    for sample in request('/api/samples?' + urllib.parse.urlencode({'topic_id': topic['id']})):
        asset = request('/api/assets/from-sample', {'sample_id': sample['id'], 'topic_id': topic['id']})
        context = {'topic_id': topic['id']}
        if asset.get('condition_id'):
            context['condition_id'] = asset['condition_id']
        criteria = request('/api/criteria?' + urllib.parse.urlencode(context))
        request('/api/criteria/confirm', {**context, 'version': criteria['version']})
        started = time.monotonic()
        run = request('/api/runs', {'asset_id': asset['id'], 'topic_id': topic['id'], 'criteria_version': criteria['version'], 'idempotency_key': 'live-smoke-' + asset['id']})
        row = {'topic_id': topic['id'], 'condition_id': asset.get('condition_id'), 'sample_id': sample['id'], 'run_id': run['id'], 'status': run['status'], 'seconds': round(time.monotonic()-started, 2), 'results': run['results'], 'trace': run.get('trace', [])}
        report.append(row)
        print(json.dumps({'topic': topic['id'], 'sample': sample['id'], 'status': run['status'], 'seconds': row['seconds'], 'results': [[r['rule_id'],r['verdict'],r.get('observed')] for r in run['results']]}, ensure_ascii=False), flush=True)
output = Path(__file__).resolve().parent.parent / '.runtime' / ('all-topic-execution-report.json' if args.all_topics else 'sample-execution-report.json')
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps({'scope':'실제 실행 관찰; 독립 성능 평가 아님','samples':report}, ensure_ascii=False, indent=2))
print('Report:', output)
if any(row['status'] == 'failed' for row in report):
    raise SystemExit('검사 실패가 있습니다. 실행 기록을 확인하세요.')
