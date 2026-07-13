import json
from pathlib import Path


_MANIFEST = json.loads((Path(__file__).with_name("test-tiers.json")).read_text(encoding="utf-8"))
INTEGRATION_TEST_FILES = set(_MANIFEST["labels"]["integration"])
SLOW_TEST_FILES = set(_MANIFEST["labels"]["slow"])


def pytest_collection_modifyitems(config, items):
    for item in items:
        filename = Path(str(item.path)).name
        if filename in INTEGRATION_TEST_FILES:
            item.add_marker("integration")
        if filename in SLOW_TEST_FILES:
            item.add_marker("slow")
