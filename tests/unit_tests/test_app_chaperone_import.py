"""Drive the Chaperone tab of OVO's design upload dialog with Streamlit's AppTest.

The tab is contributed as an ``ovo.design_upload`` extension point, so it is reached by opening the
Designs page and its upload dialog. OVO registers its pages with paths relative to the main script,
so the app entrypoint has to be the main script here, and the plugin has to be installed for the
extension point to be found.
"""

import os

import pytest
from streamlit.testing.v1 import AppTest

from tests.fixture_paths import MOCK_DESIGN_IDS, MOCK_RUNDIR

TIMEOUT = 120
DESIGNS_PAGE = os.path.join("app", "pages", "designs", "designs.py")
TAB_TITLE = "Chaperone run"
# query param / session key simple_tabs keeps the selected tab under, see create_new_pool
TAB_KEY = "design_upload_tab"


def app_entrypoint() -> str:
    """Return OVO's app entrypoint, which is the main script of every page."""
    import ovo

    return os.path.join(os.path.dirname(os.path.abspath(ovo.__file__)), "run_app.py")


@pytest.fixture
def admin_user(monkeypatch):
    """Make the test user an admin, who may name any directory on the server."""
    from ovo import config
    from ovo.core.auth import get_username

    monkeypatch.setattr(config.auth, "admin_users", [get_username()])


@pytest.fixture
def non_admin_user(monkeypatch):
    """Take the test user's admin rights away - ``ovo init home`` grants them by default."""
    from ovo import config

    monkeypatch.setattr(config.auth, "admin_users", [])


@pytest.fixture
def upload_dialog(project_data) -> AppTest:
    """Open the design upload dialog on the Designs page, with a project selected."""
    from ovo.app.utils.testing import show_test_dialog

    project, _, _ = project_data
    at = AppTest.from_file(app_entrypoint(), default_timeout=TIMEOUT)
    at.session_state["project"] = project
    at.run()
    at.switch_page(DESIGNS_PAGE).run()
    show_test_dialog(at, "create_new_pool")
    return at


@pytest.fixture
def chaperone_tab(upload_dialog) -> AppTest:
    """Select the Chaperone tab, which is what renders its inputs: simple_tabs draws one tab."""
    upload_dialog.session_state[TAB_KEY] = TAB_TITLE
    return upload_dialog.run()


def test_the_dialog_offers_the_chaperone_tab(upload_dialog):
    """The plugin's source is a tab beside the built-in one, which stays selected by default."""
    at = upload_dialog

    assert not at.exception, at.exception
    tab_labels = [tab.label for tab in at.tabs]
    assert tab_labels[:2] == ["File upload", TAB_TITLE], tab_labels
    # the built-in source is the default, so only its inputs are drawn
    assert at.file_uploader(key="uploader") is not None
    assert not [field for field in at.text_input if field.key == "chaperone_rundir_path"]


def test_server_path_is_shown_to_everyone_but_needs_configuring(non_admin_user, chaperone_tab, monkeypatch):
    """A non-admin sees the server path input, and cannot use it until an admin allows a directory."""
    from ovo import config

    at = chaperone_tab
    assert at.text_input(key="chaperone_rundir_path").disabled, "nothing configured, so no path"
    assert any("allowed_rundirs" in caption.value for caption in at.caption)

    # configuring a directory enables the input for a non-admin, who is then held to it
    monkeypatch.setitem(config.plugins, "ovo_chaperone", {"allowed_rundirs": [os.path.dirname(MOCK_RUNDIR)]})
    at = at.run()
    assert not at.text_input(key="chaperone_rundir_path").disabled
    at = at.text_input(key="chaperone_rundir_path").set_value("/etc").run()
    assert any("outside the allowed directories" in error.value for error in at.error)

    at = at.text_input(key="chaperone_rundir_path").set_value(MOCK_RUNDIR).run()
    assert not at.error, [error.value for error in at.error]
    assert [button for button in at.button if button.label == "Import run"]


def test_import_a_run(admin_user, chaperone_tab, project_data):
    """Name a run directory as an admin, check what the tab read off it, and import it."""
    from ovo import db

    _, project_round, _ = project_data
    at = chaperone_tab

    at = at.text_input(key="chaperone_rundir_path").set_value(MOCK_RUNDIR).run()
    assert not at.exception, at.exception

    # the final step is the one that accumulated every metric namespace
    final_step = at.selectbox(key="chaperone_final_step")
    assert sorted(final_step.options) == ["colabfold", "mpnn", "rfd3"]
    assert final_step.value == "colabfold"
    # the designed chains are read off the run: chains with a sequence, minus the motif ones
    assert at.text_input(key="chaperone_designed_chains").value == "A"
    # one preview row per design of the run, found by its contents: a plain dataframe is not a
    # widget, so AppTest does not expose the key it was given
    previews = [
        frame.value for frame in at.dataframe if "chaperone_design_id" in list(frame.value.columns)
    ]
    assert previews, [list(frame.value.columns)[:3] for frame in at.dataframe]
    assert list(previews[0]["chaperone_design_id"]) == MOCK_DESIGN_IDS

    pool_name = "AppTest Chaperone import"
    at = at.text_input(key="chaperone_pool_name").set_value(pool_name).run()
    at = at.button(key="chaperone_submit").click().run()

    pools = db.Pool.select(round_id=project_round.id, name=pool_name)
    assert len(pools) == 1, f"expected one pool named {pool_name!r}, got {len(pools)}"
    assert len(db.Design.select(pool_id=pools[0].id)) == len(MOCK_DESIGN_IDS)

    # Importing reruns, which closes the dialog, so the run cannot be imported twice. The new pool
    # is selected through the query params the Designs page reads.
    assert not [button for button in at.button if button.label == "Import run"]
    assert at.query_params["pool_ids"] == pools[0].id
