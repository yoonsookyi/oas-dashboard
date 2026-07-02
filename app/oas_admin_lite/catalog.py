import json
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

from .storage import Job


class CatalogSummary(object):
    def __init__(self, endpoint, last_scan=0.0, counts=None, message="아직 수집 결과가 없습니다."):
        self.endpoint = endpoint
        self.last_scan = last_scan
        self.counts = counts or {}
        self.message = message

    def to_dict(self):
        return {
            "endpoint": self.endpoint,
            "last_scan": self.last_scan,
            "counts": self.counts,
            "message": self.message,
        }


class CatalogService(object):
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def last_summary(self):
        return self.store.get_json("catalog_summary", CatalogSummary(self.cfg.oas.analytics_url).to_dict())

    def scan(self):
        endpoint = self._catalog_endpoint()
        started = time.time()
        status = "SUCCESS"
        message = ""
        counts = {}
        try:
            req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read(2000000)
                message = "HTTP {0} {1}".format(resp.status, resp.reason)
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type.lower():
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                    counts = infer_counts(parsed)
        except urllib.error.HTTPError as exc:
            status = "FAILED"
            message = "HTTP {0}: {1}".format(exc.code, exc.reason)
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
        summary = CatalogSummary(endpoint=endpoint, last_scan=time.time(), counts=counts, message=message).to_dict()
        self.store.set_json("catalog_summary", summary)
        self.store.add(Job(
            type="catalog_scan",
            command="GET {0}".format(endpoint),
            status=status,
            exit_code=0 if status == "SUCCESS" else 1,
            started_at=started,
            ended_at=time.time(),
            message=message,
        ))
        if status != "SUCCESS":
            raise RuntimeError(message)
        return summary

    def _catalog_endpoint(self):
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