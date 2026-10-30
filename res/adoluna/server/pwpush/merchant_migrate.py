#!/usr/bin/env python3
"""One-time migration: existing device groups -> merchants/stores, legacy passwordless grants -> positions.
   PW_DIR=... PW_DB=... python3 merchant_migrate.py            (dry run: prints the mapping, writes nothing)
   PW_DIR=... PW_DB=... python3 merchant_migrate.py --apply    (writes adoluna.db; device groups keep their ids)
Rules: "Merchant - Store" -> merchant + store; any other name -> merchant of that name with store "总店 Main";
overrides in MAP below.  Idempotent: device groups already linked to a store are skipped."""
import json, os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merchants as M

INTERNAL = "Adoluna 内部 / Internal"
MAP = {  # device group name -> (merchant, store)
    "测试设备 / Test Devices": (INTERNAL, "测试设备 / Test Devices"),
    "Jay's Device": (INTERNAL, "Jay's Device"),
}

def plan():
    ro = M.ro(); c = M.md()
    linked = {r[0] for r in c.execute("select dgroup_id from stores")}
    out = []
    for gid, name in ro.execute("select id, name from device_groups order by id"):
        if gid in linked:
            continue
        if name in MAP:
            m, s = MAP[name]
        elif " - " in name:
            m, s = [x.strip() for x in name.split(" - ", 1)]
        else:
            m, s = name.strip(), "总店 Main"
        n = ro.execute("select count(*) from peers where group_id=?", (gid,)).fetchone()[0]
        out.append({"dgroup_id": gid, "dgroup": name, "merchant": m, "store": s, "devices": n})
    users = {r[0]: (r[1], r[2], r[3]) for r in ro.execute("select id, username, group_id, is_admin from users")}
    groups = dict(ro.execute("select id, name from groups").fetchall())
    grants = c.execute("select id, subj_kind, subj, tgt_kind, tgt from grants").fetchall()
    pg = dict((str(i), g) for i, g in ro.execute("select id, group_id from peers"))
    pos = {}
    for gid_, sk, sv, tk, tv in grants:
        dg = int(tv) if tk == "dgroup" else pg.get(tv)
        if sk == "group":
            mem = [u for u, x in users.items() if str(x[1]) == sv]
            if mem and all(users[u][2] for u in mem):
                continue  # every member is already an admin (sees everything)
            p = pos.setdefault(groups.get(int(sv), "group %s" % sv), {"members": mem, "dgroups": set()})
            if dg:
                p["dgroups"].add(dg)
        elif sk == "user" and not (users.get(int(sv)) or (0, 0, 0))[2]:
            p = pos.setdefault("%s (个人 / personal)" % users.get(int(sv), ("#" + sv,))[0], {"members": [int(sv)], "dgroups": set()})
            if dg:
                p["dgroups"].add(dg)
    abmap = {}
    for cid, uid, name in ro.execute("select id, user_id, name from address_book_collections"):
        if "全部商家" in name or "All Merchants" in name:
            abmap.setdefault(uid, cid)
    ro.close()
    return out, pos, abmap, grants

def apply():
    out, pos, abmap, grants = plan()
    c = M.md(); now = int(time.time())
    mids = {r[1].lower(): r[0] for r in c.execute("select id, name from merchants")}
    for x in out:
        mid = mids.get(x["merchant"].lower())
        if not mid:
            mid = c.execute("insert into merchants(name, address, created_at, created_by) values(?,?,?,?)", (x["merchant"], "", now, "migration")).lastrowid
            mids[x["merchant"].lower()] = mid
        c.execute("insert into stores(merchant_id, name, dgroup_id, created_at) values(?,?,?,?)", (mid, x["store"], x["dgroup_id"], now))
    sid = dict(c.execute("select dgroup_id, id from stores").fetchall())
    for name, p in pos.items():
        r = c.execute("select id from positions where name=?", (name,)).fetchone()
        pid = r[0] if r else c.execute("insert into positions(name, level, created_at) values(?,2,?)", (name, now)).lastrowid
        for u in p["members"]:
            c.execute("insert or ignore into pos_members values(?,?)", (pid, u))
        for g in p["dgroups"]:
            if g in sid:
                c.execute("insert or ignore into pos_access values(?,?,?)", (pid, "store", sid[g]))
    for u, col in abmap.items():
        c.execute("insert or ignore into staff_ab values(?,?)", (u, col))
    # legacy grants stay honoured until here; keep a copy, then the merchant model takes over
    c.execute("create table if not exists grants_legacy as select * from grants where 0")
    c.execute("insert into grants_legacy select * from grants"); c.execute("delete from grants")
    c.commit(); c.close()
    freeze_scope()
    print("reconcile:", M.reconcile())

def freeze_scope():
    """The batch password scope may name address books (e.g. c3 = 全部商家).  Reconcile changes what those books
    contain, which would silently widen the batch push.  Resolve them to the device IDs they hold right now."""
    p = os.path.join(M.DIR, "state.json")
    if not os.path.exists(p):
        return
    st = json.load(open(p)); sc = st.get("scope") or {}
    if not sc.get("abs"):
        return
    ro = M.ro(); ids = set(sc.get("peers", []))
    for a in sc["abs"]:
        q = ("select id from address_books where collection_id=?", (int(a[1:]),)) if a[0] == "c" else \
            ("select id from address_books where collection_id=0 and user_id=?", (int(a[1:]),))
        ids |= {str(r[0]) for r in ro.execute(*q)}
    ro.close()
    st["scope_before_merchants"] = dict(sc); sc["peers"] = sorted(ids); sc["abs"] = []; st["scope"] = sc
    tmp = p + ".tmp"; json.dump(st, open(tmp, "w"), indent=1); os.chmod(tmp, 0o600); os.replace(tmp, p)
    print("batch scope frozen to devices:", sc["peers"])

if __name__ == "__main__":
    out, pos, abmap, grants = plan()
    print(json.dumps({"stores": out, "positions": {k: {"members": v["members"], "dgroups": sorted(v["dgroups"])} for k, v in pos.items()},
                      "managed_address_books": abmap, "legacy_grants": len(grants)}, ensure_ascii=False, indent=1))
    if "--apply" in sys.argv:
        apply(); print("applied")
