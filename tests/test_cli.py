import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from captain_tunnel.cli import main


class CliTests(unittest.TestCase):
    def test_version_without_gui(self):
        with patch.object(sys, "argv", ["captain-tunnel", "--version"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertIn("0.1.0", output.getvalue())

    def test_help_without_gui(self):
        with patch.object(sys, "argv", ["captain-tunnel", "--help"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertIn("--service", output.getvalue())

    def test_invalid_arguments_do_not_launch_gui(self):
        for args in (["--typo"], ["--service"], ["--service", "delete"], ["--headless", "--service-run"]):
            with self.subTest(args=args), patch.object(sys, "argv", ["captain-tunnel", *args]), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 2)
