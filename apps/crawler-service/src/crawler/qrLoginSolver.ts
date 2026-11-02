/**
 * 闲鱼扫码登录求解器
 * ==================
 *
 * 使用 Playwright 打开淘宝/闲鱼登录页面：
 * 1. 拦截页面二维码图片（截图二维码区域或网络响应）
 * 2. 等待用户用闲鱼 App 扫码确认登录
 * 3. 监听登录成功跳转（回到 goofish.com 首页）
 * 4. context.cookies() 一次性提取所有 Cookie
 * 5. 返回 Cookie 字符串供 Python 端写入 DB
 *
 * 与 sliderSolver.ts 的区别：
 * - sliderSolver 处理"已登录但出现滑块"场景
 * - qrLoginSolver 处理"Session 过期需重新登录"场景
 *
 * 与 xianyu_qr_login.py（直接调用 mtop API）的区别：
 * - Python 实现依赖逆向接口，可能不稳定
 * - 本实现走真实浏览器，与用户手动登录完全一致，最稳定
 */
import { chromium, type Browser, type BrowserContext, type ElementHandle, type Page, type BrowserContextOptions } from 'playwright';
import { isHeadedDisplayAvailable, parseCookieString } from './sliderSolver.js';
import {
  isAllowedBrowserNavigationUrl,
  isSafeBrowserResourceUrl,
  safeErrorType,
  toPublicCrawlerError,
} from '../policy.js';

export interface QrLoginSolveOptions {
  /** 旧的 Cookie（可选，用于复用部分会话状态） */
  cookieStr?: string;
  /** 登录入口 URL，默认闲鱼首页 */
  targetUrl?: string;
  /** 是否无头模式，默认 false（扫码登录需要有头模式方便用户扫码） */
  headless?: boolean;
  /** 等待扫码确认的超时时间（毫秒），默认 120000 = 2 分钟 */
  scanTimeoutMs?: number;
  /** 是否返回二维码图片 bytes（默认 true） */
  captureQrImage?: boolean;
}

export interface QrLoginSolveResult {
  /** 整体是否成功（用户已扫码登录并提取到 Cookie） */
  ok: boolean;
  /** 登录阶段：qr_ready / waiting_scan / scanned / login_success / timeout / error */
  stage: string;
  /** 二维码图片 PNG bytes（仅当 captureQrImage=true 且二维码已就绪时返回） */
  qrImageBytes?: Buffer;
  /** 提取到的 Cookie 字符串（;分隔，可直接写入 DB） */
  cookieStr?: string;
  /** 关键 Cookie 中的 unb（用户 ID） */
  unb?: string;
  /** _m_h5_tk 令牌 */
  mH5Tk?: string;
  /** 失败/超时/错误信息 */
  error?: string;
  /** 总耗时（毫秒） */
  durationMs: number;
}

const DEFAULT_TARGET_URL = 'https://www.goofish.com/';
const DEFAULT_SCAN_TIMEOUT = 120000; // 2 分钟

/**
 * 启动浏览器并打开闲鱼首页。
 * 若未登录会自动跳转到登录页，函数会等待登录页二维码就绪并截图。
 */
