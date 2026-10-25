# Provenance of this fixture

Built by `scripts/build_mock_rundir.py` from real OVO designs, so the import is exercised on real
structures and real numbers. It is **not** the output of a real Chaperone run.

## Designs

| Chaperone design id | Source OVO design |
|---|---|
| `rfd3_binder_chunk0_binder_0_model_0__mpnn_1` | `ovo_avz_0030_cycle02` |
| `rfd3_binder_chunk0_binder_1_model_0__mpnn_1` | `ovo_avz_0124_cycle04` |
| `rfd3_binder_chunk0_binder_2_model_0__mpnn_1` | `ovo_avz_0299_cycle01` |

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
