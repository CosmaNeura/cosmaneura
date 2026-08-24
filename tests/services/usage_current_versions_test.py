"""Usage dashboards must count current generated notes, not retained versions."""

from contextlib import contextmanager

from app.services import usage


class _Rows:
    def all(self) -> list:
        return []


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement, _params=None) -> _Rows:
        self.statements.append(str(statement))
        return _Rows()


def test_every_usage_query_excludes_superseded_note_versions(monkeypatch) -> None:
    session = _Session()

    @contextmanager
    def fake_get_session():
        yield session

    monkeypatch.setattr(usage, "get_session", fake_get_session)

    usage.recent_appointments()
    usage.doctor_usage()
    usage.daily_volume()
    usage.pending_uploads()
    usage.time_to_note()

    assert len(session.statements) == 5
    assert all("avn.superseded_at IS NULL" in sql for sql in session.statements)
