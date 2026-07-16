import base64
import os
import tempfile
import unittest
from pathlib import Path

from app.oas_admin_lite.catalog import CatalogService, analyze_acl, build_dashboard, extract_catalog_items, infer_counts, normalize_items
from app.oas_admin_lite.config import AppConfig, load_config, parse_simple_yaml
from app.oas_admin_lite.scripts_runner import ScriptService, allowed_scripts
from app.oas_admin_lite.storage import JobStore
from app.oas_admin_lite.web import AppContext, runtime_link_url, script_form_state, script_preview_matches, script_request, scripts_page


class ConfigTests(unittest.TestCase):
    def test_parse_nested_lists(self):
        data = parse_simple_yaml(Path("configs/app.yaml.sample").read_text(encoding="utf-8"))
        self.assertEqual(data["server"]["listen"], "127.0.0.1:18080")
        self.assertNotIn("datamodel.sh", data["scripts"]["allowed"])
        self.assertIn("runcat.sh", data["scripts"]["allowed"])
        self.assertIn("exportarchive.sh", data["scripts"]["allowed"])
        self.assertIn("diagnostic_dump.sh", data["scripts"]["allowed"])
        self.assertIn("/u01/stage/patches", data["patch"]["allowed_patch_dirs"])
        self.assertEqual(data["oas"]["catalog_base_url"], "https://bi-internal.example.com")
        self.assertEqual(data["oas"]["catalog_api_path"], "/api/20210901/catalog")

    def test_load_local_config(self):
        cfg = load_config("configs/app.local.yaml")
        self.assertEqual(cfg.paths.root, ".local/oas-admin-lite")
        self.assertEqual(cfg.scripts.allowed, ["diagnostic_dump.sh", "exportarchive.sh", "runcat.sh"])
        self.assertEqual(cfg.oas.catalog_api_path, "/mock/catalog")
        self.assertEqual(cfg.ohs.domain_home, ".local/mock/ohs_domain")
        self.assertEqual(cfg.ohs.http_port, "7777")
        self.assertTrue(cfg.ohs.monitor_local)

    def test_importarchive_is_blocked(self):
        cfg = load_config("configs/app.local.yaml")
        cfg.scripts.allowed = ["diagnostic_dump.sh", "datamodel.sh", "importarchive.sh", "exportarchive.sh"]
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



    def test_script_run_rejects_concurrent_execution(self):
        cfg = load_config("configs/app.local.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            service = ScriptService(cfg, store)
            service._run_lock.acquire()
            try:
                with self.assertRaises(RuntimeError) as err:
                    service.run("diagnostic_dump.sh", "/u01/oas-admin-lite/bundles/test.zip")
            finally:
                service._run_lock.release()
            self.assertIn("실행 중", str(err.exception))

    def test_diagnostic_dump_form_uses_zip_file_name(self):
        script, args, stdin_text, stdin_label = script_request({
            "script": ["diagnostic_dump.sh"],
            "arg_mode": ["diagnostic"],
            "diagnostic_zip": ["/u01/oas-admin-lite/bundles/aa.zip"],
        })
        self.assertEqual(script, "diagnostic_dump.sh")
        self.assertEqual(args, "/u01/oas-admin-lite/bundles/aa.zip")
        self.assertEqual(stdin_text, "")
        self.assertEqual(stdin_label, "")
        with self.assertRaises(ValueError):
            script_request({"script": ["diagnostic_dump.sh"], "arg_mode": ["diagnostic"]})


    def test_exportarchive_uses_password_file_path_without_reading_it(self):
        script, args, stdin_text, stdin_label = script_request({
            "script": ["exportarchive.sh"],
            "arg_mode": ["exportarchive"],
            "service_instance": ["bootstrap"],
            "export_dir": ["/u01/oas-admin-lite/backups"],
            "export_options": ["includedata"],
            "stdin_file": ["/u01/oas-admin-lite/backups/exportpwd.txt"],
        })
        self.assertEqual(script, "exportarchive.sh")
        self.assertEqual(args, "bootstrap /u01/oas-admin-lite/backups includedata")
        self.assertEqual(stdin_text, "")
        self.assertEqual(stdin_label, "/u01/oas-admin-lite/backups/exportpwd.txt")

    def test_exportarchive_rejects_cli_password_option(self):
        with self.assertRaises(ValueError) as cli:
            script_request({
                "script": ["exportarchive.sh"],
                "arg_mode": ["exportarchive"],
                "service_instance": ["bootstrap"],
                "export_dir": ["/u01/oas-admin-lite/backups"],
                "export_options": ["encryptionpassword=Admin123"],
                "stdin_file": ["/u01/oas-admin-lite/backups/exportpwd.txt"],
            })
        self.assertIn("encryptionpassword", str(cli.exception))

    def test_script_form_state_excludes_stdin_secret(self):
        state = script_form_state({
            "script": ["exportarchive.sh"],
            "arg_mode": ["exportarchive"],
            "service_instance": ["ssi"],
            "export_dir": ["/u01/oas-admin-lite/backups/export"],
            "export_options": ["includedata"],
            "stdin_file": ["/u01/oas-admin-lite/backups/exportpwd.txt"],
            "stdin_text": ["secret-password"],
        })
        self.assertEqual(state["service_instance"], "ssi")
        self.assertEqual(state["export_dir"], "/u01/oas-admin-lite/backups/export")
        self.assertEqual(state["stdin_file"], "/u01/oas-admin-lite/backups/exportpwd.txt")
        self.assertNotIn("stdin_text", state)
        self.assertNotIn("secret-password", str(state))

    def test_script_preview_must_match_current_form(self):
        preview = script_form_state({
            "script": ["diagnostic_dump.sh"],
            "arg_mode": ["diagnostic"],
            "diagnostic_zip": ["/u01/oas-admin-lite/bundles/first.zip"],
        })
        self.assertTrue(script_preview_matches(preview, dict(preview)))
        changed = dict(preview)
        changed["diagnostic_zip"] = "/u01/oas-admin-lite/bundles/changed.zip"
        self.assertFalse(script_preview_matches(preview, changed))

    def test_runtime_link_url_allows_safe_catalog_path(self):
        self.assertEqual(runtime_link_url("/catalog"), "/catalog")
        self.assertEqual(runtime_link_url("//untrusted.example"), "")


    def test_script_preview_uses_stdin_file_without_password_leak(self):
        cfg = load_config("configs/app.local.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            service = ScriptService(cfg, store)
            service.preview("exportarchive.sh", "ssi /tmp/export", "", "/tmp/exportpwd.txt")
            state = service.state_dict()
            self.assertIn("exportarchive.sh", state["last_command"])
            self.assertIn("ssi", state["last_command"])
            self.assertIn("< /tmp/exportpwd.txt", state["last_command"])
            self.assertNotIn("secret-password", state["last_command"])
            self.assertEqual(state["last_job_type"], "script_command_check")
            self.assertIn("명령어 미리보기만 생성했습니다", state["last_output"])

    def test_scripts_page_separates_preview_from_actual_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.paths.root = tmp
            cfg.paths.data_dir = os.path.join(tmp, "data")
            cfg.paths.log_dir = os.path.join(tmp, "logs")
            cfg.paths.backup_dir = os.path.join(tmp, "backups")
            cfg.paths.bundle_dir = os.path.join(tmp, "bundles")
            cfg.paths.package_dir = os.path.join(tmp, "packages")
            cfg.oas.bitools_bin = "/u01/data/domains/bi/bitools/bin"
            ctx = AppContext(cfg)
            ctx.scripts.preview("diagnostic_dump.sh", "/u01/oas-admin-lite/bundles/test.zip")
            html = scripts_page(ctx, {"script": ["diagnostic_dump.sh"]})

            self.assertIn("content-scripts", html)
            self.assertIn("목적", html)
            self.assertIn("실행 구문 형식", html)
            self.assertIn("필수 파라미터", html)
            self.assertIn("옵션 파라미터", html)
            self.assertIn("파라미터 입력", html)
            self.assertIn("입력 완료(명령어 확인)", html)
            self.assertIn("명령어 확인 및 실행", html)
            self.assertIn("쉘 스크립트 + 파라미터", html)
            self.assertIn(">실행</button>", html)
            self.assertIn("최근 실제 실행 결과", html)
            self.assertIn("data-script-running-status", html)
            self.assertIn("실행 중...", html)
            self.assertIn("미리보기로 생성된 명령어입니다", html)
            self.assertNotIn("명령어 미리보기만 생성했습니다", html)
            self.assertNotIn("<span class=\"step-number\">3</span>", html)


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


    def test_catalog_summary_excludes_system_account_folders(self):
        items = normalize_items([
            {"id": "root", "name": "Shared", "type": "folders", "owner": "System Account", "path": "/shared"},
            {"id": "book-1", "name": "Sales", "type": "workbooks", "owner": "analyst", "path": "/shared/Sales"},
        ])
        summary = build_dashboard("endpoint", "endpoint", "admin", 1, "SUCCESS", "HTTP 200 OK", "application/json", "ok", items, [], [], {"checked": 0, "eligible": 0, "risk_total": 0, "broad_write": 0, "permission_management": 0, "acl_failed": 0})
        self.assertEqual(summary["total_assets"], 1)
        self.assertNotIn("folders", summary["counts"])
        self.assertEqual(summary["folder_rows"], [{"folder": "/shared", "count": 1}])

    def test_catalog_type_endpoint_includes_search_wildcard(self):
        cfg = load_config("configs/app.local.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            service = CatalogService(cfg, store)
            endpoint = service._catalog_type_endpoint("datasets", 1)
        self.assertIn("/catalog/datasets?", endpoint)
        self.assertIn("manageContent=true", endpoint)
        self.assertIn("search=%2A", endpoint)
        self.assertIn("limit=500", endpoint)
        self.assertIn("page=1", endpoint)

    def test_type_endpoint_items_can_use_type_hint(self):
        payload = {"items": [{"id": "dataset-1", "name": "Sales Dataset", "owner": "bi_admin", "lastModified": "2026-07-04T08:00:00Z"}]}
        items = normalize_items(extract_catalog_items(payload, type_hint="datasets"), type_hint="datasets")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "datasets")
        self.assertEqual(items[0]["name"], "Sales Dataset")

    def test_catalog_type_only_scan_has_empty_message(self):
        cfg = load_config("configs/app.local.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(os.path.join(tmp, "test.db"))
            service = CatalogService(cfg, store)

            def request_json(_endpoint, method="GET", data=None):
                return [{"type": "analysis"}], {"http_status": "HTTP 200 OK", "content_type": "application/json", "headers": {}}

            service._request_json = request_json
            service._scan_type = lambda _type_name, _errors: []
            dashboard = service.scan()

            self.assertEqual(dashboard["status"], "WARN")
            self.assertEqual(dashboard["message"], "")
            self.assertEqual(dashboard["supported_types"], ["analysis"])

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
