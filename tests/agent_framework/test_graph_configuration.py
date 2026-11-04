"""Opt-in config and version-scoped graph source identities; no external services."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from backend.agent_framework.composition import load_config,TeamConfiguration
from backend.agent_framework.graph_projection import source_rows
from backend.agent_framework.graph_store import FrameworkGraph


def test_existing_configuration_wire_shape_and_hash_are_not_changed_when_disabled():
    value=load_config('examples/agent_framework/team_config.synthetic.json')
    assert 'context_preparation' not in value.model_dump(mode='json')
    assert value.contract_version=='3.0.0'
    invalid=value.model_dump(mode='json')|{'context_preparation':{'mode':'neo4j_rag'}}
    with pytest.raises(ValidationError):TeamConfiguration.model_validate(invalid)
    raw=load_config('examples/agent_framework/team_config.poster-review.template.json').model_dump(mode='json')
    configured=TeamConfiguration.model_validate(raw|{'context_preparation':{'mode':'neo4j_rag'}})
    assert configured.context_preparation.policy=='af-approved-sources-v1'
    assert set(configured.roles)=={'A02','A04_A05','A06','A07'}


def test_criterion_id_reuse_in_a_new_version_has_different_source_identity():
    criteria=[{'criterion_id':'c001','source':{'source_hash':'a'*64},'value_source':{'quote_sha256':'b'*64}}]
    one=source_rows('version-one',criteria);two=source_rows('version-two',criteria)
    assert len(one)==2 and {x['source_key'] for x in one}.isdisjoint(x['source_key'] for x in two)


def test_invalid_projection_is_rejected_before_a_driver_is_created():
    graph=FrameworkGraph('bolt://example.invalid','test','not-used')
    with pytest.raises(ValueError):graph.replace_project('p',[{'key':'same'},{'key':'same'}],[],1)
    with pytest.raises(ValueError):graph.replace_project('p',[{'key':'one'}],[{'source':'one','target':'missing'}],1)
    assert graph._driver is None
