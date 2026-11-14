'use strict';
// Range streaming + "will this play on a phone?" checks + one-at-a-time FFmpeg conversion.
//
// Phone browsers (iOS Safari, Android Chrome) all reliably play exactly one thing:
//   MP4/M4V/MOV container + H.264 (8-bit 4:2:0) video + AAC or MP3 audio.
// Anything else is converted ONCE into cache_dir and reused:
//   remux     = H.264 video copied as-is, only the container (and non-AAC audio) changes. Fast.
//   transcode = video re-encoded to H.264. Slow and CPU heavy, but done once.
// The original files are only ever read. FFmpeg writes nowhere except cache_dir.
const fs = require('fs');
const fsp = fs.promises;
const path = require('path');
const crypto = require('crypto');
const { spawn, execFile } = require('child_process');
const { SECURITY_HEADERS, json } = require('./http-util');

const CTYPES = {
  '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
  '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo', '.ts': 'video/mp2t', '.m2ts': 'video/mp2t',
  '.mpg': 'video/mpeg', '.mpeg': 'video/mpeg', '.wmv': 'video/x-ms-wmv',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
  '.gif': 'image/gif', '.bmp': 'image/bmp',
};

const CODEC_NAMES = {
  mpeg2video: 'MPEG-2 video (DVD)', mpeg1video: 'MPEG-1 video', hevc: 'HEVC/H.265 video', vc1: 'VC-1 video (Blu-ray)',
  mpeg4: 'MPEG-4/DivX/Xvid video', av1: 'AV1 video', vp9: 'VP9 video', vp8: 'VP8 video', wmv3: 'WMV video',
  ac3: 'Dolby Digital (AC3) audio', eac3: 'Dolby Digital Plus (E-AC3) audio', dts: 'DTS audio', truehd: 'Dolby TrueHD audio',
  flac: 'FLAC audio', opus: 'Opus audio', vorbis: 'Vorbis audio', mp2: 'MP2 audio', pcm_s16le: 'PCM audio',
  pcm_s24le: 'PCM audio', pcm_bluray: 'PCM audio', pcm_dvd: 'PCM audio', wmav2: 'WMA audio',
};

// ------------------------------------------------------------------ Range serving
async function serveFile(req, res, file, { cache = 'private, max-age=0', corp } = {}) {
  let st;
  try { st = await fsp.stat(file); } catch { return json(res, 404, { error: 'file not found on the host' }); }
  if (!st.isFile()) return json(res, 404, { error: 'not a file' });

  const size = st.size;
  let start = 0, end = size - 1, status = 200;
  const m = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range || '').split(',')[0].trim());
  if (m && (m[1] !== '' || m[2] !== '')) {
    if (m[1] === '') { start = Math.max(0, size - Number(m[2])); }
    else { start = Number(m[1]); if (m[2] !== '') end = Math.min(Number(m[2]), size - 1); }
    if (start > end || start >= size) {
      res.writeHead(416, { ...SECURITY_HEADERS, 'Content-Range': `bytes */${size}`, 'Content-Length': 0 });
      return res.end();
    }
    status = 206;
  }
  const headers = {
    ...SECURITY_HEADERS,
    'Content-Type': CTYPES[path.extname(file).toLowerCase()] || 'application/octet-stream',
    'Content-Length': end - start + 1,
    'Accept-Ranges': 'bytes',
    'Last-Modified': st.mtime.toUTCString(),
    'Cache-Control': cache,
  };
  if (corp) headers['Cross-Origin-Resource-Policy'] = corp;
  if (status === 206) headers['Content-Range'] = `bytes ${start}-${end}/${size}`;
  res.writeHead(status, headers);
  if (req.method === 'HEAD' || size === 0) return res.end();

  // createReadStream opens with full sharing on Windows: never blocks the app from saving/moving files.
  const s = fs.createReadStream(file, { start, end, highWaterMark: 512 * 1024 });
  s.on('error', () => res.destroy());
  res.on('close', () => s.destroy());
  s.pipe(res);
}

// ------------------------------------------------------------------ media checks + conversion
class Media {
  constructor(cfg) {
    this.ffmpeg = cfg.ffmpeg && fs.existsSync(cfg.ffmpeg) ? cfg.ffmpeg : null;
    this.ffprobe = cfg.ffprobe && fs.existsSync(cfg.ffprobe) ? cfg.ffprobe : null;
    this.cacheDir = cfg.cache_dir;
    this.probes = new Map();    // key -> info
    this.jobs = new Map();      // key -> { state, percent, mode, error, src }
    this.queue = [];
    this.current = null;        // { key, proc, tmp }
    if (this.cacheDir) {
      fs.mkdirSync(this.cacheDir, { recursive: true });
      for (const f of fs.readdirSync(this.cacheDir)) {      // leftovers from an interrupted conversion
        if (f.endsWith('.part')) fs.rm(path.join(this.cacheDir, f), { force: true }, () => {});
      }
    }
  }

