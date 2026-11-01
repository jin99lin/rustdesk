"""Adoluna Support: merchant-centred device management (Jay 2026-10-30).
Model: merchant > store > device.  A store IS a rustdesk device group (stores.dgroup_id), so assigning a
device = peers.group_id.  Access: positions (job titles) hold staff and are granted a whole merchant (incl.
future stores), single stores, or everything; level tech(2) / view(1); admin = users.is_admin (all, level 3).
Derived automatically (reconcile, on every change + every 60 s): one managed address-book collection per
staff member with exactly the devices they may access (alias = device name, correct platform, merchant tag),
passwordless grants (computed live in granted()), merchant fixed password (state.json "merchants")."""
import json, os, sqlite3, threading, time

DIR = os.environ.get("PW_DIR", "/opt/rustdesk/pwpush")
DB = os.environ.get("PW_DB", "/opt/rustdesk/api-data/rustdeskapi.db")
MDB = os.path.join(DIR, "adoluna.db")
LOCK = threading.RLock()
MANAGED_NAME = "商家 / Merchants"
TAG_COLOR = 4280391411

def ts():
    t = time.time(); lt = time.localtime(t); off = -time.altzone if lt.tm_isdst > 0 else -time.timezone
    return time.strftime("%Y-%m-%d %H:%M:%S", lt) + ".%09d" % int((t % 1) * 1e9) + "%+03d:%02d" % (off // 3600, abs(off) % 3600 // 60)

def rw():
    c = sqlite3.connect(DB, timeout=10); c.execute("pragma busy_timeout=10000"); return c

def ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)

class ClosingConn(sqlite3.Connection):
    """sqlite3's own `with conn:` only commits/rolls back and never closes, so every `with md() as c:` leaked one
    file descriptor.  After ~1000 the sidecar hit EMFILE ("unable to open database file", sockets stuck in CLOSE-WAIT)
    and hung (2026-10-31 morning).  This connection closes itself when the with-block ends."""
    def __exit__(self, et, ev, tb):
        try:
            return super().__exit__(et, ev, tb)
        finally:
            self.close()

def md():
    c = sqlite3.connect(MDB, timeout=10, factory=ClosingConn)
    c.executescript("""
      create table if not exists merchants(id integer primary key, name text not null, address text not null default '',
        pwless integer not null default 1, created_at integer, created_by text);
      create table if not exists stores(id integer primary key, merchant_id integer not null, name text not null,
        dgroup_id integer not null unique, created_at integer);
      create table if not exists positions(id integer primary key, name text not null unique, level integer not null default 2,
        created_at integer);
      create table if not exists pos_members(position_id integer not null, user_id integer not null, primary key(position_id, user_id));
      create table if not exists pos_access(position_id integer not null, kind text not null, ref integer not null default 0,
        primary key(position_id, kind, ref));
      create table if not exists staff_ab(user_id integer primary key, collection_id integer not null);
      create table if not exists grants(id integer primary key, subj_kind text not null, subj text not null,
        tgt_kind text not null, tgt text not null, created_at integer, created_by text, unique(subj_kind, subj, tgt_kind, tgt));
      create table if not exists roles(peer_id text primary key, accept integer not null default 0,
        ctrl_only integer not null default 0, ts integer not null, updated_by text);
      create table if not exists audit(id integer primary key, ts integer, user text, my_id text, peer_id text, ip text, result text);""")
    return c

def platform(os_str):
    o = str(os_str or "").lower()
    for k, v in (("android", "Android"), ("windows", "Windows"), ("mac", "Mac OS"), ("ios", "iOS")):
        if k in o:
            return v
    if any(k in o for k in ("linux", "debian", "ubuntu", "fedora", "centos")):
        return "Linux"
    return str(os_str or "")

def log(user, peer, ip, text):
    try:
        with md() as c:
            c.execute("insert into audit(ts,user,my_id,peer_id,ip,result) values(?,?,?,?,?,?)",
                      (int(time.time()), user, "", peer or "", ip or "", "商家: " + text))
    except Exception:
        pass

