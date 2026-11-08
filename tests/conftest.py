import pytest
from fastapi.testclient import TestClient

from board.app import create_app
from board.store import Store

SAMPLE = """# A question

First paragraph about anchors.

Second paragraph, same text.

## Options

- one
- two
"""


@pytest.fixture
def sample():
    return SAMPLE


@pytest.fixture
def root(tmp_path):
    (tmp_path / "docs").mkdir()
    return tmp_path


@pytest.fixture
def store(root):
    instance = Store(root)
    instance.ensure_layout()
    return instance


@pytest.fixture
def client(root):
    return TestClient(create_app(root))


@pytest.fixture
def seed(store):
    def _seed(name="0001-question", markdown=SAMPLE):
        return store.create(name, markdown)

    return _seed
