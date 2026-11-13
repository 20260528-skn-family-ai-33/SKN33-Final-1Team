from datetime import timedelta
from pathlib import Path
from runpy import run_path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from backend.agent_framework.my_tasks import MyTasks
from backend.agent_framework.postgres import events, runs
from tests.agent_framework.test_criteria_postgres_integration import (
    a02, auth_database, database, document_database, initial_pdf_database, review, runtime,
)
from tests.agent_framework.test_a02_postgres_integration import admit, register


tool = run_path(str(Path(__file__).resolve().parents[2] /
                    'deploy/agent-framework/expire-aws-test-reviews.py'))


def test_rollout_replaces_only_aws_test_rules_for_selected_ports():
    header = tool['MAP_HEADER']
    legacy = '    ~^18016:POST:/api/framework/v1/runs/shared/(?:approvals)/?$ 18016;'
    expired = '    ~^18016:POST:/api/framework/v1/runs/expired/(?:approvals)/?$ 18026;'
    main = '    ~^8018:POST:/api/framework/v1/runs/main/(?:approvals)/?$ 18028;'
    source = '\n'.join((header, legacy, expired, '    ~^18016: 18027;',
                        '    default $cc_a02_backend_port;', main, '    ~^8018: 18018;', '}\n'))
    assert tool['routing_ports'](source) == (18027, {18016, 18026, 18027}, {8018, 18018, 18028})
    result = tool['render_rollout'](source, {18026: [], 18027: ['fresh']}, 18029)
    assert expired not in result
    assert legacy in result and main in result
    assert '/runs/fresh/' in result and '~^18016: 18029;' in result


@pytest.mark.db_integration
def test_expiry_preserves_queue_and_other_runtime_and_filters_rollout(review):
    r = review
    store = r.app.state.framework
    scope = (store.config_hash, store.runtime_hash)
    queued, _ = register(r, 'FES-NEW072')
    reply = admit(r, queued)
    assert reply.status_code == 202, reply.text
    queued_id = reply.json()['context']['run_id']
    fresh_id, other_id = str(uuid4()), str(uuid4())
    with r.owner.begin() as db:
        original = store._row(db, r.rid)
        for run_id, runtime_hash in ((fresh_id, scope[1]), (other_id, '0' * 64)):
            snapshot = original['snapshot_json']
            clone = dict(snapshot, context=dict(snapshot['context'], run_id=run_id))
            db.execute(runs.insert().values(
                **{key: value for key, value in original.items()
                   if key not in ('run_id', 'runtime_sha256', 'snapshot_json', 'updated_at', 'created_at')},
                run_id=run_id, runtime_sha256=runtime_hash, snapshot_json=clone,
            ))
        db.execute(update(runs).where(runs.c.run_id.in_([r.rid, other_id, queued_id]))
                   .values(updated_at=func.clock_timestamp() - timedelta(hours=25)))

    assert tool['expire_reviews'](store, scope) == 1
    assert tool['rollout_ids'](store, scope, preview=True) == sorted([fresh_id, queued_id])
    assert tool['expire_reviews'](store, scope, apply=True) == 1
    assert tool['expire_reviews'](store, scope, apply=True) == 0
    assert tool['rollout_ids'](store, scope) == sorted([fresh_id, queued_id])

    with store.engine.connect() as db:
        saved = store._row(db, r.rid)
        assert saved['input_json'] == original['input_json']
        assert saved['snapshot_json']['criteria'] == original['snapshot_json']['criteria']
        assert saved['snapshot_json']['error']['code'] == 'REVIEW_EXPIRED'
        assert store._row(db, other_id)['snapshot_json']['execution_status'] == 'waiting_criteria'
        assert store._row(db, queued_id)['snapshot_json']['execution_status'] == 'queued'
        kinds = db.execute(select(events.c.kind).where(events.c.run_id == r.rid)).scalars().all()
        assert kinds.count('history.visibility_changed') == 1
    assert r.rid not in {item['run_id'] for item in MyTasks(store).list(r.subject)['items']}