  async _key(file) {
    const st = await fsp.stat(file);
    return { st, key: crypto.createHash('sha1').update(`${file}|${st.size}|${st.mtimeMs}`).digest('hex').slice(0, 24) };
  }

  _probe(file) {
    return new Promise(resolve => {
      if (!this.ffprobe) return resolve(null);
      execFile(this.ffprobe, ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file],
        { windowsHide: true, timeout: 30000, maxBuffer: 16 * 1024 * 1024 }, (err, stdout) => {
          if (err) return resolve(null);
          try {
            const j = JSON.parse(stdout);
            const streams = j.streams || [];
            const v = streams.find(s => s.codec_type === 'video' && !(s.disposition && s.disposition.attached_pic));
            const auds = streams.filter(s => s.codec_type === 'audio');
            const a = auds.find(s => s.disposition && s.disposition.default) || auds[0];
            resolve({
              format: String((j.format && j.format.format_name) || ''),
              duration: Number(j.format && j.format.duration) || 0,
              video: v ? { codec: v.codec_name, pix_fmt: v.pix_fmt, width: v.width, height: v.height,
                           field_order: v.field_order || 'progressive' } : null,
              audio: a ? { codec: a.codec_name, channels: a.channels, index: auds.indexOf(a) } : null,
            });
          } catch { resolve(null); }
        });
    });
  }

  // Decide how this file can reach the client.
  //   phone:   MP4/M4V/MOV + 8-bit H.264 + AAC/MP3 only.
  //   desktop: another PubHorror app (WebView2/Chromium) also plays MKV/WebM, VP8/VP9/AV1, Opus/Vorbis/FLAC.
  decide(file, info, profile = 'phone') {
    const ext = path.extname(file).toLowerCase();
    const desktop = profile === 'desktop';
    const mp4Like = (desktop ? ['.mp4', '.m4v', '.mov', '.mkv', '.webm'] : ['.mp4', '.m4v', '.mov']).includes(ext);
    if (!info) {
      // No ffprobe: trust MP4-family files, otherwise we can't convert safely without knowing what's inside.
      return mp4Like ? { mode: 'direct', reasons: [] }
                     : { mode: this.ffmpeg ? 'transcode' : 'direct', reasons: ['Unknown format'] };
    }
    const v = info.video, a = info.audio, reasons = [];
    const h264Ok = v && v.codec === 'h264' && (!v.pix_fmt || /^yuvj?420p$/.test(v.pix_fmt));
    const vOk = !v || h264Ok || (desktop && ['vp8', 'vp9', 'av1'].includes(v.codec));
    const aOk = !a || ['aac', 'mp3'].includes(a.codec) || (desktop && ['opus', 'vorbis', 'flac'].includes(a.codec));
    if (!mp4Like) reasons.push(`${ext.slice(1).toUpperCase()} container`);
    if (v && !vOk) reasons.push(v.codec === 'h264' ? '10-bit or 4:4:4 H.264 video' : (CODEC_NAMES[v.codec] || `${v.codec} video`));
    if (a && !aOk) reasons.push(CODEC_NAMES[a.codec] || `${a.codec} audio`);
    if (mp4Like && vOk && aOk) return { mode: 'direct', reasons };
    return { mode: h264Ok || !v ? 'remux' : 'transcode', reasons };
  }

  // Status for one file; never starts work.
  async status(file, profile = 'phone') {
    let k;
    try { k = await this._key(file); } catch { return { state: 'missing', mode: null, reasons: ['File not found on the host'] }; }
    let info = this.probes.get(k.key);
    if (info === undefined) { info = await this._probe(file); this.probes.set(k.key, info); }
    const d = this.decide(file, info, profile);
    const base = { mode: d.mode, reasons: d.reasons, duration: info ? info.duration : null };
    if (d.mode === 'direct') return { ...base, state: 'ready', key: k.key, file };
    if (!this.cacheDir) return { ...base, state: 'unsupported', key: k.key, error: 'Conversion is not set up on the host.' };
    const out = path.join(this.cacheDir, k.key + '.mp4');
    if (fs.existsSync(out)) return { ...base, state: 'ready', key: k.key, file: out, converted: true };
    if (!this.ffmpeg) return { ...base, state: 'unsupported', key: k.key, error: 'FFmpeg is missing on the host.' };
    const job = this.jobs.get(k.key);
    if (job) return { ...base, state: job.state, percent: job.percent, error: job.error, key: k.key,
                      position: job.state === 'queued' ? this.queue.indexOf(k.key) + 1 : 0 };
    return { ...base, state: 'needs_prepare', key: k.key };
  }

  async prepare(file, profile = 'phone') {
    const s = await this.status(file, profile);
    if (s.state !== 'needs_prepare' && s.state !== 'error') return s;
    const info = this.probes.get(s.key);
    const stat = await fsp.stat(file);
    try {
      const fsInfo = await fsp.statfs(this.cacheDir);
      const free = fsInfo.bavail * fsInfo.bsize;
      const need = stat.size * 1.1 + 300e6;
      if (free < need) {
        return { ...s, state: 'error', error: `Not enough free disk space on the host (needs ~${(need / 1e9).toFixed(1)} GB).` };
      }
    } catch { /* statfs unsupported: carry on */ }
    this.jobs.set(s.key, { state: 'queued', percent: 0, mode: s.mode, error: null, file, info });
    this.queue.push(s.key);
    this._next();
    return this.status(file, profile);
  }

  _args(file, out, mode, info) {
    const v = info && info.video, a = info && info.audio;
    const args = ['-hide_banner', '-nostdin', '-y', '-i', file, '-map', '0:v:0?',
                  '-map', a ? `0:a:${a.index}?` : '0:a:0?', '-sn', '-dn', '-map_chapters', '-1'];
    if (mode === 'remux') {
      args.push('-c:v', 'copy', '-tag:v', 'avc1');
    } else {
      const filters = [];
      if (v && v.field_order && v.field_order !== 'progressive' && v.field_order !== 'unknown') filters.push('yadif');
      // square pixels (DVDs are anamorphic) and at most 1920 wide, even dimensions
      filters.push("scale='trunc(min(1920\\,iw*sar)/2)*2':'trunc(ih*min(1920\\,iw*sar)/(iw*sar)/2)*2'", 'setsar=1');
      args.push('-vf', filters.join(','), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
                '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-level:v', '4.1');
    }
    if (a && (a.codec === 'aac' || a.codec === 'mp3')) args.push('-c:a', 'copy');
    else args.push('-c:a', 'aac', '-b:a', '192k', '-ac', '2');
    args.push('-movflags', '+faststart', '-f', 'mp4', '-progress', 'pipe:1', '-nostats', out);
    return args;
  }

  _next() {
    if (this.current || this.queue.length === 0) return;
    const key = this.queue.shift();
    const job = this.jobs.get(key);
    if (!job) return this._next();
    const out = path.join(this.cacheDir, key + '.mp4');
    const tmp = out + '.part';
    job.state = 'converting';
    const dur = (job.info && job.info.duration) || 0;
    console.error(`[sidecar] converting (${job.mode}) ${path.basename(job.file)}`);
    const proc = spawn(this.ffmpeg, this._args(job.file, tmp, job.mode, job.info),
                       { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    this.current = { key, proc, tmp };
    let errTail = '';
    proc.stderr.on('data', d => { errTail = (errTail + d).slice(-3000); });
    let buf = '';
    proc.stdout.on('data', d => {
      buf += d;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        const m = /^out_time_(?:us|ms)=(\d+)/.exec(line);
        if (m && dur > 0) job.percent = Math.min(99, Math.round(Number(m[1]) / 1e6 / dur * 100));
      }
    });
    proc.on('close', code => {
      this.current = null;
      let ok = code === 0;
      try { ok = ok && fs.statSync(tmp).size > 0; } catch { ok = false; }
      if (ok) {
        try { fs.renameSync(tmp, out); } catch (e) { ok = false; errTail = e.message; }
      }
      if (ok) {
        this.jobs.delete(key);
        console.error(`[sidecar] converted ${path.basename(job.file)}`);
      } else {
        fs.rm(tmp, { force: true }, () => {});
        job.state = 'error';
        job.error = /No space left/i.test(errTail) ? 'The host ran out of disk space.' : 'Conversion failed on the host.';
        console.error(`[sidecar] conversion failed for ${path.basename(job.file)} (exit ${code}):\n${errTail}`);
      }
      this._next();
    });
    proc.on('error', e => { errTail = e.message; });
  }

  stop() {
    this.queue = [];
    if (this.current) {
      try { this.current.proc.kill(); } catch { /* ignore */ }
      fs.rm(this.current.tmp, { force: true }, () => {});
    }
  }
}

module.exports = { serveFile, Media };
