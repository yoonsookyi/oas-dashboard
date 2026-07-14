import unittest
from unittest.mock import patch

from app.oas_admin_lite.config import AppConfig
from app.oas_admin_lite.resources import ResourceCollector, catalog_endpoint_check, component_process_check, listener_check, load_metric, oas_component_statuses, process_service_check, process_check, threshold_status


class ResourceStatusTests(unittest.TestCase):
    def test_threshold_status_uses_high_for_fail_level(self):
        self.assertEqual(threshold_status(10, 40, 75), "OK")
        self.assertEqual(threshold_status(40, 40, 75), "WARN")
        self.assertEqual(threshold_status(75, 40, 75), "HIGH")


    def test_oas_checks_report_missing_configured_paths(self):
        cfg = AppConfig()
        cfg.oas.oracle_home = "/missing/oas_home"
        cfg.oas.domain_home = "/missing/domain_home"
        cfg.oas.bitools_bin = "/missing/domain_home/bitools/bin"
        cfg.scripts.allowed = ["diagnostic_dump.sh"]
        cfg.ohs.monitor_local = True
        cfg.ohs.oracle_home = "/missing/ohs_home"
        cfg.ohs.domain_home = "/missing/ohs_domain"

        checks = ResourceCollector(cfg)._oas_checks()
        by_name = {check.name: check for check in checks}

        self.assertEqual(by_name["ORACLE_HOME"].status, "WARN")
        self.assertIn("경로 없음", by_name["ORACLE_HOME"].detail)
        self.assertEqual(by_name["Script diagnostic_dump.sh"].status, "WARN")
        self.assertEqual(by_name["OHS ORACLE_HOME"].status, "WARN")
        self.assertEqual(by_name["OHS DOMAIN_HOME"].status, "WARN")

    def test_oas_checks_skip_ohs_paths_when_ohs_is_remote(self):
        cfg = AppConfig()
        cfg.ohs.monitor_local = False
        names = {check.name for check in ResourceCollector(cfg)._oas_checks()}

        self.assertNotIn("OHS ORACLE_HOME", names)
        self.assertNotIn("OHS DOMAIN_HOME", names)

    def test_listener_check_filters_before_output_is_limited(self):
        class Result(object):
            returncode = 0
            stdout = "LISTEN noise\n" * 1000 + "LISTEN 0 511 *:7777 *:*\n"

        with patch("app.oas_admin_lite.resources.shutil.which", return_value="/usr/bin/ss"):
            with patch("app.oas_admin_lite.resources.subprocess.run", return_value=Result()):
                check = listener_check()

        self.assertEqual(check.status, "OK")
        self.assertIn("*:7777", check.value)
        self.assertIn("명령: ss -lnt", check.detail)
        self.assertIn("\n역할:", check.detail)
        self.assertIn("7777: OHS HTTP/REST", check.detail)

    def test_process_check_filters_before_display_limit(self):
        class Result(object):
            returncode = 0
            stdout = "\n".join(
                ["{0:7d}       1 sleep           sleep 60".format(i) for i in range(1000)]
                + [
                    "  18636   17063 sawserver       /u01/app/Oracle/Middleware/Oracle_Home/bi/bifoundation/web/bin/sawserver",
                    " 632511  632466 java            -Dweblogic.Name=bi_server1 weblogic.Server",
                    "1106395    8401 httpd           /u01/app/Oracle/Middleware/ohs_14.1.2/ohs/bin/httpd",
                ]
            )

        with patch("app.oas_admin_lite.resources.subprocess.run", return_value=Result()):
            check = process_check()

        self.assertEqual(check.status, "OK")
        self.assertIn("sawserver", check.value)
        self.assertIn("bi_server1", check.value)
        self.assertIn("httpd", check.value)
        self.assertIn("역할                         PID", check.value)
        self.assertIn("OAS Presentation Services", check.value)
        self.assertIn("OAS WebLogic Managed", check.value)
        self.assertIn("OHS HTTP Server", check.value)
        self.assertIn("명령: ps -eo pid,ppid,comm,args", check.detail)
        self.assertIn("\n역할:", check.detail)

    def test_runtime_process_service_check_shows_starting_state(self):
        active = process_service_check("OAS BI Server", "/u01/bi/bin/obis1", ["obis"], "query processing")
        waiting = process_service_check("OAS JavaHost", "", ["javahost"], "java processing")

        self.assertEqual(active.status, "OK")
        self.assertEqual(active.value, "실행 중")
        self.assertEqual(waiting.status, "WARN")
        self.assertEqual(waiting.value, "시작 대기")

    def test_oas_component_statuses_parses_status_script_output(self):
        class Result(object):
            stdout = """Name            Type            Machine                   Restart Int Max Restart  Status
obiccs1         OBICCS          oas2026-mp                3600        5000         RUNNING
obisch1         OBISCH          oas2026-mp                3600        5000         STARTING
"""

        cfg = AppConfig()
        with patch("app.oas_admin_lite.resources.os.path.isfile", return_value=True):
            with patch("app.oas_admin_lite.resources.os.access", return_value=True):
                with patch("app.oas_admin_lite.resources.subprocess.run", return_value=Result()):
                    states, error = oas_component_statuses(cfg)

        self.assertEqual(error, "")
        self.assertEqual(states["obiccs1"], "RUNNING")
        self.assertEqual(states["obisch1"], "STARTING")
        check = component_process_check("Scheduler", "obisch1", "nqscheduler", ["nqscheduler"], states, "status.sh last confirmed", "job scheduling")
        self.assertEqual(check.status, "OK")
        self.assertIn("STARTING", check.detail)

    def test_load_metric_exposes_labeled_metadata(self):
        with patch("app.oas_admin_lite.resources.os.getloadavg", return_value=(2.0, 1.5, 1.0)):
            with patch("app.oas_admin_lite.resources.os.cpu_count", return_value=4):
                metric = load_metric()

        self.assertEqual(metric.percent, 50)
        self.assertIn(("수집", "os.getloadavg()"), metric.metadata)
        self.assertIn(("CPU cores", "4"), metric.metadata)

    def test_catalog_endpoint_check_uses_configured_rest_path(self):
        cfg = AppConfig()
        cfg.oas.catalog_base_url = "http://127.0.0.1:7777"
        cfg.oas.catalog_api_path = "/api/20210901/catalog"

        with patch("app.oas_admin_lite.resources.socket.create_connection") as connect:
            connect.return_value.close.return_value = None
            check = catalog_endpoint_check(cfg)

        self.assertEqual(check.status, "OK")
        self.assertIn("127.0.0.1:7777", check.value)


if __name__ == "__main__":
    unittest.main()
