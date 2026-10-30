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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts as ACC

LISTEN = (os.environ.get("PW_LISTEN", "10.20.26.62"), int(os.environ.get("PW_PORT", "21114")))
UP = (os.environ.get("PW_UP_HOST", "127.0.0.1"), int(os.environ.get("PW_UP_PORT", "21124")))
DIR = os.environ.get("PW_DIR", "/opt/rustdesk/pwpush")
DB = os.environ.get("PW_DB", "/opt/rustdesk/api-data/rustdeskapi.db")
STATE = os.path.join(DIR, "state.json")
HERE = os.path.dirname(os.path.abspath(__file__))
KEY = Ed25519PrivateKey.from_private_bytes(open(os.path.join(DIR, "signing.key"), "rb").read())
LOCK = threading.Lock()
PEERUI = os.environ.get("PW_PEERUI", "1") == "1"  # set PW_PEERUI=0 to disable the injected device-list UI
UNHIDE = [(b'meta:{title:"UserManage",hide:!0,', b'meta:{title:"UserManage",'),
          (b'meta:{title:"GroupManage",hide:!0,', b'meta:{title:"GroupManage",'),
          (b'meta:{title:"OauthManage",hide:!0,', b'meta:{title:"OauthManage",')]
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
        ent = self.command == "GET" and self.path.split("?")[0].startswith("/_admin/static/entry/index-") and self.path.split("?")[0].endswith(".js")
        if idx or ent:  # we rewrite the admin index: ask upstream for a plain, uncached copy
            for k in list(hdr):
                if k.lower() in ("accept-encoding", "if-none-match", "if-modified-since"):
                    del hdr[k]
        path0 = self.path.split("?")[0]
        cur = None
        if path0.startswith("/api/admin/") and self.headers.get("api-token"):
            if path0 not in ACC.ALLOW_FORCED and ACC.load().get("must_change"):
                cur = ACC.uid_for_token(self.headers.get("api-token"))
                if cur and ACC.must_change(cur[0]):
                    return self.send(200, json.dumps({"code": 101, "message": "请先修改临时密码 / Change your temporary password first",
                                                      "data": None, "adoluna_must_change": True}).encode())
            if path0 == "/api/admin/user/changeCurPwd" and self.command == "POST":
                try:
                    q = json.loads(body or b"{}")
                except Exception:
                    q = {}
                zh = not str(self.headers.get("Accept-Language", "zh")).lower().startswith("en")
                bad = ACC.pw_problem(q.get("new_password"), zh)
                if bad:
                    return self.send(200, json.dumps({"code": 101, "message": bad, "data": None}, ensure_ascii=False).encode())
                cur = ACC.uid_for_token(self.headers.get("api-token"))
        try:
            r, data = self.upstream(self.command, self.path, body, hdr)
        except Exception as e:
            return self.send(502, b'{"error":"upstream"}')
        if path0 == "/api/admin/user/changeCurPwd" and cur and r.status == 200:
            try:
                if json.loads(data).get("code") == 0:
                    ACC.set_must(cur[0], False)
            except Exception:
                pass
        if self.command == "POST" and self.path.split("?")[0] == "/api/heartbeat" and r.status == 200:
            data = self.add_pw(body, data)
        extra = [(k, v) for k, v in r.getheaders() if k.lower() not in HOP and k.lower() != "content-type"]
        if idx and r.status == 200 and b"</head>" in data and PEERUI:
            data = data.replace(b"<head>", b"<head><script>try{if(!localStorage.getItem('adoluna_lang_init')){localStorage.setItem('lang','zh-CN');"
                                b"localStorage.setItem('adoluna_lang_init','1')}}catch(e){}</script>", 1)
            data = data.replace(b"</head>", b'<link rel="stylesheet" href="/_admin/adoluna-peerui.css?v=3">'
                                b'<script src="/_admin/adoluna-peerui.js?v=3" defer></script></head>', 1)
            extra = [(k, v) for k, v in extra if k.lower() not in ("etag", "last-modified", "cache-control")] + [("Cache-Control", "no-store")]
        if ent and r.status == 200 and PEERUI:  # admin build hides these menus; admins need Users (email, reset password)
            for a, b in UNHIDE:
                data = data.replace(a, b)
            extra = [(k, v) for k, v in extra if k.lower() not in ("etag", "last-modified", "cache-control")] + [("Cache-Control", "no-cache")]
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
        if path.startswith("/_admin/adoluna-api/acct/") or path in ("/_admin/adoluna-forgot", "/_admin/adoluna-reset"):
            return self.acct(path)
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

    def body_json(self):
        n = min(int(self.headers.get("Content-Length") or 0), 65536)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def jout(self, code, obj):
        return self.send(code, json.dumps(obj, ensure_ascii=False).encode(), extra=[("Cache-Control", "no-store")])

    def acct(self, path):
        ip = self.client_address[0]
        if not private(ip) or any(self.headers.get(h) and not all(private(x) for x in self.headers[h].split(","))
                                  for h in ("X-Forwarded-For", "X-Real-IP")):
            return self.send(403, b"forbidden", "text/plain")
        if path in ("/_admin/adoluna-forgot", "/_admin/adoluna-reset"):
            return self.send(200, open(os.path.join(HERE, "account.html"), "rb").read(), "text/html; charset=utf-8",
                             [("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY")])
        act = path.rsplit("/", 1)[1]
        if act == "me":
            u = ACC.uid_for_token(self.headers.get("api-token", ""))
            return self.jout(200, {"must_change": bool(u and ACC.must_change(u[0])), "is_admin": bool(u and u[1])} if u else {"error": "login"})
        if self.command != "POST":
            return self.jout(405, {"error": "POST"})
        q = self.body_json(); zh = not str(q.get("lang", "zh")).lower().startswith("en")
        if act == "forgot":
            code = ACC.request_reset(str(q.get("email", "")), ip, q.get("lang"))
            if code == 429:
                return self.jout(429, {"error": "请求太多次，请 15 分钟后再试" if zh else "Too many requests. Try again in 15 minutes."})
            return self.jout(200, {"message": ACC.NEUTRAL["zh" if zh else "en"]})
        if act == "check":
            return self.jout(200, {"valid": ACC.find_reset(str(q.get("t", ""))) is not None})
        if act == "reset":
            if q.get("new") != q.get("confirm"):
                return self.jout(400, {"error": "两次输入的新密码不一致" if zh else "The two new passwords don't match."})
            code, msg = ACC.do_reset(str(q.get("t", "")), q.get("code"), q.get("new"), zh, ip)
            return self.jout(code, {"message": msg} if code == 200 else {"error": msg})
        if act == "admin-reset":
            u = ACC.uid_for_token(self.headers.get("api-token", ""))
            if not u or not u[1]:
                return self.jout(403, {"error": "只有管理员可以重设密码" if zh else "Only an administrator can reset passwords."})
            try:
                target = int(q.get("user_id"))
            except Exception:
                return self.jout(400, {"error": "user_id"})
            if target == u[0]:
                return self.jout(400, {"error": "不能重设自己的密码，请到「个人信息」修改" if zh else "You can't reset your own password here. Use My Info."})
            tmp = ACC.temp_password()
            try:
                r, d = self.upstream("POST", "/api/admin/user/changePwd", json.dumps({"id": target, "password": tmp}),
                                     {"api-token": self.headers.get("api-token", ""), "Content-Type": "application/json"})
                ok = r.status == 200 and json.loads(d).get("code") == 0
            except Exception:
                ok = False
            if not ok:
                return self.jout(400, {"error": "重设失败" if zh else "Reset failed."})
            ACC.set_must(target, True)
            sys.stderr.write("admin-reset user %s by %s from %s\n" % (target, u[0], ip))
            return self.jout(200, {"temp_password": tmp})
        return self.jout(404, {})

    def handle_any(self):
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
