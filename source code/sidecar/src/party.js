'use strict';
// Watch Party rooms. The server holds the ONE official playback state per room:
//   { playing, position (seconds), at (server ms when position was true), seq }
// Everyone computes "where the room is now" from that and corrects themselves toward it.
//
// Rules:
//   * anyone in the room may play / pause / seek; it is broadcast with their name ("Fox paused")
//   * names are attached by the server, so nobody can act as someone else
//   * buffering: if a viewer stalls >1s while playing, the room holds ("Waiting for Fox…") and resumes
//     when they're ready; after 10s it carries on without them, and they don't trigger holds for 60s
//   * late joiners get the current state straight away
const crypto = require('crypto');
const { WebSocketServer } = require('ws');

const ROOM_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
const MAX_ROOMS = 20, MAX_MEMBERS = 30;
const HOLD_DEBOUNCE_MS = 1000, HOLD_MAX_MS = 10000, SLOW_GRACE_MS = 60000;
const EMOJI = ['👻', '😱', '💀', '🔪', '🩸', '😂', '🍿', '❤️'];

function now() { return Date.now(); }
function cleanName(s) {
  return String(s || '').replace(/[\u0000-\u001f\u007f<>]/g, '').replace(/\s+/g, ' ').trim().slice(0, 24) || 'Guest';
}
function fmt(t) {
  t = Math.max(0, Math.floor(t));
  const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}

class Room {
  constructor(id, movie, createdBy) {
    this.id = id;
    this.movie = movie;
    this.createdBy = createdBy;
    this.createdAt = now();
    this.state = { playing: false, position: 0, at: now(), seq: 0 };
    this.members = new Map();     // memberId -> member
    this.chat = [];
    this.hold = null;             // { waiting: Set<memberId>, since, resumeAt }
    this.emptySince = now();
    this.ownerKey = crypto.randomBytes(16).toString('hex');   // only the creator gets this back
  }

  pos(t = now()) {
    const s = this.state;
    return s.playing ? s.position + (t - s.at) / 1000 : s.position;
  }

  publicState() { return { ...this.state, held: !!this.hold }; }

  membersList() {
    return [...this.members.values()].map(m => ({ id: m.id, name: m.name, kind: m.kind, source: m.source,
      buffering: m.buffering, ready: m.ready, behind: m.behind }));
  }

  info() {
    return { id: this.id, movie: this.movie, created_by: this.createdBy, members: this.members.size,
             playing: this.state.playing, position: this.pos() };
  }

  send(m, msg) {
    if (m.ws.readyState === 1) m.ws.send(JSON.stringify(msg));
  }

  broadcast(msg, except) {
    const data = JSON.stringify(msg);
    for (const m of this.members.values()) if (m !== except && m.ws.readyState === 1) m.ws.send(data);
  }

  notice(text, except) { this.broadcast({ type: 'notice', text }, except); }

  setState(patch, by, action) {
    const p = this.pos();
    this.state = { playing: this.state.playing, position: p, ...patch, at: now(), seq: this.state.seq + 1 };
    this.broadcast({ type: 'state', state: this.publicState(), by: by ? by.name : null, action, server_now: now() });
  }

  // ---------------- buffering hold ----------------
  startHold(m) {
    if (!this.state.playing || (m.slowUntil && m.slowUntil > now())) return;
    if (!this.hold) {
      this.hold = { waiting: new Set(), since: now() };
      this.setState({ playing: false }, null, 'hold');
    }
    this.hold.waiting.add(m.id);
    this.broadcastHold();
    clearTimeout(this.holdTimer);
    this.holdTimer = setTimeout(() => this.giveUpHold(), HOLD_MAX_MS);
  }

  broadcastHold() {
    const names = [...(this.hold ? this.hold.waiting : [])].map(id => this.members.get(id)).filter(Boolean).map(x => x.name);
    this.broadcast({ type: 'hold', active: !!this.hold, waiting_for: names });
  }

