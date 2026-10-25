"""Descriptors of the Chaperone plugin: deliberately none.

Chaperone does not declare its metrics anywhere machine-readable - a block's ``compile()`` puts
whatever keys it likes into a JSON column, with no units, no direction and no stable vocabulary - so
there is no list to generate OVO :class:`~ovo.core.database.models.Descriptor` objects from. An
import keeps its metrics in a :class:`~ovo_chaperone.models_chaperone.ChaperoneImportArtifact`
instead, where only :mod:`ovo_chaperone.visualize_chaperone` reads them, one design at a time.

Declaring a descriptor per metric would make the values reachable by OVO's descriptor table, plots,
acceptance thresholds and ranking. It would also mean every Chaperone metric name becoming a
permanent ``descriptor_value.descriptor_key``, so an upstream rename would orphan stored values and
need a key migration. That trade is worth making once Chaperone exports a metric registry (name,
unit, direction) to generate the descriptors from; until then this list stays empty.
"""

from ovo.core.database.models import Descriptor

DESCRIPTORS: list[Descriptor] = []

PRESETS: list[dict] = []
