import contextlib
import importlib.util
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_for_codex.py"


def load_module():
    spec = importlib.util.spec_from_file_location("install_for_codex", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallForCodexTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_should_exclude_pyc_files_by_wildcard(self):
        self.assertTrue(self.module.should_exclude("cache.pyc", "/repo/cache.pyc"))
        self.assertTrue(self.module.should_exclude(".git", "/repo/.git"))
        self.assertTrue(self.module.should_exclude("task-cards", "/repo/task-cards"))
        self.assertTrue(self.module.should_exclude("tmp-smoke", "/repo/tmp-smoke"))
        self.assertFalse(self.module.should_exclude("README.md", "/repo/README.md"))

    def test_copy_skill_excludes_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "src"
            dest = pathlib.Path(tmp) / "dest"
            (src / "scripts").mkdir(parents=True)
            (src / "assets").mkdir()
            (src / "__pycache__").mkdir()
            (src / ".worktrees").mkdir()
            (src / ".codegraph").mkdir()
            (src / "ref").mkdir()
            (src / "ai").mkdir()
            (src / "README.md").write_text("ok\n", encoding="utf-8")
            (src / "AGENTS.md").write_text("generated\n", encoding="utf-8")
            (src / "CLAUDE.md").write_text("generated\n", encoding="utf-8")
            (src / "assets" / "AGENTS.md").write_text("template\n", encoding="utf-8")
            (src / "assets" / "CLAUDE.md").write_text("template\n", encoding="utf-8")
            (src / "scripts" / "update_skill.py").write_text("ok\n", encoding="utf-8")
            (src / "scripts" / "tool.pyc").write_text("compiled\n", encoding="utf-8")
            (src / "__pycache__" / "x.pyc").write_text("compiled\n", encoding="utf-8")
            (src / ".worktrees" / "artifact.txt").write_text("artifact\n", encoding="utf-8")
            (src / ".codegraph" / "codegraph.db").write_text("db\n", encoding="utf-8")
            (src / "ref" / "article.md").write_text("local reference\n", encoding="utf-8")
            (src / "ai" / "dispatch-to-claude.sh").write_text("generated\n", encoding="utf-8")

            self.module.copy_skill(str(src), str(dest))

            self.assertTrue((dest / "README.md").exists())
            self.assertTrue((dest / "scripts" / "update_skill.py").exists())
            self.assertTrue((dest / "assets" / "AGENTS.md").exists())
            self.assertTrue((dest / "assets" / "CLAUDE.md").exists())
            self.assertFalse((dest / "AGENTS.md").exists())
            self.assertFalse((dest / "CLAUDE.md").exists())
            self.assertFalse((dest / "scripts" / "tool.pyc").exists())
            self.assertFalse((dest / "__pycache__").exists())
            self.assertFalse((dest / ".worktrees").exists())
            self.assertFalse((dest / ".codegraph").exists())
            self.assertFalse((dest / "ref").exists())
            self.assertFalse((dest / "ai").exists())
            self.assertFalse((dest / "task-cards").exists())
            self.assertFalse((dest / "tmp-smoke").exists())

    def test_copy_skill_noops_when_source_is_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "skill"
            src.mkdir()
            marker = src / "SKILL.md"
            marker.write_text("ok\n", encoding="utf-8")

            self.module.copy_skill(str(src), str(src))

            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok\n")

    def test_build_bootstrap_command_targets_installed_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = pathlib.Path(tmp) / "skills" / "ai-coding-workflow"
            cmd = self.module.build_bootstrap_command(str(skill), ".")

            self.assertIn("install_workflow.py", cmd)
            self.assertIn("ai-coding-workflow", cmd)
            self.assertIn(".", cmd)

            update_cmd = self.module.build_bootstrap_command(
                str(skill), ".", update_workflow_files=True
            )
            self.assertIn("--update-workflow-files", update_cmd)

    def test_skill_entrypoint_keeps_routing_context(self):
        skill = ROOT / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        self.assertIn("Reference Router", content)
        self.assertIn("Builder", content)
        self.assertIn("Checker/Test", content)
        self.assertIn("claude-runtime.md", content)
        self.assertIn("review-policy.md", content)
        self.assertIn("Dirty source requires clean restoration or an explicit hash-bound snapshot", content)

    def test_skill_entrypoint_stays_within_default_context_budget(self):
        content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(content.encode("utf-8")), 18_000)
        self.assertLessEqual(len(content.split()), 2_500)

    def test_skill_entrypoint_references_exist_and_are_first_level(self):
        content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        references = set(re.findall(r"`(references/[^`]+\.md)`", content))
        self.assertGreaterEqual(len(references), 5)
        for relative in references:
            self.assertEqual(pathlib.PurePosixPath(relative).parent.as_posix(), "references")
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_installed_skill_keeps_progressive_disclosure_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = pathlib.Path(tmp) / "skill"
            self.module.copy_skill(str(ROOT), str(destination))

            installed_entrypoint = destination / "SKILL.md"
            self.assertLessEqual(installed_entrypoint.stat().st_size, 18_000)
            self.assertTrue((destination / "references" / "routing-and-spark.md").is_file())
            self.assertTrue((destination / "references" / "claude-runtime.md").is_file())

    def test_main_prints_bootstrap_next_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = pathlib.Path(tmp) / "skills"
            old_get = self.module.get_codex_skills_dir
            self.module.get_codex_skills_dir = lambda: str(skills_dir)
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.module.main([])
                output = stdout.getvalue()
            finally:
                self.module.get_codex_skills_dir = old_get

            self.assertIn("Next step for each target repository", output)
            self.assertIn("--bootstrap-current", output)
            self.assertIn("--bootstrap-repo", output)
            self.assertIn("Convenient update command", output)
            self.assertIn("update_skill.py", output)
            self.assertIn("ai/dispatch-to-claude.sh is missing", output)
            self.assertIn("--update-workflow-files", output)
            self.assertIn("Context intelligence check", output)
            self.assertIn("CodeGraph CLI:", output)
            self.assertIn("CodeGraph init:", output)
            self.assertIn("install_context_tools.py", output)
            self.assertIn("Optional code-search services", output)
            self.assertIn("code-search-service.py", output)
            self.assertIn("Non-interactive install; skipping service prompt", output)
            self.assertTrue((skills_dir / "ai-coding-workflow" / "SKILL.md").exists())

    def test_main_bootstrap_repo_installs_workflow_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = pathlib.Path(tmp) / "skills"
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            old_get = self.module.get_codex_skills_dir
            self.module.get_codex_skills_dir = lambda: str(skills_dir)
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.module.main(["--bootstrap-repo", str(repo), "--code-search-services", "skip"])
            finally:
                self.module.get_codex_skills_dir = old_get

            self.assertTrue((repo / "AGENTS.md").exists())
            self.assertTrue((repo / "CLAUDE.md").exists())
            self.assertTrue((repo / "ai" / "dispatch-to-claude.sh").exists())
            self.assertTrue((repo / "ai" / "doctor_workflow.py").exists())
            self.assertTrue((repo / "ai" / "code-search-service.py").exists())
            self.assertIn("Context intelligence check", stdout.getvalue())
            self.assertIn("CodeGraph index for", stdout.getvalue())
            self.assertIn("Skipped by --code-search-services=skip", stdout.getvalue())

    def test_parse_args_accepts_code_search_services_skip(self):
        args = self.module.parse_args(["--code-search-services", "skip"])
        self.assertEqual(args.code_search_services, "skip")

    def test_detect_context_tools_reports_codegraph_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()

            status = self.module.detect_context_tools(str(repo))
            self.assertIn("lsp", status)
            self.assertIn("codegraph_cli", status)
            self.assertFalse(status["codegraph_initialized"])

            (repo / ".codegraph").mkdir()
            status = self.module.detect_context_tools(str(repo))
            self.assertTrue(status["codegraph_initialized"])

    def test_update_skill_helper_builds_install_command(self):
        helper = ROOT / "scripts" / "update_skill.py"
        spec = importlib.util.spec_from_file_location("update_skill", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            installer = source / "scripts" / "install_for_codex.py"
            installer.write_text("ok\n", encoding="utf-8")

            resolved_source, resolved_installer = module.validate_source(str(source))
            self.assertEqual(pathlib.Path(resolved_source), source)
            self.assertEqual(pathlib.Path(resolved_installer), installer)

            args = module.parse_args(["--source", str(source), "--bootstrap-repo", "/tmp/repo"])
            cmd = module.build_install_command(str(installer), args)
            self.assertIn(str(installer), cmd)
            self.assertIn("--bootstrap-repo", cmd)
            self.assertIn("/tmp/repo", cmd)


class TestParseArgsAutoSetup(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_auto_setup_accepted(self):
        args = self.module.parse_args(["--auto-setup", "/tmp/repo"])
        self.assertEqual(args.auto_setup, "/tmp/repo")
        self.assertFalse(args.apply)

    def test_auto_setup_with_apply(self):
        args = self.module.parse_args(["--auto-setup", "/tmp/repo", "--apply"])
        self.assertEqual(args.auto_setup, "/tmp/repo")
        self.assertTrue(args.apply)

    def test_apply_without_auto_setup_errors(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--apply"])


class TestDetectRepoProfiles(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def _track_all(self, repo):
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)

    def test_detects_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "main.py").write_text("pass\n")
            self._track_all(tmp)
            profiles = self.module.detect_repo_profiles(tmp)
            self.assertEqual(profiles, {"python"})

    def test_detects_multiple_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "main.py").write_text("pass\n")
            (pathlib.Path(tmp) / "index.ts").write_text("const x = 1;\n")
            (pathlib.Path(tmp) / "lib.go").write_text("package lib\n")
            (pathlib.Path(tmp) / "lib.rs").write_text("fn main() {}\n")
            self._track_all(tmp)
            profiles = self.module.detect_repo_profiles(tmp)
            self.assertEqual(profiles, {"python", "node", "go", "rust"})

    def test_empty_repo_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = self.module.detect_repo_profiles(tmp)
            self.assertEqual(profiles, set())

    def test_ignores_untracked_vendor_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "node_modules" / "pkg").mkdir(parents=True)
            (pathlib.Path(tmp) / "node_modules" / "pkg" / "index.js").write_text("x\n")
            (pathlib.Path(tmp) / "main.py").write_text("pass\n")
            subprocess.run(["git", "init"], cwd=tmp, capture_output=True, check=True)
            subprocess.run(["git", "add", "main.py"], cwd=tmp, capture_output=True, check=True)
            profiles = self.module.detect_repo_profiles(tmp)
            self.assertEqual(profiles, {"python"})


