'use strict';
// Reads the config file the PubHorror desktop app writes (data/share/sidecar.json).
// This file is the whole contract between Python and Node: bump PROTOCOL when it changes incompatibly.
const fs = require('fs');

const PROTOCOL = 1;

function ipv4Parts(ip) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(ip || ''));
  if (!m) return null;
  const p = m.slice(1).map(Number);
  return p.every(n => n <= 255) ? p : null;
}

// Only loopback, private LAN (RFC 1918) and Tailscale (100.64.0.0/10) addresses may ever be bound.
// Never 0.0.0.0, never a public address -- even if the config asks for it.
function classify(ip) {
  const p = ipv4Parts(ip);
  if (!p) return null;
  const [a, b] = p;
  if (a === 127) return 'loopback';
  if (a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)) return 'lan';
  if (a === 100 && b >= 64 && b <= 127) return 'tailscale';
  return null;
}

function loadConfig(file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (raw.protocol !== PROTOCOL) {
    throw new Error(`config protocol ${raw.protocol} not supported (this sidecar speaks ${PROTOCOL})`);
  }
  const need = (k, type) => {
    if (typeof raw[k] !== type || (type === 'string' && !raw[k])) throw new Error(`missing or invalid "${k}"`);
  };
  need('access_code', 'string');
  need('control_token', 'string');
  need('host_name', 'string');
  need('port', 'number');
  if (!Array.isArray(raw.bind) || raw.bind.length === 0) throw new Error('"bind" must list at least one address');

  const bind = [];
  for (const b of raw.bind) {
    const kind = classify(b && b.address);
    if (!kind) throw new Error(`refusing to bind non-private address ${JSON.stringify(b && b.address)}`);
    bind.push({ address: b.address, kind });
  }
  if (!bind.some(b => b.kind === 'loopback')) bind.unshift({ address: '127.0.0.1', kind: 'loopback' });

  return {
    protocol: PROTOCOL,
    access_code: raw.access_code,
    control_token: raw.control_token,
    host_name: raw.host_name.slice(0, 60),
    host_id: String(raw.host_id || ''),
    port: raw.port | 0,
    bind,
    extra_hostnames: (raw.extra_hostnames || []).filter(h => typeof h === 'string' && /^[a-z0-9.-]+$/i.test(h)),
    parent_pid: raw.parent_pid | 0,
    standalone: !!raw.standalone,
    advertise: raw.advertise !== false,
    // Paths for later milestones. Node only ever READS these.
    library_file: raw.library_file || null,
    posters_dir: raw.posters_dir || null,
    media_roots: Array.isArray(raw.media_roots) ? raw.media_roots : [],
    ffmpeg: raw.ffmpeg || null,
    ffprobe: raw.ffprobe || null,
    cache_dir: raw.cache_dir || null,
    party_client: raw.party_client || null,
    app_config: raw.app_config || null,     // the desktop app's config.json: read for the theme only
    assets_dir: raw.assets_dir || null,     // the desktop app's assets (OpenDyslexic font)
  };
}

module.exports = { loadConfig, classify, PROTOCOL };
