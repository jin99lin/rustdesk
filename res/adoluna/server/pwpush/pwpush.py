#!/usr/bin/env python3
"""Adoluna Support: permanent-password push sidecar for rustdesk-api.
Listens where rustdesk-api used to (10.20.26.62:21114), proxies everything to
rustdesk-api (127.0.0.1:21124) and adds a signed {v,h,s,sig} to /api/heartbeat
responses for devices whose applied version is older.  Only sha256(pw+salt) is stored.
Admin page: /_admin/adoluna-password (LAN/VPN only, needs admin login token)."""
import base64, http.client, ipaddress, json, os, secrets, sqlite3, string, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hashlib import sha256
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

LISTEN = (os.environ.get("PW_LISTEN", "10.20.26.62"), int(os.environ.get("PW_PORT", "21114")))
UP = (os.environ.get("PW_UP_HOST", "127.0.0.1"), int(os.environ.get("PW_UP_PORT", "21124")))
DIR = os.environ.get("PW_DIR", "/opt/rustdesk/pwpush")
DB = os.environ.get("PW_DB", "/opt/rustdesk/api-data/rustdeskapi.db")
STATE = os.path.join(DIR, "state.json")
HERE = os.path.dirname(os.path.abspath(__file__))
KEY = Ed25519PrivateKey.from_private_bytes(open(os.path.join(DIR, "signing.key"), "rb").read())
LOCK = threading.Lock()
ACL_DB = os.path.join(DIR, "adoluna.db")
TKEY_PATH = os.path.join(DIR, "ticket.key")
TKEY = Ed25519PrivateKey.from_private_bytes(open(TKEY_PATH, "rb").read()) if os.path.exists(TKEY_PATH) else None
TICKET_TTL = 60
RATE = {}
PEERUI = os.environ.get("PW_PEERUI", "1") == "1"  # set PW_PEERUI=0 to disable the injected device-list UI
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "host"}

def load():
    try:
        return json.load(open(STATE))
    except FileNotFoundError:
        return {"v": 0, "targets": []}

