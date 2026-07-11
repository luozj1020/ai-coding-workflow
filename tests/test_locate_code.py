import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "locate-code.py"


def _load_module():
    """Load locate-code as a module so we can call internals and mock."""
    spec = importlib.util.spec_from_file_location("locate_code", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LocateCodeTests(unittest.TestCase):
    def init_repo(self, repo):
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)

    def run_locator(self, repo, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(repo)] + list(args),
            cwd=str(repo),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    def test_lexical_search_finds_candidate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "src").mkdir(parents=True)
            (repo / "tests").mkdir()
            (repo / "src" / "user_service.py").write_text(
                "class UserService:\n"
                "    def authenticate(self, token):\n"
                "        return token == 'ok'\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_user_service.py").write_text(
                "from src.user_service import UserService\n"
                "def test_authenticate():\n"
                "    assert UserService().authenticate('ok')\n",
                encoding="utf-8",
            )
            self.init_repo(repo)

            result = self.run_locator(repo, "--codegraph", "off", "UserService authenticate")

            self.assertIn("CodeGraph: skipped (off)", result.stdout)
            self.assertIn("src/user_service.py", result.stdout)
            self.assertIn("tests/test_user_service.py", result.stdout)
            self.assertIn("def authenticate", result.stdout)
            self.assertIn("Suggested Targeted Reads", result.stdout)

    def test_auto_codegraph_skips_large_repositories_before_cli_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".codegraph").mkdir()
            (repo / "one.py").write_text("alpha = 1\n", encoding="utf-8")
            (repo / "two.py").write_text("alpha = 2\n", encoding="utf-8")
            self.init_repo(repo)

            result = self.run_locator(
                repo,
                "--codegraph",
                "auto",
                "--codegraph-auto-file-threshold",
                "1",
                "alpha",
            )

            self.assertIn("CodeGraph: broad skipped (auto skipped broad: tracked files", result.stdout)
            self.assertIn("one.py", result.stdout)
            self.assertIn("two.py", result.stdout)


