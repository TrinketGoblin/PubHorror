'use strict';
// PubHorror sharing sidecar.
//   node server.js --config <path>    (started by the PubHorror desktop app)
//
// stdout carries exactly one machine-readable line: {"ready":true,...} or {"ready":false,"error":...}.
// Everything else goes to stderr, which the desktop app writes to data/share/sidecar.log.
//
// It shuts itself down when: the app asks (/control/shutdown), the app's process disappears,
// or its stdin pipe closes. On Windows the app also puts it in a kill-on-close Job Object.
const http = require('http');
const { loadConfig } = require('./src/config');
const { Auth } = require('./src/auth');
const { createApp, createUpgrade } = require('./src/app');
const { Parties } = require('./src/party');
const { Library } = require('./src/library');
const { Media } = require('./src/stream');

function emit(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function arg(name) { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : null; }

// One-shot LAN search used by the app's "Find on my network" button:
//   node server.js --discover [--timeout 3000] [--interface 192.168.1.5]   -> one JSON line, then exit
if (process.argv.includes('--discover')) {
  const { discover } = require('./src/discovery');
  discover({ timeoutMs: Math.min(10000, Number(arg('--timeout')) || 3000), iface: arg('--interface') || undefined })
    .then(r => { emit(r); process.exit(0); });
  return;
}

const cfgPath = arg('--config');
if (!cfgPath) {
  console.error('usage: node server.js --config <file>   |   node server.js --discover');
  process.exit(2);
}

let cfg;
try {
  cfg = loadConfig(cfgPath);
} catch (e) {
  console.error('[sidecar] bad config:', e.message);
  emit({ ready: false, error: 'config: ' + e.message });
  process.exit(2);
}

const auth = new Auth(cfg.access_code);
const library = new Library(cfg.library_file);   // read-only
const media = new Media(cfg);
const servers = [];
const state = { port: 0, bound: [], failed: [], startedAt: Date.now() };
let stopping = false;
let advert = { stop() {} };

function shutdown(reason) {
  if (stopping) return;
  stopping = true;
  console.error(`[sidecar] shutting down: ${reason}`);
  try { parties.stop(); media.stop(); library.stop(); advert.stop(); } catch { /* ignore */ }
  for (const s of servers) {
    try { s.close(); s.closeAllConnections(); } catch { /* ignore */ }
  }
  setTimeout(() => process.exit(0), 300).unref();
}

const parties = new Parties({ library, auth, cfg });
const ctx = { cfg, auth, state, shutdown, library, media, parties };
const handler = createApp(ctx);
const upgrade = createUpgrade(ctx, () => ctx.allowedHosts());

function listen(address, port) {
  return new Promise((resolve, reject) => {
    const srv = http.createServer(handler);
    srv.on('upgrade', upgrade);
    srv.keepAliveTimeout = 5000;
    srv.headersTimeout = 15000;
    srv.requestTimeout = 0; // long video responses must not be cut off
    srv.once('error', reject);
    srv.listen({ host: address, port, exclusive: true }, () => {
      srv.removeListener('error', reject);
      srv.on('error', e => console.error(`[sidecar] server error on ${address}:`, e.message));
      resolve(srv);
    });
  });
}

async function start() {
  // Loopback first, so we settle on a port that is free; then every other address uses the same port.
  const loop = cfg.bind.find(b => b.kind === 'loopback');
  let port = cfg.port;
  for (let attempt = 0; ; attempt++) {
    try {
      servers.push(await listen(loop.address, port));
      break;
    } catch (e) {
      if (e.code === 'EADDRINUSE' && attempt < 10 && port !== 0) { port += 1; continue; }
      throw e;
    }
  }
  state.port = servers[0].address().port;
  state.bound.push(loop);

  for (const b of cfg.bind) {
    if (b === loop) continue;
    try {
      servers.push(await listen(b.address, state.port));
      state.bound.push(b);
    } catch (e) {
      console.error(`[sidecar] could not listen on ${b.address}:${state.port}: ${e.message}`);
      state.failed.push({ ...b, error: e.code || e.message });
    }
  }

  console.error(`[sidecar] listening on port ${state.port}: ${state.bound.map(b => `${b.address} (${b.kind})`).join(', ')}`);
  emit({ ready: true, protocol: 1, pid: process.pid, port: state.port, bound: state.bound, failed: state.failed });

  const lan = state.bound.find(b => b.kind === 'lan');
  if (lan && cfg.advertise !== false) {
    const { advertise } = require('./src/discovery');
    advert = advertise({ name: cfg.host_name, id: cfg.host_id || 'unknown', port: state.port, ip: lan.address, protocol: 1 });
  }
}

// ---- never outlive the desktop app ----
if (!cfg.standalone) {
  process.stdin.on('end', () => shutdown('app closed our stdin'));
  process.stdin.on('error', () => shutdown('stdin error'));
  process.stdin.resume();
  if (cfg.parent_pid) {
    setInterval(() => {
      try { process.kill(cfg.parent_pid, 0); }
      catch (e) { if (e.code === 'ESRCH') shutdown(`PubHorror (pid ${cfg.parent_pid}) is gone`); }
    }, 2000);
  }
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', e => console.error('[sidecar] uncaught:', e && e.stack || e));

start().catch(e => {
  console.error('[sidecar] failed to start:', e.message);
  emit({ ready: false, error: e.code === 'EADDRINUSE' ? 'port in use' : e.message });
  process.exit(1);
});
