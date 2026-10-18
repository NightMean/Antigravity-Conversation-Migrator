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
import json
import sqlite3
import shutil
import base64
import uuid
import urllib.parse
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from .proto import (
    decode_proto_fields,
    encode_field,
    encode_varint,
    add_or_replace_field,
    set_cascade_metadata_project_id,
    get_cascade_metadata_project_id,
)
from .paths import AntigravityPaths, normalize_workspace_uri

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
            conn_ag2.commit()

        conn_ag2.close()

    def _promote_projects_in_ag2(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Discovers established 2.0 projects, generates deterministic UUIDs for unassigned
        workspaces, stamps Field 18 into conversation blobs, and updates projectsOrder.
        """
        conn = sqlite3.connect(self.paths.ag2_conv_db if not dry_run else f"file:{self.paths.ag2_conv_db}?mode=ro", uri=dry_run)
        cur = conn.cursor()

        cur.execute("SELECT conversation_id, title, preview, workspace_uris, project_id, raw_summary FROM conversation_summaries")
        all_convs = cur.fetchall()

        # Map established projects
        known_ws_to_pid = {}
        for cid, title, preview, ws_json, pid, raw in all_convs:
            if pid and pid != "outside-of-project":
                try:
                    uris = json.loads(ws_json) if ws_json else []
                except Exception:
                    uris = []
                for u in uris:
                    norm = normalize_workspace_uri(u)
                    if norm and norm not in known_ws_to_pid:
                        known_ws_to_pid[norm] = pid

        # Group unassigned
        unassigned_convs = {}
        for cid, title, preview, ws_json, pid, raw in all_convs:
            if not pid or pid == "outside-of-project":
                try:
                    uris = json.loads(ws_json) if ws_json else []
                except Exception:
                    uris = []
                primary_uri = uris[0] if uris else ""
                norm = normalize_workspace_uri(primary_uri)
                if norm:
                    unassigned_convs.setdefault(norm, []).append((cid, title, preview, raw))

        # Assign UUIDs
        ws_to_pid = dict(known_ws_to_pid)
        newly_promoted = {}
        for norm, conv_list in unassigned_convs.items():
            if norm not in ws_to_pid:
                assigned_pid = str(uuid.uuid5(uuid.NAMESPACE_URL, norm))
                ws_to_pid[norm] = assigned_pid
                basename = urllib.parse.unquote(norm.rstrip("/").split("/")[-1])
                newly_promoted[norm] = (assigned_pid, basename, len(conv_list))

        # Read existing projectsOrder
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
        for norm, (pid, name, cnt) in newly_promoted.items():
            if pid not in updated_order:
                if "default-cli-project" in updated_order:
                    idx = updated_order.index("default-cli-project")
                    updated_order.insert(idx, pid)
                else:
                    updated_order.append(pid)

        # Update conversations
        convs_updated = 0
        updated_summaries_proto = {}

        for cid, title, preview, ws_json, pid, raw in all_convs:
            try:
                uris = json.loads(ws_json) if ws_json else []
            except Exception:
                uris = []
            primary_uri = uris[0] if uris else ""
            norm = normalize_workspace_uri(primary_uri)

            target_pid = ws_to_pid.get(norm)
            current_proto_pid = get_cascade_metadata_project_id(raw) if raw else None
            needs_pid = bool(target_pid and (pid != target_pid or current_proto_pid != target_pid))
            needs_title = bool(not title and preview)

            if needs_pid or needs_title:
                new_pid = target_pid if target_pid else pid
                new_title = preview if needs_title else title
                new_raw = set_cascade_metadata_project_id(raw, new_pid, title=new_title) if raw else raw

                convs_updated += 1
                updated_summaries_proto[cid] = (new_pid, new_title, new_raw)

                if not dry_run:
                    cur.execute(
                        "UPDATE conversation_summaries SET project_id = ?, title = ?, raw_summary = ? WHERE conversation_id = ?",
                        (new_pid, new_title, new_raw, cid)
                    )
                    try:
                        cur.execute(
                            "UPDATE conversations_fts SET project = ?, title = ? WHERE conversation_id = ?",
                            (new_pid, new_title, cid)
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

        if not dry_run:
            conn.commit()
            conn.close()

            # Update agyhub_summaries_proto.pb
            if self.paths.ag2_agyhub_pb.exists() and updated_summaries_proto:
                try:
                    with open(self.paths.ag2_agyhub_pb, "rb") as f:
                        pb_data = f.read()
                    pb_fields = decode_proto_fields(pb_data)
                    new_pb = bytearray()
                    seen_cids = set()
                    for fn, wire, val in pb_fields:
                        if fn == 1 and isinstance(val, bytes):
                            sub = decode_proto_fields(val)
                            cid_val = None
                            for sfn, sw, sv in sub:
                                if sfn == 1 and isinstance(sv, bytes):
                                    cid_val = sv.decode("utf-8", errors="ignore")
                            if cid_val:
                                seen_cids.add(cid_val)
                            if cid_val in updated_summaries_proto:
                                _, _, sum_bytes = updated_summaries_proto[cid_val]
                                entry_b = bytearray()
                                entry_b.extend(encode_field(1, 2, cid_val.encode("utf-8")))
                                entry_b.extend(encode_field(2, 2, sum_bytes))
                                new_pb.extend(encode_field(1, 2, bytes(entry_b)))
                            else:
                                new_pb.extend(encode_field(fn, wire, val))
                        else:
                            new_pb.extend(encode_field(fn, wire, val))

                    # Append any conversations that were not already in the pb index
                    for cid_val, (_, _, sum_bytes) in updated_summaries_proto.items():
                        if cid_val not in seen_cids and sum_bytes:
                            entry_b = bytearray()
                            entry_b.extend(encode_field(1, 2, cid_val.encode("utf-8")))
                            entry_b.extend(encode_field(2, 2, sum_bytes))
                            new_pb.extend(encode_field(1, 2, bytes(entry_b)))

                    with open(self.paths.ag2_agyhub_pb, "wb") as f:
                        f.write(new_pb)
                except Exception:
                    pass

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
