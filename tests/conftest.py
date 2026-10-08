import os
from uuid import uuid4
import pytest
from dqflow.settings import load


@pytest.fixture
def cfg(tmp_path):
    config = load().model_copy(deep=True)
    config.environment = "test"
    config.dataset = "test_" + uuid4().hex[:12]
    config.source.type = "csv"
    config.source.directory = str(tmp_path / "demo")
    config.artifacts.raw = str(tmp_path / "raw")
    config.artifacts.root = str(tmp_path / "artifacts")
    config.notifications.endpoint = None
    return config


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in item.keywords and os.environ.get("DQFLOW_INTEGRATION") != "1":
            item.add_marker(
                pytest.mark.skip(reason="Set DQFLOW_INTEGRATION=1 on an isolated migrated PostgreSQL")
            )
