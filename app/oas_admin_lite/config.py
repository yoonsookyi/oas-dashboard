from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ServerConfig:
    listen: str = "127.0.0.1:18080"


@dataclass
class PathsConfig:
    root: str = "/u01/oas-admin-lite"
    data_dir: str = "/u01/oas-admin-lite/data"
    log_dir: str = "/u01/oas-admin-lite/logs"
    backup_dir: str = "/u01/oas-admin-lite/backups"
    bundle_dir: str = "/u01/oas-admin-lite/bundles"
    package_dir: str = "/u01/oas-admin-lite/packages"


@dataclass
class OASConfig:
    oracle_home: str = "/u01/app/oracle/product/fmw"
    domain_home: str = "/u01/app/oracle/config/domains/bi"
    bitools_bin: str = "/u01/app/oracle/config/domains/bi/bitools/bin"
    analytics_url: str = "https://oas.example.com/analytics"


@dataclass
class PatchConfig:
    allowed_patch_dirs: list[str] = field(default_factory=lambda: [
        "/u01/oas-admin-lite/packages/patches",
        "/u01/stage/patches",
    ])


@dataclass
class ScriptsConfig:
    allowed: list[str] = field(default_factory=lambda: [
        "datamodel.sh",
        "diagnostic_dump.sh",
        "exportarchive.sh",
        "importarchive.sh",
    ])


@dataclass
class SecurityConfig:
    username: str = "admin"
    password_sha256: str = ""


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    oas: OASConfig = field(default_factory=OASConfig)
    patch: PatchConfig = field(default_factory=PatchConfig)
    scripts: ScriptsConfig = field(default_factory=ScriptsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    def ensure_dirs(self) -> None:
        for item in [
            self.paths.data_dir,
            self.paths.log_dir,
            os.path.join(self.paths.log_dir, "jobs"),
            self.paths.backup_dir,
            self.paths.bundle_dir,
            self.paths.package_dir,
            os.path.join(self.paths.package_dir, "patches"),
            os.path.join(self.paths.package_dir, "releases"),
            os.path.join(self.paths.package_dir, "rollback"),
        ]:
            Path(item).mkdir(parents=True, exist_ok=True)


def load_config(path: str | None) -> AppConfig:
    cfg = AppConfig()
    if not path:
        path = os.environ.get("OAS_ADMIN_LITE_CONFIG")
    if not path:
        path = os.path.join(cfg.paths.root, "app", "config", "app.yaml")
    if os.path.exists(path):
        data = parse_simple_yaml(Path(path).read_text(encoding="utf-8"))
        apply_mapping(cfg, data)
    normalize_config(cfg)
    return cfg


def parse_simple_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip().lstrip("\ufeff") or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip().lstrip("\ufeff")))
    if not lines:
        return {}
    value, next_index = parse_block(lines, 0, lines[0][0])
    if next_index != len(lines):
        raise ValueError("invalid trailing config lines")
    if not isinstance(value, dict):
        raise ValueError("top-level config must be a mapping")
    return value


def parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    is_list = lines[index][1].startswith("- ")
    if is_list:
        values: list[Any] = []
        while index < len(lines):
            current_indent, content = lines[index]
            if current_indent < indent:
                break
            if current_indent != indent or not content.startswith("- "):
                break
            values.append(parse_scalar(content[2:].strip()))
            index += 1
        return values, index

    values: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError(f"unexpected indentation: {content}")
        if ":" not in content:
            raise ValueError(f"invalid config line: {content}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            values[key] = parse_scalar(raw_value)
        else:
            if index < len(lines) and lines[index][0] > current_indent:
                child, index = parse_block(lines, index, lines[index][0])
                values[key] = child
            else:
                values[key] = {}
    return values, index


def parse_scalar(value: str) -> Any:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    return value


def apply_mapping(cfg: AppConfig, data: dict[str, Any]) -> None:
    for section, values in data.items():
        target = getattr(cfg, section, None)
        if target is None or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, value)


def normalize_config(cfg: AppConfig) -> None:
    root = cfg.paths.root or "/u01/oas-admin-lite"
    cfg.paths.root = root
    cfg.paths.data_dir = cfg.paths.data_dir or os.path.join(root, "data")
    cfg.paths.log_dir = cfg.paths.log_dir or os.path.join(root, "logs")
    cfg.paths.backup_dir = cfg.paths.backup_dir or os.path.join(root, "backups")
    cfg.paths.bundle_dir = cfg.paths.bundle_dir or os.path.join(root, "bundles")
    cfg.paths.package_dir = cfg.paths.package_dir or os.path.join(root, "packages")
    cfg.server.listen = cfg.server.listen or "127.0.0.1:18080"
    cfg.scripts.allowed = cfg.scripts.allowed or ScriptsConfig().allowed
    cfg.patch.allowed_patch_dirs = cfg.patch.allowed_patch_dirs or PatchConfig().allowed_patch_dirs