class LocateCodeMockTests(unittest.TestCase):
    """Tests that mock tool availability without replacing PATH."""

    def test_python_lexical_fallback_when_rg_and_git_grep_unavailable(self):
        """When rg is absent and git grep fails (not a repo), the Python scan finds matches."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "hello.py").write_text(
                "def greet():\n    return 'hello world'\n",
                encoding="utf-8",
            )
            (repo / "data.txt").write_text(
                "greet the user\n",
                encoding="utf-8",
            )
            # No git init — git grep will fail with rc != 0
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                matches, status = mod.search_lexical(
                    str(repo), ["greet"], [], [], [], 5.0, 80, 25,
                )
            self.assertTrue(len(matches) >= 1, "Expected at least one match from Python fallback")
            found_terms = {m[3].lower() for m in matches}
            self.assertTrue(
                any("greet" in s for s in found_terms),
                "Expected 'greet' in match snippets",
            )
            self.assertTrue(
                any("backend=python" in s for s in status),
                "Expected 'backend=python' in status",
            )

    def test_python_lexical_fallback_respects_includes(self):
        """The Python fallback respects include globs."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "code.py").write_text("alpha = 1\n", encoding="utf-8")
            (repo / "data.txt").write_text("alpha = 2\n", encoding="utf-8")
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                matches, _ = mod.search_lexical(
                    str(repo), ["alpha"], [], ["*.py"], [], 5.0, 80, 25,
                )
            paths = [m[0] for m in matches]
            self.assertIn("code.py", paths)
            self.assertNotIn("data.txt", paths)

    def test_python_lexical_fallback_respects_excludes(self):
        """The Python fallback respects exclude globs."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "src").mkdir()
            (repo / "vendor").mkdir()
            (repo / "src" / "app.py").write_text("alpha = 1\n", encoding="utf-8")
            (repo / "vendor" / "lib.py").write_text("alpha = 2\n", encoding="utf-8")
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                matches, _ = mod.search_lexical(
                    str(repo), ["alpha"], [], [], ["vendor/**"], 5.0, 80, 25,
                )
            paths = [m[0] for m in matches]
            self.assertIn("src/app.py", paths)
            self.assertNotIn("vendor/lib.py", paths)

    def test_python_lexical_fallback_respects_max_matches(self):
        """The Python fallback respects max_matches limit."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            for i in range(10):
                (repo / "f{}.py".format(i)).write_text("alpha = {}\n".format(i), encoding="utf-8")
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                matches, _ = mod.search_lexical(
                    str(repo), ["alpha"], [], [], [], 5.0, 3, 25,
                )
            self.assertLessEqual(len(matches), 3)

    def test_python_lexical_fallback_skips_binary(self):
        """The Python fallback skips files with null bytes (binary)."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "text.py").write_text("alpha = 1\n", encoding="utf-8")
            (repo / "binary.bin").write_bytes(b"\x00\x01alpha\x00\x02\n")
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                matches, _ = mod.search_lexical(
                    str(repo), ["alpha"], [], [], [], 5.0, 80, 25,
                )
            paths = [m[0] for m in matches]
            self.assertIn("text.py", paths)
            self.assertNotIn("binary.bin", paths)

    def test_zoekt_unavailable_returns_backend_unavailable(self):
        """When zoekt CLI is missing, returns backend_unavailable status."""
        mod = _load_module()
        with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
            matches, status, detail = mod.search_zoekt("test", "/nonexistent", 5.0, 80)
        self.assertEqual(matches, [])
        self.assertEqual(status, "backend_unavailable")
        self.assertIn("zoekt CLI missing", detail)

    def test_zoekt_index_missing_returns_backend_unavailable(self):
        """When zoekt index directory is missing, returns backend_unavailable."""
        mod = _load_module()
        with unittest.mock.patch.object(mod.shutil, "which", return_value="/usr/bin/zoekt"):
            matches, status, detail = mod.search_zoekt("test", "/nonexistent/idx", 5.0, 80)
        self.assertEqual(matches, [])
        self.assertEqual(status, "backend_unavailable")
        self.assertIn("index", detail.lower())

    def test_sourcegraph_unavailable_returns_backend_unavailable(self):
        """When SOURCEGRAPH_URL is not set, returns backend_unavailable."""
        mod = _load_module()
        matches, status, detail = mod.search_sourcegraph("test", "", "", 5.0, 80)
        self.assertEqual(matches, [])
        self.assertEqual(status, "backend_unavailable")
        self.assertIn("SOURCEGRAPH_URL", detail)

    def test_narrow_codegraph_query_uses_top_paths_and_symbols(self):
        """The narrowed CodeGraph query includes ranked paths and extracted terms."""
        mod = _load_module()
        candidates = {
            "src/user_service.py": {"hits": 5, "terms": {"UserService"}, "examples": [], "path_hint": True},
            "src/auth.py": {"hits": 3, "terms": {"authenticate"}, "examples": [], "path_hint": True},
            "tests/test_auth.py": {"hits": 1, "terms": {"authenticate"}, "examples": [], "path_hint": True},
        }
        result = mod._narrow_codegraph_query("UserService authenticate", candidates, max_paths=3)
        self.assertIn("UserService", result)
        self.assertIn("authenticate", result)
        # Should include basenames without extension from top paths
        self.assertTrue(
            "user_service" in result or "auth" in result or "test_auth" in result,
            "Expected path-derived names in narrowed query: {}".format(result),
        )

    def test_narrow_codegraph_query_falls_back_to_original(self):
        """When candidates is empty, narrowed query returns original query terms."""
        mod = _load_module()
        result = mod._narrow_codegraph_query("UserService authenticate", {}, max_paths=5)
        self.assertIn("UserService", result)

    def test_full_report_shows_backend_unavailable_for_missing_zoekt(self):
        """Full integration: the report shows backend_unavailable for missing zoekt."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("alpha = 1\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
            subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)

            args = mod.parse_args([
                "--repo", str(repo),
                "--backend", "zoekt",
                "--codegraph", "off",
                "alpha",
            ])
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                report = mod.build_report(args)
            self.assertIn("Zoekt: backend_unavailable", report)
            # When zoekt is unavailable, lexical fallback should still find the file
            self.assertIn("app.py", report)

    def test_full_report_codegraph_off_with_lexical(self):
        """Full integration: report works with codegraph off and lexical search."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "service.py").write_text(
                "class MyService:\n    def run(self): pass\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
            subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)

            args = mod.parse_args([
                "--repo", str(repo),
                "--codegraph", "off",
                "MyService",
            ])
            report = mod.build_report(args)
            self.assertIn("service.py", report)
            self.assertIn("CodeGraph: skipped (off)", report)

    def test_full_report_python_fallback_in_subprocess(self):
        """End-to-end: when no tools are available, Python fallback produces results."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "main.py").write_text(
                "def process_data():\n    pass\n",
                encoding="utf-8",
            )
            # No git init; run with a minimal PATH that has python but not rg/git
            import os
            env = dict(os.environ)
            # Keep python and essential system tools, remove rg/git from PATH
            path_entries = env.get("PATH", "").split(os.pathsep)
            filtered = []
            for entry in path_entries:
                # Keep entries that are likely system dirs (not tool-specific)
                if "ripgrep" not in entry.lower() and "rg" not in entry.split("/")[-1:]:
                    filtered.append(entry)
            env["PATH"] = os.pathsep.join(filtered) if filtered else "/usr/bin"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo", str(repo),
                 "--backend", "lexical", "--codegraph", "off", "process_data"],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, "Script failed: {}".format(result.stderr))
            self.assertIn("main.py", result.stdout)

    def _graph_repo_args(self, tmp, codegraph_mode="try", threshold=5000):
        mod = _load_module()
        repo = pathlib.Path(tmp) / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / ".codegraph").mkdir()
        (repo / "src" / "worker.py").write_text(
            "class Worker:\n    def execute(self): return True\n", encoding="utf-8"
        )
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)
        args = mod.parse_args([
            "--repo", str(repo), "--codegraph", codegraph_mode,
            "--codegraph-auto-file-threshold", str(threshold),
            "--codegraph-timeout", "0.1", "--codegraph-narrow-timeout", "0.05",
            "Worker", "execute",
        ])
        return mod, repo, args

    def test_broad_timeout_gets_one_candidate_scoped_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod, _repo, args = self._graph_repo_args(tmp)
            calls = []

            def fake_graph(_root, query, _timeout, _max_bytes):
                calls.append(query)
                if len(calls) == 1:
                    return {"rc": None, "stdout": "", "stderr": "", "elapsed": 0.1, "timed_out": True}, ""
                return {"rc": 0, "stdout": "narrow ok", "stderr": "", "elapsed": 0.01, "timed_out": False}, "narrow ok"

            with unittest.mock.patch.object(mod.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                 unittest.mock.patch.object(mod, "search_zoekt", return_value=([], "backend_unavailable", "missing")), \
                 unittest.mock.patch.object(mod, "run_codegraph", side_effect=fake_graph):
                report = mod.build_report(args)
            self.assertEqual(len(calls), 2)
            self.assertIn("src/worker.py", calls[1])
            self.assertIn("codegraph_broad: timeout", report)
            self.assertIn("codegraph_narrowed: rc=0", report)

    def test_large_auto_skips_broad_and_runs_one_narrowed_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod, _repo, args = self._graph_repo_args(tmp, codegraph_mode="auto", threshold=0)
            calls = []

            def fake_graph(_root, query, _timeout, _max_bytes):
                calls.append(query)
                return {"rc": 0, "stdout": "ok", "stderr": "", "elapsed": 0.01, "timed_out": False}, "ok"

            with unittest.mock.patch.object(mod.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                 unittest.mock.patch.object(mod, "search_zoekt", return_value=([], "backend_unavailable", "missing")), \
                 unittest.mock.patch.object(mod, "run_codegraph", side_effect=fake_graph):
                report = mod.build_report(args)
            self.assertEqual(len(calls), 1)
            self.assertIn("src/worker.py", calls[0])
            self.assertIn("codegraph_broad: skipped", report)
            self.assertIn("codegraph_narrowed: rc=0", report)

    def test_narrowed_timeout_stops_after_second_graph_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod, _repo, args = self._graph_repo_args(tmp)
            calls = []

            def fake_graph(_root, query, _timeout, _max_bytes):
                calls.append(query)
                return {"rc": None, "stdout": "", "stderr": "", "elapsed": 0.1, "timed_out": True}, ""

            with unittest.mock.patch.object(mod.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                 unittest.mock.patch.object(mod, "search_zoekt", return_value=([], "backend_unavailable", "missing")), \
                 unittest.mock.patch.object(mod, "run_codegraph", side_effect=fake_graph):
                report = mod.build_report(args)
            self.assertEqual(len(calls), 2)
            self.assertIn("codegraph_narrowed: timeout", report)
            self.assertIn("src/worker.py", report)

    def test_missing_zoekt_and_codegraph_reports_python_fallback(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "main.py").write_text("def process_data(): pass\n", encoding="utf-8")
            args = mod.parse_args(["--repo", str(repo), "process_data"])
            with unittest.mock.patch.object(mod.shutil, "which", return_value=None):
                report = mod.build_report(args)
            self.assertIn("backend_unavailable: zoekt,codegraph", report)
            self.assertIn("fallback_backend: python", report)
            self.assertIn("main.py", report)


if __name__ == "__main__":
    unittest.main()
