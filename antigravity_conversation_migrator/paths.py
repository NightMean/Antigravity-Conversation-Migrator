"""
Environment, Path Resolution & Installation Detection
======================================================
Locates Antigravity IDE and Antigravity 2.0 databases and configuration across
Windows, WSL, macOS, and Linux. Supports auto-detection via Windows Registry,
filesystem candidate scanning, active process paths, and explicit CLI overrides.
"""

import os
import sys
import shutil
import time
import urllib.parse
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

def get_appdata_dir() -> Path:
    """Returns Roaming AppData on Windows, ~/Library/Application Support on macOS, or ~/.config on Linux."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata)
        return Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    else:
        config = os.environ.get("XDG_CONFIG_HOME")
        if config:
            return Path(config)
        return Path.home() / ".config"

def get_gemini_dir() -> Path:
    """Returns ~/.gemini directory."""
    return Path.home() / ".gemini"

def uri_to_path(u: str) -> str:
    """Converts file:///f:/path to Windows path F:\\path or POSIX /path."""
    if u.startswith("file:///"):
        p = u[8:]
        if len(p) >= 2 and p[1] == ':':
            return p.replace("/", "\\")
        return "/" + p
    return ""

def canonicalize_workspace_uri(u: str, base_projects_dir: Optional[Path] = None) -> str:
    """
    Normalizes and canonicalizes a workspace URI to match actual directory casing
    and naming on disk. Handles:
    - URL decoding (%3A -> :, %20 -> space)
    - Slash standardization and drive-letter casing
    - Drive remapping if path moved across drives (e.g. J: -> F:)
    - Resolving folder name variations (hyphens vs underscores, missing Projects prefix)
      against actual directories on disk.
    """
    if not u:
        return ""
    import re
    u = urllib.parse.unquote(u).replace("\\", "/")
    u = re.sub(r"/+\.?$", "", u)
    
    parsed = urllib.parse.urlparse(u)
    path_part = parsed.path
    if path_part.startswith("/") and len(path_part) > 2 and path_part[2] == ":":
        raw_fs_path = path_part[1:]
    else:
        raw_fs_path = path_part

    # Direct existence check
    p = Path(raw_fs_path) if raw_fs_path else None
    if p:
        try:
            p = p.resolve()
            raw_fs_path = p.as_posix()
        except Exception:
            pass

    if p and p.exists() and p.is_dir():
        drive = raw_fs_path[0].lower()
        if p.name.lower() == "google_antigravity_projects":
            return f"file:///{drive}:{p.parent.as_posix()[2:]}/{p.name}"
        try:
            parent = p.parent
            if parent.name.lower() == "google_antigravity_projects":
                for child in parent.iterdir():
                    if child.name.lower() == p.name.lower():
                        return f"file:///{drive}:{parent.as_posix()[2:]}/{child.name}"
        except Exception:
            pass
        rest = raw_fs_path[2:].replace("\\", "/").rstrip("/")
        return f"file:///{drive}:{rest}"

    # Drive letter re-mapping attempt
    if raw_fs_path and len(raw_fs_path) >= 2 and raw_fs_path[1] == ':':
        for cand_drive in ["f", "c", "d", "e", "g", "h", "i", "j"]:
            alt_path = Path(cand_drive + raw_fs_path[1:])
            if alt_path.exists() and alt_path.is_dir():
                return f"file:///{cand_drive}:{alt_path.as_posix()[2:]}"

    # Search against Google_Antigravity_Projects on candidate drives
    m = re.search(r"Google_Antigravity_Projects/([^/]+)", u, re.IGNORECASE)
    if m:
        target_name = m.group(1).rstrip("/")
        cand_bases = []
        if base_projects_dir and base_projects_dir.exists():
            cand_bases.append(base_projects_dir)
        for d in ["f", "c", "d", "e", "j"]:
            p1 = Path(f"{d}:/Projects/Google_Antigravity_Projects")
            p2 = Path(f"{d}:/Google_Antigravity_Projects")
            if p1.exists() and p1 not in cand_bases:
                cand_bases.append(p1)
            if p2.exists() and p2 not in cand_bases:
                cand_bases.append(p2)

        target_norm = re.sub(r"[^a-z0-9]+", "", target_name.lower())
        for b in cand_bases:
            try:
                for child in b.iterdir():
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    if child.name.lower() == target_name.lower():
                        drive = b.as_posix()[0].lower()
                        return f"file:///{drive}:{b.as_posix()[2:]}/{child.name}"
                    child_norm = re.sub(r"[^a-z0-9]+", "", child.name.lower())
                    if child_norm == target_norm:
                        drive = b.as_posix()[0].lower()
                        return f"file:///{drive}:{b.as_posix()[2:]}/{child.name}"
                if "slido" in target_norm:
                    cand = b / "Slido-Upvoter"
                    if cand.exists():
                        drive = b.as_posix()[0].lower()
                        return f"file:///{drive}:{b.as_posix()[2:]}/Slido-Upvoter"
                if "bazos" in target_norm:
                    cand = b / "Bazos-Alert-bot"
                    if cand.exists():
                        drive = b.as_posix()[0].lower()
                        return f"file:///{drive}:{b.as_posix()[2:]}/Bazos-Alert-bot"
                if "gphoto" in target_norm or "ios" in target_norm:
                    cand = b / "iOS-GPhotos-Reverse-Engineering"
                    if cand.exists():
                        drive = b.as_posix()[0].lower()
                        return f"file:///{drive}:{b.as_posix()[2:]}/iOS-GPhotos-Reverse-Engineering"
            except Exception:
                pass

    return u.rstrip("/")

