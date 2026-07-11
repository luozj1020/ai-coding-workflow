#!/usr/bin/env python3
"""Locate likely code-edit targets with bounded, low-token repository search.

The helper is intentionally lexical-first. CodeGraph can be useful, but on very
large repositories a broad graph query can dominate wall time. This script keeps
CodeGraph behind a short timeout and always falls back to file-list + rg/git grep
evidence.
"""

import argparse
import collections
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


DEFAULT_EXCLUDES = [
    ".git/**",
    ".worktrees/**",
    "node_modules/**",
    "vendor/**",
    "third_party/**",
    "bazel-*",
    "build/**",
    "dist/**",
]


def run_command(args, cwd, timeout):
    started = time.monotonic()
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return {
            "rc": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed": time.monotonic() - started,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "rc": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "elapsed": time.monotonic() - started,
            "timed_out": True,
        }
    except FileNotFoundError as exc:
        return {
            "rc": 127,
            "stdout": "",
            "stderr": str(exc),
            "elapsed": time.monotonic() - started,
            "timed_out": False,
        }


def find_repo_root(start):
    result = run_command(["git", "-C", start, "rev-parse", "--show-toplevel"], start, 5)
    if result["rc"] == 0 and result["stdout"].strip():
        return os.path.abspath(result["stdout"].strip())
    return os.path.abspath(start)


def git_files(repo_root):
    result = run_command(["git", "ls-files", "-z"], repo_root, 20)
    if result["rc"] == 0:
        return [p for p in result["stdout"].split("\0") if p]
    files = []
    for root, dirs, names in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in {".git", ".worktrees", "node_modules"}]
        for name in names:
            path = os.path.relpath(os.path.join(root, name), repo_root).replace(os.sep, "/")
            files.append(path)
    return files


def unique_ordered(values):
    seen = set()
    out = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _is_text_file(filepath):
    """Check if a file is likely text by reading the first 8 KiB."""
    try:
        with open(filepath, "rb") as fh:
            chunk = fh.read(8192)
        if b"\x00" in chunk:
            return False
        chunk.decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


