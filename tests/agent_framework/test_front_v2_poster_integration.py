"""Real front-v2, API, PostgreSQL and CLI worker. Only model HTTP replies are scripted."""
import base64
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
import socket
import subprocess
import sys
from threading import Thread
import time

from PIL import Image
import pytest
import uvicorn

from backend.agent_framework.composition import load_config
from backend.agent_framework.postgres_api import create_postgres_app
from backend.app.storage.local import LocalStorage
from tests.app.auth_fixtures import auth_environment
from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_database, runtime, pdf
from tests.agent_framework.test_semantic_postgres_integration import document_database
from tests.agent_framework.test_a02_semantic import requirement

pytestmark = [pytest.mark.db_integration, pytest.mark.skipif(not os.getenv('FRAMEWORK_BROWSER_NODE'),
    reason='Set FRAMEWORK_BROWSER_NODE to a Node executable with frontend dependencies and a Playwright browser installed.')]


class ModelReplies(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        schema = body.get('response_format', {}).get('json_schema', {}).get('name')
        if schema == 'poster_semantic_requirements':
            title = requirement(1, 'event_name', 'SPRING', presentation={'label':'행사명','value':'SPRING'})
            pending = requirement(2, 'opening_hours', '', unresolved=True, operator='manual', presentation=None)
            for i, item in enumerate((title, pending), 1):
                item['source'] = {'start_line': f'p1-text:L{i}', 'end_line': f'p1-text:L{i}'}
            result = {'requirements': [title, pending]}
        elif schema == 'PosterInspection':
            prompt, image = body['messages'][-1]['content']
            png = base64.b64decode(image['image_url']['url'].split(',', 1)[1])
            with Image.open(BytesIO(png)) as poster:
                corrected = poster.convert('RGB').getpixel((0, 0))[1] > 128
            criterion, = json.loads(prompt['text'].split('CRITERIA_JSON:\n')[1])
            result = {'observations': [{'criterion_id': criterion['criterion_id'], 'observation_status': 'observed',
                'observed': 'SPRING' if corrected else 'WINTER', 'observed_unit': None,
                'verdict': 'pass' if corrected else 'fail', 'bbox': [0, 0, 1, 1], 'uncertainty': None}]}
        else:
            self.send_error(503)  # A07 still produces the full deterministic report.
            return
        self.server.calls.append(schema)
        raw = json.dumps({'model': body['model'], 'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(result, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_browser_approves_reviews_revises_and_finalizes(runtime, tmp_path):
    r = runtime
    root = tmp_path / 'posters'
    LocalStorage.initialize(root)
    config = load_config('deploy/qwen-a02/worker-config.poster.example.json')
    with ThreadingHTTPServer(('127.0.0.1', 0), ModelReplies) as model, socket.socket() as listener:
        model.calls = []
        Thread(target=model.serve_forever, daemon=True).start()
        endpoint = f'http://127.0.0.1:{model.server_port}/v1'
        config = config.model_copy(update={'pdf_extraction': config.pdf_extraction.model_copy(
            update={'endpoint': endpoint + '/chat/completions'})})
        settings = replace(r.settings, hosts=('localhost', '127.0.0.1'), origins=('http://127.0.0.1:5189',))
        api = create_postgres_app(settings, config, engine=r.db.engine, pdf_storage_root=r.root, poster_storage_root=root)
        listener.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(api, log_level='error', access_log=False))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        configfile = tmp_path / 'poster.json'
        configfile.write_text(config.model_dump_json())
        envfile = tmp_path / 'worker.env'
        envfile.write_text(''.join(f'{k}={v}\n' for k, v in auth_environment(r.db, CC_QWEN_BASE_URL=endpoint).items()))
        envfile.chmod(0o600)
        worker = subprocess.Popen([sys.executable, '-m', 'backend.agent_framework', 'worker', '--config', str(configfile),
            '--env-file', str(envfile), '--pdf-storage-root', str(r.root), '--poster-storage-root', str(root)])
        try:
            for _ in range(100):
                if server.started: break
                time.sleep(.1)
            assert server.started
            subprocess.run([os.environ['FRAMEWORK_BROWSER_NODE'], 'tests/e2e/framework-poster.mjs',
                str(listener.getsockname()[1]), os.getenv('FRAMEWORK_BROWSER_CHANNEL', 'chrome'),
                base64.b64encode(pdf('Poster title: SPRING.\nOpening hours: pending confirmation.')).decode()],
                cwd='frontend', check=True, timeout=110)
            assert model.calls == ['poster_semantic_requirements', 'PosterInspection', 'PosterInspection']
        finally:
            worker.terminate()
            try: worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
            server.should_exit = True
            thread.join(timeout=15)
            model.shutdown()
