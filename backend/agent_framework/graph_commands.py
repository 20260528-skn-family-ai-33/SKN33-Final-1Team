"""Explicit graph administration/delivery. Never starts or migrates either database."""
import json
from threading import Event
import signal
from uuid import UUID
from sqlalchemy import text

from backend.app.persistence.database import create_app_engine
from backend.app.persistence.transaction import unit_of_work
from backend.app.config import ConfigurationError
from .postgres_commands import _values,_ready
from .graph_store import FrameworkGraph,GraphUnavailable
from .graph_projection import ProjectionWorker,mark_project_changed,installed,graph_status


def graph_command(args):
    values,settings=_values(args.env_file,'framework-projection')
    graph=FrameworkGraph(values.get('CC_NEO4J_URI',''),values.get('CC_NEO4J_USER','neo4j'),values.get('CC_NEO4J_PASSWORD',''))
    engine=None
    try:
        if args.command=='graph-init':
            graph.initialize();print(json.dumps({'graph':'AFProjection','initialized':True}));return 0
        engine=create_app_engine(settings)
        readiness=_ready(engine,settings)
        with unit_of_work(engine) as c:
            if not installed(c):raise ConfigurationError('Apply AF005 explicitly before graph commands.')
            if args.command=='graph-rebuild':
                # One explicit project only. The public record is not modified.
                found=c.execute(text('SELECT project_id FROM public.projects WHERE project_id=:p'),{'p':args.project_id}).scalar_one_or_none()
                if found is None:raise ConfigurationError('Project does not exist.')
                mark_project_changed(c,args.project_id,'graph.rebuild_requested',args.project_id)
                result=graph_status(c,args.project_id)
                print(json.dumps({'project_id':str(args.project_id),'status':result['status'],'required_revision':result['required_revision']}));return 0
        if args.command=='graph-check':
            graph.check()
            print(json.dumps({'postgres':'ready','neo4j':'ready','schema':readiness['revision'],
                'read_only':True}));return 0
        stop=Event()
        for name in (signal.SIGINT,signal.SIGTERM):signal.signal(name,lambda *_:stop.set())
        worker=ProjectionWorker(engine,graph)
        while not stop.is_set():
            delivered=worker.deliver_one()
            if args.once or delivered:print(json.dumps({'event':'framework_projection_cycle','processed':delivered,'outcome':worker.last_outcome}),flush=True)
            if args.once:return 1 if worker.last_outcome in ('deferred','authority_lost') else 0
            stop.wait(1)
        return 0
    except GraphUnavailable as error:
        print(json.dumps({'error':{'code':error.code,'message':'The framework graph operation was not confirmed.'}}));return 1
    finally:
        graph.close()
        if engine is not None:engine.dispose()
