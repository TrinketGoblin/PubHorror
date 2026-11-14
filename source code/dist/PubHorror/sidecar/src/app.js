'use strict';
// Request router. Every route except the login page, the login call and /api/ping needs the access code.
// /control/* is the Python app's private channel: loopback only, plus a per-launch token.
const fs = require('fs');
const { send, json, redirect, page, readJsonBody, publicFile } = require('./http-util');
const { safeEqual } = require('./auth');
const { PROTOCOL } = require('./config');
const { serveFile } = require('./stream');

const path = require('path');
const { Theme } = require('./theme');
const STATIC = {
  '/static/app.js': ['app.js', 'text/javascript; charset=utf-8'],
  '/static/party-sync.js': ['party-sync.js', 'text/javascript; charset=utf-8'],
  '/static/party.js': ['party.js', 'text/javascript; charset=utf-8'],
};
// party-sync.js is ONE file shared with the desktop app (app/shared/). Python passes its path.
let partyClientPath = null;
const staticCache = new Map();
function staticFile(name) {
  if (!staticCache.has(name)) {
    const file = name === 'party-sync.js'
      ? (partyClientPath || path.join(__dirname, '..', '..', 'app', 'shared', 'party-sync.js'))
      : publicFile(name);
    staticCache.set(name, fs.readFileSync(file));
  }
  return staticCache.get(name);
}

// What a browser may see about playback: never the host's file paths.
// The media token goes in the URL because AirPlay / Chromecast make the TV fetch the video itself,
// and the TV doesn't have the phone's login cookie. It only unlocks video/poster files, never the API.
function clientStatus(s, id, ep, profile, mediaToken) {
  const out = { state: s.state, mode: s.mode, reasons: s.reasons || [], percent: s.percent || 0,
                error: s.error || null, position: s.position || 0, duration: s.duration || null,
                converted: !!s.converted };
  if (s.state === 'ready') {
    out.src = `/api/stream/${encodeURIComponent(id)}?ep=${ep}` + (profile === 'desktop' ? '&profile=desktop' : '')
            + (mediaToken ? `&t=${encodeURIComponent(mediaToken)}` : '');
  }
  return out;
}

function isLoopback(addr) {
  return addr === '127.0.0.1' || addr === '::ffff:127.0.0.1' || addr === '::1';
}

function clientIp(req) {
  return String(req.socket.remoteAddress || '').replace(/^::ffff:/, '');
}

