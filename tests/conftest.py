"""Shared pytest setup. The `live` marker is registered here because pyproject.toml is not edited."""


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs the local Ollama (127.0.0.1:11434) with the named model pulled")
