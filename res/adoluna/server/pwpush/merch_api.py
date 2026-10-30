"""HTTP API for the merchant pages: /_admin/adoluna-api/m/<action>.  Mixed into pwpush.H as H.merch."""
import json, sys, time
import merchants as M
import accounts as ACC

def _ctx(self):
    return sys.modules[type(self).__module__]

def _ip_ok(self, G):
    return G.private(self.client_address[0]) and not any(
        self.headers.get(h) and not all(G.private(x) for x in self.headers[h].split(",")) for h in ("X-Forwarded-For", "X-Real-IP"))

def devices(G, uid, adm, acc):
    st = G.load(); sn = G.seen_all(); c = M.md(); stores = {s["dgroup_id"]: s for s in M.stores_all(c)}
    ro = M.ro()
    try:
        rows = ro.execute("select id, hostname, alias, os, last_online_time, last_online_ip, group_id, created_at, version from peers order by id").fetchall()
    finally:
        ro.close()
    out = []; ipmap = {}
    for i, h, a, o, lo, ip, g, ca, ver in rows:
        s = stores.get(g); i = str(i)
        if s and s["id"] not in acc:
            continue
        if not s and not adm and not any(v >= 2 for v in acc.values()):
            continue
        e = G.effective(st, i); have = (sn.get(i) or {}).get("ver")
        src = "custom" if i in st.get("custom", {}) else (G.base_payload(st, i)[1] if e else None)
        d = {"id": i, "name": a or "", "hostname": h or "", "os": o or "", "platform": M.platform(o), "ip": ip or "",
             "last_online_time": lo or 0, "created_at": ca or "", "version": ver or "", "store_id": s["id"] if s else None,
             "merchant_id": s["merchant_id"] if s else None, "level": acc.get(s["id"], 0) if s else (3 if adm else 2),
             "pw": {"src": src, "want": e["v"] if e else 0, "have": have},
             "sync": "pending" if e and (have is None or have < e["v"]) else "ok"}
        out.append(d)
        if s and ip:
            ipmap.setdefault(ip, {}).setdefault(s["id"], 0); ipmap[ip][s["id"]] += 1
    for d in out:
        if d["store_id"] is None and d["ip"] in ipmap:
            d["suggest"] = max(ipmap[d["ip"]].items(), key=lambda x: x[1])[0]
    return out

def overview(G, uid, adm):
    c = M.md(); stores = M.stores_all(c); pos = M.positions_all(c); acc = M.access(uid, adm, c, stores, pos)
    st = G.load(); devs = devices(G, uid, adm, acc)
    users = M.users_all()
    merch = []
    for m in M.merchants_all(c):
        ms = [s for s in stores if s["merchant_id"] == m["id"]]
        if not adm and not any(s["id"] in acc for s in ms):
            continue
        md_ = [d for d in devs if d["merchant_id"] == m["id"]]
        mp = st.get("merchants", {}).get(str(m["id"]))
        access_ = []
        for p in pos:
            cov = M.pos_stores(p, stores) & {s["id"] for s in ms}
            if cov:
                whole = any(a["kind"] == "all" or (a["kind"] == "merchant" and a["ref"] == m["id"]) for a in p["access"])
                access_.append({"id": p["id"], "name": p["name"], "level": p["level"], "whole": whole,
                                "stores": sorted(cov), "members": len(p["members"])})
        merch.append(dict(m, stores=[dict(s, devices=sum(1 for d in md_ if d["store_id"] == s["id"]),
                                         online=sum(1 for d in md_ if d["store_id"] == s["id"] and time.time() - d["last_online_time"] < 300))
                                    for s in ms],
                          devices=len(md_), online=sum(1 for d in md_ if time.time() - d["last_online_time"] < 300),
                          pw={"set": bool(mp), "v": mp["v"] if mp else 0, "at": mp.get("at") if mp else None,
                              "pushed": sum(1 for d in md_ if d["pw"]["src"] and str(d["pw"]["src"]).startswith("m") and d["sync"] == "ok"),
                              "total": sum(1 for d in md_ if d["pw"]["src"] and str(d["pw"]["src"]).startswith("m"))},
                          access=access_, admins=sum(1 for u in users if u["is_admin"] and u["status"] == 1)))
    return {"me": {"id": uid, "admin": adm}, "merchants": merch, "devices": devs,
            "positions": pos if adm else [], "users": [{k: u[k] for k in ("id", "username", "nickname", "email", "is_admin", "status")} for u in users] if adm else [],
            "unassigned": sum(1 for d in devs if d["store_id"] is None)}

