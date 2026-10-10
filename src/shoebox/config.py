"""Loads `config/shoebox.toml` (paths and sources). Paths starting with `~` are expanded."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("SHOEBOX_CONFIG", REPO_ROOT / "config" / "shoebox.toml"))


@dataclass(frozen=True)
class Source:
    name: str
    root: Path
    person: str
    synced: bool
    hosted_consent: bool


@dataclass(frozen=True)
class Config:
    shoebox_root: Path
    sources: dict[str, Source]

    @property
    def derived_dir(self) -> Path:
        return self.shoebox_root / "derived"

    @property
    def results_dir(self) -> Path:
        return self.shoebox_root / "results"


def load(path: Path = CONFIG_PATH) -> Config:
    data = tomllib.loads(Path(path).read_text())
    sources = {
        name: Source(
            name=name,
            root=Path(s["root"]).expanduser(),
            person=s["person"],
            synced=s["synced"],
            hosted_consent=s["hosted_consent"],
        )
        for name, s in data.get("sources", {}).items()
    }
    return Config(shoebox_root=Path(data["shoebox_root"]).expanduser(), sources=sources)