class TestClassifyRepoScale(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_negative_is_unknown(self):
        self.assertEqual(self.module.classify_repo_scale(-1), "unknown")

    def test_small(self):
        self.assertEqual(self.module.classify_repo_scale(0), "small")
        self.assertEqual(self.module.classify_repo_scale(100), "small")
        self.assertEqual(self.module.classify_repo_scale(500), "small")

    def test_medium(self):
        self.assertEqual(self.module.classify_repo_scale(501), "medium")
        self.assertEqual(self.module.classify_repo_scale(5000), "medium")

    def test_large(self):
        self.assertEqual(self.module.classify_repo_scale(5001), "large")


class TestCountTrackedFiles(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_counts_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, capture_output=True, check=True)
            (pathlib.Path(tmp) / "a.txt").write_text("a\n")
            subprocess.run(["git", "add", "a.txt"], cwd=tmp, capture_output=True, check=True)
            count = self.module.count_tracked_files(tmp)
            self.assertEqual(count, 1)

    def test_returns_negative_for_non_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.module.count_tracked_files(tmp), -1)


class TestSelectInstallPlan(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def _make_ctx(self, tools, available=False):
        ctx = MagicMock()
        ctx.SUGGESTIONS = {"test": tools}
        ctx._is_available = MagicMock(return_value=available)
        return ctx

    def test_prefers_pip_over_apt(self):
        tool = {"name": "tool-a", "check": ["tool-a"],
                "commands": {"pip": ["pip", "install", "tool-a"],
                             "apt": ["apt", "install", "-y", "tool-a"]}}
        ctx = self._make_ctx([tool])
        with patch("shutil.which") as mock:
            mock.side_effect=lambda c: "/usr/bin/" + c if c == "pip" else None
            plan, manual = self.module.select_install_plan("test", ctx)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0][1], ["pip", "install", "tool-a"])
        self.assertEqual(manual, [])

    def test_reports_manual_when_no_manager(self):
        tool = {"name": "tool-a", "check": ["tool-a"],
                "commands": {"apt": ["apt", "install", "-y", "tool-a"]}}
        ctx = self._make_ctx([tool])
        with patch("shutil.which", return_value=None):
            plan, manual = self.module.select_install_plan("test", ctx)
        self.assertEqual(plan, [])
        self.assertEqual(manual, ["tool-a"])

    def test_skips_available_tools(self):
        tool = {"name": "tool-a", "check": ["tool-a"],
                "commands": {"pip": ["pip", "install", "tool-a"]}}
        ctx = self._make_ctx([tool], available=True)
        plan, manual = self.module.select_install_plan("test", ctx)
        self.assertEqual(plan, [])
        self.assertEqual(manual, [])


