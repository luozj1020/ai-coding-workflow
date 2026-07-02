#!/usr/bin/env python3
"""
install_workflow.py  -  Install or update the AI coding workflow in a target repository.

Usage:
    python scripts/install_workflow.py /path/to/repo

This script:
    1. Copies assets into the target repo.
    2. If AGENTS.md or CLAUDE.md exist, preserves user content and replaces only managed blocks.
    3. Ensures CLAUDE.md contains @AGENTS.md import.
    4. Makes shell scripts executable.
    5. Runs bash -n on installed shell scripts (if bash is available).
    6. Prints a summary of created, updated, skipped, and validated files.

Uses only the Python standard library.
"""

import os
import subprocess
import sys


def _to_bash_path(path):
    """Convert a Windows path to a Unix-style path for bash."""
    p = path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p

# Managed block markers
BEGIN_MARKER = "<!-- AI-CODING-WORKFLOW:BEGIN managed -->"
END_MARKER = "<!-- AI-CODING-WORKFLOW:END managed -->"

AGENTS_IMPORT = "@AGENTS.md"

# Files to copy directly (source relative to assets/, dest relative to repo root)
DIRECT_COPY = [
    ("task-card-template.md", "ai/task-card-template.md"),
    ("evidence-packet-template.md", "ai/evidence-packet-template.md"),
    ("README.md", "ai/README.md"),
]

# Shell scripts to install (source relative to scripts/, dest relative to repo root)
SCRIPTS = [
    ("dispatch-to-claude.sh", "ai/dispatch-to-claude.sh"),
    ("review-with-codex.sh", "ai/review-with-codex.sh"),
    ("run-loop.sh", "ai/run-loop.sh"),
    ("status-claude.sh", "ai/status-claude.sh"),
    ("watch-claude.sh", "ai/watch-claude.sh"),
    ("kill-claude.sh", "ai/kill-claude.sh"),
    ("cleanup-worktree.sh", "ai/cleanup-worktree.sh"),
]

# PowerShell helpers to install (source relative to scripts/, dest relative to repo root)
POWERSHELL_SCRIPTS = [
    ("pwsh-utf8.ps1", "ai/pwsh-utf8.ps1"),
]

# Python helpers to install (source relative to scripts/, dest relative to repo root)
PYTHON_SCRIPTS = [
    ("doctor_workflow.py", "ai/doctor_workflow.py"),
    ("clean_runtime.py", "ai/clean_runtime.py"),
    ("install_context_tools.py", "ai/install_context_tools.py"),
]


def get_script_dir():
    """Return the directory containing this script."""
    return os.path.dirname(os.path.abspath(__file__))


def get_assets_dir():
    """Return the assets directory path."""
    return os.path.join(os.path.dirname(get_script_dir()), "assets")


def read_file(path):
    """Read a file and return its contents with normalized line endings."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Normalize line endings to \n
    return content.replace("\r\n", "\n").replace("\r", "\n")


def write_file(path, content):
    """Write content to a file, creating directories as needed.
    Normalizes to LF line endings and single trailing newline."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    normalized = normalize_text(content)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(normalized)


def normalize_text(text):
    """Normalize text to canonical form: LF line endings, single trailing newline,
    no trailing whitespace per line, no trailing blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    lines = [line.rstrip() for line in lines]
    # Strip trailing blank lines
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def normalize_for_compare(content):
    """Normalize content for comparison: strip trailing whitespace per line,
    collapse runs of 3+ blank lines to 2, strip trailing blank lines.
    Returns a canonical string for equality comparison."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Strip trailing whitespace per line
    lines = [line.rstrip() for line in lines]
    # Collapse runs of 3+ blank lines to 2
    result = []
    blank_count = 0
    for line in lines:
        if line == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)
    # Strip trailing blank lines
    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)


def extract_managed_block(content):
    """Extract the managed block from content.
    Returns the block content (between markers), or None."""
    begin_idx = content.find(BEGIN_MARKER)
    end_idx = content.find(END_MARKER)
    if begin_idx == -1 or end_idx == -1:
        return None
    block_start = begin_idx + len(BEGIN_MARKER)
    # Include leading newline after BEGIN marker if present
    if block_start < len(content) and content[block_start] == "\n":
        block_start += 1
    return content[block_start:end_idx]


