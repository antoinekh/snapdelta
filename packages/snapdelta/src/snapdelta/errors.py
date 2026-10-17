"""Errors that the CLI shows as a message, without a traceback."""


class SnapdeltaError(Exception):
    """A fault of the input or of the use, not a bug."""


class PluginError(SnapdeltaError):
    """A plugin that cannot load, or that is not of the kind of its entry point group."""


class LoadError(SnapdeltaError):
    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


class UnknownFileError(LoadError):
    """A file whose content the source does not know."""


class SchemaVersionError(SnapdeltaError):
    """A saved file of a schema version that this snapdelta does not read."""


class NoCommonModelError(SnapdeltaError):
    """Two loads with no model in common."""


class NotFoundError(SnapdeltaError):
    """A snapshot or output reference that matches no file."""


class FetchError(LoadError):
    """One or more tasks of a parallel fetch failed; `failures` maps each failed task to its error."""

    def __init__(self, failures: dict[str, Exception]) -> None:
        super().__init__("parallel fetch", "; ".join(f"{name}: {error}" for name, error in failures.items()))
        self.failures = failures
