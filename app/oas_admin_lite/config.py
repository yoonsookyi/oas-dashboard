import os
from pathlib import Path


class ServerConfig(object):
    def __init__(self):
        self.listen = "127.0.0.1:18080"


class PathsConfig(object):
    def __init__(self):
        self.root = "/u01/oas-admin-lite"
        self.data_dir = "/u01/oas-admin-lite/data"
        self.log_dir = "/u01/oas-admin-lite/logs"
        self.backup_dir = "/u01/oas-admin-lite/backups"
        self.bundle_dir = "/u01/oas-admin-lite/bundles"
        self.package_dir = "/u01/oas-admin-lite/packages"


class OASConfig(object):
    def __init__(self):
        self.oracle_home = "/u01/app/oracle/product/fmw"
        self.domain_home = "/u01/app/oracle/config/domains/bi"
        self.bitools_bin = "/u01/app/oracle/config/domains/bi/bitools/bin"
        self.analytics_url = "https://oas.example.com/analytics"
        self.catalog_api_url = ""


class PatchConfig(object):
    def __init__(self):
        self.allowed_patch_dirs = [
            "/u01/oas-admin-lite/packages/patches",
            "/u01/stage/patches",
        ]


class ScriptsConfig(object):
    def __init__(self):
        self.allowed = [
            "datamodel.sh",
            "diagnostic_dump.sh",
            "exportarchive.sh",
            "importarchive.sh",
        ]


class SecurityConfig(object):
    def __init__(self):
        self.username = "admin"
        self.password_sha256 = ""


class AppConfig(object):
    def __init__(self):
        self.server = ServerConfig()
        self.paths = PathsConfig()
        self.oas = OASConfig()
        self.patch = PatchConfig()
        self.scripts = ScriptsConfig()
        self.security = SecurityConfig()

    def ensure_dirs(self):
        dirs = [
            self.paths.data_dir,
            self.paths.log_dir,
            os.path.join(self.paths.log_dir, "jobs"),
            self.paths.backup_dir,
            self.paths.bundle_dir,
            self.paths.package_dir,
            os.path.join(self.paths.package_dir, "patches"),
            os.path.join(self.paths.package_dir, "releases"),
            os.path.join(self.paths.package_dir, "rollback"),
        ]
        for item in dirs:
            Path(item).mkdir(parents=True, exist_ok=True)


def load_config(path=None):
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


def parse_simple_yaml(text):
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip().lstrip("\ufeff")
        if not stripped or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, stripped))
    if not lines:
        return {}
    value, next_index = parse_block(lines, 0, lines[0][0])
    if next_index != len(lines):
        raise ValueError("invalid trailing config lines")
    if not isinstance(value, dict):
        raise ValueError("top-level config must be a mapping")
    return value


def parse_block(lines, index, indent):
    if index >= len(lines):
        return {}, index
    is_list = lines[index][1].startswith("- ")
    if is_list:
        values = []
        while index < len(lines):
            current_indent, content = lines[index]
            if current_indent < indent:
                break
            if current_indent != indent or not content.startswith("- "):
                break
            values.append(parse_scalar(content[2:].strip()))
            index += 1
        return values, index

    values = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError("unexpected indentation: {0}".format(content))
        if ":" not in content:
            raise ValueError("invalid config line: {0}".format(content))
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


def parse_scalar(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    return value


def apply_mapping(cfg, data):
    for section, values in data.items():
        target = getattr(cfg, section, None)
        if target is None or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, value)


def normalize_config(cfg):
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