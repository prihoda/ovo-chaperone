plugin = dict(
    extension_points={
        "ovo.workflow_page": [
            dict(
                module_name="ovo_chaperone",
                title="🎩 Chaperone Import",
                path="ovo_chaperone.pages.chaperone_import",
                category="Import",
                labels=["Chaperone", "Import"],
                short_description="Import an existing Chaperone run directory as a pool of designs",
                thumbnail=None,
            ),
        ],
        # No "ovo.design_view": the imported metrics are not descriptor values, and a plugin-owned
        # view of them would only make that detour comfortable. Single designs are visualized
        # through ChaperoneDesignWorkflow.visualize_single_design_structures.
        # Intentionally empty, see the module docstring
        "ovo.descriptors": ["ovo_chaperone.descriptors_chaperone"],
    },
    submodule_names=[
        # registers ChaperoneDesignWorkflow and ChaperoneImportArtifact
        "ovo_chaperone.models_chaperone",
    ],
)
