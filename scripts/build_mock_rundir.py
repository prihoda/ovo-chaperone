"""Build the mock Chaperone run directory the tests import, from real OVO designs.

Run against an OVO home holding the source pool, from the repository root::

    python scripts/build_mock_rundir.py --pool-id avz --num-designs 3

The fixture mimics what a Chaperone ``rfd3 -> mpnn -> colabfold`` run publishes: one directory per
step, each with an ``output.parquet`` of handler rows, the per-design files beside it, and the
structures of every step. Structures and most metric values are real, taken from the source designs;
see the PROVENANCE.md the script writes for what is real and what is synthesized.

Structure paths are written **relative to the run directory** so the fixture stays portable; real
Chaperone writes absolute ones, which the importer handles as well.
"""

import argparse
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "mock_chaperone_rundir"

# descriptor keys of the source pool, by the Chaperone step that stands in for them
SOURCE_STRUCTURES = {
    "rfd3": "rfd_ee|rfdiffusion|backbone_structure_path",
    "mpnn": "rfd_ee|proteinmpnn_fastrelax|sequence_structure_path",
    "colabfold": "refolding|af2_primary|af2_structure_path",
}
SOURCE_METRICS = {
    "rfd3": {
        "radius_of_gyration": "rfd_ee|backbone_metrics|radius_of_gyration",
        "helix_fraction": "rfd_ee|backbone_metrics|pydssp_helix_percent",
        "loop_fraction": "rfd_ee|backbone_metrics|pydssp_loop_percent",
        "sheet_fraction": "rfd_ee|backbone_metrics|pydssp_strand_percent",
        "n_contact_interface": "rfd_ee|backbone_metrics|N_contact_interface",
    },
    "colabfold": {
        "plddt": "refolding|af2_primary|binder_plddt",
        "ptm": "refolding|af2_primary|ptm",
        "iptm": "refolding|af2_primary|iptm",
        "max_pae": "refolding|af2_primary|ipae",
        "binder_pae": "refolding|af2_primary|binder_pae",
        "target_aligned_binder_rmsd": "refolding|af2_primary|target_aligned_binder_rmsd",
    },
}
# fractions are stored as percentages in OVO and as fractions in Chaperone
PERCENT_METRICS = {"rfd3.helix_fraction", "rfd3.loop_fraction", "rfd3.sheet_fraction"}

