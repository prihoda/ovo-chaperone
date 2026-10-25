"""Read a Chaperone run directory and save it as a pool of designs plus one metrics artifact.

A Chaperone step publishes ``<rundir>/<step>/output.parquet``, and the handler row of a step carries
everything its upstream steps produced: the accumulated ``structures`` list, the ``metrics`` of every
step namespaced by step name, and the ``sequences``. Reading the final step therefore reads the whole
run, which is what makes this import generic - it never needs to know which blocks ran or which
metrics they report.
"""

import json
import os
import re
from dataclasses import dataclass, field
from io import StringIO

import pandas as pd

from ovo import config, db, storage
from ovo.core.auth import get_username
from ovo.core.database.models import Design, DesignJob, DesignSpec, Pool, ProjectArtifact, Round
from ovo.core.utils.pdb import get_sequences_from_pdb_str, mmcif_to_pdb
from ovo_chaperone.database.models_chaperone import (
    CHAPERONE_ID_COLUMN,
    DESIGN_ID_COLUMN,
    STRUCTURE_PATH_SUFFIX,
    ChaperoneColumn,
    ChaperoneDesignWorkflow,
    ChaperoneImportArtifact,
)

# what a Chaperone step publishes its handler rows as
STEP_OUTPUT_FILE = "output.parquet"
# extensions Chaperone writes structures as
STRUCTURE_EXTENSIONS = (".cif", ".mmcif", ".pdb")


@dataclass
class ChaperoneDesign:
    """One design of a Chaperone run, as read from the final step's handler row.

    :param chaperone_design_id: Id the design has in Chaperone, or the stem of its structure
    :param structure_paths: Absolute path of the structure each step produced, by step name
    :param metrics: Metric values keyed ``<step>.<metric>``, nested keys joined with dots
    :param sequences: One-letter sequence by chain id, empty when the run carries none
    :param motif_chains: Chains Chaperone marked as motif, which are the fixed ones
    """

    chaperone_design_id: str
    structure_paths: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, object] = field(default_factory=dict)
    sequences: dict[str, str] = field(default_factory=dict)
    motif_chains: set[str] = field(default_factory=set)


@dataclass
class ChaperoneRun:
    """A Chaperone run directory, read and ready to save.

    :param rundir: Directory that was read
    :param final_step: Step whose handler rows were read, and whose structure is the design's
    :param step_names: Steps that contributed a structure or a metric, in pipeline order
    :param designs: One entry per row of the final step
    """

    rundir: str
    final_step: str
    step_names: list[str]
    designs: list[ChaperoneDesign]


@dataclass
class ChaperoneImportResult:
    """Outcome of :func:`save_chaperone_import`."""

    pool: Pool
    num_designs: int
    artifact: ChaperoneImportArtifact
    # warnings from converting Chaperone's mmCIF output to PDB, if any
    conversion_warnings: list[str] = field(default_factory=list)


