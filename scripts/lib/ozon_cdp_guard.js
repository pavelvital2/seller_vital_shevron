'use strict';

const path = require('path');
const { execFileSync } = require('child_process');

function normalizePath(value) {
  return path.resolve(String(value || ''));
}

function parseCdpUrl(cdpUrl) {
  let parsed;
  try {
    parsed = new URL(cdpUrl);
  } catch (error) {
    const err = new Error(`Invalid Ozon CDP URL: ${cdpUrl}`);
    err.code = 'OZON_CDP_CONTOUR_MISMATCH';
    throw err;
  }
  const port = Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80));
  return { host: parsed.hostname, port };
}

function listeningPidForPort(port) {
  const output = execFileSync('ss', ['-ltnp'], { encoding: 'utf8' });
  for (const line of output.split('\n')) {
    const columns = line.trim().split(/\s+/);
    const localAddress = columns[3] || '';
    if (!localAddress.endsWith(`:${port}`) && !localAddress.endsWith(`]:${port}`)) continue;
    const pidMatch = line.match(/pid=(\d+)/);
    if (pidMatch) return { pid: Number(pidMatch[1]), line: line.trim() };
  }
  return null;
}

function processCommand(pid) {
  return execFileSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8' }).trim();
}

function fail(message, details) {
  const error = new Error(message);
  error.code = 'OZON_CDP_CONTOUR_MISMATCH';
  error.details = details;
  throw error;
}

function assertOzonCdpContour(options) {
  const cdpUrl = options.cdpUrl;
  const expectedPort = Number(options.expectedPort);
  const expectedProfileDir = normalizePath(options.expectedProfileDir);
  const parsed = parseCdpUrl(cdpUrl);
  const allowedHosts = new Set(['127.0.0.1', 'localhost', '::1']);

  if (!allowedHosts.has(parsed.host)) {
    fail(`Ozon CDP contour mismatch: host ${parsed.host} is not local`, {
      cdpUrl,
      expectedPort,
      expectedProfileDir,
    });
  }

  if (parsed.port !== expectedPort) {
    fail(`Ozon CDP contour mismatch: port ${parsed.port} != expected ${expectedPort}`, {
      cdpUrl,
      expectedPort,
      expectedProfileDir,
    });
  }

  const listener = listeningPidForPort(expectedPort);
  if (!listener) {
    fail(`Ozon CDP contour mismatch: no listener on port ${expectedPort}`, {
      cdpUrl,
      expectedPort,
      expectedProfileDir,
    });
  }

  const command = processCommand(listener.pid);
  if (!command.includes(`--user-data-dir=${expectedProfileDir}`)) {
    fail(`Ozon CDP contour mismatch: port ${expectedPort} is not bound to expected Chrome profile`, {
      cdpUrl,
      expectedPort,
      expectedProfileDir,
      pid: listener.pid,
      listener: listener.line,
    });
  }

  return {
    ok: true,
    cdpUrl,
    expectedPort,
    expectedProfileDir,
    pid: listener.pid,
  };
}

module.exports = { assertOzonCdpContour };
