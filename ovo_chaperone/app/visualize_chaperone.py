"""Show the structures of one imported Chaperone design, one tab per step.

Routed to by
:meth:`~ovo_chaperone.database.models_chaperone.ChaperoneDesignWorkflow.visualize_single_design_structures`.

This is deliberately all the UI the plugin has. The imported metrics live in a
:class:`~ovo_chaperone.database.models_chaperone.ChaperoneImportArtifact` rather than in descriptor values,
so they are not reachable by OVO's descriptor table, plots, thresholds or ranking; a plugin-owned
view of them would only make that detour comfortable. The metrics of the step being looked at are
shown here beside its structure, and the whole table can be read with
:func:`~ovo_chaperone.logic.chaperone_logic.read_metrics_table`.
"""

import pandas as pd
import streamlit as st

from ovo import storage, viz
from ovo.app.utils.cached_db import get_cached_design
from ovo_chaperone.logic.chaperone_logic import (
    find_chaperone_import,
    get_design_structure_paths,
    read_metrics_table,
)
from ovo_chaperone.database.models_chaperone import (
    CHAPERONE_ID_COLUMN,
    DESIGN_ID_COLUMN,
    STRUCTURE_PATH_SUFFIX,
    ChaperoneImportArtifact,
)

# how a structure is shown, matching OVO's default design visualization
REPRESENTATION = "cartoon+ball-and-stick"


def visualize_chaperone_design_structures(design_id: str, height="500px"):
    """Show one structure per Chaperone step of a single design, the final step first."""
    design = get_cached_design(design_id)
    imported = get_cached_chaperone_import(design.pool_id)
    if imported is None:
        st.write("No Chaperone import found for this design's pool.")
        return

    table = get_cached_metrics_table(imported.metrics_table_path)
    structure_paths = get_design_structure_paths(table, design_id, imported.step_names)
    if not structure_paths:
        st.write("The Chaperone import holds no structures for this design.")
        return

    row = table[table[DESIGN_ID_COLUMN] == design_id].iloc[0]
    ordered = [imported.final_step] + [s for s in structure_paths if s != imported.final_step]
    for tab, step in zip(st.tabs(ordered), ordered):
        with tab:
            st.caption(f"`{row[CHAPERONE_ID_COLUMN]}` as published by step `{step}`")
            viz.molstar(
                viz.StructureVisualization(
                    data=storage.read_file_str(structure_paths[step]),
                    color="chain-id",
                    representation=REPRESENTATION,
                ),
                key=f"chaperone_structure_{step}",
                height=height,
            )
            show_step_metrics(imported, row, step)


def show_step_metrics(imported: ChaperoneImportArtifact, row: pd.Series, step: str):
    """Show what one step measured about one design, as a two-column table."""
    metrics = {
        key: row[key]
        for key in imported.keys_of_step(step)
        if key in row and not key.endswith(f".{STRUCTURE_PATH_SUFFIX}")
    }
    if not metrics:
        return
    st.dataframe(
        pd.DataFrame({"metric": list(metrics), "value": [str(value) for value in metrics.values()]}),
        hide_index=True,
        height=min(320, 40 + 35 * len(metrics)),
    )


@st.cache_data(max_entries=100, ttl="10m", show_spinner=False)
def get_cached_chaperone_import(pool_id: str) -> ChaperoneImportArtifact | None:
    return find_chaperone_import(pool_id)


@st.cache_data(max_entries=10, ttl="10m", show_spinner="Loading Chaperone metrics...")
def get_cached_metrics_table(metrics_table_path: str) -> pd.DataFrame:
    return read_metrics_table(metrics_table_path)
