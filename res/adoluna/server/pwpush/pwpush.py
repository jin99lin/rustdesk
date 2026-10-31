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
import merchants as M

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
UNHIDE = [(b'meta:{title:"UserManage",hide:!0,', b'meta:{title:"UserManage",'),
          (b'meta:{title:"GroupManage",hide:!0,', b'meta:{title:"GroupManage",'),
          (b'meta:{title:"OauthManage",hide:!0,', b'meta:{title:"OauthManage",')]
import re
# Jay 10/29 batch 2: compact action columns, nowrap date columns (rewrite admin page chunks on the fly)
_ACT = re.compile(rb'(label:[\w$]+\([\w$]+\)\("Actions"\),align:"center")([^{}]*)\}')
_DATE = re.compile(rb'(label:[\w$]+\([\w$]+\)\("(?:CreatedAt|UpdatedAt|LastOnlineTime|CloseTime|ExpireTime)"\)(?:,prop:"\w+")?,align:"center")([^{}]*)\}')
def _strip(rest):
    rest = re.sub(rb',(?:width|"min-width"|"class-name"):"[^"]*"', b"", rest)
    return rest
def _act(m):
    w = re.search(rb'(?<!-)width:"(\d+)"', m.group(2))
    nw = max(130, min(300, int(int(w.group(1)) * 0.5))) if w else 150
    rest = re.sub(rb',fixed:"right"', b"", _strip(m.group(2)))
    return m.group(1) + b',width:"%d","class-name":"table-actions adp-act",fixed:"right"' % nw + rest + b"}"
def _date(m):
    return m.group(1) + b',width:"180","class-name":"adp-date"' + _strip(m.group(2)) + b"}"
_NAME = re.compile(rb'(label:[\w$]+\([\w$]+\)\("(?:Name|AddressBookName|Username|Nickname|Email|Hostname|Alias|Remark|Group|Owner)"\)(?:,prop:"\w+")?,align:"center")([^{}]*)\}')
def _name(m):
    if re.search(rb'(?<!-)width:"', m.group(2)):
        return m.group(0)
    return m.group(1) + b',"min-width":"130"' + re.sub(rb',"min-width":"[^"]*"', b"", m.group(2)) + b"}"
def fix_chunk(data):
    return _NAME.sub(_name, _DATE.sub(_date, _ACT.sub(_act, data)))
CHUNK_V = b"adp2"
_ROOTS = None
def chunk_roots(fetch):
    """page chunks that no other chunk imports: safe to cache-bust from the entry without loading a module twice"""
    global _ROOTS
    if _ROOTS is None:
        try:
            names = set(re.findall(rb'\.\./chunk/([\w-]+\.js)', fetch("ENTRY")))
            imported = set()
            for n in names:
                body = fetch(n)
                imported |= set(re.findall(rb'\./([\w-]+\.js)', body)) - {n}
            _ROOTS = {n for n in names if not n.startswith(b"_") and n not in imported}
        except Exception:
            return set()
    return _ROOTS

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

AB_WRITE = {"/api/admin/address_book/create", "/api/admin/address_book/batchCreate", "/api/admin/address_book/update",
            "/api/admin/my/address_book/create", "/api/admin/my/address_book/update"}

def ab_id_problem(path, body, zh):
    """Jay 10/29: address-book entries must point at a device that exists in our system.
    Returns an error message, or None. Fails open if the db can't be read."""
    try:
        q = json.loads(body or b"{}")
    except Exception:
        return None
    dev = str(q.get("id", "")).strip()
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        try:
            if c.execute("select 1 from peers where id=? limit 1", (dev,)).fetchone():
                return None
            if path.endswith("/update") and q.get("row_id"):  # old entry kept its id: don't block editing it
                r = c.execute("select id from address_books where row_id=?", (int(q["row_id"]),)).fetchone()
                if r and str(r[0]) == dev:
                    return None
        finally:
            c.close()
    except Exception:
        return None
    return ("设备 ID %s 不在系统设备列表里，请从已有设备中选择" % dev) if zh else \
        ("Device ID %s is not in the device list. Pick an existing device." % dev)

