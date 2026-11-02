"""Explicit database initialization, operator-issued accounts, project export."""

import argparse
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import sys
from uuid import UUID
import zipfile

from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from .auth import normalize_email, password_hash, public_user
from .database import Base, create_engine, session_factory
from . import models as M
from .services import iso, record_event
from .settings import get_settings
from .storage import create_storage
from .tool_records import ToolCall


def _row(record) -> dict:
    result = {}
    for prop in inspect(type(record)).column_attrs:
        value = getattr(record, prop.key)
        result[prop.columns[0].name] = iso(value) if isinstance(value, datetime) else value
    return result


def export_project(db, storage, project_id: str, output: Path) -> None:
    project = db.get(M.Project, str(UUID(str(project_id))))
    if project is None:
        raise ValueError("Project not found")
    snapshot = {"format": "creative-checker-project-v1", "exported_at": iso(M.utcnow()), "project": _row(project)}
    for model in (M.Membership, M.FileRecord, M.Document, M.CriterionSet, M.Asset, M.AssetVersion,
                  M.Logo, M.Run, M.ChangeRequest, M.Approval, M.Event, M.Job, M.Outbox, ToolCall):
        snapshot[model.__tablename__] = [_row(item) for item in db.scalars(select(model).where(model.project_id == project.id))]
    member_ids = [row["user_id"] for row in snapshot[M.Membership.__tablename__]]
    snapshot["users"] = [public_user(user) for user in db.scalars(select(M.User).where(M.User.id.in_(member_ids)))]
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves an existing backup even when export fails.
    with output.open("xb") as stream:
        try:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False))
                for file in snapshot[M.FileRecord.__tablename__]:
                    data = storage.read(file["storage_key"])
                    import hashlib
                    if len(data) != file["size"] or hashlib.sha256(data).hexdigest() != file["sha256"]:
                        raise ValueError("A registered file failed its size/checksum comparison")
                    archive.writestr(f"files/{file['id']}", data)
        except BaseException:
            stream.close()
            output.unlink(missing_ok=True)
            raise


def initialize_database(engine, settings) -> None:
    # Importing ToolCall above registers the receipt table before schema creation.
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        from langgraph.checkpoint.postgres import PostgresSaver
        from .worker import checkpoint_dsn

        with PostgresSaver.from_conn_string(checkpoint_dsn(settings.database_url)) as saver:
            saver.setup()
    if settings.storage_backend == "local":
        create_storage(settings)


def request_graph_rebuild(db, project_id: str | None) -> int:
    query = select(M.Project).order_by(M.Project.id).with_for_update()
    if project_id is not None:
        query = query.where(M.Project.id == str(UUID(str(project_id))))
    projects = db.scalars(query).all()
    if project_id is not None and not projects:
        raise ValueError("Project not found")
    for project in projects:
        record_event(db, project, "graph.rebuild_requested", None, {"project_id": project.id})
    return len(projects)


