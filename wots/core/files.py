"""FileStore: each office's files live under data/orgs/{org_id}/ (spec v2 §9).

A local folder today; the same interface moves to S3 / Supabase Storage in Phase 7.
"""
from __future__ import annotations

from pathlib import Path


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def org_dir(self, org_id: str) -> Path:
        return self.root / "orgs" / org_id

    def item_dir(self, org_id: str, item_id: str, create: bool = True) -> Path:
        path = self.org_dir(org_id) / "items" / item_id
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self, org_id: str, item_id: str, relative: str) -> Path:
        """A file inside one item's folder. Anything that escapes the folder is refused."""
        base = self.item_dir(org_id, item_id, create=False).resolve()
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            raise PermissionError("That path is outside this work item's folder")
        return target

    def outbox(self, org_id: str) -> Path:
        path = self.root / "outbox" / org_id
        path.mkdir(parents=True, exist_ok=True)
        return path
