#!/usr/bin/env python3
"""
Madium licence crack - patcher + local auth server + update watchdog.

Usage:
    python madium_crack.py patch     patch the installed binary once
    python madium_crack.py serve     run only the fake auth server
    python madium_crack.py watch     keep patch + server alive (used by task)
    python madium_crack.py install   patch, install logon task, start watcher
"""
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import http.server
import socketserver

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
EXE = os.path.join(LOCAL, "Madium", "Bin", "Madium.exe")
BACKUP = EXE + ".original.bak"
PORT = 8787
LOCAL_URL = b"http://127.0.0.1:8787/abcd"
MS_EXPIRY = 4102444800000  # 2100-01-01 UTC in milliseconds - matches the fake server
NO_TIME = b"You have no time left. Top up on the site."

FAKE_SIG = "A" * 86
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ0eXAiOiJzZXNzaW9uIiwidWlkIjoiMTA3ODIiLCJ0diI6MSwiaWF0IjoxNzk1MTAxNDAzLCJleHAiOjQxMDI0NDQ4MDB9."
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)

# ------------------------------------------------------- PE helpers
def sections(data):
    pe = re.search(rb"PE\x00\x00", data)
    if not pe:
        raise RuntimeError("not a PE")
    off = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, off + 6)[0]
    opt_size = struct.unpack_from("<H", data, off + 20)[0]
    image_base = struct.unpack_from("<Q", data, off + 24 + 24)[0]
    sec = off + 24 + opt_size
    out = {}
    for i in range(nsec):
        p = sec + i * 40
        name = data[p:p+8].rstrip(b"\x00").decode("latin1")
        vsize = struct.unpack_from("<I", data, p + 8)[0]
        vaddr = struct.unpack_from("<I", data, p + 12)[0]
        rsize = struct.unpack_from("<I", data, p + 16)[0]
        raddr = struct.unpack_from("<I", data, p + 20)[0]
        out[name] = (vaddr, vsize, raddr, rsize)
    return image_base, out

def off2va(va_base, secs, off):
    for name, (v, vs, raw, rs) in secs.items():
        if raw <= off < raw + rs:
            return va_base + v + (off - raw)
    return None

def rip_target(ins):
    for op in ins.operands:
        if op.type == capstone.x86.X86_OP_MEM and op.mem.base == capstone.x86.X86_REG_RIP:
            return ins.address + ins.size + op.mem.disp
    return None

try:
    import capstone
except ImportError:
    capstone = None

# ---------------------------------------------------------------- patch core
def patch_bytes(data):
    """Return (patched_bytearray, list_of_applied_patch_names)."""
    if capstone is None:
        raise RuntimeError("capstone is required: pip install capstone")
    d = bytearray(data)
    applied = []

    # 1. auth endpoint -> loopback (length must stay 26)
    hit = None
    for m in re.finditer(rb"https?://[\x20-\x7e]{1,60}", bytes(d)):
        s = m.group()
        if b"getmadium" in s and len(s) == len(LOCAL_URL):
            hit = m
            break
    if hit:
        d[hit.start():hit.end()] = LOCAL_URL
        applied.append("auth-url")
    elif LOCAL_URL not in d:
        raise RuntimeError("auth URL not found - unexpected Madium version")

    va_base, secs = sections(bytes(d))
    text = secs.get(".text")
    if not text:
        raise RuntimeError(".text missing")
    tv, tvs, traw, trs = text
    code = bytes(d[traw:traw+tvs])
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    insns = list(md.disasm(code, va_base + tv))
    by_addr = {i.address: i for i in insns}

    # 2. make a signature-failure jump never fire.
    #    anchor: the error string loaded by the failure block
    def neutralise_jump_to_string(needle):
        i = bytes(d).find(needle)
        if i < 0:
            return False
        target = off2va(va_base, secs, i)
        if target is None:
            return False
        blocks = [ins.address for ins in insns
                  if ins.mnemonic == "lea" and rip_target(ins) == target]
        done = False
        for blk in blocks:
            for ins in insns:
                if blk - 0x600 <= ins.address < blk:
                    if ins.mnemonic.startswith("j") and ins.mnemonic != "jmp" and ins.operands:
                        op = ins.operands[0]
                        # the target is the start of the error block; the
                        # string load can be a few instructions inside it
                        if op.type == capstone.x86.X86_OP_IMM and blk - 32 <= op.imm <= blk:
                            o = traw + (ins.address - (va_base + tv))
                            d[o:o+ins.size] = b"\x90" * ins.size
                            done = True
        return done

    if neutralise_jump_to_string(b"forged_signature"):
        applied.append("sig-verify-1")
    if neutralise_jump_to_string(b"Madium's session signature did not verify"):
        applied.append("sig-verify-2")

    # 3. force expires_at to a far-future value (0x7FFFFFFF seconds)
    pat = bytes.fromhex("31eda801480f45ea")
    new = bytes.fromhex("bdffffff7f909090")
    hits = [m.start() for m in re.finditer(re.escape(pat), bytes(d))]
    if len(hits) == 1:
        o = hits[0]
        d[o:o+len(pat)] = new
        applied.append("expiry")
    elif len(hits) > 1:
        raise RuntimeError("ambiguous expiry pattern")
    # else: already patched (or a different version - leave it alone)

    # 4. Newer builds compare the expiry against a millisecond clock. The
    #    legacy 32-bit 0x7fffffff is 1970 in ms, so every launch drops into
    #    the "You have no time left" block. Widen it to a real 64-bit ms
    #    timestamp and cut out now()/cmp/jle in one shot.
    legacy = bytes.fromhex("bdffffff7f909090")
    for m in re.finditer(re.escape(legacy), bytes(d)):
        o = m.start()
        tail = bytes(d[o + 8:o + 22])
        if (len(tail) == 14
                and tail[5:8] == bytes.fromhex("4839c5")
                and tail[8] == 0x0F and tail[9] == 0x8E):
            d[o:o + 22] = (b"\x48\xbd" + struct.pack("<Q", MS_EXPIRY)
                           + b"\x90" * 12)
            applied.append("expiry-ms")
            break

    # 5. Version-proof fallback: if the byte pattern above shifted in a new
    #    release, cut any conditional jump aimed at the error block instead.
    if "expiry-ms" not in applied and NO_TIME in d:
        target = off2va(va_base, secs, bytes(d).find(NO_TIME))
        if target is not None:
            blocks = [ins.address for ins in insns
                      if ins.mnemonic == "lea" and rip_target(ins) == target]
            cut = 0
            for blk in blocks:
                for ins in insns:
                    if (ins.mnemonic.startswith("j") and ins.mnemonic != "jmp"
                            and ins.operands
                            and ins.operands[0].type == capstone.x86.X86_OP_IMM
                            and ins.operands[0].imm == blk):
                        o = traw + (ins.address - (va_base + tv))
                        d[o:o + ins.size] = b"\x90" * ins.size
                        cut += 1
            if cut:
                applied.append("expired-branch")

    return d, applied

