# ovo-chaperone

Import a [Chaperone](https://rosettacommons.github.io/chaperone/) run into [OVO](https://github.com/MSDLLCpapers/ovo), as a pool of designs
plus one table of its metrics.

This is a proof of concept of a **generic** pipeline import: it reads whatever steps and whatever
metrics a run happens to hold, so no Chaperone block or metric name appears anywhere in this plugin.

## Setup

```bash
# Install the plugin (and OVO if not installed already):
pip install git+https://github.com/prihoda/ovo-chaperone

# Initialize ovo home dir (if not already initialized)
ovo init home

# Run ovo web app
ovo app
```

In the app, open **Designs → Upload designs** and pick the **Chaperone run** tab. Give it a run
directory, choose the final step, name the pool, and import. The designed chains are prefilled from
the run, and importing closes the dialog and selects the new pool, so one run cannot be imported
twice. Open a single design afterwards for one structure per Chaperone step, each with what that
step measured.

The tab is contributed as an `ovo.design_upload` extension point, so it sits beside OVO's own
**File upload** tab rather than on a page of its own.

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

### Why metrics are not descriptors (yet?)

Chaperone does not declare its available metrics anywhere machine-readable. 
Turning them into OVO descriptors would mean hand-mapping every metric name. 
So for now, we keep all metrics in a CSV artifact, and the design page shows them in a table.

Keeping them in an artifact costs the descriptor machinery: imported metrics do **not** appear in the
wide descriptor table, the descriptor plots, acceptance thresholds, ranking or the Excel export.

### Importing from a server path

Everyone sees the **Server path** tab, but a path is only read when it is allowed. Admins may name
any directory. Everyone else is held to the directories listed in the plugin's config section, and
can name none until one is listed:

```yaml
# config.yml
plugins:
  ovo_chaperone:
    allowed_rundirs:
      - /shared/chaperone/runs
```

Symlinks and `..` are resolved before the check, so a path cannot leave an allowed directory.
Uploading a run directory always works and needs no configuration.

From Python:

```python
from ovo_chaperone.logic.chaperone_logic import read_chaperone_rundir, save_chaperone_import

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
