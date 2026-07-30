#!/usr/bin/env node
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const apiBase = 'https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1';
const pageUrl = 'https://seller.wildberries.ru/discount-and-prices/main-table';

const opts = {
  mode: '',
  plan: '',
  approvalSha: '',
  upload: '',
  outDir: '',
  profile: process.env.WB_BROWSER_PROFILE || defaultProfile,
};
const args = process.argv.slice(2);
for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--mode') opts.mode = args[++i];
  else if (arg === '--plan') opts.plan = path.resolve(args[++i]);
  else if (arg === '--approval-sha') opts.approvalSha = args[++i];
  else if (arg === '--upload') opts.upload = path.resolve(args[++i]);
  else if (arg === '--out-dir') opts.outDir = path.resolve(args[++i]);
  else if (arg === '--profile') opts.profile = path.resolve(args[++i]);
  else throw new Error(`Unknown argument: ${arg}`);
}
if (!['download', 'apply'].includes(opts.mode)) throw new Error('--mode download|apply is required');
if (!opts.outDir) throw new Error('--out-dir is required');
if (opts.mode === 'apply' && (!opts.plan || !opts.approvalSha || !opts.upload)) {
  throw new Error('--plan, --approval-sha and --upload are required for apply');
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function runPython(command, xlsx) {
  const executable = '/home/Codex/agent-tools/python/bin/python';
  const script = path.join(projectRoot, 'scripts', 'pricing', 'wb_price_grid.py');
  const result = spawnSync(executable, [
    script,
    command,
    '--plan', opts.plan,
    '--approval-sha', opts.approvalSha,
    '--xlsx', xlsx,
  ], {
    cwd: projectRoot,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    throw new Error(`${command} failed: ${(result.stdout || '')} ${(result.stderr || '')}`.trim());
  }
  return JSON.parse(result.stdout);
}

async function main() {
  fs.mkdirSync(opts.outDir, { recursive: true });
  if (opts.mode === 'apply' && sha256(opts.plan) !== opts.approvalSha) {
    throw new Error('Plan checksum mismatch before WB LK write');
  }
  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  }));
  const page = context.pages()[0] || await context.newPage();
  const result = {
    status: 'blocked',
    mode: opts.mode,
    started_at: new Date().toISOString(),
    source_xlsx: '',
    upload_sha256: opts.upload ? sha256(opts.upload) : '',
    baseline_validation: null,
    upload_response: null,
    verify: null,
    error: '',
  };
  try {
    let authHeaders = null;
    page.on('request', (request) => {
      if (authHeaders || !request.url().includes('/api/v1/list/goods/filter')) return;
      const headers = request.headers();
      authHeaders = {
        authorizev3: headers.authorizev3,
        'wb-seller-lk': headers['wb-seller-lk'],
        'root-version': headers['root-version'],
        referer: headers.referer,
      };
    });
    await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForTimeout(8000);
    const session = await page.evaluate(() => ({
      title: document.title,
      url: location.href,
      hasSeller: Boolean(localStorage.getItem('wb-eu-portal.seller-token')),
      hasPassport: Boolean(localStorage.getItem('wb-eu-passport-v2.access-token')),
    }));
    if (!session.hasSeller || !session.hasPassport || /login|passport/i.test(session.url)) {
      throw new Error(`WB LK authorization is not active: ${JSON.stringify(session)}`);
    }
    if (!authHeaders?.authorizev3 || !authHeaders?.['wb-seller-lk']) {
      throw new Error('Authorized WB request headers were not captured');
    }
    const downloadWorkbook = async () => {
      const generate = await page.request.post(
        `${apiBase}/template/min/price/autodiscounts`,
        {
          headers: { ...authHeaders, 'content-type': 'application/json' },
          data: {},
          timeout: 60000,
        },
      );
      if (!generate.ok()) {
        throw new Error(`Cannot generate WB minimum-price workbook: HTTP ${generate.status()}`);
      }
      await page.waitForTimeout(5000);
      const response = await page.request.get(
        `${apiBase}/template/min/price/autodiscounts/result`,
        {
          headers: authHeaders,
          timeout: 60000,
        },
      );
      const payload = await response.json();
      const base64 = payload?.data?.autoDiscountLockExcel;
      if (!response.ok() || !base64) {
        throw new Error(`Cannot download WB minimum-price workbook: HTTP ${response.status()}`);
      }
      const body = Buffer.from(base64, 'base64');
      if (body.slice(0, 2).toString() !== 'PK') {
        throw new Error('WB minimum-price workbook is not an XLSX archive');
      }
      return body;
    };

    const sourceBody = await downloadWorkbook();
    const sourcePath = path.join(opts.outDir, opts.mode === 'apply' ? 'fresh_before.xlsx' : 'fresh_source.xlsx');
    fs.writeFileSync(sourcePath, sourceBody);
    result.source_xlsx = sourcePath;
    if (opts.mode === 'download') {
      result.status = 'ok';
      return result;
    }

    result.baseline_validation = runPython('validate-min-baseline', sourcePath);
    const plan = JSON.parse(fs.readFileSync(opts.plan, 'utf8'));
    if (sha256(opts.upload) !== plan.minimum_upload_xlsx_sha256) {
      throw new Error('Minimum-price XLSX checksum differs from approved plan');
    }
    const uploadBase64 = fs.readFileSync(opts.upload).toString('base64');
    const uploadResponse = await page.request.post(
      `${apiBase}/upload/task/min/price/autodiscounts/excel`,
      {
        headers: { ...authHeaders, 'content-type': 'application/json' },
        data: { data: uploadBase64 },
        timeout: 90000,
      },
    );
    const uploadText = await uploadResponse.text();
    let uploadJson = null;
    try { uploadJson = uploadText ? JSON.parse(uploadText) : null; } catch { uploadJson = uploadText; }
    result.upload_response = {
      status: uploadResponse.status(),
      ok: uploadResponse.ok(),
      json: uploadJson,
    };
    if (!result.upload_response.ok || result.upload_response.json?.error) {
      throw new Error(`WB minimum-price upload rejected: ${JSON.stringify(result.upload_response)}`);
    }

    let verifyPath = '';
    for (const waitSeconds of [10, 15, 20, 30]) {
      await page.waitForTimeout(waitSeconds * 1000);
      let verifyBody;
      try { verifyBody = await downloadWorkbook(); } catch { continue; }
      verifyPath = path.join(opts.outDir, `fresh_after_${waitSeconds}s.xlsx`);
      fs.writeFileSync(verifyPath, verifyBody);
      try {
        result.verify = runPython('verify-min', verifyPath);
        result.status = 'ok';
        break;
      } catch (error) {
        result.verify = { status: 'pending', error: String(error.message || error) };
      }
    }
    if (result.status !== 'ok') {
      throw new Error('WB minimum prices were not fully verified before timeout');
    }
    return result;
  } catch (error) {
    result.status = 'blocked';
    result.error = String(error && error.message ? error.message : error).slice(0, 4000);
    process.exitCode = 1;
    return result;
  } finally {
    result.finished_at = new Date().toISOString();
    fs.writeFileSync(
      path.join(opts.outDir, `${opts.mode}_result.json`),
      `${JSON.stringify(result, null, 2)}\n`,
    );
    await context.close().catch(() => {});
  }
}

main().then((result) => {
  console.log(JSON.stringify(result, null, 2));
}).catch((error) => {
  console.error(error);
  process.exit(1);
});
