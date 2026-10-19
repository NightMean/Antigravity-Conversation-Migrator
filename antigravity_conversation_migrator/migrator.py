"""
Core Migration Logic: Antigravity IDE <-> Antigravity 2.0
=========================================================
Performs bidirectional conversation and project synchronization:
- File-level session migration (conversations/*.db, brain transcripts/artifacts)
- WorkspaceStorage cache migration
- Database indexing: state.vscdb (Protobuf map & recentlyOpenedPathsList)
- Antigravity 2.0 Project Entity promotion (conversation_summaries.db, Field 18,
  agyhub_summaries_proto.pb, and app_storage.json projectsOrder)
"""

import os
import sys
import re
import json
import sqlite3
import shutil
import base64
import uuid
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from .proto import (
    decode_proto_fields,
    encode_field,
    encode_varint,
    add_or_replace_field,
    set_cascade_metadata_project_id,
    get_cascade_metadata_project_id,
    get_cascade_metadata_conversation_id,
    get_cascade_trajectory_workspace_uri,
)
from .paths import AntigravityPaths, normalize_workspace_uri, canonicalize_workspace_uri

def copy_directory_files(src_dir: Path, dst_dir: Path, is_dir: bool = False, dry_run: bool = False) -> int:
    """Copies missing files or subdirectories from src_dir to dst_dir."""
    if not src_dir.exists():
        return 0
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
    
    copied = 0
    for item in src_dir.iterdir():
        target = dst_dir / item.name
        if not target.exists():
            copied += 1
            if not dry_run:
                if is_dir and item.is_dir():
                    shutil.copytree(item, target)
                elif item.is_file():
                    shutil.copy2(item, target)
    return copied

