"""Local stdlib tests; installed-vLLM behavioral check is --validate."""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import apply_fix


class PatchTests(unittest.TestCase):
    def test_cli_apply_writes_patch_and_default_mode_is_read_only(self):
        source = (
            "class Manager:\n"
            "    def get_parser(cls, reasoning_parser_cls, tool_parser_cls):\n"
            + apply_fix.SHORTCUT
            + "        return ('adapters', reasoning_parser_cls, tool_parser_cls)\n"
        )
        digest = hashlib.sha256(source.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parser_manager.py"
            path.write_text(source)
            with patch.object(apply_fix, "SOURCE_SHA256", digest):
                expected = apply_fix.patch_source(source)
                with patch.object(sys, "argv", ["apply_fix.py", "--source", str(path)]):
                    apply_fix.main()
                self.assertEqual(path.read_text(), source)
                with patch.object(
                    sys, "argv", ["apply_fix.py", "--source", str(path), "--apply"]
                ):
                    apply_fix.main()
                self.assertEqual(path.read_text(), expected)
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    hashlib.sha256(expected.encode()).hexdigest(),
                )

    def test_unknown_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            apply_fix.patch_source("class ParserManager: pass\n")

    def test_only_shared_engine_shortcut_is_removed(self):
        source = (
            "class Manager:\n"
            "    def get_parser(cls, reasoning_parser_cls, tool_parser_cls):\n"
            + apply_fix.SHORTCUT
            + "        return ('adapters', reasoning_parser_cls, tool_parser_cls)\n"
            "    def _get_parser_engine_cls(cls):\n"
            "        return 'engine'\n"
        )
        digest = hashlib.sha256(source.encode()).hexdigest()
        with patch.object(apply_fix, "SOURCE_SHA256", digest):
            result = apply_fix.patch_source(source)
            namespace = {}
            exec(result, namespace)
            manager = namespace["Manager"]()
            self.assertEqual(
                manager.get_parser("reasoning", "tools"),
                ("adapters", "reasoning", "tools"),
            )
            with self.assertRaisesRegex(ValueError, "Unrecognized"):
                apply_fix.patch_source(result)

    def test_missing_or_duplicate_anchor_is_refused(self):
        for count in (0, 2):
            source = apply_fix.SHORTCUT * count
            with patch.object(
                apply_fix, "SOURCE_SHA256", hashlib.sha256(source.encode()).hexdigest()
            ):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    apply_fix.patch_source(source)

    def test_cli_apply_does_not_modify_unknown_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parser_manager.py"
            path.write_text("# unknown\n")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(apply_fix.__file__)),
                    "--apply",
                    "--source",
                    str(path),
                ],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(), "# unknown\n")


if __name__ == "__main__":
    unittest.main()
