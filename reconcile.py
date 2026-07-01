#!/usr/bin/env python3
"""
reconcile.py — normalize recovered bundles into file-service storage keys.

Point it at any mix of recovered bundles (folders or .zip files, ANY tool version
— short/truncated names, extensions, or full keys) and it lays every unique blob
out named EXACTLY by its storage key (externalID), no extension, so restoring is
just:

    cp restore/* /storage/          # (or into the storage PVC / bucket prefix)

It works by re-hashing each file's *content* (SHA3-256, plus IPFS CIDv0 for legacy
blobs), so it never trusts the bundle's filename. Duplicates across bundles collapse
to one. With a key list it also verifies every blob and quarantines anything that
matches no known key.

    python3 reconcile.py alkemio-recovered*.zip alkemio-recovered*/ --out .
    python3 reconcile.py *.zip --out . --db /path/file.csv     # verify + name legacy CIDs

--db accepts the prod `file` table CSV, the since-<date> CSV, or a plain hash list —
anything containing the externalIDs; it's scanned for 64-hex SHA3 keys and `Qm…`
CIDs. Output: `restore/` (verified, storage-key-named — ready to copy) and, when a
key list is given, `review/` (blobs matching no known key — do NOT restore blindly).
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SHA3_RE = re.compile(r"\b[0-9a-f]{64}\b")
CID_RE = re.compile(r"\bQm[1-9A-HJ-NP-Za-km-z]{44}\b")


def sha3(b):
    return hashlib.sha3_256(b).hexdigest()


def cidv0(b):
    n = int.from_bytes(b"\x12\x20" + hashlib.sha256(b).digest(), "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return out  # CIDv0 has no leading NUL bytes -> no "1" padding needed


def load_keys(db_path):
    text = Path(db_path).read_text(encoding="utf-8", errors="replace")
    return set(SHA3_RE.findall(text)), set(CID_RE.findall(text))


def guess_ext(b):
    if b.startswith(b"%PDF-"): return "pdf"
    if b.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
    if b.startswith(b"\xff\xd8\xff"): return "jpg"
    if b[:6] in (b"GIF87a", b"GIF89a"): return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "webp"
    if b[:2] == b"PK": return "zip"
    return "bin"


def iter_blob_files(inputs, workdir):
    """Yield every recovered blob file across inputs (dirs/zips). Blobs live in a
    `files/` folder in every bundle version; metadata (manifest/README/excluded)
    sits outside it and is skipped."""
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.parent.name == "files":
                    yield f
        elif p.is_file() and p.suffix.lower() == ".zip":
            ex = Path(tempfile.mkdtemp(dir=workdir))
            try:
                with zipfile.ZipFile(p) as z:
                    z.extractall(ex)
            except zipfile.BadZipFile:
                print(f"  ! skipping unreadable zip: {p}", file=sys.stderr)
                continue
            for f in ex.rglob("*"):
                if f.is_file() and f.parent.name == "files":
                    yield f
        elif p.is_file():
            yield p   # a bare blob file passed directly


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="bundle .zip files and/or folders")
    ap.add_argument("--out", default=".", help="output dir (creates restore/ [+ review/])")
    ap.add_argument("--db", default=None, help="key list / file.csv to verify + name legacy CIDs")
    args = ap.parse_args()

    sha3_keys, cid_keys = (load_keys(args.db) if args.db else (set(), set()))
    have_db = bool(args.db)
    if have_db:
        print(f"Loaded {len(sha3_keys):,} SHA3 + {len(cid_keys):,} CID known keys.")

    out = Path(args.out).expanduser()
    restore = out / "restore"
    review = out / "review"
    restore.mkdir(parents=True, exist_ok=True)

    seen, restored, reviewed, scanned, dups = set(), 0, 0, 0, 0
    with tempfile.TemporaryDirectory() as workdir:
        for f in iter_blob_files(args.inputs, workdir):
            try:
                body = f.read_bytes()
            except OSError:
                continue
            scanned += 1
            h = sha3(body)
            key = None
            if not have_db:
                key = h  # no list: assume SHA3 key (correct for all non-legacy blobs)
            elif h in sha3_keys:
                key = h
            elif cidv0(body) in cid_keys:
                key = cidv0(body)
            if key:
                if key in seen:
                    dups += 1
                    continue
                seen.add(key)
                (restore / key).write_bytes(body)   # storage key, no extension
                restored += 1
            else:  # matches no known key: quarantine for review, never auto-restore
                review.mkdir(parents=True, exist_ok=True)
                if h not in seen:
                    seen.add(h)
                    (review / f"{h}.{guess_ext(body)}").write_bytes(body)
                    reviewed += 1

    print(f"\nscanned {scanned} file(s) across the bundles")
    print(f"  -> restore/ : {restored} unique blob(s), named by storage key "
          f"({'verified against DB' if have_db else 'SHA3; pass --db to verify + name legacy CIDs'})")
    if dups:
        print(f"  (dropped {dups} duplicate copies across bundles)")
    if have_db and reviewed:
        print(f"  -> review/  : {reviewed} blob(s) matched NO known key — inspect, do NOT restore blindly")
    print(f"\nRestore with:  cp {restore}/* /storage/")


if __name__ == "__main__":
    main()
