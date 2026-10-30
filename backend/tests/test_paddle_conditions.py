"""조건별 영역 제안·변환·실패 복구를 실제 모델 없이 검증한다."""
import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient

from backend.app import paddle_conditions as retry
from backend.app import paddle_condition_worker as worker
from backend.app import api
from backend.app.document_conditions import render_condition
from backend.app.review_rules import review_criteria
from backend.app.structured_review import compare


def record(text='오독제목문구',identifier='title',box=None):
    return {'id':identifier,'text':text,'confidence':.71,'recognition_engine':'paddleocr','bbox_xyxy':box or [20,30,280,110]}


def criterion(value='기대행사명',label='행사명',identifier='title'):
    return {'id':identifier,'text':f'{label}: {value}','type':'information','source':'user'}


def synthetic_arc():
    image=Image.new('RGB',(600,800),(10,70,10))
    draw=ImageDraw.Draw(image)
    for angle in np.linspace(-2.65,-.45,16):
        x,y=300+200*np.cos(angle),650+200*np.sin(angle)
        draw.rectangle((round(x)-5,round(y)-12,round(x)+5,round(y)+12),fill='white')
    rows=[record('LATIN',box=[100,380,280,520]),record('WORD',identifier='right',box=[330,420,520,600])]
    return image,rows


def test_arc_geometry_and_polygon_are_grounded_in_pixels():
    image,rows=synthetic_arc()
    views,meta=retry.arc_views(image,rows)
    assert meta['status']=='found' and len(views)==2
    for crop,transform in views:
        assert len(transform['polygon'])==24
        assert all(0<=x<=600 and 0<=y<=800 for x,y in transform['polygon'])
        assert crop.width>300 and crop.height>=80
        assert abs(transform['center'][0]-300)<20 and abs(transform['center'][1]-650)<20
        assert abs(transform['radius']-200)<20


def test_arc_without_two_supported_anchors_does_not_invent_a_crop():
    image,rows=synthetic_arc()
    assert retry.arc_views(image,rows[:1])[0]==[]


def test_expected_words_do_not_change_region_proposals(monkeypatch):
    image=Image.new('RGB',(300,200),'black')
    rows=[record()]
    calls=[]
    def views(image,source,kind):
        calls.append((source['id'],kind))
        return []
    monkeypatch.setattr(retry,'line_views',views)
    retry.plan(image,rows,[criterion('행사명하나')],{})
    first=calls.copy();calls.clear()
    retry.plan(image,rows,[criterion('다른문구정답')],{})
    assert first==calls==[('title','title')]


def test_retry_never_replaces_read_text_with_expected_text(monkeypatch,tmp_path):
    source=tmp_path/'image.png';Image.new('RGB',(300,200)).save(source)
    rows=[record()];original=copy.deepcopy(rows)
    monkeypatch.setattr(retry,'plan',lambda *args: ([(Image.new('RGB',(200,60)),
        {'kind':'title','language':'ko','source_ocr_id':'title','bbox_xyxy':[20,30,280,110],'polygon':None})],{}))
    def read(crops,directory,timeout):
        return {'outputs':[{'id':crops[0]['id'],'text':'실제로읽은오독','confidence':.999}], 'model_identity':{}}
    new,summary=retry.refine(source,rows,[criterion('기대한정답')],{},tmp_path/'retry',read=read,check_models=False)
    assert summary['status']=='success' and new[0]['text']=='실제로읽은오독'
    assert new[0]['baseline_text']==rows[0]['text'] and new[0]['requires_review'] is True
    assert rows==original
    result=compare([criterion('기대한정답')],review_criteria([criterion('기대한정답')],new,multiline=True),new,{})[0]
    assert result['status']=='review'


@pytest.mark.parametrize('failure',[subprocess.TimeoutExpired('worker',90),ValueError('private/internal')],ids=['timeout','invalid-response'])
def test_retry_failure_preserves_original_records(monkeypatch,tmp_path,failure):
    source=tmp_path/'image.png';Image.new('RGB',(300,200)).save(source)
    rows=[record()]
    monkeypatch.setattr(retry,'plan',lambda *args: ([(Image.new('RGB',(200,60)),
        {'kind':'title','language':'ko','source_ocr_id':'title','bbox_xyxy':[20,30,280,110],'polygon':None})],{}))
    def fail(*args):raise failure
    new,summary=retry.refine(source,rows,[criterion()],{},tmp_path/'retry',read=fail,check_models=False)
    assert new==rows and summary['status']=='failed'
    assert 'private' not in json.dumps(summary)