def merch(self, path):
    G = _ctx(self); tr = self.tr
    if not _ip_ok(self, G):
        return self.send(403, b'{"error":"forbidden"}')
    u = ACC.uid_for_token(self.headers.get("api-token", ""))
    if not u:
        return self.jout(403, {"error": tr("请先登录", "Sign in first")})
    uid, adm = u; by = "%s@%s" % (uid, self.client_ip()); act = path[len("/_admin/adoluna-api/m/"):]
    if self.command == "GET":
        if act == "overview":
            return self.jout(200, overview(G, uid, adm))
        if act == "log":
            with M.md() as c:
                rows = [dict(zip(("ts", "user", "peer_id", "ip", "result"), r)) for r in
                        c.execute("select ts, user, peer_id, ip, result from audit order by id desc limit 300")]
            return self.jout(200, {"items": rows})
        return self.jout(404, {})
    q = self.body_json() or {}
    acc = M.access(uid, adm)
    def need_admin():
        if not adm:
            raise M.Err("admin")
    def need_tech(store_ids):
        if not adm and any(s is not None and acc.get(s, 0) < 2 for s in store_ids):
            raise M.Err("level")
    try:
        if act == "merchant":
            need_admin(); pw = q.get("password") or ""
            if pw and not 8 <= len(pw) <= 64:
                raise M.Err("pwlen")
            mid = M.create_merchant(q.get("name"), q.get("address"), q.get("store"), q.get("positions"), by)
            if pw:
                with G.LOCK:
                    st = G.load(); G.set_merchant_pw(st, mid, pw, by); G.save(st)
            res = {"id": mid}
        elif act == "merchant/update":
            need_admin(); M.update_merchant(int(q["id"]), q.get("name"), q.get("address"), by); res = {}
        elif act == "merchant/delete":
            need_admin(); M.delete_merchant(int(q["id"]), by)
            with G.LOCK:
                st = G.load(); st.get("merchants", {}).pop(str(int(q["id"])), None); G.save(st)
            res = {}
        elif act == "merchant/positions":
            need_admin(); M.set_merchant_positions(int(q["id"]), q.get("positions") or [], by); res = {}
        elif act == "merchant/password":
            need_admin(); pw = q.get("password") or ""
            if not 8 <= len(pw) <= 64:
                raise M.Err("pwlen")
            with G.LOCK:
                st = G.load(); v = G.set_merchant_pw(st, int(q["id"]), pw, by); G.save(st)
            M.log(by, "", "", "商家 #%s 固定密码 v%s" % (q["id"], v)); res = {"v": v}
        elif act == "store":
            need_admin(); res = {"id": M.add_store(int(q["merchant_id"]), q.get("name"), by)}
        elif act == "store/update":
            need_admin(); M.rename_store(int(q["id"]), q.get("name"), by); res = {}
        elif act == "store/delete":
            need_admin(); M.delete_store(int(q["id"]), by); res = {}
        elif act == "assign":
            ids = [str(x) for x in (q.get("ids") or []) if str(x).isdigit()][:500]
            sid = int(q["store_id"]) if q.get("store_id") else None
            if sid is None:
                need_admin()
            need_tech([sid] + [M.peer_store_id(i) for i in ids])
            mode = q.get("password", "switch")
            with G.LOCK:
                st = G.load()
                before = {i: G.effective(st, i) for i in ids}
                M.assign(ids, sid, by); st = G.load()
                for i in ids:
                    if mode == "keep":
                        if before[i] and i not in st.get("custom", {}):
                            st.setdefault("custom", {})[i] = dict(before[i], at=G.now_s(), by=by, kept=True)
                    else:
                        st.get("custom", {}).pop(i, None)
                        e = G.effective(st, i)
                        if e and (not before[i] or before[i]["h"] != e["h"] or before[i]["v"] >= e["v"]):
                            G.reissue(st, i)
                G.save(st)
            res = {"moved": len(ids)}
        elif act == "push":
            # 一键推送密码 (Jay 10/30): selected devices get their merchant password again, or one new own password
            ids = [str(x) for x in (q.get("ids") or []) if str(x).isdigit()][:500]
            if not ids:
                raise M.Err("ids")
            need_tech([M.peer_store_id(i) for i in ids])
            mode = q.get("mode", "merchant"); pw = q.get("password") or ""
            if mode == "own" and not 8 <= len(pw) <= 64:
                raise M.Err("pwlen")
            pushed, skipped = [], []
            with G.LOCK:
                st = G.load()
                for i in ids:
                    if mode == "own":
                        G.set_custom(st, i, pw, by); pushed.append(i)
                    else:
                        st.get("custom", {}).pop(i, None)
                        (pushed if G.reissue(st, i) else skipped).append(i)
                G.save(st)
            for i in pushed:
                M.log(by, i, "", "推送密码（单独密码）" if mode == "own" else "推送密码（商家密码）")
            res = {"pushed": pushed, "skipped": skipped}
        elif act == "rename":
            need_tech([M.peer_store_id(q.get("id"))]); M.rename_device(str(q.get("id")), q.get("name"), by); res = {}
        elif act == "delete-devices":
            need_admin(); ro = M.ro()
            ids = [str(x) for x in (q.get("ids") or []) if str(x).isdigit()][:500]
            rows = [r[0] for r in ro.execute("select row_id from peers where id in (%s)" % ",".join("?" * len(ids)), ids)] if ids else []
            ro.close()
            r, d = self.upstream("POST", "/api/admin/peer/batchDelete", json.dumps({"row_ids": rows}),
                                 {"api-token": self.headers.get("api-token", ""), "Content-Type": "application/json"})
            if r.status != 200 or json.loads(d).get("code") != 0:
                raise M.Err("upstream")
            for i in ids:
                M.log(by, i, "", "删除设备")
            res = {"deleted": len(rows)}
        elif act == "position":
            need_admin()
            res = {"id": M.save_position(q.get("id"), q.get("name"), q.get("level"), q.get("members"), q.get("access"), by)}
        elif act == "position/delete":
            need_admin(); M.delete_position(int(q["id"]), by); res = {}
        elif act == "staff":
            need_admin(); M.set_staff(int(q["user_id"]), q.get("positions"), q.get("is_admin"), by, uid); res = {}
        elif act == "sync":
            res = {}
        else:
            return self.jout(404, {})
    except M.Err as e:
        k = str(e); n = k.split(":")[1] if ":" in k else ""
        msg = {"admin": ("只有管理员可以做这个操作", "Only an administrator can do this"),
               "level": ("你的职位是只读，不能改这些设备", "Your position is view-only for these devices"),
               "name": ("请填写名称", "Enter a name"), "dup": ("这个名称已经有了", "That name is already used"),
               "pwlen": ("密码长度需 8–64 位", "Password must be 8–64 characters"),
               "ids": ("请先选择设备", "Select devices first"),
               "has_devices": ("请先删除或移走 %s 台设备" % n, "Remove or move %s devices first" % n),
               "last_store": ("商家至少要留一家门店", "A merchant needs at least one store"),
               "self_admin": ("不能取消你自己的管理员", "You can't remove your own admin access"),
               "upstream": ("删除失败", "Delete failed")}.get(k.split(":")[0], ("操作失败：" + k, "Failed: " + k))
        return self.jout(400, {"error": tr(*msg)})
    except (KeyError, ValueError, TypeError, AttributeError):
        return self.jout(400, {"error": tr("参数不对", "Bad request")})
    res["sync"] = M.reconcile()
    return self.jout(200, res)
