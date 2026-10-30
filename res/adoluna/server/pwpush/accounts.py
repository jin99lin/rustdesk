"""Adoluna Support admin accounts (Jay 2026-10-29), modelled on Adoluna Pay:
* change own password (built-in 个人信息 page or the forced-change dialog); min 8 chars enforced here
* temporary password: admin "Reset password" -> shown once, user must change it before using the admin
* forgot password: neutral answer, rate limited, one-time link + 6-digit code by email, 30 minutes
Only sha256 of tokens/codes is stored (accounts.json, 0600). New passwords are bcrypt-hashed like rustdesk-api."""
import hashlib, hmac, json, os, secrets, smtplib, sqlite3, ssl, threading, time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

DIR = os.environ.get("PW_DIR", "/opt/rustdesk/pwpush")
DB = os.environ.get("PW_DB", "/opt/rustdesk/api-data/rustdeskapi.db")
ACC = os.path.join(DIR, "accounts.json")
MAILCONF = os.path.join(DIR, "mail.env")
TTL, MAX_TRIES = 30 * 60, 5
LOCK = threading.Lock()
ALLOW_FORCED = {"/api/admin/user/current", "/api/admin/user/changeCurPwd", "/api/admin/user/logout",
                "/api/admin/login", "/api/admin/captcha", "/api/admin/login-options"}
NEUTRAL = {"zh": "如果这个邮箱属于某个后台账号，我们已经发出重设密码的邮件。链接只能用一次，30 分钟内有效。",
           "en": "If that email belongs to an account, we have sent a password reset email. The link works once, for 30 minutes."}

def _h(s): return hashlib.sha256(s.encode()).hexdigest()
def _now(): return int(time.time())

def load():
    try:
        return json.load(open(ACC))
    except FileNotFoundError:
        return {"must_change": {}, "resets": []}

def save(st):
    st["resets"] = [r for r in st.get("resets", []) if r["exp"] > _now() - 86400][-500:]
    tmp = ACC + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.chmod(tmp, 0o600); os.replace(tmp, ACC)

def mailconf():
    c = {}
    try:
        for line in open(MAILCONF):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1); c[k.strip()] = v
    except FileNotFoundError:
        pass
    return c

def db(rw=False):
    return sqlite3.connect(DB if rw else f"file:{DB}?mode=ro", uri=not rw, timeout=5)

def uid_for_token(tok):
    if not tok:
        return None
    try:
        c = db(); r = c.execute("select t.user_id,u.is_admin from user_tokens t join users u on u.id=t.user_id where t.token=? "
                                "and (t.expired_at=0 or t.expired_at>?) and u.status=1", (tok, _now())).fetchone(); c.close()
        return (int(r[0]), bool(r[1])) if r else None
    except Exception:
        return None

def must_change(uid):
    return str(uid) in load().get("must_change", {})

def set_must(uid, on):
    with LOCK:
        st = load(); m = st.setdefault("must_change", {})
        if on: m[str(uid)] = _now()
        else: m.pop(str(uid), None)
        save(st)

def pw_problem(pw, zh):
    if not isinstance(pw, str) or not 8 <= len(pw) <= 32:
        return "新密码需要 8–32 位" if zh else "The new password must be 8-32 characters."
    if pw.lower() == pw or pw.upper() == pw or not any(ch.isdigit() for ch in pw):
        return "新密码要同时有大写、小写字母和数字" if zh else "Use upper- and lower-case letters and a number."
    return None

def temp_password():
    a = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "Tmp-" + "".join(secrets.choice(a) for _ in range(12)) + "9a"

# ---- rate limits (in memory) ----
_RL, _RLL = {}, threading.Lock()
def hit(kind, key, limit, window):
    now = time.time(); k = (kind, key)
    with _RLL:
        q = [t for t in _RL.get(k, []) if t > now - window]
        ok = len(q) < limit
        if ok: q.append(now)
        _RL[k] = q
        if len(_RL) > 5000:
            for kk in list(_RL)[:1000]: _RL.pop(kk, None)
    return ok

# ---- forgot / reset ----
def request_reset(email, ip, lang):
    email = (email or "").strip().lower()[:200]
    if not hit("forgot_ip", ip, 5, 900) or not hit("forgot_all", "*", 60, 3600):
        return 429
    if "@" not in email or not hit("forgot_email", email, 3, 3600):
        return 200
    try:
        c = db(); users = c.execute("select id,username from users where lower(email)=? and status=1", (email,)).fetchall(); c.close()
    except Exception:
        users = []
    for uid, uname in users[:3]:
        token, code = secrets.token_urlsafe(32), "%06d" % secrets.randbelow(10**6)
        with LOCK:
            st = load()
            for r in st["resets"]:
                if r["uid"] == uid and not r.get("used"): r["used"] = _now()  # only the newest link works
            st["resets"].append({"th": _h(token), "ch": _h(code), "uid": uid, "exp": _now() + TTL, "tries": 0, "ip": ip[:64]})
            save(st)
        threading.Thread(target=send_reset, args=(email, uname, token, code), daemon=True).start()
    return 200

