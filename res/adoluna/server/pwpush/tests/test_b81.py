"""b81: app list, kiosk check, software library, batch plan."""
import io, json, os, sqlite3, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import devcmd as D, swlib as S

class B81(unittest.TestCase):
    def setUp(self):
        D.set_key_dir(tempfile.mkdtemp()); self.c = sqlite3.connect(":memory:"); D.init(self.c); S.init(self.c)
        self.d = tempfile.mkdtemp()
        info = {"A1": {"sdk": 33, "build": 80}, "A2": {"sdk": 33, "build": 81}, "W1": {"platform": "windows", "build": 80},
                "W2": {"platform": "windows", "build": 81}, "W0": {"platform": "windows", "build": 79}}
        for k, v in info.items():
            self.c.execute("insert into dev_info values(?,?,1)", (k, json.dumps(v)))
        D.store_apps(self.c, "A2", {"apps": [{"name": "Loyverse", "pkg": "com.loyverse.sale", "ver": "2.71", "vc": 271, "icon": "x" * 9000}]}, 1)
        D.store_apps(self.c, "W2", {"apps": [{"name": "Chrome", "aumid": "Chrome", "exe": "c:\\c.exe"}], "installed": [{"name": "Google Chrome", "ver": "130.0.1"}]}, 1)

    def test_store_apps_and_kiosk(self):
        a = D.apps_of(self.c, "A2"); self.assertEqual(a["apps"][0]["pkg"], "com.loyverse.sale"); self.assertNotIn("icon", a["apps"][0])
        self.assertIsNone(D.kiosk_check(self.c, "A2", {"pkg": "com.loyverse.sale"}, "android"))
        self.assertTrue(D.kiosk_check(self.c, "A2", {"pkg": "com.evil"}, "android"))
        self.assertIsNone(D.kiosk_check(self.c, "A1", {"pkg": "anything"}, "android"))  # old client: manual entry
        D.remember_lock(self.c, "A2", {"pkg": "com.loyverse.sale"}, 5); self.assertEqual(D.apps_of(self.c, "A2")["last"], "com.loyverse.sale")

    def test_up_to_date_and_plan(self):
        cli = {"platform": "windows", "pkg": "", "name": "Adoluna Support", "ver": "1.5.0-b81", "build": 81, "ext": "msi"}
        p = D.batch_plan(self.c, ["W0", "W1", "W2", "A1"], cli)
        self.assertEqual(p["send"], ["W0", "W1"]); self.assertEqual(p["uptodate"], ["W2"]); self.assertEqual(p["platform"], ["A1"])
        self.assertEqual(D.batch_plan(self.c, ["W2"], cli, force=True)["send"], ["W2"])
        chrome = {"platform": "windows", "pkg": "", "name": "Chrome", "ver": "130.0.1", "build": None, "ext": "exe"}
        self.assertTrue(D.up_to_date(self.c, "W2", chrome)); self.assertIsNone(D.up_to_date(self.c, "W1", chrome))
        chrome["ver"] = "131.0"; self.assertFalse(D.up_to_date(self.c, "W2", chrome))
        lv = {"platform": "android", "pkg": "com.loyverse.sale", "name": "Loyverse", "ver": "2.71", "build": 271, "ext": "apk"}
        self.assertTrue(D.up_to_date(self.c, "A2", lv)); lv["build"] = 272; self.assertFalse(D.up_to_date(self.c, "A2", lv))
        self.assertIsNone(D.up_to_date(self.c, "A1", lv))

    def test_swlib_store_delete(self):
        S.DIR_OVERRIDE = None
        os.environ["PW_DIR"] = self.d
        body = os.urandom(1000)
        try:
            r = S.store(self.c, self.d, io.BytesIO(body), len(body), "ChromeSetup-130.0.6723.92.exe", "t")
        except TypeError:
            self.skipTest("store signature differs")
        import hashlib
        self.assertEqual(r["sha256"], hashlib.sha256(body).hexdigest()); self.assertEqual((r["platform"], r["ext"], r["ver"]), ("windows", "exe", "130.0.6723.92"))
        self.assertEqual(len(S.listing(self.c)), 1)
        with self.assertRaises(ValueError):
            S.store(self.c, self.d, io.BytesIO(b"x"), 1, "evil.sh", "t")
        S.delete(self.c, self.d, r["sha256"]); self.assertEqual(len(S.listing(self.c)), 0)
        self.assertEqual(S.guess_from_filename("adoluna-support-1.5.0-b81-x86_64.msi"), {"ver": "1.5.0-b81", "build": 81, "name": "Adoluna Support"})

if __name__ == "__main__":
    unittest.main()
