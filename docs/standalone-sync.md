# Maintainer synchronization

The authoring copy remains at `02_Workflows/video-production` in the private Wei workspace.
Existing films, the workspace SOP/prompt and Workflow OS integration continue using that path.
The public repository is a curated export, not a submodule or second autonomous source of truth.

## Workspace to community

Run from the workspace root, substituting the standalone checkout location:

```sh
git -C ../video-production-graph pull --ff-only
.venv/bin/python 02_Workflows/video-production/scripts/sync_standalone.py \
  ../video-production-graph --check
# Exit 0 = identical; 1 = changes previewed; 2 = refused/error.
.venv/bin/python 02_Workflows/video-production/scripts/sync_standalone.py \
  ../video-production-graph --apply
```

The helper uses a positive allowlist in `publication.json`, replaces workspace-only entry documentation,
rebuilds generated docs without private case-study data, scans outgoing content locally for known
credential patterns, private case-study identifiers and machine-specific home paths, and then checks
every destination conflict before copying. It never stages, commits, pushes or restarts a service.
The scan is defense in depth, not a guarantee that arbitrary future content is safe.

It records public file hashes in `.vpg-sync.json`. Later runs update or remove only managed files
whose destination bytes still match the previous snapshot. Symlinks, traversal paths and independent
destination edits are refused. An unmanaged file is never deleted or overwritten.
Review unknown/untracked destination files separately before publishing them.

```sh
cd ../video-production-graph
# Activate a Python environment with requirements.txt installed.
python -m vpg validate
python -m vpg docs --check
python -m pytest -q -p no:cacheprovider
python -m vpg dry-run
git diff --check
git diff --stat
# Stage reviewed explicit paths, including .vpg-sync.json; never bulk-copy the private workspace.
git add -- <reviewed-paths>
git diff --cached
git commit -m "feat: describe the reviewed update" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git push origin main
```

## Community to workspace

Pull first. If a contributor changed a managed file, the helper refuses to overwrite it. Review that
diff, apply the accepted change to the corresponding workspace source, and rerun preview. If both
copies now have identical bytes, the helper accepts the reconciliation. Overlay files map back to
`publishing/` (for example public `README.md` maps to `publishing/README.md`).
Do not delete the hash manifest or use a force override to erase a community contribution.

For a deliberate managed-file deletion, first remove it from the workspace publication selection
and let the helper preview the deletion while the destination still matches the old hash.

## Publication boundary and rollback

Only the allowlisted engine/project files and fictional fixtures are shared. Private history, production
projects, preference records and workspace credentials remain local. No original workspace history is pushed.
Original code is MIT licensed; the bundled font keeps its own license.

If export tests fail, do not commit or push: correct the workspace source and repeat. If private data or
a credential is discovered, stop publication, remove it from the selection, and rotate any exposed
credential. A revert alone does not erase exposed Git history.

If an export causes a runtime regression, revert that public change with a normal reviewed commit,
correct the workspace source, and re-export; do not force-push. Existing productions must not be
silently moved or reconfigured. Changes to engine fingerprints may require the operator's explicit
`vpg accept-config` before an active production resumes.
