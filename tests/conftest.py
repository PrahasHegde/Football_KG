"""
Shared fixtures. The tests run against the BUILT app database (app_data/football.duckdb) and graph
(football_kg.gpickle), so build first:

    python scripts/get_data.py && python src/build_kg.py && python -m server.build_db
    python -m pytest

Everything runs offline (FKG_OFFLINE=1): no Wikimedia or Groq calls are made.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["FKG_OFFLINE"] = "1"          # must be set before server.media is imported
os.environ["GROQ_API_KEY"] = ""  # never call the LLM from tests (load_dotenv does not override it)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DB = ROOT / "app_data" / "football.duckdb"
GRAPH = ROOT / "football_kg.gpickle"
DATA = ROOT / "data" / "international_results"


def pytest_collection_modifyitems(config, items):
    """Skip data-dependent tests with a clear message when the build outputs are missing."""
    missing = [p for p in (DB, GRAPH, DATA) if not p.exists()]
    if not missing:
        return
    skip = pytest.mark.skip(reason=f"build outputs missing ({', '.join(p.name for p in missing)}): "
                                   "run get_data.py, build_kg.py and server.build_db first")
    for item in items:
        if "needs_build" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def store():
    from server.store import Store
    return Store()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from server.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def graph():
    import pickle
    with open(GRAPH, "rb") as fh:
        return pickle.load(fh)
