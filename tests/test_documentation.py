from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_troubleshooting_cast_is_valid_asciinema_v2(self) -> None:
        lines = (ROOT / "docs" / "troubleshooting.cast").read_text(
            encoding="utf-8"
        ).splitlines()
        header = json.loads(lines[0])
        self.assertEqual(2, header["version"])
        self.assertGreater(len(lines), 10)
        events = [json.loads(line) for line in lines[1:]]
        self.assertTrue(all(len(event) == 3 and event[1] == "o" for event in events))
        transcript = "".join(event[2] for event in events)
        self.assertIn("diagnose_service", transcript)
        self.assertIn("plan_mutation", transcript)
        self.assertIn("confirm_mutation", transcript)

    def test_claude_guide_never_embeds_a_token(self) -> None:
        guide = (ROOT / "docs" / "claude-clients.md").read_text(encoding="utf-8")
        self.assertIn("${MCP_HUB_TOKEN}", guide)
        self.assertIn("--transport http", guide)
        self.assertIn("MCP_READ_ONLY=true", guide)
        self.assertNotIn("Bearer changeme", guide)

    def test_environment_reference_covers_every_runtime_variable(self) -> None:
        sources = (
            (ROOT / "config.py").read_text(encoding="utf-8")
            + (ROOT / "server.py").read_text(encoding="utf-8")
        )
        runtime = set(
            re.findall(
                r'(?:env|env_bool|env_int|env_path)\("([A-Z][A-Z0-9_]+)"',
                sources,
            )
        )
        runtime.update(
            re.findall(r'os\.environ\.get\("([A-Z][A-Z0-9_]+)"', sources)
        )
        reference = (ROOT / "docs" / "environment.md").read_text(encoding="utf-8")
        documented = set(
            re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", reference, re.MULTILINE)
        )
        self.assertEqual(runtime, documented)

    def test_environment_example_covers_the_complete_reference(self) -> None:
        reference = (ROOT / "docs" / "environment.md").read_text(encoding="utf-8")
        documented = set(
            re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", reference, re.MULTILINE)
        )
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        represented = set(
            re.findall(r"^#?([A-Z][A-Z0-9_]+)=", example, re.MULTILINE)
        )
        self.assertEqual(documented, represented)

    def test_readme_example_links_exist(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        examples = set(re.findall(r"\]\(([^)]+\.example(?:\.[a-z]+)?)\)", readme))
        self.assertGreaterEqual(len(examples), 4)
        self.assertEqual([], sorted(path for path in examples if not (ROOT / path).is_file()))

    def test_private_project_instructions_template_is_tracked_and_safe(self) -> None:
        template = (ROOT / "PROJECT_INSTRUCTIONS.example.md").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("PROJECT_INSTRUCTIONS.md", gitignore)
        self.assertIn("Copy this file to `PROJECT_INSTRUCTIONS.md`", template)
        self.assertIn("Do-Not-Touch Rules", template)

    def test_local_testing_and_docker_docs_are_linked(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/testing-local.md", readme)
        self.assertIn("docs/docker-packaging.md", readme)
        self.assertTrue((ROOT / "docs" / "testing-local.md").is_file())
        self.assertTrue((ROOT / "docs" / "docker-packaging.md").is_file())

    def test_http_transport_doc_exists_and_mentions_required_headers(self) -> None:
        guide = (ROOT / "docs" / "http-transport-2026-07-28.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("MCP-Protocol-Version", guide)
        self.assertIn("Mcp-Method", guide)
        self.assertIn("Mcp-Name", guide)
        self.assertIn("sticky-session", guide)

    def test_generated_tool_reference_is_current(self) -> None:
        result = subprocess.run(
            ["python3", "scripts/generate_tool_reference.py", "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
