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

def normalize_workspace_uri(u: str) -> str:
    """
    Normalizes a workspace URI:
    - Decodes URL encoding (%3A, %20, etc.)
    - Reconciles slashes and casing
    - If path does not exist on disk, attempts drive re-mapping (e.g. J: -> F:)
    """
    if not u:
        return ""
    u = urllib.parse.unquote(u)
    u = u.replace("\\", "/").rstrip("/").lower()

    # Drive letter reconciliation if path moved
    p = uri_to_path(u)
    if p and not os.path.exists(p) and len(p) >= 2 and p[1] == ':':
        for candidate_drive in ["f", "c", "d", "e", "g", "h", "i", "j"]:
            alt_path = candidate_drive + p[1:]
            if os.path.exists(alt_path):
                return "file:///" + alt_path.replace("\\", "/").lower()

    return u

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
