"""What the web app uses at run time: the settings and the store, reloaded after the settings change."""

from dataclasses import dataclass, field
from pathlib import Path

from snapdelta.settings import Settings, load_settings
from snapdelta.store import Store, open_store_from


@dataclass
class AppState:
    settings: Settings
    store: Store
    settings_option: Path | None = None
    store_option: str | None = None
    retired: list[Store] = field(default_factory=list)

    def reload(self) -> None:
        """Read the settings again; the state changes only when the new store opens."""
        settings = load_settings(self.settings_option or self.settings.file, self.store_option)
        store = open_store_from(settings) if settings.store != self.settings.store else self.store
        previous = self.store
        self.settings, self.store = settings, store
        if previous is not store:
            # A job or a request that started before can still use it: close it with the server.
            self.retired.append(previous)

    def close(self) -> None:
        for store in [*self.retired, self.store]:
            store.close()