  releaseFromHold(memberId) {
    if (!this.hold) return;
    this.hold.waiting.delete(memberId);
    if (this.hold.waiting.size === 0) this.endHold(true);
    else this.broadcastHold();
  }

  giveUpHold() {
    if (!this.hold) return;
    for (const id of this.hold.waiting) {
      const m = this.members.get(id);
      if (m) { m.slowUntil = now() + SLOW_GRACE_MS; this.notice(`Continuing without ${m.name} (slow connection). They'll catch up.`); }
    }
    this.endHold(true);
  }

  endHold(resume) {
    clearTimeout(this.holdTimer);
    this.hold = null;
    this.broadcastHold();
    if (resume) this.setState({ playing: true }, null, 'resume');
  }

  // ---------------- membership ----------------
  uniqueName(name) {
    const taken = new Set([...this.members.values()].map(m => m.name.toLowerCase()));
    if (!taken.has(name.toLowerCase())) return name;
    for (let i = 2; ; i++) { const n = `${name} (${i})`; if (!taken.has(n.toLowerCase())) return n; }
  }

  remove(m) {
    if (!this.members.delete(m.id)) return;
    if (this.hold) this.releaseFromHold(m.id);
    this.notice(`${m.name} left`);
    this.broadcast({ type: 'members', members: this.membersList() });
    if (this.members.size === 0) {
      this.emptySince = now();
      if (this.state.playing) this.state = { ...this.state, playing: false, position: this.pos(), at: now() };
    }
  }
}

class Parties {
  constructor({ library, auth, cfg }) {
    this.library = library;
    this.auth = auth;
    this.cfg = cfg;
    this.rooms = new Map();
    this.wss = new WebSocketServer({ noServer: true, maxPayload: 4096, perMessageDeflate: false });
    this.timer = setInterval(() => this.tick(), 5000);
  }

  // A party token lets another PubHorror app (whose page is a different origin) open the WebSocket.
  partyToken() { return this.auth._sign('party-v1').slice(0, 32); }

  newId() {
    for (;;) {
      const id = Array.from(crypto.randomBytes(6), b => ROOM_ALPHABET[b % ROOM_ALPHABET.length]).join('');
      if (!this.rooms.has(id)) return id;
    }
  }

  create({ movie_id, ep, created_by, tmdb_id, duration, start_at }) {
    if (this.rooms.size >= MAX_ROOMS) throw Object.assign(new Error('Too many parties are running on this PC.'), { status: 429 });
    const m = this.library.get(movie_id);
    if (!m) throw Object.assign(new Error('That title is not in the host library.'), { status: 404 });
    const e = m.type === 'series' ? (m.episodes || [])[ep | 0] : null;
    if (m.type === 'series' && !e) throw Object.assign(new Error('No such episode.'), { status: 404 });
    if (!this.library.mediaPath(movie_id, ep)) throw Object.assign(new Error('The host has no playable file for this title.'), { status: 409 });
    const movie = {
      id: m.id, ep: ep | 0, title: m.title, year: m.year, type: m.type, poster: m.poster,
      // the host library's id wins; a hint is only used if the host hasn't saved one yet
      tmdb_id: m.tmdb_id || (Number(tmdb_id) || null), tmdb_kind: m.tmdb_kind || (m.type === 'series' ? 'tv' : 'movie'),
      episode: e ? { index: e.index, title: e.title, season: e.season, episode: e.episode } : null,
      duration: Number(duration) || null,
    };
    const room = new Room(this.newId(), movie, cleanName(created_by));
    // "Make this a party" from a player: start where the creator already was.
    const at = Number(start_at);
    if (Number.isFinite(at) && at > 0) room.state.position = movie.duration ? Math.min(at, movie.duration) : at;
    this.rooms.set(room.id, room);
    console.error(`[sidecar] party ${room.id} started for "${m.title}" by ${room.createdBy}`);
    return { ...room.info(), owner_key: room.ownerKey };
  }

