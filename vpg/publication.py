"""Check the explicit public file inventory without exporting project data."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from .util import PKG_ROOT

FORBIDDEN_PARTS = {".vpg", ".impeccable", ".env", "work", "preferences", "case-studies", "history"}
FORBIDDEN_FILES = {"reactions.jsonl", "library-index.json", "library-map.json"}
CACHE_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
BINARY_TYPES = {".png", ".gif", ".ttf"}
SECRET_PATTERNS = (
    rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    rb"\bxox[baprs]-[A-Za-z0-9-]{15,}",
    rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",
    rb"://[^/\s:]+:[^@\s/]{8,}@",
)


class PublicationError(ValueError):
    pass


def inventory(root: Path) -> set[str]:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True)
    if result.returncode == 0 and Path(result.stdout.strip()).resolve() == root.resolve():
        result = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                                 "--exclude-standard", "-z"], capture_output=True, check=True)
        return {name for name in result.stdout.decode().split("\0") if name and (root / name).exists()}
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not CACHE_PARTS.intersection(p.relative_to(root).parts)
            and p.name != ".DS_Store"}


def private_terms(path: Path | None, root: Path) -> list[str]:
    if path is None:
        return []
    if path.resolve().is_relative_to(root.resolve()):
        raise PublicationError("the private term list must remain outside the toolkit checkout")
    data = json.loads(path.read_text())
    values = data.get("terms") if isinstance(data, dict) else data
    if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
        raise PublicationError("the private term list must be a non-empty-string list or an object with a terms list")
    return values


def check(root: Path = PKG_ROOT, terms_path: Path | None = None) -> list[str]:
    config = json.loads((root / "publication.json").read_text())
    if config.get("schema") != "vpg-starter-publication/v1":
        raise PublicationError("unsupported public file inventory")
    names = config.get("files")
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names) or len(names) != len(set(names)):
        raise PublicationError("public inventory files must be a list of unique paths")
    terms = [re.compile(re.escape(term), re.I) for term in private_terms(terms_path, root)]
    secrets = [re.compile(pattern) for pattern in SECRET_PATTERNS]
    errors = []
    for name in names:
        rel = PurePosixPath(name)
        if not name or rel.is_absolute() or ".." in rel.parts or "\\" in name:
            errors.append(f"{name}: unsafe publication path")
            continue
        if FORBIDDEN_PARTS.intersection(rel.parts) or rel.name in FORBIDDEN_FILES or rel.name.startswith(".env"):
            errors.append(f"{name}: private runtime data is not publishable")
            continue
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
            errors.append(f"{name}: symlinks are not publishable")
            continue
        if not path.is_file():
            errors.append(f"{name}: required public file is missing")
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in secrets):
            errors.append(f"{name}: possible credential (value withheld)")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if path.suffix not in BINARY_TYPES:
                errors.append(f"{name}: unreviewed binary type")
            continue
        home_pattern = re.escape("/" + "Users" + "/") + r"[^/\s]+/|[A-Z]:\\Users\\"
        if re.search(home_pattern, text):
            errors.append(f"{name}: machine-specific home path")
        if any(pattern.search(text) for pattern in terms):
            errors.append(f"{name}: private term matched (value withheld)")
    for name in sorted(inventory(root) - set(names)):
        errors.append(f"{name}: not in the reviewed public inventory")
    return errors
