/* PubHorror Watch Party sync client.
 * ONE file, used by both the desktop app (app/index.html) and the phone/browser page (served by the sidecar).
 *
 *   const sync = new PartySync({ url, room, name, kind, source, video, on: { ...callbacks } });
 *   sync.start();  ...  sync.stop();
 *
 * How it stays together:
 *   - The server keeps the room's official state {playing, position, at}. We estimate the server clock
 *     (NTP-style pings), so "where the room is now" = position + (serverNow - at) when playing.
 *   - Our own play/pause/seek (from any controls) is sent to the room; changes from others are applied here.
 *     Echoes are ignored by comparing with the room state rather than with timers.
 *   - Drift: every second we compare our position with the room's. 0.3-2 s off -> play up to 8% faster/slower
 *     until caught up; more than 2 s -> jump.
 *   - Buffering: if our video stalls while the room plays, we tell the room (it holds briefly for us).
 */
(function (global) {
  'use strict';

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  class PartySync {
    constructor(opts) {
      this.o = opts;
      this.v = opts.video;
      this.on = opts.on || {};
      this.source = opts.source || 'stream';
      this.state = null;
      this.offset = 0;              // serverNow ~= Date.now() + offset
      this.samples = [];
      this.ws = null;
      this.stopped = false;
      this.retry = 0;
      this.bufferingSent = false;
      this.lastHardSeek = 0;
      this.seekTimer = null;
      this.bufTimer = null;
      this.held = false;
      this.me = null;
      this.tune = PartySync.tuning(opts.device || PartySync.detectDevice());
      this.seekLead = this.tune.lead;               // seconds a jump takes here; learned as we go
      this.syncedSince = 0;
      this._bind();
    }

    // ------------------------------------------------------------ connection
    start() {
      this.stopped = false;
      this._connect();
      this.loop = setInterval(() => this._driftCheck(), 1000);
      this.statusLoop = setInterval(() => this._sendStatus(), 3000);
    }

    stop() {
      this.stopped = true;
      clearInterval(this.loop); clearInterval(this.statusLoop); clearInterval(this.pingLoop);
      clearTimeout(this.seekTimer); clearTimeout(this.bufTimer); clearTimeout(this.reconnectTimer);
      this._unbind();
      try { this.v.playbackRate = 1; } catch (e) { /* ignore */ }
      if (this.ws) { try { this.ws.close(); } catch (e) { /* ignore */ } }
      this.ws = null;
    }

    _connect() {
      let ws;
      try { ws = new WebSocket(this.o.url); } catch (e) { return this._reconnectLater(); }
      this.ws = ws;
      ws.onopen = () => {
        this.retry = 0;
        this.samples = [];
        this._send({ type: 'hello', room: this.o.room, name: this.o.name, kind: this.o.kind || 'browser', source: this.source,
                     key: this.o.ownerKey || undefined });
        let n = 0;                                  // quick burst of clock samples, then one every 10 s
        const burst = setInterval(() => { this._ping(); if (++n >= 5) clearInterval(burst); }, 250);
        clearInterval(this.pingLoop);
        this.pingLoop = setInterval(() => this._ping(), 10000);
      };
      ws.onmessage = ev => { let m; try { m = JSON.parse(ev.data); } catch (e) { return; } this._onMessage(m); };
      ws.onclose = () => {
        clearInterval(this.pingLoop);
        if (this.stopped || this.fatal) return;
        this._emit('connection', { connected: false });
        this._reconnectLater();
      };
      ws.onerror = () => {};
    }

    _reconnectLater() {
      if (this.stopped) return;
      const wait = [1000, 2000, 5000, 10000][Math.min(this.retry++, 3)];
      this.reconnectTimer = setTimeout(() => this._connect(), wait);
    }

    _send(msg) {
      if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(msg));
    }

    _ping() { this._send({ type: 'ping', t: Date.now() }); }

    _emit(name, data) { if (typeof this.on[name] === 'function') { try { this.on[name](data); } catch (e) { console.error(e); } } }

    serverNow() { return Date.now() + this.offset; }

    roomPosition() {
      const s = this.state;
      if (!s) return 0;
      return s.playing ? s.position + (this.serverNow() - s.at) / 1000 : s.position;
    }

    // ------------------------------------------------------------ messages from the room
    _onMessage(m) {
      switch (m.type) {
        case 'pong': {
          const rtt = Date.now() - m.t;
          this.samples.push({ rtt, off: m.server_now - (m.t + rtt / 2) });
          if (this.samples.length > 8) this.samples.shift();
          this.offset = this.samples.reduce((a, b) => (b.rtt < a.rtt ? b : a)).off;   // least-delayed sample wins
          break;
        }
        case 'welcome':
          if (!this.samples.length) this.offset = m.server_now - Date.now();
          this.me = m.you;
          this.held = !!(m.hold && m.hold.active);
          this._emit('welcome', m);
          this._emit('connection', { connected: true });
          this._apply(m.state, null, 'join');
          break;
        case 'state':
          this._apply(m.state, m.by, m.action);
          break;
        case 'heartbeat':
          if (!this.state || m.state.seq !== this.state.seq) this._apply(m.state, null, 'resync');
          break;
        case 'hold':
          this.held = !!m.active;
          this._emit('hold', m);
          break;
        case 'notice': this._emit('notice', m.text); break;
        case 'members': this._emit('members', m.members); break;
        case 'chat': this._emit('chat', m); break;
        case 'react': this._emit('react', m); break;
        case 'error':
          this.fatal = true;
          this._emit('error', m);
          break;
        case 'closed':
          this.fatal = true;
          this._emit('closed', m);
          break;
        default:
      }
    }

    _apply(state, by, action) {
      const first = !this.state;
      const prev = this.state;
      this.state = state;
      const v = this.v;
      const target = this._clampToMedia(this.roomPosition());
      // Only jump when the room really moved (someone seeked / paused / we just joined), not for every message:
      // a phone mid-stream is often a little off, and jumping it makes it re-buffer.
      const moved = first || !prev || action === 'seek' || action === 'join' || !!state.playing !== !!prev.playing;
      const tol = moved ? (state.playing ? this.tune.applyTol : 0.4) : this.tune.hardSeek;
      if (v.readyState >= 1 && Math.abs(v.currentTime - target) > tol) this._correctTo(this.roomPosition());
      this._setRate(1);
      if (state.playing && v.paused) this._play();
      else if (!state.playing && !v.paused) v.pause();
      if (by && action && action !== 'seek') {          // seeks get a "jumped to" notice from the server
        this._emit('notice', `${by} ${action === 'play' ? 'pressed play' : action === 'pause' ? 'paused' : action}`);
      }
      if (first || by || action) this._emit('state', { state, by, action });
    }

    _play() {
      if (this.playPending) return;                  // don't stack play() calls while one is still starting
      this.playPending = true;
      let p;
      try { p = this.v.play(); } catch (e) { p = null; }
      const done = () => { this.playPending = false; };
      if (p && p.then) {
        p.then(done, err => {
          done();
          // iOS/Safari etc. refuse to start video without a tap: ask the page to show a "tap to sync" button.
          if (err && err.name === 'NotAllowedError') this._emit('needTap');
        });
      } else done();
    }

    // Our own correction jump. Marked so it's never mistaken for the viewer seeking (which would move
    // EVERYONE), and so the re-buffering it causes never makes the whole room wait.
    _correctTo(roomPos) {
      const v = this.v;
      const lead = this.state && this.state.playing ? this.seekLead : 0;   // land where the room WILL be
      this.selfSeekPending = Date.now() + 8000;      // cleared by the 'seeked' this jump causes
      this.catchUpUntil = Date.now() + 8000;         // re-buffering until then is our own catch-up, not a stall
      this.lastHardSeek = Date.now();
      this.seekStartedAt = this.state && this.state.playing ? Date.now() : 0;
      this.syncedSince = 0;
      this.selfSeekTarget = this._clampToMedia(roomPos + lead);
      try { v.currentTime = this.selfSeekTarget; } catch (e) { /* not seekable yet */ }
    }

    // Changing speed makes iPhones stutter, and every change costs a little, so only touch it when it matters.
    _setRate(r) {
      const v = this.v;
      if (!this.tune.rate) r = 1;
      if (Math.abs((v.playbackRate || 1) - r) > 0.004) { try { v.playbackRate = r; } catch (e) { /* ignore */ } }
    }

    // Call from a click/tap handler (unlocks autoplay on phones), then re-applies the room state.
    unlock() {
      const v = this.v;
      const p = v.play();
      // after a "tap to sync" we may be well behind: jump straight to the room instead of creeping up
      const after = () => { if (this.state) this._apply(this.state, null, 'join'); };
      if (p && p.then) p.then(after, after); else after();
    }

    _clampToMedia(t) {
      const d = this.v.duration;
      return Number.isFinite(d) && d > 0 ? clamp(t, 0, Math.max(0, d - 0.5)) : Math.max(0, t);
    }

    // ------------------------------------------------------------ our own actions
    _bind() {
      const v = this.v;
      this.h = {
        play: () => {
          if (!this.state || this.state.playing) return;           // already what the room is doing (or our echo)
          this._local();
          this._send({ type: 'play', position: v.currentTime });
        },
        pause: () => {
          if (!this.state || !this.state.playing || v.ended) return;
          // A stall never fires 'pause' (it fires 'waiting' and stays un-paused), so this IS a real pause,
          // even if we'd told the room we were buffering. (Ignoring it here used to swallow phone pauses.)
          clearTimeout(this.bufTimer);
          if (this.bufferingSent) { this.bufferingSent = false; this._send({ type: 'buffering', on: false }); this._emit('buffering', false); }
          this._local();
          this._send({ type: 'pause', position: v.currentTime });
        },
        // The viewer starts a seek: the loading it causes isn't a stall (reporting it as one would make the
        // room "wait" and snap us back before our seek reaches everyone).
        seeking: () => {
          if (!this.state) return;
          const ours = Date.now() < (this.selfSeekPending || 0) && Math.abs(v.currentTime - (this.selfSeekTarget || 0)) < 2.5;
          if (ours) return;
          clearTimeout(this.bufTimer);
          this.catchUpUntil = Date.now() + 8000;
          this._local();
        },
        seeked: () => {
          if (!this.state) return;
          // Our own jump (it may land a bit off, on a keyframe). If the viewer seeked somewhere else while it
          // was loading, the browser only reports the viewer's seek -- that one must still go to the room.
          const ours = Date.now() < (this.selfSeekPending || 0) && Math.abs(v.currentTime - (this.selfSeekTarget || 0)) < 2.5;
          this.selfSeekPending = 0;
          if (ours) return;
          if (Math.abs(v.currentTime - this.roomPosition()) < 1.5) return;
          this._local();                                           // a real seek: don't let drift correction undo it
          clearTimeout(this.seekTimer);                            // dragging the bar fires many seeks; send the last
          this.seekTimer = setTimeout(() => {
            if (Math.abs(v.currentTime - this.roomPosition()) >= 1.5) { this._local(); this._send({ type: 'seek', position: v.currentTime }); }
          }, 350);
        },
        waiting: () => {
          if (!this.state || !this.state.playing) return;
          // Only make the room wait for a viewer who WAS keeping up and then stalled. Loading after joining,
          // or after our own catch-up jump, is our problem: we catch up on our own instead of freezing everyone.
          if (!this._stable() || Date.now() < (this.catchUpUntil || 0)) return;
          clearTimeout(this.bufTimer);
          this.bufTimer = setTimeout(() => {
            if (v.readyState < 3 && !v.paused && !this.bufferingSent) { this.bufferingSent = true; this._send({ type: 'buffering', on: true }); this._emit('buffering', true); }
          }, this.tune.stallMs);
        },
        ready: () => {
          clearTimeout(this.bufTimer);
          // learn how long a jump takes on this connection, so the next one lands closer
          if (this.seekStartedAt && v.readyState >= 3 && !v.seeking) {
            const took = (Date.now() - this.seekStartedAt) / 1000;
            this.seekLead = clamp(this.seekLead * 0.5 + took * 0.5, 0.1, 5);
            this.seekStartedAt = 0;
          }
          // our jump has landed and is playing: the catch-up allowance only needs to cover a moment more
          if (this.catchUpUntil && v.readyState >= 3 && !v.seeking) this.catchUpUntil = Math.min(this.catchUpUntil, Date.now() + 1500);
          if (this.bufferingSent && v.readyState >= 3) { this.bufferingSent = false; this._send({ type: 'buffering', on: false }); this._emit('buffering', false); }
        },
        meta: () => {
          this.syncedSince = 0;
          if (this.state) { const s = this.state; this.state = null; this._apply(s, null, 'join'); }   // jump to the room once
        },
      };
      v.addEventListener('play', this.h.play);
      v.addEventListener('pause', this.h.pause);
      v.addEventListener('seeking', this.h.seeking);
      v.addEventListener('seeked', this.h.seeked);
      v.addEventListener('waiting', this.h.waiting);
      v.addEventListener('playing', this.h.ready);
      v.addEventListener('canplay', this.h.ready);
      v.addEventListener('canplaythrough', this.h.ready);
      v.addEventListener('loadedmetadata', this.h.meta);
    }

    // After we act, give the room ~1.5 s to confirm before drift correction second-guesses us.
    _local() { this.localUntil = Date.now() + 1500; }

    _unbind() {
      const v = this.v, h = this.h;
      v.removeEventListener('play', h.play);
      v.removeEventListener('pause', h.pause);
      v.removeEventListener('seeking', h.seeking);
      v.removeEventListener('seeked', h.seeked);
      v.removeEventListener('waiting', h.waiting);
      v.removeEventListener('playing', h.ready);
      v.removeEventListener('canplay', h.ready);
      v.removeEventListener('canplaythrough', h.ready);
      v.removeEventListener('loadedmetadata', h.meta);
    }

    play() { this._send({ type: 'play', position: this.v.currentTime }); }
    pause() { this._send({ type: 'pause', position: this.v.currentTime }); }
    seek(t) { this._send({ type: 'seek', position: t }); }
    chat(text) { this._send({ type: 'chat', text }); }
    end() { this._send({ type: 'end' }); }          // only works for the creator or the hosting PC
    react(emoji) { this._send({ type: 'react', emoji }); }
    setSource(s) { this.source = s; this._sendStatus(); }

    // ------------------------------------------------------------ drift correction
    _driftCheck() {
      const v = this.v, s = this.state, t = this.tune;
      if (!s || v.readyState < 1) return;
      if (this.bufferingSent && v.readyState >= 3) this.h.ready();
      if (v.seeking || this.bufferingSent || Date.now() < (this.localUntil || 0)) return;
      const target = this._clampToMedia(this.roomPosition());
      const d = v.currentTime - target;               // + ahead, - behind
      this.lastDrift = d;
      if (!s.playing) {
        if (!v.paused) v.pause();
        if (Math.abs(d) > 0.5 && Date.now() - this.lastHardSeek > 1500) this._correctTo(this.roomPosition());
        this._setRate(1);
        return;
      }
      if (v.paused && !v.ended) { this._play(); return; }
      if (v.readyState < 3) return;                   // still loading: measuring now would only cause another jump
      const ad = Math.abs(d);
      if (ad < 1) { if (!this.syncedSince) this.syncedSince = Date.now(); } else this.syncedSince = 0;
      if (ad > t.hardSeek) {
        if (Date.now() - this.lastHardSeek > t.cooldown) this._correctTo(this.roomPosition());   // don't keep jumping
        this.correcting = false;
        this._setRate(1);
      } else if (t.rate && (ad > t.deadband || (this.correcting && ad > 0.15))) {
        this.correcting = true;                       // keep going until we're really close (hysteresis)
        // whole-percent steps, so the speed isn't fiddled with every second
        this._setRate(Math.round(clamp(1 - d * (t.gain || 0.3), 1 - t.maxRate, 1 + t.maxRate) * 100) / 100);
      } else {
        this.correcting = false;
        this._setRate(1);
      }
    }

    // Keeping up (within 1 s) for a few seconds: only then can our stalls hold the room.
    _stable() { return !!this.syncedSince && Date.now() - this.syncedSince > 4000; }

    _sendStatus() {
      const v = this.v;
      this._send({ type: 'status', ready: v.readyState >= 2, offset: this.lastDrift || 0, source: this.source });
    }
  }

  // Phones stream over Wi-Fi: jumps take a second or more, and iPhones stutter when the speed changes.
  // So phones correct more gently and less often than the desktop app.
  PartySync.detectDevice = function () {
    const n = typeof navigator !== 'undefined' ? navigator : {};
    const ua = String(n.userAgent || '');
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && n.maxTouchPoints > 1)) return 'apple';
    if (/Android|Mobile/i.test(ua)) return 'mobile';
    return 'desktop';
  };
  PartySync.tuning = function (device) {
    if (device === 'apple')  return { device, rate: false, deadband: 1, maxRate: 0, hardSeek: 1.2, cooldown: 8000, applyTol: 1, stallMs: 1500, lead: 1 };
    if (device === 'mobile') return { device, rate: true, gain: 0.5, deadband: 0.4, maxRate: 0.12, hardSeek: 2.5, cooldown: 8000, applyTol: 1, stallMs: 1500, lead: 1 };
    return { device: 'desktop', rate: true, deadband: 0.3, maxRate: 0.08, hardSeek: 2, cooldown: 4000, applyTol: 0.75, stallMs: 700, lead: 0.3 };
  };

  PartySync.EMOJI = ['👻', '😱', '💀', '🔪', '🩸', '😂', '🍿', '❤️'];
  global.PartySync = PartySync;
})(typeof window !== 'undefined' ? window : globalThis);
