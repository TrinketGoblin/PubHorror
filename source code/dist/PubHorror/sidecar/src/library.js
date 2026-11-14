'use strict';
// READ-ONLY view of the PubHorror library (data/movies.json).
//
// Safety rules (the user has lost a library before):
//   * This module never opens the library for writing; there is no write call anywhere in it.
//   * Reads use fs.readFile, which on Windows opens with full sharing (read/write/delete), so
//     the desktop app can always save/replace the file while we read it -- we never lock it.
//   * If a read fails or the JSON doesn't parse (e.g. caught mid-save), we keep the last good copy.
//   * Change detection is stat-polling (fs.watchFile) plus an explicit "reload" nudge from the app.
const fs = require('fs');
const path = require('path');

const MEDIA_EXT = new Set(['.mp4', '.m4v', '.mov', '.mkv', '.webm', '.avi', '.ts', '.m2ts', '.mpg', '.mpeg', '.wmv']);
const IMG_EXT = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']);

function fromFileUrl(p) {
  if (typeof p !== 'string' || !p) return null;
  if (/^file:/i.test(p)) {
    let rest = p.replace(/^file:\/\/\/?/i, '');
    try { rest = decodeURIComponent(rest); } catch { /* keep as-is */ }
    p = /^[A-Za-z]:/.test(rest) ? rest : '/' + rest.replace(/^\/+/, '');
  }
  if (/^https?:/i.test(p)) return null;
  return path.resolve(p);
}

class Library {
  constructor(file) {
    this.file = file;
    this.movies = [];          // raw records (internal only; never sent to clients as-is)
    this.byId = new Map();
    this.version = 0;          // bumps when content changes; clients can poll it
    this.loadedAt = 0;
    this.lastError = null;
    this._raw = null;
    // First load is synchronous so the library is there before the server says it's ready.
    if (file) {
      try { this._apply(fs.readFileSync(file, 'utf8')); } catch (e) { this.lastError = e.code || e.message; }
      fs.watchFile(file, { interval: 2000, persistent: false }, () => this.reload());
    }
  }

  reload() {
    if (!this.file) return;
    fs.readFile(this.file, 'utf8', (err, text) => {
      if (err) { this.lastError = err.code || err.message; return; }
      this._apply(text);
    });
  }

  _apply(text) {
    {
      if (text === this._raw) return;
      let data;
      try { data = JSON.parse(text); }
      catch { this.lastError = 'library file is mid-save or invalid; keeping last good copy'; return; }
      if (!Array.isArray(data)) { this.lastError = 'library file is not a list; keeping last good copy'; return; }
      this._raw = text;
      this.movies = data.filter(m => m && typeof m === 'object' && m.id && !m.remote);
      this.byId = new Map(this.movies.map(m => [String(m.id), m]));
      this.version += 1;
      this.loadedAt = Date.now();
      this.lastError = null;
      console.error(`[sidecar] library loaded: ${this.movies.length} titles (v${this.version})`);
    }
  }

  stop() { if (this.file) fs.unwatchFile(this.file); }

  // ---- media resolution: only files the library itself points at can ever be served ----
  playable(m) {
    if (m.source_type === 'series') return (m.episodes || []).some(e => this._mediaPath(e && e.path));
    return !!this.mediaPath(m.id);
  }

  _mediaPath(p) {
    const abs = fromFileUrl(p);
    return abs && MEDIA_EXT.has(path.extname(abs).toLowerCase()) && this._exists(abs) ? abs : null;
  }

  // Remembered for a minute so listing a big library doesn't stat every file on every refresh.
  _exists(abs) {
    if (!this._existCache) this._existCache = new Map();
    const c = this._existCache.get(abs);
    if (c && Date.now() - c.at < 60e3) return c.ok;
    let ok = false;
    try { ok = fs.statSync(abs).isFile(); } catch { ok = false; }
    this._existCache.set(abs, { ok, at: Date.now() });
    if (this._existCache.size > 20000) this._existCache.clear();
    return ok;
  }

  mediaPath(id, ep) {
    const m = this.byId.get(String(id));
    if (!m) return null;
    if (m.source_type === 'series') {
      const e = (m.episodes || [])[ep | 0];
      return e ? this._mediaPath(e.path) : null;
    }
    if (m.source_type === 'dvd') return this._mediaPath(m.ripped_file); // un-ripped discs can't be shared
    return this._mediaPath(m.file);
  }

  posterPath(id) {
    const m = this.byId.get(String(id));
    const abs = m && fromFileUrl(m.poster);
    return abs && IMG_EXT.has(path.extname(abs).toLowerCase()) ? abs : null;
  }

  // ---- what clients see: metadata only, never file paths, notes or private fields ----
  publicMovie(m, detail = false) {
    const poster = /^https:\/\//i.test(m.poster || '') ? m.poster
                 : (this.posterPath(m.id) ? `/api/poster/${encodeURIComponent(m.id)}?v=${this.version}` : null);
    const out = {
      id: String(m.id),
      title: String(m.title || 'Untitled'),
      year: Number(m.year) || null,
      type: m.source_type === 'series' ? 'series' : 'movie',
      poster,
      categories: Array.isArray(m.categories) ? m.categories.slice(0, 8).map(String) : [],
      playable: this.playable(m),
      tmdb_id: m.tmdb_id || null,
      tmdb_kind: m.tmdb_id ? (m.tmdb_kind || (m.source_type === 'series' ? 'tv' : 'movie')) : null,
    };
    if (detail) {
      out.desc = String(m.desc || '');
      out.director = String(m.director || '');
      out.cast = Array.isArray(m.cast) ? m.cast.slice(0, 10).map(String) : [];
      if (out.type === 'series') {
        out.episodes = (m.episodes || []).map((e, i) => ({
          index: i,
          title: String((e && e.title) || `Episode ${i + 1}`),
          season: e && e.season_num != null ? Number(e.season_num) : null,
          season_name: e && e.season_name ? String(e.season_name) : null,
          episode: e && e.ep_num != null ? Number(e.ep_num) : null,
          playable: !!this._mediaPath(e && e.path),
        }));
      }
    }
    return out;
  }

  list() {
    return this.movies.map(m => this.publicMovie(m))
      .sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base', numeric: true }));
  }

  get(id) {
    const m = this.byId.get(String(id));
    return m ? this.publicMovie(m, true) : null;
  }
}

module.exports = { Library, fromFileUrl, MEDIA_EXT };