function createApp(ctx) {
  // ctx: { cfg, auth, state: { port, bound, startedAt }, shutdown(reason) }
  const { cfg, auth } = ctx;
  if (cfg.party_client) partyClientPath = cfg.party_client;
  const theme = new Theme(cfg.app_config);
  ctx.theme = theme;
  // The desktop app's font, served to phones so the page looks like PubHorror. (SIL OFL, listed in the notices.)
  const assetsDir = cfg.assets_dir || path.join(path.dirname(partyClientPath || path.join(__dirname, '..', '..', 'app', 'shared', 'x')), '..', 'assets');
  const FONTS = new Set(['OpenDyslexic-Regular.otf', 'OpenDyslexic-Bold.otf']);
  const fontCache = new Map();

  // Looks only, no library data: allowed before sign-in so the login page is themed too.
  function publicStatic(req, res, url) {
    if (url.pathname === '/static/theme.css') {
      send(res, 200, theme.css(), 'text/css; charset=utf-8', { 'Cache-Control': 'no-cache' });
      return true;
    }
    if (url.pathname === '/static/app.css') {
      send(res, 200, fs.readFileSync(publicFile('app.css')), 'text/css; charset=utf-8', { 'Cache-Control': 'no-cache' });
      return true;
    }
    const fm = /^\/static\/fonts\/([A-Za-z-]+\.otf)$/.exec(url.pathname);
    if (fm && FONTS.has(fm[1])) {
      if (!fontCache.has(fm[1])) {
        try { fontCache.set(fm[1], fs.readFileSync(path.join(assetsDir, fm[1]))); } catch { fontCache.set(fm[1], null); }
      }
      const data = fontCache.get(fm[1]);
      if (!data) { json(res, 404, { error: 'font not found' }); return true; }
      send(res, 200, data, 'font/otf', { 'Cache-Control': 'public, max-age=604800' });
      return true;
    }
    return false;
  }

  function allowedHosts() {
    const port = ctx.state.port;
    const names = ['localhost', ...ctx.state.bound.map(b => b.address), ...cfg.extra_hostnames];
    const set = new Set();
    for (const n of names) { set.add(`${n}:${port}`.toLowerCase()); }
    return set;
  }

  async function handleControl(req, res, url) {
    if (!isLoopback(req.socket.localAddress) || !safeEqual(req.headers['x-control-token'] || '', cfg.control_token)) {
      return json(res, 403, { error: 'forbidden' });
    }
    const route = `${req.method} ${url.pathname}`;
    if (route === 'GET /control/status') {
      return json(res, 200, { ok: true, protocol: PROTOCOL, pid: process.pid, port: ctx.state.port,
                              bound: ctx.state.bound, uptime_s: Math.round((Date.now() - ctx.state.startedAt) / 1000) });
    }
    if (route === 'POST /control/set-code') {
      const body = await readJsonBody(req);
      auth.setCode(body.code);
      console.error('[sidecar] access code changed; existing sessions signed out');
      return json(res, 200, { ok: true });
    }
    if (route === 'POST /control/reload-library') {
      ctx.library.reload();
      return json(res, 200, { ok: true });
    }
    if (route === 'POST /control/shutdown') {
      json(res, 200, { ok: true });
      setImmediate(() => ctx.shutdown('requested by PubHorror'));
      return;
    }
    return json(res, 404, { error: 'unknown control route' });
  }

  ctx.allowedHosts = allowedHosts;
  return async function handler(req, res) {
    try {
      // Reject requests addressed to any other hostname (blocks DNS-rebinding attacks from web pages).
      const host = String(req.headers.host || '').toLowerCase();
      if (!allowedHosts().has(host)) return send(res, 421, 'Misdirected request');

      const url = new URL(req.url, 'http://x');
      if (url.pathname.startsWith('/control/')) return await handleControl(req, res, url);

      // State-changing requests from browsers must come from our own pages.
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        const origin = req.headers.origin;
        if (origin && origin.toLowerCase() !== `http://${host}`) return json(res, 403, { error: 'bad origin' });
      }

      const route = `${req.method} ${url.pathname}`;

      if (route === 'GET /api/ping') return json(res, 200, { app: 'PubHorror', protocol: PROTOCOL });
      if (req.method === 'GET' && url.pathname.startsWith('/static/') && publicStatic(req, res, url)) return;
      if (route === 'GET /login') return page(res, publicFile('login.html'));
      if (route === 'POST /api/login') {
        if (!String(req.headers['content-type'] || '').startsWith('application/json')) {
          return json(res, 415, { error: 'expected JSON' });
        }
        const body = await readJsonBody(req);
        const r = auth.tryCode(clientIp(req), body.code);
        if (r.ok) return json(res, 200, { ok: true }, { 'Set-Cookie': auth.issueCookie() });
        console.error(`[sidecar] wrong access code from ${clientIp(req)}`);
        return json(res, r.wait ? 429 : 401, {
          error: r.wait ? `Too many wrong codes. Try again in ${r.wait}s.` : 'Wrong access code.' });
      }
      if (route === 'GET /logout') return redirect(res, '/login', { 'Set-Cookie': auth.clearCookie() });

      // ---------- everything below needs the access code ----------
      // (or, for video/poster files only, the media token another PubHorror app puts in the URL)
      const seg = url.pathname.split('/');               // ['', 'api', '<what>', '<id>']
      const mediaRoute = seg.length === 4 && seg[1] === 'api' && (seg[2] === 'stream' || seg[2] === 'poster');
      const tokenAuthed = mediaRoute && auth.isMediaAuthed(req, url);
      if (!tokenAuthed && !auth.isAuthed(req)) {
        if (req.method === 'GET' && !url.pathname.startsWith('/api/')) return redirect(res, '/login');
        return json(res, 401, { error: 'access code required' });
      }
      // Files loaded by another PubHorror app's page (a different origin) need this to be allowed.
      const fileOpts = tokenAuthed ? { corp: 'cross-origin' } : {};

      if (route === 'GET /' || route === 'GET /index.html') return page(res, publicFile('index.html'));
      if (req.method === 'GET' && STATIC[url.pathname]) {
        const [file, type] = STATIC[url.pathname];
        return send(res, 200, staticFile(file), type, { 'Cache-Control': 'no-cache' });
      }
      if (route === 'GET /api/whoami') {
        return json(res, 200, { host_name: cfg.host_name, host_id: cfg.host_id, protocol: PROTOCOL,
                                media_token: auth.mediaToken(), party_token: ctx.parties.partyToken() });
      }

      // ---------- watch parties ----------
      if (route === 'GET /api/parties') return json(res, 200, { parties: ctx.parties.list() });
      if (route === 'POST /api/party') {
        if (!String(req.headers['content-type'] || '').startsWith('application/json')) return json(res, 415, { error: 'expected JSON' });
        const b = await readJsonBody(req);
        let duration = null;
        const f = ctx.library.mediaPath(b.movie_id, b.ep);
        if (f) { try { duration = (await ctx.media.status(f, 'desktop')).duration; } catch { /* unknown */ } }
        return json(res, 200, ctx.parties.create({ movie_id: String(b.movie_id || ''), ep: Number(b.ep) | 0,
          created_by: b.name, tmdb_id: b.tmdb_id, duration, start_at: b.start_at }));
      }
      const endMatch = /^\/api\/party\/([A-Za-z0-9]{6})\/end$/.exec(url.pathname);
      if (req.method === 'POST' && endMatch) {
        const b = await readJsonBody(req);
        const hostPc = isLoopback(req.socket.localAddress);       // the hosting PC's own app
        if (!ctx.parties.get(endMatch[1])) return json(res, 404, { error: 'That party has already ended.' });
        if (!hostPc && !ctx.parties.canEnd(endMatch[1], b.owner_key)) {
          return json(res, 403, { error: 'Only the person who started the party (or the host PC) can end it.' });
        }
        const ok = ctx.parties.end(endMatch[1], String(b.name || '').slice(0, 24) || null);
        return ok ? json(res, 200, { ok: true }) : json(res, 404, { error: 'That party has already ended.' });
      }
      if (req.method === 'GET' && url.pathname.startsWith('/api/party/')) {
        const r = ctx.parties.get(decodeURIComponent(url.pathname.slice('/api/party/'.length)));
        return r ? json(res, 200, r.info()) : json(res, 404, { error: 'That party has ended or the code is wrong.' });
      }

      // ---------- library (read-only) ----------
      const lib = ctx.library;
      if (route === 'GET /api/library') {
        return json(res, 200, { host_name: cfg.host_name, host_id: cfg.host_id, version: lib.version, items: lib.list() });
      }
      if (seg.length === 4 && seg[1] === 'api') {
        const id = decodeURIComponent(seg[3]);
        const ep = Number(url.searchParams.get('ep') || 0) | 0;
        const what = seg[2];
        // "desktop" = another PubHorror app (WebView2/Chromium), which plays more formats than phones.
        const profile = url.searchParams.get('profile') === 'desktop' ? 'desktop' : 'phone';

        if (what === 'movie' && req.method === 'GET') {
          const m = lib.get(id);
          return m ? json(res, 200, m) : json(res, 404, { error: 'not in library' });
        }
        if (what === 'poster' && req.method === 'GET') {
          const p = lib.posterPath(id);
          return p ? serveFile(req, res, p, { cache: 'private, max-age=86400', ...fileOpts })
                   : json(res, 404, { error: 'no poster' });
        }

        const file = (what === 'play' || what === 'prepare' || what === 'stream') ? lib.mediaPath(id, ep) : null;
        if (what === 'play' || what === 'prepare' || what === 'stream') {
          if (!file) return json(res, 404, { error: 'This title has no playable file on the host.' });
        }
        if (what === 'play' && req.method === 'GET') {
          return json(res, 200, clientStatus(await ctx.media.status(file, profile), id, ep, profile, auth.mediaToken()));
        }
        if (what === 'prepare' && req.method === 'POST') {
          return json(res, 200, clientStatus(await ctx.media.prepare(file, profile), id, ep, profile, auth.mediaToken()));
        }
        if (what === 'stream' && (req.method === 'GET' || req.method === 'HEAD')) {
          const st = await ctx.media.status(file, profile);
          if (st.state !== 'ready') return json(res, 409, { error: 'not ready', state: st.state });
          return serveFile(req, res, st.file, fileOpts);
        }
      }
      return json(res, 404, { error: 'not found' });
    } catch (e) {
      console.error('[sidecar] request error:', e && e.stack || e);
      if (!res.headersSent) json(res, e.status || 500, { error: e.status ? e.message : 'server error' });
      else res.destroy();
    }
  };
}

// WebSocket upgrades (/ws/party) get the same Host check, then cookie / app-header / party-token auth.
function createUpgrade(ctx, allowedHostsFn) {
  return function upgrade(req, socket, head) {
    socket.on('error', () => {});
    const host = String(req.headers.host || '').toLowerCase();
    const url = new URL(req.url, 'http://x');
    if (!allowedHostsFn().has(host) || url.pathname !== '/ws/party') {
      socket.write('HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return socket.destroy();
    }
    ctx.parties.handleUpgrade(req, socket, head, url, ctx.auth.isAuthed(req));
  };
}

module.exports = { createApp, createUpgrade };