  // Ends a party for everyone. Allowed for whoever started it (owner key) and for the hosting PC itself.
  end(id, by) {
    const room = this.get(id);
    if (!room) return false;
    clearTimeout(room.holdTimer);
    room.broadcast({ type: 'closed', ended: true, text: `${by || 'The host'} ended the party.` });
    for (const m of room.members.values()) { clearTimeout(m.bufTimer); try { m.ws.close(); } catch { /* ignore */ } }
    room.members.clear();
    this.rooms.delete(room.id);
    console.error(`[sidecar] party ${room.id} ended by ${by || 'the host'}`);
    return true;
  }

  canEnd(id, key) {
    const room = this.get(id);
    return !!room && !!key && require('./auth').safeEqual(String(key), room.ownerKey);
  }

  list() { return [...this.rooms.values()].map(r => r.info()); }
  get(id) { return this.rooms.get(String(id || '').toUpperCase()); }

  tick() {
    for (const r of this.rooms.values()) {
      if (r.members.size === 0 && now() - r.emptySince > 10 * 60e3) {     // empty for 10 min: close it
        this.rooms.delete(r.id);
        console.error(`[sidecar] party ${r.id} closed (empty)`);
        continue;
      }
      r.broadcast({ type: 'heartbeat', state: r.publicState(), server_now: now() });
    }
  }

  // ---------------- websocket ----------------
  // authed: true if the request carried a valid cookie (same-origin browser) or app header.
  handleUpgrade(req, socket, head, url, authed) {
    const origin = req.headers.origin;
    const host = String(req.headers.host || '').toLowerCase();
    const tokenOk = !!url.searchParams.get('pt') && require('./auth').safeEqual(url.searchParams.get('pt'), this.partyToken());
    // Cookie auth only counts from our own pages (blocks other websites from riding the cookie).
    const cookieOk = authed && (!origin || origin.toLowerCase() === `http://${host}`);
    if (!tokenOk && !cookieOk) {
      socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return socket.destroy();
    }
    // The hosting PC's own app connects over loopback; nobody else can reach 127.0.0.1.
    const isHostPc = /^(::ffff:)?127\.0\.0\.1$|^::1$/.test(String(req.socket.localAddress || ''));
    this.wss.handleUpgrade(req, socket, head, ws => this.onConnection(ws, isHostPc));
  }