class TestPlanCodegraph(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_reuse_when_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / ".codegraph").mkdir()
            result = self.module.plan_codegraph(tmp, False, "large", 5000)
            self.assertEqual(result["status"], "reuse")

    def test_skip_small_repos(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.module.plan_codegraph(tmp, False, "small", 50)
            self.assertEqual(result["status"], "skip")

    def test_manual_when_cli_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value=None):
                result = self.module.plan_codegraph(tmp, False, "large", 5000)
            self.assertEqual(result["status"], "manual")

    def test_preview_plan_when_cli_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/codegraph"):
                result = self.module.plan_codegraph(tmp, False, "large", 5000)
            self.assertEqual(result["status"], "plan")
            self.assertIsNone(result.get("argv"))

    def test_apply_sets_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/codegraph"):
                result = self.module.plan_codegraph(tmp, True, "large", 5000)
            self.assertEqual(result["status"], "install")
            self.assertEqual(result["argv"], ["codegraph", "init"])


class TestPlanZoekt(unittest.TestCase):
    """Zoekt routing tests mock shutil.which to be machine-independent."""

    def setUp(self):
        self.module = load_module()

    def test_skip_non_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.module.plan_zoekt(tmp, tmp, False, "medium")
            self.assertEqual(result["status"], "skip")
            self.assertIn("large", result["detail"])

    def test_reuse_when_binaries_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/zoekt"):
                result = self.module.plan_zoekt(tmp, tmp, False, "large")
            self.assertEqual(result["status"], "reuse")

    def test_plan_when_binaries_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value=None):
                result = self.module.plan_zoekt(tmp, tmp, False, "large")
            self.assertEqual(result["status"], "plan")
            self.assertIn("missing", result["detail"])

    def test_apply_invokes_helper_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = pathlib.Path(tmp) / "skill"
            (skill / "scripts").mkdir(parents=True)
            helper = skill / "scripts" / "code-search-service.py"
            helper.write_text("# stub\n")
            with patch("shutil.which", return_value=None):
                result = self.module.plan_zoekt(tmp, str(skill), True, "large")
            self.assertEqual(result["status"], "install")
            self.assertEqual(result["argv"][0], sys.executable)
            self.assertEqual(result["argv"][-2:], ["install-zoekt", "--yes"])

    def test_apply_blocked_when_helper_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value=None):
                result = self.module.plan_zoekt(tmp, "/nonexistent/skill", True, "large")
            self.assertEqual(result["status"], "blocked")


