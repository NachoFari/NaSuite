# -*- coding: utf-8 -*-
"""Process runner, centralized log ring buffer, and async install/update task manager."""

from __future__ import annotations

import collections
import datetime
import json
import os
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
from typing import Any, Callable, Deque, Dict, List, Optional

from backend import USER_AGENT

NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
PROTECTED_DIR_NAMES = {"nasuite_launcher", "iconos_pixelart_32x32", "nasuite"}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _now_hms() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _urlopen_safe(req: urllib.request.Request, timeout: int = 30):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError:
        ctx = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _find_python_executable(app_dir: str) -> Optional[str]:
    """Find the best Python interpreter for launching a Python app."""
    candidates = [
        os.path.join(app_dir, ".venv", "Scripts", "python.exe"),
        os.path.join(app_dir, "venv", "Scripts", "python.exe"),
        os.path.join(app_dir, ".venv", "bin", "python"),
        os.path.join(app_dir, "venv", "bin", "python"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    if not getattr(sys, "frozen", False) and sys.executable and os.path.isfile(sys.executable):
        return sys.executable

    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _remove_readonly(func, path, exc_info):
    """Handle read-only files (such as .git pack files) during shutil.rmtree on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


class LogBuffer:
    """Thread-safe ring buffer for centralized launcher and app logs."""

    def __init__(self, maxlen: int = 500) -> None:
        self._entries: Deque[Dict[str, Any]] = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, level: str, source: str, message: str) -> Dict[str, Any]:
        clean_msg = str(message or "").rstrip()
        if not clean_msg:
            return {}
        with self._lock:
            self._seq += 1
            entry = {
                "id": self._seq,
                "ts": _now_hms(),
                "iso": _now_iso(),
                "level": level if level in ("info", "warn", "error", "success") else "info",
                "source": str(source or "Launcher"),
                "message": clean_msg,
            }
            self._entries.append(entry)
            return entry

    def get_logs(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._entries)
        if limit and limit < len(items):
            return items[-limit:]
        return items

    def clear_logs(self) -> None:
        with self._lock:
            self._entries.clear()


class AppRunner:
    """Manages launching/stopping apps and running async install/update background tasks."""

    def __init__(self, log_buffer: LogBuffer) -> None:
        self.logs = log_buffer
        self._procs: Dict[str, subprocess.Popen] = {}
        self._proc_lock = threading.Lock()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._task_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Process tracking & launch / stop
    # ------------------------------------------------------------------

    def is_running(self, app_id: str) -> bool:
        key = (app_id or "").lower()
        with self._proc_lock:
            proc = self._procs.get(key)
            if proc is None:
                return False
            if proc.poll() is None:
                return True
            self._procs.pop(key, None)
            return False

    def get_pid(self, app_id: str) -> Optional[int]:
        key = (app_id or "").lower()
        with self._proc_lock:
            proc = self._procs.get(key)
            if proc is not None and proc.poll() is None:
                return proc.pid
            if proc is not None:
                self._procs.pop(key, None)
            return None

    def _pipe_stream_to_logs(self, stream, source: str, level: str = "info") -> None:
        try:
            for raw in iter(stream.readline, ""):
                line = raw.strip()
                if line:
                    self.logs.add(level, source, line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _watch_process_exit(self, app_id: str, app_name: str, proc: subprocess.Popen) -> None:
        code = proc.wait()
        with self._proc_lock:
            if self._procs.get(app_id) is proc:
                self._procs.pop(app_id, None)
        lvl = "info" if code == 0 else "warn"
        self.logs.add(lvl, app_name, f"Proceso finalizado (codigo {code}).")

    def launch_app(self, app_record: Dict[str, Any]) -> Dict[str, Any]:
        app_id = str(app_record.get("id") or "").lower()
        app_name = str(app_record.get("name") or app_id)
        target = str(app_record.get("launch_target") or "")
        launch_type = str(app_record.get("launch_type") or "")

        if not app_id:
            return {"ok": False, "error": "ID de aplicacion invalido."}
        if not target or not os.path.isfile(target):
            return {"ok": False, "error": f"No se encontro el ejecutable o script para {app_name}."}

        existing_pid = self.get_pid(app_id)
        if existing_pid is not None:
            return {
                "ok": True,
                "already_running": True,
                "pid": existing_pid,
                "message": f"{app_name} ya esta en ejecucion (PID {existing_pid}).",
            }

        cwd = os.path.dirname(target)
        low_target = target.lower()
        try:
            if launch_type == "html" or low_target.endswith((".html", ".htm")):
                if sys.platform == "win32":
                    os.startfile(target)
                else:
                    webbrowser.open(f"file://{os.path.abspath(target)}")
                self.logs.add("success", app_name, f"Abierto en navegador -> {os.path.basename(target)}")
                return {
                    "ok": True,
                    "pid": 0,
                    "target": target,
                    "message": f"{app_name} abierto correctamente.",
                }
            elif launch_type == "bat" or low_target.endswith((".bat", ".cmd")):
                proc = subprocess.Popen(
                    [target],
                    cwd=cwd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif launch_type == "exe" or low_target.endswith(".exe"):
                proc = subprocess.Popen(
                    [target],
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                py_exe = _find_python_executable(cwd)
                if not py_exe:
                    return {
                        "ok": False,
                        "error": "No se encontro un interprete de Python disponible en el sistema.",
                    }
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.Popen(
                    [py_exe, target],
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=NO_WINDOW_FLAG,
                )
                if proc.stdout:
                    threading.Thread(
                        target=self._pipe_stream_to_logs,
                        args=(proc.stdout, app_name, "info"),
                        daemon=True,
                    ).start()
                if proc.stderr:
                    threading.Thread(
                        target=self._pipe_stream_to_logs,
                        args=(proc.stderr, app_name, "warn"),
                        daemon=True,
                    ).start()

            with self._proc_lock:
                self._procs[app_id] = proc

            threading.Thread(
                target=self._watch_process_exit,
                args=(app_id, app_name, proc),
                daemon=True,
            ).start()

            self.logs.add("success", app_name, f"Iniciado correctamente (PID {proc.pid}) -> {os.path.basename(target)}")
            return {
                "ok": True,
                "pid": proc.pid,
                "target": target,
                "message": f"{app_name} iniciado (PID {proc.pid}).",
            }
        except Exception as exc:
            self.logs.add("error", app_name, f"Error al iniciar: {exc}")
            return {"ok": False, "error": f"No se pudo iniciar {app_name}: {exc}"}

    def stop_app(self, app_id: str, app_name: str = "") -> Dict[str, Any]:
        key = (app_id or "").lower()
        label = app_name or app_id
        with self._proc_lock:
            proc = self._procs.get(key)

        if proc is None or proc.poll() is not None:
            with self._proc_lock:
                self._procs.pop(key, None)
            return {"ok": False, "error": f"{label} no esta en ejecucion."}

        pid = proc.pid
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=NO_WINDOW_FLAG,
                    )
                else:
                    proc.kill()
            with self._proc_lock:
                self._procs.pop(key, None)
            self.logs.add("info", label, f"Aplicacion detenida (PID {pid}).")
            return {"ok": True, "message": f"{label} detenido."}
        except Exception as exc:
            self.logs.add("error", label, f"Error al detener: {exc}")
            return {"ok": False, "error": f"No se pudo detener {label}: {exc}"}

    def open_folder(self, app_record: Dict[str, Any]) -> Dict[str, Any]:
        local_path = str(app_record.get("local_path") or "")
        launch_target = str(app_record.get("launch_target") or "")
        target_dir = ""
        if local_path and os.path.isdir(local_path):
            target_dir = os.path.abspath(local_path)
        elif launch_target and os.path.isfile(launch_target):
            target_dir = os.path.dirname(os.path.abspath(launch_target))
        if not target_dir or not os.path.isdir(target_dir):
            return {"ok": False, "error": "Carpeta local no encontrada."}
        try:
            if sys.platform == "win32":
                os.startfile(target_dir)
            else:
                subprocess.Popen(["xdg-open", target_dir])
            return {"ok": True, "path": target_dir}
        except Exception as exc:
            return {"ok": False, "error": f"No se pudo abrir la carpeta: {exc}"}

    def open_github(self, app_record: Dict[str, Any]) -> Dict[str, Any]:
        url = str(app_record.get("repo_url") or "")
        if not url:
            return {"ok": False, "error": "Esta aplicacion no tiene URL de GitHub."}
        try:
            webbrowser.open(url)
            return {"ok": True, "url": url}
        except Exception as exc:
            return {"ok": False, "error": f"No se pudo abrir el navegador: {exc}"}

    # ------------------------------------------------------------------
    # Background task manager
    # ------------------------------------------------------------------

    def _create_task(self, app_id: str, app_name: str, action: str) -> str:
        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id,
            "app_id": app_id,
            "app_name": app_name,
            "action": action,
            "status": "running",
            "progress": 0,
            "stage": "Iniciando...",
            "logs": [],
            "error": "",
            "started_at": _now_iso(),
            "finished_at": "",
        }
        with self._task_lock:
            self._tasks[task_id] = task
            # Keep at most 40 historical tasks
            if len(self._tasks) > 40:
                oldest = list(self._tasks.keys())[:-40]
                for old_id in oldest:
                    if self._tasks[old_id].get("status") != "running":
                        self._tasks.pop(old_id, None)
        return task_id

    def _update_task(
        self,
        task_id: str,
        *,
        progress: Optional[int] = None,
        stage: Optional[str] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
        log_line: Optional[str] = None,
        log_level: str = "info",
    ) -> None:
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if progress is not None:
                task["progress"] = max(0, min(100, int(progress)))
            if stage is not None:
                task["stage"] = stage
            if status is not None:
                task["status"] = status
                if status in ("done", "error"):
                    task["finished_at"] = _now_iso()
            if error is not None:
                task["error"] = error
            if log_line:
                clean = log_line.strip()
                if clean:
                    task["logs"].append(f"[{_now_hms()}] {clean}")
                    if len(task["logs"]) > 200:
                        task["logs"] = task["logs"][-200:]
            app_name = task["app_name"]
        if log_line:
            self.logs.add(log_level, app_name, log_line)

    def get_tasks(self) -> List[Dict[str, Any]]:
        with self._task_lock:
            items = [dict(t, logs=list(t["logs"])) for t in self._tasks.values()]
        items.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return items

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return dict(task, logs=list(task["logs"]))

    def _download_file(
        self,
        url: str,
        dest_path: str,
        task_id: str,
        token: str = "",
        progress_start: int = 10,
        progress_end: int = 75,
    ) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp_path = f"{dest_path}.part"
        with _urlopen_safe(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            chunk_size = 65536
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        ratio = downloaded / total
                        pct = progress_start + int((progress_end - progress_start) * ratio)
                        mb_done = downloaded / (1024 * 1024)
                        mb_total = total / (1024 * 1024)
                        self._update_task(
                            task_id,
                            progress=pct,
                            stage=f"Descargando ({mb_done:.1f} / {mb_total:.1f} MB)...",
                        )
        os.replace(tmp_path, dest_path)

    def _run_cmd_stream(
        self,
        cmd: List[str],
        cwd: Optional[str],
        task_id: str,
    ) -> int:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW_FLAG,
        )
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, ""):
            line = raw.strip()
            if line:
                self._update_task(task_id, log_line=line)
        return proc.wait()

    @staticmethod
    def _write_installed_meta(dest_dir: str, app_record: Dict[str, Any], mode: str) -> None:
        os.makedirs(dest_dir, exist_ok=True)
        meta = {
            "id": app_record.get("id", ""),
            "name": app_record.get("name", ""),
            "repo": app_record.get("full_name", ""),
            "repo_url": app_record.get("repo_url", ""),
            "version": app_record.get("remote_version", ""),
            "tag": app_record.get("remote_tag", ""),
            "commit": app_record.get("remote_commit", ""),
            "install_mode": mode,
            "installed_at": _now_iso(),
        }
        with open(os.path.join(dest_dir, "installed_meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)

    def _extract_zip_clean(self, zip_path: str, dest_dir: str, task_id: str) -> None:
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            total = max(1, len(members))
            for idx, member in enumerate(members, start=1):
                zf.extract(member, dest_dir)
                if idx % 20 == 0 or idx == total:
                    pct = 75 + int(20 * (idx / total))
                    self._update_task(task_id, progress=pct, stage=f"Extrayendo archivos ({idx}/{total})...")

    def _install_from_release(
        self,
        app_record: Dict[str, Any],
        install_dir: str,
        mode: str,
        token: str,
        task_id: str,
    ) -> bool:
        assets = app_record.get("release_assets") or {}
        portable_zip = assets.get("portable_zip")
        portable_exe = assets.get("portable_exe")
        installer = assets.get("installer")
        repo_name = str(app_record.get("name") or app_record.get("id"))
        dest_dir = os.path.join(install_dir, repo_name)
        dl_dir = os.path.join(tempfile.gettempdir(), "NaSuite_Downloads")
        os.makedirs(dl_dir, exist_ok=True)

        if mode in ("auto", "portable_zip") and portable_zip and portable_zip.get("url"):
            asset_name = portable_zip.get("name") or f"{repo_name}.zip"
            zip_path = os.path.join(dl_dir, asset_name)
            self._update_task(task_id, progress=10, stage=f"Descargando {asset_name}...", log_line=f"Descargando ZIP portable: {asset_name}")
            self._download_file(portable_zip["url"], zip_path, task_id, token=token)
            self._update_task(task_id, progress=76, stage="Extrayendo paquete portable...", log_line=f"Extrayendo en {dest_dir}")
            self._extract_zip_clean(zip_path, dest_dir, task_id)
            self._write_installed_meta(dest_dir, app_record, "portable_zip")
            try:
                os.remove(zip_path)
            except OSError:
                pass
            return True

        if mode in ("auto", "portable_exe") and portable_exe and portable_exe.get("url"):
            asset_name = portable_exe.get("name") or f"{repo_name}.exe"
            exe_dest = os.path.join(dest_dir, asset_name)
            self._update_task(task_id, progress=10, stage=f"Descargando {asset_name}...", log_line=f"Descargando ejecutable portable: {asset_name}")
            self._download_file(portable_exe["url"], exe_dest, task_id, token=token, progress_end=92)
            self._write_installed_meta(dest_dir, app_record, "portable_exe")
            return True

        if mode in ("auto", "release_installer") and installer and installer.get("url"):
            asset_name = installer.get("name") or f"{repo_name}-Setup.exe"
            setup_path = os.path.join(dl_dir, asset_name)
            self._update_task(task_id, progress=10, stage=f"Descargando instalador {asset_name}...", log_line=f"Descargando instalador: {asset_name}")
            self._download_file(installer["url"], setup_path, task_id, token=token, progress_end=90)
            self._update_task(task_id, progress=92, stage="Ejecutando instalador...", log_line=f"Lanzando instalador: {setup_path}")
            subprocess.Popen([setup_path], cwd=dl_dir)
            return True

        return False

    def _install_via_github_zip(
        self,
        app_record: Dict[str, Any],
        install_dir: str,
        task_id: str,
        token: str = "",
        overwrite: bool = False,
    ) -> None:
        """Download and extract repository source ZIP directly from GitHub (works on any PC without Git)."""
        repo_name = str(app_record.get("name") or app_record.get("id"))
        full_name = str(app_record.get("full_name") or "")
        branch = str(app_record.get("default_branch") or "main")
        if not full_name or "/" not in full_name:
            raise RuntimeError("No se encontro el repositorio de GitHub para descargar.")

        zip_url = f"https://github.com/{full_name}/archive/refs/heads/{branch}.zip"
        dl_dir = os.path.join(tempfile.gettempdir(), "NaSuite_Downloads")
        os.makedirs(dl_dir, exist_ok=True)
        zip_path = os.path.join(dl_dir, f"{repo_name}-{branch}.zip")
        dest_dir = os.path.join(install_dir, repo_name)

        if os.path.isdir(dest_dir) and os.listdir(dest_dir) and not overwrite:
            shutil.rmtree(dest_dir, onerror=_remove_readonly)

        self._update_task(
            task_id,
            progress=15,
            stage=f"Descargando {repo_name} desde GitHub...",
            log_line=f"Descargando archivo ZIP: {zip_url}",
        )
        self._download_file(zip_url, zip_path, task_id, token=token, progress_start=15, progress_end=72)

        self._update_task(task_id, progress=75, stage="Extrayendo archivos...", log_line=f"Extrayendo en {dest_dir}")
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            top_prefix = ""
            if members and "/" in members[0]:
                candidate_prefix = members[0].split("/", 1)[0] + "/"
                if all(m.startswith(candidate_prefix) for m in members if m.strip()):
                    top_prefix = candidate_prefix
            total = max(1, len(members))
            for idx, member in enumerate(members, start=1):
                rel_name = member[len(top_prefix):] if (top_prefix and member.startswith(top_prefix)) else member
                if not rel_name:
                    continue
                target_path = os.path.join(dest_dir, rel_name)
                if member.endswith("/"):
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(member) as src_f, open(target_path, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
                if idx % 20 == 0 or idx == total:
                    pct = 75 + int(18 * (idx / total))
                    self._update_task(task_id, progress=pct, stage=f"Extrayendo ({idx}/{total})...")

        try:
            os.remove(zip_path)
        except OSError:
            pass

        self._write_installed_meta(dest_dir, app_record, "github_zip")

    def _install_via_git_clone(
        self,
        app_record: Dict[str, Any],
        install_dir: str,
        task_id: str,
        token: str = "",
    ) -> None:
        git_exe = shutil.which("git")
        if not git_exe:
            self._install_via_github_zip(app_record, install_dir, task_id, token=token, overwrite=True)
            return

        repo_name = str(app_record.get("name") or app_record.get("id"))
        clone_url = str(app_record.get("clone_url") or app_record.get("repo_url") or "")
        if not clone_url:
            self._install_via_github_zip(app_record, install_dir, task_id, token=token, overwrite=True)
            return

        os.makedirs(install_dir, exist_ok=True)
        dest_dir = os.path.join(install_dir, repo_name)
        if os.path.isdir(dest_dir) and os.listdir(dest_dir):
            shutil.rmtree(dest_dir, onerror=_remove_readonly)

        self._update_task(task_id, progress=15, stage=f"Clonando {repo_name}...", log_line=f"git clone {clone_url} -> {dest_dir}")
        rc = self._run_cmd_stream([git_exe, "clone", "--progress", clone_url, dest_dir], install_dir, task_id)
        if rc != 0:
            self._update_task(task_id, log_line="git clone fallo; usando descarga directa ZIP desde GitHub...", log_level="warn")
            self._install_via_github_zip(app_record, install_dir, task_id, token=token, overwrite=True)
            return

        self._update_task(task_id, progress=70, stage="Verificando dependencias...")
        req_file = os.path.join(dest_dir, "requirements.txt")
        if os.path.isfile(req_file):
            py_exe = _find_python_executable(dest_dir)
            if py_exe:
                self._update_task(task_id, progress=75, stage="Instalando requirements.txt...", log_line="Ejecutando pip install -r requirements.txt")
                pip_rc = self._run_cmd_stream([py_exe, "-m", "pip", "install", "-r", req_file], dest_dir, task_id)
                if pip_rc != 0:
                    self._update_task(task_id, log_line=f"Aviso: pip install termino con codigo {pip_rc}.", log_level="warn")

        self._write_installed_meta(dest_dir, app_record, "git_clone")

    def start_install(
        self,
        app_record: Dict[str, Any],
        install_dir: str,
        mode: str = "auto",
        token: str = "",
        on_complete: Optional[Callable[[], None]] = None,
    ) -> str:
        app_id = str(app_record.get("id") or "")
        app_name = str(app_record.get("name") or app_id)
        task_id = self._create_task(app_id, app_name, "install")

        def worker():
            try:
                self._update_task(task_id, progress=5, stage="Preparando instalacion...", log_line=f"Iniciando instalacion de {app_name} (modo: {mode})")
                installed = False
                if mode != "git_clone":
                    installed = self._install_from_release(app_record, install_dir, mode, token, task_id)
                if not installed:
                    self._install_via_git_clone(app_record, install_dir, task_id)

                if on_complete:
                    self._update_task(task_id, progress=96, stage="Actualizando catalogo local...")
                    on_complete()

                self._update_task(
                    task_id,
                    progress=100,
                    stage="Instalacion completada",
                    status="done",
                    log_line=f"{app_name} instalado correctamente.",
                    log_level="success",
                )
            except Exception as exc:
                self._update_task(
                    task_id,
                    status="error",
                    stage="Error en instalacion",
                    error=str(exc),
                    log_line=f"Error instalando {app_name}: {exc}",
                    log_level="error",
                )

        threading.Thread(target=worker, daemon=True).start()
        return task_id

    def start_update(
        self,
        app_record: Dict[str, Any],
        install_dir: str,
        token: str = "",
        on_complete: Optional[Callable[[], None]] = None,
    ) -> str:
        app_id = str(app_record.get("id") or "")
        app_name = str(app_record.get("name") or app_id)
        task_id = self._create_task(app_id, app_name, "update")

        def worker():
            try:
                if self.is_running(app_id):
                    self._update_task(task_id, progress=5, stage="Deteniendo instancia activa...")
                    self.stop_app(app_id, app_name)

                local_path = str(app_record.get("local_path") or "")
                has_git = bool(app_record.get("has_git"))

                if has_git and local_path and os.path.isdir(local_path) and shutil.which("git"):
                    git_exe = shutil.which("git")
                    branch = str(app_record.get("default_branch") or "").strip()
                    pull_cmd = (
                        [git_exe, "-C", local_path, "pull", "--ff-only", "origin", branch]
                        if branch
                        else [git_exe, "-C", local_path, "pull", "--ff-only"]
                    )
                    self._update_task(task_id, progress=20, stage="Descargando cambios desde Git...", log_line=" ".join(pull_cmd))
                    rc = self._run_cmd_stream(pull_cmd, local_path, task_id)
                    if rc != 0:
                        raise RuntimeError(f"git pull fallo con codigo {rc}. Revisa cambios locales sin confirmar.")
                    self._update_task(task_id, progress=85, stage="Actualizando metadatos...")
                else:
                    self._update_task(task_id, progress=10, stage="Descargando ultima version...", log_line=f"Actualizando {app_name} desde GitHub")
                    ok = self._install_from_release(app_record, install_dir, "auto", token, task_id)
                    if not ok:
                        self._install_via_github_zip(app_record, install_dir, task_id, token=token, overwrite=True)

                if on_complete:
                    self._update_task(task_id, progress=95, stage="Verificando instalacion...")
                    on_complete()

                self._update_task(
                    task_id,
                    progress=100,
                    stage="Actualizacion completada",
                    status="done",
                    log_line=f"{app_name} actualizado correctamente.",
                    log_level="success",
                )
            except Exception as exc:
                self._update_task(
                    task_id,
                    status="error",
                    stage="Error al actualizar",
                    error=str(exc),
                    log_line=f"Error actualizando {app_name}: {exc}",
                    log_level="error",
                )

        threading.Thread(target=worker, daemon=True).start()
        return task_id

    def uninstall_app(
        self,
        app_record: Dict[str, Any],
        install_dir: str,
        on_complete: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        app_id = str(app_record.get("id") or "")
        app_name = str(app_record.get("name") or app_id)
        local_path = str(app_record.get("local_path") or "")

        if not local_path or not os.path.isdir(local_path):
            return {"ok": False, "error": f"{app_name} no tiene una carpeta local instalada."}

        abs_local = os.path.abspath(local_path)
        abs_install = os.path.abspath(install_dir)
        base_lower = os.path.basename(abs_local).lower()

        if base_lower in PROTECTED_DIR_NAMES:
            return {"ok": False, "error": f"No se permite desinstalar el directorio protegido '{base_lower}'."}

        if abs_local == abs_install:
            return {"ok": False, "error": "No se puede eliminar la carpeta raiz de instalacion."}

        has_meta = os.path.isfile(os.path.join(abs_local, "installed_meta.json"))
        inside_install = False
        try:
            inside_install = os.path.commonpath([abs_install, abs_local]) == abs_install
        except ValueError:
            inside_install = False

        if not has_meta and not inside_install:
            return {
                "ok": False,
                "error": "Por seguridad solo se pueden desinstalar apps ubicadas en install_dir o con installed_meta.json.",
            }

        if self.is_running(app_id):
            self.stop_app(app_id, app_name)

        try:
            shutil.rmtree(abs_local, onerror=_remove_readonly)
            self.logs.add("info", app_name, f"Desinstalado correctamente ({abs_local}).")
            if on_complete:
                on_complete()
            return {"ok": True, "message": f"{app_name} desinstalado."}
        except Exception as exc:
            self.logs.add("error", app_name, f"Error al desinstalar: {exc}")
            return {"ok": False, "error": f"No se pudo eliminar {abs_local}: {exc}"}
