"""실제 이미지→Paddle 전체 OCR→조건별 재인식→검수 API를 실행해 증거를 보존한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path)
    parser.add_argument('--criteria',type=Path,required=True,help='criteria 목록 또는 기존 검수 JSON')
    parser.add_argument('--expected',type=Path,help='추론 완료 후에만 읽을 전사 기대값 JSON; title/year/arc 키')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    criteria_input=json.loads(args.criteria.read_text(encoding='utf-8'))
    criteria=criteria_input['criteria'] if isinstance(criteria_input,dict) else criteria_input
    args.output.mkdir(parents=True,exist_ok=True)
    # 이 검증 프로세스에서만 로컬 Paddle 경로를 고정한다. 외부 모델은 호출하지 않는다.
    os.environ.update(RUN_TARGET='local',OCR_PIPELINE='paddle-only',OCR_CONDITION_RETRY='1',LEGACY_READ_ONLY='0')
    os.environ.pop('REVIEW_MODELS_URL',None)
    from fastapi.testclient import TestClient
    from backend.app.api import create_app
    started=time.monotonic()
    with TestClient(create_app(review_root=args.output/'reviews')) as client:
        response=client.post('/api/reviews',data={'criteria':json.dumps(criteria,ensure_ascii=False),'criteria_version':1,'project_name':'실제 Paddle 조건별 재인식 검증'},
            files={'image':(args.image.name,args.image.read_bytes(),'application/octet-stream')})
        if response.status_code!=202:
            parser.error(response.text)
        result=client.get('/api/reviews/'+response.json()['id']).json()
    (args.output/'review-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary=result.get('ocr',{}).get('conditional_retry',{})
    chosen={}
    for item in summary.get('attempts',[]):
        if item['kind'] not in chosen or item['confidence']>chosen[item['kind']]['confidence']:
            chosen[item['kind']]=item
    # 기대값은 이 시점에만 읽으며 영역 제안·전처리·Paddle 작업자 입력에는 전달하지 않는다.
    expected=json.loads(args.expected.read_text(encoding='utf-8')) if args.expected else {}
    def norm(text):return ''.join(unicodedata.normalize('NFC',text).split())
    checks={key:{'expected':value,'observed':chosen.get(key,{}).get('text'),
        'score':chosen.get(key,{}).get('confidence'),
        'exact_equal':chosen.get(key,{}).get('text')==value,
        'whitespace_normalized_equal':norm(chosen.get(key,{}).get('text',''))==norm(value)} for key,value in expected.items()}
    report={'review_id':result['id'],'status':result['status'],'elapsed_seconds':round(time.monotonic()-started,3),
        'actual_full_paddle_inference':True,'actual_region_paddle_inference':summary.get('status')=='success',
        'llm_inference_executed':False,'expected_loaded_after_inference':bool(expected),
        'image_sha256':hashlib.sha256(args.image.read_bytes()).hexdigest(),
        'criteria_sha256':hashlib.sha256(args.criteria.read_bytes()).hexdigest(),
        'expected_sha256':hashlib.sha256(args.expected.read_bytes()).hexdigest() if args.expected else None,
        'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [
            'backend/app/paddle_conditions.py','backend/app/paddle_condition_worker.py','backend/app/structured_review.py','backend/app/api.py']},
        'checks':checks,'scope':'한 포스터의 지정 전사값 비교. 모델 전체 정확도·자동 승인 평가가 아닙니다.',
        'retry_status':summary.get('status'),'model_identity':summary.get('model_identity',{})}
    (args.output/'transcription-checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return int(result['status']!='completed' or any(not c['whitespace_normalized_equal'] for c in checks.values()))


if __name__=='__main__':raise SystemExit(main())