def build_managed_file(header, block, footer):
    """Build a file with a managed block.
    Produces deterministic output: header, blank line, BEGIN, block, END,
    blank line (if footer), footer, single trailing newline."""
    parts = []
    if header:
        # Normalize header: strip trailing blank lines, keep content
        header_lines = header.rstrip("\n").split("\n")
        while header_lines and header_lines[-1].strip() == "":
            header_lines.pop()
        parts.extend(header_lines)
        parts.append("")
    parts.append(BEGIN_MARKER)
    # Normalize block: strip leading/trailing blank lines
    block_lines = block.rstrip("\n").split("\n")
    while block_lines and block_lines[0].strip() == "":
        block_lines.pop(0)
    while block_lines and block_lines[-1].strip() == "":
        block_lines.pop()
    parts.extend(block_lines)
    parts.append(END_MARKER)
    if footer:
        # Normalize footer: strip leading blank lines, keep content
        footer_lines = footer.rstrip("\n").split("\n")
        while footer_lines and footer_lines[0].strip() == "":
            footer_lines.pop(0)
        if footer_lines:
            parts.append("")
            parts.extend(footer_lines)
    # Ensure single trailing newline
    result = "\n".join(parts) + "\n"
    return result


def merge_with_managed_block(existing_content, new_block):
    """Replace or insert managed block. Returns the full file content."""
    existing_norm = existing_content.replace("\r\n", "\n").replace("\r", "\n")

    if BEGIN_MARKER in existing_norm and END_MARKER in existing_norm:
        # Replace existing managed block
        begin_idx = existing_norm.find(BEGIN_MARKER)
        end_idx = existing_norm.find(END_MARKER)
        end_of_block = end_idx + len(END_MARKER)

        header = existing_norm[:begin_idx]
        # Skip newline after END marker
        if end_of_block < len(existing_norm) and existing_norm[end_of_block] == "\n":
            end_of_block += 1
        footer = existing_norm[end_of_block:]

        return build_managed_file(header, new_block, footer)
    else:
        # No managed block  -  insert near the top, after the first heading if present
        lines = existing_norm.split("\n")
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("#"):
                insert_idx = i + 1
            else:
                break

        header = "\n".join(lines[:insert_idx])
        footer = "\n".join(lines[insert_idx:])
        return build_managed_file(header, new_block, footer)


def get_managed_block_from_asset(asset_content):
    """Extract the managed block content from an asset file."""
    block = extract_managed_block(asset_content)
    if block is None:
        return asset_content
    return block


