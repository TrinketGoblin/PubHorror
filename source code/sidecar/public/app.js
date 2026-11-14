'use strict';
// PubHorror phone/browser client. Routes (so the phone's Back button works):
//   #/            library grid
//   #/t/<id>      title details
//   #/play/<id>/<episode index>
(function () {
  const $ = id => document.getElementById(id);
  const state = { items: [], version: -1, host: '', hostId: '', type: '', q: '', detail: null, pollTimer: null };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids) if (c != null) n.append(c);
    return n;
  }

  let toastTimer;
  function toast(msg, ms = 3500) {
    const t = $('toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (r.status === 401) { location.replace('/login'); throw new Error('signed out'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `Error ${r.status}`);
    return j;
  }

  function fmtTime(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0');
  }

  // Each phone remembers its own resume position (the host's library is never written to).
  const posKey = (id, ep) => `ph_pos:${state.hostId}:${id}:${ep}`;
  function getPos(id, ep) { try { return JSON.parse(localStorage.getItem(posKey(id, ep)) || 'null'); } catch { return null; } }
  function setPos(id, ep, t, d) { try { localStorage.setItem(posKey(id, ep), JSON.stringify({ t, d })); } catch { /* private mode */ } }

  // ------------------------------------------------------------------ library grid
  async function loadLibrary() {
    const j = await api('/api/library');
    state.host = j.host_name;
    $('host-name').textContent = '🎞 ' + j.host_name;
    document.title = j.host_name + ' · PubHorror';
    if (j.version !== state.version) {
      state.version = j.version;
      state.items = j.items;
      renderGrid();
    }
  }

  function poster(item) {
    if (!item.poster) return el('div', { class: 'fallback', text: item.title });
    const img = el('img', { src: item.poster, alt: '', loading: 'lazy', decoding: 'async', referrerpolicy: 'no-referrer' });
    img.addEventListener('error', () => img.replaceWith(el('div', { class: 'fallback', text: item.title })));
    return img;
  }

  function renderGrid() {
    const q = state.q.trim().toLowerCase();
    const list = state.items.filter(i => (!state.type || i.type === state.type) &&
      (!q || i.title.toLowerCase().includes(q) || i.categories.some(c => c.toLowerCase().includes(q))));
    const grid = $('grid');
    grid.replaceChildren(...list.map(i => el('button', {
        class: 'card' + (i.playable ? '' : ' off'),
        onclick: () => { location.hash = '#/t/' + encodeURIComponent(i.id); } },
      el('div', { class: 'art' }, poster(i), i.type === 'series' ? el('span', { class: 'badge', text: '📺 Series' }) : null),
      el('div', { class: 'card-content' },
        el('div', { class: 't', text: i.title }),
        el('div', { class: 'y', text: [i.year, i.type === 'series' ? 'Series' : 'Movie', i.playable ? null : 'not streamable'].filter(Boolean).join(' · ') }),
        i.categories.length ? el('div', { class: 'cats' }, ...i.categories.slice(0, 2).map(c => el('span', { text: c }))) : null))));
    const empty = $('empty');
    empty.hidden = list.length > 0;
    empty.textContent = state.items.length ? 'Nothing matches that search.' : 'This library is empty.';
  }

  // ------------------------------------------------------------------ details + prepare
  async function showDetail(id) {
    stopPoll();
    let m;
    try { m = await api('/api/movie/' + encodeURIComponent(id)); }
    catch (e) { toast(e.message); location.hash = '#/'; return; }
    state.detail = m;
    $('d-poster').replaceChildren(poster(m));
    $('d-title').textContent = m.title;
    $('d-sub').textContent = [m.year, m.director].filter(Boolean).join(' · ');
    $('d-cats').replaceChildren(...m.categories.map(c => el('span', { text: c })));
    $('d-desc').textContent = m.desc || '';
    $('d-cast').textContent = m.cast.length ? 'Starring ' + m.cast.join(', ') : '';
    $('d-episodes').replaceChildren();
    $('d-play-area').replaceChildren();

    if (m.type === 'series') renderEpisodes(m);
    else if (!m.playable) $('d-play-area').append(el('div', { class: 'prep', text: 'This title has no file the host can stream (e.g. a disc that hasn’t been ripped).' }));
    else checkPlayable(m.id, 0, $('d-play-area'));
  }

  function renderEpisodes(m) {
    const box = $('d-episodes');
    let season;
    for (const e of m.episodes) {
      const sname = e.season_name || (e.season != null ? 'Season ' + e.season : null);
      if (sname && sname !== season) { season = sname; box.append(el('h3', { class: 'season', text: sname })); }
      const p = getPos(m.id, e.index);
      const seen = p && p.d && p.t / p.d > 0.9 ? 'Watched' : (p && p.t > 30 ? fmtTime(p.t) : '');
      box.append(el('button', { class: 'ep', disabled: !e.playable,
          onclick: () => { location.hash = `#/play/${encodeURIComponent(m.id)}/${e.index}`; } },
        el('span', { class: 'n', text: e.episode != null ? 'E' + e.episode : String(e.index + 1) }),
        el('span', { class: 'et', text: e.title }),
        seen ? el('span', { class: 'seen', text: seen }) : null,
        e.playable ? el('span', { class: 'ep-party', title: 'Start a watch party', text: '🎉',
          onclick: ev => { ev.stopPropagation(); window.PHParty.start(m.id, e.index); } }) : null));
    }
    if (!m.episodes.length) box.append(el('p', { class: 'muted', text: 'No episodes found.' }));
  }

  function partyButton(id, ep) {
    return el('button', { class: 'play-btn party-btn', onclick: () => window.PHParty.start(id, ep) }, '🎉 Start watch party');
  }

  function stopPoll() { clearTimeout(state.pollTimer); state.pollTimer = null; }

  function describe(st) {
    const why = st.reasons.length ? st.reasons.join(', ') : 'its format';
    if (st.mode === 'remux') {
      return [el('b', { text: 'Needs a quick conversion for phones. ' }),
              `Because of ${why}, the host repackages it once (usually a minute or two, no quality loss).`];
    }
    return [el('b', { text: 'Needs converting for phones. ' }),
            `Because of ${why}, the host has to re-encode it once. That can take a while (often about as long as the movie) and works the host PC hard.`];
  }

  async function checkPlayable(id, ep, area, autoplay) {
    stopPoll();
    let st;
    try { st = await api(`/api/play/${encodeURIComponent(id)}?ep=${ep}`); }
    catch (e) { area.replaceChildren(el('div', { class: 'prep', text: e.message })); return null; }

    if (st.state === 'ready') {
      const p = getPos(id, ep);
      const resume = p && p.t > 30 && (!p.d || p.t / p.d < 0.95);
      area.replaceChildren(el('button', { class: 'play-btn',
        onclick: () => { location.hash = `#/play/${encodeURIComponent(id)}/${ep}`; } },
        resume ? `▶ Resume from ${fmtTime(p.t)}` : '▶ Play'), partyButton(id, ep));
      return st;
    }
    if (st.state === 'needs_prepare' || st.state === 'error') {
      area.replaceChildren(el('div', { class: 'prep' }, ...describe(st),
        st.error ? el('div', { style: 'margin-top:8px;color:var(--hi)', text: '⚠ ' + st.error }) : null,
        partyButton(id, ep),
        el('button', { class: 'play-btn', onclick: async ev => {
          ev.target.disabled = true;
          try { await api(`/api/prepare/${encodeURIComponent(id)}?ep=${ep}`, { method: 'POST' }); }
          catch (e) { toast(e.message); }
          checkPlayable(id, ep, area);
        } }, st.state === 'error' ? 'Try again' : 'Convert for phones')));
      return st;
    }
    if (st.state === 'queued' || st.state === 'converting') {
      const bar = el('div', {});
      bar.style.width = st.percent + '%';
      area.replaceChildren(el('div', { class: 'prep' }, ...describe(st),
        el('div', { style: 'margin-top:8px', text: st.state === 'queued'
          ? `Waiting for another conversion to finish (#${st.position} in line)…`
          : `Converting… ${st.percent}% — you can leave this page; it keeps going on the host.` }),
        el('div', { class: 'progress' }, bar)));
      state.pollTimer = setTimeout(() => checkPlayable(id, ep, area), 2500);
      return st;
    }
    area.replaceChildren(el('div', { class: 'prep', text: st.error || 'This file can’t be played here.' }));
    return st;
  }

  // ------------------------------------------------------------------ player
  const video = $('video');
  let playing = null, saveTimer = null;

  async function showPlayer(id, ep) {
    stopPoll();
    if (!state.detail || state.detail.id !== id) {
      try { state.detail = await api('/api/movie/' + encodeURIComponent(id)); }
      catch (e) { toast(e.message); location.hash = '#/'; return; }
    }
    const m = state.detail;
    const e = m.type === 'series' ? m.episodes[ep] : null;
    $('p-title').textContent = e ? `${m.title} — ${e.title}` : m.title;
    const msg = $('p-msg');
    msg.hidden = true;

    let st;
    try { st = await api(`/api/play/${encodeURIComponent(id)}?ep=${ep}`); } catch (err) { toast(err.message); history.back(); return; }
    if (st.state !== 'ready') {        // needs converting: send them to the details page to start it
      location.replace('#/t/' + encodeURIComponent(id));
      return;
    }
    playing = { id, ep };
    const next = m.type === 'series' ? m.episodes.slice(ep + 1).find(x => x.playable) : null;
    $('p-next').hidden = !next;
    $('p-next').onclick = next ? () => location.replace(`#/play/${encodeURIComponent(id)}/${next.index}`) : null;

    video.src = st.src;
    const p = getPos(id, ep);
    video.onloadedmetadata = () => {
      if (p && p.t > 30 && (!p.d || p.t / p.d < 0.95)) video.currentTime = p.t;
      video.play().catch(() => { /* needs a tap on iOS; controls are visible */ });
    };
    video.onerror = () => {
      msg.hidden = false;
      msg.textContent = 'This browser couldn’t play the file. If it keeps happening, tell the host which title it was.';
    };
    video.onended = () => {
      setPos(id, ep, video.duration, video.duration);
      if (next) location.replace(`#/play/${encodeURIComponent(id)}/${next.index}`);
    };
    clearInterval(saveTimer);
    saveTimer = setInterval(() => {
      if (playing && !video.paused && video.currentTime > 0) setPos(playing.id, playing.ep, video.currentTime, video.duration || 0);
    }, 5000);
  }

  function stopPlayer() {
    if (playing && video.currentTime > 0) setPos(playing.id, playing.ep, video.currentTime, video.duration || 0);
    playing = null;
    clearInterval(saveTimer);
    video.pause();
    video.removeAttribute('src');
    video.load();                       // actually drops the network connection
  }

  // ------------------------------------------------------------------ TV (AirPlay on iPhone, Cast on Android Chrome)
  // The button only shows when the phone actually sees a TV. The TV fetches the video itself (the src
  // carries the media token), and must be on the same Wi-Fi as the host PC.
  function castButton(v, btn) {
    btn.hidden = true;
    if (window.WebKitPlaybackTargetAvailabilityEvent && v.webkitShowPlaybackTargetPicker) {
      v.addEventListener('webkitplaybacktargetavailabilitychanged', e => { btn.hidden = e.availability !== 'available'; });
      btn.addEventListener('click', () => { try { v.webkitShowPlaybackTargetPicker(); } catch { /* ignore */ } });
    } else if (v.remote && v.remote.watchAvailability) {
      v.remote.watchAvailability(a => { btn.hidden = !a; }).catch(() => { btn.hidden = true; });
      btn.addEventListener('click', () => {
        v.remote.prompt().catch(e => { if (e && e.name !== 'AbortError') toast('Couldn’t reach the TV: ' + e.message); });
      });
    }
  }
  castButton(video, $('p-cast'));

  // "🎉 Party" in the normal player: turn what I'm watching into a party, from where I am now.
  $('p-party').addEventListener('click', () => {
    if (!playing || !window.PHParty) return;
    const { id, ep } = playing, at = video.currentTime || 0;
    window.PHParty.start(id, ep, at);
  });

  // ------------------------------------------------------------------ routing
  window.PH = { el, api, toast, castButton };
  function route() {
    const h = location.hash.replace(/^#\/?/, '');
    const parts = h.split('/').map(decodeURIComponent);
    const inPlayer = parts[0] === 'play' && parts[1];
    const inDetail = parts[0] === 't' && parts[1];
    const inParty = parts[0] === 'party' && parts[1];
    if (!inPlayer) stopPlayer();
    if (!inParty && window.PHParty && window.PHParty.active()) window.PHParty.leave();
    $('player').hidden = !inPlayer;
    $('detail').hidden = !inDetail;
    $('party').hidden = !inParty;
    document.body.style.overflow = inPlayer || inDetail || inParty ? 'hidden' : '';
    if (inPlayer) showPlayer(parts[1], Number(parts[2] || 0) | 0);
    else if (inDetail) showDetail(parts[1]);
    else if (inParty) { stopPoll(); window.PHParty.show(parts[1]); }
    else { stopPoll(); if (window.PHParty) window.PHParty.banners(); }
  }

  document.querySelectorAll('[data-back]').forEach(b => b.addEventListener('click', () => {
    if (history.length > 1) history.back(); else location.hash = '#/';
  }));
  $('search').addEventListener('input', e => { state.q = e.target.value; renderGrid(); });
  document.querySelectorAll('#top .chip').forEach(c => c.addEventListener('click', () => {
    document.querySelectorAll('#top .chip').forEach(x => x.classList.toggle('on', x === c));
    state.type = c.dataset.type; renderGrid();
  }));
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadLibrary().catch(() => {}); });
  setInterval(() => {
    if (document.hidden || playing) return;
    loadLibrary().catch(() => {});
    if (!location.hash.replace(/^#\/?/, '') && window.PHParty) window.PHParty.banners();
  }, 30000);

  api('/api/whoami').then(j => { state.hostId = j.host_id; return loadLibrary(); })
    .then(route)
    .catch(e => { if (e.message !== 'signed out') toast('Can’t reach the host: ' + e.message, 8000); });
})();
