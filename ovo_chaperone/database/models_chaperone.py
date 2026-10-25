"""Database objects of a Chaperone import: the artifact holding a run's metrics and the workflow
owning its designs.

Chaperone names its metrics freely - a block's ``compile()`` puts whatever keys it likes into a JSON
column, and nothing upstream declares them (no units, no direction, no stable vocabulary). Declaring
one OVO :class:`~ovo.core.database.models.Descriptor` per key would mean hand-converting every
Chaperone metric name and migrating stored values whenever Chaperone renames one, so an import keeps
its metrics in :class:`ChaperoneImportArtifact` instead and the plugin renders them itself.
"""

from dataclasses import dataclass, field
from typing import Callable, Union

from ovo.core.database.models import (
    Artifact,
    ArtifactTypes,
    Base,
    DescriptorJob,
    DesignJob,
    DesignWorkflow,
    WorkflowTypes,
)

# column of the metrics table holding the design each row describes
DESIGN_ID_COLUMN = "ovo_design_id"
# column holding the id the design had in Chaperone
CHAPERONE_ID_COLUMN = "chaperone_design_id"
# suffix of the column holding a step's structure, as "<step>.structure_path"
STRUCTURE_PATH_SUFFIX = "structure_path"


@dataclass
class ChaperoneColumn:
    """One column of an imported metrics table.

    :param key: Column name, ``<step>.<metric>`` for a metric and ``<step>.structure_path`` for a file
    :param step: Chaperone step that produced it, the part of the key before the first dot
    :param numeric: Whether every non-empty value parsed as a number, so it can be plotted
    """

    key: str
    step: str
    numeric: bool


@ArtifactTypes.register()
@dataclass
class ChaperoneImportArtifact(Artifact):
    """Metrics of one imported Chaperone run, as a CSV in storage plus the columns it holds.

    The table is referenced rather than embedded so that a campaign-scale run does not land in a
    JSON column, and so that ``get_storage_paths`` covers it on project export and cleanup. The
    column list is embedded, so a view can offer its selectors without reading the table.

    :param source_rundir: Chaperone run directory the import read, kept for provenance only
    :param final_step: Step whose structure became ``Design.structure_path``
    :param step_names: Chaperone steps found in the run, in pipeline order
    :param metrics_table_path: Storage path of the CSV, one row per imported design
    :param columns: Every column of the CSV except the two id columns
    :param num_designs: Rows of the CSV, which is also the number of designs created
    """

    source_rundir: str
    final_step: str
    step_names: list[str]
    metrics_table_path: str
    columns: list[ChaperoneColumn]
    num_designs: int

    def get_storage_paths(self) -> list[str]:
        return [self.metrics_table_path] if self.metrics_table_path else []

    @classmethod
    def from_dict_inner(cls, data: dict) -> dict:
        """Rebuild the column list, which is stored as a list of dicts."""
        columns = data.get("columns") or []
        data["columns"] = [c if isinstance(c, ChaperoneColumn) else ChaperoneColumn(**c) for c in columns]
        return data

    def numeric_keys(self) -> list[str]:
        """Keys of the columns that can go on a plot axis."""
        return [column.key for column in self.columns if column.numeric]

    def keys_of_step(self, step: str) -> list[str]:
        """Keys the given step contributed, in table order."""
        return [column.key for column in self.columns if column.step == step]


@WorkflowTypes.register("Chaperone Import")
@dataclass
class ChaperoneDesignWorkflow(DesignWorkflow):
    """The import that created a pool of designs, so they get the Chaperone design visualization.

    This workflow runs nothing. It exists because a :class:`~ovo.core.database.models.Pool` names
    its origin through a :class:`~ovo.core.database.models.DesignJob`, and the workflow of that job
    is what decides how a single design is visualized. Submitting it is refused by the inherited
    ``get_pipeline_name()``.

    :param source_rundir: Chaperone run directory that was imported
    :param step_names: Chaperone steps found in the run, in pipeline order
    :param final_step: Step whose structure became ``Design.structure_path``
    """

    source_rundir: str = None
    step_names: list[str] = field(default_factory=list)
    final_step: str = None

    def prepare_params(self, workdir: str) -> dict:
        raise NotImplementedError(
            "Chaperone results are imported, not submitted: run the pipeline with Chaperone itself, "
            "then import its run directory on the Chaperone Import page."
        )

    def process_results(self, job: Union[DesignJob, DescriptorJob], callback: Callable = None) -> list[Base]:
        raise NotImplementedError(
            "A Chaperone import has no job to process: its designs and metrics are saved by "
            "ovo_chaperone.logic.chaperone_logic.save_chaperone_import()."
        )

    @classmethod
    def visualize_single_design_structures(cls, design_id: str):
        from ovo_chaperone.app.visualize_chaperone import visualize_chaperone_design_structures

        visualize_chaperone_design_structures(design_id)
