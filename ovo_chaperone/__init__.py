plugin = dict(
    extension_points={
        # one more source in OVO's design upload dialog, shown as a tab beside "File upload"
        "ovo.design_upload": [
            dict(
                title="Chaperone run",
                path="ovo_chaperone.app.chaperone_import:show_chaperone_upload",
            ),
        ],
        # No "ovo.design_view": the imported metrics are not descriptor values, and a plugin-owned
        # view of them would only make that detour comfortable. Single designs are visualized
        # through ChaperoneDesignWorkflow.visualize_single_design_structures.
        # Intentionally empty, see the module docstring
        "ovo.descriptors": ["ovo_chaperone.database.descriptors_chaperone"],
    },
    submodule_names=[
        # registers ChaperoneDesignWorkflow and ChaperoneImportArtifact
        "ovo_chaperone.database.models_chaperone",
    ],
)
