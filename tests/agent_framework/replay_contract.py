"""Explicit test-only replay of v2 content under the current contract.

Historical recordings stay untouched. This changes only version tags in a copy;
it is not a production reader, stored-run migration or new model evidence.
"""
from backend.agent_framework.contracts import Contract


def current_contract_copy(value):
    if isinstance(value,dict):
        return {key:Contract.model_fields['contract_version'].default
                if key=='contract_version' and item=='2.0.0' else current_contract_copy(item)
                for key,item in value.items()}
    if isinstance(value,list):return [current_contract_copy(item) for item in value]
    return value
