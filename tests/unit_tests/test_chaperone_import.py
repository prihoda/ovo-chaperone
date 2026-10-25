"""End-to-end test of reading a Chaperone run directory and saving it as a pool of designs."""

import json
import os

import pytest

from tests.fixture_paths import MOCK_DESIGN_IDS, MOCK_RUNDIR, MOCK_STEPS


def test_chaperone_import(project_data, tmp_path):
    from ovo import db, storage
    from ovo.core.database.models import DescriptorValue, ProjectArtifact
    from ovo_chaperone.logic.chaperone_logic import (
        discover_chaperone_steps,
        find_chaperone_import,
        flatten_metrics,
        format_designed_chains,
        format_run_designed_chains,
        pick_final_step,
        read_chaperone_rundir,
        get_design_structure_paths,
        read_metrics_table,
        save_chaperone_import,
    )
    from ovo_chaperone.database.models_chaperone import (
        CHAPERONE_ID_COLUMN,
        DESIGN_ID_COLUMN,
        ChaperoneDesignWorkflow,
        ChaperoneImportArtifact,
    )

    project, project_round, _ = project_data

    # --- discovering the steps of a run directory ---
    steps = discover_chaperone_steps(MOCK_RUNDIR)
    assert sorted(steps) == sorted(MOCK_STEPS)
    # colabfold accumulated all three metric namespaces, so it is the final step
    assert pick_final_step(steps) == "colabfold"

    # --- reading it, which reads only the final step ---
    run = read_chaperone_rundir(MOCK_RUNDIR)
    assert run.final_step == "colabfold"
    assert [d.chaperone_design_id for d in run.designs] == MOCK_DESIGN_IDS
    # structures of every step are carried by the final step's rows, in lineage order
    assert list(run.step_names)[:3] == MOCK_STEPS
    design = run.designs[0]
    assert sorted(design.structure_paths) == sorted(MOCK_STEPS)
    for step, path in design.structure_paths.items():
        assert os.path.isabs(path) and os.path.isfile(path), f"{step}: {path}"
    # Chaperone reports a sequence per protein chain, so the designed chain cannot be told from
    # those alone: the motif metadata says which chains were kept fixed
    assert sorted(design.sequences) == ["A", "B"]
    assert design.motif_chains == {"B"}
    assert format_designed_chains(design) == ["A"]
    assert format_run_designed_chains(run) == ["A"]

    # metrics are namespaced per step, nested keys flattened, list values kept as JSON
    assert design.metrics["colabfold.plddt"] > 0
    assert design.metrics["rfd3.n_clashing.interresidue_clashes_w_backbone"] == 0
    assert json.loads(design.metrics["rfd3.fixed_com"]) == [0.0, 0.0, 0.0]
    assert design.metrics["colabfold.model_name"] == "alphafold2_multimer_v3_model_1"
    assert design.metrics["mpnn.seed"] == 37

    # --- saving it as a pool ---
    result = save_chaperone_import(
        run=run,
        project_id=project.id,
        pool_name="Chaperone import unit test",
        round_id=project_round.id,
        description="Imported by the unit test",
    )
    assert result.num_designs == 3
    assert not result.conversion_warnings, "the fixture holds PDB, so nothing should be converted"

    designs = db.Design.select(pool_id=result.pool.id)
    assert len(designs) == 3
    assert sorted(d.id for d in designs) == sorted(
        f"ovo_{result.pool.id}_{chaperone_id}" for chaperone_id in MOCK_DESIGN_IDS
    )
    for saved in designs:
        # the final step's structure is the design's, and every step's is in storage
        assert saved.structure_path and storage.read_file_str(saved.structure_path).startswith(
            ("ATOM", "HEADER", "REMARK", "MODEL", "TITLE", "CRYST")
        )
        assert [chain.chain_ids for chain in saved.spec.chains] == [["A"]]

    # the pool's job carries the import, so its designs get the Chaperone visualization
    pool = db.Pool.get(id=result.pool.id)
    job = db.DesignJob.get(id=pool.design_job_id)
    assert job.workflow.is_instance(ChaperoneDesignWorkflow)
    assert job.workflow.step_names == MOCK_STEPS and job.workflow.final_step == "colabfold"

    # --- the metrics live in the artifact, not in descriptor values ---
    assert not db.select(DescriptorValue, design_id__in=[d.id for d in designs]), (
        "a Chaperone import must not write descriptor values, see descriptors_chaperone"
    )
    artifacts = db.select(
        ProjectArtifact,
        project_id=project.id,
        design_job_id=job.id,
        artifact_type=ChaperoneImportArtifact.artifact_type,
    )
    assert len(artifacts) == 1
    artifact = artifacts[0].artifact
    assert isinstance(artifact, ChaperoneImportArtifact), f"loaded as {type(artifact).__name__}"
    assert artifact.num_designs == 3
    assert artifact.step_names == MOCK_STEPS
    assert artifact.get_storage_paths() == [artifact.metrics_table_path]

    # numeric columns are detected from the values, so they can go on a plot axis
    assert "colabfold.plddt" in artifact.numeric_keys()
    assert "colabfold.model_name" not in artifact.numeric_keys(), "a string metric is not plottable"
    assert "rfd3.fixed_com" not in artifact.numeric_keys(), "a list metric is not plottable"
    assert "colabfold.structure_path" not in artifact.numeric_keys()
    assert set(artifact.keys_of_step("mpnn")) >= {"mpnn.seq_rec", "mpnn.structure_path"}

    # --- the stored table is readable and complete, through what the design view reads it with ---
    assert find_chaperone_import(result.pool.id).metrics_table_path == artifact.metrics_table_path
    assert find_chaperone_import(project_data[2].id) is None, "a pool with no import has none"
    table = read_metrics_table(artifact.metrics_table_path)
    assert len(table) == 3
    assert list(table[CHAPERONE_ID_COLUMN]) == MOCK_DESIGN_IDS
    assert sorted(table[DESIGN_ID_COLUMN]) == sorted(d.id for d in designs)
    for step in MOCK_STEPS:
        column = f"{step}.structure_path"
        assert column in table.columns
        for path in table[column]:
            assert storage.read_file_str(path), f"{column} is not in storage: {path}"

    # the design visualization reads one structure per step out of that table
    for saved in designs:
        paths = get_design_structure_paths(table, saved.id, artifact.step_names)
        assert sorted(paths) == sorted(MOCK_STEPS)
        assert paths[artifact.final_step] == saved.structure_path
    assert get_design_structure_paths(table, "ovo_nope_0001", artifact.step_names) == {}

    # --- refusals ---
    with pytest.raises(ValueError, match="No Chaperone step found"):
        read_chaperone_rundir(str(tmp_path))
    with pytest.raises(ValueError, match="is not in"):
        read_chaperone_rundir(MOCK_RUNDIR, final_step="esmfold2")
    with pytest.raises(ValueError, match="does not exist"):
        read_chaperone_rundir(str(tmp_path / "missing"))

    # flattening is pure and total: nothing of a nested metrics dict is dropped
    assert flatten_metrics({"a": {"b": 1, "c": {"d": 2}}, "e": [1, 2]}) == {
        "a.b": 1,
        "a.c.d": 2,
        "e": "[1, 2]",
    }


