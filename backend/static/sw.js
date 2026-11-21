/* 智答系统 Service Worker：App-shell 缓存 + 离线回退。
 *
 * 硬约束：
 * 1) /api/ 一律直连（SSE 流式回答、Cookie/JWT 鉴权都靠它），不拦截、不缓存；
 * 2) 静态资源仍走 network-first，靠后端 no-cache + ETag 保证「改了代码就看到新代码」；
 * 3) 只有网络不可用时才回退缓存，保证离线还能打开外壳。
 *
 * 版本号改动即触发新缓存；activate 里清掉 zhida-* 旧缓存。
 */
const CACHE = "zhida-v3";
const CACHE_PREFIX = "zhida-";
const OFFLINE_URL = "/static/offline.html";
const FALLBACK_URL = "/static/app.html";

/* App shell：三张页面 + 样式 + 脚本 + 图标。任一 404 只跳过该条，不影响整体安装。 */
const APP_SHELL = [
  "/static/index.html",
  "/static/register.html",
  "/static/app.html",
  "/static/offline.html",
  "/static/manifest.webmanifest",
  "/static/css/common.css",
  "/static/css/app.css",
  "/static/css/auth.css",
  "/static/js/theme-boot.js",
  "/static/js/common.js",
  "/static/js/api.js",
  "/static/js/auth.js",
  "/static/js/markdown.js",
  "/static/js/app.js",
  "/static/js/views/chat.js",
  "/static/js/views/knowledge.js",
  "/static/js/views/workbench.js",
  "/static/js/views/history.js",
  "/static/js/views/documents.js",
  "/static/js/views/content.js",
  "/static/js/views/recycle.js",
  "/static/js/views/admin-feedback.js",
  "/static/js/views/admin.js",
  "/static/js/views/feedback.js",
  "/static/js/views/profile.js",
  // 下面五个视图原先漏了：离线时切到这些页面会白屏
  "/static/js/views/courses.js",
  "/static/js/views/notifications.js",
  "/static/js/views/export-share.js",
  "/static/js/views/shares.js",
  "/static/js/views/search-debug.js",
  "/static/js/nav-drawer-boot.js",
  "/static/js/sw-boot.js",
  "/static/img/logo.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-192.png",
  "/static/icons/icon-maskable-512.png",
];

/* 运行期缓存上限：静态资源是 network-first 写的，不设上限会随版本号无限增长。 */
const MAX_CACHED_ENTRIES = APP_SHELL.length * 3;

async function trimCache(cache) {
  const keys = await cache.keys();
  if (keys.length <= MAX_CACHED_ENTRIES) return;
  // 先建的先删（Map 语义的 Cache.keys 按插入顺序返回）
  const excess = keys.length - MAX_CACHED_ENTRIES;
  await Promise.all(keys.slice(0, excess).map((key) => cache.delete(key)));
}

async function putCached(request, response) {
  try {
    const cache = await caches.open(CACHE);
    await cache.put(request, response);
    await trimCache(cache);
  } catch (err) {
    /* 配额满 / 被中止：缓存只是加速手段，失败不影响页面 */
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // 单条失败不拖垮安装：某个文件改名或暂时 500 时，外壳其余部分仍可用
      await Promise.all(
        APP_SHELL.map((url) =>
          cache.add(new Request(url, { cache: "reload" })).catch(() => null)
        )
      );
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

/* 页面侧可发 {type:"SKIP_WAITING"} 主动激活新版本 */
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

/* 导航请求：network-first，离线回退到缓存的同一页面 → app.html → 离线页 */
async function handleNavigation(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      putCached(request, response.clone());
    }
    return response;
  } catch (err) {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    const appShell = await cache.match(FALLBACK_URL, { ignoreSearch: true });
    if (appShell) return appShell;
    const offline = await cache.match(OFFLINE_URL, { ignoreSearch: true });
    if (offline) return offline;
    throw err;
  }
}

/* 同源静态资源：network-first，失败回退缓存（忽略 ?v= 版本串） */
async function handleAsset(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === "basic") {
      putCached(request, response.clone());
    }
    return response;
  } catch (err) {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 接口 / SSE / 鉴权：完全交给浏览器直连，SW 不参与（否则会卡住流式回答）
  if (url.pathname.startsWith("/api/") || url.pathname === "/health") return;

  if (request.mode === "navigate") {
    event.respondWith(handleNavigation(request));
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(handleAsset(request));
  }
});
