"""Import an existing Chaperone run directory as a pool of designs.

Shown as a tab of OVO's design upload dialog, see
:func:`~ovo.app.components.pool_components.create_new_pool`.
"""

import os
import tempfile

import pandas as pd
import streamlit as st

from ovo import config, db
from ovo.app.components.navigation import ROUND_IDS_QUERY_PARAM
from ovo.core.auth import get_username
from ovo.core.database.models import Pool
from ovo_chaperone.logic.chaperone_logic import (
    check_rundir_path,
    discover_chaperone_steps,
    format_run_designed_chains,
    pick_final_step,
    read_allowed_rundirs,
    read_chaperone_rundir,
    save_chaperone_import,
)

# what the upload dialog's round selectbox holds when a new round is to be created
NEW_ROUND = "__new__"


def show_rundir_input() -> str | None:
    """Show the run directory inputs and return the directory to read, if there is one.

    Naming a directory the server can already see is the quick path when Chaperone ran on the same
    filesystem. An admin may name any of them; everyone else is held to the configured ones, see
    :func:`~ovo_chaperone.logic.chaperone_logic.read_allowed_rundirs`. Uploading always works, and
    is materialized into a temporary directory that lives as long as the session, so the run can be
    previewed before importing.
    """
    is_admin = get_username() in config.auth.admin_users
    allowed_dirs = read_allowed_rundirs()
    tab_upload, tab_path = st.tabs(["Upload directory", "Server path"])

    with tab_path:
        path = show_server_path_input(is_admin=is_admin, allowed_dirs=allowed_dirs)
    with tab_upload:
        uploaded_files = st.file_uploader(
            "Upload a Chaperone run directory", accept_multiple_files="directory"
        )

    if path:
        return path
    if not uploaded_files:
        return None
    return store_uploaded_rundir(uploaded_files)


def show_server_path_input(*, is_admin: bool, allowed_dirs: list[str]) -> str | None:
    """Show the server path input and return the directory it names, if it is allowed."""
    blocked = not is_admin and not allowed_dirs
    path = st.text_input(
        "Chaperone run directory on the server",
        placeholder="/path/to/chaperone/rundir",
        disabled=blocked,
        key="chaperone_rundir_path",
    )
    if is_admin:
        st.caption(
            "The engine's rundir, holding one directory per step. You are an admin, so any "
            "directory the server can read is allowed."
        )
    elif allowed_dirs:
        st.caption(
            "The engine's rundir, holding one directory per step. It has to be inside "
            + ", ".join(f"`{directory}`" for directory in allowed_dirs)
            + ", which an admin configures under `plugins.ovo_chaperone.allowed_rundirs` in "
            "`config.yml`."
        )
    else:
        st.caption(
            "No directory is allowed on this installation, so upload the run instead. An admin can "
            "list directories to allow under `plugins.ovo_chaperone.allowed_rundirs` in `config.yml`."
        )

    if not path:
        return None
    refusal = check_rundir_path(path, allow_any=is_admin, allowed_dirs=allowed_dirs)
    if refusal:
        st.error(refusal)
        return None
    return path


