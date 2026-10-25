"""Integration tests run against your real OVO home, writing into the "OVO Tests" project.

The round is named after this repository and the day, and a previous run of the same day is cleared,
so repeated runs do not pile up pools. Nothing outside that round is touched.
"""

import datetime
import os

import pytest

from ovo.core.utils.tests import TEST_PROJECT_NAME


@pytest.fixture(scope="session", autouse=True)
def check_unit_test_mode():
    if os.environ.get("OVO_UNIT_TEST") == "1":
        raise RuntimeError("Please run unit tests and integration tests separately.")


@pytest.fixture(scope="session")
def project_data():
    """Create one project and round for the entire test run."""
    from ovo import db

    prefix = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    round_name = prefix + " " + datetime.datetime.now().strftime("%a %d %b %Y")

    if not db.Project.count(name=TEST_PROJECT_NAME):
        project = db.Project(name=TEST_PROJECT_NAME, author="test", public=True)
        db.save(project)
    else:
        project = db.Project.get(name=TEST_PROJECT_NAME)

    if db.Round.count(name=round_name, project_id=project.id):
        # Clear the round of an earlier run of the same day
        project_round = db.Round.get(name=round_name, project_id=project.id)
        pools = db.Pool.select(round_id=project_round.id)
        designs = db.Design.select(pool_id__in=[p.id for p in pools])
        db.DescriptorValue.remove(design_id__in=[d.id for d in designs])
        db.Design.remove(pool_id__in=[p.id for p in pools])
        db.ProjectArtifact.remove(design_job_id__in=[p.design_job_id for p in pools if p.design_job_id])
        db.DesignJob.remove(id__in=[p.design_job_id for p in pools if p.design_job_id])
        db.Pool.remove(round_id=project_round.id)
        db.Round.remove(id=project_round.id)

    project_round = db.Round(name=round_name, project_id=project.id, author="test")
    db.save(project_round)
    return project, project_round
