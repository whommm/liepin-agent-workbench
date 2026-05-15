"""Playwright browser session manager for Liepin automation."""

import logging
import os
import queue
import re
import sys
import threading
from datetime import datetime
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

from .config import ConfigManager

try:
    from playwright.sync_api import (
        BrowserContext,
        Error,
        Page,
        Playwright,
        sync_playwright,
    )
except ImportError:  # pragma: no cover - validated at runtime when dependency exists
    BrowserContext = None
    Error = Exception
    Page = None
    Playwright = None
    sync_playwright = None


class LiepinBrowserError(Exception):
    """Base error for Liepin browser automation."""


class PlaywrightNotInstalledError(LiepinBrowserError):
    """Raised when Playwright is not installed."""


class LiepinLoginRequiredError(LiepinBrowserError):
    """Raised when the browser is open but not logged in to Liepin."""


@dataclass
class LiepinBrowserState:
    """Observable browser session state."""

    profile_dir: str
    channel: str
    headless: bool
    is_running: bool = False
    logged_in: bool = False
    current_url: str = ""


T = TypeVar("T")
logger = logging.getLogger(__name__)


class LiepinBrowserManager:
    """Manage a persistent Playwright browser context for Liepin."""

    LOGIN_URL = "https://h.liepin.com/"
    SEARCH_URL = "https://h.liepin.com/search/getConditionItem"

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._thread: Optional[threading.Thread] = None
        self._task_queue = queue.Queue()
        self._thread_id: Optional[int] = None

    def _get_base_dir(self) -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    @staticmethod
    def _find_system_browser_executable() -> tuple:
        """Prefer installed Chrome/Edge for frozen builds."""
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "PROGRAMFILES(X86)", r"C:\Program Files (x86)"
        )

        candidates = [
            (
                "chrome",
                os.path.join(
                    program_files, "Google", "Chrome", "Application", "chrome.exe"
                ),
            ),
            (
                "chrome",
                os.path.join(
                    program_files_x86,
                    "Google",
                    "Chrome",
                    "Application",
                    "chrome.exe",
                ),
            ),
            (
                "chrome",
                os.path.join(
                    local_app_data,
                    "Google",
                    "Chrome",
                    "Application",
                    "chrome.exe",
                ),
            ),
            (
                "msedge",
                os.path.join(
                    program_files,
                    "Microsoft",
                    "Edge",
                    "Application",
                    "msedge.exe",
                ),
            ),
            (
                "msedge",
                os.path.join(
                    program_files_x86,
                    "Microsoft",
                    "Edge",
                    "Application",
                    "msedge.exe",
                ),
            ),
            (
                "msedge",
                os.path.join(
                    local_app_data,
                    "Microsoft",
                    "Edge",
                    "Application",
                    "msedge.exe",
                ),
            ),
        ]
        for channel, path in candidates:
            if path and os.path.exists(path):
                return channel, path
        return "", ""

    @staticmethod
    def _configure_playwright_browser_path() -> None:
        """Point frozen builds to the shared Playwright browser cache."""
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            return
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            return
        shared_browser_dir = os.path.join(local_app_data, "ms-playwright")
        if os.path.isdir(shared_browser_dir):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = shared_browser_dir

    def get_profile_dir(self) -> str:
        """Return the absolute path to the persistent browser profile directory."""
        profile_dir = os.path.normpath(
            self.config_manager.config.liepin_browser_profile_dir.strip()
        )
        if os.path.isabs(profile_dir):
            return profile_dir
        return os.path.normpath(os.path.join(self._get_base_dir(), profile_dir))

    def get_debug_dir(self) -> str:
        """Return the absolute path for browser debug artifacts."""
        path = os.path.join(self._get_base_dir(), "debug_artifacts", "liepin")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _pick_best_page(pages, current_page=None):
        """Prefer the most recently opened Liepin page."""
        valid_pages = []
        for page in pages or []:
            try:
                if page.is_closed():
                    continue
            except Exception:
                continue
            valid_pages.append(page)

        if not valid_pages:
            return current_page

        liepin_pages = []
        for page in valid_pages:
            try:
                if "liepin.com" in (page.url or "").lower():
                    liepin_pages.append(page)
            except Exception:
                continue

        if liepin_pages:
            return liepin_pages[-1]
        return valid_pages[-1]

    def _ensure_worker(self) -> None:
        """Start the dedicated Playwright worker thread if needed."""
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._worker_loop,
            name="LiepinBrowserWorker",
            daemon=True,
        )
        self._thread.start()

    def _worker_loop(self) -> None:
        """Run Playwright tasks on a single dedicated thread."""
        self._thread_id = threading.get_ident()
        while True:
            item = self._task_queue.get()
            if item is None:
                break

            func, args, kwargs, result_queue = item
            try:
                value = func(*args, **kwargs)
                result_queue.put((True, value))
            except Exception as exc:
                result_queue.put((False, exc))

    def _run_on_worker(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute a callable on the dedicated Playwright thread."""
        self._ensure_worker()
        if threading.get_ident() == self._thread_id:
            return func(*args, **kwargs)

        result_queue = queue.Queue(maxsize=1)
        self._task_queue.put((func, args, kwargs, result_queue))
        success, payload = result_queue.get()
        if success:
            return payload
        raise payload

    def _sync_active_page(self) -> Optional[Page]:
        """Keep the current page pointer aligned to the best available page."""
        if self._context is None:
            return None
        self._page = self._pick_best_page(list(self._context.pages), self._page)
        return self._page

    def _apply_stealth_locked(self, page: Page) -> None:
        """Inject minimal anti-detection scripts without breaking site rendering."""
        if page is None:
            return
        try:
            if getattr(page, "_liepin_stealth_applied", False):
                return
            page.add_init_script(
                r"""
                (() => {
                    // 1. Hide webdriver flag
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    // 2. Pretend we have plugins (some sites check length)
                    if (!navigator.plugins || navigator.plugins.length === 0) {
                        Object.defineProperty(navigator, 'plugins', {
                            get: () => [{
                                0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                                description: "Portable Document Format",
                                filename: "internal-pdf-viewer",
                                length: 1,
                                name: "Chrome PDF Plugin",
                                item: idx => [][idx]
                            }]
                        });
                    }
                    // 3. MimeTypes
                    if (!navigator.mimeTypes || navigator.mimeTypes.length === 0) {
                        Object.defineProperty(navigator, 'mimeTypes', {
                            get: () => [{
                                description: "Portable Document Format",
                                suffixes: "pdf",
                                type: "application/pdf",
                                enabledPlugin: null
                            }]
                        });
                    }
                    // 4. languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['zh-CN', 'zh', 'en']
                    });
                    // 5. permissions query patch (common bot check)
                    if (navigator.permissions && navigator.permissions.query) {
                        const originalQuery = navigator.permissions.query;
                        navigator.permissions.query = params => {
                            if (params && params.name === 'notifications') {
                                return Promise.resolve({state: 'default', onchange: null});
                            }
                            return originalQuery.call(navigator.permissions, params);
                        };
                    }
                })();
                """
            )
            page._liepin_stealth_applied = True
        except Exception:
            pass

    def _is_context_alive_locked(self) -> bool:
        """Check whether the persistent context is still usable."""
        if self._context is None:
            return False
        try:
            pages = self._context.pages
            if not pages:
                return False
            # If every page is closed, the browser was likely killed manually
            for page in pages:
                if not page.is_closed():
                    return True
            return False
        except Exception:
            return False

    def _launch_locked(self) -> LiepinBrowserState:
        """Launch the persistent browser inside the worker thread."""
        if sync_playwright is None:
            raise PlaywrightNotInstalledError(
                "未安装 Playwright，请先安装 playwright 并执行 playwright install chromium"
            )

        if self._context is not None:
            if self._is_context_alive_locked():
                return self._get_state_locked()
            # Browser was closed externally; don't call close() here because
            # it can block for 30s if the browser process is already dead.
            self._context = None
            self._page = None

        # Ensure any previous playwright instance is fully stopped before
        # starting a new one, otherwise sync_playwright().start() can fail
        # with "using Playwright Sync API inside the asyncio loop".
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        profile_dir = self.get_profile_dir()
        os.makedirs(profile_dir, exist_ok=True)

        if getattr(sys, "frozen", False):
            self._configure_playwright_browser_path()

        self._playwright = sync_playwright().start()
        browser_type = self._playwright.chromium
        default_launch_kwargs = {
            "user_data_dir": profile_dir,
            "headless": self.config_manager.config.liepin_browser_headless,
            "viewport": {"width": 1440, "height": 960},
            "args": [
                "--start-maximized",
                "--window-size=1920,1080",
                "--force-device-scale-factor=1",
            ],
        }
        launch_kwargs = dict(default_launch_kwargs)

        preferred_channel = self.config_manager.config.liepin_browser_channel
        detected_channel = ""
        if getattr(sys, "frozen", False):
            detected_channel, _ = self._find_system_browser_executable()
        logger.warning(
            "Liepin browser launch prepare: frozen=%s, preferred_channel=%s, detected_channel=%s, playwright_browsers_path=%s, profile_dir=%s",
            getattr(sys, "frozen", False),
            preferred_channel,
            detected_channel or "",
            os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
            profile_dir,
        )
        if detected_channel:
            launch_kwargs["channel"] = detected_channel
        elif preferred_channel in ("chrome", "msedge"):
            launch_kwargs["channel"] = preferred_channel

        try:
            logger.warning(
                "Liepin browser launch mode: channel/default, launch_kwargs=%s",
                {
                    key: value
                    for key, value in launch_kwargs.items()
                    if key != "user_data_dir"
                },
            )
            self._context = browser_type.launch_persistent_context(**launch_kwargs)
        except Exception:
            logger.exception(
                "Liepin browser launch fallback triggered, retrying with pure playwright chromium"
            )
            self._context = browser_type.launch_persistent_context(
                **default_launch_kwargs
            )
        # Auto-dismiss dialogs so accidental clicks on filter widgets don't hang the worker
        try:
            self._context.on("dialog", lambda dialog: dialog.dismiss())
        except Exception:
            pass

        # Hide basic automation flags (P0 anti-detection)
        try:
            self._context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                """
            )
        except Exception:
            pass

        actual_browser = launch_kwargs.get("channel") or "playwright-chromium"
        logger.warning(
            "Liepin browser launch success: actual_browser=%s, current_url=%s",
            actual_browser,
            (
                self._pick_best_page(list(self._context.pages)).url
                if self._context and self._context.pages
                else ""
            ),
        )
        self._page = self._pick_best_page(list(self._context.pages))
        if self._page is None:
            self._page = self._context.new_page()
        self._apply_stealth_locked(self._page)
        return self._get_state_locked()

    def _ensure_page_locked(self) -> Page:
        """Return an active page inside the worker thread."""
        if self._context is None or self._page is None:
            self._launch_locked()
        page = self._sync_active_page()
        if page is None and self._context is not None:
            self._page = self._context.new_page()
            page = self._page
        self._apply_stealth_locked(page)
        return page

    @staticmethod
    def _is_search_page_url(url: str) -> bool:
        """Return whether a URL looks like the Liepin search workspace."""
        normalized = (url or "").lower()
        return (
            "h.liepin.com/search/getconditionitem" in normalized
            or "h.liepin.com/search" in normalized
            or "liepin.com/zhaopin" in normalized
        )

    def _get_state_locked(self) -> LiepinBrowserState:
        """Return browser state from inside the worker thread."""
        page = self._sync_active_page()
        return LiepinBrowserState(
            profile_dir=self.get_profile_dir(),
            channel=self.config_manager.config.liepin_browser_channel,
            headless=self.config_manager.config.liepin_browser_headless,
            is_running=self._context is not None,
            logged_in=self._is_logged_in_locked(),
            current_url=page.url if page else "",
        )

    def get_state(self) -> LiepinBrowserState:
        """Return the current browser session state."""
        return self._run_on_worker(self._get_state_locked)

    def launch(self) -> LiepinBrowserState:
        """Launch a persistent Playwright browser context."""
        return self._run_on_worker(self._launch_locked)

    def ensure_page(self) -> Page:
        """Return an active page, launching the browser if needed."""
        return self._run_on_worker(self._ensure_page_locked)

    def new_page(self) -> Page:
        """Create a new page on the dedicated worker thread."""

        def _new_page_locked() -> Page:
            if self._context is None:
                self._launch_locked()
            page = self._context.new_page()
            self._page = page
            self._apply_stealth_locked(page)
            return page

        return self._run_on_worker(_new_page_locked)

    def set_active_page(self, page: Page) -> None:
        """Update the internal active page pointer (useful after closing transient tabs)."""
        self._page = page

    def run_with_page(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute a callback with the active page on the worker thread."""

        def _invoke():
            page = self._ensure_page_locked()
            return func(page, *args, **kwargs)

        return self._run_on_worker(_invoke)

    def export_debug_snapshot(self, reason: str = "manual") -> str:
        """Export the current page structure for selector debugging.

        Enhanced version includes pagination info, candidate cards, viewport details,
        and error diagnostics to help troubleshoot page navigation issues.
        """
        if not getattr(self.config_manager.config, "debug_snapshots_enabled", False):
            logger.warning(
                "Liepin debug snapshot skipped because debug_snapshots_enabled=false reason=%s",
                reason,
            )
            return ""

        def _export(page):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", reason).strip("_") or "snapshot"
            base_name = "{}_{}".format(timestamp, slug)
            debug_dir = self.get_debug_dir()
            html_path = os.path.join(debug_dir, base_name + ".html")
            meta_path = os.path.join(debug_dir, base_name + ".txt")

            html = page.content()
            title = page.title()
            url = page.url or ""

            # Enhanced diagnostics data
            diagnostics = page.evaluate(
                r"""
                () => {
                  const collect = (selector) => Array.from(document.querySelectorAll(selector)).slice(0, 80).map((el, index) => ({
                    index,
                    tag: el.tagName,
                    id: el.id || '',
                    name: el.getAttribute('name') || '',
                    type: el.getAttribute('type') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    role: el.getAttribute('role') || '',
                    href: el.getAttribute('href') || '',
                    text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200),
                    className: (el.className || '').toString().replace(/\s+/g, ' ').trim().slice(0, 240),
                    rectTop: Math.round((el.getBoundingClientRect && el.getBoundingClientRect().top) || 0),
                    rectLeft: Math.round((el.getBoundingClientRect && el.getBoundingClientRect().left) || 0),
                  }));
                  
                  // Pagination analysis
                  const paginationInfo = (() => {
                    const pagination = document.querySelector('.ant-pagination, .pagination, [class*="pagination"]');
                    if (!pagination) return { found: false };
                    
                    const items = Array.from(pagination.querySelectorAll('li, .page-item, [class*="page"]'));
                    const activeItem = pagination.querySelector('.ant-pagination-item-active, .active, .current');
                    const nextBtn = pagination.querySelector('.ant-pagination-next, [title="下一页"], .next-page');
                    const prevBtn = pagination.querySelector('.ant-pagination-prev, [title="上一页"], .prev-page');
                    
                    return {
                      found: true,
                      totalItems: items.length,
                      activePage: activeItem ? (activeItem.innerText || activeItem.textContent || '').trim() : 'N/A',
                      hasNext: !!nextBtn && !nextBtn.classList.contains('ant-pagination-disabled'),
                      hasPrev: !!prevBtn && !prevBtn.classList.contains('ant-pagination-disabled'),
                      paginationHTML: pagination.outerHTML.slice(0, 2000),
                    };
                  })();
                  
                  // Candidate cards analysis
                  const candidateInfo = (() => {
                    const selectors = [
                      '[data-selector="jobseeker-item"]',
                      '.sojob-item-main',
                      '.candidate-card',
                      '.resume-list-item',
                      '.ant-list-item',
                      '.resume-item',
                      '[class*="resume-item"]',
                      '[class*="candidate"]',
                    ];
                    
                    for (const selector of selectors) {
                      const cards = document.querySelectorAll(selector);
                      if (cards.length > 0) {
                        return {
                          selector: selector,
                          count: cards.length,
                          firstCardHTML: cards[0] ? cards[0].outerHTML.slice(0, 1000) : 'N/A',
                          visibleCards: Array.from(cards).filter(c => {
                            const rect = c.getBoundingClientRect();
                            return rect.top >= 0 && rect.top <= window.innerHeight;
                          }).length,
                        };
                      }
                    }
                    return { found: false, count: 0 };
                  })();
                  
                  // Viewport and scroll info
                  const viewportInfo = {
                    width: window.innerWidth,
                    height: window.innerHeight,
                    scrollX: window.scrollX,
                    scrollY: window.scrollY,
                    documentHeight: document.documentElement.scrollHeight,
                  };
                  
                  // Check for error messages or loading states
                  const errorInfo = (() => {
                    const errorElements = document.querySelectorAll('.ant-empty, .error-message, [class*="error"], [class*="empty"]');
                    const loadingElements = document.querySelectorAll('.ant-spin, .loading, [class*="loading"]');
                    return {
                      errorCount: errorElements.length,
                      errors: Array.from(errorElements).slice(0, 3).map(e => e.innerText.slice(0, 200)),
                      loadingCount: loadingElements.length,
                    };
                  })();
                  
                  return {
                    inputs: collect('input, textarea, [contenteditable="true"]'),
                    buttons: collect('button, [role="button"], input[type="button"], input[type="submit"]'),
                    forms: collect('form'),
                    iframes: collect('iframe'),
                    pagination: paginationInfo,
                    candidates: candidateInfo,
                    viewport: viewportInfo,
                    errors: errorInfo,
                  };
                }
                """
            )

            with open(html_path, "w", encoding="utf-8") as html_file:
                html_file.write(html)

            with open(meta_path, "w", encoding="utf-8") as meta_file:
                # Basic info
                meta_file.write("URL: {}\n".format(url))
                meta_file.write("TITLE: {}\n".format(title))
                meta_file.write("TIMESTAMP: {}\n".format(timestamp))
                meta_file.write("REASON: {}\n\n".format(reason))

                # Viewport info
                viewport = diagnostics.get("viewport", {})
                meta_file.write("[VIEWPORT]\n")
                meta_file.write(
                    "Window Size: {}x{}\n".format(
                        viewport.get("width", "N/A"), viewport.get("height", "N/A")
                    )
                )
                meta_file.write(
                    "Scroll Position: x={}, y={}\n".format(
                        viewport.get("scrollX", "N/A"), viewport.get("scrollY", "N/A")
                    )
                )
                meta_file.write(
                    "Document Height: {}\n\n".format(
                        viewport.get("documentHeight", "N/A")
                    )
                )

                # Pagination info
                pagination = diagnostics.get("pagination", {})
                meta_file.write("[PAGINATION]\n")
                if pagination.get("found"):
                    meta_file.write("Status: FOUND\n")
                    meta_file.write(
                        "Total Items: {}\n".format(pagination.get("totalItems", "N/A"))
                    )
                    meta_file.write(
                        "Current Page: {}\n".format(pagination.get("activePage", "N/A"))
                    )
                    meta_file.write(
                        "Has Next Page: {}\n".format(pagination.get("hasNext", "N/A"))
                    )
                    meta_file.write(
                        "Has Prev Page: {}\n".format(pagination.get("hasPrev", "N/A"))
                    )
                    meta_file.write(
                        "HTML Snippet:\n{}\n".format(
                            pagination.get("paginationHTML", "N/A")
                        )
                    )
                else:
                    meta_file.write("Status: NOT FOUND\n")
                meta_file.write("\n")

                # Candidates info
                candidates = diagnostics.get("candidates", {})
                meta_file.write("[CANDIDATES]\n")
                if candidates.get("found") is not False:
                    meta_file.write("Status: FOUND\n")
                    meta_file.write(
                        "Selector Used: {}\n".format(candidates.get("selector", "N/A"))
                    )
                    meta_file.write(
                        "Total Count: {}\n".format(candidates.get("count", "N/A"))
                    )
                    meta_file.write(
                        "Visible in Viewport: {}\n".format(
                            candidates.get("visibleCards", "N/A")
                        )
                    )
                    meta_file.write(
                        "First Card HTML:\n{}\n".format(
                            candidates.get("firstCardHTML", "N/A")
                        )
                    )
                else:
                    meta_file.write("Status: NOT FOUND\n")
                meta_file.write("\n")

                # Error/Loading states
                errors = diagnostics.get("errors", {})
                meta_file.write("[ERRORS & LOADING]\n")
                meta_file.write(
                    "Error Elements: {}\n".format(errors.get("errorCount", 0))
                )
                if errors.get("errors"):
                    for i, err in enumerate(errors.get("errors", [])):
                        meta_file.write("  Error {}: {}\n".format(i + 1, err))
                meta_file.write(
                    "Loading Elements: {}\n".format(errors.get("loadingCount", 0))
                )
                meta_file.write("\n")

                # Standard interactive elements
                for section in ("inputs", "buttons", "forms", "iframes"):
                    meta_file.write("[{}]\n".format(section.upper()))
                    for item in diagnostics.get(section, []):
                        meta_file.write(
                            "{tag} | top={rectTop} | left={rectLeft} | id={id} | name={name} | type={type} | placeholder={placeholder} | role={role} | href={href} | class={className} | text={text}\n".format(
                                **item
                            )
                        )
                    meta_file.write("\n")

            return meta_path

        return self.run_with_page(_export)

    def open_home(self) -> LiepinBrowserState:
        """Navigate to the Liepin home page."""

        def _open_home_locked():
            page = self._ensure_page_locked()
            page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            return self._get_state_locked()

        return self._run_on_worker(_open_home_locked)

    def open_search_page(self) -> LiepinBrowserState:
        """Navigate to the Liepin search page."""

        def _open_search_locked():
            page = self._ensure_page_locked()
            current_url = page.url or ""
            if not self._is_search_page_url(current_url):
                try:
                    page.goto(
                        self.SEARCH_URL, wait_until="domcontentloaded", timeout=15000
                    )
                except Exception:
                    # Liepin may redirect immediately after navigation; as long as
                    # we land on a valid search page, treat it as success.
                    self._sync_active_page()
                    current_url = (self._page.url if self._page else "") or ""
                    if not current_url or current_url.lower() == "about:blank":
                        try:
                            page.goto(
                                self.LOGIN_URL,
                                wait_until="domcontentloaded",
                                timeout=15000,
                            )
                        except Exception:
                            pass
            self._sync_active_page()
            return self._get_state_locked()

        return self._run_on_worker(_open_search_locked)

    def _is_logged_in_locked(self) -> bool:
        """Best-effort login state detection inside the worker thread."""
        page = self._sync_active_page()
        if page is None:
            return False

        try:
            url = (page.url or "").lower()
            if "login" in url or "passport" in url:
                return False
            if self._is_search_page_url(url):
                return True

            text = page.locator("body").inner_text(timeout=3000)
        except Error:
            return False
        except Exception:
            return False

        logged_in_markers = (
            "退出登录",
            "安全退出",
            "个人中心",
            "我的主页",
            "我的简历",
            "我的职位",
            "消息",
            "招聘官",
            "找人",
            "找简历",
            "人才管理",
        )
        login_markers = (
            "登录",
            "注册",
            "立即登录",
        )

        if any(marker in text for marker in logged_in_markers):
            return True
        if any(marker in text for marker in login_markers):
            return False
        return False

    def is_logged_in(self) -> bool:
        """Best-effort login state detection for Liepin.

        This first implementation intentionally uses conservative heuristics and
        will be refined against the live site during integration.
        """
        return self._run_on_worker(self._is_logged_in_locked)

    def ensure_logged_in(self) -> LiepinBrowserState:
        """Raise if the browser is not currently logged in to Liepin."""
        state = self.get_state()
        if state.logged_in:
            return state
        raise LiepinLoginRequiredError("猎聘当前未登录，请先在浏览器中手动完成登录")

    def close_browser(self) -> None:
        """Close only the browser context without killing the worker thread."""

        def _close_browser_locked() -> None:
            if self._context is not None:
                # Use a short timeout to avoid blocking when the browser
                # process is already dead.
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._page = None

        if self._thread and self._thread.is_alive():
            self._run_on_worker(_close_browser_locked)
        else:
            self._context = None
            self._playwright = None
            self._page = None

    def close(self) -> None:
        """Close the persistent browser context and Playwright runtime."""

        def _close_locked() -> None:
            if self._context is not None:
                self._context.close()
                self._context = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
            self._page = None

        if self._thread and self._thread.is_alive():
            self._run_on_worker(_close_locked)
            self._task_queue.put(None)
            self._thread.join(timeout=3.0)
        self._thread = None
        self._thread_id = None
