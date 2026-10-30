"""HTTP tests use a real SQLite test database and a temporary local file store."""

from contextlib import ExitStack
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend.creative_checker.api import create_app
from backend.creative_checker.auth import password_hash
from backend.creative_checker.database import Base, create_engine, session_factory
from backend.creative_checker.models import User
from backend.creative_checker.settings import Settings
from backend.creative_checker.storage import LocalStorage
from backend.creative_checker.tool_records import ToolCall  # Register the receipt FK table.


PASSWORD = "test password with spaces"


@pytest.fixture
def api(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data", testing=True, cookie_secure=False)
    storage = LocalStorage(tmp_path / "files")
    app = create_app(settings, engine=engine, storage=storage)
    users = {}
    encoded = password_hash(PASSWORD)
    with sessions.begin() as db:
        for name in ("owner", "editor", "viewer", "outsider", "admin"):
            user = User(email=f"{name}@example.test", name=name, password_hash=encoded, is_admin=name == "admin")
            db.add(user)
            db.flush()
            users[name] = user.id
    with ExitStack() as stack:
        def client(name=None):
            http = stack.enter_context(TestClient(app))
            if name:
                login = http.post("/api/auth/login", json={"email": f"{name}@example.test", "password": PASSWORD})
                assert login.status_code == 200, login.text
                http.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            return http

        harness = SimpleNamespace(app=app, engine=engine, sessions=sessions, storage=storage,
                                  settings=settings, users=users, client=client, password=PASSWORD, tmp_path=tmp_path)
        harness.owner = client("owner")
        yield harness
    engine.dispose()
