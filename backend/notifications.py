# -*- coding: utf-8 -*-
"""Persistent 'What's New' (Novedades) notification center for NaSuite Launcher."""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Set

MAX_NOTIFICATIONS = 60


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _clean_summary(text: str, max_len: int = 220) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


class NotificationManager:
    """Generates, deduplicates, and persists What's New notifications in notifications.json."""

    def __init__(self, app_root: str) -> None:
        self.app_root = os.path.abspath(app_root)
        self.path = os.path.join(self.app_root, "notifications.json")
        self._lock = threading.RLock()
        self._notifications: Dict[str, Dict[str, Any]] = {}
        self._read_ids: Set[str] = set()
        self._seen_repos: Set[str] = set()
        self._is_fresh_store: bool = not os.path.isfile(self.path)
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            for item in data.get("notifications") or []:
                if isinstance(item, dict) and item.get("id"):
                    self._notifications[str(item["id"])] = item
            for rid in data.get("read_ids") or []:
                if rid:
                    self._read_ids.add(str(rid))
            for repo in data.get("seen_repos") or []:
                if repo:
                    self._seen_repos.add(str(repo).lower())
        except Exception:
            pass

    def _save(self) -> None:
        sorted_items = self._sorted_items_locked()[:MAX_NOTIFICATIONS]
        kept_ids = {item["id"] for item in sorted_items}
        self._notifications = {item["id"]: item for item in sorted_items}
        # Prune read_ids only if huge, keeping recent history so old items don't resurface unread
        if len(self._read_ids) > 500:
            self._read_ids = self._read_ids.intersection(kept_ids)

        payload = {
            "seen_repos": sorted(self._seen_repos),
            "read_ids": sorted(self._read_ids),
            "notifications": sorted_items,
        }
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp_path = f"{self.path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
            self._is_fresh_store = False
        except Exception:
            pass

    def _sorted_items_locked(self) -> List[Dict[str, Any]]:
        items = list(self._notifications.values())
        items.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
        return items

    def _add_if_new_locked(self, entry: Dict[str, Any], newly_created: List[Dict[str, Any]]) -> None:
        nid = str(entry.get("id") or "").strip()
        if not nid or nid in self._notifications:
            return
        self._notifications[nid] = entry
        if nid not in self._read_ids:
            item_copy = copy.deepcopy(entry)
            item_copy["read"] = False
            newly_created.append(item_copy)

    @property
    def is_fresh_store(self) -> bool:
        with self._lock:
            return self._is_fresh_store

    def get_unread_count(self) -> int:
        with self._lock:
            return sum(
                1 for nid in self._notifications
                if nid not in self._read_ids
            )

    def get_payload(self) -> Dict[str, Any]:
        with self._lock:
            items = self._sorted_items_locked()[:MAX_NOTIFICATIONS]
            enriched: List[Dict[str, Any]] = []
            unread_count = 0
            for item in items:
                is_read = item["id"] in self._read_ids
                if not is_read:
                    unread_count += 1
                copy_item = copy.deepcopy(item)
                copy_item["read"] = is_read
                copy_item.setdefault("kind", copy_item.get("type") or "commit")
                copy_item.setdefault("body", copy_item.get("summary") or "")
                copy_item.setdefault("repo_url", copy_item.get("url") or "")
                enriched.append(copy_item)
            return {
                "ok": True,
                "unread_count": unread_count,
                "notifications": enriched,
            }

    def mark_read(self, notif_id: Optional[str] = None, mark_all: bool = False) -> Dict[str, Any]:
        with self._lock:
            if mark_all:
                self._read_ids.update(self._notifications.keys())
            elif notif_id:
                self._read_ids.add(str(notif_id).strip())
            self._save()
            return self.get_payload()

    def record_auto_update(self, app: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Record a single automatic git pull update notification."""
        with self._lock:
            newly_created: List[Dict[str, Any]] = []
            self._ingest_auto_update_locked(app, newly_created)
            if newly_created:
                self._save()
                return newly_created[0]
            return None

    def _ingest_auto_update_locked(
        self,
        app: Dict[str, Any],
        newly_created: List[Dict[str, Any]],
    ) -> None:
        app_id = str(app.get("id") or "").lower()
        app_name = str(app.get("name") or app_id or "App")
        if not app_id:
            return
        target_ref = str(
            app.get("remote_commit")
            or app.get("remote_tag")
            or app.get("remote_version")
            or _now_iso()[:16]
        )
        nid = f"auto_update:{app_id}:{target_ref}"
        summary_detail = (
            f"Se actualizo automaticamente a {target_ref} mediante git pull."
            if target_ref
            else f"Se actualizo {app_name} automaticamente mediante git pull."
        )
        self._add_if_new_locked(
            {
                "id": nid,
                "type": "auto_update",
                "app_id": app_id,
                "app_name": app_name,
                "title": f"{app_name} actualizado automaticamente",
                "summary": summary_detail,
                "url": str(app.get("repo_url") or ""),
                "date": _now_iso(),
            },
            newly_created,
        )

    def ingest_sync(
        self,
        apps: List[Dict[str, Any]],
        prev_app_ids: Optional[Iterable[str]] = None,
        commits_by_app: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        auto_updated_apps: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Ingest sync results and return newly created unread notifications."""
        prev_set = {str(x).lower() for x in (prev_app_ids or []) if x}
        commits_map = commits_by_app or {}
        auto_list = auto_updated_apps or []

        with self._lock:
            had_seen_before = bool(self._seen_repos) or bool(prev_set)
            newly_created: List[Dict[str, Any]] = []

            for app in apps or []:
                app_id = str(app.get("id") or "").lower()
                app_name = str(app.get("name") or app_id or "App")
                full_name = str(app.get("full_name") or "").strip()
                repo_url = str(app.get("repo_url") or "")
                updated_at = str(app.get("updated_at") or _now_iso())

                if not app_id:
                    continue

                # 1. Newly discovered GitHub repository
                if full_name and not full_name.lower().startswith("local/"):
                    full_low = full_name.lower()
                    if full_low not in self._seen_repos:
                        if had_seen_before and app_id not in prev_set:
                            self._add_if_new_locked(
                                {
                                    "id": f"new_repo:{full_low}",
                                    "type": "new_repo",
                                    "app_id": app_id,
                                    "app_name": app_name,
                                    "title": f"Nuevo repositorio detectado: {app_name}",
                                    "summary": _clean_summary(
                                        str(app.get("description") or f"Se descubrio {full_name} en GitHub.")
                                    ),
                                    "url": repo_url,
                                    "date": updated_at,
                                },
                                newly_created,
                            )
                        self._seen_repos.add(full_low)

                # 2. GitHub Release
                remote_tag = str(app.get("remote_tag") or "").strip()
                remote_ver = str(app.get("remote_version") or "").strip()
                tag_key = remote_tag or (f"v{remote_ver}" if remote_ver else "")
                if tag_key:
                    rel_notes = _clean_summary(str(app.get("release_notes") or ""))
                    if not rel_notes:
                        rel_notes = f"Lanzamiento {tag_key} disponible para {app_name}."
                    self._add_if_new_locked(
                        {
                            "id": f"release:{app_id}:{tag_key}",
                            "type": "release",
                            "app_id": app_id,
                            "app_name": app_name,
                            "title": f"Nueva version {tag_key} de {app_name}",
                            "summary": rel_notes,
                            "url": str(app.get("release_url") or repo_url),
                            "date": str(app.get("release_published_at") or updated_at),
                        },
                        newly_created,
                    )

                # 3. Recent commits (from commits_by_app or app['recent_commits'])
                commits = commits_map.get(app_id)
                if commits is None:
                    commits = app.get("recent_commits") or []
                for cm in (commits or [])[:5]:
                    if not isinstance(cm, dict):
                        continue
                    sha = str(cm.get("sha") or cm.get("full_sha") or "").strip()
                    if not sha:
                        continue
                    short_sha = sha[:7]
                    msg = str(cm.get("message") or "").strip()
                    first_line = msg.splitlines()[0].strip() if msg else f"Commit {short_sha}"
                    author = str(cm.get("author") or "NachoFari").strip()
                    cm_date = str(cm.get("date") or updated_at)
                    cm_url = str(cm.get("url") or "")
                    if not cm_url and repo_url:
                        cm_url = f"{repo_url.rstrip('/')}/commit/{cm.get('full_sha') or short_sha}"
                    self._add_if_new_locked(
                        {
                            "id": f"commit:{app_id}:{short_sha}",
                            "type": "commit",
                            "app_id": app_id,
                            "app_name": app_name,
                            "title": f"{app_name}: {_clean_summary(first_line, 88)}",
                            "summary": f"Commit {short_sha} por {author}",
                            "url": cm_url,
                            "date": cm_date,
                        },
                        newly_created,
                    )

            # 4. Auto-updated apps
            for updated_app in auto_list:
                if isinstance(updated_app, dict):
                    self._ingest_auto_update_locked(updated_app, newly_created)

            if newly_created or self._is_fresh_store:
                self._save()

            newly_created.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
            return newly_created
