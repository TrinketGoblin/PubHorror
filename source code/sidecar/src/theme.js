'use strict';
// The phone page wears the same theme as the desktop app.
// We READ the app's config.json (never write it) and turn theme / custom colours / text size into CSS variables
// with the same names the desktop uses (--bg-main, --accent, --highlight, ...). Re-read at most every 2 s, so
// changing the theme on the PC shows up on phones on their next page load.
const fs = require('fs');

const PRESETS = {
  horror:   { bg: '#050000', grad: '#1a0000', input: '#1a0000', text: '#d2d2d2', accent: '#8b0000', hi: '#ff4d4d', border: '#3a0000', glow: '#ff0000' },
  midnight: { bg: '#000205', grad: '#000c1a', input: '#000c1a', text: '#d2e1ff', accent: '#00338b', hi: '#4d94ff', border: '#00223a', glow: '#0080ff' },
  forest:   { bg: '#000501', grad: '#001a05', input: '#001a05', text: '#d2ffd6', accent: '#008b2a', hi: '#4dff77', border: '#003a0e', glow: '#00ff40' },
  classic:  { bg: '#050505', grad: '#1a1a1a', input: '#1a1a1a', text: '#e0e0e0', accent: '#444444', hi: '#ffffff', border: '#333333', glow: '#888888' },
};

const HEX = /^#[0-9a-f]{6}$/i;
function rgb(hex) { return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)); }
function lighten(hex, f) { return 'rgb(' + rgb(hex).map(c => Math.min(255, Math.round(c + (255 - c) * f))).join(',') + ')'; }

// Same maths as applyCustomTheme() in the desktop app.
function customPalette(accent, hi, bg) {
  return { bg, grad: lighten(bg, 0.15), input: lighten(bg, 0.12), text: lighten(hi, 0.6),
           accent, hi, border: lighten(accent, 0.15), glow: hi };
}

class Theme {
  constructor(configFile) {
    this.file = configFile;
    this.cache = null;
    this.at = 0;
  }

  read() {
    let cfg = {};
    if (this.file) {
      try { cfg = JSON.parse(fs.readFileSync(this.file, 'utf8')) || {}; } catch { cfg = {}; }   // read-only, full sharing
    }
    let name = String(cfg.theme || 'horror');
    let p = PRESETS[name];
    if (name === 'custom' && HEX.test(cfg.custom_accent || '') && HEX.test(cfg.custom_highlight || '') && HEX.test(cfg.custom_bg || '')) {
      p = customPalette(cfg.custom_accent, cfg.custom_highlight, cfg.custom_bg);
    }
    if (!p) { name = 'horror'; p = PRESETS.horror; }
    const size = Math.max(12, Math.min(24, Number(cfg.font_size) || 16));
    return { name, p, size };
  }

  css() {
    if (this.cache && Date.now() - this.at < 2000) return this.cache;
    const { p, size } = this.read();
    // phones read a touch bigger than the PC setting, but never tiny
    const phoneSize = Math.max(15, Math.min(22, size));
    this.cache = `:root{--bg-main:${p.bg};--bg-grad:radial-gradient(circle, ${p.grad} 0%, ${p.bg} 100%);` +
      `--bg-input:${p.input};--text-main:${p.text};--accent:${p.accent};--highlight:${p.hi};--border:${p.border};` +
      `--glow:${p.glow};--base-font-size:${phoneSize}px;}\n`;
    this.at = Date.now();
    return this.cache;
  }

  themeColor() { return this.read().p.bg; }
}

module.exports = { Theme, PRESETS };