# ---------- read model ----------
def stores_all(c=None):
    c = c or md()
    return [dict(zip(("id", "merchant_id", "name", "dgroup_id"), r)) for r in c.execute("select id, merchant_id, name, dgroup_id from stores order by id")]

def merchants_all(c=None):
    c = c or md()
    return [dict(zip(("id", "name", "address", "pwless"), r)) for r in c.execute("select id, name, address, pwless from merchants order by name collate nocase")]

def positions_all(c=None):
    c = c or md()
    out = []
    for i, n, lv in c.execute("select id, name, level from positions order by name collate nocase").fetchall():
        mem = [r[0] for r in c.execute("select user_id from pos_members where position_id=?", (i,))]
        acc = [{"kind": k, "ref": r} for k, r in c.execute("select kind, ref from pos_access where position_id=?", (i,))]
        out.append({"id": i, "name": n, "level": lv, "members": mem, "access": acc})
    return out

def pos_stores(p, stores):
    """store ids a position covers"""
    s = set()
    for a in p["access"]:
        if a["kind"] == "all":
            return {x["id"] for x in stores}
        if a["kind"] == "merchant":
            s |= {x["id"] for x in stores if x["merchant_id"] == a["ref"]}
        if a["kind"] == "store":
            s.add(a["ref"])
    return s

def users_all():
    c = ro()
    try:
        return [dict(zip(("id", "username", "nickname", "email", "is_admin", "status", "group_id"), r))
                for r in c.execute("select id, username, nickname, email, is_admin, status, group_id from users order by id")]
    finally:
        c.close()

def access(uid, is_admin, c=None, stores=None, positions=None):
    """{store_id: level} for one user"""
    stores = stores if stores is not None else stores_all(c)
    if is_admin:
        return {s["id"]: 3 for s in stores}
    out = {}
    for p in (positions if positions is not None else positions_all(c)):
        if uid in p["members"]:
            for s in pos_stores(p, stores):
                out[s] = max(out.get(s, 0), int(p["level"]))
    return out

_PM = {"t": 0, "m": {}}
def peer_merchant(peer_id):
    """merchant id of a device (30 s cache; used on every heartbeat)"""
    now = time.time()
    if now - _PM["t"] > 30:
        try:
            dg = {s["dgroup_id"]: s["merchant_id"] for s in stores_all()}
            c = ro(); m = {str(i): dg.get(g) for i, g in c.execute("select id, group_id from peers")}; c.close()
            _PM.update(t=now, m=m)
        except Exception:
            pass
    return _PM["m"].get(str(peer_id))

def bust():
    _PM["t"] = 0

def peer_store_id(peer_id):
    c = ro(); r = c.execute("select group_id from peers where id=? limit 1", (str(peer_id),)).fetchone(); c.close()
    if not r or not r[0]:
        return None
    x = md().execute("select id from stores where dgroup_id=?", (r[0],)).fetchone()
    return x[0] if x else None

def granted(uid, is_admin, peer_id):
    """passwordless follows the merchant: anyone whose positions cover the device's store"""
    try:
        s = peer_store_id(peer_id)
        if s is None:
            return False
        c = md()
        m = c.execute("select m.pwless from stores s join merchants m on m.id=s.merchant_id where s.id=?", (s,)).fetchone()
        if not m or not m[0]:
            return False
        return access(uid, is_admin, c).get(s, 0) >= 1
    except Exception:
        return False

# ---------- reconcile ----------
def nm(x):
    """b80: one spelling for bilingual names everywhere ("中文 / English"): trim, single spaces, " / " separator"""
    import re
    x = re.sub(r"\s+", " ", str(x or "")).strip()
    return re.sub(r"\s*/\s*", " / ", x)

def dgroup_name(mname, sname):
    return "%s - %s" % (mname, sname)