def _python_lexical_scan(repo_root, terms, paths, includes, excludes, max_matches, max_matches_per_term, time_budget, candidate_files=None):
    """Bounded Python line scan — last-resort fallback when rg and git grep are unavailable."""
    matches = []
    term_used = {t: 0 for t in terms}
    deadline = time.monotonic() + time_budget
    lowered = [t.lower() for t in terms]
    skip_dirs = {".git", ".worktrees", "node_modules", "vendor", "third_party", "dist", "build"}

    scan_paths = []
    if candidate_files is not None:
        for rel in candidate_files:
            norm = rel.replace(os.sep, "/")
            if paths and not any(norm == p or norm.startswith(p.rstrip("/") + "/") for p in paths):
                continue
            scan_paths.append(os.path.join(repo_root, rel))
    elif paths:
        for p in paths:
            full = os.path.join(repo_root, p)
            if os.path.isdir(full):
                for root, dirs, names in os.walk(full):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for name in names:
                        scan_paths.append(os.path.join(root, name))
            elif os.path.isfile(full):
                scan_paths.append(full)
    else:
        for root, dirs, names in os.walk(repo_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for name in names:
                scan_paths.append(os.path.join(root, name))

    for filepath in scan_paths:
        if time.monotonic() >= deadline:
            break
        if len(matches) >= max_matches:
            break
        rel = os.path.relpath(filepath, repo_root).replace(os.sep, "/")
        if not path_allowed(rel, includes, excludes):
            continue
        if not _is_text_file(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, 1):
                    if time.monotonic() >= deadline:
                        break
                    lower_line = line.lower()
                    for i, term in enumerate(terms):
                        if lowered[i] in lower_line:
                            if term_used[term] >= max_matches_per_term:
                                continue
                            snippet = line.rstrip()[:240]
                            matches.append((rel, line_no, "python", snippet))
                            term_used[term] += 1
                            if len(matches) >= max_matches:
                                break
        except (OSError, UnicodeDecodeError):
            continue
    return matches


def extract_terms(query):
    raw = query.strip()
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_:.#/-]{2,}", raw)
    dotted = []
    for token in tokens:
        dotted.append(token)
        if "." in token or "::" in token or "/" in token:
            dotted.extend(re.split(r"[.:/#-]+", token))
    terms = [t.strip() for t in dotted if len(t.strip()) >= 3]
    terms = sorted(unique_ordered(terms), key=lambda t: (-len(t), t.lower()))
    return terms[:10]


def path_allowed(path, includes, excludes):
    norm = path.replace(os.sep, "/")
    if includes and not any(fnmatch.fnmatch(norm, pattern) for pattern in includes):
        return False
    for pattern in excludes:
        if fnmatch.fnmatch(norm, pattern) or fnmatch.fnmatch(norm, "*/" + pattern):
            return False
    return True


def score_path_hints(files, terms, includes, excludes, max_files):
    scored = {}
    lowered_terms = [t.lower() for t in terms]
    for path in files:
        if not path_allowed(path, includes, excludes):
            continue
        lower = path.lower()
        matched = {terms[i] for i, term in enumerate(lowered_terms) if term in lower}
        if matched:
            scored[path] = {
                "hits": 0,
                "terms": set(matched),
                "examples": [],
                "path_hint": True,
            }
    return dict(sorted(scored.items(), key=lambda item: (-len(item[1]["terms"]), item[0]))[:max_files])


def parse_match_line(line):
    first = line.find(":")
    if first <= 0:
        return None
    second = line.find(":", first + 1)
    if second <= first:
        return None
    path = line[:first]
    if path.startswith("./"):
        path = path[2:]
    line_no = line[first + 1:second]
    if not line_no.isdigit():
        return None
    return path.replace("\\", "/"), int(line_no), line[second + 1:].strip()


def default_zoekt_index():
    return os.environ.get(
        "AI_CODE_ZOEKT_INDEX",
        os.path.join(os.path.expanduser("~"), ".cache", "ai-coding-workflow", "zoekt"),
    )


def parse_zoekt_output(text, max_matches):
    matches = []
    for line in text.splitlines():
        parsed = parse_match_line(line)
        if parsed:
            path, line_no, snippet = parsed
            matches.append((path, line_no, "zoekt", snippet))
            if len(matches) >= max_matches:
                break
    return matches


def search_zoekt(query, index, timeout, max_matches):
    query_bin = shutil.which("zoekt")
    if not query_bin:
        return [], "backend_unavailable", "zoekt CLI missing"
    if not os.path.isdir(index):
        return [], "backend_unavailable", "Zoekt index not found"
    result = run_command([query_bin, "-index_dir", index, query], os.getcwd(), timeout)
    if result["timed_out"]:
        return [], "timeout", "timeout after {:.1f}s".format(result["elapsed"])
    if result["rc"] not in (0, 1):
        return [], "error", "rc={} {}".format(result["rc"], result["stderr"].strip()[:160])
    matches = parse_zoekt_output(result["stdout"], max_matches)
    return matches, "ok", "{} match line(s) elapsed={:.1f}s".format(len(matches), result["elapsed"])


def _sourcegraph_line_matches(item):
    path = item.get("path") or item.get("file") or item.get("repository", "")
    line_matches = item.get("lineMatches") or item.get("line_matches") or []
    out = []
    for match in line_matches:
        line_no = match.get("lineNumber") or match.get("line") or match.get("line_number") or 0
        preview = match.get("preview") or match.get("line") or match.get("text") or ""
        try:
            line_no = int(line_no)
        except (TypeError, ValueError):
            line_no = 0
        if path and line_no:
            out.append((path, line_no, "sourcegraph", str(preview)))
    return out


def extract_sourcegraph_matches(value, max_matches):
    matches = []
    stack = [value]
    while stack and len(matches) < max_matches:
        current = stack.pop()
        if isinstance(current, dict):
            if "lineMatches" in current or "line_matches" in current:
                matches.extend(_sourcegraph_line_matches(current))
                matches = matches[:max_matches]
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return matches[:max_matches]


def search_sourcegraph(query, url, token, timeout, max_matches):
    if not url:
        return [], "backend_unavailable", "SOURCEGRAPH_URL not configured"
    endpoint = url.rstrip("/") + "/.api/graphql"
    graphql = {
        "query": (
            "query Search($query: String!) { "
            "search(query: $query, version: V3) { "
            "results { results { "
            "__typename ... on FileMatch { file { path } lineMatches { lineNumber preview } } "
            "} } } }"
        ),
        "variables": {"query": query},
    }
    body = json.dumps(graphql).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "token {}".format(token))
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
        data = json.loads(payload)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [], "error", str(exc)
    matches = extract_sourcegraph_matches(data, max_matches)
    return matches, "ok", "{} match line(s) elapsed={:.1f}s".format(len(matches), time.monotonic() - started)


