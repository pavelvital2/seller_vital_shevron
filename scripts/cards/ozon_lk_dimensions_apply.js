#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const projectRoot = path.resolve(__dirname, '../..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const expectedProfileDir = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');

function writeRunManifest(runDir, summaryPath) {
  const python = process.env.SELLER_AGENT_PYTHON || '/home/Codex/agent-tools/python/bin/python';
  const sourceRoot = path.join(projectRoot, 'src');
  const code = [
    'import json, sys',
    'from pathlib import Path',
    'from seller_agent.core.run_manifest import write_summary_run_manifest',
    'project_root, run_dir, summary_path = map(Path, sys.argv[1:4])',
    'summary = json.loads(summary_path.read_text(encoding="utf-8"))',
    'mode = summary.get("mode", "apply")',
    'result = write_summary_run_manifest(',
    '  data_dir=project_root / "data", run_dir=run_dir, summary=summary,',
    '  task="ozon-lk-item-update-plan" if mode == "dry_run" else "ozon-lk-item-update-apply",',
    '  mode=mode, risk="high",',
    '  marketplaces=["ozon"],',
    '  inputs={"internal_sku": summary["internal_sku"], "product_id": summary["product_id"], "before": summary["before"], "target": summary["target"]},',
    '  approved_id=summary.get("approved_id"),',
    '  pending_id=summary.get("run_id") if mode == "dry_run" else None,',
    '  lifecycle_status="pending_review" if mode == "dry_run" else "applied", closed=False,',
    ')',
    'print(json.dumps(result, ensure_ascii=False))',
  ].join('\n');
  const result = spawnSync(
    python,
    ['-c', code, projectRoot, runDir, summaryPath],
    {
      encoding: 'utf8',
      env: { ...process.env, PYTHONPATH: sourceRoot },
    },
  );
  if (result.status !== 0) {
    return {
      ok: false,
      error: String(result.stderr || result.stdout || 'run manifest write failed').slice(0, 2000),
    };
  }
  return { ok: true, result: JSON.parse(result.stdout) };
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    if (key === 'confirmed-by-user' || key === 'dry-run') {
      values[key] = true;
      continue;
    }
    values[key] = argv[index + 1];
    index += 1;
  }
  return values;
}

function required(args, key) {
  const value = String(args[key] || '').trim();
  if (!value) throw new Error(`Missing --${key}`);
  return value;
}

function safeRequest(payload) {
  return {
    id: payload.id,
    offer_id: payload.offer_id,
    name: payload.name,
    depth: payload.depth,
    width: payload.width,
    height: payload.height,
    weight: payload.weight,
    price: payload.price,
    old_price: payload.old_price,
    primary_image: payload.primary_image,
    images: payload.images,
    images_count: Array.isArray(payload.images) ? payload.images.length : 0,
    barcodes: payload.barcodes,
  };
}

function validateRequest(request, target, guards) {
  for (const [name, expected] of Object.entries(target)) {
    if (String(request[name]) !== expected) {
      throw new Error(`UNEXPECTED_APPLY_PAYLOAD ${name}: ${request[name]} != ${expected}`);
    }
  }
  if (String(request.offer_id) !== guards.internalSku) {
    throw new Error(`UNEXPECTED_APPLY_PAYLOAD offer_id: ${request.offer_id} != ${guards.internalSku}`);
  }
  if (Number(request.price) !== Number(guards.expectedPrice)) {
    throw new Error(`UNEXPECTED_APPLY_PAYLOAD price: ${request.price} != ${guards.expectedPrice}`);
  }
  if (Number(request.old_price) !== Number(guards.expectedOldPrice)) {
    throw new Error(`UNEXPECTED_APPLY_PAYLOAD old_price: ${request.old_price} != ${guards.expectedOldPrice}`);
  }
  if (!Array.isArray(request.barcodes) || !request.barcodes.includes(guards.expectedBarcode)) {
    throw new Error(`UNEXPECTED_APPLY_PAYLOAD barcode: ${request.barcodes} lacks ${guards.expectedBarcode}`);
  }
  const requestPrimaryImage = request.primary_image
    || (request.images && request.images[0] && (request.images[0].url || request.images[0]));
  if (String(requestPrimaryImage) !== guards.expectedPrimaryImage) {
    throw new Error(
      `UNEXPECTED_APPLY_PAYLOAD primary_image: ${requestPrimaryImage} != ${guards.expectedPrimaryImage}`,
    );
  }
  const expectedRequestImagesCount = request.primary_image
    ? Number(guards.expectedImagesCount)
    : Number(guards.expectedImagesCount) + 1;
  if (Number(request.images_count) !== expectedRequestImagesCount) {
    throw new Error(
      `UNEXPECTED_APPLY_PAYLOAD images_count: ${request.images_count} != ${expectedRequestImagesCount}`,
    );
  }
}

