"""Read-only attestation before any test graph schema/data mutation."""
import hashlib
import os
import re

from backend.agent_framework.graph_store import FrameworkGraph

PURPOSE='agent-framework-disposable-tests'


class UnsafeGraphTestTarget(RuntimeError):
    pass


def isolated_graph_settings(environ=None,graph_factory=FrameworkGraph):
    env=os.environ if environ is None else environ
    names=('AF_TEST_NEO4J_URI','AF_TEST_NEO4J_PASSWORD','AF_TEST_NEO4J_RUN_ID','AF_TEST_NEO4J_TOKEN')
    if any(not env.get(k) for k in names):
        raise UnsafeGraphTestTarget('Use scripts/framework_graph_test.py; no development DB fallback.')
    run=env['AF_TEST_NEO4J_RUN_ID']
    if not re.fullmatch(r'cc-af-test-[0-9a-f]{12}',run):
        raise UnsafeGraphTestTarget('Invalid isolated test run identity.')
    graph=graph_factory(env['AF_TEST_NEO4J_URI'],'neo4j',env['AF_TEST_NEO4J_PASSWORD'])
    try:
        with graph._session() as session:
            row=session.run('MATCH(m:AFTestEnvironment {purpose:$purpose,run_id:$run,token_sha256:$token}) '
                'RETURN count(m) AS count',purpose=PURPOSE,run=run,
                token=hashlib.sha256(env['AF_TEST_NEO4J_TOKEN'].encode()).hexdigest()).single()
        if not row or row['count']!=1:raise ValueError()
    except Exception:
        raise UnsafeGraphTestTarget('Refused unattested graph; development graph was not modified.') from None
    finally:graph.close()
    return {'uri':env['AF_TEST_NEO4J_URI'],'password':env['AF_TEST_NEO4J_PASSWORD']}
