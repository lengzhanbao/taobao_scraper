import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const playwrightCorePath = process.env.LIVE_PLAYWRIGHT_CORE_PATH;
const { chromium } = playwrightCorePath
  ? require(playwrightCorePath)
  : require('playwright-core');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)));
const STUDY_ROOT = path.join(ROOT, '直播研究数据');
const COOKIE_JSON = path.join(STUDY_ROOT, '_config', 'taobao_cookies.json');
const NOW = new Date();
const DATE_STAMP = [
  NOW.getFullYear(),
  String(NOW.getMonth() + 1).padStart(2, '0'),
  String(NOW.getDate()).padStart(2, '0'),
].join('');
const TXT_PATH = path.join(ROOT, `数字人确认_${DATE_STAMP}.txt`);
const EDGE_PATH = process.env.LIVE_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const PROFILE_DIR = path.join(ROOT, `.digital_check_playwright_${Date.now()}`);
const LOG_DIR = path.join(ROOT, '_logs');
const LOG_PATH = path.join(LOG_DIR, `detect_digital_${Date.now()}.log`);

const ID_LIST = [
  '4185708607630442',
  '2159201216030444',
  '3802945795113668',
  '1798606982673197',
  '4440671415937176',
  '2835520390595837',
  '3662375446845606',
  '906353825743304',
  '2320599129799487',
  '3877462002379074',
  '1906390834281132',
  '3953145595953056',
  '2772046376030968',
  '4414154203819745',
  '3194494387131868',
  '2966900568804956',
  '3266226158167777',
  '3968170402713334',
  '3203763468795195',
  '2779840023470123',
];

function log(message) {
  const now = new Date().toISOString().replace('T', ' ').slice(0, 19);
  const line = `[${now}] ${message}`;
  console.log(line);
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(LOG_PATH, `${line}\n`, 'utf8');
  } catch {
    // Logging is diagnostic only; the txt report is the deliverable.
  }
}

function normalizeBool(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    const v = value.trim().toLowerCase();
    return v === 'true' || v === '1' || v === 'yes';
  }
  return null;
}

function findKeys(obj, key) {
  const found = [];
  if (Array.isArray(obj)) {
    for (const item of obj) found.push(...findKeys(item, key));
  } else if (obj && typeof obj === 'object') {
    for (const [k, v] of Object.entries(obj)) {
      if (k === key) found.push(v);
      found.push(...findKeys(v, key));
    }
  }
  return found;
}

function firstText(values) {
  for (const value of values || []) {
    if (value === null || value === undefined) continue;
    const text = String(value).trim();
    if (text && text !== 'None') return text;
  }
  return '';
}

function parseDetail(raw, liveId) {
  if (!raw || !String(raw).includes(liveId)) return null;
  const text = String(raw);
  let obj = null;
  try {
    obj = JSON.parse(text);
  } catch {
    obj = null;
  }

  const result = { liveId };
  if (obj) {
    const digitalValues = findKeys(obj, 'isDigitalAnchorLive');
    if (digitalValues.length) result.isDigitalAnchorLive = normalizeBool(digitalValues[0]);
    result.liveTitle = firstText(
      findKeys(obj, 'liveTitle').length ? findKeys(obj, 'liveTitle') : findKeys(obj, 'title'),
    );
    result.anchorName = firstText(
      findKeys(obj, 'nickName').length
        ? findKeys(obj, 'nickName')
        : findKeys(obj, 'anchorName').length
          ? findKeys(obj, 'anchorName')
          : findKeys(obj, 'userName'),
    );
    result.liveStatus = firstText(
      findKeys(obj, 'liveStatus').length
        ? findKeys(obj, 'liveStatus')
        : findKeys(obj, 'status').length
          ? findKeys(obj, 'status')
          : findKeys(obj, 'isLive'),
    );
  } else {
    const digitalMatch = text.match(/"isDigitalAnchorLive"\s*:\s*("(?:true|false)"|true|false)/);
    if (digitalMatch) {
      result.isDigitalAnchorLive = digitalMatch[1].replaceAll('"', '').toLowerCase() === 'true';
    }
    for (const key of ['liveTitle', 'title', 'nickName', 'anchorName', 'userName', 'liveStatus', 'status', 'isLive']) {
      const m = text.match(new RegExp(`"${key}"\\s*:\\s*"([^"]*)"`));
      if (m) result[key] = m[1];
    }
  }
  result.rawSnippet = text.slice(0, 1200);
  return result;
}

function waitForDetail(page, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (!settled) {
        settled = true;
        page.off('response', handler);
        resolve(value);
      }
    };
    const handler = async (response) => {
      try {
        if (response.url().includes('live.detail.get')) {
          finish(await response.text());
        }
      } catch {
        finish(null);
      }
    };
    page.on('response', handler);
    setTimeout(() => finish(null), timeoutMs);
  });
}

function loadCookies() {
  if (!fs.existsSync(COOKIE_JSON)) return [];
  const parsed = JSON.parse(fs.readFileSync(COOKIE_JSON, 'utf8'));
  if (!Array.isArray(parsed)) return [];
  return parsed
    .filter((c) => c && c.name && c.value !== undefined)
    .map((c) => ({
      name: c.name,
      value: c.value,
      domain: c.domain || '.taobao.com',
      path: c.path || '/',
      ...(c.expires ? { expires: c.expires } : {}),
      ...(c.httpOnly ? { httpOnly: true } : {}),
      ...(c.secure ? { secure: true } : {}),
      ...(c.sameSite ? { sameSite: c.sameSite } : {}),
    }));
}

