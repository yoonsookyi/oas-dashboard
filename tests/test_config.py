import base64
import os
import tempfile
import unittest
from pathlib import Path

from app.oas_admin_lite.catalog import CatalogService, infer_counts
from app.oas_admin_lite.config import load_config, parse_simple_yaml
from app.oas_admin_lite.scripts_runner import ScriptService, allowed_scripts
from app.oas_admin_lite.storage import JobStore


class ConfigTests(unittest.TestCase):
    def test_parse_nested_lists(self):
        data = parse_simple_yaml(Path("configs/app.yaml.sample").read_text(encoding="utf-8"))
        self.assertEqual(data["server"]["listen"], "127.0.0.1:18080")
        self.assertIn("datamodel.sh", data["scripts"]["allowed"])
        self.assertIn("/u01/stage/patches", data["patch"]["allowed_patch_dirs"])
        self.assertEqual(data["oas"]["catalog_base_url"], "http://localhost:7777")
        self.assertEqual(data["oas"]["catalog_api_path"], "/api/20210901/catalog")

    def test_load_local_config(self):
        cfg = load_config("configs/app.local.yaml")
        self.assertEqual(cfg.paths.root, ".local/oas-admin-lite")
        self.assertEqual(cfg.scripts.allowed[0], "datamodel.sh")
        self.assertEqual(cfg.oas.catalog_api_path, "/mock/catalog")

    def test_importarchive_is_blocked(self):
        cfg = load_config("configs/app.local.yaml")
        cfg.scripts.allowed = ["datamodel.sh", "importarchive.sh"]
        self.assertEqual(allowed_scripts(cfg.scripts.allowed), ["datamodel.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            store.set_json("script_state", {"allowed": ["datamodel.sh", "importarchive.sh"]})
            service = ScriptService(cfg, store)
            self.assertNotIn("importarchive.sh", service.state_dict()["allowed"])
            with self.assertRaises(ValueError):
                service.preview("importarchive.sh", "")

    def test_catalog_endpoint_and_basic_auth(self):
        cfg = load_config("configs/app.local.yaml")
        cfg.oas.catalog_base_url = "http://localhost:7777"
        cfg.oas.catalog_api_path = "/api/20210901/catalog"
        cfg.oas.catalog_username = "weblogic"
        cfg.oas.catalog_password = "welcome1"
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            service = CatalogService(cfg, store)
            self.assertEqual(service._catalog_endpoint(), "http://localhost:7777/api/20210901/catalog")
            expected = base64.b64encode(b"weblogic:welcome1").decode("ascii")
            self.assertEqual(service._auth_header(), "Basic " + expected)

    def test_infer_catalog_counts(self):
        payload = {
            "items": [
                {"type": "folder", "name": "Shared"},
                {"objectType": "analysis", "name": "Revenue"},
                {"itemType": "dashboard", "name": "Sales"},
                {"resourceType": "dataModel", "name": "DM"},
            ]
        }
        self.assertEqual(infer_counts(payload), {
            "folder": 1,
            "analysis": 1,
            "dashboard": 1,
            "dataModel": 1,
        })


if __name__ == "__main__":
    unittest.main()