#!/usr/bin/env python3
"""Generate target_hashes.txt (the embedded lost-file list) from a CSV export.

The CSV may be either:
  * the full `file` table (with a header incl. an `externalID` column), or
  * a 2-3 column dump `id,externalID[,extra]` with NO header.

Only the content-hash column (externalID: 64-hex SHA3-256, or `Qm...` IPFS CIDv0)
is used. NO filenames or any other field are written -- the output is hashes only,
which is what gets embedded in the client tool.

    python3 gen_hashes.py /path/alkemio_files_since_april30.csv
    python3 gen_hashes.py /path/file.csv --out target_hashes.txt
"""
import argparse
import csv
import re
import sys

csv.field_size_limit(10 * 1024 * 1024)
HEXRE = re.compile(r"^[0-9a-f]{64}$")
CIDRE = re.compile(r"^Qm[1-9A-HJ-NP-Za-km-z]{44}$")


def is_hash(v):
    return bool(HEXRE.match(v) or CIDRE.match(v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="target_hashes.txt")
    a = ap.parse_args()

    seen, order = set(), []
    with open(a.csv, newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    if not rows:
        sys.exit("empty CSV")

    # locate the externalID column
    header = rows[0]
    col = None
    start = 0
    if "externalID" in header:
        col = header.index("externalID")
        start = 1
    else:
        # headerless: pick the column whose first data cell looks like a hash
        for i, v in enumerate(header):
            if is_hash(v.strip()):
                col = i
                break
        if col is None:
            sys.exit("could not find an externalID/hash column")

    for r in rows[start:]:
        if len(r) <= col:
            continue
        v = r[col].strip()
        if is_hash(v) and v not in seen:
            seen.add(v)
            order.append(v)

    with open(a.out, "w", encoding="utf-8") as o:
        o.write("\n".join(order) + "\n")
    sha3 = sum(1 for v in order if HEXRE.match(v))
    cid = len(order) - sha3
    print(f"wrote {a.out}: {len(order)} hashes ({sha3} sha3-256, {cid} ipfs-cid) "
          f"-- no filenames")


if __name__ == "__main__":
    main()
