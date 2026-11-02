"""Neo4j is a rebuildable, project-scoped relationship projection."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from neo4j import GraphDatabase


class GraphUnavailable(RuntimeError):
    def __init__(self, message: str = "관계 저장소를 사용할 수 없습니다."):
        super().__init__(message)


class GraphStore:
    def __init__(self, settings):
        self.settings = settings
        self.driver = None
        if settings.neo4j_uri:
            self.driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                connection_timeout=8,
                max_transaction_retry_time=10,
                max_connection_pool_size=4,
            )

    def close(self):
        if self.driver:
            self.driver.close()

    def _driver(self):
        if not self.driver:
            raise GraphUnavailable("Neo4j 연결이 설정되지 않았습니다.")
        return self.driver

    def initialize(self):
        with self._driver().session(database="neo4j") as session:
            session.run(
                "CREATE CONSTRAINT cc_entity_identity IF NOT EXISTS "
                "FOR (n:CCEntity) REQUIRE (n.project_id, n.entity_id) IS UNIQUE"
            ).consume()
            session.run(
                "CREATE CONSTRAINT cc_graph_identity IF NOT EXISTS "
                "FOR (n:CCProjection) REQUIRE n.project_id IS UNIQUE"
            ).consume()

    def replace_project(self, project_id: str, nodes: list[dict], edges: list[dict], revision: int):
        """Apply one authoritative snapshot atomically; replay is harmless.

        PostgreSQL outbox delivery serializes project snapshots. Revision also
        prevents a delayed delivery from replacing newer graph information.
        """
        identifiers = {n["id"] for n in nodes}
        if len(identifiers) != len(nodes):
            raise ValueError("Duplicate graph entity identifiers")
        if any(e["source"] not in identifiers or e["target"] not in identifiers for e in edges):
            raise ValueError("Graph relationship refers to an absent entity")
        prepared = []
        for node in nodes:
            properties = {k: v for k, v in node.items() if k != "id" and isinstance(v, (str, int, float, bool))}
            prepared.append({"id": node["id"], "properties": properties})

        def write(tx):
            watermark = tx.run(
                "MERGE (p:CCProjection {project_id:$pid}) "
                "ON CREATE SET p.revision=-1 "
                "SET p.lock_revision=p.revision RETURN p.revision AS revision",
                pid=project_id,
            ).single()
            if watermark["revision"] >= revision:
                return False
            tx.run("MATCH (n:CCEntity {project_id:$pid}) DETACH DELETE n", pid=project_id).consume()
            tx.run(
                "UNWIND $nodes AS row CREATE (n:CCEntity {project_id:$pid,entity_id:row.id}) "
                "SET n += row.properties",
                pid=project_id, nodes=prepared,
            ).consume()
            tx.run(
                "UNWIND $edges AS edge "
                "MATCH (a:CCEntity {project_id:$pid,entity_id:edge.source}) "
                "MATCH (b:CCEntity {project_id:$pid,entity_id:edge.target}) "
                "CREATE (a)-[:CC_RELATION {kind:edge.kind}]->(b)",
                pid=project_id, edges=edges,
            ).consume()
            tx.run(
                "MATCH (p:CCProjection {project_id:$pid}) "
                "SET p.revision=$revision,p.synced_at=$at",
                pid=project_id, revision=revision, at=datetime.now(UTC).isoformat(),
            ).consume()
            return True

        with self._driver().session(database="neo4j") as session:
            return session.execute_write(write)

    def delete_project(self, project_id: str):
        def delete(tx):
            tx.run("MATCH (n:CCEntity {project_id:$pid}) DETACH DELETE n", pid=project_id).consume()
            tx.run("MATCH (n:CCProjection {project_id:$pid}) DELETE n", pid=project_id).consume()
        with self._driver().session(database="neo4j") as session:
            session.execute_write(delete)

    def project_graph(self, project_id: str) -> dict[str, Any]:
        def read(tx):
            status = tx.run(
                "MATCH (p:CCProjection {project_id:$pid}) RETURN p.synced_at AS synced_at",
                pid=project_id,
            ).single()
            if status is None:
                return {"status": "pending", "synced_at": None, "nodes": [], "edges": []}
            rows = list(tx.run(
                "MATCH (n:CCEntity {project_id:$pid}) RETURN properties(n) AS node "
                "ORDER BY n.kind,n.entity_id LIMIT 801", pid=project_id,
            ))
            truncated = len(rows) > 800
            nodes = [{"id": r["node"]["entity_id"], **{k: v for k, v in r["node"].items()
                      if k not in {"project_id", "entity_id"}}} for r in rows[:800]]
            ids = [n["id"] for n in nodes]
            edges = [dict(row) for row in tx.run(
                "MATCH (a:CCEntity {project_id:$pid})-[r:CC_RELATION]->(b:CCEntity {project_id:$pid}) "
                "WHERE a.entity_id IN $ids AND b.entity_id IN $ids "
                "RETURN a.entity_id AS source,b.entity_id AS target,r.kind AS kind LIMIT 4001",
                pid=project_id, ids=ids,
            )]
            return {"status": "synced", "synced_at": status["synced_at"], "nodes": nodes,
                    "edges": edges[:4000], "truncated": truncated or len(edges) > 4000}
        try:
            with self._driver().session(database="neo4j") as session:
                return session.execute_read(read)
        except Exception as exc:
            if isinstance(exc, GraphUnavailable):
                raise
            raise GraphUnavailable() from None

    def impact(self, project_id: str, criterion_id: str) -> dict:
        """Find runs and approvals that used the specified criterion version(s)."""
        def read(tx):
            rows = tx.run(
                "MATCH (c:CCEntity {project_id:$pid,kind:'criterion',criterion_id:$cid}) "
                "MATCH (cs:CCEntity {project_id:$pid,kind:'criteria'})-[:CC_RELATION {kind:'contains'}]->(c) "
                "MATCH (r:CCEntity {project_id:$pid,kind:'run'})-[:CC_RELATION {kind:'applies'}]->(cs) "
                "OPTIONAL MATCH (r)-[:CC_RELATION {kind:'inspects'}]->(v:CCEntity {project_id:$pid}) "
                "OPTIONAL MATCH (a:CCEntity {project_id:$pid,kind:'approval'})-[:CC_RELATION {kind:'approves'}]->(r) "
                "RETURN DISTINCT r.entity_id AS run_id,v.entity_id AS asset_version_id, "
                "cs.entity_id AS criteria_version_id,collect(DISTINCT a.entity_id) AS approval_ids LIMIT 501",
                pid=project_id, cid=criterion_id,
            )
            items = [dict(row) for row in rows]
            return {"criterion_id": criterion_id, "items": items[:500], "truncated": len(items) > 500}
        try:
            with self._driver().session(database="neo4j") as session:
                return session.execute_read(read)
        except Exception:
            raise GraphUnavailable() from None
