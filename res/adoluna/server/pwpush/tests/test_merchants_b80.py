"""b80: store-level address-book tags, device-group auto-sync, name normalisation, batch-create platform fill."""
import importlib, json, os, sqlite3, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); SRC = os.path.dirname(HERE)

class B80(unittest.TestCase):
    def setUp(self):
        t = tempfile.mkdtemp(); os.environ.update(PW_DIR=t, PW_DB=os.path.join(t, "api.db"))
        sys.path.insert(0, SRC)
        import merchants; self.M = importlib.reload(merchants)
        c = sqlite3.connect(os.environ["PW_DB"])
        c.executescript("""create table peers(id text, hostname text, alias text, os text, group_id integer, updated_at text);
          create table users(id integer, username text, nickname text, email text, is_admin integer, status integer, group_id integer, updated_at text);
          create table device_groups(id integer primary key, name text, created_at text, updated_at text);
          create table address_book_collections(id integer primary key, user_id integer, name text, created_at text, updated_at text);
          create table address_books(row_id integer primary key, id text, username text, password text, hostname text, alias text, platform text,
            tags text, hash text, user_id integer, force_always_relay integer, rdp_port text, rdp_username text, online integer, login_name text,
            same_server integer, collection_id integer, created_at text, updated_at text);
          create table tags(id integer primary key, name text, user_id integer, color integer, collection_id integer, created_at text, updated_at text);
          insert into users values(1,'admin','','',1,1,1,'');"""); c.commit(); c.close()

    def test_store_tags_groups_names(self):
        M = self.M
        a = M.create_merchant("  寿司  王/Sushi King ", "", "Little Creek", [], "t")
        b = M.create_merchant("China Wok", "", "", [], "t")
        self.assertEqual(M.merchants_all()[1]["name"] if M.merchants_all()[1]["id"] == a else M.merchants_all()[0]["name"], "寿司 王 / Sushi King")
        s2 = M.add_store(a, "Newtown", "t")
        st = {s["id"]: s for s in M.stores_all()}
        db = M.rw()
        g1 = [s for s in st.values() if s["merchant_id"] == a and s["id"] != s2][0]["dgroup_id"]
        gb = [s for s in st.values() if s["merchant_id"] == b][0]["dgroup_id"]
        db.executemany("insert into peers values(?,?,?,?,?,'')", [("1", "h1", "POS 1", "Windows 10 Pro", g1, ), ("2", "h2", "Kiosk", "Android 13", st[s2]["dgroup_id"]),
                                                                 ("3", "h3", "Wok POS", "Windows 11", gb), ("4", "h4", "lost", "Linux", 999)])
        db.execute("update device_groups set name='stale' where id=?", (gb,)); db.commit(); db.close()
        n = M.reconcile()
        self.assertGreaterEqual(n["groups"], 2)  # renamed group + orphan device
        db = M.rw()
        tags = {r[0]: json.loads(r[1])[0] for r in db.execute("select id, tags from address_books")}
        self.assertEqual(tags, {"1": "寿司 王 / Sushi King · Little Creek", "2": "寿司 王 / Sushi King · Newtown", "3": "China Wok"})
        self.assertEqual(db.execute("select name from device_groups where id=?", (gb,)).fetchone()[0], "China Wok - 总店 Main")
        self.assertEqual(db.execute("select group_id from peers where id='4'").fetchone()[0], 0)
        self.assertEqual(sorted(r[0] for r in db.execute("select name from tags")), sorted(set(tags.values())))
        db.close()
        # store deleted -> merchant has one store -> label collapses, stale tag removed
        M.assign(["2"], None, "t"); M.delete_store(s2, "t"); M.reconcile()
        db = M.rw()
        self.assertEqual(json.loads(db.execute("select tags from address_books where id='1'").fetchone()[0])[0], "寿司 王 / Sushi King")
        self.assertNotIn("寿司 王 / Sushi King · Little Creek", [r[0] for r in db.execute("select name from tags")])
        db.close()

if __name__ == "__main__":
    unittest.main()