export async function solveQrLoginInBrowser(
  options: QrLoginSolveOptions = {}
): Promise<QrLoginSolveResult> {
  const startTime = Date.now();
  const targetUrl = options.targetUrl || DEFAULT_TARGET_URL;
  const headless = options.headless ?? !isHeadedDisplayAvailable();
  const scanTimeoutMs = options.scanTimeoutMs ?? DEFAULT_SCAN_TIMEOUT;
  const captureQrImage = options.captureQrImage ?? true;

  let browser: Browser | null = null;
  let context: BrowserContext | null = null;
  let page: Page | null = null;

  try {
    // === 1. 启动浏览器 ===
    const contextOptions: BrowserContextOptions = {
      viewport: { width: 1280, height: 800 },
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      locale: 'zh-CN',
    };
    // 注入旧 Cookie（如果有），可能跳过登录直接进入首页
    if (options.cookieStr) {
      const cookies = parseCookieString(options.cookieStr);
      contextOptions.storageState = { cookies, origins: [] };
    }

    browser = await launchQrBrowser(headless);
    context = await browser.newContext(contextOptions);
    await guardLoginNavigation(context);

    // 反检测
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      (window as any).chrome = { runtime: {} };
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    });

    page = await context.newPage();
    console.log('[QrLoginSolver] 访问登录目标页面');
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

    // === 2. 判断当前是否已登录 ===
    // 已登录：URL 停留在 goofish.com 且页面包含"我想要"等关键词
    // 未登录：跳转到 login.taobao.com 或显示登录入口
    await page.waitForTimeout(2000);
    const currentUrl = page.url();
    const isLoggedIn = await hasUsableUnb(context)
      && !isLoginPageUrl(currentUrl)
      && !(await detectLoginIndicator(page));

    if (isLoggedIn) {
      console.log('[QrLoginSolver] 检测到已登录状态，直接提取 Cookie');
      const cookieStr = await extractCookies(context);
      const { unb, mH5Tk } = parseKeyCookies(cookieStr);
      return {
        ok: true,
        stage: 'login_success',
        cookieStr,
        unb,
        mH5Tk,
        durationMs: Date.now() - startTime,
      };
    }

    // === 3. 打开登录框并等待二维码就绪 ===
    console.log('[QrLoginSolver] 未登录，打开登录框并等待二维码出现');
    await openLoginQr(page);
    let qrImageBytes: Buffer | undefined;
    if (captureQrImage) {
      try {
        qrImageBytes = await captureQrCodeImage(page);
        console.log(
          `[QrLoginSolver] 二维码已捕获，大小=${qrImageBytes?.length || 0} bytes`
        );
      } catch (e: any) {
        console.warn(`[QrLoginSolver] 二维码截图失败: errorType=${safeErrorType(e)}`);
      }
    }

    // === 4. 等待用户扫码登录成功 ===
    console.log(`[QrLoginSolver] 等待用户扫码（超时 ${scanTimeoutMs}ms）`);
    const loginSuccess = await waitForLoginSuccess(page, scanTimeoutMs);
    if (!loginSuccess) {
      return {
        ok: false,
        stage: 'timeout',
        qrImageBytes,
        error: '等待用户扫码超时',
        durationMs: Date.now() - startTime,
      };
    }

    // === 5. 提取 Cookie ===
    console.log('[QrLoginSolver] 登录成功，提取 Cookie');
    // 登录后弹出的“保持登录状态”默认 8 秒后选“不保持”，必须主动点“保持”，
    // 否则会话缺少长期 SSO Cookie，约 30-60 分钟后被判定过期。
    await clickKeepLoginIfPresent(page);
    // 等待 2 秒让所有 Set-Cookie 完成
    await page.waitForTimeout(2000);
    const cookieStr = await extractCookies(context);
    const { unb, mH5Tk } = parseKeyCookies(cookieStr);
    if (!cookieStr || !unb) {
      return {
        ok: false,
        stage: 'error',
        qrImageBytes,
        error: '登录成功但 Cookie 提取失败（unb 为空）',
        durationMs: Date.now() - startTime,
      };
    }

    return {
      ok: true,
      stage: 'login_success',
      cookieStr,
      unb,
      mH5Tk,
      durationMs: Date.now() - startTime,
    };
  } catch (e: any) {
    console.error(`[QrLoginSolver] operation=solve errorType=${safeErrorType(e)}`);
    return {
      ok: false,
      stage: 'error',
      error: toPublicCrawlerError(e, '二维码登录处理失败'),
      durationMs: Date.now() - startTime,
    };
  } finally {
    try {
      if (context) await context.close();
    } catch {}
    try {
      if (browser) await browser.close();
    } catch {}
  }
}