def search_lexical(repo_root, terms, paths, includes, excludes, timeout, max_matches, max_matches_per_term, candidate_files=None):
    rg = shutil.which("rg")
    matches = []
    status = []
    use_python_fallback = False

    for term in terms:
        if len(matches) >= max_matches:
            break
        if rg:
            args = [
                rg,
                "--line-number",
                "--no-heading",
                "--color",
                "never",
                "--fixed-strings",
                "--ignore-case",
                "--max-columns",
                "240",
                "--max-columns-preview",
            ]
            for pattern in includes:
                args.extend(["--glob", pattern])
            for pattern in excludes:
                args.extend(["--glob", "!" + pattern])
            args.append(term)
            args.extend(paths or ["."])
        else:
            args = ["git", "grep", "-n", "-I", "--ignore-case", "-F", term]
            args.extend(["--"] + (paths or ["."]))

        result = run_command(args, repo_root, timeout)
        label = "rg" if rg else "git grep"
        if result["timed_out"]:
            status.append("{} term={!r}: timeout after {:.1f}s".format(label, term, result["elapsed"]))
            continue
        # When git grep is not usable (not found, not a repo, etc.), switch to Python
        if not rg and result["rc"] not in (0, 1):
            use_python_fallback = True
            status.append("{}: unavailable (rc={})".format(label, result["rc"]))
            break
        if result["rc"] not in (0, 1):
            status.append("{} term={!r}: rc={} {}".format(label, term, result["rc"], result["stderr"].strip()[:160]))
            continue
        parsed = [parse_match_line(line) for line in result["stdout"].splitlines()]
        parsed = [item for item in parsed if item and path_allowed(item[0], includes, excludes)]
        used_for_term = 0
        for path, line_no, snippet in parsed:
            matches.append((path, line_no, term, snippet))
            used_for_term += 1
            if len(matches) >= max_matches:
                break
            if used_for_term >= max_matches_per_term:
                break
        status.append(
            "{} term={!r}: {} match line(s), kept {}".format(
                label, term, len(parsed), used_for_term
            )
        )

    if use_python_fallback:
        effective_timeout = max(1.0, timeout * len(terms))
        python_matches = _python_lexical_scan(
            repo_root, terms, paths, includes, excludes,
            max_matches, max_matches_per_term, effective_timeout, candidate_files,
        )
        matches.extend(python_matches)
        status.append("python: {} match(es), backend=python".format(len(python_matches)))

    return matches, status


def should_try_codegraph(mode, repo_root, tracked_count, threshold):
    """Return broad permission, narrowed permission, and routing reason."""
    if mode == "off":
        return False, False, "off"
    if not os.path.isdir(os.path.join(repo_root, ".codegraph")):
        return False, False, "no .codegraph index"
    if not shutil.which("codegraph"):
        return False, False, "codegraph CLI missing"
    if mode == "try":
        return True, True, "explicit try"
    if tracked_count > threshold:
        return False, True, "auto skipped broad: tracked files {} > threshold {}".format(tracked_count, threshold)
    return True, True, "auto small-enough repo"


def run_codegraph(repo_root, query, timeout, max_bytes):
    result = run_command(["codegraph", "explore", query], repo_root, timeout)
    text = (result["stdout"] or result["stderr"] or "").strip()
    if len(text.encode("utf-8")) > max_bytes:
        encoded = text.encode("utf-8")[:max_bytes]
        text = encoded.decode("utf-8", errors="replace") + "\n...[truncated]"
    return result, text


