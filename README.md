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
| **macOS** | [`alkemio-recover-macos.zip`](https://github.com/alkem-io/client-recovery-tool/releases/download/latest/alkemio-recover-macos.zip) | unzip → **right-click** the `.app` → **Open** (unsigned). Grant Full Disk Access for Safari (see below) |
| **Linux** | [`alkemio-recover-linux`](https://github.com/alkem-io/client-recovery-tool/releases/download/latest/alkemio-recover-linux) | `chmod +x alkemio-recover-linux && ./alkemio-recover-linux` |

> These links point at a rolling `latest` release refreshed on every push to
> `main`. This repo is private, so downloading requires GitHub access. The
> binaries are **unsigned** — see [Code signing](#code-signing-removes-the-os-unidentified-developer-warnings)
> for how to remove the OS warnings, or use the right-click/Run-anyway steps above.

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

`--db` output: `files/` (named), `manifest.csv` (saved_as, content_hash, size,
crc_verified, browser, db_filename, source_cache_file), `README.txt`, `.zip`.

## Reconciling returned bundles

Each client returns a `.zip` of hash-named blobs. A recovered blob's
`content_hash` matches `file.externalID`, and one blob may satisfy **many** file
rows (heavy dedup: 1.04M rows → ~19.6k unique blobs). Join
`content_hash → file.externalID` to restore every reference at once.

## Files

| File | Purpose |
|---|---|
| `alkemio_cache_recover.py` | the app: GUI + CLI engine (stdlib only; `--selftest`) |
| `gen_hashes.py` | build `target_hashes.txt` from a DB CSV (hashes only) |
| `target_hashes.txt` | embedded lost-file hash list (generated) |
| `packaging/build.sh` / `build.ps1` | per-OS standalone GUI builds |
| `.github/workflows/build.yml` | CI matrix (win/mac/linux) |
