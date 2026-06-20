#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const projectRoot = path.resolve(__dirname, '..', '..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const expectedStore = process.env.OZON_EXPECTED_STORE || 'Vital Shevron';
const expectedCdpPort = 9544;
const expectedProfileDir = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const companyType = 'seller';

function parseArgs(argv) {
  const args = {
    runDir: '',
    itemsJson: '',
    reviewUuids: [],
    limit: 20,
    downloadMedia: true,
  };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === '--run-dir') {
      args.runDir = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--items-json') {
      args.itemsJson = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--review-uuid') {
      args.reviewUuids.push(argv[i + 1]);
      i += 1;
    } else if (argv[i] === '--limit') {
      args.limit = Number(argv[i + 1] || 20);
      i += 1;
    } else if (argv[i] === '--no-download') {
      args.downloadMedia = false;
    }
  }
  if (!args.runDir) throw new Error('Missing required --run-dir');
  if (!args.itemsJson) {
    args.itemsJson = path.join(args.runDir, 'processed', 'reviews_questions_items.json');
  }
  return args;
}

function assertInsideProject(filePath) {
  const resolved = path.resolve(filePath);
  const root = path.resolve(projectRoot);
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Refusing to access outside project: ${resolved}`);
  }
  return resolved;
}

function ensureDir(dirPath) {
  fs.mkdirSync(assertInsideProject(dirPath), { recursive: true });
}

function writeJson(filePath, value) {
  const target = assertInsideProject(filePath);
  ensureDir(path.dirname(target));
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const text = fs.readFileSync(filePath, 'utf8');
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    const key = match[1];
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

function readFirstLine(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return '';
  return fs.readFileSync(filePath, 'utf8').split(/\r?\n/).map((line) => line.trim()).find(Boolean) || '';
}

function firstEnv(...names) {
  for (const name of names) {
    if (process.env[name]) return process.env[name];
  }
  return '';
}

function loadCompanyId() {
  loadEnvFile(path.join(projectRoot, '.env'));
  loadEnvFile(path.join(projectRoot, '.sessions', 'ozon', 'ozon_api_credentials.env'));
  if (process.env.OZON_REVIEW_COMPANY_ID) return process.env.OZON_REVIEW_COMPANY_ID;
  if (process.env.OZON_SELLER_CLIENT_ID) return process.env.OZON_SELLER_CLIENT_ID;
  return readFirstLine(firstEnv(
    'VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE',
    'SELLER_OZON_SELLER_CREDENTIALS_FILE',
  ));
}

function pick(obj, ...names) {
  for (const name of names) {
    if (obj && obj[name] !== undefined && obj[name] !== null) return obj[name];
  }
  return '';
}

async function post(page, url, body, pageType, companyId) {
  return page.evaluate(async ({ url, body, companyId, pageType }) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        accept: 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'accept-language': 'ru',
        'x-o3-app-name': 'seller-ui',
        'x-o3-language': 'ru',
        'x-o3-company-id': companyId,
        'x-o3-page-type': pageType,
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    let json = null;
    let jsonParseOk = true;
    try {
      json = text ? JSON.parse(text) : {};
    } catch {
      jsonParseOk = false;
    }
    return { ok: response.ok, status: response.status, json, jsonParseOk };
  }, { url, body, companyId, pageType });
}

async function summarizePage(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 3000),
    webdriver: navigator.webdriver,
  }));
}

function classifyPage(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expectedRegex = new RegExp(String(expectedStore).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  return {
    expectedStoreFound: expectedRegex.test(all),
    blocked: /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied/i.test(all) || (rrMode && !sellerShellOpen),
    needsLogin: /registration\/signin|sso\.ozon|вход и регистрация|войти по почте|войти по номеру|введите код/i.test(all),
  };
}

async function verifyCabinet(page) {
  await page.goto('https://seller.ozon.ru/app/dashboard/main', {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await page.waitForTimeout(3000);
  const summary = await summarizePage(page);
  return { summary, status: classifyPage(summary) };
}

function readMediaReviewUuids(itemsJson, explicitUuids, limit) {
  const result = new Map();
  for (const uuid of explicitUuids) {
    if (uuid) result.set(uuid, { source_id: uuid, explicit: true });
  }
  if (!fs.existsSync(itemsJson)) return [...result.values()].slice(0, limit);
  const items = JSON.parse(fs.readFileSync(itemsJson, 'utf8'));
  const rows = Array.isArray(items) ? items : Array.isArray(items.items) ? items.items : [];
  for (const item of rows) {
    if (item.platform !== 'ozon' || item.source_type !== 'review') continue;
    const photosCount = Number(item.photos_count || 0);
    const videosCount = Number(item.videos_count || 0);
    if (photosCount <= 0 && videosCount <= 0 && !item.has_media) continue;
    const uuid = item.id || item.source_id;
    if (!uuid || result.has(uuid)) continue;
    result.set(uuid, {
      source_id: uuid,
      offer_id: item.offer_id || '',
      sku: item.sku || '',
      product_title: item.product_title || '',
      rating: item.rating || '',
      text_exists: Boolean(item.text),
      photos_count: photosCount,
      videos_count: videosCount,
      explicit: false,
    });
  }
  return [...result.values()].slice(0, limit);
}

function normalizePhoto(photo, index) {
  return {
    index,
    url: photo?.url || '',
    width: photo?.width || '',
    height: photo?.height || '',
  };
}

function normalizeVideo(video, index) {
  return {
    index,
    url: pick(video, 'url', 'video_url', 'videoUrl', 'src'),
    preview_url: pick(video, 'preview_url', 'previewUrl', 'cover_url', 'coverUrl'),
    width: video?.width || '',
    height: video?.height || '',
    duration: video?.duration || '',
  };
}

function redactedDetail(detailJson) {
  const review = detailJson?.result || detailJson || {};
  const text = typeof review.text === 'object' && review.text !== null ? review.text : { comment: review.text || '' };
  const product = review.product || {};
  const photos = Array.isArray(review.photos) ? review.photos.map(normalizePhoto) : [];
  const videos = Array.isArray(review.videos) ? review.videos.map(normalizeVideo) : [];
  return {
    uuid: review.uuid || review.id || '',
    interaction_status: review.interaction_status || '',
    product: {
      sku: product.sku || '',
      offer_id: product.offer_id || product.offerId || '',
      title: product.title || '',
      rating: product.rating || '',
      cover_image_url_present: Boolean(product.cover_image || product.coverImage),
      brand_name: product.brand_info?.name || product.brandInfo?.name || '',
    },
    rating: review.rating || '',
    text: {
      positive: text.positive || '',
      negative: text.negative || '',
      comment: text.comment || '',
    },
    photos,
    videos,
    photos_count: photos.length,
    videos_count: videos.length,
    is_commentable: review.is_commentable ?? '',
    is_commentable_2: review.is_commentable_2 ?? '',
    is_empty: review.is_empty ?? '',
    published_at: review.published_at || review.publishedAt || '',
    visibility: review.visibility || '',
    visibility_short: review.visibility_message_short?.short_description || '',
    redaction_note: 'author, order_number, chat_url, user ids and other private fields intentionally omitted',
  };
}

function extensionFromContentType(contentType, fallbackUrl) {
  const normalized = String(contentType || '').toLowerCase();
  if (normalized.includes('image/jpeg')) return '.jpg';
  if (normalized.includes('image/png')) return '.png';
  if (normalized.includes('image/webp')) return '.webp';
  if (normalized.includes('video/mp4')) return '.mp4';
  const cleanUrl = String(fallbackUrl || '').split('?')[0];
  const match = cleanUrl.match(/\.(jpg|jpeg|png|webp|mp4)$/i);
  return match ? `.${match[1].toLowerCase().replace('jpeg', 'jpg')}` : '.bin';
}

async function downloadUrl(url, filePath) {
  const response = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  const finalPath = filePath.replace(/\.bin$/, extensionFromContentType(response.headers.get('content-type'), url));
  fs.writeFileSync(assertInsideProject(finalPath), buffer);
  return {
    ok: true,
    path: path.relative(projectRoot, finalPath),
    bytes: buffer.length,
    content_type: response.headers.get('content-type') || '',
  };
}

async function fetchDetail(page, companyId, reviewUuid) {
  return post(page, '/api/v2/review/detail', {
    company_id: companyId,
    company_type: companyType,
    review_uuid: reviewUuid,
  }, 'review', companyId);
}

function printableResult(result) {
  return {
    ...result,
    reviews: result.reviews.map((review) => ({
      source_id: review.source_id,
      offer_id: review.offer_id,
      sku: review.sku,
      product_title: review.product_title,
      rating: review.rating,
      ok: review.ok,
      status: review.status,
      jsonParseOk: review.jsonParseOk,
      photos_count: review.photos.length,
      videos_count: review.videos.length,
      downloaded_photos: review.photos.filter((photo) => photo.local_path).map((photo) => photo.local_path),
      downloaded_videos: review.videos.filter((video) => video.local_path).map((video) => video.local_path),
      errors: review.errors,
      detail_redacted: review.detail_redacted,
    })),
  };
}

(async () => {
  const args = parseArgs(process.argv);
  const runDir = assertInsideProject(args.runDir);
  const itemsJson = assertInsideProject(args.itemsJson);
  const outputDir = path.join(runDir, 'processed', 'ozon_review_media_detail');
  ensureDir(outputDir);

  const result = {
    source: 'ozon_lk_cdp_internal_api',
    endpoint: '/api/v2/review/detail',
    cdpUrl,
    expectedStore,
    ok: false,
    blocker: '',
    review_count: 0,
    downloaded_media_count: 0,
    reviews: [],
    outputs: {},
    valuesPrinted: false,
  };
  let page;

  try {
    const targets = readMediaReviewUuids(itemsJson, args.reviewUuids, args.limit);
    result.review_count = targets.length;
    if (!targets.length) {
      result.ok = true;
      result.blocker = 'no_ozon_reviews_with_media_found';
      return;
    }

    const companyId = loadCompanyId();
    if (!companyId) throw new Error('Missing Ozon company id. Set OZON_REVIEW_COMPANY_ID or Ozon seller credentials file.');

    result.contourGuard = assertOzonCdpContour({
      cdpUrl,
      expectedPort: expectedCdpPort,
      expectedProfileDir,
      expectedStore,
    });
    const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 10000 });
    const context = browser.contexts()[0];
    if (!context) throw new Error('No browser context found in CDP session');
    page = await context.newPage();
    page.setDefaultTimeout(15000);

    const cabinet = await verifyCabinet(page);
    if (cabinet.status.blocked) throw new Error('Ozon blocked/no-connection page detected');
    if (cabinet.status.needsLogin) throw new Error('Login required in Ozon LK');
    if (!cabinet.status.expectedStoreFound) throw new Error('Expected Vital Shevron store marker not found');

    await page.goto('https://seller.ozon.ru/app/reviews', {
      waitUntil: 'domcontentloaded',
      timeout: 60000,
    });
    await page.waitForTimeout(2000);

    for (const target of targets) {
      const response = await fetchDetail(page, companyId, target.source_id);
      const row = {
        source_id: target.source_id,
        offer_id: target.offer_id || '',
        sku: target.sku || '',
        product_title: target.product_title || '',
        rating: target.rating || '',
        ok: response.ok,
        status: response.status,
        jsonParseOk: response.jsonParseOk,
        photos: [],
        videos: [],
        errors: [],
      };

      if (!response.ok || !response.jsonParseOk) {
        row.errors.push(`detail_request_failed:${response.status}`);
        result.reviews.push(row);
        continue;
      }

      const detail = redactedDetail(response.json);
      row.offer_id = row.offer_id || detail.product.offer_id;
      row.sku = row.sku || detail.product.sku;
      row.product_title = row.product_title || detail.product.title;
      row.rating = row.rating || detail.rating;

      const detailPath = path.join(outputDir, `${target.source_id}_detail_redacted.json`);
      writeJson(detailPath, detail);
      row.detail_redacted = path.relative(projectRoot, detailPath);

      for (const photo of detail.photos) {
        const mediaRow = { ...photo };
        if (args.downloadMedia && photo.url) {
          const basePath = path.join(outputDir, `${target.source_id}_photo_${photo.index}.bin`);
          try {
            const downloaded = await downloadUrl(photo.url, basePath);
            mediaRow.local_path = downloaded.path;
            mediaRow.bytes = downloaded.bytes;
            mediaRow.content_type = downloaded.content_type;
            result.downloaded_media_count += 1;
          } catch (error) {
            mediaRow.download_error = error.message || String(error);
          }
        }
        row.photos.push(mediaRow);
      }

      for (const video of detail.videos) {
        const mediaRow = { ...video };
        if (args.downloadMedia && video.url) {
          const basePath = path.join(outputDir, `${target.source_id}_video_${video.index}.bin`);
          try {
            const downloaded = await downloadUrl(video.url, basePath);
            mediaRow.local_path = downloaded.path;
            mediaRow.bytes = downloaded.bytes;
            mediaRow.content_type = downloaded.content_type;
            result.downloaded_media_count += 1;
          } catch (error) {
            mediaRow.download_error = error.message || String(error);
          }
        }
        row.videos.push(mediaRow);
      }

      result.reviews.push(row);
      await page.waitForTimeout(500);
    }

    result.ok = result.reviews.every((row) => row.ok);
  } catch (error) {
    result.blocker = error.message || String(error);
    process.exitCode = /Login required/.test(result.blocker) ? 10 : /blocked|no-connection|Vital Shevron store/.test(result.blocker) ? 20 : 1;
  } finally {
    if (page) await page.close().catch(() => null);
    const summaryPath = path.join(outputDir, 'summary.json');
    const manifestPath = path.join(outputDir, 'media_manifest.json');
    result.outputs.summary = path.relative(projectRoot, summaryPath);
    result.outputs.media_manifest = path.relative(projectRoot, manifestPath);
    writeJson(summaryPath, result);
    writeJson(manifestPath, result.reviews);
    console.log(JSON.stringify(printableResult(result), null, 2));
    setImmediate(() => process.exit(process.exitCode || 0));
  }
})();
