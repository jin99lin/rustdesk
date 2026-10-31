"""Adoluna b79: device facts + signed command queue riding on /api/heartbeat.

Heartbeat request (new clients): adoluna_cmd_n (last executed n), adoluna_ack [{n,ok,msg}], adoluna_dev {...}.
Heartbeat response: adoluna_cmd [{n,k,p,e,sig}], sig = ed25519(TKEY) over
"adoluna-cmd-v1\n{id}\n{n}\n{k}\n{p}\n{e}".  n is the global row id (monotonic per device too).
Secrets: only the per-device one-time-code HMAC secret is stored (needed to compute codes);
unlock codes are shown once to the admin and only sha256(salt+code) goes to the device."""
import base64, hashlib, hmac, json, re, secrets, time

KINDS = {"ping", "info", "reboot", "install", "update", "kiosk_lock", "kiosk_unlock", "policy",
         "unlock_code", "otp_secret"}
TTL = {"reboot": 3600}
DEFAULT_TTL = 7 * 86400
SHA = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """
  create table if not exists dev_info(peer_id text primary key, info text, ts integer);
  create table if not exists dev_cmds(id integer primary key autoincrement, peer_id text not null, k text not null,
    p text not null, e integer not null, created_at integer, created_by text, delivered_at integer,
    acked_at integer, ok integer, msg text);
  create index if not exists dev_cmds_peer on dev_cmds(peer_id, id);
  create table if not exists dev_secrets(peer_id text primary key, otp_k text, ts integer);
  create table if not exists releases(platform text primary key, url text, sha256 text, label text, auto integer,
    ts integer, by text);
  create table if not exists dev_rel(peer_id text, platform text, rel_ts integer, primary key(peer_id, platform));
"""

def init(c):
    c.executescript(SCHEMA)

def msg(dev, n, k, p, e):
    return f"adoluna-cmd-v1\n{dev}\n{n}\n{k}\n{p}\n{e}"

def platform_of(info):
    if not isinstance(info, dict):
        return ""
    if "sdk" in info or str(info.get("platform", "")).lower() == "android":
        return "android"
    p = str(info.get("platform", "")).lower()
    return {"windows": "windows", "macos": "macos", "mac os": "macos", "linux": "linux"}.get(p, p)

def validate(k, p):
    """Returns error string or None. p is a dict."""
    if k not in KINDS:
        return "unknown command"
    if not isinstance(p, dict):
        return "bad payload"
    if k in ("install", "update"):
        u = str(p.get("url", ""))
        if not re.match(r"^https?://", u):
            return "url must be http(s)"
        if not SHA.match(str(p.get("sha256", "")).lower()):
            return "sha256 (64 hex) required"
        p["sha256"] = p["sha256"].lower()
    if k == "kiosk_lock" and not (p.get("pkg") or p.get("app")):
        return "pkg (Android) or app (Windows AUMID / path) required"
    if k == "policy" and "brightness" in p:
        try:
            b = int(p["brightness"])
        except Exception:
            return "brightness must be -1..255"
        if not -1 <= b <= 255:
            return "brightness must be -1..255"
    return None

def capability_error(c, dev, k):
    """Refuse actions the device reported it can't do (Win7/8, Home kiosk) instead of failing on the device."""
    r = c.execute("select info from dev_info where peer_id=?", (dev,)).fetchone()
    try:
        cap = (json.loads(r[0]) if r else {}).get("cap")
    except Exception:
        cap = None
    if not isinstance(cap, dict):
        return None
    if k == "install" and not cap.get("install"):
        return "push install not supported: " + str(cap.get("reason") or "")
    if k == "kiosk_lock" and cap.get("kiosk") == "none":
        return "single-app lock not supported: " + str(cap.get("reason") or "")
    if k in ("update", "reboot") and cap.get("reason") == "os_unsupported":
        return "not supported on this Windows version"
    return None

def queue(c, dev, k, p, by, now=None):
    now = int(now or time.time())
    ps = json.dumps(p, separators=(",", ":"), sort_keys=True)
    cur = c.execute("insert into dev_cmds(peer_id,k,p,e,created_at,created_by) values(?,?,?,?,?,?)",
                    (dev, k, ps, now + TTL.get(k, DEFAULT_TTL), now, by))
    return cur.lastrowid

def new_unlock_code(c, dev, by):
    code = "%06d" % secrets.randbelow(10**6)
    salt = secrets.token_hex(8)
    n = queue(c, dev, "unlock_code", {"h": hashlib.sha256((salt + code).encode()).hexdigest(), "s": salt}, by)
    return code, n

def new_otp_secret(c, dev, by):
    k = secrets.token_hex(20)
    c.execute("insert or replace into dev_secrets(peer_id,otp_k,ts) values(?,?,?)", (dev, k, int(time.time())))
    return queue(c, dev, "otp_secret", {"k": k}, by)

