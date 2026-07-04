import unittest
from unittest.mock import patch

from app.oas_admin_lite.resources import process_check, threshold_status


class ResourceStatusTests(unittest.TestCase):
    def test_threshold_status_uses_high_for_fail_level(self):
        self.assertEqual(threshold_status(10, 40, 75), "OK")
        self.assertEqual(threshold_status(40, 40, 75), "WARN")
        self.assertEqual(threshold_status(75, 40, 75), "HIGH")

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


if __name__ == "__main__":
    unittest.main()
