"""Never read the developer's credentials, environment, or mailbox."""

import pytest


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("APPLENOTES_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
