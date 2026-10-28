"""Group indexed videos into films, versions and other collections. Pure functions over path strings and metadata;
every decision carries a short 'why' that the UI shows.

Roles come from the file name and path, films from the nearest meaningful root (a VPG project, a video/ folder of a
project, an archive batch, a Downloads folder or a repo folder), versions from the governed run state of a VPG project,
path segments under the film root and the file name. A film needs at least one master, review, picture or rough video;
everything else is listed under "Other videos", grouped by folder.
"""
from __future__ import annotations

import hashlib
import os
import re

ROLE_RANK = {"master": 0, "review": 1, "picture": 3, "rough": 4, "silent": 5, "clip": 6, "other": 7,
             "source": 8, "recording": 8, "eval": 8, "published": 8}
FILM_ROLES = frozenset({"master", "review", "picture", "rough"})
VERSION_ROLES = FILM_ROLES | {"silent"}
OTHER_ONLY_ROLES = frozenset({"source", "recording", "eval", "published"})
ROLES = ("master", "review", "silent", "picture", "rough", "clip", "recording", "eval", "source", "published", "other")
GENERIC = frozenset({
    "src", "source", "sources", "assets", "public", "static", "site", "_site", "www", "build", "dist", "out", "output",
    "outputs", "work", "media", "files", "data", "content", "tmp", "temp", "scratch", "renders", "render", "exports",
    "export", "video", "videos", "film", "films", "clips", "docs", "artifacts", "runs", "revisions", "projects", "vpg",
    "archive", "archives", "downloads", "desktop", "documents", "movies", "github", "candidates", "package", "packages",
})
SOURCE_SEGMENTS = frozenset({"sources", "source", "capture", "captures", "capture_tenant", "references", "reference",
                             "refs", "inputs", "assets", "prototypes-hd"})
FINISHED_FOLDERS = frozenset({"candidates", "releases", "release", "delivery", "deliveries", "deliverables"})
FILM_FOLDERS = frozenset({"video", "videos", "film"})
SITE_SEGMENTS = frozenset({"_site", "public"})
VARIANT_PARENT_NAMES_GENERIC = frozenset({"vpg", "video", "videos", "film", "work", "runs", "variants", "candidates"})
_NUMBERED = re.compile(r"^\d{2}[-_ ]")
_CLIP_PREFIX = re.compile(r"^(seg|segment|clip|scene|take)(\d|[-_ ]|$)")
_CLIP_INNER = re.compile(r"[-_ ](clip|seg|segment|scene)[-_ ]?\d")
_NN_NAME = re.compile(r"^\d{2}[-_ ](\d{2}[-_ ])?(?!(master|review|silent|final)\b)[a-z]")
_CC_MINS = re.compile(r"\bcc \d+\s*mins?\b")
_REEL = re.compile(r"(^|[-_ ])reel($|[-_ ])")
_ROUND_RV = re.compile(r"^r(\d+)-v\d+$")
_ROUND = re.compile(r"^round-(\d+)$")
_VERSION_SEG = re.compile(r"^v\d+[a-z0-9]*$")
_CAND = re.compile(r"^cand-[a-z]-[\w-]+$", re.I)
_VPG_V = re.compile(r"^vpg-v\d+-[\w-]+$", re.I)
_FILM_V = re.compile(r"^film-(v\d+[a-z0-9]*)$", re.I)
_ATTEMPT = re.compile(r"^a\d+$")
_DATE = re.compile(r"^(\d{8}(-\d{4,6})?|\d{4}-\d{2}-\d{2})$")
_NAME_TOKEN = re.compile(r"(?<![a-z0-9])(r\d{2,}|v\d+[a-z]?)(?![a-z0-9])")
_NATURAL = re.compile(r"(\d+)")
_FAMILY_SUFFIX = re.compile(r"#[a-z](?![a-z])")


