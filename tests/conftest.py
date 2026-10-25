import pytest


@pytest.fixture(scope="session")
def project_data():
    """Create one project, project_round, and pool for the entire test run."""
    # imported inside the fixture: pytest loads this conftest before the one of a test
    # subdirectory, and tests/unit_tests/conftest.py has to set OVO_HOME before anything imports ovo
    from ovo.core.utils.tests import create_test_project_data

    return create_test_project_data()
