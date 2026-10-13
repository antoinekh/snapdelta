"""What the web app uses at run time: the settings and the store, reloaded after the settings change."""

from dataclasses import dataclass
from pathlib import Path

from snapdelta.settings import Settings, load_settings
from snapdelta.store import Store, open_store_from


@dataclass
class AppState:
    settings: Settings
    store: Store
    settings_option: Path | None = None
    store_option: str | None = None

    def reload(self) -> None:
        """Read the settings again; the state changes only when the new store opens."""
        settings = load_settings(self.settings_option or self.settings.file, self.store_option)
        store = open_store_from(settings) if settings.store != self.settings.store else self.store
        self.settings, self.store = settings, store
