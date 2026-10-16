import unittest
from antigravity_conversation_migrator.paths import (
    AntigravityPaths,
    uri_to_path,
    normalize_workspace_uri,
)

class TestPaths(unittest.TestCase):
    def test_uri_to_path_windows(self):
        uri = "file:///f:/Projects/Google_Antigravity_Projects"
        path = uri_to_path(uri)
        self.assertEqual(path.lower(), "f:\\projects\\google_antigravity_projects".lower())

    def test_normalize_workspace_uri_unquote(self):
        uri = "file:///f%3A/Projects/Google_Antigravity_Projects/Gotohp-Mobile-Uploader"
        norm = normalize_workspace_uri(uri)
        self.assertEqual(norm, "file:///f:/projects/google_antigravity_projects/gotohp-mobile-uploader")

    def test_normalize_workspace_uri_trailing_slash(self):
        uri = "file:///f:/Projects/Google_Antigravity_Projects/"
        norm = normalize_workspace_uri(uri)
        self.assertEqual(norm, "file:///f:/projects/google_antigravity_projects")

    def test_paths_detection_summary(self):
        paths = AntigravityPaths()
        summary = paths.get_detection_summary()
        self.assertIn("ide", summary)
        self.assertIn("ag2", summary)
        self.assertIn("detected", summary["ide"])
        self.assertIn("detected", summary["ag2"])

if __name__ == "__main__":
    unittest.main()

