#!/usr/bin/env python3
"""
Alkemio File Recovery  --  browser-cache extractor (Windows / macOS / Linux)

Alkemio lost its uploaded-file storage and its backups. Some of those files may
still be in YOUR browser's local cache from when you last viewed them. This tool
scans your browser caches and rescues only the files Alkemio knows it lost.

Double-click it for a simple window (no terminal). Power users / the recovery
team can pass flags (see --help) to run it as a command-line tool.

PRIVACY
-------
* The tool carries ONLY cryptographic fingerprints (hashes) of the lost files --
  no filenames, no idea what any file is.
* It extracts a file ONLY when that file's content fingerprint EXACTLY matches a
  lost-file fingerprint. Everything else in your cache is ignored.
* It makes NO network connections. It writes one .zip on your machine and asks
  first. YOU decide whether to send it. It only ever reads your cache.

WHAT IT CAN / CANNOT FIND
-------------------------
Can:  images, avatars, banners, and uploaded attachments you viewed/downloaded.
Cannot: live-edited Collabora office documents (never cached locally).

Python 3.8+, standard library only (Tkinter for the GUI; ships with Python).
"""
import argparse
import csv
import hashlib
import json
import os
import platform
import re
import struct
import sys
import zlib
import zipfile
from collections import Counter
from pathlib import Path

# ------------------------------------------------------------------ constants
SIMPLE_INITIAL_MAGIC = 0xFCFB6D1BA7725C30
SIMPLE_EOF_MAGIC = 0xF4FA6F45970D41D8
EOF_MAGIC_BYTES = struct.pack("<Q", SIMPLE_EOF_MAGIC)
FLAG_HAS_CRC32 = 1
FLAG_HAS_KEY_SHA256 = 2
MARKER = b"/rest/storage/"
csv.field_size_limit(10 * 1024 * 1024)

URL_RE = re.compile(rb"https?://[^\x00\s\"'<>\\]+")
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b):
    n = int.from_bytes(b, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def sha3_hex(b):
    return hashlib.sha3_256(b).hexdigest()


def cidv0(b):
    """Legacy file-service externalIDs are IPFS CIDv0 = base58(0x12 0x20 sha256)."""
    return b58encode(b"\x12\x20" + hashlib.sha256(b).digest())


def human_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def guess_ext(body):
    if body.startswith(b"%PDF-"):
        return "pdf"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if body.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    if body[:2] == b"PK" and body[2:4] in (b"\x03\x04", b"\x05\x06"):
        head = body[:6000]
        if b"word/" in head:
            return "docx"
        if b"xl/" in head:
            return "xlsx"
        if b"ppt/" in head:
            return "pptx"
        return "zip"
    if body[:5] == b"<?xml" or body[:4] == b"<svg":
        return "svg"
    if body[4:8] == b"ftyp":
        return "mp4"
    if body.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"
    return "bin"


def ext_from_mime(mime, fallback):
    m = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
         "image/webp": "webp", "image/svg+xml": "svg", "application/pdf": "pdf",
         "application/zip": "zip", "video/mp4": "mp4",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
         "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx"}
    return m.get((mime or "").split(";")[0].strip(), fallback)


# ------------------------------------------------- Chromium Simple Cache parse
def _find_body_by_crc(data, body_start):
    n = len(data)
    positions = []
    s = body_start
    while True:
        p = data.find(EOF_MAGIC_BYTES, s)
        if p < 0:
            break
        positions.append(p)
        s = p + 1
    crc_acc, cur, fallback = 0, body_start, None
    for p in positions:
        if p + 20 > n:
            continue
        if p > cur:
            crc_acc = zlib.crc32(data[cur:p], crc_acc) & 0xFFFFFFFF
            cur = p
        flags, dcrc, ssize = struct.unpack_from("<IIi", data, p + 8)
        if (flags & FLAG_HAS_CRC32) and crc_acc == dcrc:
            return data[body_start:p], True
        if ssize == (p - body_start) and ssize > 0 and fallback is None:
            fallback = data[body_start:p]
    return (fallback, False) if fallback is not None else (None, False)