# Chaperone's handler schema, see chaperone/src/chaperone/data/schema.py
METADATA_TYPE = pa.struct(
    [
        ("task", pa.string()),
        ("original_input", pa.string()),
        ("diffused_index_map", pa.string()),
        ("motif", pa.list_(pa.string())),
        ("ligand", pa.list_(pa.string())),
    ]
)
STRUCTURE_TYPE = pa.struct(
    [
        ("name", pa.string()),
        ("path", pa.string()),
        ("path_handle", pa.string()),
        ("store_path", pa.string()),
        ("storage_format", pa.string()),
        ("metadata", METADATA_TYPE),
    ]
)
HANDLER_SCHEMA = pa.schema(
    [
        ("structures", pa.list_(STRUCTURE_TYPE)),
        ("metrics", pa.string()),
        ("design_id", pa.string()),
        ("parent_design_id", pa.string()),
        ("artifact_kind", pa.string()),
        ("sequences", pa.string()),
    ]
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-id", default="avz", help="Source pool to read real designs from")
    parser.add_argument("--num-designs", type=int, default=3, help="How many designs to include")
    parser.add_argument("--out", default=str(FIXTURE_DIR), help="Fixture directory to write")
    args = parser.parse_args()

    sources = read_source_designs(args.pool_id, args.num_designs)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    write_fixture(sources, out)
    print(f"Wrote {sum(1 for _ in out.rglob('*') if _.is_file())} files to {out}")


def read_source_designs(pool_id: str, num_designs: int) -> list[dict]:
    """Read the structures, sequence and metrics of real designs out of the OVO database."""
    from ovo import db, storage
    from ovo.core.logic import descriptor_logic

    design_ids = sorted(design.id for design in db.Design.select(pool_id=pool_id, accepted=True))
    if len(design_ids) < num_designs:
        raise ValueError(f"Pool '{pool_id}' has only {len(design_ids)} accepted designs")
    design_ids = design_ids[:num_designs]

    keys = list(SOURCE_STRUCTURES.values()) + [k for step in SOURCE_METRICS.values() for k in step.values()]
    values = descriptor_logic.get_wide_descriptor_table(
        design_ids=design_ids, descriptor_keys=keys, human_readable=False
    )
    specs = {design.id: design.spec for design in db.Design.select(id__in=design_ids)}

    sources = []
    for design_id in design_ids:
        row = values.loc[design_id]
        sources.append(
            dict(
                source_design_id=design_id,
                structures={
                    step: storage.read_file_str(row[key]) for step, key in SOURCE_STRUCTURES.items()
                },
                metrics={
                    f"{step}.{name}": float(row[key])
                    for step, metrics in SOURCE_METRICS.items()
                    for name, key in metrics.items()
                },
                # every chain, as Chaperone reports a sequence per protein chain of the complex
                sequences=read_chain_sequences(row[SOURCE_STRUCTURES["colabfold"]], storage),
                designed_chains=sorted(
                    {chain_id for chain in specs[design_id].chains for chain_id in chain.chain_ids}
                ),
            )
        )
    return sources


def read_chain_sequences(storage_path: str, storage) -> dict[str, str]:
    """Return the one-letter sequence of each chain of a stored PDB, read off its CA atoms."""
    three_to_one = {
        "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
        "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
        "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    }
    sequences: dict[str, str] = {}
    for line in storage.read_file_str(storage_path).splitlines():
        if line.startswith("ATOM") and line[12:16].strip() == "CA":
            sequences.setdefault(line[21], "")
            sequences[line[21]] += three_to_one.get(line[17:20].strip().upper(), "X")
    return sequences


def write_fixture(sources: list[dict], out: Path):
    """Write the whole mock run directory: one directory per step, plus provenance."""
    designs = [build_design(index, source) for index, source in enumerate(sources)]

    write_rfd3_step(out / "rfd3", designs)
    write_mpnn_step(out / "mpnn", designs)
    write_colabfold_step(out / "colabfold", designs)
    write_logs(out / "logs", designs)
    write_provenance(out / "PROVENANCE.md", designs)


def build_design(index: int, source: dict) -> dict:
    """Return everything the three steps need for one design, with its Chaperone names and paths."""
    stem = f"rfd3_binder_chunk0_binder_{index}_model_0"
    design_id = f"{stem}__mpnn_1"
    return dict(
        index=index,
        stem=stem,
        design_id=design_id,
        source=source,
        # relative to the run directory, see the module docstring
        rfd3_path=f"rfd3/chunk_0/{stem}.pdb",
        mpnn_path=f"mpnn/chunk_0/packed/{stem}__0_1.pdb",
        colabfold_path=f"colabfold/chunk_0/outputs/{design_id}_model.pdb",
        # the residues RFD3 kept fixed, which for a binder run are the target's
        motif=[
            f"{chain}{index}"
            for chain, sequence in sorted(source["sequences"].items())
            if chain not in source["designed_chains"]
            for index in range(1, len(sequence) + 1)
        ],
        # real values, plus the nested and list-valued metrics Chaperone also reports, so the import
        # is exercised on both (see PROVENANCE.md)
        rfd3_metrics={
            **{k.split(".", 1)[1]: percent_to_fraction(k, v) for k, v in source["metrics"].items() if k.startswith("rfd3.")},
            "n_chainbreaks": 0,
            "num_residues": sum(len(s) for s in source["sequences"].values()),
            "n_clashing": {"interresidue_clashes_w_backbone": 0, "interresidue_clashes_w_sidechain": 0},
            "fixed_com": [0.0, 0.0, 0.0],
        },
        mpnn_metrics={
            "id": 1,
            "T": 0.1,
            "seed": 37,
            "overall_confidence": 0.5123,
            "ligand_confidence": 0.0,
            "seq_rec": 0.3114,
            "output_mode": "structures",
        },
        colabfold_metrics={
            **{k.split(".", 1)[1]: v for k, v in source["metrics"].items() if k.startswith("colabfold.")},
            "rank": 1,
            "model_name": "alphafold2_multimer_v3_model_1",
        },
    )


def percent_to_fraction(key: str, value: float) -> float:
    return value / 100 if key in PERCENT_METRICS else value


def write_rfd3_step(step_dir: Path, designs: list[dict]):
    chunk = step_dir / "chunk_0"
    rows = []
    for design in designs:
        write_structure(chunk / f"{design['stem']}.pdb", design["source"]["structures"]["rfd3"])
        write_json(
            chunk / f"{design['stem']}.json",
            {
                "specification": {"input": "files/target.pdb", "contigs": "A1-100/0 60-60"},
                "diffused_index_map": {},
                "metrics": design["rfd3_metrics"],
                "ckpt_path": "rfd3_latest.ckpt",
                "seed": 1000 + design["index"],
            },
        )
        rows.append(
            handler_row(
                structures=[structure("rfd3", design["rfd3_path"], design["motif"])],
                metrics={"rfd3": design["rfd3_metrics"]},
                artifact_kind="structure",
            )
        )
    write_manifest(chunk, "rfd3", ["rfd3", "design", "inputs=inputs/binder.json"])
    write_json(chunk / "inputs" / "binder.json", {"binder": {"contigs": "A1-100/0 60-60"}})
    write_handler(chunk / "output.parquet", rows)
    write_handler(step_dir / "output.parquet", rows)


def write_mpnn_step(step_dir: Path, designs: list[dict]):
    chunk = step_dir / "chunk_0"
    rows = []
    for design in designs:
        write_structure(chunk / "packed" / f"{design['stem']}__0_1.pdb", design["source"]["structures"]["mpnn"])
        sequence = ":".join(design["source"]["sequences"][c] for c in sorted(design["source"]["sequences"]))
        (chunk / "seqs").mkdir(parents=True, exist_ok=True)
        (chunk / "seqs" / f"{design['stem']}.fa").write_text(
            f">{design['stem']}, id=1, T=0.100, seed=37, overall_confidence=0.5123, seq_rec=0.3114\n"
            f"{sequence}\n"
        )
        rows.append(
            handler_row(
                structures=[
                    structure("rfd3", design["rfd3_path"], design["motif"]),
                    structure("mpnn", design["mpnn_path"], design["motif"]),
                ],
                metrics={"rfd3": design["rfd3_metrics"], "mpnn": design["mpnn_metrics"]},
                design_id=design["design_id"],
                parent_design_id=design["stem"],
                artifact_kind="structure",
                sequences=design["source"]["sequences"],
            )
        )
    write_manifest(chunk, "mpnn", ["python", "/opt/LigandMPNN/run.py", "--config_json", "inputs/mpnn.json"])
    write_json(chunk / "inputs" / "mpnn.json", {"model_type": "protein_mpnn", "temperature": 0.1})
    write_handler(chunk / "output.parquet", rows)
    write_handler(step_dir / "output.parquet", rows)


def write_colabfold_step(step_dir: Path, designs: list[dict]):
    chunk = step_dir / "chunk_0"
    rows = []
    for design in designs:
        model = "alphafold2_multimer_v3_model_1_seed_000"
        write_structure(
            chunk / "outputs" / f"{design['design_id']}_unrelaxed_rank_001_{model}.pdb",
            design["source"]["structures"]["colabfold"],
        )
        # Chaperone symlinks this name for lineage matching; a committed fixture keeps a real copy
        write_structure(
            chunk / "outputs" / f"{design['design_id']}_model.pdb",
            design["source"]["structures"]["colabfold"],
        )
        write_json(
            chunk / "outputs" / f"{design['design_id']}_scores_rank_001_{model}.json",
            {
                "plddt": [design["colabfold_metrics"]["plddt"]],
                "ptm": design["colabfold_metrics"]["ptm"],
                "iptm": design["colabfold_metrics"]["iptm"],
                "max_pae": design["colabfold_metrics"]["max_pae"],
            },
        )
        rows.append(
            handler_row(
                structures=[
                    structure("rfd3", design["rfd3_path"], design["motif"]),
                    structure("mpnn", design["mpnn_path"], design["motif"]),
                    structure("colabfold", design["colabfold_path"], design["motif"]),
                ],
                metrics={
                    "rfd3": design["rfd3_metrics"],
                    "mpnn": design["mpnn_metrics"],
                    "colabfold": design["colabfold_metrics"],
                },
                design_id=design["design_id"],
                parent_design_id=design["stem"],
                artifact_kind="structure",
                sequences=design["source"]["sequences"],
            )
        )
    write_manifest(chunk, "colabfold", ["colabfold_batch", "inputs", "outputs", "--msa-mode", "single_sequence"])
    write_handler(chunk / "output.parquet", rows)
    write_handler(step_dir / "output.parquet", rows)


def write_logs(log_dir: Path, designs: list[dict]):
    log_dir.mkdir(parents=True, exist_ok=True)
    for step in ["rfd3", "mpnn", "colabfold"]:
        (log_dir / f"{step}.log").write_text(f"[chunk_0] {step}: {len(designs)} design(s) written\n")


def write_provenance(path: Path, designs: list[dict]):
    rows = "\n".join(
        f"| `{d['design_id']}` | `{d['source']['source_design_id']}` |" for d in designs
    )
    path.write_text(
        f"""# Provenance of this fixture

Built by `scripts/build_mock_rundir.py` from real OVO designs, so the import is exercised on real
structures and real numbers. It is **not** the output of a real Chaperone run.

## Designs

| Chaperone design id | Source OVO design |
|---|---|
{rows}

The source designs come from the `OVO Publication Examples 1` project and were produced by an
RFdiffusion1 -> ProteinMPNN+FastRelax -> AlphaFold2-initial-guess pipeline. The fixture presents them
under the Chaperone step names `rfd3`, `mpnn` and `colabfold`, because what is under test is the
import of an arbitrary Chaperone pipeline, not the equivalence of those tools.

## Real

- every structure file (backbone, sequence design, refolded prediction), as published by OVO
- `rfd3.radius_of_gyration`, `rfd3.helix_fraction`, `rfd3.loop_fraction`, `rfd3.sheet_fraction`,
  `rfd3.n_contact_interface` (the DSSP percentages converted to fractions)
- `colabfold.plddt`, `.ptm`, `.iptm`, `.max_pae`, `.binder_pae`, `.target_aligned_binder_rmsd`
- the sequences of each design

## Synthesized

- `mpnn.*` - the source pipeline reports no MPNN confidences
- `rfd3.n_chainbreaks`, `rfd3.num_residues`, `rfd3.n_clashing.*` (nested, to exercise flattening),
  `rfd3.fixed_com` (a list, to exercise the JSON fallback)
- `colabfold.rank`, `colabfold.model_name` (a string, to exercise non-numeric column detection)
- every file that is not a structure: the manifests, the per-design JSON, the FASTA, the logs

## Deviations from a real Chaperone run

- structure paths in the parquet are **relative** to the run directory, so the fixture is portable;
  real Chaperone writes absolute paths and the importer accepts both
- `colabfold/chunk_0/outputs/<design>_model.pdb` is a real copy, where Chaperone writes a symlink
- the parquet is written with pyarrow rather than polars, with Chaperone's schema declared explicitly
- no `output.csv` side-car: Chaperone only writes one when a step is asked for `debug_csv=True`
"""
    )


def handler_row(
    structures: list[dict],
    metrics: dict,
    design_id: str = None,
    parent_design_id: str = None,
    artifact_kind: str = "structure",
    sequences: dict = None,
) -> dict:
    return {
        "structures": structures,
        "metrics": json.dumps(metrics),
        "design_id": design_id,
        "parent_design_id": parent_design_id,
        "artifact_kind": artifact_kind,
        "sequences": json.dumps(sequences) if sequences else None,
    }


def structure(name: str, path: str, motif: list[str] = None) -> dict:
    return {
        "name": name,
        "path": path,
        "path_handle": None,
        "store_path": None,
        "storage_format": "raw",
        "metadata": {
            "task": "binder",
            "original_input": "files/target.pdb",
            "diffused_index_map": None,
            "motif": motif or None,
            "ligand": None,
        },
    }


def write_structure(path: Path, content: str) -> None:
    """Write one structure file, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_handler(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=HANDLER_SCHEMA), path)


def write_manifest(chunk: Path, name: str, command: list[str]):
    write_json(
        chunk / "manifest.json",
        {"name": name, "command": command, "cwd": str(chunk), "command_str": " ".join(command)},
    )


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
