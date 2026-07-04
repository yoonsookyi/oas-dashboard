import base64
import os
import tempfile
import unittest
from pathlib import Path

from app.oas_admin_lite.catalog import CatalogService, analyze_acl, build_dashboard, infer_counts, normalize_items
from app.oas_admin_lite.config import load_config, parse_simple_yaml
from app.oas_admin_lite.scripts_runner import ScriptService, allowed_scripts
from app.oas_admin_lite.storage import JobStore
from app.oas_admin_lite.web import script_request


class ConfigTests(unittest.TestCase):
    def test_parse_nested_lists(self):
        data = parse_simple_yaml(Path("configs/app.yaml.sample").read_text(encoding="utf-8"))
        self.assertEqual(data["server"]["listen"], "127.0.0.1:18080")
        self.assertNotIn("datamodel.sh", data["scripts"]["allowed"])
        self.assertNotIn("runcat.sh", data["scripts"]["allowed"])
        self.assertIn("exportarchive.sh", data["scripts"]["allowed"])
        self.assertIn("diagnostic_dump.sh", data["scripts"]["allowed"])
        self.assertIn("/u01/stage/patches", data["patch"]["allowed_patch_dirs"])
        self.assertEqual(data["oas"]["catalog_base_url"], "http://localhost:7777")
        self.assertEqual(data["oas"]["catalog_api_path"], "/api/20210901/catalog")

    def test_load_local_config(self):
        cfg = load_config("configs/app.local.yaml")
        self.assertEqual(cfg.paths.root, ".local/oas-admin-lite")
        self.assertEqual(cfg.scripts.allowed, ["diagnostic_dump.sh", "exportarchive.sh"])
        self.assertEqual(cfg.oas.catalog_api_path, "/mock/catalog")

    def test_importarchive_is_blocked(self):
        cfg = load_config("configs/app.local.yaml")
        cfg.scripts.allowed = ["diagnostic_dump.sh", "importarchive.sh", "exportarchive.sh"]
        self.assertEqual(allowed_scripts(cfg.scripts.allowed), ["diagnostic_dump.sh", "exportarchive.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            store.set_json("script_state", {"last_command": "/tmp/datamodel.sh downloadrpd", "last_output": "legacy"})
            service = ScriptService(cfg, store)
            state = service.state_dict()
            self.assertNotIn("importarchive.sh", state["allowed"])
            self.assertNotIn("datamodel.sh", state["allowed"])
            self.assertNotIn("runcat.sh", state["allowed"])
            self.assertEqual(state["last_command"], "")
            with self.assertRaises(ValueError):
                service.preview("importarchive.sh", "")


    def test_diagnostic_dump_form_drops_arguments(self):
        script, args, stdin_text = script_request({
            "script": ["diagnostic_dump.sh"],
            "arg_mode": ["diagnostic"],
            "args": ["aa.zip"],
        })
        self.assertEqual(script, "diagnostic_dump.sh")
        self.assertEqual(args, "")
        self.assertEqual(stdin_text, "")


    def test_script_preview_uses_stdin_without_command_leak(self):
        cfg = load_config("configs/app.local.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            service = ScriptService(cfg, store)
            service.preview("exportarchive.sh", "ssi /tmp/export", "secret-password")
            state = service.state_dict()
            self.assertIn("exportarchive.sh", state["last_command"])
            self.assertIn("ssi", state["last_command"])
            self.assertNotIn("secret-password", state["last_command"])
            self.assertIn("stdin 입력: 있음", state["last_output"])

    def test_catalog_dashboard_summary(self):
        items = normalize_items([
            {"id": "L0BDYXRhbG9nL3NoYXJlZC9GaW5hbmNlL1JldmVudWU=", "name": "Revenue", "type": "analysis", "owner": "finance_ops", "lastModified": "2026-07-01T09:42:00Z"},
            {"id": "L0BDYXRhbG9nL3NoYXJlZC9TYWxlcy9FeGVj", "name": "Executive", "type": "dashboard", "owner": "bi_admin", "lastModified": "2026-06-30T14:18:00Z"},
            {"id": "legacy", "name": "Legacy", "type": "analysis", "owner": "", "lastModified": "2024-03-11T22:05:00Z", "parentId": "archive"},
        ])
        items[1]["aclRisk"] = "WARN"
        items[2]["aclRisk"] = "FAILED"
        summary = build_dashboard("http://localhost:7777/api/20210901/catalog", "http://localhost:7777/api/20210901/catalog", "weblogic", 1, "SUCCESS", "HTTP 200 OK", "application/json", "ok", items, ["analysis", "dashboard"], [], {"checked": 2, "eligible": 3, "risk_total": 2, "broad_write": 1, "permission_management": 1, "acl_failed": 0})
        self.assertEqual(summary["total_assets"], 3)
        self.assertEqual(summary["counts"]["analysis"], 2)
        self.assertEqual(summary["owner_count"], 3)
        self.assertEqual(summary["owners"][0]["owner"], "finance_ops")
        self.assertEqual(summary["acl_summary"]["risk_total"], 2)

    def test_analyze_acl_risk(self):
        risk = analyze_acl([{"accountDisplayName": "Authenticated User", "permissions": {"read": True, "write": True, "changePermission": False}}])
        self.assertEqual(risk["level"], "FAILED")
        self.assertEqual(risk["broad_write"], 1)
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