def store_labels(stores, merch):
    """b80 (Jay 10/31): the technician address book lists STORES.  Tag = merchant name when the merchant has one
    store, otherwise "merchant · store"."""
    cnt = {}
    for s in stores:
        cnt[s["merchant_id"]] = cnt.get(s["merchant_id"], 0) + 1
    out = {}
    for s in stores:
        m = merch.get(s["merchant_id"])
        if not m:
            continue
        out[s["id"]] = m["name"] if cnt[s["merchant_id"]] <= 1 else "%s · %s" % (m["name"], s["name"])
    return out

def _old_labels(db, col, uid):
    """earlier store/merchant tags in the managed collection (anything with the " · " store separator)"""
    return {r[0] for r in db.execute("select name from tags where collection_id=? and user_id=? and name like '% · %'", (col, uid))}

def _sync_groups(db, stores, merch):
    """b80: keep device groups in step with stores - name "Merchant - Store", missing group recreated,
    devices pointing at a deleted group go back to unassigned.  Returns number of fixes."""
    fixes = 0
    groups = dict(db.execute("select id, name from device_groups").fetchall())
    for s in stores:
        m = merch.get(s["merchant_id"])
        if not m:
            continue
        want = dgroup_name(m["name"], s["name"])
        if s["dgroup_id"] not in groups:
            g = _new_dgroup(db, want)
            with md() as c:
                c.execute("update stores set dgroup_id=? where id=?", (g, s["id"]))
            s["dgroup_id"] = g; fixes += 1
        elif groups[s["dgroup_id"]] != want:
            db.execute("update device_groups set name=?, updated_at=? where id=?", (want, ts(), s["dgroup_id"])); fixes += 1
    valid = set(groups) | {s["dgroup_id"] for s in stores}
    for (pid, g) in db.execute("select id, group_id from peers where group_id<>0 and group_id is not null").fetchall():
        if g not in valid:
            db.execute("update peers set group_id=0, updated_at=? where id=?", (ts(), pid)); fixes += 1
    if fixes:
        bust()
    return fixes

