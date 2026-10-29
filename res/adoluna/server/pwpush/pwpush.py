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
        try:
            r, data = self.upstream(self.command, self.path, body, hdr)
        except Exception as e:
            return self.send(502, b'{"error":"upstream"}')
        if self.command == "POST" and self.path.split("?")[0] == "/api/heartbeat" and r.status == 200:
            data = self.add_pw(body, data)
        extra = [(k, v) for k, v in r.getheaders() if k.lower() not in HOP and k.lower() != "content-type"]
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
