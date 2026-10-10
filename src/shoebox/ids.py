"""Asset IDs: BLAKE3 of the original file's bytes, lowercase hex."""

from pathlib import Path

import blake3

_CHUNK = 8 * 1024 * 1024


def asset_id(path: Path) -> str:
    h = blake3.blake3()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def bytes_id(data: bytes) -> str:
    return blake3.blake3(data).hexdigest()