def reconcile():
    """Make address books match the merchant model.  Idempotent; returns counts."""
    with LOCK:
        c = md(); stores = stores_all(c); merch = {m["id"]: m for m in merchants_all(c)}; pos = positions_all(c)
        sab = dict(c.execute("select user_id, collection_id from staff_ab").fetchall())
        by_dg = {s["dgroup_id"]: s for s in stores}
        labels = store_labels(stores, merch)
        db = rw(); n = {"added": 0, "updated": 0, "removed": 0, "collections": 0}
        try:
            n["groups"] = _sync_groups(db, stores, merch)
            peers = db.execute("select id, hostname, alias, os, group_id from peers").fetchall()
            users = db.execute("select id, is_admin, status from users").fetchall()
            for uid, adm, status in users:
                acc = access(uid, bool(adm), stores=stores, positions=pos) if status == 1 else {}
                want = {}
                for pid, h, a, o, g in peers:
                    s = by_dg.get(g)
                    if s and s["id"] in acc:
                        want[str(pid)] = (a or "", h or "", platform(o), labels[s["id"]])
                col = sab.get(uid)
                if col and not db.execute("select 1 from address_book_collections where id=?", (col,)).fetchone():
                    col = None
                if not col:
                    if not want:
                        continue
                    cur = db.execute("insert into address_book_collections(user_id, name, created_at, updated_at) values(?,?,?,?)",
                                     (uid, MANAGED_NAME, ts(), ts()))
                    col = cur.lastrowid; c.execute("insert or replace into staff_ab values(?,?)", (uid, col)); n["collections"] += 1
                mtags = {m["name"] for m in merch.values()} | set(labels.values()) | _old_labels(db, col, uid)
                seen = set()
                for row_id, pid, alias, plat, tags, hn in db.execute(
                        "select row_id, id, alias, platform, tags, hostname from address_books where collection_id=? and user_id=? order by row_id",
                        (col, uid)).fetchall():
                    pid = str(pid)
                    if pid not in want or pid in seen:
                        db.execute("delete from address_books where row_id=?", (row_id,)); n["removed"] += 1; continue
                    seen.add(pid); a, h, p, mt = want[pid]
                    try:
                        tl = [t for t in (json.loads(tags or "[]") or []) if t not in mtags]
                    except Exception:
                        tl = []
                    nt = json.dumps([mt] + tl, ensure_ascii=False)
                    if (alias or "") != a or (plat or "") != p or (tags or "") != nt or (hn or "") != h:
                        db.execute("update address_books set alias=?, platform=?, tags=?, hostname=?, updated_at=? where row_id=?",
                                   (a, p, nt, h, ts(), row_id)); n["updated"] += 1
                for pid, (a, h, p, mt) in want.items():
                    if pid not in seen:
                        db.execute("insert into address_books(id, username, password, hostname, alias, platform, tags, hash, user_id, "
                                   "force_always_relay, rdp_port, rdp_username, online, login_name, same_server, collection_id, created_at, updated_at) "
                                   "values(?,'','',?,?,?,?,'',?,0,'','',0,'',0,?,?,?)",
                                   (pid, h, a, p, json.dumps([mt], ensure_ascii=False), uid, col, ts(), ts())); n["added"] += 1
                used = {w[3] for w in want.values()}
                for (tn,) in db.execute("select name from tags where collection_id=? and user_id=?", (col, uid)).fetchall():
                    if tn in mtags and tn not in used:  # b80: store list follows the stores, stale ones disappear
                        db.execute("delete from tags where collection_id=? and user_id=? and name=?", (col, uid, tn))
                have = {r[0] for r in db.execute("select name from tags where collection_id=? and user_id=?", (col, uid))}
                for mt in {w[3] for w in want.values()} - have:
                    db.execute("insert into tags(name, user_id, color, collection_id, created_at, updated_at) values(?,?,?,?,?,?)",
                               (mt, uid, TAG_COLOR, col, ts(), ts()))
            for row_id, plat in db.execute("select row_id, platform from address_books where platform not in ('Windows','Mac OS','Linux','Android','iOS','')").fetchall():
                if platform(plat) != plat:
                    db.execute("update address_books set platform=? where row_id=?", (platform(plat), row_id)); n["updated"] += 1
            db.commit(); c.commit()
        finally:
            db.close(); c.close()
        return n

def loop(interval=60):
    def run():
        while True:
            time.sleep(interval)
            try:
                reconcile()
            except Exception as e:
                import sys; sys.stderr.write("reconcile failed: %s\n" % e)
    threading.Thread(target=run, daemon=True).start()

# ---------- writes ----------
class Err(Exception):
    pass

def _new_dgroup(db, name):
    return db.execute("insert into device_groups(name, created_at, updated_at) values(?,?,?)", (name, ts(), ts())).lastrowid

def create_merchant(name, address, store, position_ids, by):
    name, store = nm(name), nm(store) or "总店 Main"
    if not name:
        raise Err("name")
    with LOCK:
        c = md()
        if c.execute("select 1 from merchants where lower(name)=lower(?)", (name,)).fetchone():
            raise Err("dup")
        mid = c.execute("insert into merchants(name, address, created_at, created_by) values(?,?,?,?)",
                        (name, (address or "").strip(), int(time.time()), by)).lastrowid
        db = rw(); g = _new_dgroup(db, dgroup_name(name, store)); db.commit(); db.close()
        c.execute("insert into stores(merchant_id, name, dgroup_id, created_at) values(?,?,?,?)", (mid, store, g, int(time.time())))
        for p in position_ids or []:
            c.execute("insert or ignore into pos_access values(?,?,?)", (int(p), "merchant", mid))
        c.commit(); c.close()
    log(by, "", "", "新建商家 %s" % name); return mid

