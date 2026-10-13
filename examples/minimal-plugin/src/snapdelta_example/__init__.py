"""The snapdelta example plugin: the smallest complete plugin, to read first and to copy.

How the parts fit together:

1. `models.py`: the diffsync models, what a compare looks at.
2. `source.py`: `ExampleSource`, the source `example`: it reads JSON files and fills the models.
3. `live.py`: `ExampleLiveSource`, the source `example-live`: it fetches the same data from a simulated device,
   with its settings `ExampleLiveSettings`.
4. `pyproject.toml`: the entry points `example` and `example-live` in the group `snapdelta.sources`; this is how
   snapdelta finds the sources once the package is installed (`snapdelta sources` lists them).

Then snapdelta does the rest: one `load()` of a source for one target gives a snapshot, the store saves it, and a
diff compares two snapshots model by model.
"""

from .live import ExampleLiveSettings, ExampleLiveSource
from .source import ExampleSource

__all__ = ["ExampleLiveSettings", "ExampleLiveSource", "ExampleSource"]
