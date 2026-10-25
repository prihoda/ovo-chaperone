"""Where the test fixture lives and what it holds."""

import os

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
# the mock Chaperone run directory, see scripts/build_mock_rundir.py and its PROVENANCE.md
MOCK_RUNDIR = os.path.join(FIXTURES_DIR, "mock_chaperone_rundir")
# designs the fixture holds, named the way Chaperone names them
MOCK_DESIGN_IDS = [
    "rfd3_binder_chunk0_binder_0_model_0__mpnn_1",
    "rfd3_binder_chunk0_binder_1_model_0__mpnn_1",
    "rfd3_binder_chunk0_binder_2_model_0__mpnn_1",
]
# steps of the fixture, in pipeline order
MOCK_STEPS = ["rfd3", "mpnn", "colabfold"]
