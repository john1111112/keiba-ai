"""Offline verification of officially supported 32/64-bit JV-Link selection."""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src import jvlink_client


class JVLinkInterpreterSelectionTests(unittest.TestCase):
    def test_explicit_interpreter_accepts_verified_architecture(self):
        with patch("src.jvlink_client.platform.system", return_value="Windows"), \
             patch.dict(os.environ, {"KEIBA_JVLINK_PYTHON": sys.executable}), \
             patch("src.jvlink_client._python_bitness", return_value=64):
            self.assertEqual(jvlink_client.find_jvlink_python(), sys.executable)

    def test_invalid_override_is_not_silently_ignored(self):
        with patch("src.jvlink_client.platform.system", return_value="Windows"), \
             patch.dict(os.environ, {"KEIBA_JVLINK_PYTHON": r"C:\\missing\\python.exe"}):
            self.assertIsNone(jvlink_client.find_jvlink_python())

    def test_auto_chooses_working_64bit_com_when_32bit_com_missing(self):
        win32 = types.ModuleType("win32com")
        win32.__path__ = []
        client_mod = types.ModuleType("win32com.client")
        win32.client = client_mod
        with patch("src.jvlink_client.platform.system", return_value="Windows"), \
             patch.dict(sys.modules, {"win32com": win32, "win32com.client": client_mod}), \
             patch.dict(os.environ, {
                 "KEIBA_JVLINK_PYTHON": "", "KEIBA_JVLINK_PREFER_64BIT": "0"
             }), \
             patch("src.jvlink_client.struct.calcsize", return_value=8), \
             patch("src.jvlink_client.find_32bit_python", return_value="python32.exe"), \
             patch("src.jvlink_client._probe_jvlink_com",
                   side_effect=lambda path: path == sys.executable):
            self.assertEqual(jvlink_client.find_jvlink_python(), sys.executable)

    def test_worker_command_enables_64bit_when_selected(self):
        commands = []
        def completed_command(command, **kwargs):
            commands.append(command)
            out = Path(command[command.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "success": False, "auth_status": "UNVERIFIED",
                "fetch_status": "NO_DATA_AVAILABLE",
                "records_count": 0, "data_spec": "0B15",
                "sid": "UNKNOWN", "timestamp": int(time.time()),
            }), encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("src.jvlink_client.find_jvlink_python", return_value=sys.executable), \
             patch("src.jvlink_client._python_bitness", return_value=64), \
             patch("src.jvlink_client.subprocess.run", side_effect=completed_command):
            client = jvlink_client.JVLinkClient()
            response = client.fetch_via_32bit_worker(
                "0B15", "20261027000000", output_path=Path(tmp) / "temp.json"
            )
            self.assertEqual(response["fetch_status"], "NO_DATA_AVAILABLE")
            self.assertIn("--allow-64bit", commands[0])
            self.assertFalse(list(Path(tmp).glob("temp.json.*.tmp")))
            client.fetch_via_32bit_worker(
                "0B15", "20261027000000", output_path=Path(tmp) / "temp.json"
            )
            self.assertEqual(len(commands), 2)
            self.assertEqual(client._worker_interpreter_cache[1], 64)

    def test_32bit_worker_does_not_use_64bit_switch(self):
        commands = []
        def completed_command(command, **kwargs):
            commands.append(command)
            out = Path(command[command.index("--out") + 1])
            out.write_text(json.dumps({
                "success": False, "auth_status": "UNVERIFIED",
                "fetch_status": "NO_DATA_AVAILABLE",
                "records_count": 0, "data_spec": "0B15",
                "sid": "UNKNOWN", "timestamp": int(time.time()),
            }), encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, \
             patch("src.jvlink_client.find_jvlink_python", return_value="python32.exe"), \
             patch("src.jvlink_client._python_bitness", return_value=32), \
             patch("src.jvlink_client.subprocess.run", side_effect=completed_command):
            client = jvlink_client.JVLinkClient()
            result = client.fetch_via_32bit_worker(
                "0B15", "20261027000000", output_path=Path(tmp) / "out.json"
            )
            self.assertEqual(result["fetch_status"], "NO_DATA_AVAILABLE")
            self.assertNotIn("--allow-64bit", commands[0])


if __name__ == "__main__":
    unittest.main()