  onConnection(ws, isHostPc) {
    let room = null, me = null;
    const bucket = { n: 0, reset: now() };
    const limited = () => {                        // 20 messages / 2 s per connection
      if (now() - bucket.reset > 2000) { bucket.n = 0; bucket.reset = now(); }
      return ++bucket.n > 20;
    };
    const send = msg => { if (ws.readyState === 1) ws.send(JSON.stringify(msg)); };
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });
    const ka = setInterval(() => {                 // drop dead connections (phone locked, Wi-Fi lost)
      if (!ws.isAlive) return ws.terminate();
      ws.isAlive = false;
      try { ws.ping(); } catch { /* closed */ }
    }, 15000);

    ws.on('message', raw => {
      let msg;
      try { msg = JSON.parse(raw.toString('utf8')); } catch { return; }
      if (!msg || typeof msg.type !== 'string') return;
      if (msg.type === 'ping') return send({ type: 'pong', t: msg.t, server_now: now() });   // clock sync: never limited
      if (limited()) return;

      if (msg.type === 'hello') {
        if (room) return;
        room = this.get(msg.room);
        if (!room) { send({ type: 'error', code: 'no_room', text: 'That party has ended or the code is wrong.' }); return ws.close(); }
        if (room.members.size >= MAX_MEMBERS) { send({ type: 'error', code: 'full', text: 'This party is full.' }); return ws.close(); }
        me = { id: crypto.randomBytes(6).toString('hex'), ws, name: room.uniqueName(cleanName(msg.name)),
               kind: msg.kind === 'app' ? 'app' : 'browser', source: msg.source === 'local' ? 'local' : 'stream',
               buffering: false, ready: false, behind: 0, slowUntil: 0,
               canEnd: !!isHostPc || this.canEnd(room.id, msg.key) };
        room.members.set(me.id, me);
        send({ type: 'welcome', you: { id: me.id, name: me.name, can_end: me.canEnd }, room: room.info(), state: room.publicState(),
               members: room.membersList(), chat: room.chat.slice(-30), emoji: EMOJI, server_now: now(),
               hold: room.hold ? { active: true } : { active: false } });
        room.notice(`${me.name} joined${me.source === 'local' ? ' (playing their own copy)' : ''}`, me);
        room.broadcast({ type: 'members', members: room.membersList() });
        return;
      }
      if (!room || !me) return;

      switch (msg.type) {
        case 'play': {
          if (room.hold) room.endHold(false);
          const p = Number.isFinite(msg.position) ? Math.max(0, msg.position) : room.pos();
          room.setState({ playing: true, position: p }, me, 'play');
          break;
        }
        case 'pause': {
          if (room.hold) room.endHold(false);
          const p = Number.isFinite(msg.position) ? Math.max(0, msg.position) : room.pos();
          room.setState({ playing: false, position: p }, me, 'pause');
          break;
        }
        case 'seek': {
          if (!Number.isFinite(msg.position)) break;
          const from = room.pos();
          if (Math.abs(msg.position - from) < 1) break;     // ignore tiny nudges
          if (room.hold) room.hold.waiting.clear();
          room.setState({ position: Math.max(0, msg.position) }, me, 'seek');
          room.notice(`${me.name} jumped to ${fmt(msg.position)}`);
          break;
        }
        case 'buffering': {
          me.buffering = !!msg.on;
          if (me.buffering) {
            clearTimeout(me.bufTimer);
            me.bufTimer = setTimeout(() => { if (me.buffering && room.members.has(me.id)) room.startHold(me); }, HOLD_DEBOUNCE_MS);
          } else {
            clearTimeout(me.bufTimer);
            room.releaseFromHold(me.id);
          }
          room.broadcast({ type: 'members', members: room.membersList() });
          break;
        }
        case 'status': {          // periodic report: ready to play? how far off the room are they?
          me.ready = !!msg.ready;
          me.behind = Number.isFinite(msg.offset) ? Math.round(msg.offset * 10) / 10 : 0;
          if (msg.source === 'local' || msg.source === 'stream') me.source = msg.source;
          break;
        }
        case 'chat': {
          const text = String(msg.text || '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 300);
          if (!text) break;
          const entry = { from: me.name, text, at: now() };
          room.chat.push(entry); if (room.chat.length > 100) room.chat.shift();
          room.broadcast({ type: 'chat', ...entry });
          break;
        }
        case 'react': {
          if (!EMOJI.includes(msg.emoji)) break;
          room.broadcast({ type: 'react', from: me.name, emoji: msg.emoji, at: room.pos() });
          break;
        }
        case 'end':
          if (me.canEnd) this.end(room.id, me.name);
          else send({ type: 'notice', text: 'Only the person who started the party (or the host PC) can end it.' });
          break;
        case 'members':
          send({ type: 'members', members: room.membersList() });
          break;
        default:
      }
    });

    ws.on('close', () => {
      clearInterval(ka);
      if (room && me) { clearTimeout(me.bufTimer); room.remove(me); }
    });
    ws.on('error', () => {});
  }

  stop() {
    clearInterval(this.timer);
    for (const r of this.rooms.values()) {
      r.broadcast({ type: 'closed', text: 'The host stopped sharing. The party has ended.' });
      for (const m of r.members.values()) try { m.ws.close(); } catch { /* ignore */ }
    }
    try { this.wss.close(); } catch { /* ignore */ }
  }
}

module.exports = { Parties, EMOJI };
