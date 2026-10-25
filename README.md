# ovo-chaperone

Import a [Chaperone](https://rosettacommons.github.io/chaperone/) run into OVO, as a pool of designs
plus one table of its metrics.

This is a proof of concept of a **generic** pipeline import: it reads whatever steps and whatever
metrics a run happens to hold, so no Chaperone block or metric name appears anywhere in this plugin.

## How it works

A Chaperone step publishes `<rundir>/<step>/output.parquet`, and the handler rows of a step carry
everything its upstream steps produced - the accumulated `structures` list, the `metrics` of every
step namespaced by step name, and the `sequences`. Reading the final step therefore reads the whole
run.

| What | Where it lands in OVO |
|---|---|
| designs | one `Design` per row of the final step, its structure stored and set as `structure_path` |
| structures of every step | the pool's storage directory, one subdirectory per step |
| metrics of every step | a CSV in the same directory, referenced by a `ChaperoneImportArtifact` |
| the import itself | a `DesignJob` carrying `ChaperoneDesignWorkflow`, which routes the design visualization |

### Why metrics are not descriptors

Chaperone does not declare its metrics anywhere machine-readable: a block's `compile()` puts whatever
keys it likes into a JSON column, with no units, no direction and no stable vocabulary. Turning them
into OVO descriptors would mean hand-mapping every metric name, and each one would become a permanent
`descriptor_value.descriptor_key` - so an upstream rename would orphan stored values and need a key
migration.

Keeping them in an artifact costs the descriptor machinery: imported metrics do **not** appear in the
wide descriptor table, the descriptor plots, acceptance thresholds, ranking or the Excel export.

The plugin deliberately ships **no design view** to paper over that. Opening a single design shows
its structures per step with what that step measured, and the full table is a CSV in storage —
anything more is a reason to integrate with the OVO data model properly rather than to build a
parallel UI. The raw table is retained, so an import can be replayed into real descriptor values
once Chaperone exports a metric registry (name, unit, direction).

## Use it

```bash
pip install -e .
```

Then in the app:

1. **🎩 Chaperone Import** - pick the project, round and pool name, give it a run directory
   (upload one, or name a server-side path if you are an admin), choose the final step, and import.
   The designed chains are prefilled from the run and the page redirects to **Designs** afterwards,
   so a run cannot be imported twice by clicking again.
2. Open a single design for one structure per Chaperone step, each with what that step measured.

From Python:

```python
from ovo_chaperone.chaperone_import import read_chaperone_rundir, save_chaperone_import

run = read_chaperone_rundir("/path/to/chaperone/rundir")   # pure, reads nothing but the run
result = save_chaperone_import(run=run, project_id=..., pool_name="My run", round_id=...)
```

## Tests

```bash
pytest tests/unit_tests          # own OVO home in test-results/unit_tests
pytest tests/integration_tests   # your real OVO home, writes into the "OVO Tests" project
```

Run them separately: the integration tests refuse to start with `OVO_UNIT_TEST=1`, which the unit
conftest sets. The unit tests cover the import logic directly and drive the page with Streamlit's
`AppTest`, which needs the plugin installed so `st.navigation` knows the page.

Both import `tests/fixtures/mock_chaperone_rundir`, a Chaperone-shaped run built from real OVO
designs by `scripts/build_mock_rundir.py`. See its `PROVENANCE.md` for which structures and numbers
are real and which are synthesized.
