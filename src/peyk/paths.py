"""Where Peyk keeps its files. PEYK_HOME redirects everything (used by tests)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path, user_data_path


@dataclass(frozen=True, slots=True)
class Paths:
    config_dir: Path
    data_dir: Path

    @classmethod
    def default(cls) -> Paths:
        if home := os.environ.get("PEYK_HOME"):
            return cls(Path(home) / "config", Path(home) / "data")
        return cls(
            user_config_path("peyk", appauthor=False, roaming=True),
            user_data_path("peyk", appauthor=False),
        )

    @property
    def config(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def secrets(self) -> Path:
        return self.data_dir / "secrets.bin"

    @property
    def session(self) -> Path:
        return self.data_dir / "account.session"

    @property
    def log(self) -> Path:
        return self.data_dir / "peyk.log"

    @property
    def lock(self) -> Path:
        return self.data_dir / "peyk.lock"
