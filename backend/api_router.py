# -*- coding: utf-8 -*-
"""REST API router for NaSuite Launcher."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend import APP_NAME, LAUNCHER_VERSION
from backend import autostart
from backend.catalog import AppCatalog
from backend.config import ConfigManager
from backend.notifications import NotificationManager
from backend.runner import AppRunner, LogBuffer


def _json_response(status: int, payload: Dict[str, Any]) -> Tuple[int, str, bytes]:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, "application/json; charset=utf-8", raw


def _parse_json_body(body: bytes) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class ApiRouter:
    """Handles all /api/* HTTP requests for NaSuite Launcher."""

    def __init__(
        self,
        app_root: str,
        resource_root: str,
        on_hide_window: Optional[Callable[[], None]] = None,
        on_notify_balloon: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.app_root = app_root
        self.resource_root = resource_root
        self.logs = LogBuffer(maxlen=500)
        self.config_mgr = ConfigManager(app_root)
        self.runner = AppRunner(self.logs)
        self.catalog = AppCatalog(self.config_mgr, self.runner, self.logs, resource_root)
        self.notifications = NotificationManager(app_root)

        self._on_hide_window = on_hide_window
        self._on_notify_balloon = on_notify_balloon
        self._auto_updating: Set[str] = set()
        self._auto_update_lock = threading.Lock()
        self._stop_event = threading.Event()

        self.logs.add("info", "Launcher", f"{APP_NAME} v{LAUNCHER_VERSION} inicializado.")

        # Start non-blocking initial GitHub sync + periodic auto-sync thread
        self._sync_thread = threading.Thread(target=self._auto_sync_loop, daemon=True)
        self._sync_thread.start()

    def set_window_callbacks(
        self,
        on_hide: Optional[Callable[[], None]] = None,
        on_balloon: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Register native window hide and tray balloon notification callbacks."""
        self._on_hide_window = on_hide
        self._on_notify_balloon = on_balloon

    def _notify_balloon(self, title: str, message: str) -> None:
        cb = self._on_notify_balloon
        if not cb:
            return
        try:
            cb(title, message)
        except Exception:
            pass

    def _schedule_hide_window(self, delay: float = 0.35) -> None:
        cb = self._on_hide_window
        if not cb:
            return

        def worker() -> None:
            if delay > 0:
                time.sleep(delay)
            try:
                cb()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _maybe_auto_update_apps(self, apps: List[Dict[str, Any]]) -> None:
        cfg = self.config_mgr.get()
        if not cfg.get("auto_update_apps", True):
            return

        install_dir = str(cfg.get("install_dir") or "")
        token = str(cfg.get("github_token") or "")

        for app in apps:
            app_id = str(app.get("id") or "").lower()
            app_name = str(app.get("name") or app_id)
            if (
                not app_id
                or not app.get("installed")
                or app.get("status") != "update_available"
                or not app.get("has_git")
                or app.get("running")
            ):
                continue

            with self._auto_update_lock:
                if app_id in self._auto_updating:
                    continue
                self._auto_updating.add(app_id)

            self.logs.add(
                "info",
                "AutoUpdate",
                f"Actualizacion automatica iniciada para {app_name} (git pull)...",
            )

            def _make_on_complete(captured_app: Dict[str, Any], captured_id: str, captured_name: str):
                def _done() -> None:
                    try:
                        self.catalog.scan_local()
                        notif = self.notifications.record_auto_update(captured_app)
                        if notif:
                            self._notify_balloon(
                                notif["title"],
                                notif["summary"],
                            )
                    finally:
                        with self._auto_update_lock:
                            self._auto_updating.discard(captured_id)
                return _done

            try:
                self.runner.start_update(
                    app,
                    install_dir=install_dir,
                    token=token,
                    on_complete=_make_on_complete(dict(app), app_id, app_name),
                )
            except Exception as exc:
                with self._auto_update_lock:
                    self._auto_updating.discard(app_id)
                self.logs.add("warn", "AutoUpdate", f"No se pudo auto-actualizar {app_name}: {exc}")

    def trigger_sync(self) -> Dict[str, Any]:
        """Run full GitHub sync, trigger git auto-updates, and generate What's New notifications."""
        prev_app_ids = [a["id"] for a in self.catalog.get_apps()]
        was_fresh = self.notifications.is_fresh_store

        result = self.catalog.sync_github()
        apps = self.catalog.get_apps()
        commits_by_app = {
            a["id"]: a.get("recent_commits") or []
            for a in apps
        }

        new_notifs = self.notifications.ingest_sync(
            apps,
            prev_app_ids=prev_app_ids,
            commits_by_app=commits_by_app,
            auto_updated_apps=[],
        )

        # Show native Windows balloon for genuinely new items (avoid spamming on very first cold boot)
        if new_notifs and not was_fresh:
            if len(new_notifs) == 1:
                self._notify_balloon(new_notifs[0]["title"], new_notifs[0]["summary"])
            else:
                first = new_notifs[0]
                self._notify_balloon(
                    f"NaSuite: {len(new_notifs)} novedades",
                    f"{first['title']} (+{len(new_notifs) - 1} mas)",
                )

        # Trigger automatic git pull updates in background if enabled
        self._maybe_auto_update_apps(apps)

        return result

    def _auto_sync_loop(self) -> None:
        # Brief delay so HTTP server binds immediately without contention
        if self._stop_event.wait(0.8):
            return
        self.trigger_sync()
        while not self._stop_event.is_set():
            cfg = self.config_mgr.get()
            minutes = int(cfg.get("auto_sync_minutes", 15) or 0)
            if minutes <= 0:
                if self._stop_event.wait(60):
                    break
                continue
            if self._stop_event.wait(minutes * 60):
                break
            self.trigger_sync()

    def shutdown(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # GET routes
    # ------------------------------------------------------------------

    def handle_get(self, path: str, query: Dict[str, str]) -> Tuple[int, str, bytes]:
        if path == "/api/health":
            apps = self.catalog.get_apps()
            meta = self.catalog.get_sync_meta()
            return _json_response(
                200,
                {
                    "ok": True,
                    "app": APP_NAME,
                    "version": LAUNCHER_VERSION,
                    "status": "ready",
                    "apps_count": len(apps),
                    "running_count": sum(1 for a in apps if a.get("running")),
                    "updates_count": sum(1 for a in apps if a.get("update_available")),
                    "unread_notifications": self.notifications.get_unread_count(),
                    **meta,
                },
            )

        if path == "/api/apps":
            if query.get("rescan") == "1":
                self.catalog.scan_local()
            apps = self.catalog.get_apps()
            meta = self.catalog.get_sync_meta()
            return _json_response(200, {"ok": True, "apps": apps, **meta})

        if path == "/api/notifications":
            return _json_response(200, self.notifications.get_payload())

        if path == "/api/tasks":
            return _json_response(200, {"ok": True, "tasks": self.runner.get_tasks()})

        if path.startswith("/api/tasks/"):
            task_id = urllib.parse.unquote(path[len("/api/tasks/"):]).strip("/")
            task = self.runner.get_task(task_id)
            if not task:
                return _json_response(404, {"ok": False, "error": "Tarea no encontrada."})
            return _json_response(200, {"ok": True, "task": task})

        if path == "/api/logs":
            try:
                limit = int(query.get("limit", "500"))
            except ValueError:
                limit = 500
            return _json_response(200, {"ok": True, "logs": self.logs.get_logs(limit=limit)})

        if path == "/api/config":
            return _json_response(200, {"ok": True, "config": self.config_mgr.get()})

        return _json_response(404, {"ok": False, "error": f"Endpoint GET no encontrado: {path}"})

    # ------------------------------------------------------------------
    # POST routes
    # ------------------------------------------------------------------

    def handle_post(self, path: str, body: bytes) -> Tuple[int, str, bytes]:
        payload = _parse_json_body(body)

        if path == "/api/sync":
            result = self.trigger_sync()
            apps = self.catalog.get_apps()
            meta = self.catalog.get_sync_meta()
            notif_data = self.notifications.get_payload()
            return _json_response(
                200,
                {
                    **result,
                    **meta,
                    "apps": apps,
                    "unread_notifications": notif_data["unread_count"],
                },
            )

        if path == "/api/notifications/read":
            mark_all = bool(payload.get("all", False))
            notif_id = payload.get("id")
            data = self.notifications.mark_read(
                notif_id=str(notif_id) if notif_id is not None else None,
                mark_all=mark_all,
            )
            return _json_response(200, data)

        if path == "/api/window/hide":
            self._schedule_hide_window(delay=0.1)
            return _json_response(200, {"ok": True})

        if path == "/api/system/autostart":
            enabled = bool(payload.get("enabled", True))
            ok, message = autostart.set_autostart(enabled)
            updated = self.config_mgr.update({"start_with_windows": enabled})
            self.logs.add("info" if ok else "warn", "Sistema", message)
            return _json_response(
                200 if ok else 400,
                {
                    "ok": ok,
                    "enabled": autostart.is_autostart_enabled(),
                    "message": message,
                    "config": updated,
                },
            )

        if path == "/api/logs/clear":
            self.logs.clear_logs()
            self.logs.add("info", "Launcher", "Registro de consola limpiado.")
            return _json_response(200, {"ok": True})

        if path == "/api/config":
            updated = self.config_mgr.update(payload)
            if "start_with_windows" in payload:
                ok_as, msg_as = autostart.set_autostart(bool(updated["start_with_windows"]))
                self.logs.add("info" if ok_as else "warn", "Sistema", msg_as)
            self.catalog.scan_local()
            self.logs.add("info", "Config", "Configuracion guardada correctamente.")
            return _json_response(200, {"ok": True, "config": updated, "apps": self.catalog.get_apps()})

        if path == "/api/repos/add":
            raw_repo = str(payload.get("repo") or payload.get("url") or "").strip()
            ok, msg_or_slug, cfg = self.config_mgr.add_custom_repo(raw_repo)
            if not ok:
                return _json_response(400, {"ok": False, "error": msg_or_slug})
            self.logs.add("info", "Catalogo", f"Repositorio personalizado agregado: {msg_or_slug}")
            self.trigger_sync()
            return _json_response(
                200,
                {"ok": True, "repo": msg_or_slug, "config": cfg, "apps": self.catalog.get_apps()},
            )

        if path == "/api/repos/remove":
            raw_repo = str(payload.get("repo") or payload.get("id") or "").strip()
            hide = bool(payload.get("hide", False))
            ok, msg_or_slug, cfg = self.config_mgr.remove_custom_repo(raw_repo, hide_if_discovered=hide)
            if not ok:
                return _json_response(400, {"ok": False, "error": msg_or_slug})
            self.logs.add("info", "Catalogo", f"Repositorio removido del catalogo: {msg_or_slug}")
            self.trigger_sync()
            return _json_response(
                200,
                {"ok": True, "repo": msg_or_slug, "config": cfg, "apps": self.catalog.get_apps()},
            )

        # /api/apps/<id>/<action>
        if path.startswith("/api/apps/"):
            rest = path[len("/api/apps/"):].strip("/")
            parts = rest.split("/")
            if len(parts) != 2:
                return _json_response(404, {"ok": False, "error": f"Ruta de app invalida: {path}"})
            app_id = urllib.parse.unquote(parts[0]).strip().lower()
            action = parts[1].strip().lower()
            return self._handle_app_action(app_id, action, payload)

        return _json_response(404, {"ok": False, "error": f"Endpoint POST no encontrado: {path}"})

    def _handle_app_action(self, app_id: str, action: str, payload: Dict[str, Any]) -> Tuple[int, str, bytes]:
        app = self.catalog.get_app_by_id(app_id)
        if not app:
            return _json_response(404, {"ok": False, "error": f"Aplicacion no encontrada: {app_id}"})

        cfg = self.config_mgr.get()
        install_dir = str(cfg.get("install_dir") or "")
        token = str(cfg.get("github_token") or "")

        if action == "launch":
            res = self.runner.launch_app(app)
            if res.get("ok") and cfg.get("minimize_to_tray_on_launch", True):
                self._schedule_hide_window(delay=0.35)
            return _json_response(200 if res.get("ok") else 400, res)

        if action == "stop":
            res = self.runner.stop_app(app_id, app.get("name", app_id))
            return _json_response(200 if res.get("ok") else 400, res)

        if action == "install":
            mode = str(payload.get("mode") or "auto")
            task_id = self.runner.start_install(
                app,
                install_dir=install_dir,
                mode=mode,
                token=token,
                on_complete=self.catalog.scan_local,
            )
            return _json_response(202, {"ok": True, "task_id": task_id, "task": self.runner.get_task(task_id)})

        if action == "update":
            task_id = self.runner.start_update(
                app,
                install_dir=install_dir,
                token=token,
                on_complete=self.catalog.scan_local,
            )
            return _json_response(202, {"ok": True, "task_id": task_id, "task": self.runner.get_task(task_id)})

        if action == "uninstall":
            res = self.runner.uninstall_app(app, install_dir=install_dir, on_complete=self.catalog.scan_local)
            return _json_response(200 if res.get("ok") else 400, res)

        if action == "open-folder":
            res = self.runner.open_folder(app)
            return _json_response(200 if res.get("ok") else 400, res)

        if action == "open-github":
            res = self.runner.open_github(app)
            return _json_response(200 if res.get("ok") else 400, res)

        return _json_response(404, {"ok": False, "error": f"Accion desconocida: {action}"})
