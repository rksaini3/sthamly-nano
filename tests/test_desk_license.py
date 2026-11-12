"""Desk license: activation, offline grace, read-only, cache chhedchhad, ghadi, dev mode, aur key ka fixture."""
import hashlib
import json
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desk"))

import desk_license as L  # noqa: E402
from desk_errors import DeskError  # noqa: E402

CFG = {"supabase_url": "https://example.invalid", "supabase_anon_key": "test"}
KEY = "STH-ABCD-EFGH-IJKL-MNOP"
FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "license_key.json").read_text(encoding="utf-8"))


def ok_reply() -> dict:
    return {"success": True, "message": "ok", "profile": "lawyer", "label": "Sthamly Legal Desk",
            "actions": [], "disclaimer": "test"}


class LicenseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, dev: bool = False) -> L.LicenseStore:
        s = L.LicenseStore(self.home, CFG, dev=dev)
        s._kr = None  # keychain ke bina file mode me test
        return s

    def activate(self) -> None:
        with unittest.mock.patch.object(L, "rpc", return_value=ok_reply()):
            self.store().activate(KEY)

    def set_cache(self, **changes) -> None:
        s = self.store()
        data = s._load()
        data.update(changes)
        s._save(data)

    def test_activate_gives_full_access(self):
        self.activate()
        cur = self.store().current()
        self.assertIsNotNone(cur)
        self.assertEqual(cur["profile"], "lawyer")
        self.assertFalse(cur["read_only"])
        self.assertIn("Notice ka draft", cur["actions"])

    def test_refused_activation_messages(self):
        cases = [("key deactivated", L.MESSAGES["revoked"]),
                 ("device limit reached", L.MESSAGES["device_limit"]),
                 ("invalid_key", L.MESSAGES["invalid_key"])]
        for server_msg, expected in cases:
            with self.subTest(server_msg=server_msg):
                with unittest.mock.patch.object(L, "rpc", return_value={"success": False, "message": server_msg}):
                    with self.assertRaises(DeskError) as cm:
                        self.store().activate(KEY)
                self.assertEqual(cm.exception.message, expected)

    def test_offline_grace_then_read_only(self):
        self.activate()
        offline = DeskError("offline", "x", 503)
        self.set_cache(checked_at=time.time() - 31 * L.DAY, last_seen=time.time())
        with unittest.mock.patch.object(L, "rpc", side_effect=offline):
            self.assertFalse(self.store().current()["read_only"])  # 31 din: grace ke andar
        self.set_cache(checked_at=time.time() - 50 * L.DAY, last_seen=time.time())
        with unittest.mock.patch.object(L, "rpc", side_effect=offline):
            self.assertTrue(self.store().current()["read_only"])  # 50 din: grace ke baad

    def test_server_revoke_clears_cache(self):
        self.activate()
        self.set_cache(checked_at=time.time() - 31 * L.DAY, last_seen=time.time())
        with unittest.mock.patch.object(L, "rpc", return_value={"success": False, "message": "key deactivated"}):
            self.assertIsNone(self.store().current())

    def test_tampered_cache_is_rejected(self):
        self.activate()
        path = self.home / "license.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["profile"] = "general"
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertIsNone(self.store().current())

    def test_clock_rollback_is_rejected(self):
        self.activate()
        self.set_cache(last_seen=time.time() + 2 * 3600)
        self.assertIsNone(self.store().current())

    def test_dev_mode_needs_no_key(self):
        cur = self.store(dev=True).current()
        self.assertEqual(cur["profile"], "general")
        self.assertFalse(cur["read_only"])

    def test_key_fixture_matches_server_rule(self):
        self.assertEqual(L.normalize_key("sth abcd-efgh ijkl mnop"), FIXTURE["normalized"])
        self.assertEqual(hashlib.sha256(FIXTURE["normalized"].encode("utf-8")).hexdigest(), FIXTURE["hash"])


if __name__ == "__main__":
    unittest.main()
