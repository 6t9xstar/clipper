"""P1b: SQLite lease queue: crash recovery + subprocess cancellation on Windows."""
import os, sqlite3, subprocess, sys, time, uuid
from pathlib import Path

DB = Path(__file__).parent / "out" / "q.db"
LEASE = 3.0


def conn():
    c = sqlite3.connect(DB, timeout=30, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=30000")
    return c


def claim(c, wid):
    now = time.time()
    c.execute("BEGIN IMMEDIATE")  # single writer: atomic claim
    row = c.execute("SELECT id FROM jobs WHERE (status='queued') OR (status='running' AND lease_until<?) "
                    "ORDER BY created LIMIT 1", (now,)).fetchone()
    if row:
        c.execute("UPDATE jobs SET status='running', worker=?, lease_until=?, attempts=attempts+1 WHERE id=?",
                  (wid, now + LEASE, row[0]))
    c.execute("COMMIT")
    return row and row[0]


def worker(wid):
    c = conn()
    while True:
        jid = claim(c, wid)
        if not jid:
            if c.execute("SELECT count(*) FROM jobs WHERE status!='done'").fetchone()[0] == 0: return
            time.sleep(0.2); continue
        time.sleep(0.05)  # "work"
        # completion only counts if we still hold the lease (fencing)
        c.execute("UPDATE jobs SET status='done', done_count=done_count+1 WHERE id=? AND worker=?", (jid, wid))


def cancel_test():
    """Start a long ffmpeg in its own process group; kill the tree; confirm it is gone within 2 s."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=600",
                          "-c:v", "libx264", "-preset", "slow", "-f", "null", "-"], creationflags=flags)
    time.sleep(2); t0 = time.time()
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(p.pid, 9)
    p.wait(timeout=5)
    return round(time.time() - t0, 2)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        worker(sys.argv[1]); sys.exit()
    DB.unlink(missing_ok=True)
    c = conn()
    c.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, status TEXT, worker TEXT, lease_until REAL, attempts INT DEFAULT 0, done_count INT DEFAULT 0, created REAL)")
    for i in range(150):
        c.execute("INSERT INTO jobs(id,status,created) VALUES(?,?,?)", (str(uuid.uuid4()), "queued", time.time() + i * 1e-6))
    t0 = time.time()
    ws = [subprocess.Popen([sys.executable, __file__, f"w{i}"]) for i in range(3)]
    time.sleep(1.0); ws[0].kill()  # hard crash of a worker mid-run
    for w in ws[1:]: w.wait(timeout=120)
    r = c.execute("SELECT count(*), sum(status='done'), sum(done_count>1), max(attempts) FROM jobs").fetchone()
    print({"jobs": r[0], "done": r[1], "double_completed": r[2], "max_attempts": r[3], "wall_s": round(time.time() - t0, 1),
           "ffmpeg_cancel_s": cancel_test()})
