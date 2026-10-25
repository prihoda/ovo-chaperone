"""Import the mock Chaperone run into the real OVO home, and read it back the way the app does."""

from tests.fixture_paths import MOCK_DESIGN_IDS, MOCK_RUNDIR, MOCK_STEPS


def test_chaperone_import_into_ovo_home(project_data):
    from ovo import db, storage
    from ovo.core.database.models import ProjectArtifact
    from ovo_chaperone.logic.chaperone_logic import (
        read_chaperone_rundir,
        read_metrics_table,
        save_chaperone_import,
    )
    from ovo_chaperone.database.models_chaperone import (
        DESIGN_ID_COLUMN,
        ChaperoneDesignWorkflow,
        ChaperoneImportArtifact,
    )

    project, project_round = project_data

    run = read_chaperone_rundir(MOCK_RUNDIR)
    result = save_chaperone_import(
        run=run,
        project_id=project.id,
        pool_name="Chaperone rfd3 mpnn colabfold",
        round_id=project_round.id,
        description="Mock Chaperone run imported by the integration test",
    )
    assert result.num_designs == len(MOCK_DESIGN_IDS)

    # --- what the Jobs and Designs pages read ---
    pool = db.Pool.get(id=result.pool.id)
    assert pool.round_id == project_round.id
    assert pool.processed is True
    job = db.DesignJob.get(id=pool.design_job_id)
    assert job.job_result is True, "an import is a finished job, so the pool is not shown as running"
    assert job.workflow.is_instance(ChaperoneDesignWorkflow)
    # the workflow exists to route the design visualization, and refuses to be submitted
    assert ChaperoneDesignWorkflow.visualize_single_design_structures is not None
    try:
        job.workflow.get_pipeline_name()
        raise AssertionError("a Chaperone import must not be submittable")
    except NotImplementedError as e:
        assert "cannot be submitted" in str(e)

    designs = db.Design.select(pool_id=pool.id)
    assert len(designs) == len(MOCK_DESIGN_IDS)

    # --- what the Chaperone design view reads ---
    artifact = db.select(
        ProjectArtifact, design_job_id=job.id, artifact_type=ChaperoneImportArtifact.artifact_type
    )[0].artifact
    table = read_metrics_table(artifact.metrics_table_path)
    assert sorted(table[DESIGN_ID_COLUMN]) == sorted(design.id for design in designs)

    # every design resolves one structure per Chaperone step, which is what the visualization shows
    for design in designs:
        row = table[table[DESIGN_ID_COLUMN] == design.id].iloc[0]
        for step in MOCK_STEPS:
            structure = storage.read_file_str(row[f"{step}.structure_path"])
            assert "ATOM" in structure, f"{design.id} has no atoms in its {step} structure"
        assert row["colabfold.structure_path"] == design.structure_path

    # the real metrics survived the round trip through the CSV
    assert table["colabfold.plddt"].between(0, 100).all()
    assert table["colabfold.iptm"].between(0, 1).all()
    assert table["rfd3.helix_fraction"].between(0, 1).all()
