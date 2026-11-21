# Madium Crack — Local Setup & Usage

> For personal, local use only.

This folder contains everything needed to patch the locally installed Madium client so
that **any username/password signs in** and the account shows a far-future expiry
(~26,704 days). It also includes a watchdog that re-applies the patch automatically
after Madium updates itself.

---

## What's in this folder

| File | Purpose |
|---|---|
| `madium_crack.py` | Patcher + fake local auth server + update watchdog |
| `START.bat` | Re-run the install (patch, register startup, start watcher) |
| `UNINSTALL.bat` | Remove the startup entry and watcher |
| `watch.log` | Watchdog activity log |
| `Madium.exe.original.bak` | Spare copy of the original, unpatched binary |
| `Madium.lnk` | Shortcut to Madium |
| `analysis\` | Reverse-engineering scripts, disassembly dumps, screenshots, notes |

The **live** backup used by the patcher is kept next to the executable at:

```
C:\Users\temel\AppData\Local\Madium\Bin\Madium.exe.original.bak
```

The copy in this folder is just a spare.

---

## First-time setup — do this in order

### 1. Sign in once with a real account that still has time left

This is important. The client needs a genuine initial session before the patch is
applied. Launch the **unpatched** Madium, sign in with an account that has remaining
time, and let it reach the main editor screen.

### 2. Close Madium completely

Close the window, then make sure no `Madium.exe` process is left running (check Task
Manager). The patcher writes directly to `Madium.exe`, so the file must not be locked.

### 3. Run `START.bat`

Double-click `START.bat`. It will:

1. Patch `C:\Users\temel\AppData\Local\Madium\Bin\Madium.exe`
2. Print the applied patch names and a SHA-256 hash
3. Register a startup entry so the watcher runs at every logon
4. Start the watcher

> The first run can take **15–25 seconds** — the patcher scans the whole `.text`
> section to locate the exact code branches. This is normal. Wait for the
> `watcher started` line.

### 4. Launch Madium normally

Windows will show a UAC prompt — accept it, since the client requests administrator
rights. Sign in with **any** username and password (for example `test` / `test`).
You should land on the main screen with **26704 days left** shown in the top-right
account-time box.

---

## How it works

- **Auth URL redirect** — the hard-coded `https://auth.getmadium.xyz` is replaced in
  place with `http://127.0.0.1:8787/abcd` (same byte length).
- **Signature checks bypassed** — two conditional jumps that would reject an unsigned
  or forged verdict are neutralized.
- **Expiry forced** — the expiry register is set to `4102444800000` (year 2100 in
  milliseconds), and the `now()/cmp/jle` sequence that raises *"You have no time
  left. Top up on the site."* is removed. Older builds that use a 32-bit seconds
  value are still handled by the original patch.
- **Fake auth server** — listens on `127.0.0.1:8787`, replies with `LICENSE_VALID`,
  `issuedAt` in **milliseconds**, `expiresAt` = year 2100 in milliseconds, and the
  `{"error": false, "message": {...}}` wrapper the sign-in flow expects.
- **Watchdog** — hashes the binary every 5 seconds. If an update replaces it, the
  patch is re-applied automatically (allow ~20 seconds).

---

## Manual commands

Run these from a terminal in this folder:

```bat
python madium_crack.py install   :: patch + register startup + start watcher (same as START.bat)
python madium_crack.py patch     :: patch the binary once, no watcher
python madium_crack.py serve     :: run only the fake auth server
python madium_crack.py watch     :: run only the watchdog + server
```

---

## After an update

You don't need to do anything. The watchdog detects the new binary and re-patches it.
Wait about **30 seconds** after the update finishes, then launch Madium.

Only run `START.bat` again if the auto-patch fails or the watcher is no longer running.

---

## Troubleshooting

**"Madium sent an invalid response"**
The fake server isn't reachable. Check that a `pythonw.exe` process running
`madium_crack.py watch` exists, and that something is listening on port 8787:

```bat
netstat -ano | findstr :8787
```

Run `START.bat` to restart it.

**"Your system clock is too far off"**
An older version of the fake server was sending seconds instead of milliseconds.
Restart the watcher with `START.bat`.

**"You have no time left. Top up on the site."**
The installed build compares expiry against a millisecond clock. Older patchers
wrote `0x7fffffff` (a seconds value), which reads as 1970 in ms and always fails.
Re-run `START.bat` with the updated `madium_crack.py` — it writes a full 64-bit ms
timestamp and removes the expiry branch entirely.

**Sign-in screen appears again**
Wait ~30 seconds for the watcher to finish, then relaunch. If it persists, run
`START.bat` and try once more.

**Port 8787 already in use**
Another copy of the server is running. Stop the extra Python processes and start the
watcher again.

**Patch fails with "auth URL not found"**
Madium was updated to a version with a changed auth string — the pattern-based patcher
no longer matches. Check `watch.log` for details.

**Want the original back**
Run `UNINSTALL.bat`, then copy `Madium.exe.original.bak` over
`C:\Users\temel\AppData\Local\Madium\Bin\Madium.exe`.

---

## Quick reference

- Installed binary: `C:\Users\temel\AppData\Local\Madium\Bin\Madium.exe`
- Config/session files: `C:\Users\temel\AppData\Local\Madium\Config\`
- Fake server: `http://127.0.0.1:8787`
- Startup entry name: `MadiumCrack` (under `HKCU\...\CurrentVersion\Run`)
- Watchdog log: `watch.log` in this folder