export async function completeQrLoginSession(
  context: BrowserContext,
  page: Page,
  scanTimeoutMs: number = DEFAULT_SCAN_TIMEOUT,
): Promise<QrLoginSolveResult> {
  const startTime = Date.now();
  const timeout = Number.isSafeInteger(scanTimeoutMs)
    ? Math.max(10000, Math.min(scanTimeoutMs, 300000)) : DEFAULT_SCAN_TIMEOUT;
  try {
    const loginSuccess = await waitForLoginSuccess(page, timeout);
    if (!loginSuccess) {
      return {
        ok: false,
        stage: 'timeout',
        error: '等待用户扫码超时',
        durationMs: Date.now() - startTime,
      };
    }

    await clickKeepLoginIfPresent(page);
    await page.waitForTimeout(2000);
    const cookieStr = await extractCookies(context);
    const { unb, mH5Tk } = parseKeyCookies(cookieStr);
    if (!cookieStr || !unb) {
      return {
        ok: false,
        stage: 'error',
        error: '登录成功但未能提取有效的闲鱼 Cookie',
        durationMs: Date.now() - startTime,
      };
    }
    return {
      ok: true,
      stage: 'login_success',
      cookieStr,
      unb,
      mH5Tk,
      durationMs: Date.now() - startTime,
    };
  } catch (error) {
    console.error(`[QrLoginSolver] operation=completeSession errorType=${safeErrorType(error)}`);
    return {
      ok: false,
      stage: 'error',
      error: toPublicCrawlerError(error, '二维码登录处理失败'),
      durationMs: Date.now() - startTime,
    };
  }
}

/**
 * 启动浏览器并只获取二维码图片（不等待扫码完成）。
 *
 * 用于"用户在飞书回复请求二维码"场景：
 * Python 端调用此函数获取二维码图片 bytes，发送给用户后关闭浏览器。
 * 然后再次调用 solveQrLoginInBrowser（复用 Cookie）等待扫码完成。
 *
 * 注意：调用方需自行关闭浏览器，此处不关闭。
 */
export async function captureQrCodeOnly(
  options: QrLoginSolveOptions = {}
): Promise<{ ok: boolean; qrImageBytes?: Buffer; error?: string; browser?: Browser; context?: BrowserContext; page?: Page }> {
  const targetUrl = options.targetUrl || DEFAULT_TARGET_URL;
  const headless = options.headless ?? !isHeadedDisplayAvailable();
  let browser: Browser | undefined;
  let context: BrowserContext | undefined;

  try {
    const contextOptions: BrowserContextOptions = {
      viewport: { width: 1280, height: 800 },
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      locale: 'zh-CN',
    };
    if (options.cookieStr) {
      const cookies = parseCookieString(options.cookieStr);
      contextOptions.storageState = { cookies, origins: [] };
    }

    browser = await launchQrBrowser(headless);
    context = await browser.newContext(contextOptions);
    await guardLoginNavigation(context);
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      (window as any).chrome = { runtime: {} };
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    });
    const page = await context.newPage();
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000);

    // 以 unb Cookie 判断登录态：闲鱼首页未登录也能正常浏览，
    // 单看 URL / 页面文案会把"未登录首页"误判成已登录，从而拿不到二维码。
    const currentUrl = page.url();
    const hasUnb = await hasUsableUnb(context);
    if (hasUnb && !isLoginPageUrl(currentUrl) && !(await detectLoginIndicator(page))) {
      return { ok: true, browser, context, page };
    }

    await openLoginQr(page);
    const qrImageBytes = await captureQrCodeImage(page);
    return {
      ok: !!qrImageBytes,
      qrImageBytes,
      browser,
      context,
      page,
    };
  } catch (e: any) {
    console.error(`[QrLoginSolver] operation=capture errorType=${safeErrorType(e)}`);
    try { if (context) await context.close(); } catch {}
    try { if (browser) await browser.close(); } catch {}
    return { ok: false, error: toPublicCrawlerError(e, '二维码捕获失败') };
  }
}

// ============================================================
// 内部辅助函数
// ============================================================

/**
 * 与 goofish.ts / goofishSearch.ts / sliderSolver.ts 保持一致：
 * Linux 容器内使用系统 Chrome（channel: 'chrome'），并关闭 Chromium 自带 sandbox，
 * 否则 AppArmor 会阻止 Chrome sandbox 初始化，或 Playwright 会去找未安装的
 * chrome-headless-shell。macOS/Windows 仍保持 sandbox 开启。
 */
function launchQrBrowser(headless: boolean): Promise<Browser> {
  const isWindows = process.platform === 'win32';
  const isLinux = process.platform !== 'win32' && process.platform !== 'darwin';
  return chromium.launch({
    headless,
    chromiumSandbox: !isLinux,
    ...((isWindows || isLinux) ? { channel: 'chrome' as const } : {}),
    ignoreDefaultArgs: ['--enable-automation'],
    args: [
      '--disable-blink-features=AutomationControlled',
      ...(isLinux
        ? [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-crash-reporter',
            '--disable-crashpad',
            '--disable-breakpad',
            '--disable-features=Crashpad',
          ]
        : []),
    ],
  });
}

