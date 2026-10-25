"""Unit tests run against their own OVO home, created from scratch in test-results/unit_tests.

OVO_HOME has to be set before anything imports ovo, so this runs at import time rather than in a
fixture, the same way the OVO repository's own unit tests do it.
"""

import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# the ovo CLI of the interpreter running the tests, which is not necessarily on PATH
OVO_CLI = os.path.join(os.path.dirname(sys.executable), "ovo")
TEST_HOME_DIR = os.path.join(REPO_ROOT, "test-results", "unit_tests")
os.environ["USER"] = "test_user"
os.environ["OVO_HOME"] = ""
os.environ["OVO_UNIT_TEST"] = "1"

if os.path.exists(TEST_HOME_DIR):
    shutil.rmtree(TEST_HOME_DIR)

subprocess.run([OVO_CLI, "init", "home", TEST_HOME_DIR, "-y", "--no-env"], check=True)
assert os.path.exists(os.path.join(TEST_HOME_DIR, "config.yml")), "OVO init home failed"
os.environ["OVO_HOME"] = TEST_HOME_DIR
os.environ["NO_VERIFY_SSL"] = "1"

import pytest  # noqa: E402  (after OVO_HOME is set)


@pytest.fixture(scope="session")
def project_data():
    """Create one project, round and pool in the test home for the whole run."""
    from ovo.core.utils.tests import create_test_project_data

    return create_test_project_data(prefix="Chaperone unit")