def _sync_dg(where, arg):
    """after a rename: device groups carry "Merchant - Store" so older screens stay readable"""
    c = md(); rows = c.execute("select s.dgroup_id, m.name, s.name from stores s join merchants m on m.id=s.merchant_id where " + where, (arg,)).fetchall(); c.close()
    db = rw()
    for g, mn, sn in rows:
        db.execute("update device_groups set name=?, updated_at=? where id=?", (dgroup_name(mn, sn), ts(), g))
    db.commit(); db.close()

def update_merchant(mid, name, address, by):
    name = nm(name)
    if not name:
        raise Err("name")
    with LOCK, md() as c:
        if c.execute("select 1 from merchants where lower(name)=lower(?) and id<>?", (name, mid)).fetchone():
            raise Err("dup")
        c.execute("update merchants set name=?, address=? where id=?", (name, (address or "").strip(), mid))
    _sync_dg("m.id=?", mid)
    log(by, "", "", "修改商家 #%s %s" % (mid, name))

def store_devices(dgroup_ids):
    if not dgroup_ids:
        return 0
    c = ro(); n = c.execute("select count(*) from peers where group_id in (%s)" % ",".join("?" * len(dgroup_ids)), list(dgroup_ids)).fetchone()[0]; c.close()
    return n

def delete_merchant(mid, by):
    with LOCK:
        c = md(); dg = [r[0] for r in c.execute("select dgroup_id from stores where merchant_id=?", (mid,))]
        n = store_devices(dg)
        if n:
            raise Err("has_devices:%d" % n)
        db = rw()
        for g in dg:
            db.execute("delete from device_groups where id=?", (g,))
        db.commit(); db.close()
        c.execute("delete from stores where merchant_id=?", (mid,)); c.execute("delete from pos_access where kind='merchant' and ref=?", (mid,))
        c.execute("delete from pos_access where kind='store' and ref not in (select id from stores)")
        c.execute("delete from merchants where id=?", (mid,)); c.commit(); c.close()
    log(by, "", "", "删除商家 #%s" % mid)

def add_store(mid, name, by):
    name = nm(name)
    if not name:
        raise Err("name")
    with LOCK:
        c = md(); m = c.execute("select name from merchants where id=?", (mid,)).fetchone()
        if not m:
            raise Err("merchant")
        db = rw(); g = _new_dgroup(db, dgroup_name(m[0], name)); db.commit(); db.close()
        sid = c.execute("insert into stores(merchant_id, name, dgroup_id, created_at) values(?,?,?,?)", (mid, name, g, int(time.time()))).lastrowid
        c.commit(); c.close()
    log(by, "", "", "新建门店 %s › %s" % (m[0], name)); return sid

def rename_store(sid, name, by):
    name = nm(name)
    if not name:
        raise Err("name")
    with LOCK, md() as c:
        c.execute("update stores set name=? where id=?", (name, sid))
    _sync_dg("s.id=?", sid)
    log(by, "", "", "门店改名 #%s %s" % (sid, name))

def delete_store(sid, by):
    with LOCK:
        c = md(); r = c.execute("select dgroup_id, merchant_id from stores where id=?", (sid,)).fetchone()
        if not r:
            raise Err("store")
        n = store_devices([r[0]])
        if n:
            raise Err("has_devices:%d" % n)
        if c.execute("select count(*) from stores where merchant_id=?", (r[1],)).fetchone()[0] <= 1:
            raise Err("last_store")
        db = rw(); db.execute("delete from device_groups where id=?", (r[0],)); db.commit(); db.close()
        c.execute("delete from stores where id=?", (sid,)); c.execute("delete from pos_access where kind='store' and ref=?", (sid,))
        c.commit(); c.close()
    log(by, "", "", "删除门店 #%s" % sid)

def set_merchant_positions(mid, position_ids, by):
    with LOCK, md() as c:
        c.execute("delete from pos_access where kind='merchant' and ref=?", (mid,))
        for p in position_ids or []:
            c.execute("insert or ignore into pos_access values(?,?,?)", (int(p), "merchant", mid))
    log(by, "", "", "商家 #%s 管理职位 %s" % (mid, list(position_ids or [])))

