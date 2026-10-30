"""Sidecar unit tests (batch 4: scoped batch push + per-device passwords).  Run:
   python3 -m unittest discover -s res/adoluna/server/pwpush/tests -v
Self-contained: temp dir, temp sqlite db, throwaway signing keys, mock upstream on a free port."""
import base64, http.server, importlib.util, json, os, socket, sqlite3, sys, tempfile, threading, unittest, urllib.request
from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

HERE = os.path.dirname(os.path.abspath(__file__)); SRC = os.path.dirname(HERE)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

TMP = tempfile.mkdtemp(prefix="pwpush-test-")
KEYB = Ed25519PrivateKey.generate()
open(os.path.join(TMP, "signing.key"), "wb").write(KEYB.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
PUB = KEYB.public_key()
DBP = os.path.join(TMP, "api.db")
c = sqlite3.connect(DBP)
c.executescript("""
create table peers(id text, hostname text, alias text, os text, last_online_time integer, group_id integer);
create table users(id integer, username text, nickname text, group_id integer, is_admin integer, status integer);
create table user_tokens(user_id integer, token text, expired_at integer);
create table device_groups(id integer, name text); create table groups(id integer, name text);
create table address_books(row_id integer primary key, id text, tags text, user_id integer, collection_id integer);
insert into users values(1,'admin','',1,1,1),(2,'staff','',1,0,1);
insert into user_tokens values(1,'admintok',0),(2,'stafftok',0);
insert into device_groups values(5,'Store A'),(6,'Store B');
insert into peers values('100','a','','',0,5),('101','b','','',0,5),('200','c','','',0,6),('300','d','','',0,0),('400','e','','',0,0);
insert into address_books(id,tags,user_id,collection_id) values('300','[]',1,7),('400','[]',2,0);
"""); c.commit(); c.close()
UPP = free_port(); SP = free_port()
os.environ.update(PW_DIR=TMP, PW_DB=DBP, PW_LISTEN="127.0.0.1", PW_PORT=str(SP), PW_UP_HOST="127.0.0.1", PW_UP_PORT=str(UPP))

class Up(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def r(self, d):
        b = json.dumps(d).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):  # peer/list works for any logged-in token (staff too), like rustdesk-api
        self.r({"code": 0 if self.headers.get("api-token") in ("admintok", "stafftok") else 1})
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0)); self.r({"modified_at": 0})

spec = importlib.util.spec_from_file_location("pwpush", os.path.join(SRC, "pwpush.py")); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
up = http.server.ThreadingHTTPServer(("127.0.0.1", UPP), Up); threading.Thread(target=up.serve_forever, daemon=True).start()
sc = http.server.ThreadingHTTPServer(("127.0.0.1", SP), M.H); threading.Thread(target=sc.serve_forever, daemon=True).start()

