"""Find where this computer saves screenshots (Windows / macOS / Linux).

detect() returns {folder: dedicated}
  dedicated=True  -> a folder that only holds screenshots: every image is taken
  dedicated=False -> a shared folder (Desktop, Pictures, Downloads): only files whose
                     name looks like a screenshot are taken
"""
import os, platform, re, subprocess
from pathlib import Path

SHOT_NAME = re.compile(
    r"screen[\s_-]?shot|screen[\s_-]?capture|screen[\s_-]?clip|snip|lightshot|greenshot|"
    r"flameshot|spectacle|shottr|sharex|capture|skärmbild|captura|capture d.écran|bildschirmfoto",
    re.I)
HOME = Path.home()


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _windows_pictures():
    """'Pictures' is often redirected to OneDrive, so read the real path from the registry."""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, "My Pictures")[0]))
    except Exception:
        return HOME / "Pictures"


def detect():
    system = platform.system()
    dedicated, shared = [], []
    if system == "Windows":
        pics = _windows_pictures()
        dedicated += [pics / "Screenshots", HOME / "Pictures" / "Screenshots",
                      HOME / "OneDrive" / "Pictures" / "Screenshots"]
        shared += [pics, HOME / "Desktop", HOME / "OneDrive" / "Desktop", HOME / "Downloads"]
    elif system == "Darwin":
        loc = _run(["defaults", "read", "com.apple.screencapture", "location"])
        if loc and Path(loc).expanduser() != HOME / "Desktop":
            dedicated.append(Path(loc).expanduser())
        shared += [HOME / "Desktop", HOME / "Pictures", HOME / "Downloads"]
    else:
        pics = Path(_run(["xdg-user-dir", "PICTURES"]) or HOME / "Pictures")
        desk = Path(_run(["xdg-user-dir", "DESKTOP"]) or HOME / "Desktop")
        dedicated += [pics / "Screenshots"]
        shared += [pics, desk, HOME / "Downloads"]

    out = {}
    for paths, flag in ((shared, False), (dedicated, True)):   # dedicated wins on overlap
        for p in paths:
            try:
                if p.is_dir():
                    out[str(p.resolve())] = flag
            except OSError:
                pass
    return out
