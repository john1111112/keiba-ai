import unittest
from unittest.mock import patch
from scripts.capture_jvlink import main


class CaptureCLITests(unittest.TestCase):
    def test_invalid_calendar_date_never_instantiates_sdk(self):
        with patch("scripts.capture_jvlink.open_jvlink") as sdk:
            with self.assertRaises(ValueError):
                main(["--sid", "TEST", "--spec", "0B15",
                      "--from-time", "202613990000", "--confirm-trial-active"])
            sdk.assert_not_called()

    def test_confirmation_required_before_sdk(self):
        with patch("scripts.capture_jvlink.open_jvlink") as sdk:
            with self.assertRaises(SystemExit):
                main(["--sid", "TEST", "--spec", "0B15",
                      "--from-time", "202610270000"])
            sdk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