def purge_project(db, storage, graph, project_id: str, confirmation: str) -> None:
    project_id = str(UUID(str(project_id)))
    if confirmation != project_id:
        raise ValueError("--confirm must exactly match --project-id")
    project = db.scalar(select(M.Project).where(M.Project.id == project_id).with_for_update())
    if project is None:
        raise ValueError("Project not found")
    if not project.archived:
        raise ValueError("Archive the project before purging it")
    active = db.scalar(select(func.count()).select_from(M.Job).where(
        M.Job.project_id == project_id, M.Job.state.in_(["running", "cancel_requested"]),
    ))
    if active:
        raise ValueError("Stop or finish active project jobs before purging")
    files = db.scalars(select(M.FileRecord).where(M.FileRecord.project_id == project_id)).all()
    if any(not item.storage_key.startswith(project_id + "/") for item in files):
        raise ValueError("A file is linked outside this project's storage prefix")
    # All paths/keys are checked before the first deletion. External deletion is
    # idempotent; a storage/Neo4j failure is reported and can be retried explicitly.
    graph.delete_project(project_id)
    for file in files:
        storage.delete(file.storage_key)
    if db.bind.dialect.name == "postgresql":
        for statement in (
            "DELETE FROM checkpoint_writes WHERE thread_id LIKE :prefix",
            "DELETE FROM checkpoint_blobs WHERE thread_id LIKE :prefix",
            "DELETE FROM checkpoints WHERE thread_id LIKE :prefix",
        ):
            db.execute(text(statement), {"prefix": project_id + ":%"})
    # Break the current-version and change-request cycles before FK-ordered deletes.
    project.current_document_id = project.current_criteria_id = None
    db.flush()
    db.execute(update(M.AssetVersion).where(M.AssetVersion.project_id == project_id).values(change_request_id=None))
    for model in (ToolCall, M.Job, M.Outbox, M.Event, M.Approval, M.ChangeRequest, M.Run,
                  M.AssetVersion, M.Asset, M.Logo, M.CriterionSet, M.Document, M.FileRecord, M.Membership):
        db.execute(delete(model).where(model.project_id == project_id))
    db.execute(delete(M.Project).where(M.Project.id == project_id))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="creative-checker")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Explicitly create the rebuilt PostgreSQL schema")
    commands.add_parser("graph-init", help="Explicitly initialize the Neo4j projection constraints")
    graph_parser = commands.add_parser("graph-rebuild", help="Queue authoritative project snapshots through the outbox")
    graph_scope = graph_parser.add_mutually_exclusive_group(required=True)
    graph_scope.add_argument("--project-id")
    graph_scope.add_argument("--all", action="store_true")
    user_parser = commands.add_parser("create-user", help="Issue a user account; prompts for its password")
    user_parser.add_argument("--email", required=True)
    user_parser.add_argument("--name", required=True)
    user_parser.add_argument("--admin", action="store_true")
    user_parser.add_argument("--password-env", help="Read the password from this environment variable instead of prompting")
    export_parser = commands.add_parser("export", help="Export one project and its file bytes to a new ZIP")
    export_parser.add_argument("--project-id", required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    purge_parser = commands.add_parser("purge-project", help="Permanently remove one archived project's records, files, checkpoints and graph")
    purge_parser.add_argument("--project-id", required=True)
    purge_parser.add_argument("--confirm", required=True, help="Repeat the exact project UUID")
    args = parser.parse_args(argv)
    engine = None
    try:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        if args.command == "init-db":
            initialize_database(engine, settings)
            print("Application and PostgreSQL checkpoint schemas initialized." if engine.dialect.name == "postgresql"
                  else "Application schema initialized for SQLite tests; checkpoints require PostgreSQL.")
            return 0
        if args.command == "graph-init":
            from .graph import GraphStore

            graph = GraphStore(settings)
            try:
                graph.initialize()
            finally:
                graph.close()
            print("Neo4j projection constraints initialized.")
            return 0
        with session_factory(engine)() as db:
            if args.command == "create-user":
                name = args.name.strip()
                if not name or len(name) > 200:
                    raise ValueError("Name must contain between 1 and 200 characters")
                if args.password_env:
                    password = os.environ.get(args.password_env)
                    if not password:
                        raise ValueError("The password environment variable is empty")
                else:
                    password = getpass.getpass("Password (12–128 characters): ")
                    if password != getpass.getpass("Confirm password: "):
                        raise ValueError("Passwords do not match")
                user = M.User(email=normalize_email(args.email), name=name,
                              password_hash=password_hash(password), is_admin=args.admin)
                db.add(user)
                db.commit()
                print(json.dumps(public_user(user), ensure_ascii=False))
            elif args.command == "graph-rebuild":
                count = request_graph_rebuild(db, args.project_id)
                db.commit()
                print(f"Queued graph rebuilds for {count} project(s). Run the projection worker to deliver them.")
            elif args.command == "purge-project":
                from .graph import GraphStore

                graph = GraphStore(settings)
                try:
                    purge_project(db, create_storage(settings), graph, args.project_id, args.confirm)
                    db.commit()
                except Exception:
                    print("Purge did not finish; external deletion may be partial. Resolve the error before retrying this same project.", file=sys.stderr)
                    raise
                finally:
                    graph.close()
                print("Archived project records, files, checkpoints and graph removed.")
            else:
                if engine.dialect.name == "postgresql":
                    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                export_project(db, create_storage(settings), args.project_id, args.output)
                print(f"Project exported to {args.output.resolve()}")
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Database operation failed. Check connection, schema initialization and unique account details.", file=sys.stderr)
        return 1
    except Exception:
        # SDK/driver exceptions can include internal URLs, SQL parameters or headers.
        print("Operation failed. Check required services and the local runtime configuration.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
