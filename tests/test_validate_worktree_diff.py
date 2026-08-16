from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-worktree-diff.py"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run("git", "init", "-q", cwd=repo)
    run("git", "config", "user.email", "test@example.com", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    (repo / "base.py").write_text("value = 1\n", encoding="utf-8")
    run("git", "add", "base.py", cwd=repo)
    run("git", "commit", "-qm", "base", cwd=repo)
    return repo


def validate(repo: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return run(
        sys.executable, str(SCRIPT), "--worktree", str(repo),
        "--output", str(output), "--json", cwd=repo,
    )


def test_untracked_files_receive_virtual_diff_check(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "new.py").write_text("value = 2  \n", encoding="utf-8")
    output = tmp_path / "receipt.json"
    result = validate(repo, output)
    value = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 1
    assert value["untracked_paths"] == ["new.py"]
    assert value["untracked_diff_check_complete"] is True
    assert any(item["label"] == "untracked:new.py" for item in value["errors"])


def test_clean_tracked_and_untracked_changes_pass(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "base.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "new.json").write_text('{"value": 3}\n', encoding="utf-8")
    output = tmp_path / "receipt.json"
    result = validate(repo, output)
    value = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stderr + result.stdout
    assert value["status"] == "passed"
    assert value["tracked_paths"] == ["base.py"]
    assert value["untracked_paths"] == ["new.json"]


def test_python_syntax_and_duplicate_entry_points_fail(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (repo / "entry.py").write_text(
        'if __name__ == "__main__":\n    print(1)\n'
        'if __name__ == "__main__":\n    print(2)\n',
        encoding="utf-8",
    )
    output = tmp_path / "receipt.json"
    result = validate(repo, output)
    value = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 1
    kinds = {item["kind"] for item in value["errors"]}
    assert "syntax" in kinds
    assert "duplicate-entry-point" in kinds


def test_cross_file_embedding_detects_concatenation(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    component = (
        '"""Component module with enough material for boundary comparison."""\n'
        "def render_component():\n    return 'component-value'\n"
    )
    (repo / "component.py").write_text(component, encoding="utf-8")
    (repo / "page.py").write_text(
        "page_value = 'start'\n" + component,
        encoding="utf-8",
    )
    output = tmp_path / "receipt.json"
    result = validate(repo, output)
    value = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 1
    assert any(
        item["kind"] == "embedded-file-content" and item["path"] == "page.py"
        for item in value["errors"]
    )


def test_tracked_deletion_is_validated_as_a_deletion(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    target = repo / "removed.py"
    target.write_text("value = 1\n", encoding="utf-8")
    run("git", "add", "removed.py", cwd=repo)
    run("git", "commit", "-qm", "add removed", cwd=repo)
    target.unlink()
    output = tmp_path / "receipt.json"

    result = validate(repo, output)
    value = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr + result.stdout
    check = next(
        item for item in value["file_checks"] if item["path"] == "removed.py"
    )
    assert check["change"] == "deleted"
    assert check["status"] == "passed"