class Migrator:
    def __init__(self, paths: Optional[AntigravityPaths] = None):
        self.paths = paths or AntigravityPaths()

    def get_status_report(self) -> Dict[str, Any]:
        """Inspects and returns conversation and project counts across both environments."""
        report = {
            "ide_exists": self.paths.ide_vscdb.exists(),
            "ag2_exists": self.paths.ag2_conv_db.exists() or self.paths.ag2_vscdb.exists(),
            "ide_conv_files": len(list(self.paths.ide_conversations.glob("*.db"))) if self.paths.ide_conversations.exists() else 0,
            "ag2_conv_files": len(list(self.paths.ag2_conversations.glob("*.db"))) if self.paths.ag2_conversations.exists() else 0,
            "ide_indexed_convs": 0,
            "ag2_indexed_convs": 0,
            "ag2_projects_count": 0,
            "ag2_unassigned_convs": 0,
        }

        # IDE index check
        if self.paths.ide_vscdb.exists():
            try:
                conn = sqlite3.connect(f"file:{self.paths.ide_vscdb}?mode=ro", uri=True)
                cur = conn.cursor()
                row = cur.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.trajectorySummaries'").fetchone()
                if row and row[0]:
                    raw = base64.b64decode(row[0])
                    fields = decode_proto_fields(raw)
                    report["ide_indexed_convs"] = len(fields)
                conn.close()
            except Exception:
                pass

        # 2.0 DB check
        if self.paths.ag2_conv_db.exists():
            try:
                conn = sqlite3.connect(f"file:{self.paths.ag2_conv_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM conversation_summaries")
                report["ag2_indexed_convs"] = cur.fetchone()[0]
                cur.execute("SELECT count(DISTINCT project_id) FROM conversation_summaries WHERE project_id != '' AND project_id != 'outside-of-project'")
                report["ag2_projects_count"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM conversation_summaries WHERE project_id = '' OR project_id IS NULL OR project_id = 'outside-of-project'")
                report["ag2_unassigned_convs"] = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass

        return report

    def migrate_ide_to_2(self, dry_run: bool = False, chats_only: bool = False) -> Dict[str, Any]:
        """
        Migrates conversations and workspaces from Antigravity IDE (1.x) to Antigravity 2.0.
        When chats_only is True, skips promoting workspaces to First-Class 2.0 Project entities.
        """
        results = {
            "direction": "IDE -> 2.0",
            "copied_conv_files": 0,
            "copied_brain_dirs": 0,
            "copied_workspace_storage": 0,
            "new_projects_promoted": 0,
            "conversations_assigned": 0,
            "total_sidebar_projects": 0,
        }

        # 1. Copy raw conversation .db files & brain directories
        results["copied_conv_files"] = copy_directory_files(
            self.paths.ide_conversations, self.paths.ag2_conversations, is_dir=False, dry_run=dry_run
        )
        results["copied_brain_dirs"] = copy_directory_files(
            self.paths.ide_brain, self.paths.ag2_brain, is_dir=True, dry_run=dry_run
        )
        results["copied_workspace_storage"] = copy_directory_files(
            self.paths.ide_workspace_storage, self.paths.ag2_workspace_storage, is_dir=True, dry_run=dry_run
        )

        # 2. Merge state.vscdb entries
        if self.paths.ide_vscdb.exists() and self.paths.ag2_vscdb.exists():
            self._merge_vscdb_ide_to_2(dry_run=dry_run)

        # 3. Synchronize conversation_summaries.db & Project Entities (if not chats_only)
        if not chats_only and self.paths.ag2_conv_db.exists():
            project_stats = self._promote_projects_in_ag2(dry_run=dry_run)
            results["new_projects_promoted"] = project_stats["new_projects"]
            results["conversations_assigned"] = project_stats["convs_updated"]
            results["total_sidebar_projects"] = project_stats["total_sidebar_projects"]
        elif chats_only and self.paths.ag2_app_storage.exists():
            try:
                with open(self.paths.ag2_app_storage, "r", encoding="utf-8") as f:
                    st_data = json.load(f)
                    raw_ord = st_data.get("projectsOrder", "[]")
                    order = json.loads(raw_ord) if isinstance(raw_ord, str) else raw_ord
                    results["total_sidebar_projects"] = len(order)
            except Exception:
                pass

        return results

    def _merge_vscdb_ide_to_2(self, dry_run: bool = False):
        """Merges trajectorySummaries, sidebarWorkspaces, and recentlyOpenedPathsList into 2.0 state.vscdb."""
        conn_ide = sqlite3.connect(f"file:{self.paths.ide_vscdb}?mode=ro", uri=True)
        conn_ag2 = sqlite3.connect(self.paths.ag2_vscdb if not dry_run else f"file:{self.paths.ag2_vscdb}?mode=ro", uri=dry_run)

        # A. history.recentlyOpenedPathsList (JSON merge)
        row_ide_rec = conn_ide.execute("SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList'").fetchone()
        row_ag2_rec = conn_ag2.execute("SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList'").fetchone()

        recent_entries = []
        seen_uris = set()
        for row in [row_ag2_rec, row_ide_rec]:
            if row and row[0]:
                try:
                    d = json.loads(row[0])
                    for item in d.get("entries", []):
                        u = item.get("folderUri") or item.get("workspace") or item.get("fileUri")
                        if u and u not in seen_uris:
                            seen_uris.add(u)
                            recent_entries.append(item)
                except Exception:
                    pass

        # B. sidebarWorkspaces (Protobuf merge)
        row_ide_ws = conn_ide.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.sidebarWorkspaces'").fetchone()
        row_ag2_ws = conn_ag2.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.sidebarWorkspaces'").fetchone()

        projects_by_uri = {}
        for row in [row_ag2_ws, row_ide_ws]:
            if row and row[0]:
                try:
                    raw_bytes = base64.b64decode(row[0])
                    fields = decode_proto_fields(raw_bytes)
                    for fn, wire, val in fields:
                        if fn == 1 and isinstance(val, bytes):
                            sub = decode_proto_fields(val)
                            for sfn, swire, sval in sub:
                                if sfn == 1 and isinstance(sval, bytes):
                                    u = sval.decode('utf-8', errors='ignore')
                                    projects_by_uri[u] = val
                                    break
                except Exception:
                    pass

        merged_ws_bytes = bytearray()
        for u, val in projects_by_uri.items():
            merged_ws_bytes.extend(encode_field(1, 2, val))
        new_ws_b64 = base64.b64encode(merged_ws_bytes).decode('utf-8')

        conn_ide.close()

        if not dry_run:
            conn_ag2.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES ('history.recentlyOpenedPathsList', ?)",
                (json.dumps({"entries": recent_entries}),)
            )
            conn_ag2.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES ('antigravityUnifiedStateSync.sidebarWorkspaces', ?)",
                (new_ws_b64,)
            )
            # Ensure Antigravity 2.0 does not read stale trajectorySummaries protobuf from state.vscdb
            conn_ag2.execute(
                "DELETE FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.trajectorySummaries'"
            )
            conn_ag2.commit()

        conn_ag2.close()

    @staticmethod
    def _score_project_match(candidate_name: str, text: str) -> int:
        """Scores relevance between candidate project folder name and conversation text."""
        c_clean = re.sub(r"[^a-z0-9]+", "", candidate_name.lower())
        t_clean = re.sub(r"[^a-z0-9]+", "", text.lower())
        if not c_clean or not t_clean:
            return 0
        if c_clean in t_clean:
            return 100
        tokens = [t for t in re.split(r"[^a-z0-9]+", candidate_name.lower()) if len(t) >= 4]
        matched = [t for t in tokens if t in t_clean]
        if matched:
            return 30 * len(matched)
        return 0

    def _detect_conversation_workspace(self, cid: str, title: str, preview: str, uris: List[str]) -> str:
        """
        Infers the canonical project workspace URI for a conversation:
        - Multi-URI: Disambiguates by scoring candidate folder names against title/preview.
        - Single URI: If generic root, refines to specific subproject via title/links/transcript.
        - Empty URI: Scans title markdown links, transcript active documents, and known project folders.
        """
        combined_text = f"{title} {preview}"

        # Collect candidate child directories under Google_Antigravity_Projects
        cand_bases = [Path(r"F:\Projects\Google_Antigravity_Projects"), Path(r"C:\Projects\Google_Antigravity_Projects")]
        existing_subdirs = {}
        for cb in cand_bases:
            if cb.exists():
                for child in cb.iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        existing_subdirs[child.name.lower()] = child

        # 1. Multi-URI: Disambiguate by matching against title/preview
        if len(uris) > 1:
            best_uri = canonicalize_workspace_uri(uris[0])
            best_score = -1
            for u in uris:
                canon = canonicalize_workspace_uri(u)
                base = canon.split("/")[-1]
                score = self._score_project_match(base, combined_text)
                if score > best_score:
                    best_score = score
                    best_uri = canon
            return best_uri

        # 2. Single URI: Check if it's already a specific subproject
        if len(uris) == 1:
            raw_u = re.sub(r"/+\.?$", "", uris[0])
            canon = canonicalize_workspace_uri(raw_u)
            slido_check = "slido" in re.sub(r"[^a-z0-9]+", "", combined_text.lower())
            if slido_check and "slido" not in canon.lower():
                return "file:///f:/Projects/Google_Antigravity_Projects/Slido-Upvoter"
            clean = canon.rstrip("/").lower()
            if not (clean.endswith("google_antigravity_projects") or clean.endswith("projects")):
                sub_part = canon.rstrip("/").split("/")[-1].lower()
                if sub_part in existing_subdirs:
                    return canonicalize_workspace_uri(existing_subdirs[sub_part].as_uri())
                elif "google_antigravity_projects" in clean:
                    # Non-existent or truncated subfolder (e.g. /r, /n, /iO, /.), fall through to re-detection
                    pass
                else:
                    return canon

        # 3. Check title / preview for explicit markdown file links
        m_links = re.findall(r"file:///([^\s\)\"'>]+)", combined_text)
        for flink in m_links:
            canon = canonicalize_workspace_uri("file:///" + flink)
            clean = canon.rstrip("/").lower()
            if canon and not (clean.endswith("google_antigravity_projects") or clean.endswith("projects")):
                sub_part = canon.rstrip("/").split("/")[-1].lower()
                if sub_part in existing_subdirs:
                    return canonicalize_workspace_uri(existing_subdirs[sub_part].as_uri())
                elif "google_antigravity_projects" not in clean:
                    return canon

        # Conversations created outside of project without workspaces or explicit file links stay outside-of-project
        if not uris:
            return ""

        # 4. Check transcript for files accessed in subfolders
        tpath = self.paths.ag2_brain / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if not tpath.exists():
            tpath = self.paths.ide_brain / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if tpath.exists():
            try:
                with open(tpath, "r", encoding="utf-8") as f:
                    for i in range(60):
                        line = f.readline()
                        if not line:
                            break
                        try:
                            step_data = json.loads(line)
                        except Exception:
                            continue

                        # Check user prompt mentions (e.g. @[path])
                        content = step_data.get("content", "")
                        if "@[" in content:
                            at_matches = re.findall(r"@\[([^\]]+)\]", content)
                            for at_p in at_matches:
                                at_canon = canonicalize_workspace_uri(at_p)
                                at_sub = at_canon.rstrip("/").split("/")[-1].lower()
                                if at_sub in existing_subdirs:
                                    return canonicalize_workspace_uri(existing_subdirs[at_sub].as_uri())

                        # Check tool calls
                        for tc in step_data.get("tool_calls", []):
                            args = tc.get("args", {})
                            for arg_key in ["Cwd", "AbsolutePath", "TargetFile", "DirectoryPath"]:
                                val = args.get(arg_key, "")
                                if isinstance(val, str) and "Google_Antigravity_Projects" in val:
                                    val_canon = canonicalize_workspace_uri(val)
                                    val_clean = val_canon.rstrip("/").lower()
                                    for sub_name, sub_path in existing_subdirs.items():
                                        if f"google_antigravity_projects/{sub_name}" in val_clean:
                                            return canonicalize_workspace_uri(sub_path.as_uri())
            except Exception:
                pass

        # 5. Check keywords in title against project folders ONLY if conversation has an existing workspace URI
        if uris:
            best_folder = None
            best_folder_score = 0
            for sub_name, sub_path in existing_subdirs.items():
                score = self._score_project_match(sub_path.name, combined_text)
                if score > best_folder_score and score >= 60:
                    best_folder_score = score
                    best_folder = sub_path
            if best_folder:
                return canonicalize_workspace_uri(best_folder.as_uri())

        # 6. Fallback to existing single URI if available (normalized)
        if len(uris) == 1:
            raw_u = re.sub(r"/+\.?$", "", uris[0])
            canon = canonicalize_workspace_uri(raw_u)
            sub_part = canon.rstrip("/").split("/")[-1].lower()
            if sub_part in existing_subdirs:
                return canonicalize_workspace_uri(existing_subdirs[sub_part].as_uri())
            if "google_antigravity_projects" in canon.lower():
                return "file:///f:/Projects/Google_Antigravity_Projects"
            return canon

        return ""

    def _promote_projects_in_ag2(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Discovers established 2.0 projects, canonicalizes workspaces, generates deterministic
        UUIDs for unassigned workspaces, stamps Field 18 into conversation blobs, and updates projectsOrder.
        """
        conn = sqlite3.connect(self.paths.ag2_conv_db if not dry_run else f"file:{self.paths.ag2_conv_db}?mode=ro", uri=dry_run)
        cur = conn.cursor()

        cur.execute("SELECT conversation_id, title, preview, workspace_uris, project_id, raw_summary FROM conversation_summaries")
        all_convs = cur.fetchall()

        # 1. Detect canonical workspaces for all conversations
        detected_ws_map = {}
        for cid, title, preview, ws_json, pid, raw in all_convs:
            try:
                uris = json.loads(ws_json) if ws_json else []
            except Exception:
                uris = []
            detected_ws_map[cid] = self._detect_conversation_workspace(cid, title, preview, uris)

        # 2. Establish project IDs (map each canonical workspace to a unique project ID)
        ws_to_pid = {}
        claimed_pids = set()

        # B. Preserve established native 2.0 projects from database without collision
        for cid, title, preview, ws_json, pid, raw in all_convs:
            if pid and pid != "outside-of-project":
                ws = detected_ws_map[cid]
                if ws and pid not in claimed_pids and ws not in ws_to_pid:
                    ws_to_pid[ws] = pid
                    claimed_pids.add(pid)

        # C. Assign deterministic UUID5 for any workspaces without an established project ID
        newly_promoted = {}
        for cid, ws in detected_ws_map.items():
            if ws and ws not in ws_to_pid:
                assigned_pid = str(uuid.uuid5(uuid.NAMESPACE_URL, ws))
                ws_to_pid[ws] = assigned_pid
                claimed_pids.add(assigned_pid)
                basename = urllib.parse.unquote(ws.rstrip("/").split("/")[-1])
                newly_promoted[ws] = (assigned_pid, basename)

        # 3. Read existing projectsOrder
        current_order = []
        if self.paths.ag2_app_storage.exists():
            try:
                with open(self.paths.ag2_app_storage, "r", encoding="utf-8") as f:
                    st_data = json.load(f)
                    raw_ord = st_data.get("projectsOrder", "[]")
                    current_order = json.loads(raw_ord) if isinstance(raw_ord, str) else raw_ord
            except Exception:
                pass

        updated_order = list(current_order)
        # Ensure all active project IDs exist in projectsOrder
        all_active_pids = set(ws_to_pid.values())
        for pid_val in all_active_pids:
            if pid_val and pid_val != "outside-of-project" and pid_val not in updated_order:
                updated_order.append(pid_val)

        # Count conversations per project to prune any empty ghost projects
        conv_counts = defaultdict(int)
        for cid, ws in detected_ws_map.items():
            p = ws_to_pid.get(ws)
            if p and p != "outside-of-project":
                conv_counts[p] += 1

        updated_order = [p for p in updated_order if conv_counts.get(p, 0) > 0]

        # 4. Update conversations
        convs_updated = 0
        updated_summaries_proto = {}

        for cid, title, preview, ws_json, pid, raw in all_convs:
            try:
                uris = json.loads(ws_json) if ws_json else []
            except Exception:
                uris = []

            canonical_ws = detected_ws_map[cid]
            target_pid = ws_to_pid.get(canonical_ws, "outside-of-project") if canonical_ws else "outside-of-project"
            new_ws_json = json.dumps([canonical_ws]) if canonical_ws else ""

            current_proto_pid = get_cascade_metadata_project_id(raw) if raw else None
            current_proto_cid = get_cascade_metadata_conversation_id(raw) if raw else None
            current_proto_ws = get_cascade_trajectory_workspace_uri(raw) if raw else None

            new_pid = target_pid
            new_title = preview if (not title and preview) else title
            expected_raw = set_cascade_metadata_project_id(
                raw,
                new_pid,
                title=new_title,
                conversation_id=cid,
                workspace_uri=canonical_ws
            ) if raw else raw

            needs_pid = bool(pid != target_pid or current_proto_pid != target_pid)
            needs_ws = bool(ws_json != new_ws_json or (canonical_ws and current_proto_ws != canonical_ws))
            needs_cid = bool(not current_proto_cid)
            needs_title = bool(not title and preview)
            needs_raw = bool(raw and expected_raw != raw)

            if needs_pid or needs_ws or needs_cid or needs_title or needs_raw:
                new_raw = expected_raw

                convs_updated += 1
                updated_summaries_proto[cid] = (new_pid, new_title, new_raw)

                if not dry_run:
                    cur.execute(
                        "UPDATE conversation_summaries SET project_id = ?, workspace_uris = ?, title = ?, raw_summary = ? WHERE conversation_id = ?",
                        (new_pid, new_ws_json, new_title, new_raw, cid)
                    )
                    try:
                        cur.execute(
                            "UPDATE conversations_fts SET project = ?, workspace = ?, title = ? WHERE conversation_id = ?",
                            (new_pid, canonical_ws, new_title, cid)
                        )
                    except Exception:
                        pass

                    # Update individual conversation file trajectory_metadata_blob
                    c_file = self.paths.ag2_conversations / f"{cid}.db"
                    if c_file.exists() and new_pid and new_pid != "outside-of-project":
                        try:
                            cdb = sqlite3.connect(c_file)
                            ccur = cdb.cursor()
                            ccur.execute("SELECT data FROM trajectory_metadata_blob WHERE id = 'main'")
                            row_blob = ccur.fetchone()
                            if row_blob and row_blob[0]:
                                updated_blob = add_or_replace_field(row_blob[0], 18, 2, new_pid)
                                ccur.execute("UPDATE trajectory_metadata_blob SET data = ? WHERE id = 'main'", (updated_blob,))
                                cdb.commit()
                            cdb.close()
                        except Exception:
                            pass
            elif raw:
                # Keep in memory for full agyhub_pb synchronization
                updated_summaries_proto[cid] = (pid, title, raw)

        if not dry_run:
            conn.commit()

            # Update agyhub_summaries_proto.pb: sync all active conversation summaries directly from conversation_summaries
            if self.paths.ag2_agyhub_pb.exists():
                try:
                    cur.execute("SELECT conversation_id, raw_summary FROM conversation_summaries WHERE raw_summary IS NOT NULL")
                    all_db_summaries = cur.fetchall()
                    new_pb = bytearray()
                    for cid_val, raw_bytes in all_db_summaries:
                        if raw_bytes:
                            entry_b = bytearray()
                            entry_b.extend(encode_field(1, 2, cid_val.encode("utf-8")))
                            entry_b.extend(encode_field(2, 2, raw_bytes))
                            new_pb.extend(encode_field(1, 2, bytes(entry_b)))

                    with open(self.paths.ag2_agyhub_pb, "wb") as f:
                        f.write(new_pb)
                except Exception:
                    pass

            conn.close()

            # Update app_storage.json projectsOrder
            if self.paths.ag2_app_storage.exists():
                try:
                    with open(self.paths.ag2_app_storage, "r", encoding="utf-8") as f:
                        st_data = json.load(f)
                    st_data["projectsOrder"] = json.dumps(updated_order)
                    with open(self.paths.ag2_app_storage, "w", encoding="utf-8") as f:
                        json.dump(st_data, f, indent=2)
                except Exception:
                    pass
        else:
            conn.close()

        return {
            "new_projects": len(newly_promoted),
            "convs_updated": convs_updated,
            "total_sidebar_projects": len(updated_order),
        }

    def migrate_2_to_ide(self, dry_run: bool = False, chats_only: bool = False) -> Dict[str, Any]:
        """
        Migrates conversations and workspace history from Antigravity 2.0 back to Antigravity IDE (1.x).
        """
        results = {
            "direction": "2.0 -> IDE",
            "copied_conv_files": 0,
            "copied_brain_dirs": 0,
            "copied_workspace_storage": 0,
            "indexed_in_ide": 0,
        }

        # 1. Copy raw files from 2.0 back to IDE
        results["copied_conv_files"] = copy_directory_files(
            self.paths.ag2_conversations, self.paths.ide_conversations, is_dir=False, dry_run=dry_run
        )
        results["copied_brain_dirs"] = copy_directory_files(
            self.paths.ag2_brain, self.paths.ide_brain, is_dir=True, dry_run=dry_run
        )
        results["copied_workspace_storage"] = copy_directory_files(
            self.paths.ag2_workspace_storage, self.paths.ide_workspace_storage, is_dir=True, dry_run=dry_run
        )

        # 2. Synchronize 2.0 state into IDE state.vscdb
        if self.paths.ag2_conv_db.exists() and self.paths.ide_vscdb.exists():
            indexed = self._sync_ag2_db_into_ide_vscdb(dry_run=dry_run)
            results["indexed_in_ide"] = indexed

        return results

    def _sync_ag2_db_into_ide_vscdb(self, dry_run: bool = False) -> int:
        """Reads conversations from 2.0 database and merges them into IDE state.vscdb."""
        conn_ag2 = sqlite3.connect(f"file:{self.paths.ag2_conv_db}?mode=ro", uri=True)
        cur_ag2 = conn_ag2.cursor()
        cur_ag2.execute("SELECT conversation_id, raw_summary FROM conversation_summaries WHERE raw_summary IS NOT NULL")
        ag2_convs = dict(cur_ag2.fetchall())
        conn_ag2.close()

        conn_ide = sqlite3.connect(self.paths.ide_vscdb if not dry_run else f"file:{self.paths.ide_vscdb}?mode=ro", uri=dry_run)
        row = conn_ide.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.trajectorySummaries'").fetchone()

        ide_entries = {}
        if row and row[0]:
            try:
                raw_bytes = base64.b64decode(row[0])
                fields = decode_proto_fields(raw_bytes)
                for fn, wire, val in fields:
                    if fn == 1 and isinstance(val, bytes):
                        sub = decode_proto_fields(val)
                        cid = None
                        for sfn, sw, sv in sub:
                            if sfn == 1 and isinstance(sv, bytes):
                                cid = sv.decode('utf-8', errors='ignore')
                                break
                        if cid:
                            ide_entries[cid] = val
            except Exception:
                pass

        new_count = 0
        for cid, raw_sum in ag2_convs.items():
            if cid not in ide_entries and raw_sum:
                new_count += 1
                entry = bytearray()
                entry.extend(encode_field(1, 2, cid.encode('utf-8')))
                entry.extend(encode_field(2, 2, raw_sum))
                ide_entries[cid] = bytes(entry)

        if not dry_run and new_count > 0:
            merged_buf = bytearray()
            for cid, val in ide_entries.items():
                merged_buf.extend(encode_field(1, 2, val))
            new_b64 = base64.b64encode(merged_buf).decode('utf-8')
            conn_ide.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES ('antigravityUnifiedStateSync.trajectorySummaries', ?)",
                (new_b64,)
            )
            conn_ide.commit()

        conn_ide.close()
        return new_count
