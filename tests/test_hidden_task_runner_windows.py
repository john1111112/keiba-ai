"""Windows-only smoke test for the one-minute invisible task wrapper.

Does NOT run KEIBA AI, JRA-VAN, or Discord; an isolated fake batch confirms that
the VBScript passes the exit status through without showing a cmd window.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows Script Host required")
class HiddenTaskRunnerWindowsTests(unittest.TestCase):
    def test_hidden_wrapper_runs_same_batch_and_preserves_exit_code(self):
        src = Path(__file__).resolve().parents[1] / "scripts" / "run_scheduled_task_hidden.vbs"
        with tempfile.TemporaryDirectory(prefix="keiba hidden runner ") as folder:
            script_dir = Path(folder)
            shutil.copyfile(src, script_dir / "run_scheduled_task_hidden.vbs")
            (script_dir / "run_scheduled_task.bat").write_bytes(
                b'@echo off\r\necho OK>"%~dp0hidden_runner_ran.txt"\r\nexit /b 17\r\n'
            )
            cscript = Path(os.environ["WINDIR"]) / "System32" / "cscript.exe"
            result = subprocess.run(
                [str(cscript), "//B", "//NoLogo",
                 str(script_dir / "run_scheduled_task_hidden.vbs")],
                capture_output=True, timeout=15, check=False
            )
            self.assertEqual(result.returncode, 17, result.stderr.decode(errors="replace"))
            self.assertTrue((script_dir / "hidden_runner_ran.txt").exists())


if __name__ == "__main__":
    unittest.main()