def save_chaperone_import(
    run: ChaperoneRun,
    project_id: str,
    pool_name: str,
    round_id: str = None,
    new_round_name: str = None,
    description: str = "",
    designed_chains: list[str] = None,
    callback: callable = None,
) -> ChaperoneImportResult:
    """Save a read Chaperone run as a new pool, storing its structures and its metrics table.

    Creates one :class:`~ovo.core.database.models.Design` per entry of ``run``, from the structure of
    the run's final step, and stores every step's structure in the pool's storage directory. The
    metrics go to a CSV in the same directory, referenced by one
    :class:`~ovo_chaperone.database.models_chaperone.ChaperoneImportArtifact`; no descriptor values are
    written, see that class. The designs, the pool, the job and the new round (if any) are saved in
    one atomic ``save_all``, the artifact right after it.

    :param run: Run to save, as returned by :func:`read_chaperone_rundir`
    :param project_id: Project to create the pool under
    :param pool_name: Name of the new pool
    :param round_id: Existing round to add the pool to; omit to create a new round
    :param new_round_name: Name for a new round, when round_id is not given
    :param description: Optional longer pool description
    :param designed_chains: Chain ids to treat as designed; see :func:`resolve_designed_chains`
    :param callback: Optional progress callback, called with a human-readable message per step
    :raises ValueError: when the run is empty, the round is not usable, or two designs would share an id
    """
    if not run.designs:
        raise ValueError(f"Chaperone run '{run.rundir}' holds no designs, nothing to import")

    def report(message: str) -> None:
        if callback is not None:
            callback(message)

    username = get_username()
    new_round, round_id = resolve_round(project_id, round_id, new_round_name, username)

    design_job = DesignJob(
        workflow=ChaperoneDesignWorkflow(
            source_rundir=run.rundir,
            step_names=list(run.step_names),
            final_step=run.final_step,
        ),
        scheduler_key="chaperone_import",
        job_id=f"import:{os.path.basename(os.path.normpath(run.rundir))}",
        author=username,
        job_result=True,
    )
    pool = Pool(
        id=Pool.generate_id(),
        author=username,
        round_id=round_id,
        name=pool_name,
        description=description,
        design_job_id=design_job.id,
        processed=True,
    )

    design_ids = format_design_ids(run, pool.id)
    pool_path = storage.get_project_path(project_id, pool.id)

    designs = []
    conversion_warnings = []
    table_rows = []
    for chaperone_design in run.designs:
        design_id = design_ids[chaperone_design.chaperone_design_id]
        report(f"Importing {design_id}...")

        row = {
            DESIGN_ID_COLUMN: design_id,
            CHAPERONE_ID_COLUMN: chaperone_design.chaperone_design_id,
        }
        for step, source_path in chaperone_design.structure_paths.items():
            pdb_str, warnings = read_structure_as_pdb(source_path)
            conversion_warnings.extend(warnings)
            row[f"{step}.{STRUCTURE_PATH_SUFFIX}"] = storage.store_file_str(
                pdb_str, os.path.join(pool_path, step, f"{design_id}.pdb")
            )
            if step == run.final_step:
                designs.append(
                    Design(
                        id=design_id,
                        pool_id=pool.id,
                        spec=DesignSpec.from_pdb_str(
                            pdb_str,
                            chains=resolve_designed_chains(pdb_str, chaperone_design, designed_chains),
                        ),
                        structure_path=row[f"{step}.{STRUCTURE_PATH_SUFFIX}"],
                    )
                )
        row.update(chaperone_design.metrics)
        table_rows.append(row)

    if len(designs) != len(run.designs):
        missing = [d.chaperone_design_id for d in run.designs if run.final_step not in d.structure_paths]
        raise ValueError(
            f"{len(missing)} design(s) of step '{run.final_step}' have no structure, so they cannot "
            f"become OVO designs: {', '.join(missing[:5])}. Import a step that publishes structures."
        )

    report(f"Saving {len(designs):,} designs to DB...")
    db.save_all(designs + ([new_round] if new_round else []) + [design_job, pool])

    report("Storing metrics table...")
    table = pd.DataFrame(table_rows)
    artifact = ChaperoneImportArtifact(
        source_rundir=run.rundir,
        final_step=run.final_step,
        step_names=list(run.step_names),
        metrics_table_path=storage.store_file_str(
            table.to_csv(index=False), os.path.join(pool_path, "chaperone_metrics.csv")
        ),
        columns=describe_columns(table),
        num_designs=len(designs),
    )
    db.save(
        ProjectArtifact(
            project_id=project_id,
            artifact_type=ChaperoneImportArtifact.artifact_type,
            design_job_id=design_job.id,
            artifact=artifact,
            author=username,
        )
    )
    return ChaperoneImportResult(
        pool=pool,
        num_designs=len(designs),
        artifact=artifact,
        conversion_warnings=conversion_warnings,
    )


def read_chaperone_rundir(rundir: str, final_step: str = None) -> ChaperoneRun:
    """Read a Chaperone run directory into the designs, structures and metrics it holds.

    Only the final step is read: its handler rows already carry the structures and the namespaced
    metrics of every step before it.

    :param rundir: Chaperone run directory, holding one directory per step
    :param final_step: Step to read; defaults to the furthest downstream one, see :func:`pick_final_step`
    :raises ValueError: when the directory holds no step, the step is unknown, or its rows are not
        handler rows
    """
    steps = discover_chaperone_steps(rundir)
    if not steps:
        raise ValueError(
            f"No Chaperone step found in '{rundir}': expected at least one <step>/{STEP_OUTPUT_FILE}. "
            f"Point this at the run directory (the engine's rundir), not at a step of it."
        )
    if final_step is None:
        final_step = pick_final_step(steps)
    if final_step not in steps:
        raise ValueError(f"Step '{final_step}' is not in '{rundir}', found: {', '.join(steps)}")

    frame = pd.read_parquet(steps[final_step])
    for column in ["structures", "metrics"]:
        if column not in frame.columns:
            raise ValueError(
                f"'{steps[final_step]}' is missing the '{column}' column, so it is not a Chaperone "
                f"handler table; found columns: {', '.join(map(str, frame.columns))}"
            )

    designs = []
    for _, row in frame.iterrows():
        structure_paths = read_structure_paths(row["structures"], rundir)
        metrics = flatten_metrics(parse_json_object(row["metrics"]))
        designs.append(
            ChaperoneDesign(
                chaperone_design_id=format_chaperone_design_id(row, structure_paths, final_step),
                structure_paths=structure_paths,
                metrics=metrics,
                sequences=parse_json_object(row.get("sequences")),
                motif_chains=read_motif_chains(row["structures"]),
            )
        )

    return ChaperoneRun(
        rundir=rundir,
        final_step=final_step,
        step_names=order_step_names(designs),
        designs=designs,
    )


