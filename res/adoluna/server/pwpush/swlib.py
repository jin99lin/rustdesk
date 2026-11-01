"""Adoluna b81: software library (软件库). Admins upload apk/exe/msi; devices download by sha256 URL.

Files live in PW_DIR/swlib/<sha256>.<ext> (0640).  Download URL: /api/adoluna-pkg/<sha256>/<name> - public but
unguessable, and every device re-verifies the sha256 before installing (signed command carries the hash)."""
import hashlib, os, re, shutil, struct, subprocess, tempfile, time, zipfile

MAX_FILE = 400 * 1024 * 1024
MAX_TOTAL = 6 * 1024 ** 3
MIN_FREE = 2 * 1024 ** 3
EXTS = {"apk": "android", "exe": "windows", "msi": "windows"}
SHA = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """create table if not exists swlib(sha256 text primary key, name text, ver text, build integer, platform text,
  ext text, size integer, filename text, pkg text, ts integer, by text, note text);"""

def init(c):
    c.executescript(SCHEMA)

def safe_name(n):
    n = os.path.basename(str(n or "")).strip()
    n = re.sub(r"[^A-Za-z0-9._-]+", "-", n).strip("-.")
    return n[:120] or "package"

# --- minimal Android binary XML reader: package / versionName / versionCode / label (literal only) ---
def _axml_attrs(data):
    if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != 3:
        return {}
    strings, pos, out = [], 8, {}
    while pos + 8 <= len(data):
        typ, hsz, size = struct.unpack_from("<HHI", data, pos)
        if size <= 0:
            break
        if typ == 1:  # string pool
            cnt, _, flags, sstart = struct.unpack_from("<IIII", data, pos + 8)
            utf8 = flags & 0x100
            offs = struct.unpack_from("<%dI" % cnt, data, pos + 28)
            base = pos + sstart
            for o in offs:
                p = base + o
                try:
                    if utf8:
                        n = data[p]; p += 2 if n & 0x80 else 1
                        n = data[p]; ln = ((n & 0x7f) << 8 | data[p + 1]) if n & 0x80 else n; p += 2 if n & 0x80 else 1
                        strings.append(data[p:p + ln].decode("utf-8", "replace"))
                    else:
                        ln = struct.unpack_from("<H", data, p)[0]; p += 2
                        if ln & 0x8000:
                            ln = ((ln & 0x7fff) << 16) | struct.unpack_from("<H", data, p)[0]; p += 2
                        strings.append(data[p:p + ln * 2].decode("utf-16-le", "replace"))
                except Exception:
                    strings.append("")
        elif typ == 0x102:  # start element
            name = strings[struct.unpack_from("<I", data, pos + 20)[0]] if strings else ""
            astart, asize, acnt = struct.unpack_from("<HHH", data, pos + 24)
            if name in ("manifest", "application"):
                for i in range(acnt):
                    a = pos + 16 + astart + i * asize
                    _, an, raw, _, _, dtype, dval = struct.unpack_from("<IIIHBBI", data, a)
                    k = strings[an] if an < len(strings) else ""
                    v = strings[raw] if raw != 0xFFFFFFFF and raw < len(strings) else (dval if dtype in (0x10, 0x11) else None)
                    if dtype == 3 and dval < len(strings):
                        v = strings[dval]
                    out[name + ":" + k] = v
            if name == "application":
                break
        pos += size
    return out

def apk_meta(path):
    try:
        with zipfile.ZipFile(path) as z:
            a = _axml_attrs(z.read("AndroidManifest.xml"))
        lab = a.get("application:label")
        return {"pkg": a.get("manifest:package") or "", "ver": str(a.get("manifest:versionName") or ""),
                "build": a.get("manifest:versionCode") if isinstance(a.get("manifest:versionCode"), int) else None,
                "name": lab if isinstance(lab, str) and lab else ""}
    except Exception:
        return {}

def msi_meta(path):
    try:
        out = subprocess.run(["msiinfo", "export", path, "Property"], capture_output=True, text=True, timeout=30).stdout
        props = dict(l.split("\t", 1) for l in out.splitlines() if "\t" in l)
        return {"name": props.get("ProductName", ""), "ver": props.get("ProductVersion", "")}
    except Exception:
        return {}

