"""Explicit synthetic OCR adapter. Never selected by production environment/config."""
from copy import deepcopy
from PIL import Image
from backend.app.review_rules import review_criteria
from backend.app.extract_event import extract_event


class SyntheticPipeline:
    def __init__(self):self.calls=0;self.mode='normal';self.revision='synthetic-only-v1'
    def describe(self):
        return dict(pipeline='synthetic-only',model_name='NO_REAL_MODEL',model_revision=self.revision,device_policy=['cpu'],
            execution_target='local',code_revision='synthetic-test-code',preprocessing={'version':'cc-display-2x-lanczos-v1','ocr_scale':2})
    def recognize(self,path,directory):
        self.calls+=1
        with Image.open(path) as image:width,height=image.size
        records=[dict(id=1,text='예시 축제',confidence=.95,bbox_xyxy=[0,0,width,height],recognition_engine='paddleocr')]
        if self.mode=='empty':records=[]
        if self.mode=='duplicate':records*=2
        if self.mode=='bad_box':records[0]['bbox_xyxy']=[0,0,width+1,height]
        if self.mode=='null_confidence':records[0]['confidence']=None
        if self.mode=='exception':raise RuntimeError('synthetic-private-url-and-token')
        return dict(status='success',engine='paddleocr',pipeline='synthetic-only',device='cpu',execution_target='local',input_dimensions=[width,height],records=records)
    def check(self,criteria,records):
        results=review_criteria(criteria,records)
        if self.mode=='missing_item':results=[]
        if self.mode=='wrong_item':results[0]['criterion_id']='wrong'
        if self.mode=='unknown_evidence':results[0]['evidence']=[{'ocr_id':'invented'}]
        if self.mode=='unsupported':results[0].update(processing_status='unsupported',status='pass',reason='지원되지 않은 검사')
        if self.mode=='structured':results[0]['observed_value']={'observed':'structure'}
        return results,extract_event(records)
