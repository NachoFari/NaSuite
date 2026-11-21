# -*- coding: utf-8 -*-
"""Dynamic GitHub discovery, local workspace scanner, icon resolver, and update detector."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from backend import USER_AGENT
from backend.config import ConfigManager, normalize_repo_slug
from backend.runner import AppRunner, LogBuffer, NO_WINDOW_FLAG, PROTECTED_DIR_NAMES

IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "dist_installer",
    "iconos_pixelart_32x32",
    "nasuite_launcher",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_version(ver: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", ver or "")
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


def clean_version_tag(tag: str) -> str:
    text = (tag or "").strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        return text[1:]
    return text


def _is_version_newer(remote_ver: str, local_ver: str) -> bool:
    if not remote_ver:
        return False
    if not local_ver:
        return False
    return parse_version(remote_ver) > parse_version(local_ver)


def _github_get_json(url: str, token: str = "", timeout: int = 12) -> Tuple[int, Any]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        try:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as exc:
            if isinstance(exc, urllib.error.HTTPError):
                raise
            ctx = ssl._create_unverified_context()
            resp_ctx = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        with resp_ctx as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            parsed = json.loads(body) if body else {"message": str(exc)}
        except Exception:
            parsed = {"message": str(exc)}
        return exc.code, parsed


def _classify_release_assets(assets: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
    result: Dict[str, Optional[Dict[str, Any]]] = {
        "installer": None,
        "portable_zip": None,
        "portable_exe": None,
    }
    for asset in assets or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        size = int(asset.get("size") or 0)
        if not name or not url:
            continue
        low = name.lower()
        info = {"name": name, "url": url, "size": size}
        if low.endswith(".exe") and any(k in low for k in ("setup", "installer", "install")):
            if result["installer"] is None:
                result["installer"] = info
        elif low.endswith(".zip"):
            if result["portable_zip"] is None or "portable" in low or "win" in low:
                result["portable_zip"] = info
        elif low.endswith(".exe"):
            if result["portable_exe"] is None:
                result["portable_exe"] = info
    return result


def _fetch_text_url(url: str, timeout: int = 7) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        try:
            resp_ctx = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as exc:
            if isinstance(exc, urllib.error.HTTPError):
                raise
            ctx = ssl._create_unverified_context()
            resp_ctx = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        with resp_ctx as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_readme_preview_and_desc(
    full_name: str,
    default_branch: str,
    local_path: str = "",
    token: str = "",
) -> Tuple[str, str, str]:
    """Extract (preview_image_url, repo_logo_url, clean_description) from README.md + git tree fallback."""
    raw = ""
    if local_path and os.path.isdir(local_path):
        for fname in ("README.md", "readme.md", "README.MD"):
            cand = os.path.join(local_path, fname)
            if os.path.isfile(cand):
                try:
                    with open(cand, "r", encoding="utf-8", errors="replace") as fh:
                        raw = fh.read(32768)
                    break
                except Exception:
                    pass
    branch_q = urllib.parse.quote(default_branch or "main", safe="")
    if not raw and full_name and "/" in full_name and not full_name.startswith("local/"):
        raw = _fetch_text_url(f"https://raw.githubusercontent.com/{full_name}/{branch_q}/README.md", timeout=6)

    def _resolve_repo_asset(url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if u.lower().startswith(("http://", "https://", "/")):
            return u
        clean_rel = re.sub(r"^\./+", "", u).lstrip("/")
        return f"https://raw.githubusercontent.com/{full_name}/{branch_q}/{clean_rel}"

    img_candidates: List[str] = []
    if raw:
        for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", raw):
            img_candidates.append(m.group(1).strip())
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', raw, flags=re.IGNORECASE):
            img_candidates.append(m.group(1).strip())

    gifs: List[str] = []
    screenshots: List[str] = []
    logos: List[str] = []

    for url in img_candidates:
        low = url.lower()
        if any(
            skip in low
            for skip in (
                "shields.io",
                "actions/workflows",
                "badge.svg",
                "static/img/icons/",
                "assets/icons/mascots/",
                "komarev.com",
                "star-history",
            )
        ):
            continue
        base_path = low.split("?")[0]
        if base_path.endswith(".svg"):
            continue
        resolved = _resolve_repo_asset(url)
        fname = base_path.rsplit("/", 1)[-1]
        is_logo_like = any(k in fname for k in ("logo", "icon", "badge_256", "badge", "favicon", "emblem"))
        if base_path.endswith(".gif"):
            gifs.append(resolved)
        elif any(base_path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
            if is_logo_like:
                logos.append(resolved)
            else:
                screenshots.append(resolved)

    # If README didn't provide both a preview and a logo, inspect the repo's git tree on GitHub
    if (not gifs and not screenshots) or not logos:
        if full_name and "/" in full_name and not full_name.startswith("local/"):
            st, tree_data = _github_get_json(
                f"https://api.github.com/repos/{full_name}/git/trees/{branch_q}?recursive=1",
                token=token,
                timeout=7,
            )
            if st == 200 and isinstance(tree_data, dict):
                for node in tree_data.get("tree") or []:
                    if not isinstance(node, dict) or node.get("type") != "blob":
                        continue
                    rel_path = str(node.get("path") or "")
                    low_p = rel_path.lower()
                    if any(
                        skip in low_p
                        for skip in ("mascots/", "static/img/icons/", "node_modules/", "vendor/")
                    ):
                        continue
                    fname = low_p.rsplit("/", 1)[-1]
                    if low_p.endswith(".gif"):
                        gifs.append(_resolve_repo_asset(rel_path))
                    elif any(low_p.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                        if any(k in fname for k in ("logo", "badge_256", "favicon", "icon")):
                            logos.append(_resolve_repo_asset(rel_path))
                        elif any(k in low_p for k in ("showcase", "screenshot", "preview", "banner", "demo", "marcador")):
                            screenshots.append(_resolve_repo_asset(rel_path))

    preview_url = gifs[0] if gifs else (screenshots[0] if screenshots else (logos[0] if logos else ""))
    logo_url = logos[0] if logos else ""

    desc = ""
    if raw:
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith(("#", "!", "<", ">", "```", "---", "|", "-", "* [", "[![")):
                continue
            if "shields.io" in s or "http://" in s or "https://" in s:
                continue
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
            clean = re.sub(r"[*_`~]+", "", clean).strip()
            if len(clean) >= 25:
                desc = clean[:160].rstrip() + ("..." if len(clean) > 160 else "")
                break

    return preview_url, logo_url, desc


def resolve_icon_and_category(
    name: str,
    topics: List[str],
    description: str,
    resource_root: str,
) -> Tuple[str, str, str, str, bool]:
    """Return (icon_key, icon_url, icon_small_url, category, is_pixel_icon)."""
    low_name = (name or "").strip().lower()
    if low_name == "natex":
        natex_logo_path = os.path.join(resource_root, "static", "img", "icons", "natex_logo.png")
        if os.path.isfile(natex_logo_path):
            return "natex_logo", "/static/img/icons/natex_logo.png", "/static/img/icons/natex_logo.png", "Escritura", False
        icon_key, category = "tex", "Escritura"
    elif low_name == "naspire":
        icon_key, category = "calc", "Hardware & Calculo"
    else:
        blob = f"{low_name} {' '.join(t.lower() for t in (topics or []))} {(description or '').lower()}"
        if any(k in blob for k in ("vision", "ocr", "camera", "image", "saliency", "poster")):
            icon_key, category = "camera", "Vision & IA"
        elif any(k in blob for k in ("radar", "lab", "virtual", "physics", "sim")):
            icon_key, category = "graph", "Ciencia & Lab"
        elif any(k in blob for k in ("graph", "plot", "chart", "visual")):
            icon_key, category = "graph", "Visualizacion"
        elif any(k in blob for k in ("math", "calc", "algebra", "matrix", "numeric")):
            icon_key, category = "math", "Matematicas"
        elif any(k in blob for k in ("python", "pypi", "script", "py")):
            icon_key, category = "python", "Desarrollo"
        elif "lua" in blob:
            icon_key, category = "lua", "Desarrollo"
        elif any(k in blob for k in ("ai", "ml", "llm", "gemini", "openai", "neural")):
            icon_key, category = "ai", "Inteligencia Artificial"
        elif any(k in blob for k in ("term", "cli", "shell", "console")):
            icon_key, category = "terminal", "Herramientas"
        elif any(k in blob for k in ("note", "doc", "markdown", "latex", "tex", "write")):
            icon_key, category = "notes", "Escritura"
        else:
            icon_key, category = "catalog", "Proyectos"

    hd_path = os.path.join(resource_root, "static", "img", "icons", "hd", f"{icon_key}_hd_256x256.png")
    if os.path.isfile(hd_path):
        icon_url = f"/static/img/icons/hd/{icon_key}_hd_256x256.png"
    else:
        icon_url = f"/static/img/icons/{icon_key}.png"

    small_path = os.path.join(resource_root, "static", "img", "icons", f"{icon_key}.png")
    icon_small_url = f"/static/img/icons/{icon_key}.png" if os.path.isfile(small_path) else "/static/img/icons/catalog.png"
    return icon_key, icon_url, icon_small_url, category, True


def _run_git_short(repo_dir: str, args: List[str]) -> str:
    git_exe = shutil.which("git")
    if not git_exe:
        return ""
    try:
        proc = subprocess.run(
            [git_exe, "-C", repo_dir, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            creationflags=NO_WINDOW_FLAG,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def _read_local_version(app_dir: str) -> str:
    """Detect local app version from installed_meta.json, constants.py, updater.py, or version.txt."""
    meta_path = os.path.join(app_dir, "installed_meta.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            ver = clean_version_tag(str(meta.get("version") or meta.get("tag") or ""))
            if ver:
                return ver
        except Exception:
            pass

    py_candidates = [
        os.path.join(app_dir, "core", "constants.py"),
        os.path.join(app_dir, "backend", "updater.py"),
        os.path.join(app_dir, "backend", "__init__.py"),
        os.path.join(app_dir, "app.py"),
    ]
    ver_pattern = re.compile(r"""(?:APP_VERSION|__version__|VERSION)\s*=\s*['"]([^'"]+)['"]""")
    for path in py_candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read(8192)
                match = ver_pattern.search(text)
                if match:
                    return clean_version_tag(match.group(1))
            except Exception:
                pass

    ver_txt = os.path.join(app_dir, "version.txt")
    if os.path.isfile(ver_txt):
        try:
            with open(ver_txt, "r", encoding="utf-8", errors="replace") as fh:
                first = fh.readline().strip()
            if first:
                return clean_version_tag(first)
        except Exception:
            pass

    return ""


def _inspect_local_folder(root_dir: str, folder_name: str) -> Optional[Dict[str, Any]]:
    """Inspect a candidate local directory (handling nested NaTex/NaTex and dist/App layouts)."""
    if not os.path.isdir(root_dir):
        return None

    low_name = folder_name.lower()
    nested_same = os.path.join(root_dir, folder_name)
    nested_modular = os.path.join(root_dir, f"{folder_name}_Modular")

    # Determine canonical git/code root
    git_dir = ""
    if os.path.exists(os.path.join(root_dir, ".git")):
        git_dir = root_dir
    elif os.path.isdir(nested_same) and os.path.exists(os.path.join(nested_same, ".git")):
        git_dir = nested_same

    # Candidate executables in priority order (prefer compiled .exe over .py)
    exe_candidates = [
        os.path.join(root_dir, f"{folder_name}.exe"),
        os.path.join(root_dir, "dist", folder_name, f"{folder_name}.exe"),
        os.path.join(root_dir, "dist", f"{folder_name}.exe"),
        os.path.join(nested_same, f"{folder_name}.exe"),
        os.path.join(nested_same, "dist", folder_name, f"{folder_name}.exe"),
        os.path.join(nested_modular, f"{folder_name}.exe"),
    ]
    launch_target = ""
    launch_type = ""
    for cand in exe_candidates:
        if os.path.isfile(cand):
            launch_target = cand
            launch_type = "exe"
            break

    # Also check any single non-setup .exe directly inside root_dir or nested_same
    if not launch_target:
        for scan_dir in (root_dir, nested_same):
            if not os.path.isdir(scan_dir):
                continue
            try:
                for entry in os.listdir(scan_dir):
                    low = entry.lower()
                    if low.endswith(".exe") and not any(k in low for k in ("setup", "unins", "installer")):
                        full = os.path.join(scan_dir, entry)
                        if os.path.isfile(full):
                            launch_target = full
                            launch_type = "exe"
                            break
            except OSError:
                pass
            if launch_target:
                break

    # Fallback to Python entry points
    if not launch_target:
        py_candidates = [
            os.path.join(root_dir, "app.py"),
            os.path.join(root_dir, "main.py"),
            os.path.join(nested_same, "app.py"),
            os.path.join(nested_same, "main.py"),
        ]
        for cand in py_candidates:
            if os.path.isfile(cand):
                launch_target = cand
                launch_type = "python"
                break

    has_meta = os.path.isfile(os.path.join(root_dir, "installed_meta.json"))
    if not launch_target and not git_dir and not has_meta:
        return None

    # Determine canonical local_path
    if git_dir:
        local_path = git_dir
    elif os.path.isdir(nested_same) and launch_target.startswith(os.path.abspath(nested_same)):
        local_path = nested_same
    else:
        local_path = root_dir

    # Read local version across local_path and root_dir
    local_version = _read_local_version(local_path)
    if not local_version and local_path != root_dir:
        local_version = _read_local_version(root_dir)

    local_commit = ""
    remote_origin = ""
    local_recent_commits: List[Dict[str, str]] = []
    if git_dir:
        local_commit = _run_git_short(git_dir, ["rev-parse", "--short=7", "HEAD"])
        remote_origin = _run_git_short(git_dir, ["remote", "get-url", "origin"])
        if not local_version:
            git_tag = _run_git_short(git_dir, ["describe", "--tags", "--abbrev=0"])
            if git_tag:
                local_version = clean_version_tag(git_tag)
        log_raw = _run_git_short(git_dir, ["log", "-n", "5", "--pretty=format:%h|%s|%cI|%an"])
        if log_raw:
            for line in log_raw.splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4 and parts[0].strip():
                    local_recent_commits.append(
                        {
                            "sha": parts[0].strip()[:7],
                            "message": parts[1].strip(),
                            "date": parts[2].strip(),
                            "author": parts[3].strip(),
                        }
                    )

    repo_slug = normalize_repo_slug(remote_origin) if remote_origin else ""
    if not repo_slug and has_meta:
        try:
            with open(os.path.join(root_dir, "installed_meta.json"), "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            repo_slug = normalize_repo_slug(str(meta.get("repo") or meta.get("repo_url") or ""))
        except Exception:
            pass

    app_name = repo_slug.split("/")[-1] if repo_slug else folder_name
    return {
        "id": app_name.lower(),
        "name": app_name,
        "repo_slug": repo_slug,
        "local_path": os.path.abspath(local_path),
        "root_dir": os.path.abspath(root_dir),
        "launch_target": os.path.abspath(launch_target) if launch_target else "",
        "launch_type": launch_type,
        "local_version": local_version,
        "local_commit": local_commit,
        "recent_commits": local_recent_commits,
        "has_git": bool(git_dir),
        "has_meta": has_meta,
    }


class AppCatalog:
    """Combines GitHub discovery with local filesystem scanning and live runner status."""

    def __init__(
        self,
        config_mgr: ConfigManager,
        runner: AppRunner,
        logs: LogBuffer,
        resource_root: str,
    ) -> None:
        self.config_mgr = config_mgr
        self.runner = runner
        self.logs = logs
        self.resource_root = resource_root
        self._lock = threading.RLock()
        self._remote_repos: List[Dict[str, Any]] = []
        self._local_apps: Dict[str, Dict[str, Any]] = {}
        self._synced_at: str = ""
        self._offline: bool = False
        self._syncing: bool = False
        self._last_warning: str = ""

        # Load cached GitHub catalog immediately for instant startup
        cache = self.config_mgr.load_cache()
        self._remote_repos = cache.get("repos", [])
        self._synced_at = cache.get("synced_at", "")
        self.scan_local()

    def _enrich_repo(self, repo: Dict[str, Any], token: str, is_custom: bool = False) -> Dict[str, Any]:
        full_name = str(repo.get("full_name") or "")
        name = str(repo.get("name") or full_name.split("/")[-1])
        default_branch = str(repo.get("default_branch") or "main")

        remote_tag = ""
        remote_version = ""
        release_notes = ""
        release_url = ""
        release_published_at = ""
        release_assets: Dict[str, Optional[Dict[str, Any]]] = {
            "installer": None,
            "portable_zip": None,
            "portable_exe": None,
        }

        rel_status, rel_data = _github_get_json(
            f"https://api.github.com/repos/{full_name}/releases/latest",
            token=token,
            timeout=10,
        )
        if rel_status == 200 and isinstance(rel_data, dict):
            remote_tag = str(rel_data.get("tag_name") or "")
            remote_version = clean_version_tag(remote_tag)
            release_notes = str(rel_data.get("body") or "")
            release_url = str(rel_data.get("html_url") or "")
            release_published_at = str(rel_data.get("published_at") or "")
            release_assets = _classify_release_assets(rel_data.get("assets") or [])

        remote_commit = ""
        recent_commits: List[Dict[str, str]] = []
        branch_q = urllib.parse.quote(default_branch, safe="")
        cm_status, cm_data = _github_get_json(
            f"https://api.github.com/repos/{full_name}/commits?sha={branch_q}&per_page=5",
            token=token,
            timeout=10,
        )
        if cm_status == 200 and isinstance(cm_data, list):
            for idx, item in enumerate(cm_data[:5]):
                if not isinstance(item, dict):
                    continue
                full_sha = str(item.get("sha") or "")
                short_sha = full_sha[:7] if full_sha else ""
                if idx == 0 and short_sha:
                    remote_commit = short_sha
                commit_obj = item.get("commit") if isinstance(item.get("commit"), dict) else {}
                author_obj = commit_obj.get("author") if isinstance(commit_obj.get("author"), dict) else {}
                committer_obj = commit_obj.get("committer") if isinstance(commit_obj.get("committer"), dict) else {}
                raw_msg = str(commit_obj.get("message") or "").strip()
                first_line = raw_msg.splitlines()[0].strip() if raw_msg else ""
                author_name = str(author_obj.get("name") or committer_obj.get("name") or "")
                cm_date = str(committer_obj.get("date") or author_obj.get("date") or "")
                if short_sha:
                    recent_commits.append(
                        {
                            "sha": short_sha,
                            "full_sha": full_sha,
                            "message": first_line,
                            "date": cm_date,
                            "author": author_name,
                            "url": str(item.get("html_url") or ""),
                        }
                    )

        raw_desc = str(repo.get("description") or "").strip()
        preview_image, repo_logo, readme_desc = _extract_readme_preview_and_desc(
            full_name, default_branch, token=token
        )
        if not preview_image and full_name and "/" in full_name:
            preview_image = f"https://opengraph.githubassets.com/1/{full_name}"

        return {
            "id": name.lower(),
            "name": name,
            "full_name": full_name,
            "description": raw_desc or readme_desc,
            "preview_image": preview_image,
            "repo_logo": repo_logo,
            "is_suite_app": bool(repo.get("_is_suite_app", True)),
            "topics": [str(t) for t in (repo.get("topics") or [])],
            "language": str(repo.get("language") or ""),
            "stars": int(repo.get("stargazers_count") or 0),
            "repo_url": str(repo.get("html_url") or f"https://github.com/{full_name}"),
            "clone_url": str(repo.get("clone_url") or f"https://github.com/{full_name}.git"),
            "default_branch": default_branch,
            "updated_at": str(repo.get("updated_at") or ""),
            "is_custom": is_custom,
            "remote_tag": remote_tag,
            "remote_version": remote_version,
            "release_notes": release_notes,
            "release_url": release_url,
            "release_published_at": release_published_at,
            "release_assets": release_assets,
            "remote_commit": remote_commit,
            "recent_commits": recent_commits,
        }

    def sync_github(self) -> Dict[str, Any]:
        """Fetch matching repositories, latest releases, and latest commits from GitHub."""
        with self._lock:
            if self._syncing:
                return {
                    "ok": True,
                    "syncing": True,
                    "synced_at": self._synced_at,
                    "warning": "Sincronizacion ya en curso.",
                }
            self._syncing = True

        cfg = self.config_mgr.get()
        github_user = cfg.get("github_user", "NachoFari")
        token = cfg.get("github_token", "")
        prefix = (cfg.get("repo_filter_prefix") or "Na").lower()
        topic = (cfg.get("repo_filter_topic") or "nasuite").lower()
        hidden = {h.lower() for h in (cfg.get("hidden_repos") or [])}
        custom_repos = list(cfg.get("custom_repos") or [])

        warning = ""
        rate_limited = False
        offline = False
        matched_raw: List[Tuple[Dict[str, Any], bool]] = []
        seen_full: set[str] = set()

        try:
            user_url = f"https://api.github.com/users/{urllib.parse.quote(github_user)}/repos?per_page=100&sort=updated"
            status, data = _github_get_json(user_url, token=token, timeout=12)
            if status == 200 and isinstance(data, list):
                for repo in data:
                    if not isinstance(repo, dict):
                        continue
                    name = str(repo.get("name") or "")
                    full_name = str(repo.get("full_name") or "")
                    if not name or not full_name:
                        continue
                    if name.lower() in hidden or full_name.lower() in hidden:
                        continue
                    if repo.get("fork"):
                        continue
                    topics_low = {str(t).lower() for t in (repo.get("topics") or [])}
                    matches_topic = bool(topic and topic in topics_low)
                    matches_prefix = bool(prefix and name.lower().startswith(prefix))
                    repo["_is_suite_app"] = bool(matches_topic or matches_prefix)
                    seen_full.add(full_name.lower())
                    matched_raw.append((repo, False))
            elif status == 403:
                rate_limited = True
                warning = "Limite de peticiones de GitHub API alcanzado. Configura un GitHub Token en Ajustes."
                self.logs.add("warn", "GitHub", warning)
            else:
                msg = data.get("message") if isinstance(data, dict) else f"HTTP {status}"
                warning = f"GitHub devolvio HTTP {status}: {msg}"
                self.logs.add("warn", "GitHub", warning)

            # Fetch custom repos explicitly added by user
            for slug in custom_repos:
                if slug.lower() in seen_full:
                    continue
                c_status, c_data = _github_get_json(f"https://api.github.com/repos/{slug}", token=token, timeout=10)
                if c_status == 200 and isinstance(c_data, dict):
                    seen_full.add(slug.lower())
                    matched_raw.append((c_data, True))
                elif c_status == 403:
                    rate_limited = True

            enriched: List[Dict[str, Any]] = []
            if matched_raw:
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    futures = [
                        pool.submit(self._enrich_repo, raw_repo, token, is_custom)
                        for raw_repo, is_custom in matched_raw
                    ]
                    for fut in concurrent.futures.as_completed(futures):
                        try:
                            enriched.append(fut.result())
                        except Exception as exc:
                            self.logs.add("warn", "GitHub", f"Error enriqueciendo repositorio: {exc}")

                enriched.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
                synced_at = _now_iso()
                with self._lock:
                    self._remote_repos = enriched
                    self._synced_at = synced_at
                    self._offline = False
                    self._last_warning = warning
                self.config_mgr.save_cache(enriched, synced_at)
                self.logs.add("success", "GitHub", f"Catalogo sincronizado: {len(enriched)} repositorios detectados.")
            elif warning:
                with self._lock:
                    self._last_warning = warning
        except Exception as exc:
            offline = True
            warning = f"Modo sin conexion (usando cache local): {exc}"
            with self._lock:
                self._offline = True
                self._last_warning = warning
            self.logs.add("warn", "GitHub", warning)
        finally:
            self.scan_local()
            with self._lock:
                self._syncing = False

        return {
            "ok": True,
            "synced_at": self._synced_at,
            "offline": offline,
            "rate_limited": rate_limited,
            "warning": warning,
        }

    def scan_local(self) -> Dict[str, Dict[str, Any]]:
        """Scan workspace_dir, install_dir, and standard Inno Setup installation directories."""
        cfg = self.config_mgr.get()
        hidden = {h.lower() for h in (cfg.get("hidden_repos") or [])}
        prefix = (cfg.get("repo_filter_prefix") or "Na").lower()

        scan_roots: List[str] = []
        for d in (cfg.get("workspace_dir"), cfg.get("install_dir")):
            if d and os.path.isdir(d):
                abs_d = os.path.abspath(d)
                if abs_d not in scan_roots:
                    scan_roots.append(abs_d)

        # Standard Windows Inno Setup / per-user locations
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        app_data = os.environ.get("APPDATA", "")
        extra_parent_dirs = [
            os.path.join(local_app_data, "Programs", "NaSuite") if local_app_data else "",
            os.path.join(local_app_data, "NaSuite") if local_app_data else "",
        ]
        for d in extra_parent_dirs:
            if d and os.path.isdir(d) and os.path.abspath(d) not in scan_roots:
                scan_roots.append(os.path.abspath(d))

        found: Dict[str, Dict[str, Any]] = {}

        for root in scan_roots:
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for entry in entries:
                low = entry.lower()
                if low in IGNORED_WORKSPACE_DIRS or low in hidden or low in PROTECTED_DIR_NAMES:
                    continue
                full = os.path.join(root, entry)
                if not os.path.isdir(full):
                    continue
                info = _inspect_local_folder(full, entry)
                if not info:
                    continue
                # Include if matches prefix, has git remote, or has installed_meta.json
                if not (
                    info["has_meta"]
                    or info["repo_slug"]
                    or (prefix and info["name"].lower().startswith(prefix))
                ):
                    continue
                key = info["id"]
                # Prefer workspace copy with git or exe over secondary copy
                prev = found.get(key)
                if prev is None or (info["launch_target"] and not prev["launch_target"]):
                    found[key] = info

        # Direct single-app Inno Setup paths
        direct_candidates = [
            ("NaTex", os.path.join(app_data, "NaTex Studio")) if app_data else ("", ""),
            ("NaTex", os.path.join(local_app_data, "Programs", "NaTex Studio")) if local_app_data else ("", ""),
            ("NaTex", os.path.join(local_app_data, "Programs", "NaTex")) if local_app_data else ("", ""),
            ("NaSpire", os.path.join(local_app_data, "Programs", "NaSpire Studio")) if local_app_data else ("", ""),
            ("NaSpire", os.path.join(local_app_data, "Programs", "NaSpire")) if local_app_data else ("", ""),
        ]
        for app_name, path in direct_candidates:
            if not app_name or not path or not os.path.isdir(path):
                continue
            key = app_name.lower()
            if key in found and found[key].get("launch_target"):
                continue
            info = _inspect_local_folder(path, app_name)
            if info:
                found[key] = info

        with self._lock:
            self._local_apps = found
        return found

    def get_apps(self) -> List[Dict[str, Any]]:
        """Merge remote GitHub catalog + local scan + live running state."""
        cfg = self.config_mgr.get()
        install_dir = os.path.abspath(cfg.get("install_dir") or "")
        hidden = {h.lower() for h in (cfg.get("hidden_repos") or [])}

        with self._lock:
            remotes = list(self._remote_repos)
            locals_map = dict(self._local_apps)

        merged: Dict[str, Dict[str, Any]] = {}

        # 1. Start from remote GitHub repos
        for remote in remotes:
            app_id = str(remote.get("id") or "").lower()
            if not app_id or app_id in hidden:
                continue
            local = locals_map.get(app_id)
            if local is None:
                # Try matching by full_name slug
                full_low = str(remote.get("full_name") or "").lower()
                for loc in locals_map.values():
                    if loc.get("repo_slug", "").lower() == full_low:
                        local = loc
                        break

            merged[app_id] = self._build_app_record(remote, local, install_dir)

        # 2. Add any local-only apps not yet on GitHub (or while offline)
        for app_id, local in locals_map.items():
            if app_id in hidden or app_id in merged:
                continue
            merged[app_id] = self._build_app_record(None, local, install_dir)

        # Sort: running first, then update_available, then installed, then not_installed; then by name
        order = {"running": 0, "update_available": 1, "installed": 2, "not_installed": 3}
        apps = list(merged.values())
        apps.sort(key=lambda a: (order.get(a["status"], 9), a["name"].lower()))
        return apps

    def get_app_by_id(self, app_id: str) -> Optional[Dict[str, Any]]:
        target = (app_id or "").strip().lower()
        for app in self.get_apps():
            if app["id"] == target:
                return app
        return None

    def _build_app_record(
        self,
        remote: Optional[Dict[str, Any]],
        local: Optional[Dict[str, Any]],
        install_dir: str,
    ) -> Dict[str, Any]:
        name = str((remote or {}).get("name") or (local or {}).get("name") or "App")
        app_id = name.lower()
        full_name = str((remote or {}).get("full_name") or (local or {}).get("repo_slug") or f"local/{name}")
        description = str((remote or {}).get("description") or "").strip()
        if not description:
            if app_id == "natex":
                description = "Editor LaTeX moderno del ecosistema NaSuite con vista previa en vivo e IA."
            elif app_id == "naspire":
                description = "Gestor portable para calculadoras TI-Nspire con editor Lua/Python y envio USB."
            else:
                description = f"Aplicacion del ecosistema NaSuite ({full_name})."

        topics = list((remote or {}).get("topics") or [])
        icon_key, icon_url, icon_small_url, category, is_pixel_icon = resolve_icon_and_category(
            name, topics, description, self.resource_root
        )
        repo_logo = str((remote or {}).get("repo_logo") or "")
        if repo_logo and app_id not in ("natex", "naspire"):
            icon_url = repo_logo
            icon_small_url = repo_logo
            is_pixel_icon = "fox" in repo_logo.lower()

        local_path = str((local or {}).get("local_path") or "")
        preview_image = str((remote or {}).get("preview_image") or "")
        if app_id == "natex" and os.path.isfile(os.path.join(self.resource_root, "static", "img", "previews", "natex_preview.gif")):
            preview_image = "/static/img/previews/natex_preview.gif"
        elif app_id == "naspire" and os.path.isfile(os.path.join(self.resource_root, "static", "img", "previews", "naspire_preview.png")):
            preview_image = "/static/img/previews/naspire_preview.png"
        elif not preview_image and local_path:
            prev_u, prev_logo, prev_d = _extract_readme_preview_and_desc(
                full_name, str((remote or {}).get("default_branch") or "main"), local_path
            )
            if prev_u:
                preview_image = prev_u
            if prev_logo and app_id not in ("natex", "naspire"):
                icon_url = prev_logo
                icon_small_url = prev_logo
                is_pixel_icon = "fox" in prev_logo.lower()
            if prev_d and description.startswith("Aplicacion del ecosistema"):
                description = prev_d

        cfg = self.config_mgr.get()
        prefix = (cfg.get("repo_filter_prefix") or "Na").lower()
        topic = (cfg.get("repo_filter_topic") or "nasuite").lower()
        topics_low = {t.lower() for t in topics}
        is_suite_app = bool(
            (remote or {}).get("is_suite_app")
            if (remote and "is_suite_app" in remote)
            else ((prefix and app_id.startswith(prefix)) or (topic and topic in topics_low))
        )
        launch_target = str((local or {}).get("launch_target") or "")
        launch_type = str((local or {}).get("launch_type") or "")
        has_git = bool((local or {}).get("has_git"))
        local_version = str((local or {}).get("local_version") or "")
        local_commit = str((local or {}).get("local_commit") or "")

        remote_version = str((remote or {}).get("remote_version") or "")
        remote_tag = str((remote or {}).get("remote_tag") or "")
        remote_commit = str((remote or {}).get("remote_commit") or "")

        installed = bool(launch_target and os.path.isfile(launch_target))
        running = self.runner.is_running(app_id)
        pid = self.runner.get_pid(app_id)

        update_available = False
        update_reason = ""
        if installed:
            if remote_version and local_version and _is_version_newer(remote_version, local_version):
                update_available = True
                update_reason = f"Nueva version v{remote_version} disponible (local v{local_version})"
            elif has_git and len(local_commit) == 7 and len(remote_commit) == 7 and local_commit != remote_commit:
                update_available = True
                update_reason = f"Nuevos cambios en GitHub ({local_commit} -> {remote_commit})"

        if running:
            status = "running"
        elif installed and update_available:
            status = "update_available"
        elif installed:
            status = "installed"
        else:
            status = "not_installed"

        can_uninstall = False
        if local_path and os.path.isdir(local_path):
            base_low = os.path.basename(local_path).lower()
            if base_low not in PROTECTED_DIR_NAMES:
                has_meta = bool((local or {}).get("has_meta"))
                inside_install = False
                if install_dir:
                    try:
                        inside_install = os.path.commonpath([install_dir, os.path.abspath(local_path)]) == install_dir
                    except ValueError:
                        inside_install = False
                can_uninstall = has_meta or inside_install

        source = "both" if (remote and local) else ("github" if remote else "local")

        return {
            "id": app_id,
            "name": name,
            "full_name": full_name,
            "description": description,
            "category": category,
            "icon": icon_key,
            "icon_url": icon_url,
            "icon_small_url": icon_small_url,
            "is_pixel_icon": is_pixel_icon,
            "preview_image": preview_image,
            "is_suite_app": is_suite_app,
            "topics": topics,
            "language": str((remote or {}).get("language") or "Python"),
            "stars": int((remote or {}).get("stars") or 0),
            "repo_url": str((remote or {}).get("repo_url") or (f"https://github.com/{full_name}" if "/" in full_name and not full_name.startswith("local/") else "")),
            "clone_url": str((remote or {}).get("clone_url") or ""),
            "default_branch": str((remote or {}).get("default_branch") or "main"),
            "updated_at": str((remote or {}).get("updated_at") or ""),
            "is_custom": bool((remote or {}).get("is_custom")),
            "source": source,
            "local_version": local_version,
            "remote_version": remote_version,
            "remote_tag": remote_tag,
            "release_notes": str((remote or {}).get("release_notes") or ""),
            "release_url": str((remote or {}).get("release_url") or ""),
            "release_published_at": str((remote or {}).get("release_published_at") or ""),
            "release_assets": (remote or {}).get("release_assets") or {
                "installer": None,
                "portable_zip": None,
                "portable_exe": None,
            },
            "local_commit": local_commit,
            "remote_commit": remote_commit,
            "recent_commits": list((remote or {}).get("recent_commits") or (local or {}).get("recent_commits") or []),
            "installed": installed,
            "local_path": local_path,
            "launch_target": launch_target,
            "launch_type": launch_type,
            "has_git": has_git,
            "can_uninstall": can_uninstall,
            "status": status,
            "running": running,
            "pid": pid,
            "update_available": update_available,
            "update_reason": update_reason,
        }

    def get_sync_meta(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "synced_at": self._synced_at,
                "offline": self._offline,
                "syncing": self._syncing,
                "warning": self._last_warning,
            }
