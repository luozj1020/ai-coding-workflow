import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest


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

    def test_skill_entrypoint_keeps_routing_context(self):
        skill = ROOT / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        self.assertIn("When To Load More", content)
        self.assertIn("Builder", content)
        self.assertIn("Checker/Test", content)

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
            self.assertIn("Context intelligence check", output)
            self.assertIn("CodeGraph CLI:", output)
            self.assertIn("CodeGraph init:", output)
            self.assertIn("install_context_tools.py", output)
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
                    self.module.main(["--bootstrap-repo", str(repo)])
            finally:
                self.module.get_codex_skills_dir = old_get

            self.assertTrue((repo / "AGENTS.md").exists())
            self.assertTrue((repo / "CLAUDE.md").exists())
            self.assertTrue((repo / "ai" / "dispatch-to-claude.sh").exists())
            self.assertTrue((repo / "ai" / "doctor_workflow.py").exists())
            self.assertIn("Context intelligence check", stdout.getvalue())
            self.assertIn("CodeGraph index for", stdout.getvalue())

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


if __name__ == "__main__":
    unittest.main()
