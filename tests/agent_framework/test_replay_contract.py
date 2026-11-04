from backend.agent_framework.contracts import Contract
from tests.agent_framework.replay_contract import current_contract_copy


def test_test_replay_preserves_historical_payload_and_nonversion_content():
    recorded={'contract_version':'2.0.0','source':{'quote':'원문 그대로'},
              'criteria':[{'contract_version':'2.0.0','expected':'2.0.0'}]}
    replay=current_contract_copy(recorded)
    assert replay['contract_version']==Contract.model_fields['contract_version'].default
    assert replay['criteria'][0]['contract_version']==replay['contract_version']
    assert replay['criteria'][0]['expected']=='2.0.0' and replay['source']==recorded['source']
    assert recorded['contract_version']=='2.0.0' and recorded['criteria'][0]['contract_version']=='2.0.0'