async function replaceInput(page, name, value) {
  const locator = page.locator(`input[name="${name}"]`);
  await locator.click();
  await locator.press('Control+A');
  await locator.press('Backspace');
  await locator.type(value);
  await locator.dispatchEvent('change');
  await locator.press('Tab');
  await page.waitForTimeout(500);
  const actual = await locator.inputValue();
  if (actual !== value) {
    throw new Error(`INPUT_REPLACE_FAILED ${name}: ${actual} != ${value}`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const dryRun = Boolean(args['dry-run']);
  if (!dryRun && !args['confirmed-by-user']) {
    throw new Error('Apply requires --confirmed-by-user');
  }
  const productId = required(args, 'product-id');
  const internalSku = required(args, 'internal-sku');
  const expectedName = required(args, 'expected-name');
  const targetName = String(args['target-name'] || expectedName).trim();
  const expectedPrice = required(args, 'expected-price');
  const expectedOldPrice = required(args, 'expected-old-price');
  const expectedBarcode = required(args, 'expected-barcode');
  const expectedPrimaryImage = required(args, 'expected-primary-image');
  const expectedImagesCount = required(args, 'expected-images-count');
  const runId = required(args, 'run-id');
  const before = {
    depth: required(args, 'before-depth'),
    width: required(args, 'before-width'),
    height: required(args, 'before-height'),
    weight: required(args, 'before-weight'),
  };
  const target = {
    name: targetName,
    depth: required(args, 'target-depth'),
    width: required(args, 'target-width'),
    height: required(args, 'target-height'),
    weight: required(args, 'target-weight'),
  };

  const startedAt = new Date();
  const day = startedAt.toISOString().slice(0, 10);
  const runDir = path.join(projectRoot, 'data', 'runs', day, runId);
  fs.mkdirSync(runDir, { recursive: true });
  assertOzonCdpContour({
    cdpUrl,
    expectedPort: 9544,
    expectedProfileDir,
  });

  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 10000 });
  const context = browser.contexts()[0];
  if (!context) throw new Error('No browser context found in Ozon CDP session');
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  let requestPayload = null;
  let request = null;
  let requestGuardError = null;
  let responseInfo = null;

  try {
    await page.goto(`https://seller.ozon.ru/app/products/${productId}/edit/general-info`, {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });
    await page.waitForTimeout(10000);

    const baseline = {
      name: expectedName,
      offerId: internalSku,
      ...before,
    };
    for (const [name, expected] of Object.entries(baseline)) {
      const actual = await page.locator(`input[name="${name}"]`).inputValue();
      if (actual !== expected) {
        throw new Error(`DRIFT ${name}: ${actual} != ${expected}`);
      }
    }

    if (targetName !== expectedName) {
      await replaceInput(page, 'name', targetName);
    }
    for (const [name, value] of Object.entries(target)) {
      if (name === 'name') continue;
      if (value !== before[name]) {
        await replaceInput(page, name, value);
      }
    }
    await page.getByText('Габариты и вес', { exact: true }).click();
    await page.waitForTimeout(1000);

    await page.route('**/api/v1/item/update', async (route) => {
      try {
        requestPayload = JSON.parse(route.request().postData() || '{}');
        request = safeRequest(requestPayload);
        validateRequest(request, target, {
          internalSku,
          expectedPrice,
          expectedOldPrice,
          expectedBarcode,
          expectedPrimaryImage,
          expectedImagesCount,
        });
        if (dryRun) {
          await route.abort('blockedbyclient');
        } else {
          await route.continue();
        }
      } catch (error) {
        requestGuardError = error;
        await route.abort('blockedbyclient').catch(() => null);
      }
    });
    page.on('response', async (response) => {
      if (!response.url().includes('/api/v1/item/update')) return;
      let body = '';
      try {
        body = (await response.text()).slice(0, 10000);
      } catch {
        body = '';
      }
      responseInfo = {
        status: response.status(),
        ok: response.ok(),
        url: 'https://seller.ozon.ru/api/v1/item/update',
        body,
      };
    });

    await page.getByRole('button', { name: 'Отправить на модерацию' }).click();
    await page.waitForTimeout(dryRun ? 3000 : 12000);
    if (!requestPayload) throw new Error('NO_ITEM_UPDATE_REQUEST');
    if (requestGuardError) throw requestGuardError;

    fs.writeFileSync(path.join(runDir, 'request_safe.json'), JSON.stringify(request, null, 2));
    if (dryRun) {
      const screenshotPath = path.join(runDir, 'dry_run_payload_blocked.png');
      await page.screenshot({ path: screenshotPath, fullPage: false });
      const summary = {
        run_id: runId,
        started_at: startedAt.toISOString(),
        finished_at: new Date().toISOString(),
        mode: 'dry_run',
        overall_status: 'ok',
        internal_sku: internalSku,
        product_id: productId,
        before: {
          name: expectedName,
          ...before,
          price: expectedPrice,
          old_price: expectedOldPrice,
          barcode: expectedBarcode,
          primary_image: expectedPrimaryImage,
          images_count: Number(expectedImagesCount),
        },
        target,
        approved_id: null,
        request,
        request_disposition: 'validated_and_aborted_before_network',
        artifacts: {
          run_dir: path.relative(projectRoot, runDir),
          request: path.relative(projectRoot, path.join(runDir, 'request_safe.json')),
          screenshot: path.relative(projectRoot, screenshotPath),
        },
      };
      const summaryPath = path.join(runDir, 'summary.json');
      fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
      const manifest = writeRunManifest(runDir, summaryPath);
      if (manifest.ok) {
        summary.artifacts.run_manifest = path.relative(projectRoot, manifest.result.manifest);
      } else {
        summary.manifest_warning = manifest.error;
      }
      fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
      console.log(JSON.stringify(summary, null, 2));
      return;
    }

    fs.writeFileSync(path.join(runDir, 'response_safe.json'), JSON.stringify(responseInfo, null, 2));
    await page.screenshot({
      path: path.join(runDir, 'after_submit.png'),
      fullPage: false,
    });

    const summary = {
      run_id: runId,
      started_at: startedAt.toISOString(),
      finished_at: new Date().toISOString(),
      mode: 'apply',
      overall_status: responseInfo && responseInfo.ok ? 'ok' : 'warning',
      internal_sku: internalSku,
      product_id: productId,
      before: {
        name: expectedName,
        ...before,
        price: expectedPrice,
        old_price: expectedOldPrice,
        barcode: expectedBarcode,
        primary_image: expectedPrimaryImage,
        images_count: Number(expectedImagesCount),
      },
      target,
      approved_id: String(args['approved-id'] || '').trim() || null,
      request,
      response: responseInfo,
      artifacts: {
        run_dir: path.relative(projectRoot, runDir),
        request: path.relative(projectRoot, path.join(runDir, 'request_safe.json')),
        response: path.relative(projectRoot, path.join(runDir, 'response_safe.json')),
        screenshot: path.relative(projectRoot, path.join(runDir, 'after_submit.png')),
      },
    };
    const summaryPath = path.join(runDir, 'summary.json');
    fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
    const manifest = writeRunManifest(runDir, summaryPath);
    if (manifest.ok) {
      summary.artifacts.run_manifest = path.relative(projectRoot, manifest.result.manifest);
    } else {
      summary.manifest_warning = manifest.error;
    }
    fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
    if (!responseInfo || !responseInfo.ok) process.exitCode = 2;
  } finally {
    await page.close().catch(() => null);
  }
}

main()
  .then(() => process.exit(process.exitCode || 0))
  .catch((error) => {
    console.error(
      JSON.stringify({
        error: error.message || String(error),
        stack: String(error.stack || '').split('\n').slice(0, 8),
      }),
    );
    process.exit(1);
  });