class TestRunAutoSetup(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def _make_repo(self, tmp):
        repo = pathlib.Path(tmp) / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("pass\n")
        subprocess.run(["git", "init"], cwd=str(repo),
                       capture_output=True, check=True)
        subprocess.run(["git", "add", "."], cwd=str(repo),
                       capture_output=True, check=True)
        return repo

    def _make_skill(self, tmp):
        skill = pathlib.Path(tmp) / "skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "install_context_tools.py").write_text(
            "SUGGESTIONS = {}\nALL_MANAGERS = []\n"
            "def _is_available(c): return True\n",
            encoding="utf-8",
        )
        return skill

    def test_preview_no_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            skill = self._make_skill(tmp)
            with patch("shutil.which", return_value=None):
                result = self.module.run_auto_setup(str(repo), str(skill), apply=False)
            self.assertFalse(result["apply"])
            self.assertFalse(result["workflow_ready"])
            self.assertIsNone(result["workflow_result"])
            self.assertEqual(result["lsp_results"], {})
            self.assertIsNone(result["codegraph_result"])
            self.assertIsNone(result["zoekt_result"])

    def test_cli_preview_does_not_install_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            home = pathlib.Path(tmp) / "home"
            home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--auto-setup", str(repo)],
                cwd=str(ROOT), env=env, text=True, encoding="utf-8",
                errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / ".codex").exists())

    def test_apply_bootstraps_missing_project_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            skill = self._make_skill(tmp)
            with patch.object(self.module, "run_bootstrap") as bootstrap, \
                 patch("shutil.which", return_value=None):
                result = self.module.run_auto_setup(str(repo), str(skill), apply=True)
            bootstrap.assert_called_once_with(str(skill), os.path.abspath(str(repo)))
            self.assertTrue(result["workflow_result"])

    def test_apply_runs_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            skill = self._make_skill(tmp)
            with patch("shutil.which", return_value=None):
                result = self.module.run_auto_setup(str(repo), str(skill), apply=True)
            self.assertTrue(result["apply"])

    def test_report_is_deterministic(self):
        result = {
            "repo": "/tmp/repo", "file_count": 100, "scale": "small",
            "profiles": ["python"],
            "lsp_plans": {"python": ([("pyright", ["pip", "install", "pyright"])], [])},
            "lsp_results": {},
            "codegraph": {"component": "codegraph", "status": "skip",
                          "detail": "small repository (100 files)"},
            "codegraph_result": None,
            "zoekt": {"component": "zoekt", "status": "skip",
                      "detail": "small repository"},
            "zoekt_result": None,
            "apply": False,
        }
        report = self.module.format_auto_setup_report(result)
        self.assertIn("Auto-setup for: /tmp/repo", report)
        self.assertIn("Repository scale: small", report)
        self.assertIn("CodeGraph: skip", report)
        self.assertIn("Zoekt: skip", report)
        # Preview mode shows the command, not a status
        self.assertIn("pip install pyright", report)


if __name__ == "__main__":
    unittest.main()