def fix_platform(body):
    """Jay 10/30: the admin "Add" stored the whole OS string ("Windows 10 Pro - 10 (19045)") as platform, so the
    client showed no icon.  The client expects Windows / Mac OS / Linux / Android."""
    try:
        q = json.loads(body or b"{}")
        if isinstance(q, dict) and q.get("platform"):
            q["platform"] = M.platform(q["platform"]); return json.dumps(q, ensure_ascii=False).encode()
    except Exception:
        pass
    return body

def device_index():
    """Compact list of every device with its store (device group) and address-book tags, for the admin search box."""
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
    try:
        tags = {}
        for i, t in c.execute("select id, tags from address_books"):
            try:
                for x in json.loads(t or "[]") or []:
                    tags.setdefault(str(i), set()).add(str(x))
            except Exception:
                pass
        out = []
        for i, h, a, o, lo, g in c.execute("select p.id, p.hostname, p.alias, p.os, p.last_online_time, coalesce(g.name,'') "
                                         "from peers p left join device_groups g on g.id=p.group_id"):
            out.append({"id": str(i), "hostname": h, "alias": a, "os": o, "last_online_time": lo or 0, "group": g,
                        "tags": sorted(tags.get(str(i), ()))})
        return out
    finally:
        c.close()

# ---- passwordless: grants / device roles / audit ----
def acl():
    c = sqlite3.connect(ACL_DB, timeout=5, factory=M.ClosingConn)  # closes on with-exit (fd leak fix, see merchants.py)
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
    if M.granted(uid, bool(is_admin), peer_id):  # merchant model: positions covering the device's store
        return True
    pg = peer_group(peer_id)
    with acl() as c:
        for sk, sv, tk, tv in c.execute("select subj_kind, subj, tgt_kind, tgt from grants"):
            subj_ok = (sk == "user" and sv == str(uid)) or (sk == "group" and sv == str(gid))
            tgt_ok = (tk == "peer" and tv == peer_id) or (tk == "dgroup" and pg and tv == pg)
            if subj_ok and tgt_ok:
                return True
    return False

# ---- password push (batch 4, Jay 10/30) ----
# state.json:  seq      highest version ever issued (global and per-device share ONE counter, so a device's
#                       applied version only ever goes up whichever source it came from)
#              v/payload the batch ("global") password: signed {v,h,s,sig}; h = b64 sha256(pw+salt)
#              scope    {"all":bool, "peers":[id], "dgroups":[gid], "abs":["c<collection id>" | "u<user id>"]}
#              custom   {peer_id: {v,h,s,sig,at,by}}   device's own password; batch pushes skip it
#              reissue  {peer_id: {v,sig}}  batch password re-signed with a fresh version for one device
#                       (after its custom password is cleared, its applied version may be above the batch one)
# Only salted hashes are stored.  Clients (build 76+) verify sig over "adoluna-pw-v1\nv\nh\ns" and
# apply when their adoluna_pw_ver < v.
def _salt():
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))

def _sign(v, h, salt):
    return base64.b64encode(KEY.sign(f"adoluna-pw-v1\n{v}\n{h}\n{salt}".encode())).decode()

def next_v(st):
    v = max(int(st.get("seq", 0)), int(st.get("v", 0)),
            *[int(x.get("v", 0)) for x in st.get("custom", {}).values()],
            *[int(x.get("v", 0)) for x in st.get("reissue", {}).values()]) + 1
    st["seq"] = v
    return v

def pw_payload(st, pw):
    salt = _salt(); h = base64.b64encode(sha256((pw + salt).encode()).digest()).decode()
    v = next_v(st)
    return {"v": v, "h": h, "s": salt, "sig": _sign(v, h, salt)}

def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")

