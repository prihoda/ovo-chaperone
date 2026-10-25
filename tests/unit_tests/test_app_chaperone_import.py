"""Drive the Chaperone Import page with Streamlit's AppTest.

The page cannot be run on its own: OVO registers its pages with paths relative to the main script,
so the app entrypoint is the main script here and the plugin page is reached with
``AppTest.switch_page``, which needs the plugin installed for ``st.navigation`` to know it.
"""

import os

import pytest
from streamlit.testing.v1 import AppTest

from tests.fixture_paths import MOCK_DESIGN_IDS, MOCK_RUNDIR

TIMEOUT = 120


def app_entrypoint() -> str:
    """Return OVO's app entrypoint, which is the main script of every page."""
    import ovo

    return os.path.join(os.path.dirname(os.path.abspath(ovo.__file__)), "run_app.py")


def page_path_relative_to_app() -> str:
    """Return the plugin page as AppTest wants it: relative to the main script's directory."""
    page = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "ovo_chaperone",
        "pages",
        "chaperone_import.py",
    )
    return os.path.relpath(page, os.path.dirname(app_entrypoint()))


@pytest.fixture
def admin_user(monkeypatch):
    """Make the test user an admin, which is what shows the server-path input."""
    from ovo import config
    from ovo.core.auth import get_username

    monkeypatch.setattr(config.auth, "admin_users", [get_username()])


@pytest.fixture
def import_page(project_data) -> AppTest:
    """Open the Chaperone Import page with a project selected, as the app would have it."""
    project, _, _ = project_data
    at = AppTest.from_file(app_entrypoint(), default_timeout=TIMEOUT)
    at.session_state["project"] = project
    at.run()
    return at.switch_page(page_path_relative_to_app()).run()


def test_page_waits_for_a_run_directory(import_page):
    """Without a run directory the page stops after its inputs, offering nothing to import."""
    at = import_page

    assert not at.exception, at.exception
    assert any("Chaperone Import" in title.value for title in at.title)
    assert not [button for button in at.button if button.label == "Import"]


def test_import_a_run(import_page, admin_user, project_data):
    """Name a run directory as an admin, check what the page read off it, and import it."""
    from ovo import db

    _, project_round, _ = project_data
    at = import_page.run()

    # the server-path input is admin-only, see show_rundir_input
    at = at.text_input(key="chaperone_rundir_path").set_value(MOCK_RUNDIR).run()
    assert not at.exception, at.exception

    # the final step is the one that accumulated every metric namespace
    final_step = [box for box in at.selectbox if box.label == "Final step"]
    assert final_step, f"no final step selectbox among {[box.label for box in at.selectbox]}"
    assert sorted(final_step[0].options) == ["colabfold", "mpnn", "rfd3"]
    assert final_step[0].value == "colabfold"
    # the designed chains are read off the run: chains with a sequence, minus the motif ones
    designed = [field for field in at.text_input if field.label == "Designed chains"]
    assert designed and designed[0].value == "A"
    # one preview row per design of the run
    assert len(at.dataframe[0].value) == len(MOCK_DESIGN_IDS)

    pool_name = "AppTest Chaperone import"
    at = at.text_input(key="input_pool_name").set_value(pool_name).run()

    imported = [button for button in at.button if button.label == "Import"]
    assert imported, f"no Import button among {[button.label for button in at.button]}"
    at = imported[0].click().run()

    pools = db.Pool.select(round_id=project_round.id, name=pool_name)
    assert len(pools) == 1, f"expected one pool named {pool_name!r}, got {len(pools)}"
    assert len(db.Design.select(pool_id=pools[0].id)) == len(MOCK_DESIGN_IDS)

    # Importing redirects to the Designs page, so the run cannot be imported twice by clicking
    # again. initialize_page shows the flash message there and pops it out of the session state.
    assert not [button for button in at.button if button.label == "Import"]
    assert any("Designs" in title.value for title in at.title), [title.value for title in at.title]
    assert any(
        f"Imported {len(MOCK_DESIGN_IDS)} designs into pool {pool_name}" in message.value
        for message in at.success
    ), [message.value for message in at.success]
