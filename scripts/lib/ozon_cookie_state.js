#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const OZON_DOMAIN_ALIASES = new Set([
  '.ozon.ru',
  'ozon.ru',
  '.seller.ozon.ru',
  'seller.ozon.ru',
]);

function normalizedDomain(value) {
  return String(value || '').trim().toLowerCase();
}

function partitionKey(cookie) {
  if (!cookie || typeof cookie.partitionKey === 'undefined') return '';
  return JSON.stringify(cookie.partitionKey);
}

function cookieIdentity(cookie, includeDomain = true) {
  const parts = [
    String(cookie?.name || ''),
    String(cookie?.path || '/'),
    partitionKey(cookie),
  ];
  if (includeDomain) parts.splice(1, 0, normalizedDomain(cookie?.domain));
  return parts.join('\t');
}

function preferAliasCookie(current, candidate) {
  if (!current) return candidate;
  const currentDomain = normalizedDomain(current.domain);
  const candidateDomain = normalizedDomain(candidate.domain);
  if (candidateDomain === '.ozon.ru' && currentDomain !== '.ozon.ru') return candidate;
  if (candidateDomain === currentDomain) return candidate;
  return current;
}

function normalizeOzonCookies(cookies) {
  const source = Array.isArray(cookies) ? cookies.filter(Boolean) : [];
  const exact = new Map();
  let exactDuplicates = 0;

  for (const cookie of source) {
    const key = cookieIdentity(cookie, true);
    if (exact.has(key)) exactDuplicates += 1;
    exact.set(key, { ...cookie });
  }

  const result = [];
  const aliasGroups = new Map();
  for (const cookie of exact.values()) {
    const domain = normalizedDomain(cookie.domain);
    if (!OZON_DOMAIN_ALIASES.has(domain)) {
      result.push(cookie);
      continue;
    }
    const key = cookieIdentity(cookie, false);
    aliasGroups.set(key, preferAliasCookie(aliasGroups.get(key), cookie));
  }
  result.push(...aliasGroups.values());

  const aliasInputCount = [...exact.values()]
    .filter((cookie) => OZON_DOMAIN_ALIASES.has(normalizedDomain(cookie.domain)))
    .length;
  const aliasDuplicates = Math.max(0, aliasInputCount - aliasGroups.size);
  const normalized = result.sort((left, right) => (
    cookieIdentity(left, true).localeCompare(cookieIdentity(right, true))
  ));

  return {
    cookies: normalized,
    stats: {
      inputCount: source.length,
      outputCount: normalized.length,
      removedCount: source.length - normalized.length,
      exactDuplicates,
      aliasDuplicates,
      normalized: source.length !== normalized.length,
      valuesPrinted: false,
    },
  };
}

function normalizeOzonStorageState(state) {
  const source = state && typeof state === 'object' ? state : {};
  const normalized = normalizeOzonCookies(source.cookies);
  return {
    state: {
      ...source,
      cookies: normalized.cookies,
      origins: Array.isArray(source.origins) ? source.origins : [],
    },
    stats: normalized.stats,
  };
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

function atomicWriteJson(targetPath, value, { backup = false } = {}) {
  const resolved = path.resolve(targetPath);
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  if (backup && fs.existsSync(resolved)) {
    const backupPath = `${resolved}.bak-${timestamp()}`;
    fs.copyFileSync(resolved, backupPath);
    fs.chmodSync(backupPath, 0o600);
  }
  const tempPath = `${resolved}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tempPath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.chmodSync(tempPath, 0o600);
  fs.renameSync(tempPath, resolved);
  fs.chmodSync(resolved, 0o600);
}

function readNormalizedStorageState(statePath, { repair = false, backup = true } = {}) {
  const source = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  const normalized = normalizeOzonStorageState(source);
  if (repair && normalized.stats.normalized) {
    atomicWriteJson(statePath, normalized.state, { backup });
  }
  return normalized;
}

async function exportNormalizedStorageState(context, statePath, { backup = false } = {}) {
  const source = await context.storageState();
  const normalized = normalizeOzonStorageState(source);
  atomicWriteJson(statePath, normalized.state, { backup });
  return normalized.stats;
}

module.exports = {
  OZON_DOMAIN_ALIASES,
  atomicWriteJson,
  exportNormalizedStorageState,
  normalizeOzonCookies,
  normalizeOzonStorageState,
  readNormalizedStorageState,
};
