import webview
import json
import os
import sys
import uuid
import requests
import shutil
import subprocess
import ctypes
import string
import threading
import re
import csv
from urllib.parse import quote
from typing import Any, Optional, cast

# WebView2 configuration for local streaming and autoplay
if sys.platform == "win32":
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
        '--autoplay-policy=no-user-gesture-required '
        '--disable-features=AudioServiceOutOfProcess '
        '--allow-file-access-from-files '
        '--enable-local-file-accesses '
    )

if getattr(sys, 'frozen', False):
    RESOURCE_DIR = getattr(sys, '_MEIPASS', None) or ''
    APP_DIR = os.path.join(RESOURCE_DIR, "app")
    ROOT_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT_DIR = os.path.abspath(os.path.join(APP_DIR, ".."))

if sys.platform == "darwin":
    ROOT_DIR = os.path.join(os.path.expanduser("~/Library/Application Support"), "PubHorror")
    os.makedirs(ROOT_DIR, exist_ok=True)

DATA_DIR = os.path.join(ROOT_DIR, "data")
LIBRARY_DIR = os.path.join(ROOT_DIR, "library")
POSTERS_DIR = os.path.join(ROOT_DIR, "posters")

# Global media extensions including the AC3 fix
MEDIA_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ac3", ".m4a", ".wav"}

# MakeMKV CLI handles disc decryption (DVD CSS / Blu-ray AACS) for discs you own
MAKEMKV_CANDIDATES = [
    r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",
    r"C:\Program Files\MakeMKV\makemkvcon64.exe",
    r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe",
    r"C:\Program Files\MakeMKV\makemkvcon.exe",
    "/Applications/MakeMKV.app/Contents/MacOS/makemkvcon",
]
DVD_RIPS_DIR = os.path.join(LIBRARY_DIR, "dvd_rips")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Audio codecs WebView2 can play natively; anything else (AC3, DTS, TrueHD...) gets converted to AAC
WEB_AUDIO_OK = {"aac", "mp3", "opus", "vorbis", "flac"}
# Video codecs WebView2 can play; DVD MPEG-2 is NOT one of them, so it gets re-encoded to H.264
WEB_VIDEO_OK = {"h264", "vp8", "vp9", "av1"}

def ensure_data_files():
    for folder in [DATA_DIR, POSTERS_DIR, LIBRARY_DIR]:
        if not os.path.exists(folder):
            os.makedirs(folder)

    files = {
        os.path.join(DATA_DIR, "movies.json"): [],
        os.path.join(DATA_DIR, "collections.json"): [],
        os.path.join(DATA_DIR, "ratings.json"): {},
        os.path.join(DATA_DIR, "progress.json"): {},
        os.path.join(DATA_DIR, "config.json"): {
            "library_path": os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Users\\Public"), "Programs", "library"),
            "tmdb_api_key": "c4ba1e6999da5754f4c4163c92a1c4ad",
            "theme": "horror",
            "font_size": 16
        }
    }

    for file_path, default_content in files.items():
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_content, f, indent=2)

ensure_data_files()

MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")
COLLECTIONS_FILE = os.path.join(DATA_DIR, "collections.json")
RATINGS_FILE = os.path.join(DATA_DIR, "ratings.json")
PROGRESS_FILE = os.path.join(DATA_DIR, "progress.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
def _resolve_index():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "app", "index.html"),
                 os.path.join(APP_DIR, "index.html"),
                 os.path.join(here, "index.html"),
                 os.path.join(RESOURCE_DIR if getattr(sys, 'frozen', False) else here, "app", "index.html")):
        if os.path.exists(cand):
            return cand
    return os.path.join(here, "app", "index.html")

INDEX_HTML = _resolve_index()

# pywebview 5+/6 moved the dialog constants; support both
def _dialog_type(name):
    fd = getattr(webview, "FileDialog", None)
    if fd is not None and hasattr(fd, name):
        return getattr(fd, name)
    return getattr(webview, name + "_DIALOG")

