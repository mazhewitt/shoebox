"""Shared pytest setup. The `live` marker is registered here because pyproject.toml is not edited."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs the local Ollama (127.0.0.1:11434) with the named model pulled")
    config.addinivalue_line("markers", "real_copy_index: privacy.filter_items loads the copy index from frames")


@pytest.fixture(autouse=True)
def _no_real_copy_index(request, monkeypatch, tmp_path_factory):
    """`privacy.filter_items` loads the cross-source copy index from the frames under `shoebox_root`, and
    the quarantine from the repo's evals/quarantine.jsonl, by default (failing closed without frames).
    Tests run on synthetic configs, so they get an empty index and an empty quarantine file unless marked
    `real_copy_index` or passing their own."""
    if request.node.get_closest_marker("real_copy_index"):
        return
    from shoebox import copies, privacy, quarantine

    monkeypatch.setattr(privacy, "default_copy_index", lambda cfg: copies.CopyIndex.build([], {}, ()))
    monkeypatch.setattr(quarantine, "QUARANTINE_PATH", tmp_path_factory.mktemp("q") / "quarantine.jsonl")
