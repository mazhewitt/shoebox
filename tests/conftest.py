"""Shared pytest setup. The `live` marker is registered here because pyproject.toml is not edited."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs the local Ollama (127.0.0.1:11434) with the named model pulled")
    config.addinivalue_line("markers", "real_copy_index: privacy.filter_items loads the copy index from frames")
    config.addinivalue_line("markers", "real_screen_gate: the screen gate loads screen records from the store")


class AllScreened:
    """Test-only stand-in for `screened.ScreenIndex`: every asset counts as screened. Never in src/."""

    def screened(self, asset_id):
        return bool(asset_id)


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


@pytest.fixture(autouse=True)
def _screen_gate_passes(request, monkeypatch):
    """The screen gate (`privacy.default_screen_index`) loads the screen store, so a synthetic asset with no
    screen record is refused as `unscreened`. Tests that are not about the gate treat every asset as
    screened; tests marked `real_screen_gate` (tests/test_screen_gate.py) exercise the real loader."""
    if request.node.get_closest_marker("real_screen_gate"):
        return
    from shoebox import privacy

    monkeypatch.setattr(privacy, "default_screen_index", lambda cfg: AllScreened())
