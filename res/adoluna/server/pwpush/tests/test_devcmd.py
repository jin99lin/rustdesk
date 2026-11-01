"""b79 device command channel tests (unit + through the sidecar).  Run:
   python3 -m unittest discover -s res/adoluna/server/pwpush/tests -v"""
import base64, http.server, importlib.util, json, os, socket, sqlite3, sys, tempfile, threading, unittest, urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

HERE = os.path.dirname(os.path.abspath(__file__)); SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)
import devcmd as D

def raw(k):
    return k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())

class Unit(unittest.TestCase):
    def setUp(self):
        import tempfile; D.set_key_dir(tempfile.mkdtemp())
        self.c = sqlite3.connect(":memory:"); D.init(self.c); self.k = Ed25519PrivateKey.generate()

    def test_queue_sign_ack(self):
        n = D.queue(self.c, "123", "reboot", {}, "t", now=1000)
        out = D.on_heartbeat(self.c, self.k, {"id": "123", "adoluna_cmd_n": 0}, now=1001)
        self.assertEqual([x["n"] for x in out], [n])
        x = out[0]
        self.k.public_key().verify(base64.b64decode(x["sig"]), D.msg("123", x["n"], x["k"], x["p"], x["e"]).encode())
        self.assertEqual(x["e"], 1000 + 3600)
        # other device never sees it; executed n is not resent; ack recorded
        self.assertEqual(D.on_heartbeat(self.c, self.k, {"id": "999", "adoluna_cmd_n": 0}, now=1001), [])
        self.assertEqual(D.on_heartbeat(self.c, self.k, {"id": "123", "adoluna_cmd_n": n}, now=1002), [])
        D.on_heartbeat(self.c, self.k, {"id": "123", "adoluna_cmd_n": n, "adoluna_ack": [{"n": n, "ok": True, "msg": "rebooting"}]}, now=1003)
        d = D.detail(self.c, "123")
        self.assertEqual((d["cmds"][0]["ok"], d["cmds"][0]["msg"]), (1, "rebooting"))
        # expired commands are not delivered
        D.queue(self.c, "123", "reboot", {}, "t", now=1000)
        self.assertEqual(D.on_heartbeat(self.c, self.k, {"id": "123", "adoluna_cmd_n": n}, now=99999), [])

    def test_staff_pin(self):
        import hashlib
        self.assertRaises(ValueError, D.set_staff_pin, self.c, "12a4", "t")
        self.assertRaises(ValueError, D.set_staff_pin, self.c, "123", "t")
        v = D.set_staff_pin(self.c, "246810", "t", now=1000)
        row = self.c.execute("select payload from staff_pin").fetchone()[0]
        self.assertTrue(row.startswith("enc1:")); self.assertNotIn("246810", row)
        out = D.on_heartbeat(self.c, self.k, {"id": "7", "adoluna_cmd_n": 0}, now=1001)
        x = [o for o in out if o["k"] == "staff_pin"][0]; p = json.loads(x["p"])
        self.assertEqual(p["h"], hashlib.pbkdf2_hmac("sha256", b"246810", bytes.fromhex(p["s"]), p["i"], 32).hex())
        self.assertEqual(p["v"], v); self.assertNotIn("246810", x["p"])
        # once per version: not queued again
        D.on_heartbeat(self.c, self.k, {"id": "7", "adoluna_cmd_n": x["n"], "adoluna_ack": [{"n": x["n"], "ok": True}]}, now=1002)
        self.assertEqual(self.c.execute("select count(*) from dev_cmds where k='staff_pin'").fetchone()[0], 1)
        self.assertEqual(self.c.execute("select p from dev_cmds where k='staff_pin'").fetchone()[0], "{}")  # scrubbed after ack
        st = D.staff_pin_status(self.c); self.assertEqual((st["set"], st["delivered"], st["applied"]), (True, 1, 1))
        # change: new version goes out, an undelivered older one is superseded
        D.set_staff_pin(self.c, "1357", "t", now=1003)
        D.set_staff_pin(self.c, "", "t", now=1004)
        out = D.on_heartbeat(self.c, self.k, {"id": "7", "adoluna_cmd_n": x["n"]}, now=1005)
        self.assertEqual([json.loads(o["p"])["h"] for o in out if o["k"] == "staff_pin"], [""])
        self.assertFalse(D.staff_pin_status(self.c)["set"])

    def test_validate(self):
        self.assertIsNone(D.validate("install", {"url": "https://x/a.apk", "sha256": "A" * 64}))
        self.assertTrue(D.validate("install", {"url": "https://x/a.apk"}))
        self.assertTrue(D.validate("install", {"url": "file:///x", "sha256": "a" * 64}))
        self.assertTrue(D.validate("rm -rf", {}))
        self.assertTrue(D.validate("kiosk_lock", {}))
        self.assertTrue(D.validate("policy", {"brightness": 999}))
        self.assertIsNone(D.validate("policy", {"no_uninstall": True, "brightness": 200}))

    def test_otp_and_unlock_code(self):
        # same algorithm as AdolunaDevice.otp (Kotlin): fixed vector
        self.assertEqual(D.otp("00" * 20, "123456789", 0), D.otp("00" * 20, "123456789", 0))
        self.assertEqual(len(D.otp("ab" * 20, "1", 5)), 8)
        self.assertNotEqual(D.otp("ab" * 20, "1", 5), D.otp("ab" * 20, "2", 5))
        D.new_otp_secret(self.c, "123", "t")
        o = D.current_otp(self.c, "123", now=6000)
        self.assertEqual(o["valid_until"], 7200)
        code, n = D.new_unlock_code(self.c, "123", "t")
        self.assertEqual(len(code), 6)
        p = json.loads(D.unseal(self.c.execute("select p from dev_cmds where id=?", (n,)).fetchone()[0]))
        import hashlib
        self.assertEqual(hashlib.sha256((p["s"] + code).encode()).hexdigest(), p["h"])
        self.assertNotIn(code, json.dumps(D.detail(self.c, "123")))  # secrets never echoed
        self.assertNotIn(self.c.execute("select otp_k from dev_secrets").fetchone()[0], json.dumps(D.detail(self.c, "123")))

    def test_release_once_per_device(self):
        D.set_release(self.c, "windows", "https://x/a.msi", "b" * 64, "b79", True, "t", 79)
        info = {"platform": "Windows"}
        a = D.on_heartbeat(self.c, self.k, {"id": "1", "adoluna_cmd_n": 0, "adoluna_dev": info})
        self.assertEqual([x["k"] for x in a], ["update"])
        b = D.on_heartbeat(self.c, self.k, {"id": "1", "adoluna_cmd_n": a[0]["n"], "adoluna_dev": info})
        self.assertEqual(b, [])
        self.assertEqual(D.on_heartbeat(self.c, self.k, {"id": "2", "adoluna_cmd_n": 0, "adoluna_dev": {"sdk": 30}}), [])

    def test_update_needs_build_and_msi(self):
        ok = {"url": "https://x/a.msi", "sha256": "a" * 64, "build": "b81"}
        self.assertIsNone(D.validate("update", ok, "windows"))
        self.assertEqual(ok["build"], 81)
        self.assertIn("build", D.validate("update", {"url": "https://x/a.msi", "sha256": "a" * 64}, "windows"))
        self.assertIn("build", D.validate("update", {"url": "https://x/a.msi", "sha256": "a" * 64, "build": "1.5.0-b81"}, "windows"))
        self.assertIn("build", D.validate("update", {"url": "https://x/a.msi", "sha256": "a" * 64, "build": 0}, "windows"))
        self.assertIn(".msi", D.validate("update", {"url": "https://x/a.exe", "sha256": "a" * 64, "build": 81}, "windows"))
        self.assertIsNone(D.validate("update", {"url": "https://x/a.MSI?x=1", "sha256": "a" * 64, "build": 81}, "windows"))
        self.assertIsNone(D.validate("update", {"url": "https://x/a.dmg", "sha256": "a" * 64, "build": 81}, "macos"))
        self.assertIsNone(D.validate("install", {"url": "https://x/a.exe", "sha256": "a" * 64}, "windows"))
        with self.assertRaises(ValueError):
            D.set_release(self.c, "windows", "https://x/a.msi", "b" * 64, "b81", True, "t", None)

    def test_release_compares_build_not_version(self):
        D.set_release(self.c, "windows", "https://x/a.msi", "b" * 64, "1.5.0-b81", True, "t", 81)
        same_ver_older = {"platform": "windows", "ver": "1.5.0", "build": 80}
        a = D.on_heartbeat(self.c, self.k, {"id": "10", "adoluna_cmd_n": 0, "adoluna_dev": same_ver_older})
        self.assertEqual([x["k"] for x in a], ["update"])
        self.assertEqual(json.loads(a[0]["p"])["build"], 81)
        newer = {"platform": "windows", "ver": "1.5.0", "build": 81}
        self.assertEqual(D.on_heartbeat(self.c, self.k, {"id": "11", "adoluna_cmd_n": 0, "adoluna_dev": newer}), [])
        legacy = {"platform": "windows", "ver": "1.5.0"}  # pre-b80 client: no build reported -> gets the update
        self.assertEqual([x["k"] for x in D.on_heartbeat(self.c, self.k, {"id": "12", "adoluna_cmd_n": 0, "adoluna_dev": legacy})], ["update"])
        self.c.execute("update releases set build=null")
        self.c.execute("delete from dev_rel")
        self.assertEqual(D.on_heartbeat(self.c, self.k, {"id": "13", "adoluna_cmd_n": 0, "adoluna_dev": same_ver_older}), [])

    def test_init_migrates_releases_build(self):
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("create table releases(platform text primary key, url text, sha256 text, label text, auto integer, ts integer, by text)")
        D.init(c)
        self.assertIn("build", [r[1] for r in c.execute("pragma table_info(releases)")])

    def test_windows_caps(self):
        old = {"platform": "windows", "win": {"major": "7", "edition": "Other", "build": "7601"}, "cap": {"install": False, "kiosk": "none", "reason": "os_unsupported"}}
        home = {"platform": "windows", "win": {"major": "11", "edition": "Home", "build": "22631", "ubr": "4317", "display_version": "23H2"}, "cap": {"install": True, "kiosk": "none", "reason": "needs_pro"}}
        D.on_heartbeat(self.c, self.k, {"id": "7", "adoluna_cmd_n": 0, "adoluna_dev": old})
        D.on_heartbeat(self.c, self.k, {"id": "8", "adoluna_cmd_n": 0, "adoluna_dev": home})
        self.assertTrue(D.capability_error(self.c, "7", "install"))
        self.assertTrue(D.capability_error(self.c, "7", "reboot"))
        self.assertIsNone(D.capability_error(self.c, "8", "install"))
        self.assertTrue(D.capability_error(self.c, "8", "kiosk_lock"))
        self.assertIsNone(D.capability_error(self.c, "9", "kiosk_lock"))  # unknown device: allowed (device decides)
        self.assertEqual(D.summary(self.c)["8"]["os_label"], "Windows 11 Home 23H2 (22631.4317)")

    def test_secrets_sealed_at_rest(self):
        D.new_otp_secret(self.c, "5", "t")
        D.new_unlock_code(self.c, "5", "t")
        raw = [r[0] for r in self.c.execute("select otp_k from dev_secrets")] + \
              [r[0] for r in self.c.execute("select p from dev_cmds where k in ('otp_secret','unlock_code')")]
        self.assertTrue(all(x.startswith("enc1:") for x in raw))
        self.assertTrue(D.current_otp(self.c, "5"))
        out = D.on_heartbeat(self.c, self.k, {"id": "5", "adoluna_cmd_n": 0})
        self.assertTrue(all(not o["p"].startswith("enc1:") for o in out))
        D.on_heartbeat(self.c, self.k, {"id": "5", "adoluna_cmd_n": 9, "adoluna_ack": [{"n": o["n"], "ok": True} for o in out]})
        self.assertEqual({r[0] for r in self.c.execute("select p from dev_cmds where k in ('otp_secret','unlock_code')")}, {"{}"})

    def test_summary_badge(self):
        D.on_heartbeat(self.c, self.k, {"id": "7", "adoluna_cmd_n": 0, "adoluna_dev": {"is_owner": True, "sdk": 30, "kiosk": False}})
        self.assertEqual(D.summary(self.c)["7"]["do"], True)
        self.assertEqual(D.summary(self.c)["7"]["platform"], "android")

