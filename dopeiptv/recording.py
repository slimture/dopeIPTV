"""Recording manager: ffmpeg/mpv stream-copy and in-player recording."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from PyQt6.QtCore import QObject, QSettings, QTimer, pyqtSignal

from .client import find_player_executable


def _bundled_ffmpeg() -> str | None:
    """Path to an ffmpeg shipped inside a frozen (PyInstaller/AppImage) build.

    The spec bundles the ffmpeg binary next to the executable so recording
    works without a system install; it is not on PATH, so look for it here
    before falling back to shutil.which().
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    for cand in (os.path.join(base, exe),
                 os.path.join(os.path.dirname(sys.executable), exe)):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def safe_filename(name: str | None) -> str:
    """Strip characters that are unsafe in filenames."""
    cleaned = "".join(c for c in (name or "recording")
                      if c not in '/\\:*?"<>|').strip()
    return cleaned[:120] or "recording"


def format_size(nbytes: float) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return (f"{nbytes:.1f} {unit}" if unit not in ("B", "KB")
                    else f"{int(nbytes)} {unit}")
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


class RecordingManager(QObject):
    """Records live streams to local files.

    Uses ffmpeg (stream copy, no re-encode) when available, otherwise
    mpv's --stream-record.  Scheduled-but-not-yet-started jobs survive
    an app restart (persisted via QSettings).
    """

    jobs_changed = pyqtSignal()
    recording_stopped = pyqtSignal(str, str)

    VIDEO_EXTS: set[str] = {".ts", ".mp4", ".mkv", ".avi", ".mov",
                             ".webm", ".m2ts"}

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.jobs: list[dict] = []
        self._load()
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.tick)
        self._timer.start()
        self.session_cap: int | None = None
        self.stop_inplayer_cb: Callable | None = None

    # -- storage location ---------------------------------------------------

    def directory(self) -> str:
        d = self.settings.value("recordings_dir", "")
        if not d:
            d = os.path.join(os.path.expanduser("~"), "Videos", "dopeIPTV")
        return d

    def set_directory(self, d: str) -> None:
        self.settings.setValue("recordings_dir", d)

    def folders(self) -> list[str]:
        """All subfolders (relative paths) below the recordings directory."""
        root = self.directory()
        found: list[str] = []
        if os.path.isdir(root):
            for base, dirs, _files in os.walk(root):
                dirs.sort()
                for d in dirs:
                    found.append(os.path.relpath(os.path.join(base, d), root))
        return found

    def files(self, folder: str | None = None) -> list[dict]:
        """Recording files as list items."""
        root = self.directory()
        base = os.path.join(root, folder) if folder else root
        out: list[dict] = []
        if not os.path.isdir(base):
            return out
        walker = (os.walk(base) if folder is None or folder == ""
                  else [(base, [], os.listdir(base))])
        for dirpath, _dirs, names in walker:
            for n in names:
                p = os.path.join(dirpath, n)
                if (os.path.splitext(n)[1].lower() in self.VIDEO_EXTS
                        and os.path.isfile(p)):
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    out.append({"name": os.path.splitext(n)[0], "_path": p,
                                "_key": p, "_kind": "recording",
                                "_size": st.st_size,
                                "added": str(int(st.st_mtime))})
        out.sort(key=lambda f: f["added"], reverse=True)
        return out

    # -- recorder backend ---------------------------------------------------

    @staticmethod
    def recorder() -> tuple[str | None, str | None]:
        """(kind, executable) of the available recorder, or (None, None)."""
        ff = _bundled_ffmpeg() or shutil.which("ffmpeg")
        if ff:
            return "ffmpeg", ff
        mpv = find_player_executable("mpv")
        if mpv:
            return "mpv", mpv
        return None, None

    # -- jobs ---------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(
                self.settings.value("recording_jobs", "") or "[]")
        except Exception:
            data = []
        now = time.time()
        for j in data if isinstance(data, list) else []:
            if (j.get("status") == "scheduled"
                    and (j.get("stop") is None or j["stop"] > now)):
                j["proc"] = None
                self.jobs.append(j)

    def _save(self) -> None:
        keep = [{k: v for k, v in j.items() if k != "proc"}
                for j in self.jobs if j.get("status") == "scheduled"]
        self.settings.setValue("recording_jobs", json.dumps(keep))

    def add_job(self, url: str, title: str, start_ts: float,
                stop_ts: float | None, folder: str = "") -> dict:
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10], "url": url, "title": title,
            "start": start_ts, "stop": stop_ts, "folder": folder or "",
            "status": "scheduled", "path": "", "error": "", "proc": None}
        self.jobs.append(job)
        self._save()
        self.tick()
        self.jobs_changed.emit()
        return job

    def update_job_times(self, job_id: str, start_ts: float,
                         stop_ts: float | None) -> None:
        """Adjust a not-yet-started scheduled job's start/stop time."""
        for j in self.jobs:
            if j["id"] == job_id and j["status"] == "scheduled":
                j["start"] = start_ts
                j["stop"] = stop_ts
                self._save()
                self.jobs_changed.emit()
                return

    def add_inplayer_job(self, title: str, path: str,
                         stop_ts: float | None, url: str = "") -> dict:
        """Register a recording that rides the embedded player's stream."""
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10], "url": url, "title": title,
            "start": time.time(), "stop": stop_ts, "folder": "",
            "status": "recording", "path": path, "error": "",
            "proc": None, "inplayer": True}
        self.jobs.append(job)
        self.jobs_changed.emit()
        return job

    def finish_inplayer(self, job_id: str, reason: str = "") -> None:
        for j in self.jobs:
            if (j["id"] != job_id or not j.get("inplayer")
                    or j["status"] != "recording"):
                continue
            if self.stop_inplayer_cb:
                try:
                    self.stop_inplayer_cb()
                except Exception:
                    pass
            j["status"] = "done"
            self.recording_stopped.emit(j["title"], reason or "stopped")
            self.jobs_changed.emit()

            def validate(j=j, reason=reason):
                if (j.get("path") and os.path.exists(j["path"])
                        and os.path.getsize(j["path"]) > 0):
                    return
                j["status"] = "failed"
                j["error"] = reason or "no data captured"
                self.jobs_changed.emit()

            QTimer.singleShot(1500, validate)
            return

    def finish_all_inplayer(self, reason: str = "") -> None:
        for j in list(self.jobs):
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(j["id"], reason)

    def cancel(self, job_id: str) -> None:
        for j in self.jobs:
            if j["id"] != job_id:
                continue
            if j.get("inplayer") and j["status"] == "recording":
                self.finish_inplayer(job_id)
            elif j["status"] == "recording":
                self._stop_proc(j)
                j["status"] = "done"
            elif j["status"] == "scheduled":
                j["status"] = "cancelled"
            self._save()
            self.jobs_changed.emit()
            return

    def remove_job(self, job_id: str) -> None:
        self.jobs = [j for j in self.jobs
                     if j["id"] != job_id
                     or j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def clear_finished(self) -> None:
        """Remove all done/failed/cancelled jobs from the list."""
        self.jobs = [j for j in self.jobs
                     if j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def prune_path(self, path: str) -> None:
        """Remove finished jobs whose file matches *path*."""
        self.jobs = [j for j in self.jobs
                     if j.get("path") != path
                     or j["status"] in ("recording", "scheduled")]
        self._save()
        self.jobs_changed.emit()

    def active_count(self) -> int:
        return sum(1 for j in self.jobs if j["status"] == "recording")

    def build_path(self, title: str, folder: str = "") -> str:
        """A unique target file for a new recording."""
        stamp = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H.%M")
        target_dir = os.path.join(self.directory(), folder or "")
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir,
                            f"{safe_filename(title)} {stamp}.ts")
        n = 1
        while os.path.exists(path):
            path = os.path.join(target_dir,
                                f"{safe_filename(title)} {stamp} ({n}).ts")
            n += 1
        return path

    def _spawn(self, j: dict) -> None:
        kind, exe = self.recorder()
        if not exe:
            j["status"] = "failed"
            j["error"] = "neither ffmpeg nor mpv found"
            return
        try:
            path = self.build_path(j["title"], j.get("folder") or "")
        except OSError as e:
            j["status"] = "failed"
            j["error"] = str(e)
            return
        secs = (max(1, int(j["stop"] - time.time()))
                if j.get("stop") else None)
        if kind == "ffmpeg":
            cmd = [exe, "-y", "-loglevel", "error", "-i", j["url"],
                   "-c", "copy"]
            if secs:
                cmd += ["-t", str(secs)]
            cmd.append(path)
        else:
            cmd = [exe, j["url"], f"--stream-record={path}", "--vo=null",
                   "--ao=null", "--no-terminal"]
            if secs:
                cmd.append(f"--length={secs}")
        try:
            j["proc"] = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            j["path"] = path
            j["status"] = "recording"
        except Exception as e:
            j["status"] = "failed"
            j["error"] = str(e)

    @staticmethod
    def _stop_proc(j: dict) -> None:
        p = j.get("proc")
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()

    def _max_bytes(self) -> int:
        """Size cap for recordings (0 = no cap). Session override wins."""
        if self.session_cap is not None:
            return self.session_cap
        try:
            val = float(self.settings.value("rec_max_value", 0) or 0)
        except (TypeError, ValueError):
            return 0
        mult = {"MB": 10**6, "GB": 10**9, "TB": 10**12}.get(
            self.settings.value("rec_max_unit", "GB"), 10**9)
        return int(val * mult) if val > 0 else 0

    def _over_size_cap(self, j: dict, cap: int) -> bool:
        try:
            return (cap > 0 and bool(j.get("path"))
                    and os.path.exists(j["path"])
                    and os.path.getsize(j["path"]) >= cap)
        except OSError:
            return False

    def tick(self) -> None:
        now = time.time()
        cap = self._max_bytes()
        changed = False
        for j in self.jobs:
            if j["status"] == "scheduled" and j["start"] <= now:
                if j.get("stop") is not None and j["stop"] <= now:
                    j["status"] = "failed"
                    j["error"] = "stop time passed before the app could start it"
                else:
                    self._spawn(j)
                self._save()
                changed = True
            elif j["status"] == "recording" and j.get("inplayer"):
                if j.get("stop") is not None and j["stop"] <= now:
                    self.finish_inplayer(j["id"], "finished")
                elif self._over_size_cap(j, cap):
                    self.finish_inplayer(j["id"], "size limit reached")
            elif j["status"] == "recording":
                rc = j["proc"].poll() if j.get("proc") else 0
                if ((j.get("stop") is not None and j["stop"] <= now)
                        or self._over_size_cap(j, cap)):
                    self._stop_proc(j)
                    j["status"] = "done"
                    self.recording_stopped.emit(
                        j["title"],
                        "size limit reached" if self._over_size_cap(j, cap)
                        else "finished")
                    changed = True
                elif rc is not None:
                    ok = (rc == 0 and j.get("path")
                          and os.path.exists(j["path"])
                          and os.path.getsize(j["path"]) > 0)
                    j["status"] = "done" if ok else "failed"
                    if not ok:
                        j["error"] = f"recorder exited early (code {rc})"
                    self.recording_stopped.emit(
                        j["title"], "finished" if ok
                        else "recorder stopped unexpectedly")
                    changed = True
        if changed:
            self.jobs_changed.emit()

    def shutdown(self) -> None:
        self.finish_all_inplayer("app closed")
        for j in self.jobs:
            if j["status"] == "recording" and not j.get("inplayer"):
                self._stop_proc(j)
        self._save()
