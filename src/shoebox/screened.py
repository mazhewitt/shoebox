"""Screen gate (design D2, spec content-screen): no asset is sent, shown or captioned before it is screened.

An asset is screened when the configured screen candidate (`SCREEN_CANDIDATE`, resolved to its current
params_hash) has a final `ok` record for it. An `error` record (no derived image, unreadable, model failure)
counts as unscreened; so does an asset screened only under an older threshold or package (another
params_hash). A `sensitive` asset is screened too: the quarantine decides where it may go.

The gate is on the asset itself, not its copies: each asset that is sent must have been screened.
"""

from dataclasses import dataclass

from shoebox import store
from shoebox.config import Config

SCREEN_CANDIDATE = "nsfw-coreml-v1"


@dataclass(frozen=True)
class ScreenIndex:
    ok: frozenset[str]  # asset ids with a final ok screen record under the current screen params

    def screened(self, asset_id: str | None) -> bool:
        return bool(asset_id) and asset_id in self.ok


def load(cfg: Config, candidate: str = SCREEN_CANDIDATE) -> ScreenIndex:
    """The assets the candidate has screened (`ok` records only) under its current params_hash."""
    from shoebox.runners import resolve

    res = resolve(candidate)
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    return ScreenIndex(frozenset(a for a, r in done.items() if r.get("status") == "ok"))
