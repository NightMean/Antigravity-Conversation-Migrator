import unittest
import tempfile
import sqlite3
import json
import base64
from pathlib import Path
from antigravity_conversation_migrator.paths import AntigravityPaths
from antigravity_conversation_migrator.migrator import Migrator
from antigravity_conversation_migrator.proto import encode_field

class TestMigrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

        # Mock directory structure
        self.paths = AntigravityPaths()
        self.paths.appdata = self.tmp_path / "AppData"
        self.paths.gemini = self.tmp_path / ".gemini"

        self.paths.ide_appdata = self.paths.appdata / "Antigravity IDE"
        self.paths.ide_vscdb = self.paths.ide_appdata / "User" / "globalStorage" / "state.vscdb"
        self.paths.ide_workspace_storage = self.paths.ide_appdata / "User" / "workspaceStorage"
        self.paths.ide_gemini = self.paths.gemini / "antigravity-ide"
        self.paths.ide_conversations = self.paths.ide_gemini / "conversations"
        self.paths.ide_brain = self.paths.ide_gemini / "brain"

        self.paths.ag2_appdata = self.paths.appdata / "Antigravity"
        self.paths.ag2_vscdb = self.paths.ag2_appdata / "User" / "globalStorage" / "state.vscdb"
        self.paths.ag2_workspace_storage = self.paths.ag2_appdata / "User" / "workspaceStorage"
        self.paths.ag2_app_storage = self.paths.ag2_appdata / "app_storage.json"
        self.paths.ag2_gemini = self.paths.gemini / "antigravity"
        self.paths.ag2_conversations = self.paths.ag2_gemini / "conversations"
        self.paths.ag2_brain = self.paths.ag2_gemini / "brain"
        self.paths.ag2_conv_db = self.paths.ag2_gemini / "conversation_summaries.db"
        self.paths.ag2_agyhub_pb = self.paths.ag2_gemini / "agyhub_summaries_proto.pb"

        # Create directories
        for d in [
            self.paths.ide_vscdb.parent, self.paths.ide_workspace_storage, self.paths.ide_conversations, self.paths.ide_brain,
            self.paths.ag2_vscdb.parent, self.paths.ag2_workspace_storage, self.paths.ag2_conversations, self.paths.ag2_brain,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        # Init mock 2.0 conversation_summaries.db
        conn = sqlite3.connect(self.paths.ag2_conv_db)
        conn.execute("""
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            preview TEXT,
            step_count INTEGER,
            last_modified_time TEXT,
            workspace_uris TEXT,
            status TEXT,
            source TEXT,
            project_id TEXT,
            agent_name TEXT,
            parent_conversation_id TEXT,
            nesting_depth INTEGER,
            battle_id TEXT,
            winning_conversation_id TEXT,
            not_fully_idle INTEGER,
            killed INTEGER,
            last_user_input_time TEXT,
            last_user_input_step_index INTEGER,
            app_data_dir TEXT,
            raw_summary BLOB,
            group_id TEXT
        )
        """)
        # Insert one conversation with empty project_id
        dummy_raw = encode_field(1, 2, "Test Chat Title")
        conn.execute(
            "INSERT INTO conversation_summaries (conversation_id, title, preview, workspace_uris, project_id, raw_summary) VALUES (?, ?, ?, ?, ?, ?)",
            ("conv-1234", "", "Test Preview", json.dumps(["file:///f:/Projects/Test-Project"]), "", dummy_raw)
        )
        conn.commit()
        conn.close()

        # Init mock app_storage.json
        with open(self.paths.ag2_app_storage, "w", encoding="utf-8") as f:
            json.dump({"projectsOrder": json.dumps(["default-cli-project"])}, f)

        # Init mock state.vscdb
        conn2 = sqlite3.connect(self.paths.ag2_vscdb)
        conn2.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn2.commit()
        conn2.close()

        conn3 = sqlite3.connect(self.paths.ide_vscdb)
        conn3.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn3.commit()
        conn3.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_ide_to_2_promotion(self):
        migrator = Migrator(paths=self.paths)
        res = migrator.migrate_ide_to_2(dry_run=False)
        self.assertEqual(res["new_projects_promoted"], 1)
        self.assertEqual(res["conversations_assigned"], 1)

        # Check DB was updated
        conn = sqlite3.connect(self.paths.ag2_conv_db)
        cur = conn.cursor()
        cur.execute("SELECT project_id, title FROM conversation_summaries WHERE conversation_id = 'conv-1234'")
        pid, title = cur.fetchone()
        conn.close()

        self.assertTrue(len(pid) > 0)
        self.assertNotEqual(pid, "outside-of-project")
        self.assertEqual(title, "Test Preview")

        # Check app_storage projectsOrder
        with open(self.paths.ag2_app_storage, "r", encoding="utf-8") as f:
            d = json.load(f)
            order = json.loads(d["projectsOrder"])
            self.assertIn(pid, order)

    def test_migrate_ide_to_2_chats_only(self):
        migrator = Migrator(paths=self.paths)
        res = migrator.migrate_ide_to_2(dry_run=False, chats_only=True)
        self.assertEqual(res["new_projects_promoted"], 0)
        self.assertEqual(res["conversations_assigned"], 0)

        # Check DB was NOT updated with new project_id
        conn = sqlite3.connect(self.paths.ag2_conv_db)
        cur = conn.cursor()
        cur.execute("SELECT project_id FROM conversation_summaries WHERE conversation_id = 'conv-1234'")
        pid = cur.fetchone()[0]
        conn.close()

        self.assertEqual(pid, "")

        # Check app_storage projectsOrder was NOT modified
        with open(self.paths.ag2_app_storage, "r", encoding="utf-8") as f:
            d = json.load(f)
            order = json.loads(d["projectsOrder"])
            self.assertEqual(order, ["default-cli-project"])

if __name__ == "__main__":
    unittest.main()

