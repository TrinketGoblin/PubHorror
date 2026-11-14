'use strict';
// LAN auto-discovery over mDNS ("Bonjour"), service type _pubhorror._tcp.
// What we advertise is only: display name, a random host id, protocol version, port, and the LAN IP.
// The access code is never advertised. mDNS does not cross Tailscale; those hosts are added by address.
const makeMdns = require('multicast-dns');

const SERVICE = '_pubhorror._tcp.local';

function txtToObj(data) {
  const out = {};
  for (const item of [].concat(data || [])) {
    const s = Buffer.isBuffer(item) ? item.toString('utf8') : String(item);
    const i = s.indexOf('=');
    if (i > 0) out[s.slice(0, i)] = s.slice(i + 1);
  }
  return out;
}

// ------------------------------------------------------------------ advertise (while sharing is on)
function advertise({ name, id, port, ip, protocol }) {
  if (!ip) return { stop() {} };
  const label = `${String(name).replace(/[.\\]/g, ' ').slice(0, 50)} [${id.slice(0, 6)}]`;
  const instance = `${label}.${SERVICE}`;
  const target = `pubhorror-${id.slice(0, 12)}.local`;
  const TTL = 120;
  const records = ttl => ({
    ptr: { name: SERVICE, type: 'PTR', ttl, data: instance },
    srv: { name: instance, type: 'SRV', ttl, data: { port, target, priority: 0, weight: 0 } },
    txt: { name: instance, type: 'TXT', ttl, data: [`id=${id}`, `name=${name}`, `proto=${protocol}`] },
    a:   { name: target, type: 'A', ttl, data: ip },
  });
  let mdns;
  try {
    mdns = makeMdns({ interface: ip, reuseAddr: true });
  } catch (e) {
    console.error('[sidecar] mDNS unavailable:', e.message);
    return { stop() {} };
  }
  mdns.on('error', e => console.error('[sidecar] mDNS error:', e.message));
  mdns.on('warning', () => {});
  const r = records(TTL);
  const announce = () => mdns.respond({ answers: [r.ptr], additionals: [r.srv, r.txt, r.a] });

  mdns.on('query', q => {
    const answers = [];
    for (const qq of q.questions || []) {
      const n = String(qq.name || '').toLowerCase();
      if (n === SERVICE.toLowerCase() && (qq.type === 'PTR' || qq.type === 'ANY')) answers.push(r.ptr);
      else if (n === instance.toLowerCase() && (qq.type === 'SRV' || qq.type === 'ANY')) answers.push(r.srv);
      else if (n === instance.toLowerCase() && qq.type === 'TXT') answers.push(r.txt);
      else if (n === target.toLowerCase() && (qq.type === 'A' || qq.type === 'ANY')) answers.push(r.a);
    }
    if (answers.length) mdns.respond({ answers, additionals: [r.srv, r.txt, r.a].filter(x => !answers.includes(x)) });
  });
  announce();
  const t = setTimeout(announce, 1000);
  console.error(`[sidecar] advertising on the LAN as "${name}" (${ip}:${port})`);
  return {
    stop() {
      clearTimeout(t);
      try {
        const g = records(0);        // "goodbye" so other PCs drop us right away
        mdns.respond({ answers: [g.ptr], additionals: [g.srv, g.txt, g.a] }, () => mdns.destroy());
        setTimeout(() => { try { mdns.destroy(); } catch { /* already gone */ } }, 300);
      } catch { /* ignore */ }
    },
  };
}

// ------------------------------------------------------------------ browse (one-shot, prints JSON)
function discover({ timeoutMs = 3000, iface } = {}) {
  return new Promise(resolve => {
    let mdns;
    try { mdns = makeMdns(iface ? { interface: iface, reuseAddr: true } : { reuseAddr: true }); }
    catch (e) { return resolve({ error: e.message, hosts: [] }); }
    mdns.on('error', () => {});
    mdns.on('warning', () => {});
    const instances = new Map();   // instance name -> { port, target, txt }
    const addrs = new Map();       // target -> ip
    mdns.on('response', resp => {
      for (const rec of [...(resp.answers || []), ...(resp.additionals || [])]) {
        const n = String(rec.name || '');
        if (rec.type === 'PTR' && n.toLowerCase() === SERVICE.toLowerCase() && rec.ttl > 0) {
          if (!instances.has(rec.data)) instances.set(rec.data, {});
        } else if (rec.type === 'SRV' && n.toLowerCase().endsWith(SERVICE.toLowerCase())) {
          const i = instances.get(n) || {}; i.port = rec.data.port; i.target = rec.data.target; instances.set(n, i);
        } else if (rec.type === 'TXT' && n.toLowerCase().endsWith(SERVICE.toLowerCase())) {
          const i = instances.get(n) || {}; i.txt = txtToObj(rec.data); instances.set(n, i);
        } else if (rec.type === 'A') {
          addrs.set(n.toLowerCase(), rec.data);
        }
      }
    });
    const ask = () => mdns.query({ questions: [{ name: SERVICE, type: 'PTR' }] });
    ask();
    const again = setTimeout(ask, 1000);
    setTimeout(() => {
      clearTimeout(again);
      const hosts = [];
      for (const [, i] of instances) {
        const ip = i.target && addrs.get(String(i.target).toLowerCase());
        if (!ip || !i.port || !i.txt || !i.txt.id) continue;
        hosts.push({ id: i.txt.id, name: i.txt.name || 'PubHorror', address: ip, port: i.port,
                     protocol: Number(i.txt.proto) || null, url: `http://${ip}:${i.port}` });
      }
      try { mdns.destroy(); } catch { /* ignore */ }
      resolve({ hosts });
    }, timeoutMs);
  });
}

module.exports = { advertise, discover };
