"""Fixed, parameterized queries over a disposable AF-only Neo4j projection."""
import os
from datetime import datetime, timezone


class GraphUnavailable(RuntimeError):
    def __init__(self, code='GRAPH_UNAVAILABLE'):
        self.code = code
        super().__init__('The framework relationship projection is unavailable.')


class FrameworkGraph:
    def __init__(self, uri=None, user=None, password=None):
        self.uri = uri if uri is not None else os.environ.get('CC_NEO4J_URI', '')
        self.user = user if user is not None else os.environ.get('CC_NEO4J_USER', 'neo4j')
        self._password = password if password is not None else os.environ.get('CC_NEO4J_PASSWORD', '')
        self._driver = None
        self._schema_ready = False

    def close(self):
        if self._driver is not None:
            self._driver.close()

    def _session(self):
        if not self.uri or not self._password:
            raise GraphUnavailable('GRAPH_NOT_CONFIGURED')
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self._password),
                connection_timeout=5, connection_acquisition_timeout=5,
                max_transaction_retry_time=5, max_connection_pool_size=4)
        return self._driver.session(database='neo4j')

    def initialize(self):
        try:
            with self._session() as session:
                session.run('CREATE CONSTRAINT af_entity_identity IF NOT EXISTS '
                    'FOR (n:AFEntity) REQUIRE (n.project_id,n.entity_key) IS UNIQUE').consume()
                session.run('CREATE CONSTRAINT af_projection_identity IF NOT EXISTS '
                    'FOR (n:AFProjection) REQUIRE n.project_id IS UNIQUE').consume()
            self._schema_ready = True
        except Exception:
            raise GraphUnavailable() from None

    def _check_schema(self):
        if self._schema_ready:return
        with self._session() as session:
            row=session.run('SHOW CONSTRAINTS YIELD name WHERE name IN $names RETURN collect(name) AS names',
                names=['af_entity_identity','af_projection_identity']).single()
        if not row or set(row['names'])!={'af_entity_identity','af_projection_identity'}:
            raise GraphUnavailable('GRAPH_SCHEMA_NOT_READY')
        self._schema_ready=True

    def check(self):
        """Authenticated, read-only schema/connectivity probe. Never initializes."""
        try:
            self._check_schema()
            with self._session() as session:
                row=session.run('RETURN 1 AS ready').single()
                if not row or row['ready']!=1:raise GraphUnavailable()
        except GraphUnavailable:raise
        except Exception:raise GraphUnavailable() from None

    def projection_revisions(self,project_ids):
        """Bounded monitoring query; never enumerates or modifies foreign graphs."""
        if len(project_ids)>500:raise GraphUnavailable('GRAPH_RESULT_LIMIT')
        try:
            from neo4j import Query
            self._check_schema()
            with self._session() as session:
                rows=session.run(Query('UNWIND $ids AS id OPTIONAL MATCH(p:AFProjection {project_id:id}) '
                    'RETURN id AS project_id,p.revision AS revision',timeout=5),ids=project_ids)
                return {r['project_id']:r['revision'] for r in rows}
        except GraphUnavailable:raise
        except Exception:raise GraphUnavailable() from None

    @staticmethod
    def _unmanaged(tx,project_id):
        row=tx.run('MATCH(n:AFEntity {project_id:$pid}) '
            'OPTIONAL MATCH(n)-[r]-(other) '
            'WITH n,r,other WHERE size(labels(n))<>1 OR '
            '(r IS NOT NULL AND (type(r)<>"AF_RELATION" OR NOT other:AFEntity OR '
            'other.project_id IS NULL OR other.project_id<>$pid)) RETURN count(*) AS count',pid=project_id).single()
        return bool(row['count'])

    def read_projection(self,project_id):
        def read(tx):
            root=tx.run('MATCH(p:AFProjection {project_id:$pid}) RETURN p.revision AS revision',pid=project_id).single()
            nodes=list(tx.run('MATCH(n:AFEntity {project_id:$pid}) '
                'RETURN n.entity_key AS key,properties(n) AS properties ORDER BY key LIMIT 10001',pid=project_id))
            edges=list(tx.run('MATCH(a:AFEntity {project_id:$pid})-[r:AF_RELATION]->(b:AFEntity {project_id:$pid}) '
                'RETURN a.entity_key AS source,b.entity_key AS target,r.kind AS kind '
                'ORDER BY source,target,kind LIMIT 20001',pid=project_id))
            if len(nodes)>10000 or len(edges)>20000:raise GraphUnavailable('GRAPH_RESULT_LIMIT')
            return {'revision':root['revision'] if root else None,
                'nodes':[{'key':r['key'],**{k:v for k,v in r['properties'].items() if k not in ('entity_key','project_id')}} for r in nodes],
                'edges':[dict(r) for r in edges],'unmanaged':self._unmanaged(tx,project_id)}
        try:
            self._check_schema()
            with self._session() as session:return session.execute_read(read)
        except GraphUnavailable:raise
        except Exception:raise GraphUnavailable() from None

    def replace_project(self, project_id, nodes, edges, revision):
        keys = {n['key'] for n in nodes}
        if len(keys) != len(nodes) or any(e['source'] not in keys or e['target'] not in keys for e in edges):
            raise ValueError('Invalid projection identities')
        # Only our scalar properties can enter the graph; no raw model text or credentials.
        prepared = [{'key': n['key'], 'properties': {k:v for k,v in n.items()
            if k != 'key' and isinstance(v, (str,int,bool))}} for n in nodes]
        def write(tx):
            row = tx.run('MERGE (p:AFProjection {project_id:$pid}) ON CREATE SET p.revision=-1 '
                'SET p.lock_revision=p.revision RETURN p.revision AS revision', pid=project_id).single()
            if row['revision'] >= revision:
                return False
            if self._unmanaged(tx,project_id):raise GraphUnavailable('GRAPH_UNMANAGED_RELATIONSHIPS')
            tx.run('MATCH (n:AFEntity {project_id:$pid}) DETACH DELETE n',pid=project_id).consume()
            tx.run('UNWIND $nodes AS row CREATE (n:AFEntity {project_id:$pid,entity_key:row.key}) '
                'SET n += row.properties',pid=project_id,nodes=prepared).consume()
            tx.run('UNWIND $edges AS e MATCH (a:AFEntity {project_id:$pid,entity_key:e.source}) '
                'MATCH (b:AFEntity {project_id:$pid,entity_key:e.target}) '
                'CREATE (a)-[:AF_RELATION {kind:e.kind}]->(b)',pid=project_id,edges=edges).consume()
            tx.run('MATCH (p:AFProjection {project_id:$pid}) SET p.revision=$revision,p.synced_at=$at',
                pid=project_id,revision=revision,at=datetime.now(timezone.utc).isoformat()).consume()
            return True
        try:
            self._check_schema()
            with self._session() as session:
                return session.execute_write(write)
        except GraphUnavailable:raise
        except Exception:
            raise GraphUnavailable() from None

    def approved_sources(self, project_id, criteria_version_id):
        def read(tx):
            state = tx.run('MATCH (p:AFProjection {project_id:$pid}) RETURN p.revision AS revision',pid=project_id).single()
            if state is None:
                raise GraphUnavailable('GRAPH_NOT_PROJECTED')
            rows = list(tx.run('MATCH (v:AFEntity {project_id:$pid,entity_key:$version,approved:true}) '
                '-[:AF_RELATION {kind:"contains"}]->(c:AFEntity {project_id:$pid}) '
                '-[:AF_RELATION {kind:"sourced_from"}]->(s:AFEntity {project_id:$pid}) '
                'RETURN c.criterion_id AS criterion_id,s.entity_key AS source_key,s.source_hash AS source_hash '
                'ORDER BY criterion_id,source_key LIMIT 1001',pid=project_id,version='criteria:'+criteria_version_id))
            if len(rows)>1000:
                raise GraphUnavailable('GRAPH_RESULT_LIMIT')
            runs=list(tx.run('MATCH (r:AFEntity {project_id:$pid,kind:"run"}) '
                '-[:AF_RELATION {kind:"applies"}]->(:AFEntity {project_id:$pid,entity_key:$version}) '
                'RETURN r.entity_key AS key ORDER BY key LIMIT 501',pid=project_id,version='criteria:'+criteria_version_id))
            run_keys=[row['key'] for row in runs[:500]]
            approvals=list(tx.run('MATCH (a:AFEntity {project_id:$pid,kind:"approval"}) '
                '-[:AF_RELATION {kind:"approves"}]->(r:AFEntity {project_id:$pid,kind:"run"}) '
                'WHERE r.entity_key IN $keys RETURN DISTINCT a.entity_key AS key ORDER BY key LIMIT 1001',
                pid=project_id,keys=run_keys))
            return {'revision':state['revision'],'sources':[dict(row) for row in rows],
                'relations':{'run_ids':[key.removeprefix('run:') for key in run_keys],
                    'approval_ids':[row['key'].removeprefix('approval:') for row in approvals[:1000]],
                    'truncated':len(runs)>500 or len(approvals)>1000}}
        try:
            self._check_schema()
            with self._session() as session:
                return session.execute_read(read)
        except GraphUnavailable:
            raise
        except Exception:
            raise GraphUnavailable() from None