def save(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.chmod(tmp, 0o600); os.replace(tmp, STATE)

def private(ip):
    try:
        a = ipaddress.ip_address(ip.strip())
        return a.is_private or a.is_loopback or a in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False

def known_peer(dev_id):
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        r = c.execute("select 1 from peers where id=? limit 1", (dev_id,)).fetchone(); c.close()
        return r is not None
    except Exception:
        return True  # never block a push just because the db is busy

# ---- passwordless: grants / device roles / audit ----
def acl():
    c = sqlite3.connect(ACL_DB, timeout=5)
    c.executescript("""
      create table if not exists grants(id integer primary key, subj_kind text not null, subj text not null,
        tgt_kind text not null, tgt text not null, created_at integer, created_by text,
        unique(subj_kind, subj, tgt_kind, tgt));
      create table if not exists roles(peer_id text primary key, accept integer not null default 0,
        ctrl_only integer not null default 0, ts integer not null, updated_by text);
      create table if not exists audit(id integer primary key, ts integer, user text, my_id text, peer_id text,
        ip text, result text);""")
    return c

def audit(user, my_id, peer_id, ip, result):
    try:
        with acl() as c:
            c.execute("insert into audit(ts,user,my_id,peer_id,ip,result) values(?,?,?,?,?,?)",
                      (int(time.time()), user, my_id, peer_id, ip, result))
            c.execute("delete from audit where id <= (select max(id) from audit) - 50000")
    except Exception as e:
        sys.stderr.write("audit failed: %s\n" % e)

def role(peer_id):
    with acl() as c:
        r = c.execute("select accept, ctrl_only, ts from roles where peer_id=?", (peer_id,)).fetchone()
    return r or (0, 0, 0)

def cfg_payload(peer_id):
    a, co, ts = role(peer_id)
    sig = base64.b64encode(TKEY.sign(f"adoluna-cfg-v1\n{peer_id}\n{a}\n{co}\n{ts}".encode())).decode()
    return {"a": a, "c": co, "t": ts, "sig": sig}

def api_user(username):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
    r = c.execute("select id, group_id, is_admin, status from users where username=?", (username,)).fetchone()
    c.close(); return r

def peer_group(peer_id):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
    r = c.execute("select group_id from peers where id=? limit 1", (peer_id,)).fetchone(); c.close()
    return str(r[0]) if r and r[0] else ""

def granted(uid, gid, is_admin, peer_id):
    if is_admin:
        return True
    pg = peer_group(peer_id)
    with acl() as c:
        for sk, sv, tk, tv in c.execute("select subj_kind, subj, tgt_kind, tgt from grants"):
            subj_ok = (sk == "user" and sv == str(uid)) or (sk == "group" and sv == str(gid))
            tgt_ok = (tk == "peer" and tv == peer_id) or (tk == "dgroup" and pg and tv == pg)
            if subj_ok and tgt_ok:
                return True
    return False

def set_pw(st, pw, by):
    salt = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    h = base64.b64encode(sha256((pw + salt).encode()).digest()).decode()
    v = int(st.get("v", 0)) + 1
    sig = base64.b64encode(KEY.sign(f"adoluna-pw-v1\n{v}\n{h}\n{salt}".encode())).decode()
    st.update(v=v, payload={"v": v, "h": h, "s": salt, "sig": sig},
              updated_at=time.strftime("%Y-%m-%d %H:%M:%S %Z"), updated_by=by)

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *a):
        if "/_admin/adoluna" in self.path:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def upstream(self, method, path, body, headers):
        c = http.client.HTTPConnection(*UP, timeout=60)
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse(); data = r.read(); c.close()
        return r, data

    def send(self, code, data, ctype="application/json; charset=utf-8", extra=()):
        self.send_response(code)
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def proxy(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        hdr = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        hdr["Host"] = self.headers.get("Host", "%s:%d" % LISTEN)
        if "X-Forwarded-For" not in self.headers and "X-Real-IP" not in self.headers:
            hdr["X-Forwarded-For"] = hdr["X-Real-IP"] = self.client_address[0]
        idx = self.command == "GET" and self.path.split("?")[0] in ("/_admin", "/_admin/", "/_admin/index.html")
        if idx:  # we rewrite the admin index: ask upstream for a plain, uncached copy
            for k in list(hdr):
                if k.lower() in ("accept-encoding", "if-none-match", "if-modified-since"):
                    del hdr[k]
        try:
            r, data = self.upstream(self.command, self.path, body, hdr)
        except Exception as e:
            return self.send(502, b'{"error":"upstream"}')
        if self.command == "POST" and self.path.split("?")[0] == "/api/heartbeat" and r.status == 200:
            data = self.add_pw(body, data)
            data = self.add_cfg(body, data)
        extra = [(k, v) for k, v in r.getheaders() if k.lower() not in HOP and k.lower() != "content-type"]
        if idx and r.status == 200 and b"</head>" in data and PEERUI:
            data = data.replace(b"<head>", b"<head><script>try{if(!localStorage.getItem('adoluna_lang_init')){localStorage.setItem('lang','zh-CN');"
                                b"localStorage.setItem('adoluna_lang_init','1')}}catch(e){}</script>", 1)
            data = data.replace(b"</head>", b'<link rel="stylesheet" href="/_admin/adoluna-peerui.css?v=4">'
                                b'<script src="/_admin/adoluna-peerui.js?v=4" defer></script></head>', 1)
            extra = [(k, v) for k, v in extra if k.lower() not in ("etag", "last-modified", "cache-control")] + [("Cache-Control", "no-store")]
        self.send(r.status, data, r.getheader("Content-Type", "application/octet-stream"), extra)

    def add_pw(self, req, data):
        try:
            st = load(); p = st.get("payload")
            if not p:
                return data
            q = json.loads(req or b"{}")
            dev = str(q.get("id", ""))
            if int(q.get("adoluna_pw_ver") or 0) >= p["v"] or "adoluna_pw_ver" not in q:
                return data  # old builds don't send the field: never hand them the hash
            if st.get("targets") and dev not in st["targets"]:
                return data
            if not known_peer(dev):
                return data
            out = json.loads(data or b"{}")
            out["adoluna_pw"] = p
            return json.dumps(out).encode()
        except Exception:
            return data

    def add_cfg(self, req, data):
        try:
            if not TKEY:
                return data
            q = json.loads(req or b"{}")
            if "adoluna_cfg_ts" not in q:
                return data
            dev = str(q.get("id", ""))
            a, co, ts = role(dev)
            if ts <= int(q.get("adoluna_cfg_ts") or 0):
                return data
            out = json.loads(data or b"{}")
            out["adoluna_cfg"] = cfg_payload(dev)
            return json.dumps(out).encode()
        except Exception:
            return data

    def client_ip(self):
        return (self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0]
                or self.client_address[0]).strip()

    def ticket(self):
        ip = self.client_ip()
        deny = lambda code, msg, user="", my="", peer="": (audit(user, my, peer, ip, "拒绝: " + msg) if user else None,
                                                           self.send(code, json.dumps({"error": msg}).encode()))[1]
        if self.command != "POST" or not TKEY:
            return self.send(404, b"{}")
        now = time.time()
        with LOCK:
            w = [t for t in RATE.get(ip, []) if t > now - 60]; w.append(now); RATE[ip] = w
            if len(RATE) > 5000:
                RATE.clear()
        if len(w) > 30:
            return self.send(429, json.dumps({"error": "too many requests"}).encode())
        n = int(self.headers.get("Content-Length") or 0)
        try:
            q = json.loads(self.rfile.read(n) or b"{}")
            peer, my, ch = str(q["peer_id"]).strip(), str(q["my_id"]).strip(), str(q["challenge"])
        except Exception:
            return self.send(400, b'{"error":"bad request"}')
        if not (peer.isdigit() and my.isdigit() and 0 < len(ch) <= 128 and "\n" not in ch):
            return self.send(400, b'{"error":"bad request"}')
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or len(auth) < 12:
            return self.send(401, b'{"error":"not logged in"}')
        try:
            r, d = self.upstream("POST", "/api/currentUser", b"{}", {"Authorization": auth, "Content-Type": "application/json"})
            name = json.loads(d).get("name") if r.status == 200 else None
        except Exception:
            name = None
        if not name:
            return self.send(401, b'{"error":"not logged in"}')
        u = api_user(name)
        if not u or u[3] != 1:
            return deny(403, "账号不可用", name, my, peer)
        a, co, _ = role(peer)
        if co:
            return deny(403, "目标设备为仅控制端", name, my, peer)
        if not a:
            return deny(403, "目标设备未开启账号免密登录", name, my, peer)
        if not known_peer(peer):
            return deny(403, "未知设备", name, my, peer)
        if not granted(u[0], u[1], u[2], peer):
            return deny(403, "未授权该设备", name, my, peer)
        exp = int(now) + TICKET_TTL
        sig = base64.b64encode(TKEY.sign(f"adoluna-tkt-v1\n{peer}\n{my}\n{name}\n{ch}\n{exp}".encode())).decode()
        audit(name, my, peer, ip, "签发")
        return self.send(200, json.dumps({"ticket": json.dumps({"u": name, "e": exp, "sig": sig})}).encode())

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def acl_admin(self, path):
        if not self.admin_ok():
            return self.send(403, json.dumps({"error": "需要在内网用管理员账号登录后台"}).encode())
        by = self.client_ip()
        if path == "/_admin/adoluna-api/acl" and self.command == "GET":
            with acl() as c:
                roles = [dict(zip(("peer_id", "accept", "ctrl_only", "ts", "updated_by"), r))
                         for r in c.execute("select peer_id, accept, ctrl_only, ts, updated_by from roles")]
                grants = [dict(zip(("id", "subj_kind", "subj", "tgt_kind", "tgt", "created_at", "created_by"), r))
                          for r in c.execute("select * from grants order by id")]
            ro = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
            users = [dict(zip(("id", "username", "nickname", "group_id", "is_admin", "status"), r))
                     for r in ro.execute("select id, username, nickname, group_id, is_admin, status from users")]
            groups = [dict(zip(("id", "name"), r)) for r in ro.execute("select id, name from groups")]
            dgroups = [dict(zip(("id", "name"), r)) for r in ro.execute("select id, name from device_groups")]
            ro.close()
            return self.send(200, json.dumps({"roles": roles, "grants": grants, "users": users, "groups": groups,
                                              "device_groups": dgroups, "ticket_enabled": bool(TKEY)}).encode())
        q = self.body_json()
        if self.command != "POST" or q is None:
            return self.send(400, b'{"error":"bad request"}')
        if path == "/_admin/adoluna-api/role":
            peer = str(q.get("peer_id", "")).strip()
            if not peer.isdigit() or not known_peer(peer):
                return self.send(400, json.dumps({"error": "设备 ID 无效"}).encode())
            a, co = int(bool(q.get("accept"))), int(bool(q.get("ctrl_only")))
            with LOCK, acl() as c:
                old = c.execute("select ts from roles where peer_id=?", (peer,)).fetchone()
                ts = max(int(time.time()), (old[0] + 1) if old else 0)
                c.execute("insert or replace into roles(peer_id, accept, ctrl_only, ts, updated_by) values(?,?,?,?,?)",
                          (peer, a, co, ts, by))
            audit("(后台)", "", peer, by, f"设置角色 免密={a} 仅控制端={co}")
            return self.send(200, json.dumps({"peer_id": peer, "accept": a, "ctrl_only": co, "ts": ts}).encode())
        if path == "/_admin/adoluna-api/grant":
            if q.get("delete"):
                with acl() as c:
                    c.execute("delete from grants where id=?", (int(q["delete"]),))
                audit("(后台)", "", "", by, f"删除授权 #{int(q['delete'])}")
                return self.send(200, b'{"ok":true}')
            sk, sv, tk, tv = (str(q.get(k, "")).strip() for k in ("subj_kind", "subj", "tgt_kind", "tgt"))
            if sk not in ("user", "group") or tk not in ("peer", "dgroup") or not sv.isdigit() or not tv.isdigit():
                return self.send(400, b'{"error":"bad grant"}')
            with acl() as c:
                c.execute("insert or ignore into grants(subj_kind, subj, tgt_kind, tgt, created_at, created_by) values(?,?,?,?,?,?)",
                          (sk, sv, tk, tv, int(time.time()), by))
            audit("(后台)", "", tv if tk == "peer" else "", by, f"添加授权 {sk}:{sv} -> {tk}:{tv}")
            return self.send(200, b'{"ok":true}')
        return self.send(404, b"{}")

    def audit_list(self):
        if not self.admin_ok():
            return self.send(403, json.dumps({"error": "需要在内网用管理员账号登录后台"}).encode())
        from urllib.parse import urlparse, parse_qs
        p = parse_qs(urlparse(self.path).query)
        lim = min(int((p.get("limit") or ["200"])[0]), 1000)
        where, args = [], []
        for k in ("user", "peer_id", "my_id"):
            if p.get(k):
                where.append(f"{k}=?"); args.append(p[k][0])
        sql = "select ts, user, my_id, peer_id, ip, result from audit" + (" where " + " and ".join(where) if where else "") + " order by id desc limit ?"
        with acl() as c:
            rows = [dict(zip(("ts", "user", "my_id", "peer_id", "ip", "result"), r)) for r in c.execute(sql, args + [lim])]
        return self.send(200, json.dumps({"items": rows}).encode())

    # ---- admin ----
    def admin_ok(self):
        if not private(self.client_address[0]):
            return False
        for h in ("X-Forwarded-For", "X-Real-IP"):
            if self.headers.get(h) and not all(private(x) for x in self.headers[h].split(",")):
                return False
        tok = self.headers.get("api-token", "")
        if not tok:
            return False
        try:
            r, d = self.upstream("GET", "/api/admin/peer/list?page=1&page_size=1", None, {"api-token": tok})
            return r.status == 200 and json.loads(d).get("code") == 0
        except Exception:
            return False

    def status(self):
        st = load()
        return {"version": st.get("v", 0), "updated_at": st.get("updated_at"), "updated_by": st.get("updated_by"),
                "targets": st.get("targets", [])}

    def admin(self):
        path = self.path.split("?")[0]
        if path in ("/_admin/adoluna-password", "/_admin/adoluna-password.html"):
            if not private(self.client_address[0]):
                return self.send(403, b"forbidden", "text/plain")
            return self.send(200, open(os.path.join(HERE, "password.html"), "rb").read(), "text/html; charset=utf-8",
                             [("Cache-Control", "no-store")])
        if path in ("/_admin/adoluna-peerui.js", "/_admin/adoluna-peerui.css"):
            f = os.path.join(HERE, path.rsplit("-", 1)[1])
            ct = "application/javascript; charset=utf-8" if f.endswith(".js") else "text/css; charset=utf-8"
            return self.send(200, open(f, "rb").read(), ct, [("Cache-Control", "no-cache")])
        if path == "/_admin/adoluna-api/reset":
            if self.command != "POST" or not self.admin_ok():
                return self.send(403, json.dumps({"error": "需要在内网用管理员账号登录后台"}).encode())
            n = int(self.headers.get("Content-Length") or 0)
            try:
                dev = str(json.loads(self.rfile.read(n) or b"{}").get("id", "")).strip()
            except Exception:
                dev = ""
            if not dev.isdigit() or not known_peer(dev):
                return self.send(400, json.dumps({"error": "设备 ID 无效"}).encode())
            with LOCK:
                st = load(); t = st.get("targets", [])
                if not t:
                    note = "已在推送范围（全部设备）"
                elif dev in t:
                    note = "已在推送范围"
                else:
                    st["targets"] = (t + [dev])[:200]; save(st); note = "已加入推送范围"
            out = self.status(); out["note"] = note
            return self.send(200, json.dumps(out).encode())
        if path in ("/_admin/adoluna-api/acl", "/_admin/adoluna-api/role", "/_admin/adoluna-api/grant"):
            return self.acl_admin(path)
        if path == "/_admin/adoluna-api/audit":
            return self.audit_list()
        if path != "/_admin/adoluna-api/password":
            return self.send(404, b"{}")
        if not self.admin_ok():
            return self.send(403, json.dumps({"error": "需要在内网用管理员账号登录后台"}).encode())
        if self.command == "GET":
            return self.send(200, json.dumps(self.status()).encode())
        n = int(self.headers.get("Content-Length") or 0)
        try:
            q = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send(400, b'{"error":"bad json"}')
        with LOCK:
            st = load()
            if "targets" in q:
                st["targets"] = [t.strip() for t in q["targets"] if str(t).strip()][:200]
            pw = q.get("password")
            if pw is not None:
                if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 64:
                    return self.send(400, json.dumps({"error": "密码长度需 8–64 位"}).encode())
                set_pw(st, pw, self.client_address[0])
            save(st)
        return self.send(200, json.dumps(self.status()).encode())

    def handle_any(self):
        if self.path.split("?")[0] == "/api/adoluna/ticket":
            return self.ticket()
        if self.path.startswith("/_admin/adoluna"):
            return self.admin()
        return self.proxy()
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = handle_any

