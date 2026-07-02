import json
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

from .storage import Job


class CatalogSummary(object):
    def __init__(self, endpoint, last_scan=0.0, counts=None, message="아직 수집을 실행하지 않았습니다.", status="READY", http_status="", content_type=""):
        self.endpoint = endpoint
        self.last_scan = last_scan
        self.counts = counts or {}
        self.message = message
        self.status = status
        self.http_status = http_status
        self.content_type = content_type

    def to_dict(self):
        return {
            "endpoint": self.endpoint,
            "last_scan": self.last_scan,
            "counts": self.counts,
            "message": self.message,
            "status": self.status,
            "http_status": self.http_status,
            "content_type": self.content_type,
        }


class CatalogService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def last_summary(self):
        return self.store.get_json("catalog_summary", CatalogSummary(self._catalog_endpoint()).to_dict())

    def scan(self):
        endpoint = self._catalog_endpoint()
        started = time.time()
        status = "SUCCESS"
        message = ""
        http_status = ""
        content_type = ""
        counts = {}
        try:
            req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read(2000000)
                code = getattr(resp, "status", None) or resp.getcode()
                reason = getattr(resp, "reason", "")
                http_status = "HTTP {0} {1}".format(code, reason).strip()
                content_type = resp.headers.get("Content-Type", "")
                if "json" not in content_type.lower():
                    status = "FAILED"
                    message = "REST API JSON 응답이 아닙니다. analytics_url 또는 catalog_api_url이 OAS 화면/로그인 페이지를 가리키는지 확인하세요."
                else:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                    counts = infer_counts(parsed)
                    if counts:
                        message = "카탈로그 object type 집계가 완료되었습니다."
                    else:
                        message = "JSON 응답은 받았지만 type/objectType/itemType 필드를 찾지 못했습니다. 실제 OAS Catalog REST 응답 구조에 맞춘 매핑이 필요합니다."
        except urllib.error.HTTPError as exc:
            status = "FAILED"
            http_status = "HTTP {0}: {1}".format(exc.code, exc.reason)
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            message = "OAS REST 호출이 실패했습니다. 인증, 권한, endpoint를 확인하세요."
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
        summary = CatalogSummary(endpoint=endpoint, last_scan=time.time(), counts=counts, message=message, status=status, http_status=http_status, content_type=content_type).to_dict()
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
        base = self.cfg.oas.analytics_url.rstrip("/") + "/"
        return urljoin(base, "")


def infer_counts(value):
    counts = {}
    visit(value, counts)
    return counts


def visit(value, counts):
    if isinstance(value, dict):
        type_value = value.get("type") or value.get("objectType") or value.get("itemType")
        if isinstance(type_value, str):
            counts[type_value] = counts.get(type_value, 0) + 1
        for item in value.values():
            visit(item, counts)
    elif isinstance(value, list):
        for item in value:
            visit(item, counts)