def ensure_agents_import(content):
    """Ensure @AGENTS.md is present near the top of content.
    Returns (new_content, was_inserted).

    Inserts after the first heading but before any managed block markers,
    using at most one blank line above and below.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")

    # Check if @AGENTS.md already exists (as a standalone line)
    for line in normalized.split("\n"):
        if line.strip() == AGENTS_IMPORT:
            return normalized, False

    # Find insertion point: after the first heading, but before BEGIN marker
    lines = normalized.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("#"):
            insert_idx = i + 1
        else:
            break

    # Clamp: don't insert past the BEGIN marker
    for i in range(insert_idx, len(lines)):
        if BEGIN_MARKER in lines[i]:
            insert_idx = i
            break

    # Determine what's before and after the insertion point to avoid double blanks
    before_blank = insert_idx > 0 and lines[insert_idx - 1].strip() == ""
    after_blank = insert_idx < len(lines) and lines[insert_idx].strip() == ""

    insert_lines = []
    if not before_blank:
        insert_lines.append("")
    insert_lines.append(AGENTS_IMPORT)
    if not after_blank:
        insert_lines.append("")

    for j, line in enumerate(insert_lines):
        lines.insert(insert_idx + j, line)
    return "\n".join(lines), True


def install_or_update_agents(src_content, dest_path):
    """Install or update AGENTS.md. Returns status string."""
    new_block = get_managed_block_from_asset(src_content)

    if not os.path.exists(dest_path):
        content = merge_with_managed_block("", new_block)
        write_file(dest_path, content)
        return "created"

    existing = read_file(dest_path)
    merged = merge_with_managed_block(existing, new_block)

    if normalize_for_compare(merged) == normalize_for_compare(existing):
        return "skipped"

    write_file(dest_path, merged)
    return "updated"


def install_or_update_claude(src_content, dest_path):
    """Install or update CLAUDE.md. Ensures @AGENTS.md is present.
    Returns status string."""
    new_block = get_managed_block_from_asset(src_content)

    if not os.path.exists(dest_path):
        # Extract header from source: everything before BEGIN marker,
        # preserving @AGENTS.md if present in the template.
        src_norm = src_content.replace("\r\n", "\n").replace("\r", "\n")
        begin_idx = src_norm.find(BEGIN_MARKER)
        if begin_idx >= 0:
            header = src_norm[:begin_idx]
        else:
            header = ""

        content = build_managed_file(header, new_block, "")
        # Ensure @AGENTS.md (handles templates that lack it)
        content, _ = ensure_agents_import(content)
        write_file(dest_path, content)
        return "created"

    existing = read_file(dest_path)
    existing_norm = existing.replace("\r\n", "\n").replace("\r", "\n")

    # If no managed block exists, @AGENTS.md may end up in the footer.
    # Remove it from existing content before merge so it gets placed correctly.
    had_agents = False
    if BEGIN_MARKER not in existing_norm:
        lines = existing_norm.split("\n")
        filtered = []
        for line in lines:
            if line.strip() == AGENTS_IMPORT:
                had_agents = True
            else:
                filtered.append(line)
        existing = "\n".join(filtered)

    merged = merge_with_managed_block(existing, new_block)
    merged, agents_inserted = ensure_agents_import(merged)

    if normalize_for_compare(merged) == normalize_for_compare(existing_norm):
        return "skipped"

    write_file(dest_path, merged)
    return "updated"


def install_or_update_plain(src_content, dest_path):
    """Install a plain file (no managed blocks). Returns status string."""
    if not os.path.exists(dest_path):
        write_file(dest_path, src_content)
        return "created"
    return "skipped"


def make_executable(path):
    """Make a file executable (no-op on Windows, sets mode on Unix)."""
    try:
        current = os.stat(path).st_mode
        os.chmod(path, current | 0o755)
    except (OSError, AttributeError):
        pass


def _find_bash():
    """Find a usable bash executable. Returns the path or None."""
    # On Windows, prefer Git Bash over WSL bash
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
    # Fall back to whatever is in PATH
    return "bash"


def validate_shell_script(path):
    """Run bash -n on a shell script to validate syntax.

    Returns one of:
        "PASS"         -  bash validated the script successfully.
        "WARN_SKIPPED"  -  bash is unavailable or broken; skipped validation.
        "FAIL"         -  bash found a syntax error.
    """
    bash_exe = _find_bash()
    bash_path = _to_bash_path(path)
    try:
        result = subprocess.run(
            [bash_exe, "-n", bash_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return "PASS"
        else:
            stderr_snippet = result.stderr.strip().split("\n")[-1] if result.stderr else ""
            print(f"  WARNING: bash -n failed for {path}: {stderr_snippet}")
            return "FAIL"
    except FileNotFoundError:
        print(f"  WARNING: bash not found, skipping syntax validation for {path}")
        return "WARN_SKIPPED"
    except UnicodeDecodeError as e:
        print(f"  WARNING: bash -n produced undecodable output for {path}: {e}")
        return "WARN_SKIPPED"
    except subprocess.SubprocessError as e:
        print(f"  WARNING: bash -n subprocess error for {path}: {e}")
        return "WARN_SKIPPED"
    except OSError as e:
        print(f"  WARNING: bash -n OS error for {path}: {e}")
        return "WARN_SKIPPED"


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} /path/to/repo")
        sys.exit(1)

    repo_path = os.path.abspath(sys.argv[1])
    assets_dir = get_assets_dir()
    scripts_dir = get_script_dir()

    if not os.path.isdir(assets_dir):
        print(f"Error: Assets directory not found: {assets_dir}")
        sys.exit(1)

    os.makedirs(repo_path, exist_ok=True)

    results = {
        "created": [],
        "updated": [],
        "skipped": [],
        "validated": [],
        "warned": [],
        "failed": [],
    }

    # --- Install AGENTS.md (managed) ---
    src = os.path.join(assets_dir, "AGENTS.md")
    dest = os.path.join(repo_path, "AGENTS.md")
    status = install_or_update_agents(read_file(src), dest)
    results[status].append("AGENTS.md")
    print(f"  {status}: AGENTS.md")

    # --- Install CLAUDE.md (managed + @AGENTS.md) ---
    src = os.path.join(assets_dir, "CLAUDE.md")
    dest = os.path.join(repo_path, "CLAUDE.md")
    status = install_or_update_claude(read_file(src), dest)
    results[status].append("CLAUDE.md")
    print(f"  {status}: CLAUDE.md")

    # --- Install direct-copy assets ---
    for src_name, dest_rel in DIRECT_COPY:
        src = os.path.join(assets_dir, src_name)
        dest = os.path.join(repo_path, dest_rel)
        status = install_or_update_plain(read_file(src), dest)
        results[status].append(dest_rel)
        print(f"  {status}: {dest_rel}")

    # --- Install scripts ---
    for src_name, dest_rel in SCRIPTS:
        src = os.path.join(scripts_dir, src_name)
        dest = os.path.join(repo_path, dest_rel)
        status = install_or_update_plain(read_file(src), dest)
        results[status].append(dest_rel)
        print(f"  {status}: {dest_rel}")

        make_executable(dest)

        validation = validate_shell_script(dest)
        if validation == "PASS":
            results["validated"].append(dest_rel)
            print(f"  validated: {dest_rel}")
        elif validation == "WARN_SKIPPED":
            results["warned"].append(dest_rel)
        else:
            results["failed"].append(dest_rel)

    # --- Install PowerShell helpers ---
    for src_name, dest_rel in POWERSHELL_SCRIPTS:
        src = os.path.join(scripts_dir, src_name)
        dest = os.path.join(repo_path, dest_rel)
        status = install_or_update_plain(read_file(src), dest)
        results[status].append(dest_rel)
        print(f"  {status}: {dest_rel}")

    # --- Install Python helpers ---
    for src_name, dest_rel in PYTHON_SCRIPTS:
        src = os.path.join(scripts_dir, src_name)
        dest = os.path.join(repo_path, dest_rel)
        status = install_or_update_plain(read_file(src), dest)
        results[status].append(dest_rel)
        print(f"  {status}: {dest_rel}")

    # --- Create .worktrees/.gitkeep ---
    worktrees_gitkeep = os.path.join(repo_path, ".worktrees", ".gitkeep")
    if not os.path.exists(worktrees_gitkeep):
        os.makedirs(os.path.dirname(worktrees_gitkeep), exist_ok=True)
        write_file(worktrees_gitkeep, "")
        results["created"].append(".worktrees/.gitkeep")
        print(f"  created: .worktrees/.gitkeep")
    else:
        results["skipped"].append(".worktrees/.gitkeep")
        print(f"  skipped: .worktrees/.gitkeep (already exists)")

    # --- Print summary ---
    print("\n=== Installation Summary ===")
    for label, key in [("Created", "created"), ("Updated", "updated"), ("Skipped", "skipped")]:
        print(f"  {label}:   {len(results[key])} files")
        for f in results[key]:
            print(f"    + {f}")
    print(f"  Validated: {len(results['validated'])} scripts")
    for f in results["validated"]:
        print(f"    OK {f}")
    if results["warned"]:
        print(f"  Warned:    {len(results['warned'])} scripts (bash unavailable)")
        for f in results["warned"]:
            print(f"    ! {f}")
    if results["failed"]:
        print(f"  Failed:    {len(results['failed'])} scripts")
        for f in results["failed"]:
            print(f"    X {f}")
    print("\nDone.")


if __name__ == "__main__":
    main()