def normalize_workspace_uri(u: str) -> str:
    """Normalizes a workspace URI using canonicalize_workspace_uri, lowercased."""
    return canonicalize_workspace_uri(u).lower()

def is_process_running(proc_name: str) -> bool:
    """Checks if a process name is currently running."""
    try:
        if os.name == "nt":
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}"],
                text=True,
                stderr=subprocess.DEVNULL
            )
            return proc_name.lower() in out.lower()
        else:
            out = subprocess.check_output(
                ["pgrep", "-f", proc_name],
                text=True,
                stderr=subprocess.DEVNULL
            )
            return len(out.strip()) > 0
    except Exception:
        return False

def check_active_processes() -> List[str]:
    """Returns list of running conflicting Antigravity processes."""
    procs = ["Antigravity.exe", "antigravity-manager.exe", "Antigravity IDE.exe"]
    return [p for p in procs if is_process_running(p)]

def find_registry_install_paths() -> Dict[str, Path]:
    """Inspects Windows Registry for registered Antigravity installation directories."""
    paths = {}
    if os.name != "nt":
        return paths

    try:
        import winreg
        for root in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            try:
                with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall") as uninstall_key:
                    num_subkeys = winreg.QueryInfoKey(uninstall_key)[0]
                    for i in range(num_subkeys):
                        try:
                            subkey_name = winreg.EnumKey(uninstall_key, i)
                            with winreg.OpenKey(uninstall_key, subkey_name) as subkey:
                                try:
                                    name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                                    name_lower = name.lower()
                                    if "antigravity" in name_lower:
                                        loc = ""
                                        try:
                                            loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                                        except Exception:
                                            pass
                                        if not loc:
                                            try:
                                                uninst, _ = winreg.QueryValueEx(subkey, "UninstallString")
                                                loc = os.path.dirname(uninst.strip('"'))
                                            except Exception:
                                                pass
                                        if loc and os.path.exists(loc):
                                            if "ide" in name_lower:
                                                paths["ide_install"] = Path(loc)
                                            else:
                                                paths["ag2_install"] = Path(loc)
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass

    return paths

