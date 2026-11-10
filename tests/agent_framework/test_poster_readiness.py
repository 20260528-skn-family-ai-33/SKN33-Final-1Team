"""Poster configuration readiness, without a database, model or worker connection."""
from contextlib import nullcontext
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.agent_framework import composition, postgres_api
from backend.agent_framework.composition import (
    load_config, poster_review_configuration_blockers, TeamConfiguration,
)
from backend.app.v1_config import load_v1_settings
from tests.app.auth_fixtures import auth_environment

POSTER_CONFIG = 'deploy/qwen-a02/worker-config.poster.example.json'
HEALTH = '/api/framework/v1/health'


@pytest.fixture
def configuration():
    return load_config(POSTER_CONFIG)


@pytest.fixture
def make_app(monkeypatch, configuration):
    def auth_app(*, settings, engine):
        app = FastAPI()
        app.state.engine, app.state.authorization = engine, object()
        return app

    monkeypatch.setattr(postgres_api, 'create_auth_app', auth_app)
    monkeypatch.setattr(postgres_api, 'unit_of_work', lambda _: nullcontext(None))
    monkeypatch.setattr(postgres_api, 'check_app_permissions', lambda _: None)
    monkeypatch.setattr(postgres_api, 'check_document_extension', lambda _: None)
    storage = lambda _: SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(postgres_api, 'open_document_storage', storage)
    monkeypatch.setattr(postgres_api, 'open_poster_storage', storage)
    monkeypatch.setattr(postgres_api.Files, 'recover', lambda *args, **kwargs: None)
    settings = load_v1_settings(auth_environment())

    def create(config=None, *, schema='AF004', documents=True, posters=True):
        monkeypatch.setattr(postgres_api, 'check_extension', lambda _: {'revision': schema})
        return postgres_api.create_postgres_app(settings, config or configuration, engine=object(),
            pdf_storage_root='unused-pdfs' if documents else None,
            poster_storage_root='unused-posters' if posters else None)

    return create