def parse_chromium_entry(data):
    if len(data) < 28 or struct.unpack_from("<Q", data, 0)[0] != SIMPLE_INITIAL_MAGIC:
        return None
    key_length = struct.unpack_from("<I", data, 12)[0]
    if key_length <= 0 or key_length > len(data):
        return None
    mpos = data.find(MARKER)
    if mpos < 0:
        return None
    best = None
    for ks in (20, 24):
        if not (ks <= mpos < ks + key_length) or ks + key_length > len(data):
            continue
        body, verified = _find_body_by_crc(data, ks + key_length)
        if body is None:
            continue
        hit = (body, verified)
        if verified:
            return hit
        best = best or hit
    return best


def carve_bodies(data):
    out = []
    i = 0
    while True:  # PNG
        s = data.find(b"\x89PNG\r\n\x1a\n", i)
        if s < 0:
            break
        e = data.find(b"IEND", s)
        if e > 0:
            out.append(data[s:e + 8])
        i = s + 8
    s = data.find(b"\xff\xd8\xff")  # JPEG
    if s >= 0:
        ends, j = [], s + 2
        while True:
            e = data.find(b"\xff\xd9", j)
            if e < 0:
                break
            ends.append(e)
            j = e + 2
        for e in reversed(ends[-8:]):
            out.append(data[s:e + 2])
    s = data.find(b"%PDF-")  # PDF
    if s >= 0:
        e = data.rfind(b"%%EOF")
        if e > s:
            out.append(data[s:e + 5])
            out.append(data[s:e + 6])
    if data[:6] in (b"GIF87a", b"GIF89a"):  # GIF
        e = data.rfind(b"\x3b")
        if e > 0:
            out.append(data[:e + 1])
    return out


# ----------------------------------------------------------- cache discovery
def candidate_roots():
    home = Path.home()
    sysname = platform.system()
    r = []
    if sysname == "Darwin":
        caches, appsup = home / "Library" / "Caches", home / "Library" / "Application Support"
        cont = home / "Library" / "Containers"
        for b in ["Google/Chrome", "Chromium", "Microsoft Edge", "BraveSoftware/Brave-Browser",
                  "Vivaldi", "Vivaldi Snapshot", "com.operasoftware.Opera",
                  "company.thebrowser.Browser"]:
            r += [("chromium", caches / b), ("chromium", appsup / b)]
        r += [("firefox", caches / "Firefox" / "Profiles"),
              ("firefox", appsup / "Firefox" / "Profiles"),
              ("safari", caches / "com.apple.Safari"), ("safari", caches / "WebKit"),
              ("safari", cont / "com.apple.Safari" / "Data" / "Library" / "Caches")]
    elif sysname == "Windows":
        lad = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        rap = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        for b in [r"Google\Chrome\User Data", r"Microsoft\Edge\User Data",
                  r"BraveSoftware\Brave-Browser\User Data", r"Chromium\User Data",
                  r"Vivaldi\User Data", r"Vivaldi Snapshot\User Data",
                  r"Opera Software\Opera Stable"]:
            r.append(("chromium", lad / b))
        r += [("firefox", lad / r"Mozilla\Firefox\Profiles"),
              ("firefox", rap / r"Mozilla\Firefox\Profiles")]
    else:  # Linux / BSD
        cache, conf = home / ".cache", home / ".config"
        for b in ["google-chrome", "chromium", "microsoft-edge",
                  "BraveSoftware/Brave-Browser", "vivaldi", "vivaldi-snapshot", "opera"]:
            r += [("chromium", cache / b), ("chromium", conf / b)]
        r += [("firefox", home / ".mozilla" / "firefox"),
              ("firefox", cache / "mozilla" / "firefox")]
        snap = home / "snap"
        r += [("chromium", snap / "chromium" / "common" / ".cache" / "chromium"),
              ("firefox", snap / "firefox" / "common" / ".cache" / "mozilla" / "firefox"),
              ("firefox", snap / "firefox" / "common" / ".mozilla" / "firefox")]
        fp = home / ".var" / "app"
        r += [("chromium", fp / "com.google.Chrome" / "cache" / "google-chrome"),
              ("chromium", fp / "com.brave.Browser" / "cache" / "BraveSoftware" / "Brave-Browser"),
              ("chromium", fp / "com.microsoft.Edge" / "cache" / "microsoft-edge"),
              ("chromium", fp / "org.chromium.Chromium" / "cache" / "chromium"),
              ("chromium", fp / "com.vivaldi.Vivaldi" / "cache" / "vivaldi"),
              ("firefox", fp / "org.mozilla.firefox" / "cache" / "mozilla" / "firefox")]
    return r