function isLoginPageUrl(url: string): boolean {
  return (
    /login\.taobao\.com/i.test(url) ||
    /login\.goofish\.com/i.test(url) ||
    /\/login\b/i.test(url) ||
    /\/uiLogin\b/i.test(url)
  );
}

async function guardLoginNavigation(context: BrowserContext): Promise<void> {
  await context.route('**/*', async (route) => {
    const request = route.request();
    if (!isSafeBrowserResourceUrl(request.url())) {
      await route.abort('blockedbyclient');
      return;
    }
    if (!request.isNavigationRequest() || request.frame().parentFrame()
        || isAllowedBrowserNavigationUrl(request.url(), true)) {
      await route.continue();
      return;
    }
    await route.abort('blockedbyclient');
  });
}

async function detectLoginIndicator(page: Page): Promise<boolean> {
  try {
    return await page.evaluate(() => {
      const text = document.body ? document.body.innerText : '';
      if (
        /扫码登录|手机号登录|账号密码登录/i.test(text) &&
        !/我想要|猜你喜欢|闲置/i.test(text)
      ) {
        return true;
      }
      return false;
    });
  } catch {
    return false;
  }
}

async function hasUsableUnb(context: BrowserContext): Promise<boolean> {
  const cookies = await context.cookies();
  return cookies.some((c) => c.name === 'unb' && c.value && c.value !== '0');
}

const QR_ELEMENT_SELECTORS = [
  '#J_QRCodeImg',
  '.qrcode-img',
  '.J_QRCodeImg',
  'img[src*="qrcode"]',
  'canvas.qrcode',
  '#login-qrcode',
  '.login-qrcode',
];

async function findVisibleQrElement(page: Page): Promise<ElementHandle | null> {
  for (const frame of page.frames()) {
    for (const selector of QR_ELEMENT_SELECTORS) {
      try {
        const element = await frame.$(selector);
        if (element && await element.isVisible()) return element;
      } catch {
        // frame 可能正在跳转，继续找下一个
      }
    }
  }
  return null;
}

/**
 * 打开闲鱼首页的登录框。
 *
 * 未登录时 www.goofish.com 仍是可正常浏览的首页，只有点右上角"登录"才会
 * 加载 passport.goofish.com 的 mini_login iframe，二维码在该 iframe 内。
 */
async function openLoginQr(page: Page): Promise<void> {
  if (await findVisibleQrElement(page)) return;

  await page.evaluate(() => {
    const candidates = Array.from(
      document.querySelectorAll<HTMLElement>('a,button,div,span')
    ).filter((element) => {
      const text = (element.textContent || '').trim();
      if (text !== '登录' && text !== '亲，请登录') return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    });
    const target = candidates.find((element) => element.tagName === 'A') || candidates[0];
    if (target) target.click();
  }).catch(() => {
    // 页面可能正在跳转，交给下面的等待逻辑
  });

  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (await findVisibleQrElement(page)) return;
    await page.waitForTimeout(500);
  }
  console.warn(`[QrLoginSolver] 登录框二维码未就绪 url=${page.url()}`);
}

/**
 * 截取二维码区域图片。二维码可能在主文档，也可能在 passport 登录 iframe 内。
 */
async function captureQrCodeImage(page: Page): Promise<Buffer | undefined> {
  const deadline = Date.now() + 12000;
  let element: ElementHandle | null = null;
  while (Date.now() < deadline) {
    element = await findVisibleQrElement(page);
    if (element) break;
    await page.waitForTimeout(500);
  }
  if (!element) {
    console.warn(`[QrLoginSolver] 未找到可见二维码 url=${page.url()}`);
    return undefined;
  }

  try {
    const buf = await element.screenshot({ type: 'png' });
    if (buf && buf.length > 0) {
      console.log(`[QrLoginSolver] 二维码截图成功 大小=${buf.length} bytes`);
      return buf;
    }
  } catch (e: any) {
    console.warn(`[QrLoginSolver] 二维码截图失败: errorType=${safeErrorType(e)}`);
  }
  return undefined;
}