def test_complete_poster_health_preserves_existing_fields_without_importing_factories(make_app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Readiness must not import or initialize configured adapters')

    monkeypatch.setattr(composition, 'importlib', SimpleNamespace(import_module=forbidden))
    with TestClient(make_app()) as client:
        response = client.get(HEALTH)
    assert response.status_code == 200
    assert response.json() == {
        'storage': 'postgres', 'queue': 'agent_framework.jobs', 'schema': 'AF004',
        'pdf_registration': True, 'free_text_registration': True, 'pdf_extraction_mode': 'qwen',
        'requirements_presentation': True, 'context_preparation': 'approved_sources',
        'graph_projection': False, 'criteria_review': True, 'poster_reviews': True,
        'task_controls': True,
        'authentication': 'existing_M008_session_and_CSRF', 'adapters_initialized_in_api': False,
        'production_ready': False, 'poster_review_ready': True, 'poster_review_blockers': [],
    }


@pytest.mark.parametrize('service,code', [
    ('documents', 'DOCUMENT_STORAGE_NOT_READY'),
    ('criteria_review', 'CRITERIA_REVIEW_NOT_READY'),
    ('poster_reviews', 'POSTER_STORAGE_NOT_READY'),
])
def test_health_never_claims_readiness_without_each_storage_service(make_app, service, code):
    app = make_app()
    with TestClient(app) as client:
        setattr(app.state, service, None)
        health = client.get(HEALTH).json()
    assert health['poster_review_ready'] is False
    assert health['poster_review_blockers'] == [code]


def test_no_storage_configuration_starts_but_is_not_poster_ready(make_app):
    with TestClient(make_app(documents=False, posters=False)) as client:
        health = client.get(HEALTH).json()
    assert health['pdf_registration'] is False and health['poster_reviews'] is False
    assert health['poster_review_ready'] is False
    assert health['poster_review_blockers'] == ['DOCUMENT_STORAGE_NOT_READY', 'POSTER_STORAGE_NOT_READY']


def test_pdf_only_configuration_still_starts_on_af002(make_app):
    config = load_config('deploy/qwen-a02/worker-config.example.json')
    with TestClient(make_app(config, schema='AF002', posters=False)) as client:
        health = client.get(HEALTH).json()
    assert health['pdf_registration'] is True and health['pdf_extraction_mode'] == 'qwen'
    assert health['poster_review_ready'] is False
    assert health['poster_review_blockers'] == [
        'CRITERIA_REVIEW_NOT_READY', 'POSTER_STORAGE_NOT_READY', 'A02_QWEN_NOT_CONFIGURED',
        'A04_A05_POSTER_NOT_CONFIGURED', 'A07_REPORTER_NOT_CONFIGURED',
    ]


@pytest.mark.parametrize('role,binding,code', [
    ('A04_A05', {'kind': 'unconfigured'}, 'A04_A05_POSTER_NOT_CONFIGURED'),
    ('A04_A05', {'kind': 'factory', 'factory': 'backend.agent_framework.adapters.team_a04_a05:create_inspector'},
     'A04_A05_POSTER_NOT_CONFIGURED'),
    ('A04_A05', {'kind': 'registered_poster', 'factory': 'examples.agent_framework.poster_adapters:make_inspector'},
     'A04_A05_POSTER_NOT_CONFIGURED'),
    ('A06', {'kind': 'unconfigured'}, 'A06_VALIDATOR_NOT_CONFIGURED'),
    ('A06', {'kind': 'synthetic'}, 'A06_VALIDATOR_NOT_CONFIGURED'),
    ('A06', {'kind': 'factory', 'factory': 'backend.agent_framework.composition:_SyntheticValidator'},
     'A06_VALIDATOR_NOT_CONFIGURED'),
    ('A07', {'kind': 'unconfigured'}, 'A07_REPORTER_NOT_CONFIGURED'),
    ('A07', {'kind': 'synthetic'}, 'A07_REPORTER_NOT_CONFIGURED'),
    ('A07', {'kind': 'factory', 'factory': 'backend.agent_framework.adapters.templates:QwenAdapterStub'},
     'A07_REPORTER_NOT_CONFIGURED'),
    ('A07', {'kind': 'factory', 'factory': 'tests.private_token_endpoint:make_reporter'},
     'A07_REPORTER_NOT_CONFIGURED'),
])
def test_missing_or_stub_roles_return_only_safe_codes(configuration, role, binding, code):
    raw = configuration.model_dump(mode='json')
    raw['roles'][role] = binding
    assert poster_review_configuration_blockers(TeamConfiguration.model_validate(raw)) == [code]


@pytest.mark.parametrize('options', [
    {'mode': 'mock', 'presentation': False, 'endpoint': None},
    {'structuring': 'literal_legacy', 'presentation': False},
    {'presentation': False},
])
def test_a02_requires_real_semantic_presentation(configuration, options):
    raw = configuration.model_dump(mode='json')
    raw['pdf_extraction'].update(options)
    assert poster_review_configuration_blockers(TeamConfiguration.model_validate(raw)) == ['A02_QWEN_NOT_CONFIGURED']


def test_synthetic_pdf_and_roles_are_not_ready():
    config = load_config('examples/agent_framework/team_config.synthetic.json')
    assert poster_review_configuration_blockers(config) == [
        'A02_QWEN_NOT_CONFIGURED', 'A04_A05_POSTER_NOT_CONFIGURED', 'A07_REPORTER_NOT_CONFIGURED',
    ]


def test_existing_mock_template_stays_not_ready():
    config = load_config('examples/agent_framework/team_config.poster-review.template.json')
    assert config.pdf_extraction.mode == 'mock'
    assert poster_review_configuration_blockers(config) == ['A02_QWEN_NOT_CONFIGURED']


def test_custom_validator_factory_is_a_declaration_not_a_connection_check(configuration, monkeypatch):
    raw = configuration.model_dump(mode='json')
    raw['roles']['A06'] = {'kind': 'factory', 'factory': 'private_team.validator:make_validator'}
    def forbidden(*args, **kwargs):
        raise AssertionError('A declared factory must not be imported')
    monkeypatch.setattr(composition, 'importlib', SimpleNamespace(import_module=forbidden))
    assert poster_review_configuration_blockers(TeamConfiguration.model_validate(raw)) == []
