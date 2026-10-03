import pytest

from growthcrew.api.auth import authorize
from growthcrew.api.main import app


@pytest.fixture(autouse=True)
def signed_in():
    """Most tests are about behaviour behind the login, so they skip the auth check.

    test_web_api.py removes this override to test auth itself.
    """
    app.dependency_overrides[authorize] = lambda: None
    yield
    app.dependency_overrides.pop(authorize, None)
