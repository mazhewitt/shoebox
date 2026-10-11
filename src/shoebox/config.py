"""Loads `config/shoebox.toml` (paths and sources). Paths starting with `~` are expanded."""

import os
import tomllib
from dataclasses import dataclass
from datetime import date
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
    held_back: tuple[str, ...] = ()  # path globs relative to root, never sent to any hosted model
    hosted_models: tuple[str, ...] | None = None  # None: ("gemini",) if hosted_consent, else ()
    # When the config entry was made: hosted sends need a frame of this source dated on or after it
    # (design D5). None: any frame that lists the source counts.
    added: date | None = None

    def __post_init__(self):
        models = self.hosted_models
        if models is None:
            models = ("gemini",) if self.hosted_consent is True else ()
        models = tuple(models)
        if models and self.hosted_consent is not True:
            raise ValueError(f"source {self.name}: hosted_models {list(models)} needs hosted_consent = true")
        object.__setattr__(self, "hosted_models", models)
        object.__setattr__(self, "held_back", tuple(self.held_back))


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


def under_volumes(path: Path) -> bool:
    return Path(path).parts[:2] == ("/", "Volumes")


def check_write_location(cfg: Config, path: Path) -> None:
    """Refuse a write under /Volumes or under any source root, symlinks followed (docs/pilot.md)."""
    target = Path(path).expanduser()
    resolved = target.resolve()
    if under_volumes(target) or under_volumes(resolved):
        raise ValueError(f"refusing to write under /Volumes: {resolved}")
    for src in cfg.sources.values():
        if resolved.is_relative_to(src.root.expanduser().resolve()):
            raise ValueError(f"refusing to write under source root {src.root} ({src.name}): {resolved}")


def check_source_root(source: Source) -> Path:
    """The source's resolved root, refused (ValueError) if it is under /Volumes (the admin's writable mounts)."""
    root = source.root.expanduser()
    if under_volumes(root) or under_volumes(root.resolve()):
        raise ValueError(f"refusing to read originals under /Volumes (source {source.name}: {root}); "
                         "use the read-only mount, e.g. ~/mnt/importantfiles-ro")
    return root.resolve()


def original_path(cfg: Config, item: dict) -> Path:
    """An item's original, refused (ValueError) if under /Volumes or if it resolves outside its source root."""
    source = cfg.sources.get(item.get("source", ""))
    if source is None:
        raise ValueError(f"unknown source {item.get('source')!r}")
    rel = Path(item["relpath"])
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"refusing a relpath that leaves the source root: {item['relpath']}")
    root = check_source_root(source)
    path = (root / rel).resolve()  # follows symlinks, so a link cannot lead elsewhere
    if under_volumes(path) or not path.is_relative_to(root):
        raise ValueError(f"refusing an original that resolves outside its source root: {path}")
    return path


def _added(name: str, value) -> date | None:
    """`added = 2026-10-10` (TOML date) or the same as a string; anything else is refused."""
    if value is None or (isinstance(value, date) and not hasattr(value, "hour")):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"source {name}: added must be a date (YYYY-MM-DD), got {value!r}") from None


def load(path: Path = CONFIG_PATH) -> Config:
    data = tomllib.loads(Path(path).read_text())
    sources = {
        name: Source(
            name=name,
            root=Path(s["root"]).expanduser(),
            person=s["person"],
            synced=s["synced"],
            hosted_consent=s["hosted_consent"],
            held_back=tuple(s.get("held_back", ())),
            hosted_models=tuple(s["hosted_models"]) if "hosted_models" in s else None,
            added=_added(name, s.get("added")),
        )
        for name, s in data.get("sources", {}).items()
    }
    return Config(shoebox_root=Path(data["shoebox_root"]).expanduser(), sources=sources)