def guess_from_filename(fn):
    m = re.search(r"(\d+\.\d+(?:\.\d+)*)(?:[-_.]?b(\d+))?", fn)
    out = {}
    if m:
        out["ver"] = m.group(1) + ("-b" + m.group(2) if m.group(2) else "")
        if m.group(2):
            out["build"] = int(m.group(2))
    base = fn.rsplit(".", 1)[0]
    nm = re.split(r"[-_ ]?v?\d+\.\d+", base)[0].strip("-_ ") or base
    out["name"] = "Adoluna Support" if nm.lower().replace("_", "-") == "adoluna-support" else nm.replace("_", " ")
    return out

def store(c, d, src_fp, length, filename, by, name="", ver="", build=None):
    """Stream an upload (length bytes from src_fp) into the library. Returns the row dict."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXTS:
        raise ValueError("只支持 .apk / .exe / .msi")
    if length <= 0 or length > MAX_FILE:
        raise ValueError("文件大小须在 1 B – %d MB" % (MAX_FILE // 1024 // 1024))
    lib = os.path.join(d, "swlib"); os.makedirs(lib, mode=0o750, exist_ok=True)
    if shutil.disk_usage(lib).free - length < MIN_FREE:
        raise ValueError("服务器磁盘空间不足")
    used = c.execute("select coalesce(sum(size),0) from swlib").fetchone()[0]
    if used + length > MAX_TOTAL:
        raise ValueError("软件库已满（上限 %d GB），请先删除旧安装包" % (MAX_TOTAL // 1024 ** 3))
    h = hashlib.sha256(); left = length
    fd, tmp = tempfile.mkstemp(dir=lib, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            while left > 0:
                b = src_fp.read(min(1 << 20, left))
                if not b:
                    raise ValueError("上传中断")
                f.write(b); h.update(b); left -= len(b)
        sha = h.hexdigest()
        meta = guess_from_filename(filename)
        meta.update({k: v for k, v in (apk_meta(tmp) if ext == "apk" else msi_meta(tmp) if ext == "msi" else {}).items() if v})
        dst = os.path.join(lib, sha + "." + ext)
        os.chmod(tmp, 0o640); os.replace(tmp, dst)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    row = {"sha256": sha, "name": (name or meta.get("name") or filename.rsplit(".", 1)[0])[:120],
           "ver": (ver or meta.get("ver") or "")[:60], "build": int(build) if str(build or "").isdigit() else meta.get("build"),
           "platform": EXTS[ext], "ext": ext, "size": length, "filename": safe_name(filename), "pkg": meta.get("pkg", ""),
           "ts": int(time.time()), "by": by}
    c.execute("insert or replace into swlib(sha256,name,ver,build,platform,ext,size,filename,pkg,ts,by) values(?,?,?,?,?,?,?,?,?,?,?)",
              tuple(row[k] for k in ("sha256", "name", "ver", "build", "platform", "ext", "size", "filename", "pkg", "ts", "by")))
    return row

COLS = ("sha256", "name", "ver", "build", "platform", "ext", "size", "filename", "pkg", "ts", "by")

def listing(c, platform=None):
    q = "select %s from swlib" % ",".join(COLS) + (" where platform=?" if platform else "") + " order by ts desc"
    return [dict(zip(COLS, r)) for r in c.execute(q, (platform,) if platform else ())]

def get(c, sha):
    r = c.execute("select %s from swlib where sha256=?" % ",".join(COLS), (sha,)).fetchone()
    return dict(zip(COLS, r)) if r else None

def path_of(d, row):
    return os.path.join(d, "swlib", row["sha256"] + "." + row["ext"])

def delete(c, d, sha):
    row = get(c, sha)
    if not row:
        return False
    c.execute("delete from swlib where sha256=?", (sha,))
    try:
        os.unlink(path_of(d, row))
    except OSError:
        pass
    return True

def url_for(base, row):
    return "%s/api/adoluna-pkg/%s/%s" % (base.rstrip("/"), row["sha256"], row["filename"])
