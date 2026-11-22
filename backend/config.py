# -*- coding: utf-8 -*-
"""Configuration and offline repository cache persistence for NaSuite Launcher."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
from typing import Any, Dict, List


def get_app_root() -> str:
    """Return writable directory for config.json and cache_repos.json."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_root() -> str:
    """Return directory containing bundled templates/ and static/ assets."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_writable_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".nasuite_write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _path_usable_on_this_machine(path: str) -> bool:
    if not path:
        return False
    if os.path.isdir(path):
        return True
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    if drive and not os.path.exists(drive + os.sep):
        return False
    parent = os.path.dirname(os.path.abspath(path))
    return os.path.isdir(parent)


GENERIC_PARENT_NAMES = {
    "desktop",
    "escritorio",
    "downloads",
    "descargas",
    "documents",
    "documentos",
    "programs",
    "program files",
    "program files (x86)",
}


def detect_default_suite_dir(app_root: str) -> str:
    """Auto-detect the best default NaSuite apps directory on ANY computer."""
    abs_root = os.path.abspath(app_root)
    parent = os.path.dirname(abs_root)
    # If running frozen inside <Launcher>/dist/NaSuite, walk up past dist/Launcher
    if os.path.basename(parent).lower() == "dist":
        launcher_dir = os.path.dirname(parent)
        candidate = os.path.dirname(launcher_dir)
        if os.path.isdir(candidate):
            return candidate

    parent_name = os.path.basename(parent).lower()
    # If parent is a dedicated suite folder (e.g. 'NaSuite') and not a generic user folder like Desktop
    if parent and os.path.isdir(parent) and parent_name not in GENERIC_PARENT_NAMES:
        if "nasuite" in parent_name or os.path.isdir(os.path.join(parent, "NaTex")) or os.path.isdir(os.path.join(parent, "NaSpire")):
            if _is_writable_dir(parent):
                return parent

    # If running portably from Desktop, USB, or arbitrary folder, use <app_root>/Apps if writable
    portable_apps_dir = os.path.join(abs_root, "Apps")
    if _is_writable_dir(portable_apps_dir):
        return portable_apps_dir

    # Fallback to user profile %LOCALAPPDATA%\NaSuite\Apps (always writable on any Windows PC)
    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback_dir = os.path.join(local_appdata, "NaSuite", "Apps")
    os.makedirs(fallback_dir, exist_ok=True)
    return fallback_dir


def normalize_repo_slug(raw: str) -> str:
    """Normalize a GitHub URL or owner/repo string into 'owner/repo'."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"^https?://(www\.)?github\.com/", "", text, flags=re.IGNORECASE)
    text = text.strip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


DEFAULT_CONFIG: Dict[str, Any] = {
    "github_user": "NachoFari",
    "github_token": "",
    "repo_filter_prefix": "Na",
    "repo_filter_topic": "nasuite",
    "workspace_dir": "",
    "install_dir": "",
    "auto_sync_minutes": 15,
    "theme": "fox",
    "custom_repos": [],
    "hidden_repos": ["NaSuite", "NaSuite_Launcher", "NachoFari", "NachoFari.github.io"],
    "start_with_windows": True,
    "minimize_to_tray_on_launch": True,
    "minimize_to_tray_on_close": True,
    "auto_update_apps": True,
}


