# Alkemio browser-cache file recovery

After the file-service storage **and** its backups were lost, some uploaded
files may still exist in end users' **browser caches** from when they last
viewed them. This tool finds and rescues them.

It ships as a **double-click GUI app** (no terminal) for Windows / macOS / Linux.
It's the only viable browser-side recovery path: client-web persists nothing of
its own to disk (no Service Worker cache, no IndexedDB, no Apollo persistence) —
the bytes only ever land in the browser's native HTTP cache, which a web page
**cannot** read. So this is a small native program users run locally.

## Download (latest build)

CI publishes the newest build to the [**latest release**](https://github.com/alkem-io/client-recovery-tool/releases/tag/latest):

| OS | Download | Run |
|---|---|---|
| **Windows** | [`alkemio-recover-windows.exe`](https://github.com/alkem-io/client-recovery-tool/releases/download/latest/alkemio-recover-windows.exe) | double-click → "More info" → **Run anyway** (unsigned) |
| **macOS** | [`alkemio-recover-macos.zip`](https://github.com/alkem-io/client-recovery-tool/releases/download/latest/alkemio-recover-macos.zip) | unzip, then **clear quarantine** (see below) — the app is unsigned |
| **Linux** | [`alkemio-recover-linux`](https://github.com/alkem-io/client-recovery-tool/releases/download/latest/alkemio-recover-linux) | `chmod +x alkemio-recover-linux && ./alkemio-recover-linux` |

> These links point at a rolling `latest` release refreshed on every push to
> `main`. This repo is private, so downloading requires GitHub access.

### First launch on macOS (required — the app is unsigned)

Because the app is **not signed/notarized**, macOS quarantines every download and
blocks it. You must clear that **once per download**. Easiest and most reliable
(Terminal):

```bash
xattr -dr com.apple.quarantine ~/Downloads/alkemio-recover.app
open ~/Downloads/alkemio-recover.app
```

Or without Terminal: **right-click** the app → **Open**; if macOS still refuses,
go to **System Settings → Privacy & Security**, scroll to the "…was blocked"
notice, click **Open Anyway**, then open it again.

*(This manual step is unavoidable while the app is unsigned — the only way to
remove it entirely is to code-sign + notarize the app; see [Code signing](#code-signing-removes-the-os-unidentified-developer-warnings).)*

## How it works

* The web client loads uploaded files as plain requests to
  `https://alkem.io/api/private/rest/storage/document/<uuid>`, served cacheable.
  Those response bodies sit in the browser's on-disk HTTP cache. (`max-age`
  controls freshness, not deletion — bytes linger until LRU eviction.)
* file-service is content-addressed: `file.externalID` = `SHA3-256(bytes)`
  (older blobs use an IPFS CIDv0). The app is given **only the hashes** of the
  lost files. It extracts a cached blob **only when its content hash exactly
  matches a lost-file hash** — which simultaneously (a) verifies the bytes are
  intact, (b) scopes to exactly what we need, and (c) guarantees it can't pick
  up any of the user's unrelated personal files.

### Coverage (all verified on real cache data)

| Browser | Cache format | Method | Status |
|---|---|---|---|
| Chrome / Brave / Edge / Vivaldi / Opera / Arc | Chromium *Simple Cache* | exact body via internal CRC32 | ✅ |
| Firefox | `cache2` | structural carve (PNG/JPEG/PDF/GIF) | ✅ |
| Safari (macOS) | WebKit `NetworkCache` / blobs | structural carve | ✅ |

Per-OS paths (Windows `%LocalAppData%`, macOS `~/Library/Caches` + Safari
container, Linux `~/.cache` + **Snap**/**Flatpak** roots) are auto-discovered
across **all browser profiles**.

### What it can / cannot recover

* **Can:** images, avatars, banners, and uploaded attachments (incl. PDFs/Office
  files a user *downloaded* via a storage URL) still in cache.
* **Cannot:** live-edited Collabora office documents — they stream server-side
  through the WOPI iframe and are never cached by the browser.

## Privacy / safety (state this to users)

* Read-only; never modifies or deletes any browser data.
* Has **only hashes**, no filenames — it cannot tell what any file is.
* Extracts **only** blobs whose content matches a known lost-file hash.
* **No network.** It writes one local `.zip` and asks first. The user decides
  whether to send that zip back.

## End-user experience

1. Download the app for their OS and open it (see signing notes below).
2. A small window appears: **"Scan my browser caches."**
3. It lists what it found (type / size / fingerprint — **no names**), then
   offers **"Create recovery file."**
4. It saves one `.zip` in the user's home folder and shows an **"Open folder"**
   button. The user sends that `.zip` back to the recovery team.

Tell users to **close all browser windows first** (a running browser locks its
cache files).

**macOS Safari needs Full Disk Access.** macOS provides *no API to trigger the
Full Disk Access prompt* (Apple disallows requesting it programmatically), so
the app can't pop it automatically. Instead, when it detects Safari is blocked
it shows a highlighted banner with an **"Open macOS Settings"** button that
jumps straight to the Full Disk Access pane, plus an **"I enabled it — scan
again"** button. The user toggles the app on there; if Safari still shows
locked, they **quit and reopen** the app (TCC applies the grant on relaunch).
Chrome/Brave/Edge/Firefox recover **without** Full Disk Access.

> Note: code-signing + notarizing the app (see below) can additionally surface
> macOS's "access data from other apps" prompt for the Safari container, but
> Full Disk Access remains the reliable mechanism.

## Build (produces one standalone app; clients install NOTHING)

PyInstaller bundles the Python runtime, **Tcl/Tk**, and the embedded hash list
into a single app. It **cannot cross-compile**, so build on each OS (or use CI).
The **build machine's** Python must have **Tkinter 8.6+** (clients don't need it):

* macOS — **do NOT use `/usr/bin/python3`**: it ships Tk 8.5, which *aborts at
  launch on macOS 26+* (`"macOS 26 required, have 16"`, SIGABRT). Use a Tk 8.6/9
  Python: `brew install python-tk@3.13` then
  `PYTHON=/opt/homebrew/bin/python3.13`, or python.org Python.
* Windows — the python.org installer includes it (keep "tcl/tk and IDLE" ticked).
* Linux — install the distro Tk package (e.g. `sudo apt-get install python3-tk`).

```bash
# 0. generate the embedded hash list from a DB export (hashes only, no names)
python3 gen_hashes.py /path/alkemio_files_since_april30.csv   # -> target_hashes.txt

# 1a. macOS  -> dist/alkemio-recover.app   (use a Tk 8.6+/9 Python, NOT /usr/bin/python3)
brew install python-tk@3.13
PYTHON=/opt/homebrew/bin/python3.13 ./packaging/build.sh

# 1a'. Linux -> dist/alkemio-recover       (sudo apt-get install -y python3-tk first)
./packaging/build.sh

# 1b. Windows (PowerShell)       -> dist\alkemio-recover.exe  (no console window)
./packaging/build.ps1

# 1c. all three at once: the GitHub Actions workflow at .github/workflows/build.yml
#     builds win/mac/linux and uploads alkemio-recover-{windows,macos,linux}
#     artifacts. Runs on push to main / PR, or trigger it from the Actions tab.
```

Verify any build: `…/alkemio-recover --selftest` (on macOS, run the inner
`alkemio-recover.app/Contents/MacOS/alkemio-recover`).

### Code signing (removes the OS "unidentified developer" warnings)

The GUI removes the terminal; signing removes the remaining download warnings:

* **macOS** — Gatekeeper blocks unsigned `.app`s. Either `codesign` + notarize,
  or tell users to **right-click → Open** the first time, then **Open**.
* **Windows** — SmartScreen warns on unsigned `.exe`s. Either sign with an
  Authenticode cert, or tell users: **More info → Run anyway**.
* **Linux** — `chmod +x` and run; no gate.

## Recovery-team / power-user mode (command line)

The same binary is also a CLI (use a console; on Windows prefer running the
`.py` since the `--windowed` exe has no console output):

```
alkemio-recover --cli                 # text mode instead of the window
alkemio-recover --paths               # show the cache locations scanned on this OS
alkemio-recover --db /path/file.csv   # verify every blob + name it by displayName
alkemio-recover --selftest            # engine self-check
```

Output (both modes): `files/` — **each file named by its full file-service
storage key** (the `externalID` = full SHA3-256, **no extension**, no truncation) —
plus `manifest.csv` (`saved_as`, `content_hash`, `size`, `crc_verified`, `browser`,
`db_filename`, `mime`, `source_cache_file`), `excluded-by-hash-check.txt`,
`README.txt`, and the `.zip`. `--db` only *adds* the `db_filename` column; it does
**not** change the storage-key filenames.

**`excluded-by-hash-check.txt`** is an audit list: Chromium-family cache entries
that were clearly Alkemio storage objects (their key carried a
`/rest/storage/document/<uuid>` URL) but whose CRC-verified bytes matched **no**
wanted hash — so they were dropped, never restored. Most are simply files outside
the recovery scope; but a mismatch on a file you *did* expect flags a partial /
corrupted / older cached copy worth manual review (run against the **full**
`externalID` set to make this signal meaningful). Safari/Firefox blobs carry no URL,
so they can't appear here.

## Restoring (this is the whole point)

Because file-service is content-addressed and the DB rows survived, a recovered
blob just needs to sit at its storage key — no re-upload, no DB writes:

```bash
cp files/* /storage/          # (or into the storage PVC / bucket prefix)
```

The `file` rows already reference these blobs by `externalID`, so they are served
again automatically. One blob may satisfy **many** rows (heavy dedup: 1.04M rows →
~19.6k unique blobs), so restoring one key can fix thousands of references at once.

> Note: the filename IS the SHA3-256 of the bytes, so you can re-verify any blob
> before copying: `openssl dgst -sha3-256 <file>` must equal its filename. (Legacy
> pre-migration blobs are named by an IPFS CID instead — those verify as CIDv0, not
> SHA3.)

## Files

| File | Purpose |
|---|---|
| `alkemio_cache_recover.py` | the app: GUI + CLI engine (stdlib only; `--selftest`) |
| `gen_hashes.py` | build `target_hashes.txt` from a DB CSV (hashes only) |
| `target_hashes.txt` | embedded lost-file hash list (generated) |
| `packaging/build.sh` / `build.ps1` | per-OS standalone GUI builds |
| `.github/workflows/build.yml` | CI matrix (win/mac/linux) |
