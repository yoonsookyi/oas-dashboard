from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin

from .config import AppConfig
from .storage import Job, JobStore


@dataclass
class CatalogSummary:
    endpoint: str
    last_scan: float = 0.0
    counts: dict[str, int] | None = None
    message: str = "아직 수집 결과가 없습니다."

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "last_scan": self.last_scan,
            "counts": self.counts or {},
            "message": self.message,
        }


class CatalogService:
    def __init__(self, cfg: AppConfig, store: JobStore):
        self.cfg = cfg
        self.store = store

    def last_summary(self) -> dict:
        return self.store.get_json("catalog_summary", CatalogSummary(self.cfg.oas.analytics_url).to_dict())

    def scan(self) -> dict:
        # This first implementation verifies REST reachability and stores raw shape hints.
        # The exact OAS REST endpoint can be adjusted in app.yaml after the customer confirms the exposed API path.
        endpoint = self._catalog_endpoint()
        started = time.time()
        status = "SUCCESS"
        message = ""
        counts: dict[str, int] = {}
        try:
            req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read(2_000_000)
                message = f"HTTP {resp.status} {resp.reason}"
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type.lower():
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                    counts = infer_counts(parsed)
        except urllib.error.HTTPError as exc:
            status = "FAILED"
            message = f"HTTP {exc.code}: {exc.reason}"
        except Exception as exc:
            status = "FAILED"
            message = str(exc)
        summary = CatalogSummary(endpoint=endpoint, last_scan=time.time(), counts=counts, message=message).to_dict()
        self.store.set_json("catalog_summary", summary)
        self.store.add(Job(
            type="catalog_scan",
            command=f"GET {endpoint}",
            status=status,
            exit_code=0 if status == "SUCCESS" else 1,
            started_at=started,
            ended_at=time.time(),
            message=message,
        ))
        if status != "SUCCESS":
            raise RuntimeError(message)
        return summary

    def _catalog_endpoint(self) -> str:
        base = self.cfg.oas.analytics_url.rstrip("/") + "/"
        return urljoin(base, "")


def infer_counts(value) -> dict[str, int]:
    counts: dict[str, int] = {}
    visit(value, counts)
    return counts


def visit(value, counts: dict[str, int]) -> None:
    if isinstance(value, dict):
        type_value = value.get("type") or value.get("objectType") or value.get("itemType")
        if isinstance(type_value, str):
            counts[type_value] = counts.get(type_value, 0) + 1
        for item in value.values():
            visit(item, counts)
    elif isinstance(value, list):
        for item in value:
            visit(item, counts)