def otp(hexkey, dev, step):
    d = hmac.new(bytes.fromhex(hexkey), f"adoluna-otp-v1:{dev}:{step}".encode(), hashlib.sha256).digest()
    v = ((d[0] & 0x7f) << 24) | (d[1] << 16) | (d[2] << 8) | d[3]
    return "%08d" % (v % 100_000_000)

def current_otp(c, dev, now=None):
    r = c.execute("select otp_k from dev_secrets where peer_id=?", (dev,)).fetchone()
    if not r or not r[0]:
        return None
    now = int(now or time.time())
    return {"otp": otp(r[0], dev, now // 600), "valid_until": (now // 600 + 2) * 600}

def on_heartbeat(c, key, q, now=None):
    """Store facts/acks; return list of signed commands to deliver (may be empty)."""
    now = int(now or time.time())
    dev = str(q.get("id", ""))
    if not dev:
        return []
    info = q.get("adoluna_dev")
    if isinstance(info, dict):
        c.execute("insert or replace into dev_info(peer_id,info,ts) values(?,?,?)", (dev, json.dumps(info)[:8000], now))
    for a in q.get("adoluna_ack") or []:
        try:
            c.execute("update dev_cmds set acked_at=?, ok=?, msg=? where id=? and peer_id=?",
                      (now, 1 if a.get("ok") else 0, str(a.get("msg", ""))[:500], int(a["n"]), dev))
        except Exception:
            pass
    last = int(q.get("adoluna_cmd_n") or 0)
    # auto-update release policy (once per device per release)
    plat = platform_of(info) if isinstance(info, dict) else ""
    if plat:
        r = c.execute("select url, sha256, label, ts from releases where platform=? and auto=1", (plat,)).fetchone()
        if r:
            seen = c.execute("select rel_ts from dev_rel where peer_id=? and platform=?", (dev, plat)).fetchone()
            if not seen or seen[0] < r[3]:
                c.execute("insert or replace into dev_rel(peer_id,platform,rel_ts) values(?,?,?)", (dev, plat, r[3]))
                queue(c, dev, "update", {"url": r[0], "sha256": r[1], "label": r[2] or "", "auto": True}, "release", now)
    rows = c.execute("select id,k,p,e from dev_cmds where peer_id=? and id>? and acked_at is null and e>? order by id limit 10",
                     (dev, last, now)).fetchall()
    out = []
    for n, k, p, e in rows:
        sig = base64.b64encode(key.sign(msg(dev, n, k, p, e).encode())).decode()
        out.append({"n": n, "k": k, "p": p, "e": e, "sig": sig})
        c.execute("update dev_cmds set delivered_at=coalesce(delivered_at,?) where id=?", (now, n))
    return out

def os_label(i):
    w = i.get("win") if isinstance(i, dict) else None
    if isinstance(w, dict) and w.get("major"):
        b = str(w.get("build") or "") + ("." + str(w["ubr"]) if w.get("ubr") else "")
        return " ".join(x for x in ("Windows " + w["major"], w.get("edition") or "", w.get("display_version") or "",
                                     "(" + b + ")" if b else "") if x)
    return str((i or {}).get("os") or "")

def summary(c):
    """Badge data for the device list: {peer_id: {...}}."""
    out = {}
    for pid, info, ts in c.execute("select peer_id, info, ts from dev_info"):
        try:
            i = json.loads(info)
        except Exception:
            i = {}
        out[pid] = {"do": bool(i.get("is_owner")), "kiosk": bool(i.get("kiosk")), "platform": platform_of(i),
                    "kiosk_capable": bool(i.get("kiosk_capable")), "os_label": os_label(i),
                    "cap": i.get("cap") if isinstance(i.get("cap"), dict) else None, "ts": ts}
    return out

def detail(c, dev):
    r = c.execute("select info, ts from dev_info where peer_id=?", (dev,)).fetchone()
    cmds = [dict(zip(("n", "k", "p", "e", "created_at", "created_by", "delivered_at", "acked_at", "ok", "msg"), x))
            for x in c.execute("select id,k,p,e,created_at,created_by,delivered_at,acked_at,ok,msg from dev_cmds "
                               "where peer_id=? order by id desc limit 30", (dev,))]
    for x in cmds:  # never echo secrets back to the UI
        if x["k"] in ("otp_secret", "unlock_code"):
            x["p"] = "{}"
    has_otp = bool(c.execute("select 1 from dev_secrets where peer_id=?", (dev,)).fetchone())
    return {"id": dev, "info": json.loads(r[0]) if r else None, "ts": r[1] if r else None, "cmds": cmds, "otp": has_otp}

def set_release(c, platform, url, sha, label, auto, by):
    c.execute("insert or replace into releases(platform,url,sha256,label,auto,ts,by) values(?,?,?,?,?,?,?)",
              (platform, url, sha, label, 1 if auto else 0, int(time.time()), by))

def releases(c):
    return [dict(zip(("platform", "url", "sha256", "label", "auto", "ts", "by"), r))
            for r in c.execute("select platform,url,sha256,label,auto,ts,by from releases")]