def scope_of(st):
    sc = st.get("scope")
    if not sc:  # pre-batch-4 state: "targets" list, empty = every device
        t = [str(x) for x in st.get("targets", [])]
        sc = {"all": not t, "peers": t, "dgroups": [], "abs": []}
    return {"all": bool(sc.get("all")), "peers": [str(x) for x in sc.get("peers", [])],
            "dgroups": [str(x) for x in sc.get("dgroups", [])], "abs": [str(x) for x in sc.get("abs", [])]}

def clean_scope(q):
    ids = lambda k, ok: sorted({str(x).strip() for x in (q.get(k) or []) if ok(str(x).strip())})[:2000]
    return {"all": bool(q.get("all")), "peers": ids("peers", str.isdigit), "dgroups": ids("dgroups", str.isdigit),
            "abs": ids("abs", lambda x: len(x) > 1 and x[0] in "cu" and x[1:].isdigit())}

def set_pw(st, pw, by):
    st["payload"] = pw_payload(st, pw); st["v"] = st["payload"]["v"]
    st["reissue"] = {}  # older re-issues are superseded by the new batch password
    st.update(updated_at=now_s(), updated_by=by)

def set_custom(st, peer, pw, by):
    p = pw_payload(st, pw); p.update(at=now_s(), by=by)
    st.setdefault("custom", {})[peer] = p
    st.get("reissue", {}).pop(peer, None)
    return p["v"]

def clear_custom(st, peer):
    """Device goes back to the batch password.  Re-sign it with a fresh version so a device whose applied
    (custom) version is above the batch version still takes it."""
    if st.get("custom", {}).pop(peer, None) is None:
        return None
    return reissue(st, peer)

def reissue(st, peer):
    """re-sign the device's base password (merchant or batch) with a fresh version so it applies even if the
    device's applied version is higher (after a custom password, or after moving between merchants)."""
    p, src = base_payload(st, peer)
    if not p:
        return None
    v = next_v(st)
    st.setdefault("reissue", {})[peer] = {"v": v, "sig": _sign(v, p["h"], p["s"]), "src": src}
    return v

def base_payload(st, dev):
    """(payload, source) without per-device passwords: the merchant's fixed password wins over the batch scope"""
    mid = M.peer_merchant(dev)
    mp = st.get("merchants", {}).get(str(mid)) if mid else None
    if mp:
        return mp, "m%s" % mid
    p = st.get("payload")
    if p and in_scope(st, dev):
        return p, "batch"
    return None, None

def set_merchant_pw(st, mid, pw, by):
    p = pw_payload(st, pw); p.update(at=now_s(), by=by)
    st.setdefault("merchants", {})[str(mid)] = p
    st["reissue"] = {k: x for k, x in st.get("reissue", {}).items() if x.get("src") != "m%s" % mid}
    return p["v"]

_SCOPE_CACHE = {"k": None, "t": 0, "set": set()}
def scope_peers(sc):
    """Resolve a scope to a set of device IDs (union of devices, device groups, address books)."""
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
    try:
        if sc["all"]:
            return {str(r[0]) for r in c.execute("select id from peers")}
        out = set(sc["peers"])
        if sc["dgroups"]:
            q = ",".join("?" * len(sc["dgroups"]))
            out |= {str(r[0]) for r in c.execute(f"select id from peers where group_id in ({q})", [int(x) for x in sc["dgroups"]])}
        for a in sc["abs"]:
            if a[0] == "c":
                rows = c.execute("select id from address_books where collection_id=?", (int(a[1:]),))
            else:
                rows = c.execute("select id from address_books where collection_id=0 and user_id=?", (int(a[1:]),))
            out |= {str(r[0]) for r in rows}
        return out
    finally:
        c.close()

def in_scope(st, dev):
    sc = scope_of(st)
    if sc["all"]:
        return True
    if dev in sc["peers"]:
        return True
    if not sc["dgroups"] and not sc["abs"]:
        return False
    k = json.dumps(sc, sort_keys=True); now = time.time()
    if _SCOPE_CACHE["k"] != k or now - _SCOPE_CACHE["t"] > 30:  # heartbeats are frequent: re-resolve at most every 30 s
        try:
            _SCOPE_CACHE.update(k=k, t=now, set=scope_peers(sc))
        except Exception:
            return False
    return dev in _SCOPE_CACHE["set"]

