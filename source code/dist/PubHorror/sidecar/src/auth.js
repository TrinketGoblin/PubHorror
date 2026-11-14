'use strict';
// Access-code login. Sessions are stateless signed cookies keyed on the access code,
// so regenerating the code instantly logs everyone out, and a sidecar restart doesn't.
const crypto = require('crypto');

const COOKIE = 'ph_s';
const SESSION_DAYS = 30;

function normalizeCode(s) {
  return String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function safeEqual(a, b) {
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

function parseCookies(header) {
  const out = {};
  for (const part of String(header || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}

class Auth {
  constructor(code) {
    this.setCode(code);
    this.failures = new Map(); // ip -> { count, until }
  }

  setCode(code) {
    const c = normalizeCode(code);
    if (c.length < 6) throw new Error('access code too short');
    this.code = c;
  }

  _sign(payload) {
    return crypto.createHmac('sha256', 'pubhorror-session:' + this.code).update(payload).digest('base64url');
  }

  issueCookie() {
    const id = crypto.randomBytes(9).toString('base64url');
    const exp = Date.now() + SESSION_DAYS * 864e5;
    const value = `v1.${id}.${exp}.${this._sign(`${id}.${exp}`)}`;
    return `${COOKIE}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}`;
  }

  clearCookie() {
    return `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
  }

  _validSession(value) {
    const p = String(value || '').split('.');
    if (p.length !== 4 || p[0] !== 'v1') return false;
    const exp = Number(p[2]);
    if (!(exp > Date.now())) return false;
    return safeEqual(p[3], this._sign(`${p[1]}.${p[2]}`));
  }

  // A media-only token derived from the code, for <video>/<img> URLs inside another PubHorror app
  // (those can't send headers). It only opens /api/stream and /api/poster, and dies with the code.
  mediaToken() {
    return this._sign('media-v1').slice(0, 32);
  }

  isMediaAuthed(req, url) {
    const t = url.searchParams.get('t');
    return !!t && safeEqual(t, this.mediaToken());
  }

  // Browsers use the cookie; other PubHorror apps send the code in a header.
  isAuthed(req) {
    const header = req.headers['x-pubhorror-code'];
    if (header && safeEqual(normalizeCode(header), this.code)) return true;
    return this._validSession(parseCookies(req.headers.cookie)[COOKIE]);
  }

  // --- brute-force protection: 5 free tries, then a lockout that doubles each time (max 15 min) ---
  lockedFor(ip) {
    const f = this.failures.get(ip);
    return f && f.until > Date.now() ? Math.ceil((f.until - Date.now()) / 1000) : 0;
  }

  tryCode(ip, attempt) {
    const wait = this.lockedFor(ip);
    if (wait) return { ok: false, wait };
    if (safeEqual(normalizeCode(attempt), this.code)) {
      this.failures.delete(ip);
      return { ok: true };
    }
    const f = this.failures.get(ip) || { count: 0, until: 0 };
    f.count += 1;
    if (f.count >= 5) f.until = Date.now() + Math.min(15 * 60e3, 30e3 * 2 ** (f.count - 5));
    this.failures.set(ip, f);
    if (this.failures.size > 5000) this.failures.clear(); // memory guard
    return { ok: false, wait: this.lockedFor(ip) };
  }
}

module.exports = { Auth, normalizeCode, safeEqual };