def send_reset(to, uname, token, code):
    c = mailconf()
    allow = [x.strip().lower() for x in c.get("MAIL_ALLOW", "").split(",") if x.strip()]
    if not c.get("SMTP_HOST") or (allow and to not in allow):
        return False
    link = c.get("PUBLIC_URL", "http://10.20.26.62:21114").rstrip("/") + "/_admin/adoluna-reset?t=" + token
    m = EmailMessage()
    m["From"] = formataddr(("Adoluna Support", c.get("SMTP_FROM", c.get("SMTP_USER", ""))))
    m["To"], m["Date"] = to, formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain="adoluna.com")
    m["Subject"] = "重设 Adoluna Support 后台密码 / Reset your Adoluna Support admin password"
    m.set_content(
        f"有人申请重设 Adoluna Support 后台账号「{uname}」的密码。\n\n"
        f"1. 在公司网络或 VPN 下打开这个链接：\n{link}\n\n2. 输入验证码：{code}\n\n"
        "链接和验证码只能用一次，30 分钟内有效。如果不是你本人操作，请忽略这封邮件，密码不会改变。\n\n"
        "-----\n\n"
        f"Someone asked to reset the password for the Adoluna Support admin account \"{uname}\".\n\n"
        f"1. On the office network or VPN, open this link:\n{link}\n\n2. Enter this code: {code}\n\n"
        "The link and code work once, for 30 minutes. If this was not you, ignore this email; your password stays the same.\n")
    try:
        port = int(c.get("SMTP_PORT", "465"))
        ip = c.get("SMTP_IP", "")  # optional: connect to this IP, still verify the certificate for SMTP_HOST
        class _S(smtplib.SMTP_SSL):
            def _get_socket(self, host, port, timeout):
                import socket
                raw = socket.create_connection((ip or host, port), timeout)
                return self.context.wrap_socket(raw, server_hostname=c["SMTP_HOST"])
        s = _S(c["SMTP_HOST"], port, context=ssl.create_default_context(), timeout=30) if port == 465 \
            else smtplib.SMTP(ip or c["SMTP_HOST"], port, timeout=30)
        if port != 465: s.starttls(context=ssl.create_default_context())
        s.login(c["SMTP_USER"], c["SMTP_PASSWORD"]); s.send_message(m); s.quit()
        return True
    except Exception as e:
        import sys; sys.stderr.write("reset mail failed: %s\n" % type(e).__name__)
        return False

def find_reset(token):
    if not token: return None
    th = _h(token)
    for r in load().get("resets", []):
        if hmac.compare_digest(r["th"], th) and not r.get("used") and r["exp"] > _now():
            return r
    return None

def do_reset(token, code, new, zh, ip):
    if not hit("reset_ip", ip, 10, 900):
        return 429, ("尝试太多次，请 15 分钟后再试" if zh else "Too many attempts. Try again in 15 minutes.")
    bad = "链接无效或已过期，请重新申请" if zh else "This link is invalid or has expired. Ask for a new one."
    with LOCK:
        st = load(); th = _h(token or "")
        r = next((x for x in st["resets"] if hmac.compare_digest(x["th"], th) and not x.get("used") and x["exp"] > _now()), None)
        if r is None:
            return 400, bad
        code = "".join(ch for ch in str(code or "") if ch.isdigit())
        if not hmac.compare_digest(r["ch"], _h(code)):
            r["tries"] += 1
            if r["tries"] >= MAX_TRIES: r["used"] = _now()
            save(st)
            return 400, (bad if r.get("used") else ("验证码不对，请看邮件里的 6 位数字" if zh else "That code is wrong. Use the 6-digit code from the email."))
        p = pw_problem(new, zh)
        if p:
            return 400, p
        import bcrypt
        h = bcrypt.hashpw(new.encode(), bcrypt.gensalt(10)).decode()
        c = db(True)
        with c:
            n = c.execute("update users set password=?, updated_at=datetime('now') where id=? and status=1", (h, r["uid"])).rowcount
            c.execute("delete from user_tokens where user_id=?", (r["uid"],))  # sign out everywhere
        c.close()
        r["used"] = _now(); st.setdefault("must_change", {}).pop(str(r["uid"]), None); save(st)
    return (200, "ok") if n else (400, bad)