class AntigravityPaths:
    """
    Encapsulates all path resolution for Antigravity IDE and Antigravity 2.0.
    Supports auto-detection across multi-drive / non-standard setups and CLI overrides.
    """
    def __init__(
        self,
        custom_ide_path: Optional[str] = None,
        custom_ag2_path: Optional[str] = None,
        custom_gemini_dir: Optional[str] = None,
    ):
        base_appdata = get_appdata_dir()
        base_gemini = Path(custom_gemini_dir) if custom_gemini_dir else get_gemini_dir()
        self.appdata = base_appdata
        self.gemini = base_gemini

        # 1. Antigravity IDE (1.x) AppData Resolution
        if custom_ide_path:
            self.ide_appdata = Path(custom_ide_path)
        else:
            # Candidate checks
            candidates = [
                base_appdata / "Antigravity IDE",
                base_appdata / "antigravity-ide",
            ]
            self.ide_appdata = candidates[0]
            for c in candidates:
                if c.exists() and (c / "User").exists():
                    self.ide_appdata = c
                    break

        self.ide_vscdb = self.ide_appdata / "User" / "globalStorage" / "state.vscdb"
        self.ide_workspace_storage = self.ide_appdata / "User" / "workspaceStorage"
        self.ide_gemini = base_gemini / "antigravity-ide"
        self.ide_conversations = self.ide_gemini / "conversations"
        self.ide_brain = self.ide_gemini / "brain"

        # 2. Antigravity 2.0 AppData Resolution
        if custom_ag2_path:
            self.ag2_appdata = Path(custom_ag2_path)
        else:
            # Candidate checks
            candidates = [
                base_appdata / "Antigravity",
                base_appdata / "antigravity",
            ]
            self.ag2_appdata = candidates[0]
            for c in candidates:
                if c.exists() and (c / "app_storage.json" or (c / "User").exists()):
                    self.ag2_appdata = c
                    break

        self.ag2_vscdb = self.ag2_appdata / "User" / "globalStorage" / "state.vscdb"
        self.ag2_workspace_storage = self.ag2_appdata / "User" / "workspaceStorage"
        self.ag2_app_storage = self.ag2_appdata / "app_storage.json"
        self.ag2_gemini = base_gemini / "antigravity"
        self.ag2_conversations = self.ag2_gemini / "conversations"
        self.ag2_brain = self.ag2_gemini / "brain"
        self.ag2_conv_db = self.ag2_gemini / "conversation_summaries.db"
        self.ag2_agyhub_pb = self.ag2_gemini / "agyhub_summaries_proto.pb"

    @property
    def config_projects_dir(self) -> Path:
        return self.gemini / "config" / "projects"

    def create_backups(self, dry_run: bool = False) -> List[Path]:
        """Creates timestamped backups of all authoritative database files."""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backed_up = []
        files_to_backup = [
            self.ide_vscdb,
            self.ag2_vscdb,
            self.ag2_conv_db,
            self.ag2_agyhub_pb,
            self.ag2_app_storage,
        ]
        for f in files_to_backup:
            if f.exists():
                dst = f.with_name(f"{f.name}.backup_{stamp}")
                backed_up.append(dst)
                if not dry_run:
                    shutil.copy2(f, dst)
        return backed_up

    def get_executable_path(self, target: str) -> Optional[Path]:
        """
        Locates the executable for the given environment ('ag2' or 'ide').
        Checks Registry install locations, local AppData Programs, and standard install paths.
        """
        reg_paths = find_registry_install_paths()
        if target == "ag2":
            if "ag2_install" in reg_paths:
                exe = reg_paths["ag2_install"] / "Antigravity.exe"
                if exe.exists():
                    return exe
            candidates = [
                Path.home() / "AppData" / "Local" / "Programs" / "Antigravity" / "Antigravity.exe",
                Path("C:/Program Files/Antigravity/Antigravity.exe"),
            ]
            for c in candidates:
                if c.exists():
                    return c
        elif target == "ide":
            if "ide_install" in reg_paths:
                exe = reg_paths["ide_install"] / "Antigravity IDE.exe"
                if exe.exists():
                    return exe
            candidates = [
                Path.home() / "AppData" / "Local" / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe",
                Path("C:/Program Files/Antigravity IDE/Antigravity IDE.exe"),
            ]
            for c in candidates:
                if c.exists():
                    return c
        return None

    def get_detection_summary(self) -> Dict[str, Dict[str, Any]]:
        """Returns structured detection information for both environments."""
        return {
            "ide": {
                "detected": self.ide_vscdb.exists() or self.ide_conversations.exists(),
                "appdata": str(self.ide_appdata),
                "state_vscdb": str(self.ide_vscdb) if self.ide_vscdb.exists() else "Not Found",
                "conversations_dir": str(self.ide_conversations) if self.ide_conversations.exists() else "Not Found",
            },
            "ag2": {
                "detected": self.ag2_conv_db.exists() or self.ag2_vscdb.exists(),
                "appdata": str(self.ag2_appdata),
                "conversation_summaries": str(self.ag2_conv_db) if self.ag2_conv_db.exists() else "Not Found",
                "app_storage": str(self.ag2_app_storage) if self.ag2_app_storage.exists() else "Not Found",
                "conversations_dir": str(self.ag2_conversations) if self.ag2_conversations.exists() else "Not Found",
            }
        }

