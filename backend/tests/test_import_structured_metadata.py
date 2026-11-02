"""Explicit JSONB mapping without turning imported model predictions into approval."""
from copy import deepcopy
import pytest
from backend.app.document_conditions import render_condition
from backend.app.management.imports import Imports, structured_criterion, projection_fields, PROFILE
from backend.app.management.io import OpsError


def criterion():
    condition=dict(label='행사명',requirement='테스트 축제',level='필수',inspection='원문과 비교',
                   sources=[dict(source_id='p1-s1',page=1,source='행사명: 테스트 축제')])
    return dict(id=1,text=render_condition(condition),type='information',document_condition=condition)


def test_matching_pdf_condition_promotes_expected_value_and_page_without_mutation():
    item=criterion();before=deepcopy(item)
    assert structured_criterion(item)==dict(expected_value='테스트 축제',is_required=True,
                                           source_locator={'sources':item['document_condition']['sources']})
    assert item==before


@pytest.mark.parametrize('change',('edited_text','invalid_condition'))
def test_stale_or_invalid_pdf_metadata_stays_in_raw_source_only(change):
    item=criterion()
    if change=='edited_text':item['text']='행사명: 사용자가 수정한 제목'
    else:item['document_condition']['sources'][0]['page']=0
    assert structured_criterion(item)==dict(expected_value=None,is_required=None,source_locator=None)


def test_explicit_nested_json_and_null_are_preserved_without_guessing_counts():
    item=criterion()
    item.update(expected_value={'count':2,'objects':['logo','seal']},is_required=False,
                source_locator={'page':3,'bbox_xyxy':[1,2,30,40]})
    assert structured_criterion(item)=={key:item[key] for key in ('expected_value','is_required','source_locator')}
    item.update(expected_value=None,is_required=None,source_locator=None)
    assert structured_criterion(item)==dict(expected_value=None,is_required=None,source_locator=None)


@pytest.mark.parametrize('key,value',[('is_required','필수'),('is_required',1),('source_locator',[1,2])])
def test_invalid_explicit_metadata_is_not_silently_coerced(key,value):
    item=criterion();item[key]=value
    with pytest.raises(OpsError,match='SOURCE_CRITERIA_METADATA_UNSUPPORTED'):structured_criterion(item)


def test_frozen_v1_projection_shape_remains_supported_and_unknown_profiles_fail():
    assert 'expected_value' not in projection_fields('conservative-source-v1')
    assert 'expected_value' in projection_fields(PROFILE)
    with pytest.raises(OpsError,match='IMPORT_PROFILE_UNSUPPORTED'):projection_fields('future-unrecognized')


def test_old_frozen_plan_keeps_its_original_projection_even_with_pdf_metadata():
    import json
    item=criterion()
    files={'snapshot.json':json.dumps({'criteria':[item]}).encode(),'result.json':b'{"results":[]}'}
    inventory=[{'relative_path':name} for name in files]
    project=Imports(None,None)
    def read(name,limit):return files[name]
    old=project.projection(read,inventory,{'version':'conservative-source-v1','assignment':{'existing_image_id':'bound-image'}})
    new=project.projection(read,inventory,{'version':PROFILE,'assignment':{'existing_image_id':'bound-image'}})
    assert old['state']==new['state']=='ready'
    assert 'expected_value' not in old['items'][0]
    assert new['items'][0]['expected_value']=='테스트 축제'
    assert old['items'][0]['source_item_jsonb']==new['items'][0]['source_item_jsonb']==item