class ConfigManager:
    """Thread-safe manager for config.json and cache_repos.json."""

    def __init__(self, app_root: str | None = None) -> None:
        self.app_root = os.path.abspath(app_root or get_app_root())
        self.config_path = os.path.join(self.app_root, "config.json")
        self.cache_path = os.path.join(self.app_root, "cache_repos.json")
        self._lock = threading.RLock()
        self._defaults = self._build_defaults()
        self._config: Dict[str, Any] = self._load_from_disk()

    def _build_defaults(self) -> Dict[str, Any]:
        default_dir = detect_default_suite_dir(self.app_root)
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        defaults["workspace_dir"] = default_dir
        defaults["install_dir"] = default_dir
        return defaults

    def _load_from_disk(self) -> Dict[str, Any]:
        cfg = copy.deepcopy(self._defaults)
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    for key, value in loaded.items():
                        if key in cfg:
                            cfg[key] = value
            except Exception:
                pass
        else:
            self._write_json(self.config_path, cfg)
        return self._sanitize(cfg)

    def _sanitize(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        clean = copy.deepcopy(self._defaults)
        clean.update(cfg)

        clean["github_user"] = str(clean.get("github_user") or "NachoFari").strip()
        clean["github_token"] = str(clean.get("github_token") or "").strip()
        clean["repo_filter_prefix"] = str(clean.get("repo_filter_prefix") or "Na").strip()
        clean["repo_filter_topic"] = str(clean.get("repo_filter_topic") or "nasuite").strip()
        ws_dir = str(clean.get("workspace_dir") or "").strip()
        if not _path_usable_on_this_machine(ws_dir):
            ws_dir = self._defaults["workspace_dir"]
        clean["workspace_dir"] = ws_dir

        inst_dir = str(clean.get("install_dir") or "").strip()
        if not _path_usable_on_this_machine(inst_dir):
            inst_dir = self._defaults["install_dir"]
        clean["install_dir"] = inst_dir
        clean["theme"] = str(clean.get("theme") or "fox").strip()

        try:
            minutes = int(clean.get("auto_sync_minutes", 15))
            clean["auto_sync_minutes"] = max(0, min(1440, minutes))
        except (TypeError, ValueError):
            clean["auto_sync_minutes"] = 15

        custom = clean.get("custom_repos")
        if isinstance(custom, list):
            seen = set()
            norm_custom: List[str] = []
            for item in custom:
                slug = normalize_repo_slug(str(item))
                if slug and slug.lower() not in seen:
                    seen.add(slug.lower())
                    norm_custom.append(slug)
            clean["custom_repos"] = norm_custom
        else:
            clean["custom_repos"] = []

        hidden = clean.get("hidden_repos")
        if isinstance(hidden, list):
            clean["hidden_repos"] = [str(x).strip() for x in hidden if str(x).strip()]
        else:
            clean["hidden_repos"] = list(self._defaults["hidden_repos"])

        clean["start_with_windows"] = bool(clean.get("start_with_windows", True))
        clean["minimize_to_tray_on_launch"] = bool(clean.get("minimize_to_tray_on_launch", True))
        clean["minimize_to_tray_on_close"] = bool(clean.get("minimize_to_tray_on_close", True))
        clean["auto_update_apps"] = bool(clean.get("auto_update_apps", True))

        return clean

    @staticmethod
    def _write_json(path: str, payload: Any) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            merged = copy.deepcopy(self._config)
            if isinstance(patch, dict):
                for key, value in patch.items():
                    if key in self._defaults:
                        merged[key] = value
            self._config = self._sanitize(merged)
            self._write_json(self.config_path, self._config)
            return copy.deepcopy(self._config)

    def add_custom_repo(self, raw_repo: str) -> tuple[bool, str, Dict[str, Any]]:
        slug = normalize_repo_slug(raw_repo)
        if not slug:
            return False, "Formato invalido. Usa 'usuario/repositorio' o una URL de GitHub.", self.get()
        with self._lock:
            custom = list(self._config.get("custom_repos", []))
            if not any(x.lower() == slug.lower() for x in custom):
                custom.append(slug)
            repo_short = slug.split("/")[-1].lower()
            hidden = [
                h for h in self._config.get("hidden_repos", [])
                if h.lower() != repo_short and h.lower() != slug.lower()
            ]
            self._config["custom_repos"] = custom
            self._config["hidden_repos"] = hidden
            self._write_json(self.config_path, self._config)
            return True, slug, copy.deepcopy(self._config)

    def remove_custom_repo(self, raw_repo: str, hide_if_discovered: bool = False) -> tuple[bool, str, Dict[str, Any]]:
        target = (raw_repo or "").strip()
        slug = normalize_repo_slug(target) or target
        if not slug:
            return False, "Repositorio no especificado.", self.get()
        with self._lock:
            slug_low = slug.lower()
            short_low = slug.split("/")[-1].lower()
            custom = [
                x for x in self._config.get("custom_repos", [])
                if x.lower() != slug_low and x.split("/")[-1].lower() != short_low
            ]
            self._config["custom_repos"] = custom
            if hide_if_discovered:
                hidden = list(self._config.get("hidden_repos", []))
                short_name = slug.split("/")[-1]
                if not any(h.lower() == short_low for h in hidden):
                    hidden.append(short_name)
                self._config["hidden_repos"] = hidden
            self._write_json(self.config_path, self._config)
            return True, slug, copy.deepcopy(self._config)

    def load_cache(self) -> Dict[str, Any]:
        with self._lock:
            target = self.cache_path
            if not os.path.isfile(target):
                bundled = os.path.join(get_resource_root(), "backend", "default_catalog.json")
                if os.path.isfile(bundled):
                    target = bundled
                else:
                    return {"synced_at": "", "repos": []}
            try:
                with open(target, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("repos"), list):
                    return {
                        "synced_at": str(data.get("synced_at") or ""),
                        "repos": data["repos"],
                    }
            except Exception:
                pass
            return {"synced_at": "", "repos": []}

    def save_cache(self, repos: List[Dict[str, Any]], synced_at: str) -> None:
        with self._lock:
            try:
                self._write_json(
                    self.cache_path,
                    {"synced_at": synced_at, "repos": repos},
                )
            except Exception:
                pass