def test_worker_rejects_expected_text_in_input_manifest(tmp_path):
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps([{'id':'a','path':'image.png','language':'ko','expected_text':'정답'}]))
    with pytest.raises(ValueError,match='Only crop identity'):
        worker.run(manifest,tmp_path/'out.json',tmp_path)


def test_missing_english_model_is_recorded_without_losing_korean_retry(monkeypatch,tmp_path):
    source=tmp_path/'image.png';Image.new('RGB',(300,200)).save(source)
    rows=[record()]
    ko={'kind':'title','language':'ko','source_ocr_id':'title','bbox_xyxy':[20,30,280,110],'polygon':None}
    en={**ko,'kind':'arc','language':'en','source_ocr_id':None}
    monkeypatch.setattr(retry,'plan',lambda *args: ([(Image.new('RGB',(200,60)),ko),(Image.new('RGB',(200,60)),en)],{}))
    def check(directory,model):
        if model==retry.ENGLISH_MODEL:raise FileNotFoundError('not installed')
        return {}
    monkeypatch.setattr(retry,'verify_model',check)
    def read(crops,*args):
        assert len(crops)==1 and crops[0]['language']=='ko'
        return {'outputs':[{'id':crops[0]['id'],'text':'재인식제목','confidence':.95}]}
    new,summary=retry.refine(source,rows,[criterion()],{},tmp_path/'retry',read=read)
    assert new[0]['text']=='재인식제목' and new[0]['requires_review']
    assert summary['skipped']==[{'kind':'arc','status':'missing_model','model':retry.ENGLISH_MODEL}]


def test_already_matching_conditions_do_not_invoke_crop_reader(tmp_path):
    source=tmp_path/'image.png';Image.new('RGB',(300,200)).save(source)
    rows=[{**record('행사명문구'), 'confidence':.99}]
    def unexpected(*args):pytest.fail('matched text must not launch another model')
    new,summary=retry.refine(source,rows,[criterion('행사명문구')],{},tmp_path/'retry',read=unexpected,check_models=False)
    assert new==rows and summary['status']=='not_needed'


def test_api_calls_refiner_with_original_coordinates_and_keeps_pending_candidate(tmp_path):
    import io
    original=Image.new('RGB',(100,100));out=io.BytesIO();original.save(out,'PNG')
    def recognize(path):
        return {'engine':'paddleocr','pipeline':'paddle_full_v1','status':'success','device':'cpu',
                'execution_target':'local','input_dimensions':[200,200],
                'records':[record(box=[20,40,180,100])]}
    def refine(path,rows,criteria,source,directory):
        assert rows[0]['bbox_xyxy']==[10,20,90,50]
        assert Image.open(path).size==(100,100)
        newer={**rows[0],'text':'새제목','baseline_text':rows[0]['text'],'baseline_confidence':.71,
               'baseline_recognition_engine':'paddleocr','baseline_requires_review':False,
               'requires_review':True,'selection_reason':'new_model_candidate_unverified'}
        return [newer],{'status':'success','version':'conditional_paddle_v1','attempts':[{'text':'새제목'}]}
    with TestClient(api.create_app(review_root=tmp_path/'reviews',recognizer=recognize,paddle_refiner=refine)) as client:
        response=client.post('/api/reviews',data={'criteria':json.dumps([criterion('새제목')]),'criteria_version':1,'project_name':'재인식 검증'},
            files={'image':('test.png',out.getvalue(),'image/png')})
        result=client.get('/api/reviews/'+response.json()['id']).json()
    assert result['status']=='completed' and result['ocr']['conditional_retry']['status']=='success'
    assert result['ocr']['records'][0]['bbox_xyxy']==[10,20,90,50]
    assert result['results'][0]['status']=='review'


def test_year_and_title_must_be_neighboring_and_pending_text_stays_review():
    c=criterion('2026 수원 재즈 페스티벌')
    rows=[record('2026','year',[110,20,200,75]),record('수원재즈페스티벌','name',[20,80,280,150])]
    for row in rows:row.update(confidence=.99,requires_review=True)
    result=compare([c],review_criteria([c],rows,multiline=True),rows,{})[0]
    assert result['status']=='review' and {e['ocr_id'] for e in result['evidence']}=={'year','name'}
    assert '재인식' in result['reason']
    rows[0]['bbox_xyxy']=[350,20,440,75]
    result=compare([c],review_criteria([c],rows,multiline=True),rows,{})[0]
    assert not result['evidence']