def store_uploaded_rundir(uploaded_files: list) -> str | None:
    """Write the uploaded files into a session-scoped temporary directory and return its root.

    Kept in the session state so the directory survives the reruns between previewing a run and
    importing it; it is removed when the session ends.
    """
    key = "chaperone_upload_dir"
    names = sorted(file.name for file in uploaded_files)
    if st.session_state.get(f"{key}_names") != names:
        st.session_state[key] = tempfile.TemporaryDirectory(prefix="ovo_chaperone_")
        st.session_state[f"{key}_names"] = names
        for file in uploaded_files:
            target = os.path.join(st.session_state[key].name, file.name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(file.getbuffer())

    root = st.session_state[key].name
    entries = [entry for entry in os.listdir(root) if not entry.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
        # a directory upload keeps the chosen directory as the common prefix of every file name
        return os.path.join(root, entries[0])
    return root


def show_chaperone_upload(*, project_id: str, round_id: str, new_round_name: str | None = None):
    """Draw the Chaperone tab of OVO's design upload dialog.

    Registered as an ``ovo.design_upload`` extension point, so the round comes from the dialog and
    this owns the rest: the run directory, what to read out of it, and saving the pool.
    """
    st.markdown(
        "Import a [Chaperone](https://rosettacommons.github.io/chaperone/) run. The final step's "
        "`output.parquet` carries the structures and the metrics of every step before it, so any "
        "pipeline can be imported without naming its blocks or its metrics."
    )
    st.markdown(
        "Metrics are stored as one table per import, not as OVO descriptors, so they do not appear "
        "in the descriptor table, in acceptance thresholds or in ranking. Opening a single design "
        "shows its structures and what each step measured; everything else needs the stored CSV. "
        "Importing them as descriptors needs a metric registry on the Chaperone side."
    )

    rundir = show_rundir_input()
    if not rundir:
        return

    steps = discover_chaperone_steps(rundir)
    if not steps:
        st.error(
            f"No Chaperone step found in `{rundir}`: expected at least one `<step>/output.parquet`. "
            "Select the run directory itself, not one of its steps."
        )
        return

    left, right = st.columns(2)
    with left:
        step_names = list(steps)
        final_step = st.selectbox(
            "Final step",
            options=step_names,
            index=step_names.index(pick_final_step(steps)),
            key="chaperone_final_step",
        )
        st.caption("Its structures become the designs. Its rows carry every earlier step as well.")

    run = read_chaperone_rundir(rundir, final_step=final_step)

    with right:
        designed_chains = show_designed_chains_input(run)

    st.write(
        f"Found **{len(run.designs):,}** designs across steps "
        + ", ".join(f"`{step}`" for step in run.step_names)
    )
    preview = pd.DataFrame(
        [{"chaperone_design_id": design.chaperone_design_id, **design.metrics} for design in run.designs]
    )
    st.dataframe(preview, hide_index=True, height=240)

    pool_name = st.text_input(
        "Pool name",
        placeholder="Descriptive name for this collection of designs",
        key="chaperone_pool_name",
    )
    description = st.text_area(
        "Pool description (optional)",
        placeholder="Optional longer description of this pool",
        key="chaperone_pool_description",
    )

    taken = pool_name and db.count(Pool, round_id=round_id, name=pool_name)
    if taken:
        st.error("A pool with this name already exists in this round. Please choose a different name.")

    error = "Please enter a pool name" if not pool_name else "Pool name already used" if taken else None
    with st.columns([3, 1])[1]:
        submit = st.button(
            "Import run",
            disabled=bool(error),
            help=error,
            type="primary",
            width="stretch",
            key="chaperone_submit",
        )
    if not submit:
        return

    with st.status("Importing...", expanded=True) as status:
        result = save_chaperone_import(
            run=run,
            project_id=project_id,
            pool_name=pool_name,
            round_id=None if round_id == NEW_ROUND else round_id,
            new_round_name=new_round_name if round_id == NEW_ROUND else None,
            description=description,
            designed_chains=designed_chains,
            callback=lambda message: status.write(message),
        )
        status.update(label=f"Imported pool {result.pool.id}", state="complete", expanded=False)

    for warning in result.conversion_warnings:
        st.warning(warning)

    # Show the new pool, and close the dialog so the run cannot be imported twice
    st.query_params[ROUND_IDS_QUERY_PARAM] = result.pool.round_id
    st.query_params["pool_ids"] = result.pool.id
    st.session_state.pop("chaperone_rundir_path", None)
    st.text("✅ Done")
    st.rerun()


def show_designed_chains_input(run) -> list[str]:
    """Show the designed chains input, prefilled from the run, and return the chain ids."""
    derived_chains = format_run_designed_chains(run)
    designed_chains = st.text_input(
        "Designed chains",
        value=",".join(derived_chains),
        placeholder="A,B",
        help="Comma- or space-separated chain ids.",
        key="chaperone_designed_chains",
    )
    if derived_chains:
        st.caption(
            "Read from the run: the chains Chaperone reports a sequence for, minus the ones it "
            "kept as motif. Edit if this run fixed something else."
        )
    else:
        st.caption(
            "This run reports no sequences, so every chain of the structure that is not motif "
            "is treated as designed. Name the chains to override."
        )
    return [chain.strip() for chain in designed_chains.replace(",", " ").split() if chain.strip()]