def read_allowed_rundirs() -> list[str]:
    """Return the directories a non-admin may import a run from, as absolute paths.

    Configured per installation, since a path input reads the server's filesystem::

        plugins:
          ovo_chaperone:
            allowed_rundirs:
              - /shared/chaperone/runs

    An empty list means only admins may name a path; everyone else uploads instead.
    """
    configured = config.plugins.get("ovo_chaperone", {}).get("allowed_rundirs") or []
    if isinstance(configured, str):
        configured = [configured]
    return [os.path.abspath(os.path.expanduser(str(directory))) for directory in configured]


def check_rundir_path(path: str, *, allow_any: bool, allowed_dirs: list[str]) -> str | None:
    """Return why a named run directory may not be read, or None when it may.

    :param path: Directory the user named
    :param allow_any: Whether any readable directory is allowed, which admins are given
    :param allowed_dirs: Directories the path has to be inside of, see :func:`read_allowed_rundirs`
    """
    if not os.path.isdir(path):
        return f"Not a directory: `{path}`"
    if allow_any:
        return None
    if not allowed_dirs:
        return (
            "Importing from a server path is not configured on this installation. Upload the run "
            "directory instead, or ask an admin to list the directories to allow under "
            "`plugins.ovo_chaperone.allowed_rundirs` in `config.yml`."
        )
    # realpath on both sides, so a symlink or a '..' cannot leave an allowed directory
    resolved = os.path.realpath(path)
    for directory in allowed_dirs:
        allowed = os.path.realpath(directory)
        if resolved == allowed or resolved.startswith(allowed + os.sep):
            return None
    return (
        f"`{path}` is outside the allowed directories: "
        + ", ".join(f"`{directory}`" for directory in allowed_dirs)
    )


def find_chaperone_import(pool_id: str) -> ChaperoneImportArtifact | None:
    """Return the Chaperone import artifact of a pool, or None when the pool was not imported."""
    pool = db.Pool.get(id=pool_id)
    if not pool.design_job_id:
        return None
    artifacts = db.select(
        ProjectArtifact,
        design_job_id=pool.design_job_id,
        artifact_type=ChaperoneImportArtifact.artifact_type,
    )
    return artifacts[0].artifact if artifacts else None


def read_metrics_table(metrics_table_path: str) -> pd.DataFrame:
    """Read an import's metrics table out of storage, given its storage path."""
    return pd.read_csv(StringIO(storage.read_file_str(metrics_table_path)))


def find_design_row(table: pd.DataFrame, design_id: str):
    """Return the metrics row of one design, or None when the table holds none."""
    rows = table[table[DESIGN_ID_COLUMN] == design_id]
    return None if rows.empty else rows.iloc[0]


def read_design_structure_paths(
    table: pd.DataFrame, design_id: str, step_names: list[str]
) -> dict[str, str]:
    """Return the storage path of the structure each step published for one design, by step name."""
    row = find_design_row(table, design_id)
    if row is None:
        return {}
    return {
        step: row[f"{step}.{STRUCTURE_PATH_SUFFIX}"]
        for step in step_names
        if f"{step}.{STRUCTURE_PATH_SUFFIX}" in row
        and isinstance(row[f"{step}.{STRUCTURE_PATH_SUFFIX}"], str)
    }


def discover_chaperone_steps(rundir: str) -> dict[str, str]:
    """Return the path of every step's handler table in the run directory, by step name."""
    if not os.path.isdir(rundir):
        raise ValueError(f"Chaperone run directory '{rundir}' does not exist")
    return {
        entry: os.path.join(rundir, entry, STEP_OUTPUT_FILE)
        for entry in sorted(os.listdir(rundir))
        if os.path.isfile(os.path.join(rundir, entry, STEP_OUTPUT_FILE))
    }