/**
 * 等待用户扫码登录成功。
 *
 * 检测方式：
 * 1. URL 跳转回 goofish.com（不再是 login.taobao.com）
 * 2. 页面包含"我想要/猜你喜欢"等已登录关键词
 * 3. Cookie 中出现 unb 且不为空
 */
/**
 * 登录成功后会弹出“保持登录状态”确认框，默认 8 秒后自动选“不保持”。
 * 主动点击“保持”，让服务端下发长期 SSO Cookie（havana_lgc_exp 等）。
 */
async function clickKeepLoginIfPresent(page: Page): Promise<void> {
  for (const frame of page.frames()) {
    try {
      const button = await frame.$('button.keep-login-confirm-btn.primary');
      if (button && await button.isVisible()) {
        await button.click({ timeout: 2000 });
        console.log('[QrLoginSolver] 已点击“保持登录”');
        await page.waitForTimeout(1200);
        return;
      }
    } catch {
      // 页面跳转中或按钮已消失，继续下一帧
    }
  }
}

async function waitForLoginSuccess(page: Page, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await clickKeepLoginIfPresent(page);
    try {
      const url = page.url();
      if (!isLoginPageUrl(url) && !(await detectLoginIndicator(page))) {
        // 二次确认：检查 Cookie 中是否有 unb
        const cookies = await page.context().cookies();
        const unbCookie = cookies.find((c) => c.name === 'unb');
        if (unbCookie && unbCookie.value && unbCookie.value !== '0') {
          // 登录成功瞬间“保持登录”弹窗可能刚出现，给页面切换留时间并主动点“保持”。
          await page.waitForTimeout(1200);
          await clickKeepLoginIfPresent(page);
          await page.waitForTimeout(800);
          return true;
        }
      }
    } catch {
      // 页面可能正在跳转，忽略
    }
    await page.waitForTimeout(1500);
  }
  return false;
}

/**
 * 从 BrowserContext 提取登录态 Cookie 并拼接成字符串。
 *
 * 不能只取 goofish.com：淘宝/闲鱼 SSO 的 `_hvn_lgc_`、`havana_lgc_exp` 等
 * 关键登录 Cookie 可能落在 `.taobao.com` 域。按域优先级去重，goofish 同名
 * Cookie 优先，避免把无关第三方 Cookie 带进账号。
 */
async function extractCookies(context: BrowserContext): Promise<string> {
  const cookies = await context.cookies();
  const selected = cookies
    .filter((c) => c.name && c.value && authCookieDomainRank(c.domain) >= 0)
    .sort((a, b) => authCookieDomainRank(a.domain) - authCookieDomainRank(b.domain));
  const seen = new Set<string>();
  const parts: string[] = [];
  for (const cookie of selected) {
    if (seen.has(cookie.name)) continue;
    seen.add(cookie.name);
    parts.push(`${cookie.name}=${cookie.value}`);
  }
  return parts.join('; ');
}

function authCookieDomainRank(domain: string): number {
  const normalized = domain.toLowerCase().replace(/^\./, '');
  if (normalized === 'goofish.com' || normalized.endsWith('.goofish.com')) return 0;
  if (normalized === 'taobao.com' || normalized.endsWith('.taobao.com')) return 1;
  if (normalized === 'tmall.com' || normalized.endsWith('.tmall.com')) return 2;
  if (normalized === 'alibaba.com' || normalized.endsWith('.alibaba.com')) return 3;
  return -1;
}

/**
 * 从 Cookie 字符串中解析关键 Cookie：unb 和 _m_h5_tk。
 */
function parseKeyCookies(cookieStr: string): { unb?: string; mH5Tk?: string } {
  if (!cookieStr) return {};
  const map: Record<string, string> = {};
  for (const part of cookieStr.split(';')) {
    const idx = part.indexOf('=');
    if (idx > 0) {
      const k = part.slice(0, idx).trim();
      const v = part.slice(idx + 1).trim();
      if (k) map[k] = v;
    }
  }
  return {
    unb: map['unb'] || undefined,
    mH5Tk: map['_m_h5_tk'] || undefined,
  };
}
