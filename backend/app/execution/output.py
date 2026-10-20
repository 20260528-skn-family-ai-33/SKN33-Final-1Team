"""Strict, bounded output mapping. A malformed graph fails as a whole."""
import math
from uuid import uuid4
from ..hashing.canonical import canonical

MAX_RAW=16*1024*1024
MAX_RECORDS=1000
MAX_EVIDENCE=10000


class OutputError(ValueError): pass


def raw_json(value):
    raw=canonical(value)
    if len(raw)>MAX_RAW: raise OutputError('Output too large')
    return raw


def number(value,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high: raise OutputError('Invalid number')
    return value


def string(value,maximum,*,nonblank=False):
    if not isinstance(value,str) or len(value)>maximum or (nonblank and not value.strip()): raise OutputError('Invalid string')
    return value


def external(value):
    if type(value) is int and abs(value)<=2**53-1: return str(value),'integer'
    if isinstance(value,str) and value.strip() and len(value)<=150: return value,'string'
    raise OutputError('Invalid external ID')


def geometry(record,width,height,scale):
    box=record.get('bbox_xyxy');polygon=record.get('polygon')
    if box is not None:
        if not isinstance(box,list) or len(box)!=4: raise OutputError('Invalid box')
        box=[number(v,0,(width if i%2==0 else height)*scale)/scale for i,v in enumerate(box)]
        if box[0]>=box[2] or box[1]>=box[3]: raise OutputError('Degenerate box')
    if polygon is not None:
        if not isinstance(polygon,list) or not 3<=len(polygon)<=64: raise OutputError('Invalid polygon')
        polygon=[[number(p[0],0,width*scale)/scale,number(p[1],0,height*scale)/scale]
                 if isinstance(p,list) and len(p)==2 else (_ for _ in ()).throw(OutputError('Invalid point')) for p in polygon]
    return box,polygon


def normalize(payload,width,height):
    raw_json(payload)
    if not isinstance(payload,dict) or payload.get('status') not in ('success','completed','ok'):
        raise OutputError('OCR failed')
    if payload.get('input_dimensions')!=[width*2,height*2]: raise OutputError('Wrong dimensions')
    if payload.get('execution_target')!='local' or payload.get('device')!='cpu': raise OutputError('Runtime mismatch')
    records=payload.get('records')
    if not isinstance(records,list) or len(records)>MAX_RECORDS: raise OutputError('Invalid records')
    mapped=[];seen=set()
    for order,record in enumerate(records):
        if not isinstance(record,dict): raise OutputError('Invalid record')
        identifier,kind=external(record.get('id'))
        if (kind,identifier) in seen: raise OutputError('Duplicate record ID')
        seen.add((kind,identifier));text=string(record.get('text'),2000)
        confidence=record.get('confidence')
        if confidence is not None:number(confidence,0,1)
        box,polygon=geometry(record,width,height,2)
        if 'requires_review' in record and type(record['requires_review']) is not bool: raise OutputError('Invalid review flag')
        engine=record.get('recognition_engine')
        if engine not in ('paddleocr','easyocr','qwen3-vl',None): raise OutputError('Unsupported engine')
        mapped.append(dict(token_id=str(uuid4()),id=record['id'],external_token_id=identifier,external_token_type=kind,
            token_order=order,text=text,confidence=confidence,bbox_xyxy=box,polygon=polygon,recognition_engine=engine,
            requires_review=record.get('requires_review'),source_record=record))
    return mapped


def aggregate(items):
    if any(item['verdict']=='fail' for item in items):return 'fail'
    if any(item['execution_status']!='completed' or item['verdict']=='review' for item in items):return 'review'
    return 'pass'


def results(criteria,raw,records,frame_id):
    raw_json(raw)
    if not isinstance(raw,list) or len(raw)!=len(criteria): raise OutputError('Incomplete results')
    by_criterion={item['external_item_id']:item for item in criteria}
    by_token={(item['external_token_type'],item['external_token_id']):item for item in records}
    mapped=[];seen=set();total=0
    for result in raw:
        if not isinstance(result,dict): raise OutputError('Invalid result')
        identifier=result.get('criterion_id')
        if identifier not in by_criterion or identifier in seen: raise OutputError('Wrong criterion')
        seen.add(identifier);criterion=by_criterion[identifier]
        processing=result.get('processing_status','completed')
        if processing not in ('completed','failed','unsupported'): raise OutputError('Invalid processing status')
        verdict=result.get('status') if processing=='completed' else None
        if processing=='completed' and verdict not in ('pass','fail','review'): raise OutputError('Invalid verdict')
        observed=result.get('observed_value',result.get('observed_text'))
        raw_json(observed)
        reason=string(result.get('reason'),20000,nonblank=True)
        method=string(result.get('check_method','cc_conservative_rules'),100,nonblank=True)
        evidence=result.get('evidence',[])
        if not isinstance(evidence,list): raise OutputError('Invalid evidence')
        total+=len(evidence)
        if total>MAX_EVIDENCE: raise OutputError('Too much evidence')
        links=[]
        for item in evidence:
            if not isinstance(item,dict): raise OutputError('Invalid evidence record')
            token=by_token.get(tuple(reversed(external(item.get('ocr_id')))))
            if token is None: raise OutputError('Unknown evidence source')
            # Copy measured token geometry/content rather than trusting duplicate evidence coordinates.
            links.append(dict(id=str(uuid4()),source_kind='ocr',text=token['text'],bbox_xyxy=token['bbox_xyxy'],polygon=token['polygon'],
                confidence=token['confidence'],frame_id=frame_id,coordinate_space='normalized_original_pixels',token_ids=[token['token_id']],
                method_code=method,limitations={'requires_review':token['requires_review']}))
        links.sort(key=lambda value:value['id'])
        mapped.append(dict(criteria_item_id=str(criterion['criteria_item_id']),result_id=str(uuid4()),position=criterion['position'],
            execution_status=processing,verdict=verdict,observed_value=observed,reason=reason,method_code=method,
            checker_version='cc-rules-v1',limitations=result.get('limitations'),evidence=links))
    return sorted(mapped,key=lambda item:item['position'])


def observations(event,records):
    raw_json(event)
    if not isinstance(event,dict) or not isinstance(event.get('fields'),dict): raise OutputError('Invalid extraction')
    by_id={tuple(reversed(external(item['id']))):item for item in records}
    mapped=[]
    for name,field in event['fields'].items():
        string(name,100,nonblank=True)
        if not isinstance(field,dict): raise OutputError('Invalid field')
        value=field.get('value');ids=field.get('source_ocr_ids',[])
        if not isinstance(ids,list): raise OutputError('Invalid extraction sources')
        tokens=[]
        for identifier in ids:
            token=by_id.get(tuple(reversed(external(identifier))))
            if token is None: raise OutputError('Unknown field source')
            tokens.append(token)
        # Missing values remain not_checked; no inference of absence from null or empty OCR.
        resolved=value is not None and bool(tokens) and field.get('status')=='extracted'
        mapped.append(dict(observation_id=str(uuid4()),field_code=name,field_path='/fields/'+name,
            presence_status='present' if tokens else 'not_checked',resolution_status='resolved' if resolved else 'review_needed',
            raw_text=' / '.join(token['text'] for token in tokens) if tokens else None,
            normalized_value=value if resolved else None,source_status=field.get('status'),source_reason=field.get('reason'),
            token_ids=[token['token_id'] for token in tokens]))
    return mapped