def cli():
    # sudo python3 pwpush.py set [--targets id1,id2]   (password read from the terminal, not echoed)
    # sudo python3 pwpush.py targets id1,id2|all      sudo python3 pwpush.py status
    import getpass
    a = sys.argv[1:]; st = load()
    if a[0] == "status":
        print({k: st.get(k) for k in ("v", "updated_at", "updated_by", "targets")}); return
    if a[0] == "targets":
        st["targets"] = [] if a[1] == "all" else [x for x in a[1].split(",") if x]
    elif a[0] == "set":
        if "--targets" in a:
            t = a[a.index("--targets") + 1]; st["targets"] = [] if t == "all" else [x for x in t.split(",") if x]
        pw = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass("新的固定密码: ")
        if sys.stdin.isatty() and getpass.getpass("再输入一次: ") != pw:
            sys.exit("两次输入不一致")
        if not 8 <= len(pw) <= 64:
            sys.exit("密码长度需 8–64 位")
        set_pw(st, pw, "cli")
    with LOCK:
        save(st)
    print({k: st.get(k) for k in ("v", "updated_at", "targets")})

if __name__ == "__main__" and len(sys.argv) > 1:
    cli(); sys.exit(0)

if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.request_queue_size = 128
    ThreadingHTTPServer(LISTEN, H).serve_forever()
