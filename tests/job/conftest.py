import pytest


@pytest.fixture
def global_session():
    """
    Sets up an activemodel global session for job tests.
    This ensures all ORM operations within the test use the same session.
    """
    from activemodel.session_manager import global_session as activemodel_global_session

    with activemodel_global_session() as session:
        assert session
        yield session
