'use strict';
// Phone/browser Watch Party view (#/party/<ROOM>). Uses the shared PartySync client.
(function () {
  const PH = window.PH;                       // helpers exported by app.js
  const { el, api, toast } = PH;
  const $ = id => document.getElementById(id);
  let sync = null, ctx = null, poll = null;

  function nameKey() { return 'ph_party_name'; }
  function savedName() { try { return localStorage.getItem(nameKey()) || ''; } catch { return ''; } }
  function saveName(n) { try { localStorage.setItem(nameKey(), n); } catch { /* private mode */ } }
  // The creator's secret for ending the party, remembered on this phone only.
  function ownerKey(room) { try { return localStorage.getItem('ph_party_key:' + room) || ''; } catch { return ''; } }
  function saveOwnerKey(room, k) { try { localStorage.setItem('ph_party_key:' + room, k); } catch { /* private mode */ } }

  async function show(roomId) {
    leave(true);
    const view = $('party');
    view.hidden = false;
    $('pt-title').textContent = 'Watch party';
    $('pt-stage-msg').textContent = '';
    $('pt-join').hidden = false;
    $('pt-room-ui').hidden = true;
    let room;
    try { room = await api('/api/party/' + encodeURIComponent(roomId)); }
    catch (e) { $('pt-join').replaceChildren(el('p', { class: 'prep', text: e.message })); return; }
    ctx = { room };
    const m = room.movie;
    $('pt-title').textContent = m.title + (m.episode ? ' — ' + m.episode.title : '');
    const input = el('input', { id: 'pt-name', maxlength: '24', placeholder: 'Your name', value: savedName(), autocomplete: 'nickname' });
    const btn = el('button', { class: 'play-btn', text: '🎉 Join the party' });
    $('pt-join').replaceChildren(
      el('p', { text: `${room.created_by} started this party for ${m.title}. ${room.members} watching now.` }),
      el('label', { for: 'pt-name', text: 'Your name (others see it when you pause, play or skip)' }), input, btn);
    const go = () => {
      const name = input.value.trim();
      if (!name) { input.focus(); return; }
      saveName(name);
      join(name);                             // called from the tap: lets the phone start video with sound
    };
    btn.onclick = go;
    input.onkeydown = e => { if (e.key === 'Enter') go(); };
  }

  async function join(name) {
    const v = $('pt-video');
    $('pt-join').hidden = true;
    $('pt-room-ui').hidden = false;
    $('pt-room-code').textContent = ctx.room.id;
    $('pt-end').hidden = true;
    $('pt-emoji').replaceChildren(...window.PartySync.EMOJI.map(e => el('button', { text: e, onclick: () => sync && sync.react(e) })));
    // unlock playback inside the tap (iOS needs this)
    try { v.muted = true; v.play().catch(() => {}); v.pause(); v.muted = false; } catch { /* ignore */ }
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    sync = new window.PartySync({
      url: `${proto}//${location.host}/ws/party`, room: ctx.room.id, name, kind: 'browser', source: 'stream', video: v,
      ownerKey: ownerKey(ctx.room.id),
      on: {
        welcome: w => { ctx.me = w.you; $('pt-end').hidden = !w.you.can_end; (w.chat || []).forEach(addChat); },
        notice: notice,
        members: renderMembers,
        chat: addChat,
        react: r => float(r.emoji),
        hold: h => banner(h.active ? `⏸ Waiting for ${h.waiting_for.join(', ')}…` : null),
        needTap: () => { $('pt-tap').hidden = false; },
        connection: c => banner(c.connected ? null : 'Reconnecting…'),
        error: e => banner('⚠ ' + e.text),
        closed: e => ended(e.text),
      },
    });
    sync.start();
    loadStream(false);
  }

  async function loadStream(prepare) {
    clearTimeout(poll);
    const m = ctx.room.movie, v = $('pt-video');
    const base = `/api/${prepare ? 'prepare' : 'play'}/${encodeURIComponent(m.id)}?ep=${m.ep || 0}`;
    let s;
    try { s = await api(base, prepare ? { method: 'POST' } : undefined); }
    catch (e) { banner('⚠ ' + e.message); return; }
    if (!ctx) return;
    if (s.state === 'ready') { banner(null); if (v.getAttribute('src') !== s.src) v.src = s.src; return; }
    if (s.state === 'needs_prepare' || s.state === 'error') {
      banner(`This file needs converting for phones first (${(s.reasons || []).join(', ')}). The others keep watching meanwhile.`,
             { label: s.state === 'error' ? 'Try again' : 'Convert now', fn: () => loadStream(true) });
      return;
    }
    if (s.state === 'queued' || s.state === 'converting') {
      banner(s.state === 'queued' ? '⏳ Waiting for another conversion…' : `⏳ Converting for phones… ${s.percent}%. You'll join automatically.`);
      poll = setTimeout(() => loadStream(false), 2500);
      return;
    }
    banner('⚠ ' + (s.error || 'This can’t play here.'));
  }

  function leave(silent) {
    clearTimeout(poll);
    if (sync) { sync.stop(); sync = null; }
    const v = $('pt-video');
    if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
    ctx = null;
    if (!silent) toast('👋 You left the party');
  }

  function ended(text) {
    if (sync) { sync.stop(); sync = null; }
    const v = $('pt-video');
    v.pause(); v.removeAttribute('src'); v.load();
    $('pt-end').hidden = true;
    banner('🎬 ' + text, { label: 'Back to the library', fn: () => { location.hash = '#/'; } });
  }

  function banner(text, action) {
    const b = $('pt-banner');
    if (!text) { b.hidden = true; return; }
    b.replaceChildren(el('span', { text }));
    if (action) b.append(el('button', { class: 'chip on', text: action.label, onclick: action.fn }));
    b.hidden = false;
  }

  function notice(text) {
    const box = $('pt-notices');
    const n = el('div', { class: 'pt-notice', text });
    box.append(n);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { n.style.opacity = '0'; setTimeout(() => n.remove(), 600); }, 3500);
  }

  function renderMembers(list) {
    $('pt-members').replaceChildren(...list.map(x => el('span', { class: 'pt-member',
      text: (x.buffering ? '⏳ ' : '') + x.name + (ctx && ctx.me && x.id === ctx.me.id ? ' (you)' : '') })));
  }

  function addChat(m) {
    const box = $('pt-chat');
    box.append(el('div', { class: 'pt-msg' }, el('b', { text: m.from + ': ' }), el('span', { text: m.text })));
    while (box.children.length > 150) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
  }

  function float(emoji) {
    const e = el('div', { class: 'pt-react', text: emoji });
    e.style.left = (10 + Math.random() * 75) + '%';
    $('pt-reactions').append(e);
    setTimeout(() => e.remove(), 2600);
  }

  PH.castButton($('pt-video'), $('pt-cast'));

  $('pt-chat-form').addEventListener('submit', e => {
    e.preventDefault();
    const i = $('pt-chat-input');
    if (sync && i.value.trim()) sync.chat(i.value.trim());
    i.value = '';
  });
  $('pt-end').addEventListener('click', () => {
    if (sync && confirm('End the party for everyone?')) sync.end();
  });
  $('pt-tap').addEventListener('click', () => { if (sync) sync.unlock(); $('pt-tap').hidden = true; });
  $('pt-copy').addEventListener('click', () => {
    const link = `${location.origin}/#/party/${ctx ? ctx.room.id : ''}`;
    (navigator.clipboard ? navigator.clipboard.writeText(link) : Promise.reject()).then(
      () => toast('📋 Party link copied. Friends also need the access code.'),
      () => toast(link, 8000));
  });

  // Start a party from the details page (any viewer can).
  async function start(movieId, ep, startAt) {
    let name = savedName();
    if (!name) {
      name = (prompt('Your name for the watch party:') || '').trim().slice(0, 24);
      if (!name) return;
      saveName(name);
    }
    try {
      const r = await api('/api/party', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                          body: JSON.stringify({ movie_id: movieId, ep, name, start_at: startAt || 0 }) });
      if (r.owner_key) saveOwnerKey(r.id, r.owner_key);
      location.hash = '#/party/' + r.id;
    } catch (e) { toast(e.message); }
  }

  async function banners() {           // "🎉 Fox is hosting a party" on the library page
    let j;
    try { j = await api('/api/parties'); } catch { return; }
    $('parties').replaceChildren(...j.parties.map(p => el('button', { class: 'party-banner',
      onclick: () => { location.hash = '#/party/' + p.id; } },
      `🎉 ${p.created_by}'s watch party: ${p.movie.title}${p.movie.episode ? ' — ' + p.movie.episode.title : ''} · ${p.members} watching — Join`)));
  }

  window.PHParty = { show, leave, start, banners, active: () => !!sync };
})();