def pick_final_step(steps: dict[str, str]) -> str:
    """Return the step whose rows accumulated the most, which is the furthest downstream one.

    A step's metrics hold one namespace per step that ran before it, so the step with the most of
    them is the last. Ties are refused rather than guessed.

    :raises ValueError: when two steps accumulated the same number of namespaces
    """
    namespaces = {}
    for step, path in steps.items():
        frame = pd.read_parquet(path, columns=["metrics"])
        namespaces[step] = len(
            {key for value in frame["metrics"] for key in parse_json_object(value)}
        )
    most = max(namespaces.values())
    candidates = sorted(step for step, count in namespaces.items() if count == most)
    if len(candidates) > 1:
        raise ValueError(
            f"Cannot tell which step is last, {', '.join(candidates)} each carry {most} metric "
            f"namespace(s): name the final step explicitly."
        )
    return candidates[0]


def read_structure_as_pdb(path: str) -> tuple[str, list[str]]:
    """Read a Chaperone structure file as a PDB string, converting mmCIF when needed.

    :return: The PDB string and any warnings the conversion reported
    """
    extension = os.path.splitext(path)[1].lower()
    if extension not in STRUCTURE_EXTENSIONS:
        raise ValueError(
            f"Structure '{path}' is not one of {', '.join(STRUCTURE_EXTENSIONS)}, so it cannot be imported"
        )
    with open(path, encoding="utf-8") as f:
        content = f.read()
    if extension == ".pdb":
        return content, []
    result = mmcif_to_pdb(content)
    return result.pdb_string, list(result.warnings)


def format_design_ids(run: ChaperoneRun, pool_id: str) -> dict[str, str]:
    """Return the OVO design id of every design of the run, by its Chaperone id.

    :raises ValueError: when two Chaperone ids produce the same OVO id
    """
    design_ids = {}
    for design in run.designs:
        design_id = f"ovo_{pool_id}_{re.sub(r'[^A-Za-z0-9_-]+', '_', design.chaperone_design_id)}"
        if design_id in design_ids.values():
            clash = [c for c, o in design_ids.items() if o == design_id]
            raise ValueError(
                f"Chaperone designs '{clash[0]}' and '{design.chaperone_design_id}' both map to OVO "
                f"design id '{design_id}': rename them in Chaperone before importing."
            )
        design_ids[design.chaperone_design_id] = design_id
    return design_ids


def describe_columns(table: pd.DataFrame) -> list[ChaperoneColumn]:
    """Describe every metric and structure column of a metrics table, in table order."""
    columns = []
    for key in table.columns:
        if key in (DESIGN_ID_COLUMN, CHAPERONE_ID_COLUMN):
            continue
        values = table[key].dropna()
        numeric = not key.endswith(f".{STRUCTURE_PATH_SUFFIX}") and bool(len(values)) and is_numeric(values)
        columns.append(ChaperoneColumn(key=key, step=key.split(".")[0], numeric=numeric))
    return columns


def read_structure_paths(structures, rundir: str) -> dict[str, str]:
    """Return the structure path each step contributed, by step name, keeping the last of a step.

    Chaperone writes absolute paths; a relative one is resolved against the run directory, so a run
    directory that was moved or packaged still reads.
    """
    # a parquet list column arrives as a numpy array, which has no truth value
    paths = {}
    for structure in [] if structures is None else list(structures):
        name, path = structure["name"], structure["path"]
        if name and path:
            path = str(path)
            paths[str(name)] = path if os.path.isabs(path) else os.path.join(rundir, path)
    return paths


def flatten_metrics(metrics: dict, prefix: str = "") -> dict[str, object]:
    """Flatten Chaperone's nested metrics into dotted keys, keeping a list as a JSON string.

    ``{"rfd3": {"n_clashing": {"ligand_clashes": 0}}}`` becomes
    ``{"rfd3.n_clashing.ligand_clashes": 0}``. A list value (a centre of mass, a per-residue array)
    is kept verbatim as JSON so nothing is dropped, and :func:`describe_columns` then reports the
    column as not plottable.
    """
    flat = {}
    for key, value in (metrics or {}).items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten_metrics(value, prefix=f"{dotted}."))
        elif isinstance(value, (list, tuple)):
            flat[dotted] = json.dumps(list(value))
        else:
            flat[dotted] = value
    return flat


