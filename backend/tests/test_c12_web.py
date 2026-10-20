"""Offline boundary checks; real HTTP/browser and Linux proxy have separate suites."""
from pathlib import Path
import json
import pytest
from starlette.responses import Response
from backend.app.config import ConfigurationError
from backend.app.v1_config import load_v1_settings
from backend.app.web import prepare_build, headers, PAGE
from backend.tests.auth_fixtures import auth_environment
from scripts.run_v1 import launch_options


def build(tmp_path):
    root=tmp_path/'web';root.mkdir();(root/'assets').mkdir()
    (root/'cc-frontend.json').write_text(json.dumps({'contract':'cc-web-v1','profile':'v1'}))
    (root/'index.html').write_text('<div id="root"></div><script src="/assets/index-12345678.js"></script>')
    (root/'assets/index-12345678.js').write_text('/* synthetic build */')
    return root


def test_explicit_build_marker_and_assets_required(tmp_path):
    root=build(tmp_path);assert prepare_build(root)==root
    (root/'cc-frontend.json').write_text('{"profile":"legacy"}')
    with pytest.raises(ConfigurationError):prepare_build(root)


@pytest.mark.parametrize('corrupt',('missing_index','missing_asset','symlink_asset','asset_directory','wrong_marker','traversal_reference'))
def test_incomplete_or_linked_build_refused(tmp_path,corrupt):
    root=build(tmp_path)
    if corrupt=='missing_index':(root/'index.html').unlink()
    if corrupt=='missing_asset':(root/'assets/index-12345678.js').unlink()
    if corrupt=='symlink_asset':
        try:(root/'assets/private-12345678.js').symlink_to(root/'index.html')
        except OSError:pytest.skip('This OS/user cannot create a symbolic link; POSIX verification is separate.')
    if corrupt=='asset_directory':(root/'assets/private').mkdir()
    if corrupt=='wrong_marker':(root/'cc-frontend.json').write_text('{}')
    if corrupt=='traversal_reference':(root/'index.html').write_text('<div id="root"></div><script src="/assets/../private.js"></script>')
    with pytest.raises(ConfigurationError):prepare_build(root)


@pytest.mark.parametrize('path',('/api/v1/unknown','/.env','/backend/app/main.py','/storage/private','/assets/missing.js','/projects/old/reviews/bad'))
def test_html_fallback_only_for_known_uuid_pages(path):assert PAGE.fullmatch(path) is None


@pytest.mark.parametrize('path',('/', '/login','/projects','/review/new','/projects/10000000-0000-4000-8000-000000000001/reviews/10000000-0000-4000-8000-000000000002'))
def test_known_pages_are_refreshable(path):assert PAGE.fullmatch(path)


@pytest.mark.parametrize('trusted',('', '*', '127.0.0.0/8', 'localhost', '127.0.0.1,'))
def test_production_launcher_requires_exact_proxy_ip(trusted):
    settings=load_v1_settings(auth_environment(APP_ENV='production',LOCAL_ADMIN_LOGIN='0',AUTH_ALLOWED_ORIGINS='https://checker.example',AUTH_ALLOWED_HOSTS='checker.example'))
    with pytest.raises(ConfigurationError):launch_options(settings,{'TRUSTED_PROXY_IPS':trusted},'0.0.0.0',8000)


def test_development_never_enables_proxy_trust_or_public_bind():
    settings=load_v1_settings(auth_environment())
    with pytest.raises(ConfigurationError):launch_options(settings,{},'0.0.0.0',8000)
    with pytest.raises(ConfigurationError):launch_options(settings,{'TRUSTED_PROXY_IPS':'127.0.0.1'},'127.0.0.1',8000)
    assert launch_options(settings,{},'127.0.0.1',8000)['proxy_headers'] is False


def test_security_headers_do_not_claim_https_in_development():
    response=Response();headers(response,secure=False);assert response.headers['x-content-type-options']=='nosniff'
    assert 'strict-transport-security' not in response.headers
    headers(response,secure=True);assert response.headers['strict-transport-security']=='max-age=31536000'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


def test_real_multiline_date_ranges_are_json_without_losing_sources():
    from backend.app.execution.pipeline import PaddlePipeline
    from backend.app.execution.output import raw_json
    results,event=PaddlePipeline().check([dict(id='date',text='행사 날짜: 2026.10.24 ~ 2026.10.25',type='information')],
        [dict(id='date-line',text='2026.10.24(토) ~ 2026.10.25(일)',confidence=.99,bbox_xyxy=[0,0,400,40])])
    assert any(group.get('values') for group in event['groups'])
    raw_json(event);raw_json(results)
    assert event['fields']['start_date']['source_ocr_ids']==['date-line']
