"""Other PubHorror copies ("remote hosts") as extra, read-only library locations.

Saved hosts live in data/share/remotes.json and TMDB matches for their titles in data/share/remote_tmdb.json.
Neither is the library: nothing from a remote host is ever written into movies.json.
This module talks to hosts over plain HTTP (the sidecar API) and never imports the sidecar.
"""
import json
import os
import subprocess
import threading
import time
import urllib.parse

import requests

from share_bridge import _write_json_atomic, find_lan_ip, NO_WINDOW, DEFAULT_PORT


class RemoteError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind          # "offline" | "code" | "missing" | "other"


def normalize_address(addr):
    a = str(addr or "").strip().rstrip("/")
    if not a:
        raise RemoteError("other", "Enter an address like 192.168.1.20 or 100.101.102.103.")
    if "://" not in a:
        a = "http://" + a
    u = urllib.parse.urlsplit(a)
    if u.scheme != "http" or not u.hostname:
        raise RemoteError("other", "That doesn't look like a PubHorror address.")
    port = u.port or DEFAULT_PORT
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"http://{host}:{port}"


class RemoteHosts:
    def __init__(self, share_dir, node_fn, sidecar_dir, own_id_fn):
        self.share_dir = share_dir
        self.file = os.path.join(share_dir, "remotes.json")
        self.tmdb_file = os.path.join(share_dir, "remote_tmdb.json")
        self._node_fn = node_fn
        self._sidecar_dir = sidecar_dir
        self._own_id_fn = own_id_fn
        self._lock = threading.RLock()
        self._tokens = {}                 # host id -> media token (memory only)
        self._party_tokens = {}           # host id -> party (websocket) token (memory only)
        self._s = requests.Session()
        self._s.trust_env = False         # never send LAN traffic through a system proxy

    # ------------------------------------------------------------ storage
    def _load(self):
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [h for h in data if isinstance(h, dict) and h.get("id") and h.get("url")]
        except (OSError, ValueError):
            return []

    def _save(self, hosts):
        _write_json_atomic(self.file, hosts)

    def _find(self, host_id):
        h = next((h for h in self._load() if h["id"] == host_id), None)
        if not h:
            raise RemoteError("missing", "That library isn't in your list any more.")
        return h

    def list(self):
        return [{"id": h["id"], "name": h.get("name") or "PubHorror", "url": h["url"]} for h in self._load()]

    # ------------------------------------------------------------ HTTP
    def _req(self, method, url, code, timeout=6, body=None):
        try:
            r = self._s.request(method, url, headers={"X-PubHorror-Code": code}, timeout=timeout,
                                json=body if method != "GET" else None)
        except requests.RequestException:
            raise RemoteError("offline", "Can't reach it. Is PubHorror open there with sharing turned on?")
        if r.status_code == 401:
            raise RemoteError("code", "The access code was rejected. It may have been changed on that PC.")
        if r.status_code == 429:
            raise RemoteError("code", "Too many wrong codes. Wait a minute and try again.")
        try:
            body = r.json()
        except ValueError:
            raise RemoteError("other", "That address answered, but it isn't PubHorror.")
        if r.status_code >= 400:
            raise RemoteError("other", body.get("error") or f"Error {r.status_code}")
        return body

    def _whoami(self, url, code):
        j = self._req("GET", url + "/api/whoami", code)
        if not j.get("host_id"):
            raise RemoteError("other", "That address answered, but it isn't PubHorror.")
        return j

    def _call(self, host_id, method, path, timeout=6, body=None):
        """Call a saved host; if it has moved to a new LAN address, find it again by id and retry once."""
        h = self._find(host_id)
        try:
            if host_id not in self._tokens:
                who = self._whoami(h["url"], h["code"])
                self._tokens[host_id] = who.get("media_token")
                self._party_tokens[host_id] = who.get("party_token")
            return h, self._req(method, h["url"] + path, h["code"], timeout, body)
        except RemoteError as e:
            if e.kind != "offline":
                raise
            moved = next((d for d in self.discover().get("hosts", []) if d["id"] == host_id and d["url"] != h["url"]), None)
            if not moved:
                raise
            with self._lock:
                hosts = self._load()
                for x in hosts:
                    if x["id"] == host_id:
                        x["url"] = moved["url"]
                self._save(hosts)
            h = self._find(host_id)
            who = self._whoami(h["url"], h["code"])
            self._tokens[host_id] = who.get("media_token")
            self._party_tokens[host_id] = who.get("party_token")
            return h, self._req(method, h["url"] + path, h["code"], timeout, body)

    def _abs(self, h, path):
        if not path:
            return None
        if path.startswith("https://"):
            return path
        if "&t=" in path or "?t=" in path:              # newer hosts already put the media token in
            return h["url"] + path
        sep = "&" if "?" in path else "?"
        return f"{h['url']}{path}{sep}t={urllib.parse.quote(self._tokens.get(h['id']) or '')}"

    # ------------------------------------------------------------ user actions
    def add(self, address, code):
        url = normalize_address(address)
        code = str(code or "").strip()
        if not code:
            raise RemoteError("code", "Enter the access code shown in that PC's PubHorror settings.")
        who = self._whoami(url, code)
        if who["host_id"] == self._own_id_fn():
            raise RemoteError("other", "That's this PC. Add another PubHorror instead.")
        with self._lock:
            hosts = [h for h in self._load() if h["id"] != who["host_id"]]
            hosts.append({"id": who["host_id"], "name": who.get("host_name") or "PubHorror", "url": url,
                          "code": code, "added": int(time.time())})
            self._save(hosts)
        self._tokens[who["host_id"]] = who.get("media_token")
        self._party_tokens[who["host_id"]] = who.get("party_token")
        return {"id": who["host_id"], "name": who.get("host_name"), "url": url}

    def remove(self, host_id):
        with self._lock:
            self._save([h for h in self._load() if h["id"] != host_id])
        self._tokens.pop(host_id, None)
        return self.list()

    def set_code(self, host_id, code):
        h = self._find(host_id)
        who = self._whoami(h["url"], str(code or "").strip())
        with self._lock:
            hosts = self._load()
            for x in hosts:
                if x["id"] == host_id:
                    x["code"] = str(code).strip()
                    x["name"] = who.get("host_name") or x.get("name")
            self._save(hosts)
        self._tokens[host_id] = who.get("media_token")
        self._party_tokens[host_id] = who.get("party_token")
        return {"ok": True}

    def discover(self, timeout_ms=3000):
        node = self._node_fn()
        script = os.path.join(self._sidecar_dir, "server.js")
        if not node or not os.path.isfile(script):
            return {"hosts": [], "error": "The sharing component is missing from this install."}
        args = [node, script, "--discover", "--timeout", str(timeout_ms)]
        lan = find_lan_ip()
        if lan:
            args += ["--interface", lan]
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=timeout_ms / 1000 + 6,
                                 cwd=self._sidecar_dir, creationflags=NO_WINDOW).stdout
            res = json.loads(out.strip().splitlines()[-1]) if out.strip() else {"hosts": []}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return {"hosts": [], "error": "Couldn't search the network."}
        own = self._own_id_fn()
        saved = {h["id"] for h in self._load()}
        hosts = []
        for d in res.get("hosts", []):
            if d.get("id") == own:
                continue
            d["saved"] = d.get("id") in saved
            hosts.append(d)
        return {"hosts": hosts, "error": res.get("error")}

    # ------------------------------------------------------------ browsing / playback
    def library(self, host_id):
        h, j = self._call(host_id, "GET", "/api/library")
        if j.get("host_name") and j["host_name"] != h.get("name"):
            with self._lock:
                hosts = self._load()
                for x in hosts:
                    if x["id"] == host_id:
                        x["name"] = j["host_name"]
                self._save(hosts)
        cache = self._tmdb_cache().get(host_id, {})
        for it in j.get("items", []):
            it["poster"] = self._abs(h, it.get("poster"))
            if not it.get("tmdb_id") and it["id"] in cache:
                it["tmdb_id"], it["tmdb_kind"] = cache[it["id"]].get("tmdb_id"), cache[it["id"]].get("tmdb_kind")
        return {"host_id": host_id, "host_name": j.get("host_name") or h.get("name"), "items": j.get("items", [])}

    def movie(self, host_id, movie_id):
        h, j = self._call(host_id, "GET", "/api/movie/" + urllib.parse.quote(movie_id, safe=""))
        j["poster"] = self._abs(h, j.get("poster"))
        return j

    def play(self, host_id, movie_id, ep=0, prepare=False):
        path = f"/api/{'prepare' if prepare else 'play'}/{urllib.parse.quote(movie_id, safe='')}?ep={int(ep)}&profile=desktop"
        h, j = self._call(host_id, "POST" if prepare else "GET", path, timeout=40)
        if j.get("src"):
            j["src"] = self._abs(h, j["src"])
        return j

    # ------------------------------------------------------------ watch parties on a remote host
    def parties(self, host_id):
        _, j = self._call(host_id, "GET", "/api/parties")
        return j.get("parties", [])

    def party_create(self, host_id, movie_id, ep, name, tmdb_id=None):
        _, j = self._call(host_id, "POST", "/api/party", timeout=40,
                          body={"movie_id": movie_id, "ep": int(ep), "name": name, "tmdb_id": tmdb_id})
        return j

    def party_get(self, host_id, room_id):
        _, j = self._call(host_id, "GET", "/api/party/" + urllib.parse.quote(str(room_id).strip().upper(), safe=""))
        return j

    def party_end(self, host_id, room_id, owner_key, name):
        _, j = self._call(host_id, "POST", f"/api/party/{urllib.parse.quote(str(room_id), safe='')}/end",
                          body={"owner_key": owner_key or "", "name": name})
        return j

    def party_ws(self, host_id):
        h = self._find(host_id)
        if host_id not in self._party_tokens:
            self._call(host_id, "GET", "/api/whoami")
        return h["url"].replace("http://", "ws://", 1) + "/ws/party?pt=" + urllib.parse.quote(self._party_tokens.get(host_id) or "")

    # ------------------------------------------------------------ TMDB ids for remote titles (client-side cache)
    def _tmdb_cache(self):
        try:
            with open(self.tmdb_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def remember_tmdb(self, host_id, movie_id, tmdb_id, kind):
        with self._lock:
            c = self._tmdb_cache()
            c.setdefault(host_id, {})[movie_id] = {"tmdb_id": tmdb_id, "tmdb_kind": kind}
            _write_json_atomic(self.tmdb_file, c)

    def cached_tmdb(self, host_id, movie_id):
        return self._tmdb_cache().get(host_id, {}).get(movie_id)
