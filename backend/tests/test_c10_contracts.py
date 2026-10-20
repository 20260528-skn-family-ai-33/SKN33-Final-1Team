"""C10 output validation and fixed request/manifest semantics without a model or DB."""
from copy import deepcopy
from uuid import uuid4
import pytest
from pydantic import ValidationError
from backend.app.execution.output import normalize,results,observations,aggregate,raw_json,OutputError
from backend.app.contracts.reviews import ReviewCreate,Retry
from backend.app.hashing.canonical import digest


def payload():return dict(status='success',device='cpu',execution_target='local',input_dimensions=[8,6],records=[dict(id='x',text='축제',confidence=.9,bbox_xyxy=[0,0,8,6],recognition_engine='paddleocr')])


@pytest.mark.parametrize('path,value',[
 ('status','failed'),('device','cuda'),('execution_target','runpod'),('input_dimensions',[4,3]),('records',{}),
 ('confidence',True),('confidence',float('nan')),('confidence',1.1),('confidence',-0.1),('text',1),('text','x'*2001),
 ('id',True),('id',None),('id',' '),('id',2**53),('bbox_xyxy',[0,0,9,6]),('bbox_xyxy',[1,1,1,2]),
 ('bbox_xyxy',[0,0,8]),('polygon',[[0,0],[1,1]]),('requires_review','yes'),('recognition_engine','invented')])
def test_strict_ocr_contract(path,value):
    data=payload()
    if path in data:data[path]=value
    else:data['records'][0][path]=value
    with pytest.raises((OutputError,ValueError)):normalize(data,4,3)


def test_geometry_is_restored_once_and_missing_coordinates_scores_stay_null():
    data=payload();data['records'][0].update(bbox_xyxy=None,confidence=None)
    result=normalize(data,4,3)[0];assert result['bbox_xyxy'] is None and result['confidence'] is None
    data=payload();data['records'][0]['polygon']=[[0,0],[8,0],[8,6],[0,6]]
    result=normalize(data,4,3)[0];assert result['bbox_xyxy']==[0,0,4,3] and result['polygon'][2]==[4,3]


def test_external_string_and_integer_ids_keep_distinct_types():
    data=payload();data['records']=[dict(data['records'][0],id=1),dict(data['records'][0],id='1')]
    records=normalize(data,4,3);assert {r['external_token_type'] for r in records}=={'string','integer'}
    data['records'].append(data['records'][0])
    with pytest.raises(OutputError):normalize(data,4,3)


@pytest.mark.parametrize('items,expected',[
 ([{'execution_status':'completed','verdict':'pass'}],'pass'),
 ([{'execution_status':'unsupported','verdict':None}],'review'),
 ([{'execution_status':'failed','verdict':None}],'review'),
 ([{'execution_status':'completed','verdict':'review'}],'review'),
 ([{'execution_status':'completed','verdict':'fail'},{'execution_status':'unsupported','verdict':None}],'fail')])
def test_exact_all_selected_policy(items,expected):assert aggregate(items)==expected


def test_null_is_not_absence_and_non_null_without_evidence_is_not_resolved():
    rows=observations({'fields':{'location':{'value':None,'source_ocr_ids':[]},'date':{'value':'2026','status':'extracted','source_ocr_ids':[]}}},[])
    assert all(row['presence_status']=='not_checked' and row['resolution_status']=='review_needed' and row['normalized_value'] is None for row in rows)


@pytest.mark.parametrize('body',[
 {'project_id':str(uuid4()),'image_id':str(uuid4()),'criteria_version_id':str(uuid4()),'execution_target':'runpod'},
 {'project_id':1,'image_id':str(uuid4()),'criteria_version_id':str(uuid4())},{'project_id':str(uuid4()),'image_id':str(uuid4())}])
def test_create_accepts_resource_ids_only(body):
    with pytest.raises(ValidationError):ReviewCreate.model_validate(body)


def test_retry_is_empty_and_request_hash_does_not_include_runtime_or_key():
    with pytest.raises(ValidationError):Retry.model_validate({'model':'anything'})
    payload={'actor_user_id':str(uuid4()),'operation':'retry_review','source_run_id':str(uuid4()),'body':{}}
    first=digest('retry_review',payload,'cc-request-jcs-v1')
    assert first==digest('retry_review',deepcopy(payload),'cc-request-jcs-v1')
    assert first!=digest('create_review',payload,'cc-request-jcs-v1')
