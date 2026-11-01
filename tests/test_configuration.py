import copy
import json
from pathlib import Path
import unittest

from captain_tunnel.configuration import validate_config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "config/captain_tunnel.example.json"
        self.cfg = json.loads(path.read_text(encoding="utf-8"))

    def test_example_is_valid(self):
        self.assertEqual(validate_config(self.cfg)["server"]["port"], 22)

    def test_string_port_is_normalized(self):
        self.cfg["tunnels"][0]["local_port"] = "18080"
        self.assertEqual(validate_config(self.cfg)["tunnels"][0]["local_port"], 18080)

    def test_invalid_ports_rejected(self):
        for value in (0, 65536, True, 22.5, None, "bad"):
            with self.subTest(value=value):
                cfg = copy.deepcopy(self.cfg)
                cfg["server"]["port"] = value
                with self.assertRaises(ValueError):
                    validate_config(cfg)

    def test_duplicate_local_ports_rejected(self):
        self.cfg["tunnels"].append(copy.deepcopy(self.cfg["tunnels"][0]))
        with self.assertRaisesRegex(ValueError, "중복"):
            validate_config(self.cfg)

    def test_invalid_rows_fail_before_network_use(self):
        for row in (None, {}, {"name": ""}):
            with self.subTest(row=row):
                cfg = copy.deepcopy(self.cfg)
                cfg["tunnels"] = [row]
                with self.assertRaises(ValueError):
                    validate_config(cfg)

    def test_keepalive_cannot_crash_supervisor(self):
        for key in ("keepalive_interval", "keepalive_count"):
            self.cfg["server"][key] = "invalid"
            with self.assertRaises(ValueError):
                validate_config(self.cfg)
            self.cfg["server"][key] = 30
