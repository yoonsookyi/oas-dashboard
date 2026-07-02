import tempfile
import unittest
from pathlib import Path

from app.oas_admin_lite.config import load_config, parse_simple_yaml


class ConfigTests(unittest.TestCase):
    def test_parse_nested_lists(self):
        data = parse_simple_yaml(Path("configs/app.yaml.sample").read_text(encoding="utf-8"))
        self.assertEqual(data["server"]["listen"], "127.0.0.1:18080")
        self.assertIn("datamodel.sh", data["scripts"]["allowed"])
        self.assertIn("/u01/stage/patches", data["patch"]["allowed_patch_dirs"])

    def test_load_local_config(self):
        cfg = load_config("configs/app.local.yaml")
        self.assertEqual(cfg.paths.root, ".local/oas-admin-lite")
        self.assertEqual(cfg.scripts.allowed[0], "datamodel.sh")


if __name__ == "__main__":
    unittest.main()
