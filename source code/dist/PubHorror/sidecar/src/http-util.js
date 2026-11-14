'use strict';
const fs = require('fs');
const path = require('path');

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Cross-Origin-Resource-Policy': 'same-origin',
};

const PAGE_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
                 "img-src 'self' data: https:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";

function send(res, status, body, type = 'text/plain; charset=utf-8', headers = {}) {
  const data = Buffer.isBuffer(body) ? body : Buffer.from(String(body ?? ''));
  res.writeHead(status, { ...SECURITY_HEADERS, 'Content-Type': type, 'Content-Length': data.length,
                          'Cache-Control': 'no-store', ...headers });
  res.end(data);
}

function json(res, status, obj, headers = {}) {
  send(res, status, JSON.stringify(obj), 'application/json; charset=utf-8', headers);
}

function redirect(res, location, headers = {}) {
  res.writeHead(302, { ...SECURITY_HEADERS, Location: location, 'Content-Length': 0, 'Cache-Control': 'no-store', ...headers });
  res.end();
}

const pageCache = new Map();
function page(res, file, headers = {}) {
  let html = pageCache.get(file);
  if (!html) {
    html = fs.readFileSync(file);
    pageCache.set(file, html);
  }
  send(res, 200, html, 'text/html; charset=utf-8', { 'Content-Security-Policy': PAGE_CSP, ...headers });
}

function readJsonBody(req, limit = 4096) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', c => {
      size += c.length;
      if (size > limit) { reject(Object.assign(new Error('body too large'), { status: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}); }
      catch { reject(Object.assign(new Error('invalid JSON'), { status: 400 })); }
    });
    req.on('error', reject);
  });
}

function publicFile(name) {
  return path.join(__dirname, '..', 'public', name);
}

module.exports = { send, json, redirect, page, readJsonBody, publicFile, SECURITY_HEADERS };