def assign(ids, sid, by):
    """move / assign devices to a store (sid None = back to unassigned).  Returns previous merchant per device."""
    with LOCK:
        c = md()
        g = 0
        if sid is not None:
            r = c.execute("select s.dgroup_id, m.pwless from stores s join merchants m on m.id=s.merchant_id where s.id=?", (sid,)).fetchone()
            if not r:
                raise Err("store")
            g, pwless = r
        db = rw(); prev = {}
        for i in ids:
            o = db.execute("select group_id from peers where id=?", (str(i),)).fetchone()
            if not o:
                continue
            prev[str(i)] = o[0]
            db.execute("update peers set group_id=?, updated_at=? where id=?", (g, ts(), str(i)))
        db.commit(); db.close()
        dflt = []
        if sid is not None:  # batch 6 (Jay 10/31): merchant devices default to passwordless ON; an explicit role (on or off) is kept
            for i in prev:
                if not c.execute("select 1 from roles where peer_id=?", (i,)).fetchone():
                    c.execute("insert into roles(peer_id, accept, ctrl_only, ts, updated_by) values(?,1,0,?,?)", (i, int(time.time()), by))
                    dflt.append(i)
        c.commit(); c.close(); bust()
    for i in prev:
        log(by, i, "", "分配到门店 #%s" % sid if sid else "移回待分配")
    for i in dflt:
        log(by, i, "", "默认开启账号免密登录")
    return prev

def rename_device(pid, name, by):
    name = (name or "").strip()[:80]
    with LOCK:
        db = rw()
        if not db.execute("select 1 from peers where id=?", (str(pid),)).fetchone():
            db.close(); raise Err("device")
        db.execute("update peers set alias=?, updated_at=? where id=?", (name, ts(), str(pid)))
        db.execute("update address_books set alias=?, updated_at=? where id=?", (name, ts(), str(pid)))  # one name everywhere
        db.commit(); db.close()
    log(by, pid, "", "改名 %s" % name)

def save_position(pid, name, level, members, acc, by):
    name = (name or "").strip()
    if not name:
        raise Err("name")
    level = 2 if int(level or 2) >= 2 else 1
    with LOCK, md() as c:
        if c.execute("select 1 from positions where lower(name)=lower(?) and id<>?", (name, pid or 0)).fetchone():
            raise Err("dup")
        if pid:
            c.execute("update positions set name=?, level=? where id=?", (name, level, pid))
        else:
            pid = c.execute("insert into positions(name, level, created_at) values(?,?,?)", (name, level, int(time.time()))).lastrowid
        if members is not None:
            c.execute("delete from pos_members where position_id=?", (pid,))
            for u in members:
                c.execute("insert or ignore into pos_members values(?,?)", (pid, int(u)))
        if acc is not None:
            c.execute("delete from pos_access where position_id=?", (pid,))
            if acc.get("all"):
                c.execute("insert into pos_access values(?,?,0)", (pid, "all"))
            else:
                for m in acc.get("merchants") or []:
                    c.execute("insert or ignore into pos_access values(?,?,?)", (pid, "merchant", int(m)))
                for s in acc.get("stores") or []:
                    c.execute("insert or ignore into pos_access values(?,?,?)", (pid, "store", int(s)))
    log(by, "", "", "保存职位 %s" % name); return pid

def delete_position(pid, by):
    with LOCK, md() as c:
        c.execute("delete from pos_members where position_id=?", (pid,)); c.execute("delete from pos_access where position_id=?", (pid,))
        c.execute("delete from positions where id=?", (pid,))
    log(by, "", "", "删除职位 #%s" % pid)