def patch_file(path=EXE, verbose=True):
    if not os.path.exists(path):
        raise RuntimeError("missing " + path)
    original = open(path, "rb").read()
    patched, applied = patch_bytes(original)
    if patched == original:
        if verbose:
            print("already patched")
        return False
    if not os.path.exists(BACKUP):
        open(BACKUP, "wb").write(original)
    # close a running copy so the write succeeds
    subprocess.run(["taskkill", "/F", "/IM", "Madium.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    open(path, "wb").write(patched)
    if verbose:
        print("patched:", ", ".join(applied))
        print("sha256:", hashlib.sha256(patched).hexdigest())
    return True

# ---------------------------------------------------------------- fake server
def verdict(req):
    now = int(time.time() * 1000)
    hwid = req.get("hwid", "")
    nonce = req.get("nonce", "")
    return {
        "alg": "Ed25519", "status": "LICENSE_VALID", "token": TOKEN,
        "issuedAt": now, "expiresAt": 4102444800000,
        "hwid": hwid, "nonce": nonce, "reason": "", "signature": FAKE_SIG,
    }

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        self._reply()
    def _reply(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        try:
            req = json.loads(body or b"{}")
        except Exception:
            req = {}
        v = verdict(req)
        if "/account/auth" in self.path:
            out = json.dumps({"error": False, "message": v, **v}).encode()
        else:
            out = json.dumps(v).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a):
        pass

Handler.do_GET = Handler._reply

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def serve_forever():
    with Server(("127.0.0.1", PORT), Handler) as srv:
        srv.serve_forever()

# ---------------------------------------------------------------- watcher
def log(msg):
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch.log"),
                  "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass

def watch():
    log("watch started")
    try:
        patch_file(verbose=False)
    except Exception as e:
        log("initial patch error: " + str(e))
    t = threading.Thread(target=serve_forever, daemon=True)
    t.start()
    expected = hashlib.sha256(open(EXE, "rb").read()).hexdigest()
    log("watching " + expected[:16])
    while True:
        time.sleep(5)
        try:
            cur = hashlib.sha256(open(EXE, "rb").read()).hexdigest()
            if cur != expected:
                log("update detected - re-patching")
                patch_file(verbose=False)
                expected = hashlib.sha256(open(EXE, "rb").read()).hexdigest()
                log("now " + expected[:16])
        except Exception as e:
            log("watch error: " + str(e))

def install():
    patch_file()
    script = os.path.abspath(sys.argv[0])
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else sys.executable
    tr = f'"{runner}" "{script}" watch'
    subprocess.run(["schtasks", "/Create", "/TN", "Madium Crack Watch",
                    "/TR", tr, "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
                   capture_output=True, text=True)
    # HKCU Run works without elevation and survives logon
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Run",
                           0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "MadiumCrack", 0, winreg.REG_SZ,
                          f'"{runner}" "{script}" watch')
        winreg.CloseKey(k)
        print("startup entry installed")
    except Exception as e:
        print("startup entry failed:", e)
    subprocess.Popen([runner, script, "watch"],
                     creationflags=0x00000008, close_fds=True)
    print("watcher started")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "install"
    if mode == "patch":
        patch_file()
    elif mode == "serve":
        serve_forever()
    elif mode == "watch":
        watch()
    else:
        install()