class API:
    def __init__(self):
        self._window: Optional[webview.Window] = None
        self._rip = {}   # movie_id -> {state, percent, output, error}
        self._scan_lock = threading.Lock()
        self._last_scan = []

    # --- CONFIG ---
    def get_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"theme": "horror", "font_size": 16}

    def save_config(self, config):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_library_path(self):
        config = self.get_config()
        path = config.get("library_path", "library/movies")
        return path if os.path.isabs(path) else os.path.abspath(os.path.join(ROOT_DIR, path))

    # --- MOVIES & COLLECTIONS ---
    def get_movies(self):
        try:
            with open(MOVIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def save_movies(self, movies):
        try:
            with open(MOVIES_FILE, "w", encoding="utf-8") as f:
                json.dump(movies, f, indent=2, ensure_ascii=False)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_movie(self, movie):
        movies = self.get_movies()
        if "id" not in movie: movie["id"] = str(uuid.uuid4())
        defaults = {
            "categories": [], "source_type": "file", "disc_id": "",
            "watch_status": "unwatched", "notes": "", "rating": 0,
            "year": 0, "director": "", "cast": [], "desc": ""
        }
        for key, value in defaults.items():
            movie.setdefault(key, value)
        if not any(m.get("id") == movie.get("id") or (m.get("file") == movie.get("file") and m.get("file")) for m in movies):
            movies.append(movie)
        return self.save_movies(movies)

    def update_movie(self, update):
        movies = self.get_movies()
        movie_id, movie_file = update.get("id"), update.get("file")
        for i, m in enumerate(movies):
            if (movie_id and m.get("id") == movie_id) or (not movie_id and movie_file and m.get("file") == movie_file):
                movies[i] = {**m, **update}
                break
        return self.save_movies(movies)

    def delete_movie(self, movie_id):
        movies = [m for m in self.get_movies() if m.get("id") != movie_id]
        return self.save_movies(movies)

    def get_collections(self):
        try:
            with open(COLLECTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return []

    def save_collections(self, collections):
        try:
            with open(COLLECTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(collections, f, indent=2, ensure_ascii=False)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_collection(self, name):
        collections = self.get_collections()
        if name not in collections:
            collections.append(name)
            return self.save_collections(collections)
        return {"success": True}

    # NEW — full collection objects [{id, name, movie_ids[]}]
    def get_collections_v2(self):
        """Return list of collection objects with id, name, movie_ids."""
        try:
            raw = self.get_collections()
            # Migrate legacy string list → object list on first access
            if raw and isinstance(raw[0], str):
                migrated = [{"id": str(uuid.uuid4()), "name": n, "movie_ids": []} for n in raw]
                self.save_collections(migrated)
                return migrated
            return raw
        except:
            return []

    def save_collections_v2(self, collections):
        return self.save_collections(collections)

    def create_collection(self, name):
        cols = self.get_collections_v2()
        if any(c.get("name","").lower() == name.lower() for c in cols):
            return {"success": False, "error": "Collection already exists"}
        new_col = {"id": str(uuid.uuid4()), "name": name, "movie_ids": []}
        cols.append(new_col)
        self.save_collections_v2(cols)
        return {"success": True, "collection": new_col}

    def rename_collection(self, col_id, new_name):
        cols = self.get_collections_v2()
        for c in cols:
            if c.get("id") == col_id:
                c["name"] = new_name
                break
        self.save_collections_v2(cols)
        return {"success": True}

    def delete_collection(self, col_id):
        cols = [c for c in self.get_collections_v2() if c.get("id") != col_id]
        self.save_collections_v2(cols)
        return {"success": True}

    def set_movie_collections(self, movie_id, col_ids):
        """Assign a movie to a specific set of collection ids (replaces previous assignment)."""
        cols = self.get_collections_v2()
        for c in cols:
            if movie_id in c.get("movie_ids", []):
                c["movie_ids"].remove(movie_id)
            if c["id"] in col_ids:
                c["movie_ids"].append(movie_id)
        self.save_collections_v2(cols)
        return {"success": True}

    # --- PROGRESS & RATINGS ---
    def get_ratings(self):
        try:
            with open(RATINGS_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return {}

    def save_rating(self, movie_id, rating):
        ratings = self.get_ratings()
        ratings[movie_id] = rating
        try:
            with open(RATINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(ratings, f, indent=2)
            return {"success": True}
        except Exception as e: return {"success": False, "error": str(e)}

    def get_progress(self):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return {}

    def save_progress(self, movie_id, time, duration=None, completed=False):
        progress = self.get_progress()
        progress[movie_id] = time
        if duration is not None: progress[f"{movie_id}_dur"] = duration
        progress["last_played_id"] = movie_id
        if completed:
            base_id = movie_id.split(":")[0]
            key = f"{base_id}_complete_count"
            progress[key] = progress.get(key, 0) + 1
        try:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(progress, f, indent=2)
            return {"success": True}
        except Exception as e: return {"success": False, "error": str(e)}

    def get_favorites(self):
        progress = self.get_progress()
        return {k[:-15]: v for k, v in progress.items() if k.endswith("_complete_count")}

    def get_in_progress(self):
        progress = self.get_progress()
        movie_map = {m["id"]: m for m in self.get_movies()}
        result = []
        for key, val in progress.items():
            if any(key.endswith(s) for s in ["_dur", "_complete_count"]) or key == "last_played_id":
                continue
            duration = progress.get(f"{key}_dur", 0)
            ratio = val / duration if duration > 0 else 0
            if 0.02 < ratio < 0.95:
                base_id = key.split(":")[0]
                if base_id in movie_map: result.append({"id": key, "ratio": ratio})
        return result

    # --- TRANSCODING (AC3 AUDIO FIX) ---
    def transcode_to_aac(self, input_path):
        try:
            if not os.path.exists(input_path): return {"success": False, "error": "File not found"}
            output_path = f"{os.path.splitext(input_path)[0]}_fixed.mkv"
            command = [self._find_ffmpeg() or 'ffmpeg', '-i', input_path, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', output_path, '-y']
            subprocess.run(command, check=True, creationflags=NO_WINDOW)
            return {"success": True, "new_path": output_path.replace("\\", "/")}
        except Exception as e: return {"success": False, "error": str(e)}

    # --- FILE SYSTEM & PICKERS (FIXED PYLANCE WARNINGS) ---
    def pick_file(self, mode="video"):
        if self._window is None: return None
        ft = ("Image Files (*.jpg;*.jpeg;*.png;*.webp;*.gif)",) if mode == "image" else \
             ("Media Files (*.mp4;*.mkv;*.avi;*.mov;*.webm;*.ac3;*.m4a;*.wav)",)
        
        # Pylance fix: Explicitly use self._window now that we checked for None
        res = self._window.create_file_dialog(cast(int, _dialog_type("OPEN")), file_types=ft)
        return res[0].replace("\\", "/") if res else None

    def pick_folder(self):
        if self._window is None: return None
        res = self._window.create_file_dialog(cast(int, _dialog_type("FOLDER")))
        return res[0].replace("\\", "/") if res else None

    def import_folder(self):
        folder = self.pick_folder()
        if not folder: return []
        items = os.listdir(folder)
        v_files = [f for f in items if os.path.splitext(f)[1].lower() in MEDIA_EXT]
        is_series = any("season" in d.lower() for d in items if os.path.isdir(os.path.join(folder, d))) or (len(v_files) > 3)
        if is_series:
            return [{"id": str(uuid.uuid4()), "file": folder.replace("\\", "/"), "title": os.path.basename(folder), 
                     "source_type": "series", "episodes": self.list_episodes(folder)}]
        return [{"id": str(uuid.uuid4()), "file": os.path.join(folder, f).replace("\\", "/"), "title": os.path.splitext(f)[0]} for f in v_files]

    def list_episodes(self, path):
        import re as _re
        found = []
        series_name = os.path.basename(path).strip()

        for root, dirs, files in os.walk(path):
            dirs.sort()
            folder_name = os.path.basename(root)
            # Detect season number from folder name (e.g. "Season 1", "S01", "Series 2")
            season_num = None
            season_name = None
            sm = _re.search(r'(?:season|series|s)\s*0*(\d+)', folder_name, _re.IGNORECASE)
            if sm:
                season_num = int(sm.group(1))
                season_name = folder_name.strip()

            for file in sorted(files):
                if os.path.splitext(file)[1].lower() not in MEDIA_EXT:
                    continue
                stem = os.path.splitext(file)[0]
                ep_season = season_num
                ep_num = None

                # Parse SxxExx / s01e01 / 1x01 patterns from filename
                m = _re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', stem)
                if m:
                    ep_season = int(m.group(1))
                    ep_num    = int(m.group(2))
                    if not season_name:
                        season_name = f"Season {ep_season}"
                else:
                    m2 = _re.search(r'(\d{1,2})[Xx](\d{1,3})', stem)
                    if m2:
                        ep_season = int(m2.group(1))
                        ep_num    = int(m2.group(2))
                        if not season_name:
                            season_name = f"Season {ep_season}"

                # Clean title: strip series name prefix and SxxExx token
                title = stem
                # Remove leading series name (case-insensitive)
                if series_name and title.lower().startswith(series_name.lower()):
                    title = title[len(series_name):].lstrip(' .-_–—')
                # Remove SxxExx token from display title
                title = _re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}', '', title).strip(' .-_–—')
                title = _re.sub(r'\d{1,2}[Xx]\d{1,3}', '', title).strip(' .-_–—')
                # If title ends up blank, use a generated label
                if not title:
                    if ep_season is not None and ep_num is not None:
                        title = f"S{ep_season:02d}E{ep_num:02d}"
                    elif ep_num is not None:
                        title = f"Episode {ep_num}"
                    else:
                        title = stem

                entry = {
                    "title": title,
                    "path":  os.path.join(root, file).replace("\\", "/"),
                }
                if ep_season is not None:
                    entry["season_num"]  = ep_season
                    entry["season_name"] = season_name or f"Season {ep_season}"
                if ep_num is not None:
                    entry["ep_num"] = ep_num
                found.append(entry)

        # Sort: season → episode number → filename
        def _sort_key(e):
            return (e.get("season_num") or 0, e.get("ep_num") or 0, e["title"].lower())
        found.sort(key=_sort_key)
        return found

    def _scan_drives(self, include_all=False):
        """Scan optical (type 5) and removable (type 2, e.g. USB DVD drives) drives.
        include_all=False -> only drives holding a video disc.
        include_all=True  -> every candidate drive with a status, for the manual picker."""
        result = []
        if sys.platform != "win32":
            return result
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if not (mask & (1 << i)):
                continue
            root = f"{letter}:\\"
            dtype = k32.GetDriveTypeW(root)
            if dtype not in (2, 5):                   # 2 = removable, 5 = CD/DVD
                continue
            buf = ctypes.create_unicode_buffer(261)
            old_mode = k32.SetErrorMode(1)            # suppress "no disc" popups
            try:
                ok = k32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
            finally:
                k32.SetErrorMode(old_mode)
            if not ok:
                if include_all and dtype == 5:
                    result.append({"path": root, "letter": letter, "label": "",
                                   "kind": "empty"})
                continue                              # tray empty / not ready
            is_dvd = os.path.isdir(os.path.join(root, "VIDEO_TS"))
            is_bd = os.path.isdir(os.path.join(root, "BDMV"))
            kind = "bluray" if is_bd else ("dvd" if is_dvd else "data")
            if kind == "data" and not include_all:
                continue
            if kind == "data" and dtype == 2:
                continue                              # ignore ordinary USB sticks in the picker
            result.append({
                "path": root,
                "letter": letter,
                "label": buf.value or f"DISC_{letter}",
                "kind": kind,
            })
        return result

    def check_drives(self):
        """Drives that currently hold a video disc Windows can read (auto-detect poll).
        Never overlaps itself: if a scan is already running (slow drive), return the last result."""
        if not self._scan_lock.acquire(blocking=False):
            return self._last_scan
        try:
            self._last_scan = self._scan_drives(False)
            return self._last_scan
        finally:
            self._scan_lock.release()

    def _makemkv_drives(self):
        """Ask MakeMKV which drives hold a disc. Reads the disc raw, so it can see
        discs Windows reports as 'not ready'. Returns [{letter, label}]."""
        exe = self._find_makemkv()
        if not exe:
            return []
        try:
            out = subprocess.run([exe, "-r", "--cache=1", "info", "disc:9999"],
                                 capture_output=True, text=True, timeout=120,
                                 creationflags=NO_WINDOW).stdout
        except Exception:
            return []
        found = []
        for line in out.splitlines():
            if not line.startswith("DRV:"):
                continue
            try:
                f = next(csv.reader([line[4:]]))
            except Exception:
                continue
            # DRV:index,visible,enabled,flags,"drive name","disc name","device"
            if len(f) >= 7 and f[5].strip() and f[6].strip():
                found.append({"letter": f[6].strip().rstrip("\\/").rstrip(":").upper(),
                              "label": f[5].strip()})
        return found

    def list_drives(self):
        """Every optical drive with a status, for the manual drive picker and ripping."""
        with self._scan_lock:
            drives = self._scan_drives(True)
            if any(d["kind"] == "empty" for d in drives) or not drives:
                mk = {d["letter"]: d["label"] for d in self._makemkv_drives()}
                for d in drives:
                    if d["kind"] == "empty" and d["letter"] in mk:
                        d["kind"] = "disc"            # Windows can't read it, MakeMKV can
                        d["label"] = mk[d["letter"]]
                known = {d["letter"] for d in drives}
                for letter, label in mk.items():
                    if letter not in known and len(letter) == 1:
                        drives.append({"path": f"{letter}:\\", "letter": letter,
                                       "label": label, "kind": "disc"})
            return drives

    def check_file_exists(self, path):
        return os.path.exists(path) if path else False

    # --- DVD RIPPING (MakeMKV handles the disc decryption) ---
    def _find_makemkv(self):
        for p in MAKEMKV_CANDIDATES:
            if os.path.exists(p):
                return p
        return shutil.which("makemkvcon64") or shutil.which("makemkvcon")

    def has_makemkv(self):
        return bool(self._find_makemkv())

    def _makemkv_disc_index(self, exe, drive_root):
        """Map a drive letter like 'D:\\' to MakeMKV's disc:N index."""
        out = subprocess.run([exe, "-r", "--cache=1", "info", "disc:9999"],
                             capture_output=True, text=True, timeout=120,
                             creationflags=NO_WINDOW).stdout
        want = drive_root.rstrip("\\/").rstrip(":").upper()
        for line in out.splitlines():
            if not line.startswith("DRV:"):
                continue
            try:
                f = next(csv.reader([line[4:]]))
            except Exception:
                continue
            if len(f) >= 7 and f[6].strip().rstrip("\\/").rstrip(":").upper() == want:
                return int(f[0])
        return None

    def _main_title_id(self, exe, disc_idx):
        """Pick the longest title on the disc."""
        out = subprocess.run([exe, "-r", "--cache=1", "info", f"disc:{disc_idx}"],
                             capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout
        best_id, best_secs = None, -1
        for line in out.splitlines():
            m = re.match(r'TINFO:(\d+),9,0,"(\d+):(\d+):(\d+)"', line)   # attr 9 = duration
            if m:
                tid, h, mi, sec = map(int, m.groups())
                secs = h * 3600 + mi * 60 + sec
                if secs > best_secs:
                    best_id, best_secs = tid, secs
        return best_id

    def _rip_log(self, text):
        """Append a line to data/rip_log.txt so a stalled rip can be diagnosed afterwards."""
        try:
            import datetime
            with open(os.path.join(DATA_DIR, "rip_log.txt"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
        except Exception:
            pass

    def _which(self, name):
        found = shutil.which(name)
        if found: return found
        extra = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin"]
        if getattr(sys, 'frozen', False):
            extra = [getattr(sys, '_MEIPASS', ''), os.path.dirname(sys.executable),
                     os.path.join(os.path.dirname(sys.executable), "..", "Resources")] + extra
        for d in extra:
            p = os.path.join(d, name) if d else ""
            if p and os.path.isfile(p) and os.access(p, os.X_OK):
                return os.path.abspath(p)
        return None

    def _find_ffmpeg(self):
        for base in (ROOT_DIR, os.path.dirname(sys.executable)):
            for name in ("ffmpeg.exe", "ffmpeg"):
                p = os.path.join(base, name)
                if os.path.exists(p):
                    return p
        return self._which("ffmpeg")

    def has_ffmpeg(self):
        return bool(self._find_ffmpeg())

    def _probe(self, ffmpeg, path):
        """Return (audio_codec_names, duration_seconds) using ffprobe next to ffmpeg."""
        probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
        if not os.path.exists(probe):
            probe = self._which("ffprobe")
        if not probe:
            return None, 0
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, creationflags=NO_WINDOW).stdout
            codecs = [c.strip() for c in out.splitlines() if c.strip()]
            dur = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
            return codecs, float(dur or 0)
        except Exception:
            return None, 0

    def _rips_dir(self):
        """Where disc rips go. Set "dvd_rips_path" in data/config.json (e.g. "D:/dvd_rips") to move them off C:."""
        p = (self.get_config() or {}).get("dvd_rips_path")
        return os.path.abspath(p) if p else DVD_RIPS_DIR

    def _ffmpeg_failure_reason(self):
        try:
            with open(os.path.join(DATA_DIR, "ffmpeg_last.log"), encoding="utf-8", errors="replace") as f:
                tail = f.read()[-4000:]
            if "No space left on device" in tail:
                return "disk full"
        except Exception:
            pass
        return None

    def needs_audio_fix(self, path):
        """True if this file has video/audio the player can't decode and ffmpeg is available to fix it."""
        try:
            ff = self._find_ffmpeg()
            if not ff or not path or not os.path.exists(path):
                return False
            codecs, _ = self._probe(ff, path)
            v = self._vcodec(ff, path)
            return (v is not None and v not in WEB_VIDEO_OK) or (bool(codecs) and codecs[0] not in WEB_AUDIO_OK)
        except Exception:
            return False

    def start_audio_fix(self, movie_id, path):
        """Retry the audio conversion on an existing rip (no re-rip needed)."""
        if self._rip.get(movie_id, {}).get("state") in ("running", "converting"):
            return {"success": True}
        if not path or not os.path.exists(path):
            return {"success": False, "error": "Ripped file not found."}
        self._rip[movie_id] = {"state": "converting", "percent": 0, "output": None, "error": None}

        def worker():
            st = self._rip[movie_id]
            try:
                st["output"] = self._make_playable(path, st).replace("\\", "/")
                st["state"] = "done"
            except Exception as e:
                st["state"], st["error"] = "error", str(e)
        threading.Thread(target=worker, daemon=True).start()
        return {"success": True}

    def _vcodec(self, ffmpeg, path):
        probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
        if not os.path.exists(probe):
            probe = self._which("ffprobe")
        if not probe:
            return None
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                 "-of", "csv=p=0", path], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
            return out.strip().splitlines()[0].strip() if out.strip() else None
        except Exception:
            return None

    def _make_playable(self, ripped, st):
        """Make a raw MakeMKV rip playable in WebView2: DVD video is MPEG-2 and audio is AC3, neither of
        which the player can decode, so re-encode to H.264 + AAC in an MP4. Returns the path to play.
        Raises RuntimeError (leaving the raw rip untouched) if it can't be done."""
        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found. Put ffmpeg.exe next to PubHorror.exe (or install it), then press play again.")
        acodecs, duration = self._probe(ffmpeg, ripped)
        vcodec = self._vcodec(ffmpeg, ripped)
        if vcodec in WEB_VIDEO_OK and acodecs and acodecs[0] in WEB_AUDIO_OK:
            return ripped                                   # already playable
        out = os.path.splitext(ripped)[0] + "_play.mp4"
        # Space check BEFORE starting: output is roughly half the raw rip's size
        need = int(os.path.getsize(ripped) * 0.5) + 300_000_000
        free = shutil.disk_usage(os.path.dirname(ripped)).free
        drive = os.path.splitdrive(ripped)[0] or "the disk"
        if free < need:
            self._rip_log(f"convert skipped: need {need/1e9:.1f} GB, only {free/1e9:.1f} GB free on {drive}")
            raise RuntimeError(f"Not enough free space on {drive} to convert (need ~{need/1e9:.1f} GB, have {free/1e9:.1f} GB). "
                               f"Free up space (or set dvd_rips_path in config.json to another drive) and press play again. "
                               f"The disc will NOT need to be ripped again.")
        st["state"] = "converting"
        st["percent"] = 0
        self._rip_log(f"convert start: {ripped} video={vcodec} audio={acodecs} duration={duration}")
        vopts = ["-c:v", "copy"] if vcodec == "h264" else \
                ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]
        try:
            errlog = open(os.path.join(DATA_DIR, "ffmpeg_last.log"), "w", encoding="utf-8")
            proc = subprocess.Popen(
                [ffmpeg, "-y", "-nostdin", "-i", ripped, "-map", "0:v:0", "-map", "0:a?", "-sn"] + vopts +
                ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart",
                 "-progress", "pipe:1", "-nostats", out],
                stdout=subprocess.PIPE, stderr=errlog, text=True, creationflags=NO_WINDOW)
            for line in proc.stdout:
                m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
                if m and duration > 0:
                    st["percent"] = min(99, int(100 * (int(m.group(1)) / 1_000_000) / duration))
            proc.wait()
            errlog.close()
            rc = proc.returncode - (1 << 32) if proc.returncode >= (1 << 31) else proc.returncode
            self._rip_log(f"ffmpeg exit code {rc}")
            if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
                raise RuntimeError("ffmpeg failed")
        except Exception as e:
            self._rip_log(f"convert failed: {e} (see data/ffmpeg_last.log)")
            try:
                if os.path.exists(out): os.remove(out)      # never leave a partial file behind
            except Exception:
                pass
            if self._ffmpeg_failure_reason() == "disk full":
                raise RuntimeError(f"The disk ran out of space while converting. Free up space on {drive} and press play again. "
                                   f"The disc will NOT need to be ripped again.")
            raise RuntimeError("Conversion failed (see data/ffmpeg_last.log). Press play to retry; no re-rip needed.")
        try:
            os.remove(ripped)                               # keep only the playable copy
            for f in ("_ripped_ok",):
                p = os.path.join(os.path.dirname(ripped), f)
                if os.path.exists(p): os.remove(p)
        except Exception:
            pass
        self._rip_log(f"convert done: {out}")
        return out

    def find_raw_rip(self, label):
        """Path to an already-ripped but not-yet-converted MKV for this disc label, else None."""
        try:
            safe = "".join(c for c in (label or "") if c.isalnum() or c in " -_").strip() or "disc"
            d = os.path.join(self._rips_dir(), safe)
            if not os.path.exists(os.path.join(d, "_ripped_ok")):
                return None
            mk = sorted((f for f in os.listdir(d) if f.lower().endswith(".mkv")),
                        key=lambda f: os.path.getmtime(os.path.join(d, f)))
            return os.path.join(d, mk[-1]) if mk else None
        except Exception:
            return None

    def start_rip(self, movie_id, drive_root, label):
        exe = self._find_makemkv()
        if not exe:
            return {"success": False, "error": "MakeMKV not found. Install it from makemkv.com."}
        if self._rip.get(movie_id, {}).get("state") in ("running", "converting"):
            return {"success": True}
        safe = "".join(c for c in label if c.isalnum() or c in " -_").strip() or "disc"
        out_dir = os.path.join(self._rips_dir(), safe)
        os.makedirs(out_dir, exist_ok=True)
        self._rip[movie_id] = {"state": "running", "percent": 0, "output": None, "error": None}

        def worker():
            st = self._rip[movie_id]
            try:
                raw = self.find_raw_rip(label)
                if raw:                                     # ripped earlier, only conversion is missing
                    self._rip_log(f"re-using existing rip {raw}")
                    st["output"] = self._make_playable(raw, st).replace("\\", "/")
                    st["state"] = "done"
                    return
                idx = self._makemkv_disc_index(exe, drive_root)
                if idx is None:
                    raise RuntimeError("MakeMKV can't see that drive/disc.")
                tid = self._main_title_id(exe, idx)
                if tid is None:
                    raise RuntimeError("No titles found on disc.")
                proc = subprocess.Popen(
                    [exe, "-r", "--progress=-same", "mkv", f"disc:{idx}", str(tid), out_dir],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=NO_WINDOW)
                for line in proc.stdout:
                    m = re.match(r"PRGV:(\d+),(\d+),(\d+)", line)
                    if m:
                        cur, _tot, mx = map(int, m.groups())
                        if mx > 0:
                            st["percent"] = int(cur * 100 / mx)
                proc.wait()
                self._rip_log(f"makemkv exit code {proc.returncode} for {label}")
                mkvs = sorted((f for f in os.listdir(out_dir)
                               if f.lower().endswith(".mkv") and not f.lower().endswith("_fixed.mkv")),
                              key=lambda f: os.path.getmtime(os.path.join(out_dir, f)))
                if proc.returncode != 0 or not mkvs:
                    raise RuntimeError("MakeMKV failed (disc may be damaged or unsupported).")
                open(os.path.join(out_dir, "_ripped_ok"), "w").close()   # rip is complete; safe to reuse
                final = self._make_playable(os.path.join(out_dir, mkvs[-1]), st)
                st["output"] = final.replace("\\", "/")
                st["state"] = "done"
            except Exception as e:
                st["state"] = "error"
                st["error"] = str(e)

        threading.Thread(target=worker, daemon=True).start()
        return {"success": True}

    def get_rip_status(self, movie_id):
        return self._rip.get(movie_id, {"state": "idle"})

    # --- TMDB & POSTERS ---
    def search_tmdb(self, title, year, api_key, source_type="file"):
        """Return up to 10 TMDB matches so same-named remakes can be told apart.
        No year filter is sent to TMDB (that hides remakes); exact-year matches are sorted first instead."""
        try:
            is_tv = source_type == "series"
            kind = "tv" if is_tv else "movie"
            res = requests.get(f"https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={quote(title)}",
                               timeout=10).json().get("results", [])
            out = []
            for r in res[:20]:
                date = (r.get("first_air_date") if is_tv else r.get("release_date")) or ""
                out.append({
                    "id": r["id"],
                    "title": r.get("name") if is_tv else r.get("title"),
                    "year": int(date[:4]) if date[:4].isdigit() else 0,
                    "overview": (r.get("overview") or "")[:180],
                    "thumb": f"https://image.tmdb.org/t/p/w92{r['poster_path']}" if r.get("poster_path") else None,
                })
            try:
                y = int(year)
            except (TypeError, ValueError):
                y = 0
            if y:   # exact year first, then nearest year; stable sort keeps TMDB's relevance order inside ties
                out.sort(key=lambda c: (c["year"] != y, abs(c["year"] - y) if c["year"] else 999))
            return out[:10]
        except Exception:
            return []

    def fetch_tmdb_info(self, title, year, api_key, source_type="file", tmdb_id=None):
        try:
            is_tv = source_type == "series"
            kind = "tv" if is_tv else "movie"
            if not tmdb_id:
                cands = self.search_tmdb(title, year, api_key, source_type)
                if not cands: return None
                tmdb_id = cands[0]["id"]
            detail = requests.get(f"https://api.themoviedb.org/3/{kind}/{tmdb_id}?api_key={api_key}&append_to_response=credits", timeout=10).json()
            creds = detail.get("credits", {})
            return {
                "year": int((detail.get("first_air_date" if is_tv else "release_date") or "0")[:4]),
                "director": ", ".join(c["name"] for c in creds.get("crew", []) if c.get("job") in ["Director", "Creator"])[:100],
                "cast": [c["name"] for c in creds.get("cast", [])[:6]],
                "categories": [g["name"] for g in detail.get("genres", [])],
                "desc": detail.get("overview", ""),
                "poster_url": f"https://image.tmdb.org/t/p/w500{detail.get('poster_path')}" if detail.get('poster_path') else None
            }
        except: return None

    # --- WIKIDATA (scholarly metadata; no API key needed) ---
    _WD_API = "https://www.wikidata.org/w/api.php"
    _WD_HEADERS = {"User-Agent": "PubHorror/1.0 (personal film library desktop app)"}

    def _wd_get(self, params):
        params = {**params, "format": "json"}
        r = requests.get(self._WD_API, params=params, headers=self._WD_HEADERS, timeout=12)
        r.raise_for_status()
        return r.json()

    def _wd_labels(self, ids):
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids: return {}
        out = {}
        for i in range(0, len(ids), 40):
            chunk = ids[i:i + 40]
            ents = self._wd_get({"action": "wbgetentities", "ids": "|".join(chunk),
                                 "props": "labels", "languages": "en"}).get("entities", {})
            for qid, e in ents.items():
                out[qid] = (e.get("labels", {}).get("en", {}) or {}).get("value", "")
        return out

    @staticmethod
    def _wd_claim_ids(entity, prop):
        ids = []
        for c in entity.get("claims", {}).get(prop, []):
            v = (c.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
            if isinstance(v, dict) and v.get("id"): ids.append(v["id"])
        return ids

    @staticmethod
    def _wd_year(entity):
        for c in entity.get("claims", {}).get("P577", []):   # publication date
            v = (c.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
            if isinstance(v, dict):
                t = (v.get("time") or "").lstrip("+")
                if t[:4].isdigit(): return int(t[:4])
        return 0

    def search_wikidata(self, title, year=0):
        """Film candidates from Wikidata, exact-year matches first."""
        try:
            hits = self._wd_get({"action": "wbsearchentities", "search": title, "language": "en",
                                 "type": "item", "limit": 15}).get("search", [])
            ids = [h["id"] for h in hits]
            if not ids: return []
            ents = self._wd_get({"action": "wbgetentities", "ids": "|".join(ids),
                                 "props": "claims|labels|descriptions", "languages": "en"}).get("entities", {})
            cands = []
            for qid in ids:
                e = ents.get(qid) or {}
                desc = (e.get("descriptions", {}).get("en", {}) or {}).get("value", "")
                dirs = self._wd_claim_ids(e, "P57")
                # keep things that look like films: have a director, or are described as one
                if not dirs and "film" not in desc.lower(): continue
                cands.append({"id": qid,
                              "title": (e.get("labels", {}).get("en", {}) or {}).get("value", title),
                              "year": self._wd_year(e), "dir_ids": dirs, "description": desc})
            labels = self._wd_labels([d for c in cands for d in c["dir_ids"]])
            for c in cands:
                c["director"] = ", ".join(labels.get(d, "") for d in c.pop("dir_ids") if labels.get(d))[:100]
            try: y = int(year)
            except (TypeError, ValueError): y = 0
            if y:
                cands.sort(key=lambda c: (c["year"] != y, abs(c["year"] - y) if c["year"] else 999))
            return cands
        except Exception as e:
            return {"error": str(e)}

    def fetch_wikidata_info(self, qid):
        try:
            if not re.fullmatch(r"Q\d+", str(qid or "")): return {"error": "Bad Wikidata ID"}
            e = self._wd_get({"action": "wbgetentities", "ids": qid, "props": "claims",
                              "languages": "en"}).get("entities", {}).get(qid) or {}
            dirs = self._wd_claim_ids(e, "P57")
            studios = self._wd_claim_ids(e, "P272")      # production company
            countries = self._wd_claim_ids(e, "P495")    # country of origin
            labels = self._wd_labels(dirs + studios + countries)
            imdb = ""
            for c in e.get("claims", {}).get("P345", []):
                v = (c.get("mainsnak", {}).get("datavalue", {}) or {}).get("value")
                if isinstance(v, str) and v.startswith("tt"): imdb = v; break
            join = lambda ids, n: ", ".join(labels.get(i, "") for i in ids if labels.get(i))[:n]
            return {"wikidata_id": qid, "imdb_id": imdb, "year": self._wd_year(e),
                    "director": join(dirs, 100), "studio": join(studios, 120), "country": join(countries, 80)}
        except Exception as ex:
            return {"error": str(ex)}

    # --- CLIP EXPORT (excerpt + source record) ---
    def export_clip(self, req):
        try:
            if self._window is None: return {"success": False, "error": "Window not ready"}
            ffmpeg = self._find_ffmpeg()
            if not ffmpeg:
                return {"success": False, "error": "ffmpeg not found. Put ffmpeg.exe next to the app or add it to PATH."}
            src = req.get("source_path") or ""
            if not src or not os.path.exists(src):
                return {"success": False, "error": "Source file not found: " + src}
            start, end = float(req.get("start", 0)), float(req.get("end", 0))
            if start < 0 or end <= start:
                return {"success": False, "error": "Invalid clip range"}

            def mmss(t):
                t = int(t); return f"{t // 60:02d}m{t % 60:02d}s"
            title = req.get("title") or "clip"
            safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "clip"
            res = self._window.create_file_dialog(
                cast(int, _dialog_type("SAVE")),
                save_filename=f"{safe}_{mmss(start)}-{mmss(end)}.mp4")
            if not res: return {"success": False, "cancelled": True}
            out = res if isinstance(res, str) else res[0]
            if not out.lower().endswith(".mp4"): out += ".mp4"

            cmd = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
                   "-map", "0:v:0?", "-map", "0:a:0?",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
            proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
            if proc.returncode != 0 or not os.path.exists(out):
                tail = (proc.stderr or "").strip().splitlines()[-3:]
                return {"success": False, "error": "ffmpeg failed: " + " | ".join(tail)[:300]}

            import datetime
            record = os.path.splitext(out)[0] + "_source.txt"
            lines = [
                f"Excerpt from: {title}",
                f"Passage: {mmss(start)} to {mmss(end)} of the source file",
                f"Source file: {os.path.basename(src)}",
            ]
            if req.get("edition"): lines.append(f"Edition: {req['edition']}")
            if req.get("tag"): lines.append(f"Tag: {req['tag']}")
            if req.get("note"): lines.append(f"Note: {req['note']}")
            if req.get("citation"): lines.append(f"Citation: {req['citation']}")
            lines.append(f"Exported: {datetime.datetime.now():%Y-%m-%d %H:%M}")
            lines.append("Re-encoded excerpt made with PubHorror for criticism, comment, or scholarship.")
            with open(record, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return {"success": True, "path": out, "record": record}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- SUBTITLES ---
    _SUB_FILE_EXT = {".srt", ".vtt", ".ass", ".ssa"}
    _SUB_TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "microdvd", "subviewer", "jacosub"}
    _LANGS = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
              "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian", "nl": "Dutch", "sv": "Swedish",
              "da": "Danish", "no": "Norwegian", "fi": "Finnish", "pl": "Polish", "tr": "Turkish", "ar": "Arabic",
              "he": "Hebrew", "hi": "Hindi", "cs": "Czech", "el": "Greek", "hu": "Hungarian", "ro": "Romanian",
              "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "uk": "Ukrainian"}
    _LANG_ALIASES = {"eng": "en", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "ita": "it",
                     "por": "pt", "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh", "rus": "ru", "dut": "nl",
                     "nld": "nl", "swe": "sv", "dan": "da", "nor": "no", "fin": "fi", "pol": "pl", "tur": "tr",
                     "ara": "ar", "heb": "he", "hin": "hi", "cze": "cs", "ces": "cs", "gre": "el", "ell": "el",
                     "hun": "hu", "rum": "ro", "ron": "ro", "tha": "th", "vie": "vi", "ind": "id", "ukr": "uk"}

    def _lang_code(self, tok):
        t = (tok or "").strip().lower()
        t = self._LANG_ALIASES.get(t, t)
        if t in self._LANGS: return t
        for code, name in self._LANGS.items():
            if t == name.lower(): return code
        return ""

    @staticmethod
    def _decode_sub_bytes(b):
        if b[:2] in (b"\xff\xfe", b"\xfe\xff"): return b.decode("utf-16", errors="replace")
        for enc in ("utf-8-sig", "cp1252"):
            try: return b.decode(enc)
            except UnicodeDecodeError: continue
        return b.decode("latin-1", errors="replace")

    @staticmethod
    def _parse_cues(text):
        """SRT or WebVTT text -> [[start_sec, end_sec, text], ...]"""
        T = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
        cue_re = re.compile(rf"^\s*{T}\s*-->\s*{T}")
        def secs(g):
            h, mi, s, ms = g
            return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0
        text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
        cues = []
        for block in re.split(r"\n\s*\n", text):
            lines = block.split("\n")
            for i, line in enumerate(lines):
                mt = cue_re.match(line)
                if not mt: continue
                g = mt.groups()
                body = "\n".join(lines[i + 1:]).strip()
                body = re.sub(r"\{\\[^}]*\}", "", body)          # stray ASS override tags in SRT
                if body: cues.append([round(secs(g[:4]), 3), round(secs(g[4:]), 3), body])
                break
        return cues

    def _ffprobe_path(self, ffmpeg):
        probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
        if not os.path.exists(probe): probe = self._which("ffprobe")
        return probe

    def list_subtitles(self, video_path):
        """Sidecar files (Movie.en.srt, Subs/ folders) + text subtitle streams inside the video."""
        try:
            if not video_path or not os.path.exists(video_path):
                return {"success": True, "tracks": []}
            tracks = []
            folder = os.path.dirname(video_path)
            stem = os.path.splitext(os.path.basename(video_path))[0]
            for suffix in ("_play", "_fixed"):
                if stem.lower().endswith(suffix): stem = stem[:-len(suffix)]
            stem_l = stem.lower()
            seen = set()
            scan = [(folder, False)]
            for sub in ("Subs", "Subtitles", "Sub"):
                scan.append((os.path.join(folder, sub), False))
                scan.append((os.path.join(folder, sub, stem), True))     # Subs/<movie name>/2_English.srt
            for d, accept_all in scan:
                if not os.path.isdir(d): continue
                for f in sorted(os.listdir(d)):
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in self._SUB_FILE_EXT: continue
                    base = os.path.splitext(f)[0]
                    if not accept_all:
                        bl = base.lower()
                        if not (bl == stem_l or (bl.startswith(stem_l) and bl[len(stem_l)] in ".-_ ")):
                            continue
                        rest = base[len(stem):]
                    else:
                        rest = base
                    full = os.path.join(d, f)
                    if full in seen: continue
                    seen.add(full)
                    toks = [t for t in re.split(r"[.\-_ ]+", rest) if t]
                    lang, extras = "", []
                    for t in toks:
                        c = self._lang_code(t)
                        if c and not lang: lang = c
                        elif t.lower() in ("forced", "sdh", "cc", "hi", "commentary"): extras.append(t.upper() if len(t) <= 3 else t.lower())
                    label = self._LANGS.get(lang) or (" ".join(toks) if toks else "External")
                    if extras: label += " (" + ", ".join(extras) + ")"
                    tracks.append({"kind": "file", "path": full, "label": label + " · file", "lang": lang, "supported": True})

            ffmpeg = self._find_ffmpeg()
            probe = self._ffprobe_path(ffmpeg) if ffmpeg else None
            if probe:
                out = subprocess.run(
                    [probe, "-v", "error", "-select_streams", "s", "-show_entries",
                     "stream=index,codec_name:stream_tags=language,title", "-of", "json", video_path],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW).stdout
                try: streams = json.loads(out or "{}").get("streams", [])
                except ValueError: streams = []
                for n, s in enumerate(streams):
                    codec = (s.get("codec_name") or "").lower()
                    tags = s.get("tags") or {}
                    lang = self._lang_code(tags.get("language", ""))
                    name = self._LANGS.get(lang) or (tags.get("language") or "Track %d" % (n + 1))
                    title = (tags.get("title") or "").strip()
                    if title and title.lower() not in name.lower(): name += " — " + title
                    ok = codec in self._SUB_TEXT_CODECS
                    label = name + (" · embedded" if ok else " · image-based (not supported yet)")
                    tracks.append({"kind": "embedded", "n": n, "codec": codec, "label": label, "lang": lang, "supported": ok})
            return {"success": True, "tracks": tracks}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_subtitle_cues(self, video_path, track):
        try:
            kind = (track or {}).get("kind")
            ffmpeg = self._find_ffmpeg()
            if kind == "file":
                path = track.get("path", "")
                ext = os.path.splitext(path)[1].lower()
                if ext not in self._SUB_FILE_EXT or not os.path.isfile(path):
                    return {"success": False, "error": "Subtitle file not found"}
                if os.path.getsize(path) > 8_000_000:
                    return {"success": False, "error": "Subtitle file is unexpectedly large"}
                text = self._decode_sub_bytes(open(path, "rb").read())
                if ext in (".ass", ".ssa"):
                    if not ffmpeg: return {"success": False, "error": "ffmpeg is needed to read .ass/.ssa subtitles"}
                    tmp = os.path.join(DATA_DIR, "_sub_tmp" + ext)
                    with open(tmp, "w", encoding="utf-8") as f: f.write(text)
                    try:
                        p = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", tmp, "-f", "webvtt", "pipe:1"],
                                           capture_output=True, creationflags=NO_WINDOW, timeout=120)
                    finally:
                        try: os.remove(tmp)
                        except OSError: pass
                    text = p.stdout.decode("utf-8", errors="replace")
                cues = self._parse_cues(text)
            elif kind == "embedded":
                if not ffmpeg: return {"success": False, "error": "ffmpeg is needed to read embedded subtitles"}
                n = int(track.get("n", 0))
                st = os.stat(video_path)
                key = hashlib.sha1(f"{video_path}|{st.st_size}|{st.st_mtime_ns}|{n}".encode()).hexdigest()[:16]
                cache_dir = os.path.join(DATA_DIR, "subs_cache")
                os.makedirs(cache_dir, exist_ok=True)
                cache = os.path.join(cache_dir, key + ".vtt")
                if os.path.exists(cache):
                    text = open(cache, encoding="utf-8").read()
                else:
                    p = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", video_path, "-map", f"0:s:{n}",
                                        "-f", "webvtt", "pipe:1"], capture_output=True, creationflags=NO_WINDOW, timeout=900)
                    text = p.stdout.decode("utf-8", errors="replace")
                    if p.returncode != 0 or "-->" not in text:
                        return {"success": False, "error": "Couldn't read that subtitle track."}
                    with open(cache, "w", encoding="utf-8") as f: f.write(text)
                cues = self._parse_cues(text)
            else:
                return {"success": False, "error": "Unknown subtitle source"}
            if not cues: return {"success": False, "error": "No subtitle lines found in that track."}
            return {"success": True, "cues": cues}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Reading the subtitles took too long."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def pick_subtitle_file(self):
        if self._window is None: return {"success": False}
        res = self._window.create_file_dialog(cast(int, _dialog_type("OPEN")),
                                              file_types=("Subtitle files (*.srt;*.vtt;*.ass;*.ssa)",))
        if not res: return {"success": False, "cancelled": True}
        path = res[0] if not isinstance(res, str) else res
        base = os.path.basename(path)
        return {"success": True, "track": {"kind": "file", "path": path, "label": base, "lang": "", "supported": True}}

    def prepare_for_web(self, path):
        """macOS: Safari's engine plays H.264/HEVC + AAC in MP4/MOV only. Anything else (MKV, AVI, AC3 audio,
        MPEG-2...) is converted once into data/cache and reused. Remuxing (copying streams) is fast;
        re-encoding a whole film can take a while."""
        try:
            if not path or not os.path.exists(path):
                return {"success": False, "error": "File not found"}
            ffmpeg = self._find_ffmpeg()
            if not ffmpeg:
                return {"success": True, "path": path}   # can't check; try as-is
            acodecs, duration = self._probe(ffmpeg, path)
            vcodec = self._vcodec(ffmpeg, path)
            ext = os.path.splitext(path)[1].lower()
            mac_audio, mac_video = {"aac", "mp3", "alac"}, {"h264", "hevc"}
            if ext in (".mp4", ".m4v", ".mov") and vcodec in mac_video and acodecs and acodecs[0] in mac_audio:
                return {"success": True, "path": path}
            if vcodec is None and not acodecs:
                return {"success": True, "path": path}   # audio-only or unprobeable: leave alone
            st = os.stat(path)
            key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
            cache_dir = os.path.join(DATA_DIR, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            out = os.path.join(cache_dir, key + ".mp4")
            if os.path.exists(out) and os.path.getsize(out) > 0:
                return {"success": True, "path": out}
            need = int(st.st_size * 1.1) + 300_000_000
            free = shutil.disk_usage(cache_dir).free
            if free < need:
                return {"success": False, "error": f"Not enough free disk space to prepare this file (need ~{need/1e9:.1f} GB, have {free/1e9:.1f} GB)."}
            tmp = out + ".part.mp4"
            if vcodec == "h264":      vopts = ["-c:v", "copy"]
            elif vcodec == "hevc":    vopts = ["-c:v", "copy", "-tag:v", "hvc1"]
            else:                     vopts = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]
            aopts = ["-c:a", "copy"] if (acodecs and acodecs[0] == "aac") else ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
            with open(os.path.join(DATA_DIR, "ffmpeg_last.log"), "w", encoding="utf-8") as errlog:
                proc = subprocess.run(
                    [ffmpeg, "-y", "-nostdin", "-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-sn"] + vopts + aopts +
                    ["-movflags", "+faststart", tmp],
                    stdout=subprocess.DEVNULL, stderr=errlog, creationflags=NO_WINDOW)
            if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                try:
                    if os.path.exists(tmp): os.remove(tmp)
                except Exception: pass
                return {"success": False, "error": "Couldn't convert this file for playback (see data/ffmpeg_last.log)."}
            os.replace(tmp, out)
            return {"success": True, "path": out, "converted": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_poster(self, movie_id, file_path):
        try:
            ext = os.path.splitext(file_path)[1].lower() or ".jpg"
            dest = os.path.join(POSTERS_DIR, f"{movie_id}{ext}")
            shutil.copy2(file_path, dest)
            return {"success": True, "poster_path": "file:///" + dest.replace("\\", "/").lstrip("/")}
        except Exception as e: return {"success": False, "error": str(e)}

    def download_poster(self, movie_id, url):
        try:
            resp = requests.get(url, timeout=10)
            ext = ".png" if "png" in resp.headers.get("content-type", "") else ".jpg"
            dest = os.path.join(POSTERS_DIR, f"{movie_id}{ext}")
            with open(dest, "wb") as f: f.write(resp.content)
            return {"success": True, "poster_path": "file:///" + dest.replace("\\", "/").lstrip("/")}
        except Exception as e: return {"success": False, "error": str(e)}

    # --- AUTO BACKUP ---
    def trigger_auto_backup(self):
        """Gather current library state and save a timestamped backup. Called from JS on init."""
        backup = {
            "movies":      self.get_movies(),
            "ratings":     self.get_ratings(),
            "collections": self.get_collections_v2(),
            "config":      self.get_config(),
        }
        return self.auto_backup(backup)

    def auto_backup(self, backup):
        """Silent timestamped backup — no file dialog, saves to data/backups/."""
        try:
            backup_dir = os.path.join(DATA_DIR, "backups")
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
            # Keep only the 10 most recent auto-backups
            existing = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith("auto_") and f.endswith(".json")],
                reverse=True
            )
            for old in existing[9:]:
                try:
                    os.remove(os.path.join(backup_dir, old))
                except Exception:
                    pass
            import datetime
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(backup_dir, f"auto_{stamp}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(backup, f, indent=2, ensure_ascii=False)
            return {"success": True, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_auto_backups(self):
        """Return list of auto-backup metadata sorted newest first."""
        import datetime
        try:
            backup_dir = os.path.join(DATA_DIR, "backups")
            if not os.path.exists(backup_dir):
                return []
            files = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith("auto_") and f.endswith(".json")],
                reverse=True
            )
            result = []
            for fname in files:
                fpath = os.path.join(backup_dir, fname)
                try:
                    raw = fname.replace("auto_", "").replace(".json", "")
                    dt = datetime.datetime.strptime(raw, "%Y%m%d_%H%M%S")
                    label = dt.strftime("%b %d %Y, %H:%M")
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    count = len(data.get("movies", []))
                    result.append({"filename": fname, "path": fpath, "label": label, "movie_count": count})
                except Exception:
                    pass
            return result
        except Exception:
            return []

    def restore_auto_backup(self, backup_path):
        """Restore library state from an auto-backup file."""
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                b = json.load(f)
            if "movies" in b:
                self.save_movies(b["movies"])
            if "ratings" in b:
                with open(RATINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(b["ratings"], f, indent=2)
            if "collections" in b:
                self.save_collections(b["collections"])
            if "config" in b:
                self.save_config(b["config"])
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- BACKUP & UTILS ---
    def export_backup(self, backup):
        if self._window is None: return {"success": False}
        res = self._window.create_file_dialog(cast(int, _dialog_type("SAVE")), save_filename="pubhorror_backup.json")
        if res:
            path = res if isinstance(res, str) else res[0]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(backup, f, indent=2, ensure_ascii=False)
            return {"success": True}
        return {"success": False}

    def import_backup(self):
        if self._window is None: return {"success": False}
        res = self._window.create_file_dialog(cast(int, _dialog_type("OPEN")), file_types=("JSON Files (*.json)",))
        if res:
            with open(res[0], "r", encoding="utf-8") as f:
                b = json.load(f)
                if "movies" in b: self.save_movies(b["movies"])
                if "ratings" in b: 
                    with open(RATINGS_FILE, "w", encoding="utf-8") as f: json.dump(b["ratings"], f, indent=2)
                if "config" in b: self.save_config(b["config"])
            return {"success": True}
        return {"success": False}

    def export_notes(self, title, content, ext="txt"):
        if self._window is None: return {"success": False}
        safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        ext = ext if ext in ("txt", "md", "csv") else "txt"
        res = self._window.create_file_dialog(cast(int, _dialog_type("SAVE")), save_filename=f"{safe}_notes.{ext}")
        if res:
            path = res if isinstance(res, str) else res[0]
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True}
        return {"success": False}

    def toggle_fullscreen(self):
        if self._window: self._window.toggle_fullscreen()

# --- LOCAL MEDIA SERVER (macOS/Linux) ---
# WKWebView won't load file:// videos or images from a web page, so on non-Windows systems the app
# serves its own page and your media over http://127.0.0.1 (random port, session token, Range support).
import http.server, mimetypes, secrets, urllib.parse, hashlib

USE_LOCAL_SERVER = sys.platform != "win32"
_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

class _MediaHandler(http.server.BaseHTTPRequestHandler):
    server_version = "PubHorror"
    def log_message(self, *a): pass

    def _deny(self, code=403):
        self.send_response(code); self.send_header("Content-Length", "0"); self.end_headers()

    def _serve(self, head_only):
        host = (self.headers.get("Host") or "")
        if not host.startswith("127.0.0.1"):          # blunt DNS-rebinding tricks
            return self._deny(403)
        parsed = urllib.parse.urlparse(self.path)
        route = urllib.parse.unquote(parsed.path)
        token = self.server.token
        if route in ("/", "/index.html"):
            try:
                html = open(INDEX_HTML, encoding="utf-8").read()
            except OSError:
                return self._deny(404)
            inject = f'<script>window.__MEDIA_BASE="/m/{token}?p=";</script>'
            html = html.replace("<head>", "<head>" + inject, 1) if "<head>" in html else inject + html
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only: self.wfile.write(data)
            return
        if route == f"/m/{token}":
            q = urllib.parse.parse_qs(parsed.query)
            path = os.path.abspath((q.get("p") or [""])[0])
            ext = os.path.splitext(path)[1].lower()
            if ext not in MEDIA_EXT and ext not in _IMG_EXT: return self._deny(403)
        else:                                          # static assets next to index.html (fonts etc.)
            base = os.path.realpath(os.path.dirname(INDEX_HTML))
            path = os.path.realpath(os.path.join(base, route.lstrip("/")))
            if not (path == base or path.startswith(base + os.sep)): return self._deny(403)
        if not os.path.isfile(path): return self._deny(404)

        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].strip().partition("-")
            try:
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    if b: end = int(b)
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0"); self.end_headers()
                return
            status = 206
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only: return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk: break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self): self._serve(False)
    def do_HEAD(self): self._serve(True)

def start_media_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
    srv.daemon_threads = True
    srv.token = secrets.token_urlsafe(16)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"

# --- START APP ---
api = API()
if USE_LOCAL_SERVER:
    start_url = start_media_server()
else:
    start_url = f"file:///{INDEX_HTML.replace(chr(92), '/')}"
window = webview.create_window("PubHorror", url=start_url, js_api=api,
                               width=1400, height=900, background_color="#0a0a0a")
api._window = window
if sys.platform == "win32":
    webview.start(gui="edgechromium", debug=False)
else:
    webview.start(debug=False)