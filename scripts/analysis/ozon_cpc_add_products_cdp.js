#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const root = path.resolve(__dirname, '../..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const planPath = path.resolve(root, process.env.PLAN_PATH || '');
const outputPath = path.resolve(root, process.env.OUTPUT_PATH || '');
const campaignId = '20233460';

if (!process.env.CONFIRMED_BY_USER || process.env.CONFIRMED_BY_USER !== 'true') {
  throw new Error('explicit owner confirmation is required');
}
if (!process.env.PLAN_PATH || !process.env.OUTPUT_PATH) {
  throw new Error('PLAN_PATH and OUTPUT_PATH are required');
}

(async () => {
  const rows = JSON.parse(fs.readFileSync(planPath, 'utf8'));
  const targets = rows
    .filter((row) => row.cpc_action === 'add_to_campaign_review')
    .map((row) => ({ sku: String(row.ozon_sku) }));
  if (targets.length !== 27 || new Set(targets.map((row) => row.sku)).size !== 27) {
    throw new Error(`expected exactly 27 unique CPC add targets, got ${targets.length}`);
  }
  assertOzonCdpContour({
    cdpUrl,
    expectedPort: 9544,
    expectedProfileDir: path.join(root, '.sessions', 'ozon', 'chrome-profile'),
  });
  const browser = await chromium.connectOverCDP(cdpUrl);
  const context = browser.contexts()[0];
  const page = await context.newPage();
  await page.goto(`https://seller.ozon.ru/app/advertisement/product/cpc/${campaignId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await page.waitForTimeout(5000);
  const bodyText = await page.locator('body').innerText();
  if (!bodyText.includes('Vital Shevron') || !bodyText.includes(`ID ${campaignId}`)) {
    throw new Error('Ozon CPC contour or campaign identity mismatch');
  }
  const response = await page.evaluate(async ({ id, items }) => {
    const result = await fetch(
      `/performance-api/seller-api/adv-performance-facade/v2/campaign/${id}/product`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ items }),
      },
    );
    const text = await result.text();
    return { ok: result.ok, status: result.status, body: text.slice(0, 5000) };
  }, { id: campaignId, items: targets });
  const output = {
    applied_at: new Date().toISOString(),
    campaign_id: campaignId,
    submitted_count: targets.length,
    submitted_skus: targets.map((row) => row.sku),
    response,
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(output, null, 2));
  await page.close();
  await browser.close();
  if (!response.ok) throw new Error(`CPC add failed with HTTP ${response.status}`);
  process.stdout.write(`${JSON.stringify({ ok: true, submitted: targets.length, output_path: outputPath })}\n`);
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
