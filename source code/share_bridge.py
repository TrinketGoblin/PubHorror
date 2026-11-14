"""PubHorror <-> sharing sidecar (Node.js) bridge.

The desktop app never imports or shares code with the sidecar. The whole contract is:
  * data/share/sidecar.json   - config Python writes, Node reads (versioned by "protocol")
  * one JSON line on stdout   - {"ready": true, "port": ..., "bound": [...]} or {"ready": false, "error": ...}
  * http://127.0.0.1:<port>/control/*  - loopback-only control API, guarded by a per-launch token

The sidecar must never outlive the app. Three independent guards make sure of that:
  1. Windows Job Object with KILL_ON_JOB_CLOSE: if this process dies for ANY reason (crash, Task Manager),
     Windows closes the job handle and kills Node with it.
  2. Node polls our PID and exits when we're gone, and exits when its stdin pipe closes.
  3. stop() asks politely, then terminates, and is called on window close and at interpreter exit.
"""
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

PROTOCOL = 1
DEFAULT_PORT = 47420
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_TS_NET = ipaddress.ip_network("100.64.0.0/10")


def _write_json_atomic(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise


def new_access_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


# ---------------------------------------------------------------- addresses
def _is_lan(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local and a not in _TS_NET


def _is_tailscale(ip):
    try:
        return ipaddress.ip_address(ip) in _TS_NET
    except ValueError:
        return False


def _local_ipv4s():
    try:
        return {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return set()


def find_lan_ip():
    """The private address of the network adapter that has the default route (your Wi-Fi/Ethernet)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))          # TEST-NET address; no packet is actually sent
            ip = s.getsockname()[0]
        finally:
            s.close()
        if _is_lan(ip):
            return ip
    except OSError:
        pass
    # Default route goes elsewhere (e.g. a Tailscale exit node): fall back to the most home-network-like address.
    cands = sorted((ip for ip in _local_ipv4s() if _is_lan(ip)),
                   key=lambda ip: (not ip.startswith("192.168."), not ip.startswith("10."), ip))
    return cands[0] if cands else None


def _tailscale_exe():
    for p in (shutil.which("tailscale"),
              r"C:\Program Files\Tailscale\tailscale.exe",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if p and os.path.isfile(p):
            return p
    return None


def find_tailscale():
    """Returns (ip, magicdns_name) for this machine on Tailscale, or (None, None)."""
    exe = _tailscale_exe()
    ip = name = None
    if exe:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=4,
                                 creationflags=NO_WINDOW).stdout
            ip = next((l.strip() for l in out.splitlines() if _is_tailscale(l.strip())), None)
            if ip:
                st = json.loads(subprocess.run([exe, "status", "--json"], capture_output=True, text=True,
                                               timeout=4, creationflags=NO_WINDOW).stdout or "{}")
                name = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".") or None
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if not ip:
        ip = next((a for a in sorted(_local_ipv4s()) if _is_tailscale(a)), None)
    return ip, name


# ---------------------------------------------------------------- Windows job object
class _KillOnCloseJob:
    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._k32 = k32
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # ExtendedLimitInformation
            return
        self.handle = h                                         # deliberately never closed: closes when we exit

    def adopt(self, proc):
        if not self.handle:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))
        except Exception:
            return False


# ---------------------------------------------------------------- bridge
class ShareBridge:
    def __init__(self, data_dir, sidecar_dir, paths_fn):
        """paths_fn() -> dict of read-only paths for the sidecar (library_file, posters_dir, media_roots, ffmpeg...)."""
        self.share_dir = os.path.join(data_dir, "share")
        self.settings_file = os.path.join(self.share_dir, "share.json")
        self.config_file = os.path.join(self.share_dir, "sidecar.json")
        self.log_file = os.path.join(self.share_dir, "sidecar.log")
        self.sidecar_dir = sidecar_dir
        self._paths_fn = paths_fn
        self._lock = threading.RLock()
        self._proc = None
        self._job = None
        self._token = None
        self._ready = None
        self._error = None
        self._ts_name = None

    # ---- persistent settings (data/share/share.json; separate from config.json so backups can't clobber it)
    def _settings(self):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        changed = False
        if not s.get("access_code"):
            s["access_code"] = new_access_code(); changed = True
        if not s.get("host_id"):
            s["host_id"] = uuid.uuid4().hex; changed = True
        if not s.get("host_name"):
            s["host_name"] = (socket.gethostname() or "PubHorror")[:60]; changed = True
        if not isinstance(s.get("port"), int):
            s["port"] = DEFAULT_PORT; changed = True
        if changed:
            _write_json_atomic(self.settings_file, s)
        return s

    def _save_settings(self, s):
        _write_json_atomic(self.settings_file, s)

    # ---- finding node + the script
    def _node_exe(self):
        exe = "node.exe" if sys.platform == "win32" else "node"
        bundled = os.path.join(self.sidecar_dir, exe)
        if os.path.isfile(bundled):
            return bundled
        if not getattr(sys, "frozen", False):            # dev only: use Node from PATH
            return shutil.which("node")
        return None

    def available(self):
        return bool(self._node_exe()) and os.path.isfile(os.path.join(self.sidecar_dir, "server.js"))

    def running(self):
        return self._proc is not None and self._proc.poll() is None and bool(self._ready)

    # ---- start / stop
    def start(self):
        with self._lock:
            if self.running():
                return self.status()
            self._cleanup_dead()
            node = self._node_exe()
            if not node or not os.path.isfile(os.path.join(self.sidecar_dir, "server.js")):
                self._error = "The sharing component is missing from this install."
                return self.status()

            s = self._settings()
            lan = find_lan_ip()
            ts_ip, self._ts_name = find_tailscale()
            bind = [{"address": "127.0.0.1"}]
            if lan: bind.append({"address": lan})
            if ts_ip: bind.append({"address": ts_ip})

            self._token = secrets.token_urlsafe(24)
            cfg = {
                "protocol": PROTOCOL,
                "access_code": s["access_code"],
                "control_token": self._token,
                "host_name": s["host_name"],
                "host_id": s["host_id"],
                "port": s["port"],
                "bind": bind,
                "extra_hostnames": [self._ts_name] if self._ts_name else [],
                "parent_pid": os.getpid(),
            }
            try:
                cfg.update(self._paths_fn() or {})
            except Exception:
                pass
            _write_json_atomic(self.config_file, cfg)

            try:
                if os.path.exists(self.log_file) and os.path.getsize(self.log_file) > 1_000_000:
                    os.replace(self.log_file, self.log_file + ".old")
            except OSError:
                pass
            log = open(self.log_file, "a", encoding="utf-8")
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} starting sidecar ({node}) ===\n"); log.flush()

            self._ready, self._error = None, None
            if self._job is None:
                self._job = _KillOnCloseJob()
            try:
                self._proc = subprocess.Popen(
                    [node, os.path.join(self.sidecar_dir, "server.js"), "--config", self.config_file],
                    cwd=self.sidecar_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                    creationflags=NO_WINDOW)
            except OSError as e:
                log.close()
                self._error = f"Couldn't start the sharing component: {e}"
                return self.status()
            log.close()                                    # child has its own handle now
            self._job.adopt(self._proc)

            got = threading.Event()
            proc = self._proc

            def reader():
                for raw in proc.stdout:
                    try:
                        msg = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if "ready" in msg and not got.is_set():
                        if msg.get("ready"):
                            self._ready = msg
                        else:
                            self._error = msg.get("error") or "sidecar failed to start"
                        got.set()
                got.set()                                   # stdout closed: process exited
            threading.Thread(target=reader, daemon=True, name="sidecar-stdout").start()

            if not got.wait(15) or not self._ready:
                if not self._error:
                    self._error = "The sharing component didn't start (see data/share/sidecar.log)."
                self._kill()
            else:
                s["port"] = self._ready["port"]             # keep the same address (and QR) next time
                self._save_settings(s)
            return self.status()

    def stop(self):
        with self._lock:
            if self._proc is None:
                return self.status()
            if self._proc.poll() is None:
                try:
                    self._control("POST", "/control/shutdown", timeout=2)
                except Exception:
                    pass
                try:
                    self._proc.stdin.close()               # second signal: Node exits when stdin closes
                except Exception:
                    pass
                try:
                    self._proc.wait(3)
                except subprocess.TimeoutExpired:
                    pass
            self._kill()
            return self.status()

    def restart(self):
        with self._lock:
            self.stop()
            return self.start()

    def _kill(self):
        p, self._proc, self._ready = self._proc, None, None
        if p is None:
            return
        if p.poll() is None:
            try:
                p.kill()
                p.wait(3)
            except Exception:
                pass
        for stream in (p.stdin, p.stdout):
            try:
                if stream: stream.close()
            except Exception:
                pass

    def _cleanup_dead(self):
        if self._proc is not None and self._proc.poll() is not None:
            if self._ready and not self._error:
                self._error = "Sharing stopped unexpectedly (see data/share/sidecar.log)."
            self._kill()

    # ---- control channel
    def _control(self, method, path, body=None, timeout=3):
        if not self._ready:
            raise RuntimeError("sidecar not running")
        req = urllib.request.Request(f"http://127.0.0.1:{self._ready['port']}{path}", method=method,
                                     data=json.dumps(body or {}).encode() if method != "GET" else None,
                                     headers={"X-Control-Token": self._token, "Content-Type": "application/json",
                                              "Host": f"127.0.0.1:{self._ready['port']}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never route via a proxy
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def local_call(self, method, path, body=None, timeout=10):
        """Call our OWN sidecar's public API as an authenticated app client (used for hosting parties)."""
        if not self.running():
            raise RuntimeError("Sharing is off.")
        port = self._ready["port"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body or {}).encode() if method != "GET" else None,
            headers={"X-PubHorror-Code": self._settings()["access_code"], "Content-Type": "application/json",
                     "Host": f"127.0.0.1:{port}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error")
            except ValueError:
                msg = None
            raise RuntimeError(msg or f"Error {e.code}")

    def local_tokens(self):
        who = self.local_call("GET", "/api/whoami")
        return {"media": who.get("media_token"), "party": who.get("party_token"), "port": self._ready["port"]}

    def invite_links(self, room_id):
        """Links friends can open in a browser. They include the access code (after #, never sent over the network)."""
        st = self.status()
        code = st["access_code"]
        return [{"label": a["label"], "url": f"{a['url']}/login#code={code}&go=party/{room_id}"} for a in st["addresses"]]

    def notify_library_changed(self):
        """Fire-and-forget nudge so the sidecar re-reads the library. Safe to call when not running."""
        if not self.running():
            return
        def go():
            try: self._control("POST", "/control/reload-library", timeout=2)
            except Exception: pass
        threading.Thread(target=go, daemon=True).start()

    # ---- user actions
    def regenerate_code(self):
        with self._lock:
            s = self._settings()
            s["access_code"] = new_access_code()
            self._save_settings(s)
            if self.running():
                try:
                    self._control("POST", "/control/set-code", {"code": s["access_code"]})
                except Exception:
                    self.restart()
            return self.status()

    def set_host_name(self, name):
        with self._lock:
            name = " ".join(str(name or "").split())[:60]
            if not name:
                return self.status()
            s = self._settings()
            s["host_name"] = name
            self._save_settings(s)
            if self.running():
                self.restart()
            return self.status()

    # ---- phone-conversion cache (data/share/cache; the sidecar's only writable folder besides its log)
    def _cache_dir(self):
        return os.path.join(self.share_dir, "cache")

    def cache_info(self):
        total = count = 0
        try:
            for f in os.listdir(self._cache_dir()):
                if f.endswith(".mp4"):
                    total += os.path.getsize(os.path.join(self._cache_dir(), f)); count += 1
        except OSError:
            pass
        return {"bytes": total, "files": count}

    def clear_cache(self):
        """Deletes converted copies only (never originals). Files being converted right now are skipped."""
        removed = 0
        try:
            for f in os.listdir(self._cache_dir()):
                if f.endswith(".mp4"):
                    try:
                        os.remove(os.path.join(self._cache_dir(), f)); removed += 1
                    except OSError:
                        pass
        except OSError:
            pass
        return {"removed": removed, **self.cache_info()}

    def status(self):
        self._cleanup_dead()
        s = self._settings()
        st = {"available": self.available(), "running": self.running(), "error": self._error,
              "access_code": s["access_code"], "host_name": s["host_name"], "addresses": [], "warnings": [],
              "cache": self.cache_info()}
        if self.running():
            port = self._ready["port"]
            for b in self._ready.get("bound", []):
                if b["kind"] == "lan":
                    st["addresses"].append({"kind": "lan", "label": "Same Wi-Fi", "url": f"http://{b['address']}:{port}"})
                elif b["kind"] == "tailscale":
                    st["addresses"].append({"kind": "tailscale", "label": "Tailscale", "url": f"http://{b['address']}:{port}"})
                    if self._ts_name:
                        st["addresses"].append({"kind": "tailscale", "label": "Tailscale name",
                                                "url": f"http://{self._ts_name}:{port}"})
            for f in self._ready.get("failed", []):
                st["warnings"].append(f"Couldn't listen on {f['address']} ({f.get('error')}).")
            if not any(a["kind"] == "lan" for a in st["addresses"]):
                st["warnings"].append("No home network found - only this PC (and Tailscale, if connected) can reach it.")
            st["port"] = port
            primary = st["addresses"][0]["url"] if st["addresses"] else f"http://127.0.0.1:{port}"
            st["qr_url"] = f"{primary}/login#code={s['access_code']}"
            st["local_url"] = f"http://127.0.0.1:{port}"
        return st
