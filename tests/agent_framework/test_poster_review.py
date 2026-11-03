"""Poster configuration, code-native fixtures and conservative validation."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.agent_framework.composition import load_config,build_role,TeamConfiguration
from backend.agent_framework.poster_review import build_poster_callback,make_reporter
from backend.agent_framework.ports import AdapterFailure
from examples.agent_framework.poster_testdata import generate


def test_template_never_fakes_a_real_inspection():
    config=load_config('examples/agent_framework/team_config.poster-review.template.json')
    with pytest.raises(AdapterFailure) as error:build_role(config,'A04_A05')
    assert error.value.code=='ADAPTER_CONTEXT_REQUIRED'
    callback=build_poster_callback(config.roles['A04_A05'].factory)
    with pytest.raises(AdapterFailure) as error:callback.inspect_poster(None,None)
    assert error.value.code=='ADAPTER_NOT_CONFIGURED'


def test_generated_variants_have_distinct_actual_bytes_and_independent_sidecar_fields(tmp_path):
    manifest=generate(tmp_path,cases=('EXH-NEW004',));records={r['variant']:r for r in manifest['records']}
    assert len({r['display_sha256'] for r in records.values()})==4
    assert records['normal']['fields']==records['revised']['fields']
    assert records['wrong_date']['fields']['date']!=records['normal']['fields']['date']
    assert records['missing_notice']['fields']['notice']==''
    for row in records.values():
        assert sha256((tmp_path/row['png']).read_bytes()).hexdigest()==row['png_sha256']
        assert sha256((tmp_path/row['svg']).read_bytes()).hexdigest()==row['svg_sha256']


@pytest.mark.parametrize('role',('A02','A06','A07'))
def test_registered_poster_binding_is_only_for_the_combined_inspector(role):
    config=load_config('examples/agent_framework/team_config.poster-review.template.json').model_dump(mode='json')
    config['roles'][role]={'kind':'registered_poster','factory':'examples.agent_framework.poster_adapters:make_inspector'}
    with pytest.raises(ValidationError):TeamConfiguration.model_validate(config)


def test_legacy_json_does_not_gain_a_null_baseline():
    from backend.agent_framework.adapters.synthetic import sample_inputs,make_agents
    from backend.agent_framework.runtime import Workflow
    context,document,asset=sample_inputs();result=Workflow(make_agents()).start(context,document,asset)
    assert 'baseline' not in result.model_dump(mode='json')


def test_registered_poster_profile_cannot_admit_unbound_generic_runs():
    from backend.agent_framework.postgres import PostgresStore
    from backend.agent_framework.adapters.synthetic import sample_inputs
    from backend.app.security.errors import AuthError
    config=load_config('examples/agent_framework/team_config.poster-review.template.json')
    with pytest.raises(AuthError) as error:PostgresStore(None,config).start(None,*sample_inputs())
    assert error.value.code=='USE_POSTER_REVIEW_ENDPOINT'