def find_cache_dirs(kind, root):
    dirs = []
    if not root.exists():
        return dirs
    try:
        for dp, _dn, _fn in os.walk(root):
            base = os.path.basename(dp)
            if kind == "chromium" and base == "Cache_Data":
                dirs.append(Path(dp))
            elif kind == "firefox" and base == "entries" and \
                    os.path.basename(os.path.dirname(dp)) == "cache2":
                dirs.append(Path(dp))
            elif kind == "safari":
                dirs.append(Path(dp))
    except OSError:
        pass
    return dirs


def iter_targets(cache_dir_override):
    if cache_dir_override:
        p = Path(cache_dir_override)
        kind = "firefox" if "cache2" in str(p) else \
               ("safari" if ("WebKit" in str(p) or "Safari" in str(p)) else "chromium")
        yield (kind, p)
        return
    for kind, root in candidate_roots():
        for d in find_cache_dirs(kind, root):
            yield (kind, d)


BROWSER_TOKENS = ["Chrome", "Chromium", "Edge", "Brave", "Vivaldi", "Opera",
                  "Safari", "WebKit", "Firefox", "Arc"]


def browser_label(path):
    s = str(path)
    for t in BROWSER_TOKENS:
        if t in s:
            return t
    return "browser"


def safari_cache_blocked():
    """macOS only: True if Safari's cache exists but is unreadable without Full
    Disk Access. (Apple provides no API to *request* FDA, so we can only detect
    the block and guide the user to the Settings pane.)"""
    if platform.system() != "Darwin":
        return False
    home = Path.home()
    probes = [
        home / "Library" / "Containers" / "com.apple.Safari" / "Data" / "Library" / "Caches",
        home / "Library" / "Caches" / "com.apple.Safari",
    ]
    for p in probes:
        try:
            list(os.scandir(p))            # works with FDA, EPERM without it
        except PermissionError:
            return True
        except OSError:
            pass                           # missing / other -> not a TCC block
    return False


def open_full_disk_access_settings():
    """Open the exact macOS 'Full Disk Access' settings pane."""
    try:
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"],
            check=False)
        return True
    except Exception:  # noqa
        return False


# -------------------------------------------------------------- target hashes
def bundled_hashes_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "target_hashes.txt")
    return p if os.path.exists(p) else None


def load_target_set(args):
    """Return (sha3_set, cid_set, name_source_or_None)."""
    if args.db:
        sha3_set, cid_set = set(), set()
        with open(args.db, newline="", encoding="utf-8", errors="replace") as f:
            rd = csv.reader(f)
            hdr = next(rd, None)
            ix = ({n: i for i, n in enumerate(hdr)} if hdr else {}).get("externalID", 8)
            for row in rd:
                if len(row) > ix and row[ix]:
                    (cid_set if row[ix].startswith("Qm") else sha3_set).add(row[ix])
        return sha3_set, cid_set, args.db
    src = args.hashes or bundled_hashes_path()
    if not src:
        raise SystemExit("No hash list. Provide --hashes target_hashes.txt (or bundle it).")
    sha3_set, cid_set = set(), set()
    with open(src, encoding="utf-8") as f:
        for line in f:
            h = line.strip()
            if h and not h.startswith("#"):
                (cid_set if h.startswith("Qm") else sha3_set).add(h)
    return sha3_set, cid_set, None