def effective(st, dev):
    """The signed payload this device should end up on, or None."""
    c = st.get("custom", {}).get(dev)
    if c:
        return {k: c[k] for k in ("v", "h", "s", "sig")}
    p, src = base_payload(st, dev)
    if not p:
        return None
    r = st.get("reissue", {}).get(dev)
    if r and r.get("src", "batch") == src and int(r["v"]) > int(p["v"]):
        return {"v": r["v"], "h": p["h"], "s": p["s"], "sig": r["sig"]}
    return dict(p)

def pw_options():
    """Everything the target picker needs: devices (with group id and address-book keys), device groups, address books."""
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
    try:
        users = {r[0]: r[1] or r[2] for r in c.execute("select id, nickname, username from users")}
        abk = {}
        for i, col, uid in c.execute("select id, collection_id, user_id from address_books"):
            abk.setdefault(str(i), set()).add("c%d" % col if col else "u%d" % uid)
        devs = [{"id": str(i), "name": a or h or "", "group_id": str(g or ""), "group": gn or "", "os": o or "",
                 "last_online_time": lo or 0, "abs": sorted(abk.get(str(i), ()))}
                for i, h, a, o, lo, g, gn in c.execute("select p.id, p.hostname, p.alias, p.os, p.last_online_time, p.group_id, g.name "
                                                       "from peers p left join device_groups g on g.id=p.group_id order by p.id")]
        dg = [{"id": str(i), "name": n} for i, n in c.execute("select id, name from device_groups order by name")]
        abs_ = [{"key": "c%d" % i, "name": n, "owner": users.get(u, "#%s" % u)} for i, u, n in
                c.execute("select id, user_id, name from address_book_collections order by name")]
        abs_ += [{"key": "u%d" % u, "name": "", "owner": users.get(u, "#%s" % u), "default": True}
                 for (u,) in c.execute("select distinct user_id from address_books where collection_id=0 order by user_id")]
        return {"devices": devs, "dgroups": dg, "abs": abs_}
    finally:
        c.close()

SEEN = {}  # peer -> applied version last reported in a heartbeat (memory + adoluna.db)
def seen(dev, ver):
    if SEEN.get(dev) == ver:
        return
    SEEN[dev] = ver
    try:
        with acl() as c:
            c.execute("create table if not exists pw_seen(peer_id text primary key, ver integer, ts integer)")
            c.execute("insert or replace into pw_seen values(?,?,?)", (dev, ver, int(time.time())))
    except Exception:
        pass

