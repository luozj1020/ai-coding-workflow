from pathlib import Path


INTEGRATION_TEST_FILES = {
    "test_check_worktree.py",
    "test_clean_runtime.py",
    "test_dirty_source_guard.py",
    "test_doctor_workflow.py",
    "test_install_context_tools.py",
    "test_install_for_codex.py",
    "test_install_workflow.py",
    "test_run_codex_spark.py",
    "test_run_parallel_loop.py",
}

SLOW_TEST_FILES = {
    "test_dirty_source_guard.py",
    "test_install_workflow.py",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        filename = Path(str(item.path)).name
        if filename in INTEGRATION_TEST_FILES:
            item.add_marker("integration")
        if filename in SLOW_TEST_FILES:
            item.add_marker("slow")
