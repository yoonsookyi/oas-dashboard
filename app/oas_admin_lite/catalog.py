import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

from .storage import Job


TYPE_KEYS = ("type", "objectType", "itemType", "resourceType", "catalogObjectType", "contentType")


class CatalogSummary(object):
    def __init__(self, endpoint, last_scan=0.0, counts=None, message="아직 수집을 실행하지 않았습니다.", status="READY", http_status="", content_type="", auth_user=""):
        self.endpoint = endpoint
        self.last_scan = last_scan
        self.counts = counts or {}
        self.message = message
        self.status = status
        self.http_status = http_status
        self.content_type = content_type
        self.auth_user = auth_user

    def to_dict(self):
        return {
            "endpoint": self.endpoint,
            "last_scan": self.last_scan,
            "counts": self.counts,
            "message": self.message,
            "status": self.status,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "auth_user": self.auth_user,
        }


class CatalogService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def last_summary(self):
        return self.store.get_json("catalog_summary", CatalogSummary(self._catalog_endpoint(), auth_user=self._username()).to_dict())

    def scan(self):
        endpoint = self._catalog_endpoint()
        username = self._username()
        started = time.time()
        status = "SUCCESS"
        message = ""
        http_status = ""
        content_type = ""
        counts = {}
        try:
            headers = {"Accept": "application/json"}
            auth_header = self._auth_header()
            if auth_header:
                headers["Authorization"] = auth_header
            req = urllib.request.Request(endpoint, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read(5000000)
                code = getattr(resp, "status", None) or resp.getcode()
                reason = getattr(resp, "reason", "")
                http_status = "HTTP {0} {1}".format(code, reason).strip()
                content_type = resp.headers.get("Content-Type", "")
                if "json" not in content_type.lower():
                    status = "FAILED"
                    message = "REST API JSON 응답이 아닙니다. catalog_base_url/catalog_api_path 또는 인증 후 redirect 여부를 확인하세요."
                else:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                    counts = infer_counts(parsed)
                    if counts:
                        message = "카탈로그 object type 집계가 완료되었습니다."
                    else:
                        message = "JSON 응답은 받았지만 알려진 type 필드를 찾지 못했습니다. 응답 구조 확인 후 매핑을 추가해야 합니다."
        except urllib.error.HTTPError as exc:
            status = "FAILED"
            http_status = "HTTP {0}: {1}".format(exc.code, exc.reason)
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            if exc.code in (401, 403):
                message = "OAS REST 인증 또는 권한이 거부되었습니다. catalog_username/password를 확인하세요."
            else:
                message = "OAS REST 호출이 실패했습니다. endpoint, 인증, 권한을 확인하세요."
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
        summary = CatalogSummary(endpoint=endpoint, last_scan=time.time(), counts=counts, message=message, status=status, http_status=http_status, content_type=content_type, auth_user=username).to_dict()
        self.store.set_json("catalog_summary", summary)
        self.store.add(Job(
            type="catalog_scan",
            command="GET {0}".format(endpoint),
            status=status,
            exit_code=0 if status == "SUCCESS" else 1,
            started_at=started,
            ended_at=time.time(),
            message="{0} {1}".format(http_status, message).strip(),
        ))
        if status != "SUCCESS":
            raise RuntimeError(message)
        return summary

    def _catalog_endpoint(self):
        catalog_api_url = getattr(self.cfg.oas, "catalog_api_url", "") or ""
        if catalog_api_url.strip():
            return catalog_api_url.strip()
        base = (getattr(self.cfg.oas, "catalog_base_url", "") or "").strip()
        path = (getattr(self.cfg.oas, "catalog_api_path", "") or "").strip()
        if base and path:
            return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        base = self.cfg.oas.analytics_url.rstrip("/") + "/"
        return urljoin(base, "")

    def _username(self):
        return (os.environ.get("OAS_ADMIN_LITE_CATALOG_USERNAME") or getattr(self.cfg.oas, "catalog_username", "") or "").strip()

    def _password(self):
        return os.environ.get("OAS_ADMIN_LITE_CATALOG_PASSWORD") or getattr(self.cfg.oas, "catalog_password", "") or ""

    def _auth_header(self):
        username = self._username()
        password = self._password()
        if not username:
            return ""
        token = base64.b64encode(("{0}:{1}".format(username, password)).encode("utf-8")).decode("ascii")
        return "Basic {0}".format(token)


def infer_counts(value):
    counts = {}
    visit(value, counts)
    return counts


def visit(value, counts):
    if isinstance(value, dict):
        type_value = None
        for key in TYPE_KEYS:
            if isinstance(value.get(key), str):
                type_value = value.get(key)
                break
        if type_value:
            counts[type_value] = counts.get(type_value, 0) + 1
        for item in value.values():
            visit(item, counts)
    elif isinstance(value, list):
        for item in value:
            visit(item, counts)