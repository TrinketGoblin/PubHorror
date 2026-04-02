import webview
import json
import os
import sys
import uuid
import requests
import shutil
import subprocess
from urllib.parse import quote
from typing import Any, Optional, cast

# WebView2 configuration for local streaming and autoplay
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

DATA_DIR = os.path.join(ROOT_DIR, "data")
LIBRARY_DIR = os.path.join(ROOT_DIR, "library")
POSTERS_DIR = os.path.join(ROOT_DIR, "posters")

# Global media extensions including the AC3 fix
MEDIA_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ac3", ".m4a", ".wav"}

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
INDEX_HTML = os.path.join(os.path.dirname(__file__), "app", "index.html")

class API:
    def __init__(self):
        self._window: Optional[webview.Window] = None

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
            command = ['ffmpeg', '-i', input_path, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', output_path, '-y']
            subprocess.run(command, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return {"success": True, "new_path": output_path.replace("\\", "/")}
        except Exception as e: return {"success": False, "error": str(e)}

    # --- FILE SYSTEM & PICKERS (FIXED PYLANCE WARNINGS) ---
    def pick_file(self, mode="video"):
        if self._window is None: return None
        ft = ("Image Files (*.jpg;*.jpeg;*.png;*.webp;*.gif)",) if mode == "image" else \
             ("Media Files (*.mp4;*.mkv;*.avi;*.mov;*.webm;*.ac3;*.m4a;*.wav)",)
        
        # Pylance fix: Explicitly use self._window now that we checked for None
        res = self._window.create_file_dialog(cast(int, webview.OPEN_DIALOG), file_types=ft)
        return res[0].replace("\\", "/") if res else None

    def pick_folder(self):
        if self._window is None: return None
        res = self._window.create_file_dialog(cast(int, webview.FOLDER_DIALOG))
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

    def check_drives(self):
        result = []
        if sys.platform == "win32":
            import ctypes, string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(drive) == 5:
                    try:
                        raw = os.popen(f"vol {letter}: 2>nul").read()
                        if "is" in raw:
                            label = raw.split("is")[-1].strip().split("\n")[0].strip()
                            result.append({"path": drive, "label": label})
                    except: pass
        return result

    def check_file_exists(self, path):
        return os.path.exists(path) if path else False

    # --- TMDB & POSTERS ---
    def fetch_tmdb_info(self, title, year, api_key, source_type="file"):
        try:
            is_tv = source_type == "series"
            kind = "tv" if is_tv else "movie"
            url = f"https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={quote(title)}"
            if year and not is_tv: url += f"&year={year}"
            res = requests.get(url, timeout=10).json().get("results", [])
            if not res: return None
            detail = requests.get(f"https://api.themoviedb.org/3/{kind}/{res[0]['id']}?api_key={api_key}&append_to_response=credits", timeout=10).json()
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

    def save_poster(self, movie_id, file_path):
        try:
            ext = os.path.splitext(file_path)[1].lower() or ".jpg"
            dest = os.path.join(POSTERS_DIR, f"{movie_id}{ext}")
            shutil.copy2(file_path, dest)
            return {"success": True, "poster_path": "file:///" + dest.replace("\\", "/")}
        except Exception as e: return {"success": False, "error": str(e)}

    def download_poster(self, movie_id, url):
        try:
            resp = requests.get(url, timeout=10)
            ext = ".png" if "png" in resp.headers.get("content-type", "") else ".jpg"
            dest = os.path.join(POSTERS_DIR, f"{movie_id}{ext}")
            with open(dest, "wb") as f: f.write(resp.content)
            return {"success": True, "poster_path": "file:///" + dest.replace("\\", "/")}
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
        res = self._window.create_file_dialog(cast(int, webview.SAVE_DIALOG), save_filename="pubhorror_backup.json")
        if res:
            path = res if isinstance(res, str) else res[0]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(backup, f, indent=2, ensure_ascii=False)
            return {"success": True}
        return {"success": False}

    def import_backup(self):
        if self._window is None: return {"success": False}
        res = self._window.create_file_dialog(cast(int, webview.OPEN_DIALOG), file_types=("JSON Files (*.json)",))
        if res:
            with open(res[0], "r", encoding="utf-8") as f:
                b = json.load(f)
                if "movies" in b: self.save_movies(b["movies"])
                if "ratings" in b: 
                    with open(RATINGS_FILE, "w", encoding="utf-8") as f: json.dump(b["ratings"], f, indent=2)
                if "config" in b: self.save_config(b["config"])
            return {"success": True}
        return {"success": False}

    def export_notes(self, title, content):
        if self._window is None: return {"success": False}
        safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        res = self._window.create_file_dialog(cast(int, webview.SAVE_DIALOG), save_filename=f"{safe}_notes.txt")
        if res:
            path = res if isinstance(res, str) else res[0]
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True}
        return {"success": False}

    def toggle_fullscreen(self):
        if self._window: self._window.toggle_fullscreen()

# --- START APP ---
api = API()
window = webview.create_window("PubHorror", url=f"file:///{INDEX_HTML.replace(chr(92), '/')}", js_api=api,
                               width=1400, height=900, background_color="#0a0a0a")
api._window = window
webview.start(gui="edgechromium", debug=False)