def test_check_rundir_path(tmp_path):
    """Who may name a server path, and which paths they may name."""
    from ovo_chaperone.logic.chaperone_logic import check_rundir_path

    allowed = tmp_path / "allowed"
    (allowed / "runs" / "one").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    allowed_dirs = [str(allowed)]

    # an admin may name any directory, and no one may name something that is not one
    assert check_rundir_path(str(elsewhere), allow_any=True, allowed_dirs=[]) is None
    assert "Not a directory" in check_rundir_path(
        str(tmp_path / "missing"), allow_any=True, allowed_dirs=[]
    )

    # without configuration a non-admin may name nothing, and is told what to do instead
    refusal = check_rundir_path(str(elsewhere), allow_any=False, allowed_dirs=[])
    assert "allowed_rundirs" in refusal and "Upload" in refusal

    # with configuration, inside is allowed and outside is refused
    assert check_rundir_path(str(allowed), allow_any=False, allowed_dirs=allowed_dirs) is None
    assert check_rundir_path(str(allowed / "runs" / "one"), allow_any=False, allowed_dirs=allowed_dirs) is None
    assert "outside the allowed directories" in check_rundir_path(
        str(elsewhere), allow_any=False, allowed_dirs=allowed_dirs
    )

    # a sibling whose name merely starts with an allowed one is not inside it
    sibling = tmp_path / "allowed_too"
    sibling.mkdir()
    assert "outside the allowed directories" in check_rundir_path(
        str(sibling), allow_any=False, allowed_dirs=allowed_dirs
    )

    # neither '..' nor a symlink can leave an allowed directory
    assert "outside the allowed directories" in check_rundir_path(
        str(allowed / "runs" / ".." / ".." / "elsewhere"), allow_any=False, allowed_dirs=allowed_dirs
    )
    escape = allowed / "escape"
    escape.symlink_to(elsewhere, target_is_directory=True)
    assert "outside the allowed directories" in check_rundir_path(
        str(escape), allow_any=False, allowed_dirs=allowed_dirs
    )


def test_get_allowed_rundirs(monkeypatch, tmp_path):
    """The allowed directories come from the plugin's own config section, as absolute paths."""
    from ovo import config
    from ovo_chaperone.logic.chaperone_logic import get_allowed_rundirs

    monkeypatch.setitem(config.plugins, "ovo_chaperone", {})
    assert get_allowed_rundirs() == []

    monkeypatch.setitem(config.plugins, "ovo_chaperone", {"allowed_rundirs": [str(tmp_path), "~/runs"]})
    assert get_allowed_rundirs() == [str(tmp_path), os.path.expanduser("~/runs")]

    # a single directory may be written without a list
    monkeypatch.setitem(config.plugins, "ovo_chaperone", {"allowed_rundirs": str(tmp_path)})
    assert get_allowed_rundirs() == [str(tmp_path)]
