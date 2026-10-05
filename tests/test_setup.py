"""Prepare a fixture machine and launch its installed selector entrypoint."""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

from fixture import SkillsetFixture, git


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillset-setup-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture = SkillsetFixture(self.base, {"acme/skills": {"alpha": "alpha"}}, [])
        self.repo = self.fixture.repo
        self.home = self.fixture.home
        self.bin = self.base / "bin"
        self.bin.mkdir()
        # Keep PATH closed so removing one link really hides that prerequisite.
        for tool in ("bash", "sh", "git", "python3", "uv", "dirname", "mkdir", "ln",
                     "basename", "sed", "uname", "tr", "rm"):
            (self.bin / tool).symlink_to(Path(shutil.which(tool)).resolve())
        self.env = dict(self.fixture.env, PATH=str(self.bin))
        # Reuse downloaded wheels, while each fixture gets its own script envs.
        self.env["UV_CACHE_DIR"] = subprocess.check_output(
            ["uv", "cache", "dir"], text=True,
        ).strip()
        self.command = self.home / ".local/bin/select-skills"

    def setup(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.repo / "setup.sh")], cwd=self.home, env=self.env,
            input="", capture_output=True, text=True, timeout=60,
        )

    def test_rerun_keeps_installed_command_and_nonterminal_does_not_launch(self) -> None:
        for _ in range(2):
            result = self.setup()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "Ready. Run: select-skills\n")
            self.assertTrue(self.command.is_symlink())
            self.assertEqual(self.command.resolve(), self.repo / "scripts/select-skills.py")
            self.assertFalse((self.home / ".agents").exists())
            self.assertFalse((self.home / ".claude").exists())

    def test_missing_uv_at_eof_declines_without_downloading_or_initializing(self) -> None:
        (self.bin / "uv").unlink()
        for tool in ("curl", "wget"):
            downloader = self.bin / tool
            downloader.write_text('#!/bin/sh\nprintf downloaded > "$HOME/download-attempt"\nexit 1\n')
            downloader.chmod(0o755)
        git(["submodule", "deinit", "-f", "--all"], self.repo)

        result = self.setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Install uv now? [y/N] ")
        self.assertIn("uv is required", result.stderr)
        self.assertFalse((self.home / "download-attempt").exists())
        self.assertFalse((self.fixture.sources["acme/skills"] / ".git").exists())
        self.assertFalse(self.command.exists())

    def test_old_python_stops_before_uv_prompt_or_source_initialization(self) -> None:
        python = self.bin / "python3"
        python.unlink()
        python.write_text('#!/bin/sh\nprintf "Python 3.10.0\\n"\nexit 1\n')
        python.chmod(0o755)
        (self.bin / "uv").unlink()
        git(["submodule", "deinit", "-f", "--all"], self.repo)

        result = self.setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.11", result.stderr)
        self.assertNotIn("Install uv", result.stdout)
        self.assertNotIn("Ready.", result.stdout)
        self.assertFalse((self.fixture.sources["acme/skills"] / ".git").exists())
        self.assertFalse(self.command.exists())

    def test_rerun_restores_deinitialized_and_wrong_commit_sources(self) -> None:
        source = self.fixture.sources["acme/skills"]
        pin = git(["rev-parse", "HEAD"], source)
        git(["submodule", "deinit", "-f", "--all"], self.repo)
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(git(["rev-parse", "HEAD"], source), pin)

        (source / "README.md").write_text("A newer commit, outside the pin.\n")
        git(["add", "README.md"], source)
        git(["commit", "-qm", "advance source"], source)
        self.assertNotEqual(git(["rev-parse", "HEAD"], source), pin)
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(git(["rev-parse", "HEAD"], source), pin)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")

    def test_warns_only_when_local_bin_is_missing_from_path(self) -> None:
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{self.command.parent} is not on PATH", result.stderr)

        self.env["PATH"] = f"{self.command.parent}:{self.bin}"
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("not on PATH", result.stderr)

    def test_locked_sync_failure_stops_before_linking_or_launching(self) -> None:
        (self.repo / "scripts/select-skills.py.lock").unlink()

        result = self.setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Ready.", result.stdout)
        self.assertFalse(self.command.exists())

    def test_installed_shebang_opens_catalog_offline_in_a_terminal(self) -> None:
        # Start without any cached dependencies to prove setup prepares them.
        self.env["UV_CACHE_DIR"] = str(self.base / "uv-cache")
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        output = bytearray()
        sent_escape = False
        process = subprocess.Popen(
            [str(self.command)], cwd=self.home,
            env=dict(self.env, TERM="xterm-256color", UV_OFFLINE="1"),
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        os.close(slave)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                    if b"Skill selector" in output and not sent_escape:
                        os.write(master, b"\x1b")
                        sent_escape = True
                elif process.poll() is not None:
                    break
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
        transcript = output.decode(errors="replace")
        self.assertIn("Skill selector", transcript)
        self.assertTrue(sent_escape, transcript)
        self.assertEqual(process.returncode, 0, transcript)
        self.assertNotIn("Downloading", transcript)
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