def set_staff(uid, position_ids, is_admin, by, self_uid):
    with LOCK:
        if is_admin is not None:
            if int(uid) == int(self_uid) and not is_admin:
                raise Err("self_admin")
            if not is_admin:
                _need_other_admin(uid)
            db = rw(); db.execute("update users set is_admin=?, updated_at=? where id=?", (1 if is_admin else 0, ts(), int(uid))); db.commit(); db.close()
        if position_ids is not None:
            with md() as c:
                c.execute("delete from pos_members where user_id=?", (int(uid),))
                for p in position_ids:
                    c.execute("insert or ignore into pos_members values(?,?)", (int(p), int(uid)))
    log(by, "", "", "员工 #%s 职位 %s 管理员=%s" % (uid, position_ids, is_admin))

# ---------- staff lifecycle (batch 5, Jay 10/30) ----------
ACTIVE, DISABLED = 1, 2  # rustdesk-api users.status

def _need_other_admin(uid):
    """never leave the system without an active admin"""
    db = ro()
    try:
        n = db.execute("select count(*) from users where is_admin=1 and status=1 and id<>?", (int(uid),)).fetchone()[0]
    finally:
        db.close()
    if n < 1:
        raise Err("last_admin")

def set_status(uid, active, by, self_uid):
    uid = int(uid)
    if uid == int(self_uid):
        raise Err("self")
    with LOCK:
        if not active:
            _need_other_admin(uid)
        db = rw()
        try:
            if not db.execute("select 1 from users where id=?", (uid,)).fetchone():
                raise Err("user")
            db.execute("update users set status=?, updated_at=? where id=?", (ACTIVE if active else DISABLED, ts(), uid))
            if not active:
                db.execute("delete from user_tokens where user_id=?", (uid,))  # signed out everywhere, now
            db.commit()
        finally:
            db.close()
    log(by, "", "", "员工 #%s %s" % (uid, "启用" if active else "停用"))

def delete_user(uid, by, self_uid):
    """only deactivated staff; logs (audit, login_logs, audit_conns) are kept"""
    uid = int(uid)
    if uid == int(self_uid):
        raise Err("self")
    with LOCK:
        db = rw()
        try:
            r = db.execute("select username, status from users where id=?", (uid,)).fetchone()
            if not r:
                raise Err("user")
            if r[1] == ACTIVE:
                raise Err("still_active")
            col = md().execute("select collection_id from staff_ab where user_id=?", (uid,)).fetchone()
            for t in ("user_tokens", "tags", "address_books", "address_book_collections", "address_book_collection_rules", "user_thirds"):
                try:
                    db.execute("delete from %s where user_id=?" % t, (uid,))
                except sqlite3.OperationalError:
                    pass
            db.execute("delete from users where id=?", (uid,)); db.commit()
        finally:
            db.close()
        with md() as c:
            c.execute("delete from pos_members where user_id=?", (uid,)); c.execute("delete from staff_ab where user_id=?", (uid,))
            c.execute("delete from grants where subj_kind='user' and subj=?", (str(uid),))
    log(by, "", "", "删除员工 #%s %s" % (uid, r[0]))

def update_user(uid, nickname, email, username, by):
    uid = int(uid); sets, args = [], []
    if nickname is not None:
        sets.append("nickname=?"); args.append(str(nickname).strip()[:60])
    if email is not None:
        e = str(email).strip()[:200]
        if e and ("@" not in e or " " in e):
            raise Err("email")
        sets.append("email=?"); args.append(e)
    if username is not None:
        un = str(username).strip()
        import re
        if not re.match(r"^[A-Za-z0-9_.@-]{2,32}$", un):
            raise Err("username")
        sets.append("username=?"); args.append(un)
    if not sets:
        return
    with LOCK:
        db = rw()
        try:
            if username is not None and db.execute("select 1 from users where lower(username)=lower(?) and id<>?", (un, uid)).fetchone():
                raise Err("dup")
            db.execute("update users set %s, updated_at=? where id=?" % ", ".join(sets), args + [ts(), uid]); db.commit()
        finally:
            db.close()
    log(by, "", "", "修改员工 #%s 资料%s" % (uid, "（含用户名 → %s）" % un if username is not None else ""))

def peer_merchant_all():
    peer_merchant("0")
    return list(_PM["m"])
