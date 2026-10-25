"""Import an existing Chaperone run directory as a pool of designs."""

import os
import tempfile

import pandas as pd
import streamlit as st

from ovo import config
from ovo.app.components.submission_components import get_pool_inputs, pool_submission_inputs
from ovo.app.utils.page_init import initialize_page
from ovo.core.auth import get_username
from ovo_chaperone.chaperone_import import (
    discover_chaperone_steps,
    format_run_designed_chains,
    pick_final_step,
    read_chaperone_rundir,
    save_chaperone_import,
)


def show_rundir_input() -> str | None:
    """Show the run directory inputs and return the directory to read, if there is one.

    An admin can name a directory the server can already see, which is the quick path when Chaperone
    ran on the same filesystem. Everyone can upload one, which is materialized into a temporary
    directory that lives as long as the session, so the run can be previewed before importing.
    """
    is_admin = get_username() in config.auth.admin_users
    uploader_label = "Upload a Chaperone run directory"

    if is_admin:
        tab_upload, tab_path = st.tabs(["Upload directory", "Server path (admin)"])
        with tab_path:
            path = st.text_input(
                "Chaperone run directory on the server",
                placeholder="/path/to/chaperone/rundir",
                key="chaperone_rundir_path",
            )
            st.caption(
                "The engine's rundir, holding one directory per step. Only shown to admins, since "
                "it reads a path on the server rather than an upload."
            )
            if path:
                if not os.path.isdir(path):
                    st.error(f"Not a directory: `{path}`")
                    return None
                return path
        with tab_upload:
            uploaded_files = st.file_uploader(uploader_label, accept_multiple_files="directory")
    else:
        uploaded_files = st.file_uploader(uploader_label, accept_multiple_files="directory")

    if not uploaded_files:
        return None
    return store_uploaded_rundir(uploaded_files)


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


if __name__ == "__main__":
    initialize_page(page_title="Chaperone Import")

    st.title("🎩 Chaperone Import")
    st.markdown(
        "Import a [Chaperone](https://rosettacommons.github.io/chaperone/) run into OVO. "
        "The final step's `output.parquet` carries the structures and the metrics of every step "
        "before it, so any pipeline can be imported without naming its blocks or its metrics."
    )
    st.markdown(
        "Metrics are stored as one table per import, not as OVO descriptors, so they do not appear "
        "in the descriptor table, in acceptance thresholds or in ranking. Opening a single design "
        "shows its structures and what each step measured; everything else needs the stored CSV. "
        "Importing them as descriptors needs a metric registry on the Chaperone side."
    )

    pool_submission_inputs(__file__)
    round_id, pool_name, pool_description = get_pool_inputs(__file__)

    rundir = show_rundir_input()
    if not rundir:
        st.stop()

    steps = discover_chaperone_steps(rundir)
    if not steps:
        st.error(
            f"No Chaperone step found in `{rundir}`: expected at least one `<step>/output.parquet`. "
            "Select the run directory itself, not one of its steps."
        )
        st.stop()

    left, right = st.columns(2)
    with left:
        step_names = list(steps)
        final_step = st.selectbox(
            "Final step",
            options=step_names,
            index=step_names.index(pick_final_step(steps)),
        )
        st.caption("Its structures become the designs. Its rows carry every earlier step as well.")

    run = read_chaperone_rundir(rundir, final_step=final_step)

    with right:
        derived_chains = format_run_designed_chains(run)
        designed_chains = st.text_input(
            "Designed chains",
            value=",".join(derived_chains),
            placeholder="A,B",
            help="Comma- or space-separated chain ids.",
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
    st.write(
        f"Found **{len(run.designs):,}** designs across steps "
        + ", ".join(f"`{step}`" for step in run.step_names)
    )
    preview = pd.DataFrame(
        [{"chaperone_design_id": design.chaperone_design_id, **design.metrics} for design in run.designs]
    )
    st.dataframe(preview, hide_index=True, height=240)

    if st.button("Import", type="primary", disabled=not pool_name, help=None if pool_name else "Name the pool"):
        with st.status("Importing...", expanded=True) as status:
            result = save_chaperone_import(
                run=run,
                project_id=st.session_state.project.id,
                pool_name=pool_name,
                round_id=round_id,
                description=pool_description,
                designed_chains=[c.strip() for c in designed_chains.replace(",", " ").split() if c.strip()],
                callback=lambda message: status.write(message),
            )
            status.update(label=f"Imported pool {result.pool.id}", state="complete", expanded=False)

        # Leave the page rather than offering the button again, so one run is not imported twice
        message = (
            f"Imported {result.num_designs:,} designs into pool {result.pool.name} "
            f"({result.pool.id}) with {len(result.artifact.columns)} metric columns"
        )
        warnings = "".join(f"\n\n:warning: {warning}" for warning in result.conversion_warnings)
        st.session_state.flash_success = message + warnings
        st.session_state.pool_inputs.pop(__file__, None)
        st.session_state.pop("chaperone_rundir_path", None)
        st.switch_page("app/pages/designs/designs.py")