def _narrow_codegraph_query(query, ranked, max_paths=5):
    """Build a narrowed CodeGraph query using top ranked paths and concrete symbols."""
    terms = extract_terms(query)
    if isinstance(ranked, dict):
        ranked = sorted(
            ranked.items(),
            key=lambda item: (-len(item[1].get("terms", set())), item[0]),
        )
    paths = [path for path, _data in ranked[:max_paths]]
    if not paths:
        return query
    return (
        "Analyze only these candidate files: {}. Concrete symbols/terms: {}. "
        "Return local relationships and relevant source only."
    ).format(", ".join(paths), ", ".join(terms[:6]) or query)


def build_report(args):
    repo_root = find_repo_root(args.repo)
    query = " ".join(args.query)
    terms = extract_terms(query)
    includes = list(args.include)
    excludes = DEFAULT_EXCLUDES + list(args.exclude)
    files = git_files(repo_root)
    tracked_count = len(files)
    paths = [p.replace("\\", "/") for p in args.path]
    backend = args.backend or os.environ.get("AI_CODE_LOCATOR_BACKEND", "auto")

    candidates = score_path_hints(files, terms, includes, excludes, args.max_files)

    backend_status = []
    backend_matches = []
    unavailable = []
    selected_backend = "none"
    if backend in ("auto", "zoekt"):
        zoek_matches, zoek_status, zoek_detail = search_zoekt(
            query,
            os.path.abspath(args.zoekt_index),
            args.zoekt_timeout,
            args.max_matches,
        )
        if zoek_status == "backend_unavailable":
            backend_status.append("Zoekt: backend_unavailable — {}".format(zoek_detail))
            unavailable.append("zoekt")
        else:
            backend_status.append("Zoekt: {}".format(zoek_detail))
        backend_matches.extend(zoek_matches)
        if zoek_matches:
            selected_backend = "zoekt"
        if backend == "zoekt" and not zoek_matches and zoek_status != "backend_unavailable":
            backend_status.append("Zoekt requested but produced no usable matches; lexical fallback enabled.")

    sourcegraph_url = args.sourcegraph_url or os.environ.get("SOURCEGRAPH_URL", "")
    sourcegraph_token = args.sourcegraph_token or os.environ.get("SOURCEGRAPH_TOKEN", "")
    if (backend == "sourcegraph") or (backend == "auto" and not backend_matches and sourcegraph_url):
        sg_matches, sg_status, sg_detail = search_sourcegraph(
            query,
            sourcegraph_url,
            sourcegraph_token,
            args.sourcegraph_timeout,
            args.max_matches,
        )
        if sg_status == "backend_unavailable":
            backend_status.append("Sourcegraph: backend_unavailable — {}".format(sg_detail))
            unavailable.append("sourcegraph")
        else:
            backend_status.append("Sourcegraph: {}".format(sg_detail))
        backend_matches.extend(sg_matches)
        if sg_matches and selected_backend == "none":
            selected_backend = "sourcegraph"
        if backend == "sourcegraph" and not sg_matches and sg_status != "backend_unavailable":
            backend_status.append("Sourcegraph requested but produced no usable matches; lexical fallback enabled.")

    broad_allowed, narrowed_allowed, codegraph_reason = should_try_codegraph(
        args.codegraph, repo_root, tracked_count, args.codegraph_auto_file_threshold
    )
    if codegraph_reason in ("no .codegraph index", "codegraph CLI missing"):
        unavailable.append("codegraph")
    codegraph_parts = []
    codegraph_text = ""
    codegraph_broad = "not_attempted"
    codegraph_narrowed = "not_attempted"
    if broad_allowed:
        result, codegraph_text = run_codegraph(
            repo_root, query, args.codegraph_timeout, args.codegraph_max_bytes
        )
        if result["timed_out"]:
            codegraph_broad = "timeout"
            codegraph_parts.append("broad timeout after {:.1f}s".format(result["elapsed"]))
            codegraph_text = ""
        else:
            codegraph_broad = "rc={}".format(result["rc"])
            codegraph_parts.append("broad rc={} elapsed={:.1f}s".format(result["rc"], result["elapsed"]))
    else:
        codegraph_broad = "skipped"
        codegraph_parts.append("broad skipped ({})".format(codegraph_reason))

    lexical_status = []
    matches = list(backend_matches)
    if backend in ("auto", "lexical") or not matches:
        lexical_matches, lexical_status = search_lexical(
            repo_root,
            terms,
            paths,
            includes,
            excludes,
            args.search_timeout,
            args.max_matches,
            args.max_matches_per_term,
            files,
        )
        matches.extend(lexical_matches)
    for path, line_no, term, snippet in matches:
        entry = candidates.setdefault(
            path,
            {"hits": 0, "terms": set(), "examples": [], "path_hint": False},
        )
        entry["hits"] += 1
        entry["terms"].add(term)
        if len(entry["examples"]) < args.examples_per_file:
            entry["examples"].append((line_no, snippet))

    ranked = sorted(
        candidates.items(),
        key=lambda item: (
            -(len(item[1]["terms"]) * 10 + item[1]["hits"] + (2 if item[1]["path_hint"] else 0)),
            item[0],
        ),
    )[: args.max_files]

    should_narrow = narrowed_allowed and ranked and (
        codegraph_broad == "timeout" or
        (codegraph_broad == "skipped" and codegraph_reason.startswith("auto skipped broad:"))
    )
    if should_narrow:
        narrowed_query = _narrow_codegraph_query(query, ranked, max_paths=5)
        narrow_result, narrow_text = run_codegraph(
            repo_root,
            narrowed_query,
            min(args.codegraph_timeout, args.codegraph_narrow_timeout),
            args.codegraph_max_bytes,
        )
        if narrow_result["timed_out"]:
            codegraph_narrowed = "timeout"
            codegraph_parts.append("narrowed timeout after {:.1f}s".format(narrow_result["elapsed"]))
        else:
            codegraph_narrowed = "rc={}".format(narrow_result["rc"])
            codegraph_parts.append(
                "narrowed rc={} elapsed={:.1f}s".format(narrow_result["rc"], narrow_result["elapsed"])
            )
            if narrow_text:
                codegraph_text = narrow_text

    if args.codegraph == "off":
        codegraph_status = "skipped (off)"
    else:
        codegraph_status = "; ".join(codegraph_parts)

    lines = []
    lines.append("# Locate Code Report")
    lines.append("")
    lines.append("- Repo: `{}`".format(repo_root))
    lines.append("- Query: `{}`".format(query))
    lines.append("- Terms: {}".format(", ".join("`{}`".format(t) for t in terms) or "(none)"))
    lines.append("- Tracked files considered: {}".format(tracked_count))
    lines.append("- Primary backend: `{}`".format(backend))
    lines.append("- CodeGraph: {}".format(codegraph_status))
    used_python = any("backend=python" in s for s in lexical_status)
    if used_python:
        lexical_label = "python"
    elif shutil.which("rg"):
        lexical_label = "rg"
    else:
        lexical_label = "git grep"
    if selected_backend == "none" and matches:
        selected_backend = lexical_label.replace(" ", "-")
    lines.append("- Lexical backend: {}".format(lexical_label))
    lines.append("")
    lines.append("## Routing")
    lines.append("- backend_unavailable: {}".format(",".join(unique_ordered(unavailable)) or "none"))
    lines.append("- fallback_backend: {}".format(selected_backend))
    lines.append("- scope_limited: {}".format("yes" if should_narrow or paths or includes else "no"))
    lines.append("- codegraph_broad: {}".format(codegraph_broad))
    lines.append("- codegraph_narrowed: {}".format(codegraph_narrowed))
    lines.append("")
    lines.append("## Candidate Files")
    if not ranked:
        lines.append("")
        lines.append("No candidate files found. Add more specific identifiers, path prefixes, or `--include` globs.")
    else:
        lines.append("")
        lines.append("| Score | Terms | Hits | File |")
        lines.append("|-------|-------|------|------|")
        for path, data in ranked:
            score = len(data["terms"]) * 10 + data["hits"] + (2 if data["path_hint"] else 0)
            lines.append(
                "| {} | {} | {} | `{}` |".format(
                    score,
                    ", ".join(sorted(data["terms"])) or "-",
                    data["hits"],
                    path,
                )
            )
    lines.append("")
    lines.append("## Match Snippets")
    for path, data in ranked:
        if not data["examples"]:
            continue
        lines.append("")
        lines.append("### `{}`".format(path))
        for line_no, snippet in data["examples"]:
            lines.append("- `{}:{}` {}".format(path, line_no, snippet[:240]))
    lines.append("")
    lines.append("## Suggested Targeted Reads")
    for path, data in ranked[: min(8, len(ranked))]:
        quoted_path = shlex.quote(path)
        if data["examples"]:
            first_line = data["examples"][0][0]
            start = max(1, first_line - args.context)
            end = first_line + args.context
            lines.append("- `nl -ba {} | sed -n '{} ,{}p'`".format(quoted_path, start, end).replace(" ,", ","))
        else:
            lines.append("- `nl -ba {} | sed -n '1,160p'`".format(quoted_path))
    lines.append("")
    lines.append("## Search Status")
    for item in backend_status:
        lines.append("- {}".format(item))
    for item in lexical_status:
        lines.append("- {}".format(item))
    if codegraph_text:
        lines.append("")
        lines.append("## CodeGraph Excerpt")
        lines.append("```text")
        lines.append(codegraph_text)
        lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="+", help="Natural-language request or identifiers to locate.")
    parser.add_argument("--repo", default=".", help="Repository root or subdirectory.")
    parser.add_argument("--path", action="append", default=[], help="Path prefix to search. Repeatable.")
    parser.add_argument("--include", action="append", default=[], help="Include glob, for example '*.py'. Repeatable.")
    parser.add_argument("--exclude", action="append", default=[], help="Exclude glob. Repeatable.")
    parser.add_argument("--max-files", type=int, default=12, help="Maximum candidate files to print.")
    parser.add_argument("--max-matches", type=int, default=80, help="Maximum raw match lines to parse.")
    parser.add_argument("--max-matches-per-term", type=int, default=25, help="Maximum match lines kept per term.")
    parser.add_argument("--examples-per-file", type=int, default=3, help="Maximum snippets per candidate file.")
    parser.add_argument("--context", type=int, default=40, help="Suggested read context around the first match.")
    parser.add_argument("--search-timeout", type=float, default=8.0, help="Timeout per lexical term.")
    parser.add_argument(
        "--backend",
        choices=["auto", "lexical", "zoekt", "sourcegraph"],
        default=None,
        help="Primary locator backend. Default: AI_CODE_LOCATOR_BACKEND or auto.",
    )
    parser.add_argument("--zoekt-index", default=default_zoekt_index(), help="Zoekt index directory.")
    parser.add_argument("--zoekt-timeout", type=float, default=8.0, help="Zoekt query timeout in seconds.")
    parser.add_argument("--sourcegraph-url", default="", help="Sourcegraph base URL.")
    parser.add_argument("--sourcegraph-token", default="", help="Sourcegraph access token.")
    parser.add_argument("--sourcegraph-timeout", type=float, default=8.0, help="Sourcegraph search timeout in seconds.")
    parser.add_argument(
        "--codegraph",
        choices=["auto", "off", "try"],
        default="auto",
        help="CodeGraph mode. auto uses a short attempt only for smaller indexed repos.",
    )
    parser.add_argument("--codegraph-timeout", type=float, default=12.0, help="CodeGraph timeout in seconds.")
    parser.add_argument(
        "--codegraph-narrow-timeout",
        type=float,
        default=6.0,
        help="Timeout for the single candidate-scoped CodeGraph retry.",
    )
    parser.add_argument(
        "--codegraph-auto-file-threshold",
        type=int,
        default=5000,
        help="Tracked-file threshold above which auto mode skips CodeGraph.",
    )
    parser.add_argument("--codegraph-max-bytes", type=int, default=6000, help="Maximum CodeGraph excerpt bytes.")
    parser.add_argument("--output", help="Write report to a file instead of stdout.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    report = build_report(args)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