function extractDigital(raw, liveId) {
  if (!raw || !String(raw).includes(liveId)) return null;
  const match = String(raw).match(/"isDigitalAnchorLive"\s*:\s*("(?:true|false)"|true|false)/);
  if (!match) return null;
  return match[1].replaceAll('"', '').toLowerCase() === 'true';
}

function extractName(raw) {
  const text = String(raw || '');
  let obj = null;
  try {
    const jsonText = text.trim().replace(/^[^(]*\(\s*/, '').replace(/\s*\)\s*$/, '');
    obj = JSON.parse(jsonText);
  } catch {
    obj = null;
  }
  const data = obj?.data || obj || {};
  return firstText([
    data.title,
    data.liveTitle,
    data.roomTitle,
    data.liveIntroduction,
    data.broadCaster?.accountName,
    data.accountName,
  ]);
}

async function checkOne(page, liveId) {
  const url = `https://tbzb.taobao.com/live?liveId=${liveId}`;
  let detailText = '';
  try {
    const waitDetail = page
      .waitForResponse(
        (response) => response.url().includes('live.detail.get'),
        { timeout: 8000 },
      )
      .then(async (response) => {
        detailText = await response.text();
        return detailText;
      })
      .catch(() => null);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await waitDetail;
    if (!detailText || !detailText.includes(liveId)) {
      const waitReload = page
        .waitForResponse(
          (response) => response.url().includes('live.detail.get'),
          { timeout: 6000 },
        )
        .then(async (response) => {
          detailText = await response.text();
          return detailText;
        })
        .catch(() => null);
      await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      await waitReload;
    }
    const digital = extractDigital(detailText, liveId);
    if (digital === null) {
      return {
        liveId,
        url,
        status: 'no_api',
        isDigitalAnchorLive: null,
      };
    }
    return {
      liveId,
      url,
      status: digital ? 'digital' : 'human',
      isDigitalAnchorLive: digital,
      roomName: extractName(detailText),
    };
  } finally {
    // Page is reused by the caller, matching the existing crawler flow.
  }
}

async function main() {
  const inputArgs = process.argv.slice(2);
  let ids = ID_LIST;
  if (inputArgs.length) {
    if (fs.existsSync(inputArgs[0])) {
      const inputText = fs.readFileSync(inputArgs[0], 'utf8');
      const matched = [...inputText.matchAll(/liveId=(\d+)/g)].map((m) => m[1]);
      ids = [...new Set(matched)];
      log(`read ids from file, count=${ids.length}`);
    } else {
      ids = [...new Set(inputArgs)];
    }
  }
  log(`start detector, ids=${ids.length}`);
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    executablePath: EDGE_PATH,
    headless: true,
    args: [
      '--disable-blink-features=AutomationControlled',
      '--disable-background-networking',
      '--disable-component-update',
      '--disable-extensions',
      '--disable-sync',
      '--no-first-run',
      '--no-default-browser-check',
    ],
  });
  log('browser launched');

  try {
    const firstPage = context.pages()[0] || (await context.newPage());
    await firstPage.goto('https://www.taobao.com', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    const cookies = loadCookies();
    log(`cookie count=${cookies.length}`);
    if (cookies.length) {
      await context.addCookies(cookies).catch((e) => log(`cookie inject warning: ${e.message}`));
    }

    const results = [];
    const page = context.pages()[0] || (await context.newPage());
    for (let index = 0; index < ids.length; index += 1) {
      const liveId = ids[index];
      log(`[${index + 1}/${ids.length}] check ${liveId}`);
      const result = await checkOne(page, liveId);
      result.checkedAt = new Date().toISOString();
      results.push(result);
      log(
        `  -> ${result.status} digital=${result.isDigitalAnchorLive} name=${result.roomName || ''}`,
      );
      if (result.apiUrls) {
        log(`  -> api urls: ${result.apiUrls.join(' | ')}`);
      }
      if (result.detailSnippet) {
        log(
          `  -> detailCount=${result.detailCount} detailLen=${result.detailLen} snippet=${result.detailSnippet}`,
        );
      }
      if (index + 1 < ids.length) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    }

    const digital = results.filter((r) => r.status === 'digital');
    const human = results.filter((r) => r.status === 'human');
    const unknown = results.filter((r) => r.status !== 'digital' && r.status !== 'human');
    log(`digital=${digital.length} human=${human.length} unknown=${unknown.length}`);

    const newLines = digital.map(
      (r) => `liveId=${r.liveId},${r.roomName || ''},${r.url}`,
    );
    const existingLines = fs.existsSync(TXT_PATH)
      ? fs.readFileSync(TXT_PATH, 'utf8').split(/\r?\n/).filter(Boolean)
      : [];
    const seen = new Set();
    const lines = [...existingLines, ...newLines].filter((line) => {
      const key = line.split(',')[0];
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const content = lines.length ? `${lines.join('\n')}\n` : '未确认到数字人直播间\n';
    fs.writeFileSync(TXT_PATH, content, 'utf8');
    log(`txt saved: ${TXT_PATH}`);
    if (digital.length) {
      log(`digital ids: ${digital.map((r) => r.liveId).join(', ')}`);
    }
  } finally {
    await context.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
