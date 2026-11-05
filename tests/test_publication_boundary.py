"""Public inventory checks use generic fixtures, never a customer-name denylist."""
import json
from pathlib import Path

import pytest

from vpg import publication
from vpg.util import PKG_ROOT


def configure(root, files):
    root.mkdir(exist_ok=True)
    (root / "publication.json").write_text(json.dumps({
        "schema": "vpg-starter-publication/v1",
        "files": ["publication.json", *files],
    }))


def test_current_publication_is_self_contained():
    assert publication.check() == []
    boundary = json.loads((PKG_ROOT / ".vpg-sync.json").read_text())
    assert boundary["schema"] == "vpg-public-starter/v1"


def test_inventory_refuses_unreviewed_content(tmp_path):
    configure(tmp_path, ["guide.md"])
    (tmp_path / "guide.md").write_text("Generic instructions.\n")
    assert publication.check(tmp_path) == []
    (tmp_path / "extra.md").write_text("Unreviewed.\n")
    assert publication.check(tmp_path) == ["extra.md: not in the reviewed public inventory"]


def test_private_terms_remain_external_and_values_are_withheld(tmp_path):
    root = tmp_path / "public"
    configure(root, ["guide.md"])
    (root / "guide.md").write_text("A synthetic restricted token.\n")
    terms = tmp_path / "terms.json"
    terms.write_text(json.dumps(["synthetic restricted token"]))
    errors = publication.check(root, terms)
    assert errors == ["guide.md: private term matched (value withheld)"]
    assert "synthetic restricted token" not in "\n".join(errors)
    with pytest.raises(publication.PublicationError, match="outside"):
        publication.check(root, root / "terms.json")


@pytest.mark.parametrize("name", ["../outside", "/absolute", "work/master.mp4", ".vpg/state.json",
                                ".impeccable/review.json", "knowledge/preferences/person/model.json"])
def test_runtime_and_escaping_paths_are_refused(tmp_path, name):
    configure(tmp_path, [name])
    assert any("unsafe" in error or "not publishable" in error for error in publication.check(tmp_path))


def test_symlink_cannot_export_another_file(tmp_path):
    root = tmp_path / "public"
    configure(root, ["guide.md"])
    outside = tmp_path / "outside.md"
    outside.write_text("Synthetic file.\n")
    (root / "guide.md").symlink_to(outside)
    assert publication.check(root) == ["guide.md: symlinks are not publishable"]


def test_missing_files_and_credential_patterns_are_reported(tmp_path):
    configure(tmp_path, ["missing.md", "guide.md"])
    token = "ghp_" + "Z" * 36
    (tmp_path / "guide.md").write_text(token)
    errors = publication.check(tmp_path)
    assert "missing.md: required public file is missing" in errors
    assert "guide.md: possible credential (value withheld)" in errors
    assert token not in "\n".join(errors)
