"""Where the app keeps its data. One directory per user, never inside the repo."""
import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("JOBHUNT_HOME")
    if override:
        p = Path(override).expanduser()
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "JobHuntAgent"
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home())) / "JobHuntAgent"
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "job-hunt-agent"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "jobhunt.db"


def packages_dir() -> Path:
    p = data_dir() / "packages"
    p.mkdir(exist_ok=True)
    return p


def resource_dir() -> Path:
    """Bundled read-only resources (templates). Works under PyInstaller too."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "jobhunt" if base else Path(__file__).resolve().parent