def short_id(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def natural_key(text):
    return [int(part) if part.isdigit() else part.lower() for part in _NATURAL.split(str(text or ""))]


def family_of(label):
    """'v25', 'v3' and 'v14a' share the family 'v#'; versions are ordered family by family."""
    return _FAMILY_SUFFIX.sub("#", _NATURAL.sub("#", str(label or "").lower()))


def portfolio_path(path):
    parts = path.lower().replace(os.sep, "/").split("/")
    return any(part == "portfolio" or part.endswith("_portfolio") for part in parts)


# ------------------------------------------------------------------------------------------------ roles
def classify_role(path, below=None):
    """(role, why) from the file name and its folders; `below` is the path from the library root down, so folder
    names above the roots are ignored (the portfolio rule still looks at the whole path)."""
    parts = (below or path).split(os.sep)
    folders = [p.lower() for p in parts[:-1]]
    name = parts[-1]
    stem = os.path.splitext(name)[0].lower()
    low = path.lower().replace(os.sep, "/")
    if portfolio_path(path) and ("/src/assets/work/" in low or "/_site/" in low):
        return "published", "a copy on a portfolio site"
    hit = next((seg for seg in folders if seg in SITE_SEGMENTS), None)
    if hit:
        return "published", f"site build output ({hit}/)"
    hit = next((seg for seg in folders if seg in SOURCE_SEGMENTS), None)
    if hit:
        return "source", f"under {hit}/ (source material, not a film)"
    if stem.startswith("eval_episode") or stem.startswith("wrist"):
        return "eval", "evaluation clip (eval_episode or wrist camera)"
    if "recording" in stem or "meeting" in stem or _CC_MINS.search(stem):
        return "recording", "a recording (Recording, Meeting or 'CC NN mins' in the name)"
    if "silent" in stem:
        return "silent", "'silent' in the name"
    if (_CLIP_PREFIX.match(stem) or _CLIP_INNER.search(stem) or _NN_NAME.match(stem) or re.fullmatch(r"b\d+", stem)
            or re.fullmatch(r"file-\d+", stem) or stem.startswith("gslides-s") or stem.endswith(".visual")):
        return "clip", "a clip or segment name (seg, clip, scene, take, NN-name, B01, file-N)"
    if re.search(r"[-_]review$", stem) or "review-cut" in stem:
        return "review", "'-review' or 'review-cut' in the name"
    if stem == "master" or re.search(r"[-_]master($|[-_])", stem):
        return "master", "named master"
    if "final" in stem:
        return "master", "'final' in the name"
    if stem in ("film", "video"):
        return "picture", f"named {stem} (picture only, as VPG's N13 renders it)"
    if (stem.startswith(("rough-cut", "proof", "preview", "motion-test", "animatic", "preflight")) or "preview" in stem
            or _REEL.search(stem)):
        return "rough", "a rough cut, proof, preview or reel, motion test, animatic or preflight render"
    hit = next((seg for seg in reversed(folders[-2:]) if seg in FINISHED_FOLDERS), None)
    if hit:
        return "master", f"a finished cut in a {hit}/ folder"
    return "other", "no role pattern matched"


# ------------------------------------------------------------------------------------------------ film roots
class Context:
    """What grouping needs to know about the machine: home, library roots, VPG projects and discovered jobs.

    projects: {project dir: {name, id, candidates: bool, governed: {relative video path: [label, why]}}}."""

    def __init__(self, home, roots, projects=None, jobs=None, overrides=None):
        self.home = os.path.abspath(home)
        self.roots = [os.path.abspath(r) for r in roots]
        self.projects = {os.path.abspath(k): v for k, v in (projects or {}).items()}
        self.jobs = {os.path.abspath(k): v for k, v in (jobs or {}).items()}
        self.overrides = overrides or []
        self.archive = os.path.join(self.home, "Downloads", "Archive")
        self.downloads = os.path.join(self.home, "Downloads")
        self.github = os.path.join(self.home, "GitHub")
        children = {}
        self.governed = {}
        self.versioned = set()
        for project, meta in self.projects.items():
            children.setdefault(os.path.dirname(project), []).append(project)
            governed = meta.get("governed") if isinstance(meta, dict) and isinstance(meta.get("governed"), dict) else {}
            for rel, entry in governed.items():
                if isinstance(entry, (list, tuple)) and len(entry) == 2:
                    self.governed[os.path.join(project, rel)] = (str(entry[0]), str(entry[1]))
            if governed or (isinstance(meta, dict) and meta.get("candidates")):
                self.versioned.add(project)
        self._project_children = children

    def root_of(self, path):
        matches = [r for r in self.roots if path == r or path.startswith(r.rstrip(os.sep) + os.sep)]
        return max(matches, key=len) if matches else os.path.dirname(path)

    def area(self, path):
        rel = os.path.relpath(path, self.home).split(os.sep)
        if rel and rel[0] == "GitHub" and len(rel) > 2:
            return rel[1]
        if rel[:2] == ["Downloads", "Archive"]:
            return "Downloads/Archive"
        if rel and rel[0] in ("Downloads", "Desktop", "Movies", "Documents") and len(rel) > 1:
            return rel[0]
        root = self.root_of(path)
        return os.path.basename(root) or root

    def below_root(self, path):
        """The path from its library root down (with a leading separator), so folders above the roots never count."""
        root = self.root_of(path)
        return os.sep + os.path.relpath(path, root) if path.startswith(root + os.sep) else path

    def archived(self, path):
        """Under ~/Downloads/Archive, or any folder below the root whose name starts with 'archive' (also '_archive')."""
        if path.startswith(self.archive + os.sep):
            return True
        return any(seg.lower().lstrip("_").startswith("archive") for seg in self.below_root(path).split(os.sep)[:-1] if seg)

    def is_container(self, folder):
        """A folder that cannot be a film on its own: a library root, home, a generic or numbered container."""
        name = os.path.basename(folder)
        return (folder in self.roots or folder == self.home or not name or name.lower() in GENERIC
                or bool(_NUMBERED.match(name)) or name.startswith("."))

    def variant_parent(self, project):
        """A non-project folder (the project's parent or grandparent) that holds two or more sibling VPG projects."""
        for parent in (os.path.dirname(project), os.path.dirname(os.path.dirname(project))):
            if parent in self.projects or parent in self.roots or parent == self.home or parent == self.archive:
                return None
            if len(self._project_children.get(parent, [])) >= 2:
                return parent
        return None


def _meaningful(ctx, base, parts):
    """The first folder under `base` (from `parts`) that is not a generic or numbered container."""
    current = base
    for seg in parts:
        current = os.path.join(current, seg)
        if not ctx.is_container(current):
            return current
    return None


def film_root(path, ctx):
    """{root, name, kind, why, archived, job_pid, batch[, variant]} for a video path."""
    folder = os.path.dirname(path)
    archived = ctx.archived(path)
    batch = None
    if path.startswith(ctx.archive + os.sep):
        rel = os.path.relpath(path, ctx.archive).split(os.sep)
        batch = rel[0] if len(rel) > 1 else None
    ancestor = folder
    limit = ctx.root_of(path)
    while ancestor.startswith(limit) and ancestor != limit:
        if ancestor in ctx.projects:
            meta = ctx.projects[ancestor] if isinstance(ctx.projects[ancestor], dict) else {}
            parent = ctx.variant_parent(ancestor)
            if parent:
                name = os.path.basename(parent)
                if name.lower() in VARIANT_PARENT_NAMES_GENERIC:
                    name = f"{batch or os.path.basename(os.path.dirname(parent))} / {name}"
                return {"root": parent, "name": name, "kind": "vpg-variants", "archived": archived, "batch": batch,
                        "job_pid": None, "variant": ancestor,
                        "why": f"VPG project {os.path.basename(ancestor)} is one of several variant projects in "
                               f"{os.path.basename(parent)}/, so they form one film with one version per variant"}
            name = meta.get("name") or os.path.basename(ancestor)
            return {"root": ancestor, "name": name, "kind": "vpg", "archived": archived, "batch": batch,
                    "job_pid": None if archived else ctx.jobs.get(ancestor),
                    "why": f"nearest vpg.project.yaml ({os.path.relpath(ancestor, ctx.home)})"}
        ancestor = os.path.dirname(ancestor)
    ancestor = folder
    while ancestor.startswith(limit) and ancestor != limit:
        parent = os.path.dirname(ancestor)
        if os.path.basename(ancestor).lower() in FILM_FOLDERS and not ctx.is_container(parent):
            return {"root": ancestor, "name": os.path.basename(parent), "kind": "video-folder", "archived": archived,
                    "batch": batch, "job_pid": None,
                    "why": f"the {os.path.basename(ancestor)}/ folder of {os.path.basename(parent)}"}
        ancestor = parent
    rel_parts = os.path.relpath(folder, ctx.home).split(os.sep) if folder.startswith(ctx.home + os.sep) else []
    if batch:
        batch_root = os.path.join(ctx.archive, batch)
        under = os.path.relpath(folder, batch_root).split(os.sep) if folder != batch_root else []
        nxt = under[0] if under and under[0] != "." else None
        if nxt and not ctx.is_container(os.path.join(batch_root, nxt)):
            return {"root": os.path.join(batch_root, nxt), "name": f"{batch} / {nxt}", "kind": "archive", "archived": True,
                    "batch": batch, "job_pid": None, "why": f"archive batch {batch}, folder {nxt}"}
        return {"root": batch_root, "name": batch, "kind": "archive", "archived": True, "batch": batch, "job_pid": None,
                "why": f"archive batch {batch}"}
    for base, label in ((ctx.downloads, "Downloads"), (os.path.join(ctx.home, "Desktop"), "Desktop"),
                        (os.path.join(ctx.home, "Movies"), "Movies"), (os.path.join(ctx.home, "Documents"), "Documents")):
        if folder.startswith(base + os.sep):
            parts = os.path.relpath(folder, base).split(os.sep)
            root = _meaningful(ctx, base, parts)
            if root:
                return {"root": root, "name": os.path.basename(root), "kind": "folder", "archived": archived, "batch": None,
                        "job_pid": None, "why": f"{label} folder {os.path.relpath(root, base)}"}
    if folder.startswith(ctx.github + os.sep) and len(rel_parts) >= 2:
        repo_root = os.path.join(ctx.github, rel_parts[1])
        root = _meaningful(ctx, repo_root, rel_parts[2:])
        if root:
            return {"root": root, "name": f"{rel_parts[1]} / {os.path.basename(root)}", "kind": "repo-folder",
                    "archived": archived, "batch": None, "job_pid": None,
                    "why": f"repo {rel_parts[1]}, first named folder {os.path.relpath(root, repo_root)}"}
        return {"root": repo_root, "name": rel_parts[1], "kind": "repo", "archived": archived, "batch": None,
                "job_pid": None, "why": f"repo {rel_parts[1]}"}
    return {"root": folder, "name": os.path.basename(folder) or folder, "kind": "folder", "archived": archived,
            "batch": None, "job_pid": None, "why": "the parent folder"}


# ------------------------------------------------------------------------------------------------ versions
def version_of(path, root, attempts_bucket=False):
    """(label, why) from the folders between the film root and the file, then the file name; else the folder path.

    attempts_bucket: the project has real versions (governed or candidates), so work/<node>/aN attempts that are not
    a version are pooled per node ("N13 attempts") instead of becoming one version each."""
    folder = os.path.dirname(path)
    rel = [] if folder == root else os.path.relpath(folder, root).split(os.sep)
    rel = [seg for seg in rel if seg not in ("", ".")]
    labels, whys = [], []

    def add(label, why):
        if label not in labels:
            labels.append(label)
            whys.append(why)

    i = 0
    while i < len(rel):
        seg = rel[i]
        low = seg.lower()
        if _ROUND_RV.match(low) or _ROUND.match(low) or _VERSION_SEG.match(low):
            add(seg, f"folder {seg}")
        elif _CAND.match(seg) or _VPG_V.match(seg):
            add(seg, f"variant folder {seg}")
        elif _FILM_V.match(seg):
            add(_FILM_V.match(seg).group(1), f"folder {seg}")
        elif low in ("outputs", "iterations", "releases") and i + 1 < len(rel):
            add(rel[i + 1], f"{low}/{rel[i + 1]}")
            i += 1
        elif low == "work" and i + 2 < len(rel) and _ATTEMPT.match(rel[i + 2]) and rel[i + 1].startswith("n"):
            node, attempt = rel[i + 1], rel[i + 2]
            short = node.split("_")[0]
            if attempts_bucket:
                add(f"{short.upper()} attempts", f"work/{node}/{attempt} is an attempt, not a governed version")
            elif node == "n14_video_assembly":
                add(attempt, f"attempt {attempt} under work/{node}")
            else:
                add(f"{short} {attempt}", f"attempt {attempt} under work/{node}")
            i += 2
        elif _DATE.match(seg):
            add(seg, f"date folder {seg}")
        i += 1
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if not any(label.endswith(" attempts") for label in labels):
        for token in _NAME_TOKEN.findall(stem):
            if not any(token in label.lower() for label in labels):
                add(token, f"'{token}' in the file name")
                break
    rounds = {int(m.group(1)) for label in labels for m in [_ROUND_RV.match(label.lower())] if m}
    kept = [(label, why) for label, why in zip(labels, whys)
            if not (_ROUND.match(label.lower()) and int(_ROUND.match(label.lower()).group(1)) in rounds)]
    if not kept:
        if not rel:
            return "(film folder)", "directly in the film folder"
        return "/".join(rel), "no version pattern, so the folder under the film root"
    return " / ".join(label for label, _ in kept), "; ".join(why for _, why in kept)


# ------------------------------------------------------------------------------------------------ grouping
def _override(path, overrides):
    for index, rule in enumerate(overrides):
        try:
            if re.search(rule["match"], path):
                return index, rule
        except (re.error, KeyError, TypeError):
            continue
    return None, None


def _primary_key(item):
    return (ROLE_RANK.get(item["role"], 9), 0 if item.get("governed") else 1, item.get("depth", 0),
            1 if "caption" in item["name"].lower() else 0, -(item.get("mtime") or 0))


def _version_label(path, film, ctx):
    governed = ctx.governed.get(path)
    if film.get("variant"):
        prefix = os.path.relpath(film["variant"], film["root"])
        detail = governed[0] if governed else version_of(path, film["variant"])[0]
        return prefix, f"variant project {prefix}; this file: {detail}", False
    if governed and film["kind"] == "vpg":
        return governed[0], governed[1], True
    base = film["root"] if not film["root"].startswith("map:") else ctx.root_of(path)
    label, why = version_of(path, base, attempts_bucket=film["kind"] == "vpg" and film["root"] in ctx.versioned)
    return label, why, False


def sort_versions(versions):
    """Families of labels ('v#', 'r#-v#'): the family with the best primary role and the newest file first, pooled
    work attempts ('N13 attempts') last; inside a family, natural order newest first, then by time."""
    families = {}
    for version in versions:
        fam = families.setdefault(family_of(version["label"]), {"rank": 99, "mtime": 0})
        fam["rank"] = min(fam["rank"], ROLE_RANK.get(version["primary"]["role"], 9))
        fam["mtime"] = max(fam["mtime"], version["mtime"])
    versions.sort(key=lambda v: (natural_key(v["label"]), v["mtime"]), reverse=True)
    versions.sort(key=lambda v: (1 if v["label"].endswith(" attempts") else 0,
                                 families[family_of(v["label"])]["rank"], -families[family_of(v["label"])]["mtime"],
                                 family_of(v["label"])))
    return versions


def group(files, ctx):
    """files: [{path, size, mtime, ...}] -> {items, films, collections, counts}."""
    items = {}
    for meta in files:
        path = meta["path"]
        item = dict(meta)
        item["id"] = short_id(path)
        item["name"] = os.path.basename(path)
        item["folder"] = os.path.dirname(path)
        item["area"] = ctx.area(path)
        index, rule = _override(path, ctx.overrides)
        role, role_why = classify_role(path, ctx.below_root(path))
        if rule and rule.get("role") in ROLES:
            role, role_why = rule["role"], f"owner map rule {index + 1}"
        item["role"], item["role_why"] = role, role_why
        if rule and isinstance(rule.get("film"), str) and rule["film"].strip():
            film = {"root": "map:" + rule["film"].strip(), "name": rule["film"].strip(), "kind": "map",
                    "archived": ctx.archived(path), "batch": None, "job_pid": None, "why": f"owner map rule {index + 1}"}
        else:
            film = film_root(path, ctx)
        item["film_root"], item["film_why"] = film["root"], film["why"]
        if rule and isinstance(rule.get("version_regex"), str):
            try:
                match = re.search(rule["version_regex"], path)
            except re.error:
                match = None
            if match:
                label = match.group(1) if match.groups() else match.group(0)
                item["version"], item["version_why"] = label, f"owner map rule {index + 1}"
        if "version" not in item:
            item["version"], item["version_why"], item["governed"] = _version_label(path, film, ctx)
        item["depth"] = 0 if film["root"].startswith("map:") else len(os.path.relpath(item["folder"], film["root"]).split(os.sep))
        item["archived"] = film["archived"]
        items[item["id"]] = (item, film)

    films, by_root = {}, {}
    for item, film in items.values():
        if item["role"] in OTHER_ONLY_ROLES:
            continue
        entry = by_root.setdefault(film["root"], {"film": film, "versions": {}})
        entry["versions"].setdefault(item["version"], []).append(item)
    other_items = [item for item, _ in items.values() if item["role"] in OTHER_ONLY_ROLES]
    for root, entry in by_root.items():
        versions = []
        for label, members in entry["versions"].items():
            if not any(m["role"] in VERSION_ROLES for m in members):
                other_items.extend(members)
                continue
            members.sort(key=_primary_key)
            primary = members[0]
            versions.append({"label": label, "items": members, "primary": primary,
                             "mtime": max(m.get("mtime") or 0 for m in members),
                             "why": f"{primary['role']} beats the other {len(members) - 1} file{'s' if len(members) != 2 else ''} in this version"
                             if len(members) > 1 else f"the only video in this version ({primary['role']})"})
        if not any(v["primary"]["role"] in FILM_ROLES for v in versions):
            for version in versions:
                other_items.extend(version["items"])
            continue
        sort_versions(versions)
        film = entry["film"]
        film_id = short_id("film:" + root)
        members = [m for v in versions for m in v["items"]]
        for version in versions:
            for member in version["items"]:
                member.update(film_id=film_id, film=film["name"], is_primary=member is version["primary"])
        poster = next((v["primary"]["id"] for v in versions if v["primary"].get("poster")), None)
        films[film_id] = {
            "id": film_id, "name": film["name"], "root": root if not root.startswith("map:") else None, "kind": film["kind"],
            "area": members[0]["area"], "archived": film["archived"], "batch": film.get("batch"), "job_pid": film.get("job_pid"),
            "why": film["why"], "versions": versions, "videos": len(members),
            "total_size": sum(m.get("size") or 0 for m in members), "latest_mtime": max(v["mtime"] for v in versions),
            "primary_poster": poster,
        }

    collections = {}
    for item in other_items:
        item.update(film_id=None, film=None, is_primary=False)
        portfolio = item["role"] == "published" and portfolio_path(item["path"])
        key = "published:portfolio" if portfolio else "folder:" + item["folder"]
        coll = collections.get(key)
        if coll is None:
            label = ("Published on the portfolio site" if portfolio
                     else os.path.relpath(item["folder"], ctx.home) if item["folder"].startswith(ctx.home) else item["folder"])
            coll = collections[key] = {"id": short_id("collection:" + key), "label": label,
                                       "folder": None if portfolio else item["folder"], "area": item["area"], "items": []}
        coll["items"].append(item)
        item["collection_id"] = coll["id"]
    for coll in collections.values():
        coll["items"].sort(key=lambda m: natural_key(m["name"]))
        roles = sorted({m["role"] for m in coll["items"]}, key=lambda r: ROLES.index(r) if r in ROLES else 99)
        coll["roles"] = roles
        coll["why"] = _collection_why(coll)
        coll["videos"] = len(coll["items"])
        coll["total_size"] = sum(m.get("size") or 0 for m in coll["items"])
        coll["latest_mtime"] = max((m.get("mtime") or 0) for m in coll["items"])

    _mark_duplicates([item for item, _ in items.values()], films, collections)
    flat = {item["id"]: item for item, _ in items.values()}
    return {"items": flat, "films": films, "collections": {c["id"]: c for c in collections.values()},
            "counts": _counts(flat, films, collections)}


def _collection_why(coll):
    roles = coll["roles"]
    if coll["folder"] is None:
        return "copies published on the portfolio site; duplicates point to the film they came from"
    if roles == ["clip"]:
        return "clips only, no master, review, picture or rough cut in this folder"
    names = {"source": "source captures", "recording": "recordings", "eval": "evaluation clips",
             "published": "site build output", "other": "videos without a film role", "clip": "clips",
             "silent": "silent renders only"}
    return ", ".join(names.get(r, r) for r in roles)


def _place(item, films, collections):
    if item.get("film_id"):
        film = films[item["film_id"]]
        return f"{film['name']} / {item['version']}"
    coll = next((c for c in collections.values() if c["id"] == item.get("collection_id")), None)
    return coll["label"] if coll else item["folder"]


def _mark_duplicates(items, films, collections):
    groups = {}
    for item in items:
        item["duplicate_of"] = None
        item["copies"] = 0
        if item.get("fingerprint"):
            groups.setdefault(item["fingerprint"], []).append(item)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (0 if m.get("film_id") else 1, 1 if m.get("archived") else 0,
                                    0 if m.get("is_primary") else 1, 1 if m["role"] == "published" else 0,
                                    ROLE_RANK.get(m["role"], 9), m.get("mtime") or 0, m["path"]))
        canonical = members[0]
        canonical["copies"] = len(members) - 1
        place = _place(canonical, films, collections)
        for member in members[1:]:
            member["duplicate_of"] = {"id": canonical["id"], "place": place, "name": canonical["name"]}


def _counts(items, films, collections):
    by_area, by_role = {}, {}
    for item in items.values():
        by_area[item["area"]] = by_area.get(item["area"], 0) + 1
        by_role[item["role"]] = by_role.get(item["role"], 0) + 1
    return {
        "videos": len(items),
        "films": len(films),
        "archived_films": sum(1 for f in films.values() if f["archived"]),
        "versions": sum(len(f["versions"]) for f in films.values()),
        "film_videos": sum(f["videos"] for f in films.values()),
        "other_collections": len(collections),
        "other_videos": sum(len(c["items"]) for c in collections.values()),
        "duplicates": sum(1 for i in items.values() if i.get("duplicate_of")),
        "by_area": dict(sorted(by_area.items(), key=lambda kv: -kv[1])),
        "by_role": dict(sorted(by_role.items(), key=lambda kv: -kv[1])),
    }