def seen_all():
    try:
        with acl() as c:
            c.execute("create table if not exists pw_seen(peer_id text primary key, ver integer, ts integer)")
            return {r[0]: {"ver": r[1], "ts": r[2]} for r in c.execute("select peer_id, ver, ts from pw_seen")}
    except Exception:
        return {}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = int(os.environ.get("PW_SOCK_TIMEOUT", "30"))  # idle/slow client socket timeout (keep-alive, slow body)
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
        chk = self.command == "GET" and PEERUI and self.path.split("?")[0].startswith("/_admin/static/chunk/") and self.path.split("?")[0].endswith(".js")
        if idx or ent or chk:  # we rewrite the admin index: ask upstream for a plain, uncached copy
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
            if path0 in AB_WRITE and self.command == "POST" and ACC.uid_for_token(self.headers.get("api-token")):
                bad = ab_id_problem(path0, body, not str(self.headers.get("Accept-Language", "zh")).lower().startswith("en"))
                if bad:
                    return self.send(200, json.dumps({"code": 101, "message": bad, "data": None}, ensure_ascii=False).encode())
                body = fix_platform(body)
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
            data = self.add_cfg(body, data)
        extra = [(k, v) for k, v in r.getheaders() if k.lower() not in HOP and k.lower() != "content-type"]
        if idx and r.status == 200 and b"</head>" in data and PEERUI:
            data = data.replace(b"<head>", b"<head><script>try{if(!localStorage.getItem('adoluna_lang_init')){localStorage.setItem('lang','zh-CN');"
                                b"localStorage.setItem('adoluna_lang_init','1')}}catch(e){}</script>", 1)
            data = data.replace(b"</head>", b'<link rel="stylesheet" href="/_admin/adoluna-peerui.css?v=9">'
                                b'<script src="/_admin/adoluna-peerui.js?v=9" defer></script>'
                                b'<link rel="stylesheet" href="/_admin/adoluna-merchui.css?v=6"><script src="/_admin/adoluna-merchui.js?v=6" defer></script></head>', 1)
            extra = [(k, v) for k, v in extra if k.lower() not in ("etag", "last-modified", "cache-control")] + [("Cache-Control", "no-store")]
        if ent and r.status == 200 and PEERUI:  # admin build hides these menus; admins need Users (email, reset password)
            for a, b in UNHIDE:
                data = data.replace(a, b)
            def _fetch(n, _p=self.path):
                u = _p.split("?")[0] if n == "ENTRY" else "/_admin/static/chunk/" + n.decode()
                return self.upstream("GET", u, None, {"Host": hdr["Host"]})[1]
            roots = chunk_roots(_fetch)
            data = re.sub(rb'\.\./chunk/([\w-]+\.js)(?=")', lambda m: m.group(0) + (b"?v=" + CHUNK_V if m.group(1) in roots else b""), data)
            extra = [(k, v) for k, v in extra if k.lower() not in ("etag", "last-modified", "cache-control")] + [("Cache-Control", "no-cache")]
        if chk and r.status == 200:
            nd = fix_chunk(data)
            if nd != data:
                data = nd
                extra = [(k, v) for k, v in extra if k.lower() not in ("etag", "last-modified", "cache-control")] + [("Cache-Control", "no-cache")]
        self.send(r.status, data, r.getheader("Content-Type", "application/octet-stream"), extra)

    def add_pw(self, req, data):
        try:
            q = json.loads(req or b"{}")
            if "adoluna_pw_ver" not in q:
                return data  # old builds don't send the field: never hand them the hash
            dev = str(q.get("id", "")); have = int(q.get("adoluna_pw_ver") or 0)
            seen(dev, have)
            p = effective(load(), dev)
            if not p or have >= int(p["v"]) or not known_peer(dev):
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
            return self.send(403, json.dumps({"error": self.tr("需要在内网用管理员账号登录后台", "Sign in as an administrator on the internal network")}).encode())
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
                return self.send(400, json.dumps({"error": self.tr("设备 ID 无效", "Invalid device ID")}).encode())
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
                    g = c.execute("select subj_kind, subj, tgt_kind, tgt from grants where id=?", (int(q["delete"]),)).fetchone()
                    c.execute("delete from grants where id=?", (int(q["delete"]),))
                audit("(后台)", "", g[3] if g and g[2] == "peer" else "", by,
                      f"删除授权 #{int(q['delete'])}" + (f" {g[0]}:{g[1]} -> {g[2]}:{g[3]}" if g else ""))
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
            return self.send(403, json.dumps({"error": self.tr("需要在内网用管理员账号登录后台", "Sign in as an administrator on the internal network")}).encode())
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
        st = load(); sc = scope_of(st); sn = seen_all()
        try:
            tgt = scope_peers(sc)
        except Exception:
            tgt = set()
        cust = {k: {"v": x["v"], "at": x.get("at"), "by": x.get("by")} for k, x in st.get("custom", {}).items()}
        devs = {}
        try:
            mdev = {str(i) for i in M.peer_merchant_all() if st.get("merchants", {}).get(str(M.peer_merchant(i)))}
        except Exception:
            mdev = set()
        for d in tgt | set(cust) | mdev:
            e = effective(st, d)
            devs[d] = {"custom": d in cust, "want": e["v"] if e else 0, "have": (sn.get(d) or {}).get("ver")}
        return {"version": st.get("v", 0), "updated_at": st.get("updated_at"), "updated_by": st.get("updated_by"),
                "scope": sc, "targets": sc["peers"] if not sc["all"] else [], "custom": cust,
                "devices": devs, "target_count": len(tgt - set(cust)), "skipped_custom": sorted(tgt & set(cust))}

    def tr(self, zh, en):
        return en if str(self.headers.get("Accept-Language", "zh")).lower().startswith("en") else zh

    def is_admin(self):
        u = ACC.uid_for_token(self.headers.get("api-token", ""))
        return bool(u and u[1]) and self.admin_ok()

    def admin(self):
        path = self.path.split("?")[0]
        if path in ("/_admin/adoluna-password", "/_admin/adoluna-password.html"):
            if not private(self.client_address[0]):
                return self.send(403, b"forbidden", "text/plain")
            return self.send(200, open(os.path.join(HERE, "password.html"), "rb").read(), "text/html; charset=utf-8",
                             [("Cache-Control", "no-store")])
        if path.startswith("/_admin/adoluna-api/m/"):
            return self.merch(path)
        if path in ("/_admin/adoluna-peerui.js", "/_admin/adoluna-peerui.css", "/_admin/adoluna-merchui.js", "/_admin/adoluna-merchui.css"):
            f = os.path.join(HERE, path.rsplit("-", 1)[1])
            ct = "application/javascript; charset=utf-8" if f.endswith(".js") else "text/css; charset=utf-8"
            return self.send(200, open(f, "rb").read(), ct, [("Cache-Control", "no-cache")])
        if path.startswith("/_admin/adoluna-api/acct/") or path in ("/_admin/adoluna-forgot", "/_admin/adoluna-reset"):
            return self.acct(path)
        if path == "/_admin/adoluna-api/devices":
            if not private(self.client_address[0]) or any(self.headers.get(h) and not all(private(x) for x in self.headers[h].split(","))
                                                          for h in ("X-Forwarded-For", "X-Real-IP")):
                return self.send(403, b'{"error":"forbidden"}')
            if not ACC.uid_for_token(self.headers.get("api-token", "")):
                return self.send(403, json.dumps({"error": "login"}).encode())
            try:
                return self.send(200, json.dumps({"list": device_index()}, ensure_ascii=False).encode(), extra=[("Cache-Control", "no-store")])
            except Exception:
                return self.send(503, b'{"error":"db busy"}')
        if path == "/_admin/adoluna-api/pw-options":
            if not self.is_admin():
                return self.send(403, json.dumps({"error": self.tr("需要在内网用管理员账号登录后台", "Sign in as an administrator on the internal network")}).encode())
            try:
                return self.send(200, json.dumps(pw_options(), ensure_ascii=False).encode(), extra=[("Cache-Control", "no-store")])
            except Exception:
                return self.send(503, b'{"error":"db busy"}')
        if path in ("/_admin/adoluna-api/reset", "/_admin/adoluna-api/device-pw"):
            zh = not str(self.headers.get("Accept-Language", "zh")).lower().startswith("en")
            if self.command != "POST" or not self.is_admin():
                return self.send(403, json.dumps({"error": "只有管理员可以设置设备密码" if zh else "Only an administrator can set device passwords."}).encode())
            q = self.body_json() or {}
            dev = str(q.get("id", "")).strip()
            if not dev.isdigit() or not known_peer(dev):
                return self.send(400, json.dumps({"error": "设备 ID 无效" if zh else "Invalid device ID"}).encode())
            with LOCK:
                st = load()
                if q.get("clear"):
                    v = clear_custom(st, dev); save(st)
                    sys.stderr.write("device-pw clear %s by %s\n" % (dev, self.client_ip()))
                    out = self.status(); out["note"] = "cleared"; out["reissued"] = v
                    return self.send(200, json.dumps(out).encode())
                pw = q.get("password")
                if not isinstance(pw, str) or not 8 <= len(pw) <= 64:
                    return self.send(400, json.dumps({"error": "密码长度需 8–64 位" if zh else "Password must be 8–64 characters"}).encode())
                if q.get("confirm") is not None and q.get("confirm") != pw:
                    return self.send(400, json.dumps({"error": "两次输入不一致" if zh else "The two passwords don't match"}).encode())
                v = set_custom(st, dev, pw, self.client_ip()); save(st)
            sys.stderr.write("device-pw set %s v%s by %s\n" % (dev, v, self.client_ip()))
            out = self.status(); out["note"] = "custom"
            return self.send(200, json.dumps(out).encode())
        if path in ("/_admin/adoluna-api/acl", "/_admin/adoluna-api/role", "/_admin/adoluna-api/grant"):
            return self.acl_admin(path)
        if path == "/_admin/adoluna-api/audit":
            return self.audit_list()
        if path != "/_admin/adoluna-api/password":
            return self.send(404, b"{}")
        if not (self.admin_ok() if self.command == "GET" else self.is_admin()):
            return self.send(403, json.dumps({"error": self.tr("需要在内网用管理员账号登录后台", "Sign in as an administrator on the internal network")}).encode())
        if self.command == "GET":
            return self.send(200, json.dumps(self.status()).encode())
        n = int(self.headers.get("Content-Length") or 0)
        try:
            q = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send(400, b'{"error":"bad json"}')
        with LOCK:
            st = load()
            if "scope" in q and isinstance(q["scope"], dict):
                st["scope"] = clean_scope(q["scope"]); st.pop("targets", None)
            elif "targets" in q:  # legacy page / CLI
                t = [str(x).strip() for x in q["targets"] if str(x).strip()][:2000]
                st["scope"] = {"all": not t, "peers": t, "dgroups": [], "abs": []}; st.pop("targets", None)
            pw = q.get("password")
            if pw is not None:
                if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 64:
                    return self.send(400, json.dumps({"error": self.tr("密码长度需 8–64 位", "Password must be 8–64 characters")}).encode())
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

    from merch_api import merch  # merchant pages API (merch_api.py)

    def health(self):
        """Watchdog probe: proves the accept loop answers and that we can still open files (the fd-leak hang)."""
        try:
            with acl() as c:
                c.execute("select 1").fetchone()
            ro = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
            try:
                ro.execute("select 1").fetchone()
            finally:
                ro.close()
            return self.send(200, b'{"ok":true}')
        except Exception as e:
            return self.send(503, json.dumps({"ok": False, "error": type(e).__name__}).encode())

    def handle_any(self):
        if self.path.split("?")[0] == "/_adoluna/health":
            return self.health()
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
        print({"v": st.get("v"), "updated_at": st.get("updated_at"), "scope": scope_of(st), "custom": sorted(st.get("custom", {}))}); return
    tg = lambda t: {"all": t == "all", "peers": [] if t == "all" else [x for x in t.split(",") if x], "dgroups": [], "abs": []}
    if a[0] == "targets":
        st["scope"] = tg(a[1]); st.pop("targets", None)
    elif a[0] == "set":
        if "--targets" in a:
            st["scope"] = tg(a[a.index("--targets") + 1]); st.pop("targets", None)
        pw = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass("新的固定密码: ")
        if sys.stdin.isatty() and getpass.getpass("再输入一次: ") != pw:
            sys.exit("两次输入不一致")
        if not 8 <= len(pw) <= 64:
            sys.exit("密码长度需 8–64 位")
        set_pw(st, pw, "cli")
    with LOCK:
        save(st)
    print({"v": st.get("v"), "updated_at": st.get("updated_at"), "scope": scope_of(st)})

if __name__ == "__main__" and len(sys.argv) > 1:
    cli(); sys.exit(0)

if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.request_queue_size = 128
    try:
        M.md().close(); M.reconcile()
    except Exception as e:
        sys.stderr.write("reconcile at start failed: %s\n" % e)
    M.loop(int(os.environ.get("PW_RECONCILE", "60")))
    ThreadingHTTPServer(LISTEN, H).serve_forever()