# ---- through the sidecar ----
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class Sidecar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="devcmd-test-")
        open(os.path.join(tmp, "signing.key"), "wb").write(raw(Ed25519PrivateKey.generate()))
        cls.tk = Ed25519PrivateKey.generate(); open(os.path.join(tmp, "ticket.key"), "wb").write(raw(cls.tk))
        db = os.path.join(tmp, "api.db"); c = sqlite3.connect(db)
        c.executescript("""create table peers(id text, hostname text, alias text, os text, last_online_time integer, group_id integer);
          create table users(id integer, username text, nickname text, group_id integer, is_admin integer, status integer);
          create table user_tokens(user_id integer, token text, expired_at integer);
          create table device_groups(id integer, name text); create table groups(id integer, name text);
          create table address_books(row_id integer primary key, id text, tags text, user_id integer, collection_id integer);
          insert into users values(1,'admin','',1,1,1),(2,'staff','',1,0,1);
          insert into user_tokens values(1,'admintok',0),(2,'stafftok',0);
          insert into peers values('100','a','','',0,0);"""); c.commit(); c.close()
        up, sp = free_port(), free_port(); cls.sp = sp
        os.environ.update(PW_DIR=tmp, PW_DB=db, PW_LISTEN="127.0.0.1", PW_PORT=str(sp), PW_UP_HOST="127.0.0.1", PW_UP_PORT=str(up))
        class Up(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def r(self, d):
                b = json.dumps(d).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def do_GET(self): self.r({"code": 0 if self.headers.get("api-token") in ("admintok", "stafftok") else 1})
            def do_POST(self): self.rfile.read(int(self.headers.get("Content-Length") or 0)); self.r({"modified_at": 0})
        spec = importlib.util.spec_from_file_location("pwpush_dc", os.path.join(SRC, "pwpush.py")); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
        for srv in (http.server.ThreadingHTTPServer(("127.0.0.1", up), Up), http.server.ThreadingHTTPServer(("127.0.0.1", sp), M.H)):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(self, path, body=None, tok=None):
        h = {"Content-Type": "application/json"}
        if tok: h["api-token"] = tok
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.sp, path), data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            x = urllib.request.urlopen(r); return x.status, json.loads(x.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_flow(self):
        self.assertEqual(self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "reboot"}, "stafftok")[0], 403)
        self.assertEqual(self.req("/_admin/adoluna-api/dev/cmd", {"id": "555", "k": "reboot"}, "admintok")[0], 400)
        self.assertEqual(self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "install", "p": {"url": "https://x"}}, "admintok")[0], 400)
        s, r = self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "kiosk_lock", "p": {"pkg": "com.pos"}}, "admintok")
        self.assertEqual(s, 200); n = r["n"]
        # old client (no adoluna_cmd_n) never gets commands
        self.assertNotIn("adoluna_cmd", self.req("/api/heartbeat", {"id": "100"})[1])
        _, h = self.req("/api/heartbeat", {"id": "100", "adoluna_cmd_n": 0, "adoluna_dev": {"is_owner": True, "sdk": 33}})
        x = h["adoluna_cmd"][0]; self.assertEqual((x["n"], x["k"]), (n, "kiosk_lock"))
        self.tk.public_key().verify(base64.b64decode(x["sig"]), D.msg("100", x["n"], x["k"], x["p"], x["e"]).encode())
        _, h = self.req("/api/heartbeat", {"id": "100", "adoluna_cmd_n": n, "adoluna_ack": [{"n": n, "ok": True, "msg": "locked"}]})
        self.assertNotIn("adoluna_cmd", h)
        s, d = self.req("/_admin/adoluna-api/dev?id=100", tok="admintok")
        self.assertEqual((d["info"]["is_owner"], d["cmds"][0]["msg"]), (True, "locked"))
        self.assertTrue(self.req("/_admin/adoluna-api/dev-list", tok="admintok")[1]["100"]["do"])
        s, r = self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "unlock_code"}, "admintok")
        self.assertEqual(len(r["unlock_code"]), 6)
        self.assertEqual(self.req("/_admin/adoluna-api/dev/otp?id=100", tok="admintok")[0], 404)
        self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "otp_secret"}, "admintok")
        s, o = self.req("/_admin/adoluna-api/dev/otp?id=100", tok="admintok"); self.assertEqual((s, len(o["otp"])), (200, 8))
        s, r = self.req("/_admin/adoluna-api/releases", {"platform": "windows", "url": "https://x/a.msi", "sha256": "c" * 64, "auto": True}, "admintok")
        self.assertEqual(s, 400)  # build number required
        s, r = self.req("/_admin/adoluna-api/releases", {"platform": "windows", "url": "https://x/a.exe", "sha256": "c" * 64, "auto": True, "build": 81}, "admintok")
        self.assertEqual(s, 400)  # Windows needs .msi
        s, r = self.req("/_admin/adoluna-api/releases", {"platform": "windows", "url": "https://x/a.msi", "sha256": "c" * 64, "auto": True, "build": 81}, "admintok")
        self.assertEqual(r["releases"][0]["platform"], "windows")
        self.assertEqual(r["releases"][0]["build"], 81)
        self.req("/api/heartbeat", {"id": "100", "adoluna_cmd_n": 0, "adoluna_dev": {"platform": "windows", "ver": "1.5.0", "build": 81}})
        u = {"url": "https://x/a.msi", "sha256": "c" * 64}
        self.assertEqual(self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "update", "p": u}, "admintok")[0], 400)
        self.assertEqual(self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "update", "p": dict(u, build=81)}, "admintok")[0], 400)
        self.assertEqual(self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "update", "p": dict(u, url="https://x/a.exe", build=82)}, "admintok")[0], 400)
        r = self.req("/_admin/adoluna-api/dev/cmd", {"id": "100", "k": "update", "p": dict(u, build=82)}, "admintok"); self.assertEqual(r[0], 200, r)
        # b80 staff PIN: admin only, never echoed, delivered as hash
        self.assertEqual(self.req("/_admin/adoluna-api/staff-pin", {"pin": "9999"}, "stafftok")[0], 403)
        self.assertEqual(self.req("/_admin/adoluna-api/staff-pin", {"pin": "99"}, "admintok")[0], 400)
        s, st = self.req("/_admin/adoluna-api/staff-pin", {"pin": "98765"}, "admintok")
        self.assertEqual((s, st["set"]), (200, True)); self.assertNotIn("98765", json.dumps(st))
        _, h = self.req("/api/heartbeat", {"id": "100", "adoluna_cmd_n": n})
        self.assertIn("staff_pin", [c["k"] for c in h.get("adoluna_cmd", [])]); self.assertNotIn("98765", json.dumps(h))

if __name__ == "__main__":
    unittest.main()