def req(path, body=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok: h["api-token"] = tok
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (SP, path), data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        x = urllib.request.urlopen(r); return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
def hb(dev, ver):
    return req("/api/heartbeat", {"id": dev, "adoluna_pw_ver": ver})[1].get("adoluna_pw")
def verify(p, pw):
    PUB.verify(base64.b64decode(p["sig"]), f"adoluna-pw-v1\n{p['v']}\n{p['h']}\n{p['s']}".encode())
    return base64.b64encode(sha256((pw + p["s"]).encode()).digest()).decode() == p["h"]
PW1, PW2, PWC = "batch-pass-1", "batch-pass-2", "custom-pass-9"

class T(unittest.TestCase):
    def setUp(self):
        for f in ("state.json", "adoluna.db"):
            try: os.remove(os.path.join(TMP, f))
            except FileNotFoundError: pass
        M.SEEN.clear(); M._SCOPE_CACHE.update(k=None)

    def test_admin_only(self):
        self.assertEqual(req("/_admin/adoluna-api/password", {"password": PW1})[0], 403)
        self.assertEqual(req("/_admin/adoluna-api/password", {"password": PW1}, "stafftok")[0], 403)
        self.assertEqual(req("/_admin/adoluna-api/device-pw", {"id": "100", "password": PWC}, "stafftok")[0], 403)
        self.assertEqual(req("/_admin/adoluna-api/password", {"password": PW1}, "admintok")[0], 200)

    def test_scope_union(self):
        st = req("/_admin/adoluna-api/password", {"password": PW1, "scope": {"peers": ["400"], "dgroups": ["5"], "abs": ["c7"]}}, "admintok")[1]
        self.assertEqual(st["target_count"], 4)  # 100,101 (group 5) + 300 (collection 7) + 400
        for d in ("100", "101", "300", "400"):
            p = hb(d, 0); self.assertTrue(p and verify(p, PW1), d)
        self.assertIsNone(hb("200", 0))  # group 6 not selected
        st = req("/_admin/adoluna-api/password", {"scope": {"abs": ["u2"]}}, "admintok")[1]  # staff's default book -> 400
        M._SCOPE_CACHE.update(k=None)
        self.assertIsNone(hb("100", 0)); self.assertTrue(hb("400", 0))
        req("/_admin/adoluna-api/password", {"scope": {"all": True}}, "admintok")
        self.assertTrue(hb("200", 0))

    def test_bad_scope_values_dropped(self):
        st = req("/_admin/adoluna-api/password", {"scope": {"peers": ["1;drop", "100"], "dgroups": ["x"], "abs": ["z1", "c"]}}, "admintok")[1]
        self.assertEqual(st["scope"], {"all": False, "peers": ["100"], "dgroups": [], "abs": []})

    def test_old_client_never_gets_hash(self):
        req("/_admin/adoluna-api/password", {"password": PW1, "scope": {"all": True}}, "admintok")
        self.assertNotIn("adoluna_pw", req("/api/heartbeat", {"id": "100"})[1])

    def test_custom_skipped_by_batch_and_cleared(self):
        req("/_admin/adoluna-api/password", {"password": PW1, "scope": {"all": True}}, "admintok")
        v1 = hb("100", 0)["v"]
        self.assertEqual(req("/_admin/adoluna-api/device-pw", {"id": "100", "password": "short"}, "admintok")[0], 400)
        self.assertEqual(req("/_admin/adoluna-api/device-pw", {"id": "100", "password": PWC, "confirm": "x" + PWC}, "admintok")[0], 400)
        self.assertEqual(req("/_admin/adoluna-api/device-pw", {"id": "999", "password": PWC}, "admintok")[0], 400)
        st = req("/_admin/adoluna-api/device-pw", {"id": "100", "password": PWC, "confirm": PWC}, "admintok")[1]
        self.assertTrue(st["devices"]["100"]["custom"])
        p = hb("100", v1); self.assertTrue(verify(p, PWC)); vc = p["v"]; self.assertGreater(vc, v1)
        self.assertIsNone(hb("101", v1))  # other devices untouched
        st = req("/_admin/adoluna-api/password", {"password": PW2}, "admintok")[1]  # new batch push skips 100
        self.assertIn("100", st["skipped_custom"])
        self.assertIsNone(hb("100", vc)); self.assertTrue(verify(hb("101", v1), PW2))
        # clearing: device goes back to the batch password with a version above its custom one
        st = req("/_admin/adoluna-api/device-pw", {"id": "100", "clear": True}, "admintok")[1]
        self.assertFalse(st["devices"]["100"]["custom"])
        p = hb("100", vc); self.assertTrue(p and verify(p, PW2)); self.assertGreater(p["v"], vc)
        self.assertIsNone(hb("100", p["v"]))
        # the next batch push supersedes the re-issue
        req("/_admin/adoluna-api/password", {"password": PW1}, "admintok"); self.assertTrue(verify(hb("100", p["v"]), PW1))

    def test_versions_monotonic_and_seen(self):
        req("/_admin/adoluna-api/password", {"password": PW1, "scope": {"all": True}}, "admintok")
        vs = [hb("100", 0)["v"]]
        req("/_admin/adoluna-api/device-pw", {"id": "200", "password": PWC}, "admintok"); vs.append(hb("200", 0)["v"])
        req("/_admin/adoluna-api/password", {"password": PW2}, "admintok"); vs.append(hb("100", 0)["v"])
        self.assertEqual(vs, sorted(set(vs)))
        hb("101", 3)
        st = req("/_admin/adoluna-api/password", tok="admintok")[1]
        self.assertEqual(st["devices"]["101"]["have"], 3)

    def test_no_plaintext_stored(self):
        req("/_admin/adoluna-api/password", {"password": PW1, "scope": {"all": True}}, "admintok")
        req("/_admin/adoluna-api/device-pw", {"id": "100", "password": PWC}, "admintok")
        blob = open(os.path.join(TMP, "state.json")).read() + open(os.path.join(TMP, "adoluna.db"), "rb").read().decode("latin1")
        self.assertNotIn(PW1, blob); self.assertNotIn(PWC, blob)
        self.assertEqual(oct(os.stat(os.path.join(TMP, "state.json")).st_mode & 0o777), "0o600")

    def test_legacy_state_migrates(self):
        json.dump({"v": 4, "targets": ["101"]}, open(os.path.join(TMP, "state.json"), "w"))
        st = req("/_admin/adoluna-api/password", tok="admintok")[1]
        self.assertEqual(st["scope"]["peers"], ["101"]); self.assertFalse(st["scope"]["all"])
        st = req("/_admin/adoluna-api/password", {"password": PW1}, "admintok")[1]
        self.assertEqual(st["version"], 5)
        json.dump({"v": 4, "targets": []}, open(os.path.join(TMP, "state.json"), "w"))
        self.assertTrue(req("/_admin/adoluna-api/password", tok="admintok")[1]["scope"]["all"])

if __name__ == "__main__":
    unittest.main()