def order_step_names(designs: list[ChaperoneDesign]) -> list[str]:
    """Return every step the designs mention, structures first in lineage order, then metric-only steps."""
    ordered = []
    for design in designs:
        for step in list(design.structure_paths) + [key.split(".")[0] for key in design.metrics]:
            if step not in ordered:
                ordered.append(step)
    return ordered


def format_chaperone_design_id(row, structure_paths: dict[str, str], final_step: str) -> str:
    """Return the design's Chaperone id, falling back to the stem of its final structure.

    Structure-producing blocks do not stamp a design id - only sequence-producing ones do - so a run
    that ends on a structure step identifies its designs by file name.
    """
    design_id = row.get("design_id")
    if isinstance(design_id, str) and design_id:
        return design_id
    path = structure_paths.get(final_step)
    if not path:
        raise ValueError(
            f"A row of step '{final_step}' has neither a design_id nor a structure, so it cannot be named"
        )
    return strip_structure_extension(os.path.basename(path))


def strip_structure_extension(filename: str) -> str:
    """Return the file name without its structure extension, including a ``.cif.gz`` one."""
    stem = filename[: -len(".gz")] if filename.lower().endswith(".gz") else filename
    base, extension = os.path.splitext(stem)
    return base if extension.lower() in STRUCTURE_EXTENSIONS else stem


def resolve_designed_chains(pdb_str: str, design: ChaperoneDesign, designed_chains: list[str]) -> list[str]:
    """Return the chain ids to treat as designed, which is what a design spec is built around.

    An explicit ``designed_chains`` wins. Otherwise the motif chains are taken as the fixed ones
    and every other chain of the structure is designed, see :func:`format_designed_chains`.
    """
    if designed_chains:
        return list(designed_chains)
    derived = format_designed_chains(design)
    if derived:
        return derived
    chains = sorted(get_sequences_from_pdb_str(pdb_str))
    return [chain for chain in chains if chain not in design.motif_chains] or chains


def format_designed_chains(design: ChaperoneDesign) -> list[str]:
    """Return the design's chains that are not motif, or an empty list if that cannot be told.

    Chaperone does not state which chain is the design, but a structure-producing block records
    the motif residues it kept fixed - for a binder run those are the target's. Every chain it
    reports a sequence for and did not fix is therefore designed. Returns an empty list for a run
    that carries no sequences, where the chains have to come from the structure itself.
    """
    if not design.sequences:
        return []
    designed = [chain for chain in sorted(design.sequences) if chain not in design.motif_chains]
    return designed or sorted(design.sequences)


def format_run_designed_chains(run: ChaperoneRun) -> list[str]:
    """Return the designed chains of a whole run, as the union over its designs."""
    return sorted({chain for design in run.designs for chain in format_designed_chains(design)})


def read_motif_chains(structures) -> set[str]:
    """Return the chains any step marked as motif, taking the chain letter of each residue id."""
    chains = set()
    for structure in [] if structures is None else list(structures):
        motif = (structure.get("metadata") or {}).get("motif")
        for residue in [] if motif is None else list(motif):
            residue = str(residue)
            if residue:
                chains.add(residue[0])
    return chains


def resolve_round(project_id: str, round_id: str, new_round_name: str, username: str) -> tuple[Round, str]:
    """Return the round to add the pool to, creating one when only a name was given.

    :return: The new round (None when an existing one was named) and the round id to use
    :raises ValueError: when neither or both were given, or the round belongs to another project
    """
    if bool(round_id) == bool(new_round_name):
        raise ValueError("Give either an existing round_id or a new_round_name, not both and not neither")
    if new_round_name:
        new_round = Round(project_id=project_id, name=new_round_name.strip(), author=username)
        return new_round, new_round.id
    rounds = db.select(Round, id=round_id)
    if not rounds:
        raise ValueError(f"Round '{round_id}' does not exist")
    if rounds[0].project_id != project_id:
        raise ValueError(f"Round '{round_id}' does not belong to project '{project_id}'")
    return None, round_id


def parse_json_object(value) -> dict:
    """Parse a handler column holding a JSON object, treating an absent value as an empty one."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    if isinstance(value, dict):
        return value
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}: {str(value)[:100]}")
    return parsed


def is_numeric(values: pd.Series) -> bool:
    """Whether every value parses as a number, so the column can go on a plot axis."""
    return not pd.to_numeric(values, errors="coerce").isna().any()
