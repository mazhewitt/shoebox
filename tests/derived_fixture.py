"""Test helper (not a test module): put a synthetic derived JPEG and its ok index record in place, as
`shoebox derive` would, stamped with the settings_hash and tools the label params_hash expects."""

import json
from pathlib import Path

from shoebox import derive, ids
from shoebox.config import Config


def write_derived(cfg: Config, asset_id: str, data: bytes | None = None, profile: str = "infer-v1",
                  profile_params: dict | None = None) -> Path:
    """Write the derived file (unless `data` is None and it already exists) and append its index record."""
    if profile_params is None:
        from shoebox.runners import profile_params as real_profile_params

        profile_params = real_profile_params(profile)
    path = derive.derived_path(cfg, profile, asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is not None or not path.exists():
        path.write_bytes(b"jpeg" if data is None else data)
    rec = {"asset_id": asset_id, "profile": profile, "status": "ok",
           "path": path.relative_to(cfg.shoebox_root).as_posix(), "derived_blake3": ids.bytes_id(path.read_bytes()),
           "error": None, "tools": profile_params["tools"], "settings_hash": profile_params["settings_hash"]}
    idx = derive.index_path(cfg, profile)
    with open(idx, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return path