def resolve_names(db_path, hashes):
    by_hash = {}
    with open(db_path, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.reader(f)
        hdr = next(rd, None)
        col = {n: i for i, n in enumerate(hdr)} if hdr else {}
        i_name, i_mime, i_size, i_ext = (col.get("displayName", 5), col.get("mimeType", 6),
                                         col.get("size", 7), col.get("externalID", 8))
        for row in rd:
            if len(row) > i_ext and row[i_ext] in hashes and row[i_ext] not in by_hash:
                by_hash[row[i_ext]] = (row[i_name], row[i_mime], row[i_size])
    return by_hash


# ----------------------------------------------------------------- core scan
def collect_hits(args, target, progress=None):
    """Scan all caches, return (found_list, stats, denied, locked). Pure logic --
    no printing or prompting. `progress(scanned, browser)` is called per dir."""
    sha3_set, cid_set = target
    use_cid = bool(cid_set)
    hits, stats, denied, locked = {}, Counter(), Counter(), 0
    is_mac = platform.system() == "Darwin"

    def _is_tcc(path):
        s = str(path)
        return is_mac and ("Safari" in s or "WebKit" in s or "Containers" in s)

    for kind, cdir in iter_targets(args.cache_dir):
        if progress:
            progress(stats["scanned"], browser_label(cdir))
        try:
            entries = list(os.scandir(cdir))
        except PermissionError:
            denied[browser_label(cdir)] += 1
            continue
        except OSError:
            continue
        for de in entries:
            try:
                if not de.is_file(follow_symlinks=False):
                    continue
                sz = de.stat().st_size
                if sz == 0 or sz > args.max_bytes:
                    continue
                with open(de.path, "rb") as fh:
                    data = fh.read()
            except PermissionError:
                if _is_tcc(cdir):
                    denied[browser_label(cdir)] += 1
                else:
                    locked += 1
                continue
            except OSError:
                continue
            stats["scanned"] += 1
            candidates = []
            if kind == "chromium" and len(data) >= 8 and MARKER in data and \
                    struct.unpack_from("<Q", data, 0)[0] == SIMPLE_INITIAL_MAGIC:
                res = parse_chromium_entry(data)
                if res:
                    candidates.append((res[0], res[1]))
            if kind in ("safari", "firefox") or (kind == "chromium" and MARKER in data):
                for body in carve_bodies(data):
                    candidates.append((body, False))
            for body, crc_ok in candidates:
                h = sha3_hex(body)
                matched = h if h in sha3_set else None
                if not matched and use_cid:
                    c = cidv0(body)
                    matched = c if c in cid_set else None
                if not matched or matched in hits:
                    continue
                hits[matched] = {"hash": matched, "body": body, "size": len(body),
                                 "ext": guess_ext(body), "crc_verified": crc_ok,
                                 "browser": browser_label(cdir), "source": de.path}
    return list(hits.values()), stats, denied, locked


def write_bundle(found, args, name_source):
    """Write files + manifest + zip. Returns (zip_path, named_count, out_dir)."""
    name_by_hash = resolve_names(name_source, {h["hash"] for h in found}) if name_source else {}
    out_dir = Path(args.out).expanduser()
    files_dir = out_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    used, rows, named = Counter(), [], 0
    for h in found:
        rec = name_by_hash.get(h["hash"])
        if rec:
            disp, mime, _dbsize = rec
            named += 1
            stem = Path(disp).stem or h["hash"][:16]
            name = f"{stem}.{Path(disp).suffix.lstrip('.') or ext_from_mime(mime, h['ext'])}"
        else:
            disp = mime = ""
            name = f"{h['hash'][:32]}.{h['ext']}"
        used[name] += 1
        if used[name] > 1:
            p = Path(name)
            name = f"{p.stem}__{used[name]}{p.suffix}"
        (files_dir / name).write_bytes(h["body"])
        rows.append({"saved_as": name, "content_hash": h["hash"], "size": h["size"],
                     "ext": h["ext"], "crc_verified": h["crc_verified"], "browser": h["browser"],
                     "db_filename": disp, "mime": mime, "source_cache_file": h["source"]})
    with open(out_dir / "manifest.csv", "w", newline="", encoding="utf-8") as mf:
        w = csv.DictWriter(mf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out_dir / "README.txt").write_text(
        "These files were recovered from this computer's browser cache because their "
        "content exactly matched Alkemio's list of lost files.\nPlease send the .zip "
        "next to this folder back to the Alkemio recovery team.\n")
    zip_path = Path(str(out_dir) + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(out_dir.parent))
    return zip_path, named, out_dir


# --------------------------------------------------------------- CLI front end
def scan_cli(args):
    sha3_set, cid_set, name_source = load_target_set(args)
    target = (sha3_set, cid_set)
    print(f"Looking for {len(target[0]) + len(target[1]):,} lost Alkemio files in your "
          f"browser caches ...")
    found, stats, denied, locked = collect_hits(args, target)
    print(f"\nScanned {stats['scanned']:,} cache files across your browsers.")
    if locked:
        msg = ("Close ALL browser windows and run again to check them."
               if platform.system() == "Windows" else "Close browsers and re-run.")
        print(f"  NOTE: {locked:,} cache files were locked. {msg}")
    if denied:
        print("  Some data needs extra permission: "
              + ", ".join(f"{b}({c})" for b, c in denied.items()))
        if platform.system() == "Darwin":
            print("  For Safari, grant Full Disk Access, then re-run. Open the pane with:")
            print("    open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'")
            print("  Enable this app there (if still locked, quit and reopen it).")
    if not found:
        print("\nNo recoverable Alkemio files were found. Nothing created or sent.")
        return 0
    total = sum(h["size"] for h in found)
    print(f"\nFound {len(found)} recoverable file(s), {human_size(total)}:")
    print("  browsers: " + ", ".join(f"{b}({c})" for b, c in
          Counter(h['browser'] for h in found).most_common()))
    for i, h in enumerate(sorted(found, key=lambda x: -x["size"]), 1):
        print(f"  {i:<3} .{h['ext']:<5} {human_size(h['size']):>9}   {h['hash'][:24]}...")
    if not args.yes:
        try:
            if input("\nCreate the recovery .zip now? [y/N]: ").strip().lower() not in ("y", "yes"):
                print("Nothing was written.")
                return 0
        except EOFError:
            pass
    zip_path, named, _ = write_bundle(found, args, name_source)
    print(f"\nDone. Saved {len(found)} file(s) ({named} named). Send this to Alkemio:\n  {zip_path}")
    return 0


# --------------------------------------------------------------- GUI front end
def run_gui(args):
    try:
        import threading
        import queue
        import subprocess
        import tkinter as tk
        from tkinter import ttk, scrolledtext
    except Exception:
        print("Graphical mode unavailable on this system; running in text mode.\n")
        return scan_cli(args)

    sha3_set, cid_set, name_source = load_target_set(args)
    target = (sha3_set, cid_set)
    q = queue.Queue()
    st = {"found": None}

    root = tk.Tk()
    root.title("Alkemio File Recovery")
    root.geometry("680x560")
    root.minsize(560, 460)

    pad = {"padx": 16, "pady": (6, 0)}
    tk.Label(root, text="Alkemio File Recovery", font=("Helvetica", 18, "bold")
             ).pack(anchor="w", **pad)
    tk.Label(root, justify="left", wraplength=640, fg="#444",
             text=("This looks in your browser cache for files Alkemio lost, and ONLY "
                   "files that exactly match Alkemio's list. It does not read anything "
                   "else, makes no internet connection, and changes nothing. At the end "
                   "it saves one file that you can choose to send back.")
             ).pack(anchor="w", padx=16, pady=(2, 10))

    # Safari / Full Disk Access banner (created hidden; shown only when needed).
    perm = tk.Frame(root, bg="#FFF4CE", bd=1, relief="solid")
    tk.Label(perm, bg="#FFF4CE", fg="#5b4a00", justify="left", wraplength=600,
             text=("Safari is locked by macOS. To also recover Safari files, give this "
                   "app Full Disk Access, then scan again. (Chrome, Edge, Brave and "
                   "Firefox work without it.)")
             ).pack(anchor="w", padx=10, pady=(8, 4))
    perm_row = tk.Frame(perm, bg="#FFF4CE")
    perm_row.pack(anchor="w", padx=10, pady=(0, 8))
    perm_settings_btn = tk.Button(perm_row, text="Open macOS Settings")
    perm_rescan_btn = tk.Button(perm_row, text="I enabled it — scan again")
    perm_settings_btn.pack(side="left", padx=(0, 8))
    perm_rescan_btn.pack(side="left")

    status = tk.StringVar(value="Ready. Close your browsers first for best results.")
    bar = ttk.Progressbar(root, mode="indeterminate")
    results = scrolledtext.ScrolledText(root, height=15, wrap="word", state="disabled",
                                        font=("Menlo", 11))

    btns = tk.Frame(root)
    scan_btn = tk.Button(btns, text="Scan my browser caches")
    create_btn = tk.Button(btns, text="Create recovery file", state="disabled")
    open_btn = tk.Button(btns, text="Open folder", state="disabled")
    quit_btn = tk.Button(btns, text="Quit", command=root.destroy)
    for b in (scan_btn, create_btn, open_btn, quit_btn):
        b.pack(side="left", padx=(0, 8))

    status_lbl = tk.Label(root, textvariable=status, anchor="w", fg="#222")
    status_lbl.pack(fill="x", padx=16, pady=(8, 2))
    bar.pack(fill="x", padx=16)

    def show_perm_banner(show_it):
        if show_it:
            perm.pack(fill="x", padx=16, pady=(0, 8), before=status_lbl)
        else:
            perm.pack_forget()
    results.pack(fill="both", expand=True, padx=16, pady=10)
    btns.pack(anchor="w", padx=16, pady=(0, 14))

    def show(text, clear=False):
        results.config(state="normal")
        if clear:
            results.delete("1.0", "end")
        results.insert("end", text)
        results.config(state="disabled")
        results.see("end")

    def worker():
        found, stats, denied, locked = collect_hits(
            args, target, lambda n, b: q.put(("prog", n, b)))
        q.put(("done", found, stats, denied, locked))

    def start_scan():
        scan_btn.config(state="disabled")
        create_btn.config(state="disabled")
        open_btn.config(state="disabled")
        show("", clear=True)
        bar.start(12)
        status.set("Scanning your browser caches...")
        threading.Thread(target=worker, daemon=True).start()

    def on_done(found, stats, denied, locked):
        bar.stop()
        st["found"] = found
        notes = ""
        if locked:
            notes += (f"\nNote: {locked:,} cache files were locked (a browser is open). "
                      "Close all browsers and scan again to check those too.\n")
        safari_blocked = bool(denied) and platform.system() == "Darwin"
        show_perm_banner(safari_blocked)
        if safari_blocked:
            notes += ("\nSafari is locked by macOS. Use the highlighted bar above to "
                      "grant Full Disk Access, then click 'scan again'.\n")
        if not found:
            status.set("No recoverable Alkemio files were found.")
            show("Nothing matched. Nothing was created or sent. Thank you for checking."
                 + notes, clear=True)
            return
        total = sum(h["size"] for h in found)
        bt = Counter(h["browser"] for h in found)
        status.set(f"Found {len(found)} file(s), {human_size(total)}. "
                   "Click 'Create recovery file' to save them.")
        show(f"{len(found)} files match Alkemio's lost-file list (exact content match).\n"
             f"Browsers: " + ", ".join(f"{b}({c})" for b, c in bt.items()) + "\n\n",
             clear=True)
        for i, h in enumerate(sorted(found, key=lambda x: -x["size"]), 1):
            show(f"{i:>3}.  .{h['ext']:<5} {human_size(h['size']):>9}   {h['hash'][:20]}...\n")
        if notes:
            show(notes)
        create_btn.config(state="normal")

    def do_create():
        try:
            zip_path, named, _ = write_bundle(st["found"], args, name_source)
        except Exception as e:  # noqa
            status.set("Could not write the file.")
            show(f"\nError: {e}\n")
            return
        st["zip"] = zip_path
        status.set("Saved. Please send this file to Alkemio.")
        show(f"\nSaved {len(st['found'])} file(s).\nFile to send to Alkemio:\n  {zip_path}\n")
        create_btn.config(state="disabled")
        open_btn.config(state="normal")

    def open_folder():
        folder = str(Path(st["zip"]).parent)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", folder])
            elif os.name == "nt":
                os.startfile(folder)  # noqa
            else:
                subprocess.run(["xdg-open", folder])
        except Exception:  # noqa
            pass

    def poll():
        try:
            while True:
                msg = q.get_nowait()
                if msg[0] == "prog":
                    status.set(f"Scanning {msg[2]}... {msg[1]:,} files checked")
                elif msg[0] == "done":
                    on_done(*msg[1:])
        except queue.Empty:
            pass
        root.after(120, poll)

    def on_open_settings():
        open_full_disk_access_settings()
        status.set("In Settings: turn ON this app under Full Disk Access, then come "
                   "back and click 'scan again'. (If it stays locked, quit and reopen "
                   "this app.)")

    scan_btn.config(command=start_scan)
    create_btn.config(command=do_create)
    open_btn.config(command=open_folder)
    perm_settings_btn.config(command=on_open_settings)
    perm_rescan_btn.config(command=start_scan)

    # Proactive check: if Safari is locked, show the banner before scanning so the
    # user can grant access up front and recover everything in one pass.
    if safari_cache_blocked():
        show_perm_banner(True)
        status.set("Safari is locked by macOS. Grant Full Disk Access (bar above) to "
                   "include Safari, or just click Scan for the other browsers.")

    root.after(120, poll)
    root.mainloop()
    return 0


# ------------------------------------------------------------------ self-test
def _synth(url, body, hsize=24):
    key = url.encode()
    hdr = struct.pack("<QIII", SIMPLE_INITIAL_MAGIC, 5, len(key), zlib.crc32(key) & 0xFFFFFFFF)
    if hsize == 24:
        hdr += b"\x00\x00\x00\x00"
    eof1 = struct.pack("<QIIi", SIMPLE_EOF_MAGIC, FLAG_HAS_CRC32, zlib.crc32(body) & 0xFFFFFFFF, 0)
    s0 = b"HTTP/1.1 200\x00content-type:image/jpeg"
    eof0 = struct.pack("<QIIi", SIMPLE_EOF_MAGIC, FLAG_HAS_CRC32 | FLAG_HAS_KEY_SHA256,
                       zlib.crc32(s0) & 0xFFFFFFFF, len(s0))
    return hdr + key + body + eof1 + s0 + eof0 + b"\x11" * 32


def selftest():
    ok = True
    url = ("1/0/_dk_https://alkem.io https://alkem.io https://alkem.io"
           "/api/private/rest/storage/document/11111111-2222-3333-4444-555555555555")
    body = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 60 + b"\xff\xd9"
    for hs in (20, 24):
        r = parse_chromium_entry(_synth(url, body, hs))
        good = bool(r) and r[0] == body and r[1]
        print(f"  chromium header={hs}B: {'PASS' if good else 'FAIL'}")
        ok &= good
    png = b"\x89PNG\r\n\x1a\n" + bytes(400) + b"IEND" + b"\xae\x42\x60\x82"
    jpg = b"\xff\xd8\xff\xe0" + bytes(300) + b"\xff\xd9"
    carved = {sha3_hex(b) for b in carve_bodies(b"x" + png + b"yy" + jpg)}
    cg = sha3_hex(png) in carved and sha3_hex(jpg) in carved
    print(f"  carve PNG+JPEG: {'PASS' if cg else 'FAIL'}")
    ok &= cg
    cid = cidv0(b"hello").startswith("Qm")
    print(f"  CIDv0 encode: {'PASS' if cid else 'FAIL'}")
    ok &= cid
    print("\nSELFTEST:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="Recover lost Alkemio files from this computer's browser caches.")
    ap.add_argument("--out", default=str(Path.home() / "alkemio-recovered"),
                    help="output folder (default: alkemio-recovered in your home dir)")
    ap.add_argument("--hashes", default=None, help="external hash list (one per line)")
    ap.add_argument("--db", default=None, help="recovery team: prod file.csv -> verify + name")
    ap.add_argument("--cache-dir", default=None, help="scan only this directory")
    ap.add_argument("--cli", action="store_true", help="force text mode (no window)")
    ap.add_argument("--yes", action="store_true", help="CLI: skip the consent prompt")
    ap.add_argument("--max-bytes", type=int, default=1024 * 1024 * 1024)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--paths", action="store_true", help="show scanned locations and exit")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.paths:
        print(f"Platform: {platform.platform()}\nScanning these locations:")
        for kind, cdir in iter_targets(args.cache_dir):
            print(f"  [{kind:8s}] {cdir}")
        return 0
    if args.cli or args.db or args.yes:
        return scan_cli(args)
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
