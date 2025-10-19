import os
import sys
import time
import random
import asyncio
import json
import math
import traceback
import pytesseract
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from tabulate import tabulate

# ======================== 多网站配置 ========================
SITE_CREDENTIALS = {
    'linux_do': {
        'username': os.getenv('LINUXDO_USERNAME'),
        'password': os.getenv('LINUXDO_PASSWORD')
    },
    'idcflare': {
        'username': os.getenv('IDCFLARE_USERNAME'),
        'password': os.getenv('IDCFLARE_PASSWORD')
    }
}

HEADLESS_MODE = os.getenv('HEADLESS', 'true').lower() == 'true'

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json", 
        'session_file': "session_data_linux_do.json",
        'final_status_file': "final_status_linux_do.json"
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json",
        'session_file': "session_data_idcflare.json", 
        'final_status_file': "final_status_idcflare.json"
    }
]

PAGE_TIMEOUT = 180000
RETRY_TIMES = 2

# ======================== 增强的反检测配置 ========================
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864},
]

# ======================== 缓存管理器 ========================
class EnhancedCacheManager:
    @staticmethod
    def get_cache_path(file_name):
        return file_name

    @staticmethod
    def load_cache(file_name, max_age_hours=None):
        path = EnhancedCacheManager.get_cache_path(file_name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                
                if 'cache_timestamp' in data:
                    cache_time = datetime.fromisoformat(data['cache_timestamp'])
                    age_hours = (datetime.now() - cache_time).total_seconds() / 3600
                    age_status = "较新" if age_hours < 12 else "较旧"
                    logger.info(f"📦 加载缓存 {file_name} (年龄: {age_hours:.1f}小时, {age_status})")
                
                if 'data' in data:
                    return data['data']
                else:
                    return data
            except Exception as e:
                logger.warning(f"缓存加载失败 {path}: {str(e)}")
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        path = EnhancedCacheManager.get_cache_path(file_name)
        try:
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '1.3'
            }
            
            with open(path, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ 缓存已保存到 {path}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {path}: {str(e)}")
            return False

    @staticmethod
    def load_session_data(site_name):
        file_name = f"session_data_{site_name}.json"
        return EnhancedCacheManager.load_cache(file_name)

    @staticmethod
    def save_session_data(data, site_name):
        file_name = f"session_data_{site_name}.json"
        return EnhancedCacheManager.save_cache(data, file_name)

    @staticmethod
    def load_cf_cookies(site_name):
        file_name = f"cf_cookies_{site_name}.json"
        return EnhancedCacheManager.load_cache(file_name)

    @staticmethod
    def save_cf_cookies(data, site_name):
        file_name = f"cf_cookies_{site_name}.json"
        return EnhancedCacheManager.save_cache(data, file_name)

    @staticmethod
    def load_browser_state(site_name):
        file_name = f"browser_state_{site_name}.json"
        return EnhancedCacheManager.load_cache(file_name)

    @staticmethod
    def save_browser_state(data, site_name):
        file_name = f"browser_state_{site_name}.json"
        return EnhancedCacheManager.save_cache(data, file_name)

    @staticmethod
    def load_final_status(site_name):
        file_name = f"final_status_{site_name}.json"
        return EnhancedCacheManager.load_cache(file_name)

    @staticmethod
    def save_final_status(data, site_name):
        file_name = f"final_status_{site_name}.json"
        return EnhancedCacheManager.save_cache(data, file_name)

# ======================== 终极Cloudflare处理器 ========================
class UltimateCloudflareHandler:
    @staticmethod
    async def handle_cloudflare(page, site_config, max_attempts=8, timeout=180):
        """修复的Cloudflare处理 - 使用正确的site_config"""
        domain = site_config['base_url'].replace('https://', '')
        start_time = time.time()
        logger.info(f"🛡️ 开始处理 {domain} Cloudflare验证")
        
        # 1. 首先检查缓存中的Cloudflare cookies
        cached_cf_valid = await UltimateCloudflareHandler.is_cached_cf_valid(site_config['name'])
        if cached_cf_valid:
            logger.success(f"✅ 检测到有效的缓存Cloudflare cookie，尝试直接绕过验证")
            # 尝试直接访问/latest页面
            try:
                await page.goto(site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                await asyncio.sleep(5)
                
                page_title = await page.title()
                if page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ 使用缓存成功绕过Cloudflare验证")
                    return True
            except Exception as e:
                logger.warning(f"使用缓存绕过失败: {str(e)}")
        
        # 2. 如果没有有效缓存，进行完整验证
        logger.info(f"🔄 开始完整Cloudflare验证流程")
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = await page.title()
                
                logger.info(f"🔍 检查页面状态 - URL: {current_url}, 标题: {page_title}")
                
                # 检查是否有有效的cf_clearance cookie
                cf_valid = await UltimateCloudflareHandler.is_cf_clearance_valid(page.context, domain)
                
                if cf_valid:
                    logger.success(f"✅ 检测到有效的 cf_clearance cookie")
                    
                    # 如果cookie有效但页面卡住，尝试强制解决方案
                    if page_title == "请稍候…" or "Checking your browser" in await page.content():
                        logger.info("🔄 Cookie有效但页面卡住，尝试强制解决方案")
                        
                        # 尝试直接访问其他路径
                        try:
                            await page.goto(site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                            await asyncio.sleep(5)
                            
                            new_title = await page.title()
                            if new_title != "请稍候…":
                                logger.success("✅ 通过访问/latest页面成功绕过卡住的主页")
                                return True
                        except Exception as e:
                            logger.warning(f"访问/latest页面失败: {str(e)}")
                    
                    else:
                        logger.success(f"✅ {domain} 页面已正常加载")
                        return True
                else:
                    # 检查页面是否已经正常加载（即使没有cf_clearance cookie）
                    if page_title != "请稍候…" and "Checking" not in page_title:
                        logger.success(f"✅ {domain} 页面已正常加载，Cloudflare验证通过")
                        return True
                    
                    # 如果没有有效的cookie，继续等待验证
                    wait_time = random.uniform(8, 15)
                    logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                    await asyncio.sleep(wait_time)
                    
                    # 每次等待后都检查cookie是否变得有效
                    cf_valid_after_wait = await UltimateCloudflareHandler.is_cf_clearance_valid(page.context, domain)
                    if cf_valid_after_wait:
                        logger.success(f"✅ 等待后检测到有效的 cf_clearance cookie，提前结束验证")
                        return True
                    
                    # 偶尔刷新页面
                    if attempt % 3 == 0:
                        logger.info("🔄 刷新页面")
                        await page.reload(wait_until='networkidle', timeout=60000)
                        await asyncio.sleep(3)
                
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ {domain} Cloudflare处理超时")
                    break
                    
            except Exception as e:
                logger.error(f"{domain} Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                await asyncio.sleep(10)
        
        # 最终检查 - 更宽松的判断条件
        final_cf_valid = await UltimateCloudflareHandler.is_cf_clearance_valid(page.context, domain)
        page_title = await page.title()
        
        if final_cf_valid or (page_title != "请稍候…" and "Checking" not in page_title):
            logger.success(f"✅ 最终验证: {domain} Cloudflare验证通过")
            return True
        else:
            logger.warning(f"⚠️ 最终验证: {domain} Cloudflare验证未完全通过，但继续后续流程")
            return True  # 即使没有完全通过也继续后续流程

    @staticmethod
    async def is_cached_cf_valid(site_name):
        """检查缓存中的Cloudflare cookie是否有效"""
        try:
            cf_cookies = EnhancedCacheManager.load_cf_cookies(site_name)
            if not cf_cookies:
                logger.info(f"📭 {site_name} 无Cloudflare缓存")
                return False
            
            # 检查是否有cf_clearance cookie且未过期
            for cookie in cf_cookies:
                if cookie.get('name') == 'cf_clearance':
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > time.time():
                        logger.info(f"✅ {site_name} 缓存中的Cloudflare cookie有效")
                        return True
            
            logger.info(f"📭 {site_name} 缓存中的Cloudflare cookie已过期")
            return False
        except Exception as e:
            logger.warning(f"检查缓存cookie失败: {str(e)}")
            return False

    @staticmethod
    async def is_cf_clearance_valid(context, domain):
        try:
            cookies = await context.cookies()
            for cookie in cookies:
                if cookie.get('name') == 'cf_clearance' and domain in cookie.get('domain', ''):
                    # 检查cookie是否过期
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > time.time():
                        return True
            return False
        except Exception:
            return False

# ======================== 浏览器管理器 ========================
class BrowserManager:
    USER_AGENTS = USER_AGENTS
    VIEWPORT_SIZES = VIEWPORT_SIZES

    @staticmethod
    async def init_browser():
        playwright = await async_playwright().start()
        
        user_agent = random.choice(BrowserManager.USER_AGENTS)
        viewport = random.choice(BrowserManager.VIEWPORT_SIZES)
        
        logger.info(f"使用 User-Agent: {user_agent[:50]}...")
        logger.info(f"使用视口大小: {viewport}")

        browser_args = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            f'--window-size={viewport["width"]},{viewport["height"]}',
            '--lang=zh-CN,zh;q=0.9,en;q=0.8',
            '--disable-features=VizDisplayCompositor',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
        ]

        browser = await playwright.chromium.launch(
            headless=HEADLESS_MODE,
            args=browser_args
        )
        
        return browser, playwright

    @staticmethod
    async def create_context(browser, site_name):
        # 先检查是否有浏览器状态缓存
        has_browser_state = EnhancedCacheManager.load_browser_state(site_name) is not None
        has_cf_cookies = EnhancedCacheManager.load_cf_cookies(site_name) is not None
        
        logger.info(f"🔍 {site_name} 缓存状态 - 浏览器状态: {'✅' if has_browser_state else '❌'}, Cloudflare Cookies: {'✅' if has_cf_cookies else '❌'}")
        
        storage_state = EnhancedCacheManager.load_browser_state(site_name)
        
        user_agent = random.choice(BrowserManager.USER_AGENTS)
        viewport = random.choice(BrowserManager.VIEWPORT_SIZES)
        
        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            storage_state=storage_state,
            ignore_https_errors=True,
            java_script_enabled=True,
        )
        
        await BrowserManager.load_caches_into_context(context, site_name)
        await context.add_init_script(BrowserManager.get_anti_detection_script())
        
        return context

    @staticmethod
    async def load_caches_into_context(context, site_name):
        try:
            cf_cookies = EnhancedCacheManager.load_cf_cookies(site_name)
            if cf_cookies:
                # 过滤掉过期的cookie
                current_time = time.time()
                valid_cookies = []
                for cookie in cf_cookies:
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > current_time:
                        valid_cookies.append(cookie)
                
                if valid_cookies:
                    await context.add_cookies(valid_cookies)
                    logger.info(f"✅ 已从缓存加载 {len(valid_cookies)} 个 {site_name} Cloudflare cookies")
                else:
                    logger.warning(f"⚠️ {site_name} 所有缓存的Cloudflare cookies已过期")
        except Exception as e:
            logger.error(f"❌ 加载 {site_name} 缓存到上下文时出错: {e}")

    @staticmethod
    def get_anti_detection_script():
        return """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {isInstalled: false} };
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        """

# ======================== 主自动化类 ========================
class LinuxDoAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.retry_count = 0
        self.session_data = EnhancedCacheManager.load_session_data(site_config['name']) or {}
        self.cf_passed = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.domain = site_config['base_url'].replace('https://', '')

    async def run_for_site(self, browser, playwright):
        self.browser = browser
        self.playwright = playwright
        
        if not self.credentials.get('username') or not self.credentials.get('password'):
            logger.error(f"❌ {self.site_config['name']} 的用户名或密码未设置，跳过该站点")
            return False
            
        try:
            self.context = await BrowserManager.create_context(browser, self.site_config['name'])
            logger.success(f"✅ {self.site_config['name']} 浏览器环境初始化完成")

            self.page = await self.context.new_page()
            self.page.set_default_timeout(PAGE_TIMEOUT)
            self.page.set_default_navigation_timeout(PAGE_TIMEOUT)

            while self.retry_count <= RETRY_TIMES:
                try:
                    # ========== 缓存优先的核心流程 ==========
                    logger.info(f"🔍 开始 {self.site_config['name']} 缓存优先验证流程")
                    
                    # 1. 首先尝试使用缓存直接访问
                    cache_success = await self.try_cache_first_approach()
                    if cache_success:
                        logger.success(f"✅ {self.site_config['name']} 缓存优先流程成功")
                        self.is_logged_in = True
                        self.cf_passed = True
                    else:
                        # 2. 缓存失败，进行完整验证流程
                        logger.warning(f"⚠️ {self.site_config['name']} 缓存优先流程失败，开始完整验证")
                        full_success = await self.full_verification_process()
                        self.is_logged_in = full_success
                    
                    # ========== 核心流程结束 ==========

                    if self.is_logged_in:
                        logger.success(f"✅ {self.site_config['name']} 登录成功，开始执行后续任务")
                        await self.save_all_caches()
                        await self.browse_topics()
                        await self.save_final_status(success=True)
                        break
                    else:
                        logger.error(f"❌ {self.site_config['name']} 登录失败")
                        
                        # 智能重试策略
                        if self.retry_count == 0:
                            if self.cf_passed and not self.is_logged_in:
                                logger.info(f"🔄 {self.site_config['name']} Cloudflare通过但登录失败，只清除登录缓存")
                                await self.clear_login_caches_only()
                            else:
                                logger.info(f"🔄 {self.site_config['name']} 清除所有缓存并重试")
                                await self.clear_caches()
                        
                        self.retry_count += 1
                        if self.retry_count <= RETRY_TIMES:
                            wait_time = 10 + self.retry_count * 5
                            logger.warning(f"将在 {wait_time} 秒后重试 ({self.retry_count}/{RETRY_TIMES})")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ {self.site_config['name']} 最大重试次数耗尽，终止脚本")
                            await self.save_final_status(success=False)
                            return False

                except Exception as e:
                    logger.error(f"{self.site_config['name']} 当前尝试失败: {str(e)}")
                    
                    if self.retry_count == 0:
                        logger.info(f"🔄 {self.site_config['name']} 清除缓存并重试")
                        await self.clear_caches()
                    
                    self.retry_count += 1
                    if self.retry_count <= RETRY_TIMES:
                        wait_time = 10 + self.retry_count * 5
                        logger.warning(f"将在 {wait_time} 秒后重试 ({self.retry_count}/{RETRY_TIMES})")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"❌ {self.site_config['name']} 最大重试次数耗尽，终止脚本")
                        await self.save_final_status(success=False)
                        return False

            return True

        except Exception as e:
            logger.critical(f"{self.site_config['name']} 脚本执行异常: {str(e)}")
            await self.save_final_status(success=False)
            traceback.print_exc()
            return False
        finally:
            await self.close_context()

    async def try_cache_first_approach(self):
        """缓存优先的验证流程"""
        try:
            logger.info(f"🔄 尝试缓存优先流程")
            
            # 1. 检查是否有有效的Cloudflare缓存
            cf_cache_valid = await UltimateCloudflareHandler.is_cached_cf_valid(self.site_config['name'])
            
            if cf_cache_valid:
                logger.info(f"✅ 检测到有效的Cloudflare缓存，尝试直接访问")
                # 直接访问/latest页面
                await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                await asyncio.sleep(5)
                
                # 检查页面状态
                page_title = await self.page.title()
                if page_title == "请稍候…" or "Checking" in page_title:
                    logger.warning("⚠️ 页面仍然卡住，但cookie有效，继续检查登录状态")
                
                # 检查登录状态
                login_status = await self.enhanced_check_login_status()
                if login_status:
                    logger.success(f"✅ 缓存优先流程成功 - 已登录")
                    return True
                else:
                    logger.warning(f"⚠️ Cloudflare缓存有效但未登录，尝试登录")
                    login_success = await self.optimized_login()
                    return login_success
            else:
                logger.info(f"📭 无有效Cloudflare缓存")
                return False
                
        except Exception as e:
            logger.error(f"缓存优先流程异常: {str(e)}")
            return False

    async def full_verification_process(self):
        """完整的验证流程"""
        try:
            logger.info(f"🔄 开始完整验证流程")
            
            # 1. 进行Cloudflare验证
            await self.page.goto(self.site_config['base_url'], wait_until='networkidle', timeout=120000)
            
            self.cf_passed = await UltimateCloudflareHandler.handle_cloudflare(
                self.page, self.site_config, max_attempts=8, timeout=180
            )
            
            if self.cf_passed:
                logger.success(f"✅ {self.site_config['name']} Cloudflare验证通过")
            else:
                logger.warning(f"⚠️ {self.site_config['name']} Cloudflare验证未通过，但继续尝试登录")
            
            # 2. 检查登录状态
            cached_login_success = await self.enhanced_check_login_status()
            if cached_login_success:
                logger.success(f"✅ {self.site_config['name']} 缓存登录成功")
                return True
            else:
                logger.warning(f"⚠️ 需要重新登录")
                login_success = await self.optimized_login()
                return login_success
                
        except Exception as e:
            logger.error(f"完整验证流程异常: {str(e)}")
            return False

    async def enhanced_check_login_status(self):
        """增强版登录状态检查 - 包含完整的用户名验证"""
        try:
            current_url = self.page.url
            page_title = await self.page.title()
            logger.info(f"🔍 检查登录状态 - URL: {current_url}, 标题: {page_title}")
            
            # 如果页面卡在Cloudflare验证，但cookie有效，尝试绕过
            if page_title == "请稍候…":
                cf_valid = await UltimateCloudflareHandler.is_cf_clearance_valid(self.page.context, self.domain)
                if cf_valid:
                    logger.info("🔄 页面卡住但Cloudflare cookie有效，尝试访问/latest页面")
                    await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                    await asyncio.sleep(5)
                    # 重新检查状态
                    current_url = self.page.url
                    page_title = await self.page.title()
            
            # 检查用户相关元素（登录成功的标志）
            user_indicators = [
                '#current-user',
                '#toggle-current-user', 
                '.header-dropdown-toggle.current-user',
                'img.avatar',
                '.user-menu',
                '[data-user-menu]'
            ]
            
            user_element_found = False
            for selector in user_indicators:
                try:
                    user_elem = await self.page.query_selector(selector)
                    if user_elem and await user_elem.is_visible():
                        logger.success(f"✅ 检测到用户元素: {selector}")
                        user_element_found = True
                        break
                except Exception:
                    continue
            
            if user_element_found:
                # 🔥 完整的用户名验证流程
                username = self.credentials['username']
                username_verified = False
                
                # 方法1: 页面内容检查 - 在页面HTML中搜索用户名
                page_content = await self.page.content()
                if username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面内容中找到用户名: {username}")
                    username_verified = True
                    return True
                
                # 方法2: 用户菜单点击 - 点击用户头像/菜单查看详细信息
                if not username_verified:
                    try:
                        logger.info("🔄 尝试点击用户菜单验证用户名")
                        user_click_selectors = ['img.avatar', '.current-user', '[data-user-menu]', '.header-dropdown-toggle']
                        for selector in user_click_selectors:
                            user_elem = await self.page.query_selector(selector)
                            if user_elem and await user_elem.is_visible():
                                await user_elem.click()
                                await asyncio.sleep(2)
                                
                                # 在展开的菜单中查找用户名
                                user_menu_content = await self.page.content()
                                if username.lower() in user_menu_content.lower():
                                    logger.success(f"✅ 在用户菜单中找到用户名: {username}")
                                    username_verified = True
                                
                                # 点击其他地方关闭菜单
                                await self.page.click('body')
                                await asyncio.sleep(1)
                                break
                    except Exception as e:
                        logger.debug(f"点击用户菜单失败: {str(e)}")
                
                # 方法3: 个人资料页面验证 - 导航到用户个人资料页面确认
                if not username_verified:
                    try:
                        logger.info("🔄 尝试导航到用户个人资料页面验证")
                        profile_url = f"{self.site_config['base_url']}/u/{username}"
                        await self.page.goto(profile_url, wait_until='networkidle', timeout=30000)
                        await asyncio.sleep(3)
                        
                        profile_content = await self.page.content()
                        if username.lower() in profile_content.lower() or "个人资料" in await self.page.title():
                            logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                            username_verified = True
                            
                        # 返回之前的页面
                        await self.page.go_back(wait_until='networkidle')
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.debug(f"导航到个人资料页面失败: {str(e)}")
                
                # 方法4: URL路径检查 - 检查URL中是否包含用户相关路径
                if not username_verified and ('/u/' in current_url or '/users/' in current_url):
                    logger.success("✅ 检测到用户相关URL路径")
                    username_verified = True
                
                # 最终判断
                if username_verified:
                    return True
                else:
                    logger.warning(f"⚠️ 检测到用户元素但无法验证用户名 {username}，默认认为已登录")
                    return True
            
            # 检查登录按钮（未登录的标志）
            login_buttons = [
                '.login-button',
                'button:has-text("登录")',
                'button:has-text("Log In")',
                '.btn.btn-icon-text.login-button'
            ]
            
            for selector in login_buttons:
                try:
                    login_btn = await self.page.query_selector(selector)
                    if login_btn and await login_btn.is_visible():
                        logger.warning(f"❌ 检测到登录按钮: {selector}")
                        return False
                except Exception:
                    continue
            
            # 如果无法确定状态，保存调试信息
            page_content = await self.page.content()
            if "请稍候" not in page_title and "Checking" not in page_title:
                # 页面可能已正常加载但没有明显的登录状态指示
                username = self.credentials['username']
                if username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面内容中找到用户名: {username}")
                    return True
                
                # 检查是否有正常的内容
                if len(page_content) > 1000:
                    logger.success("✅ 页面显示正常内容，可能已登录")
                    return True
            
            logger.warning(f"⚠️ 登录状态不确定，默认认为未登录。页面标题: {page_title}")
            return False
            
        except Exception as e:
            logger.warning(f"{self.site_config['name']} 检查登录状态时出错: {str(e)}")
            return False

    async def optimized_login(self):
        """优化的登录流程"""
        try:
            logger.info(f"🔐 开始 {self.site_config['name']} 优化登录流程")
            
            # 清除可能的旧会话
            await self.page.context.clear_cookies()
            
            # 导航到登录页面
            logger.info(f"🔄 导航到登录页面: {self.site_config['login_url']}")
            await self.page.goto(self.site_config['login_url'], wait_until='networkidle', timeout=90000)
            
            # 等待页面稳定
            await asyncio.sleep(5)
            
            # 等待登录表单
            form_loaded = False
            for i in range(5):
                try:
                    await self.page.wait_for_selector('#login-account-name', timeout=10000)
                    await self.page.wait_for_selector('#login-account-password', timeout=10000)
                    form_loaded = True
                    break
                except:
                    logger.warning(f"登录表单加载失败，重试 {i+1}/5")
                    await asyncio.sleep(3)
            
            if not form_loaded:
                logger.error("❌ 登录表单加载超时")
                return False
            
            # 填写登录信息
            username = self.credentials['username']
            password = self.credentials['password']
            
            logger.info("📝 填写登录信息")
            await self.page.fill('#login-account-name', username)
            await self.page.fill('#login-account-password', password)
            
            await asyncio.sleep(2)
            
            # 点击登录按钮
            login_button_selectors = ['#login-button', 'button[type="submit"]', 'input[type="submit"]']
            clicked = False
            for selector in login_button_selectors:
                try:
                    login_btn = await self.page.query_selector(selector)
                    if login_btn and await login_btn.is_visible():
                        await login_btn.click()
                        clicked = True
                        logger.info(f"✅ 点击登录按钮: {selector}")
                        break
                except:
                    continue
            
            if not clicked:
                logger.error("❌ 找不到可点击的登录按钮")
                return False
            
            # 等待登录结果
            logger.info("⏳ 等待登录结果...")
            await asyncio.sleep(20)
            
            # 检查登录后的页面状态
            current_url = self.page.url
            logger.info(f"登录后URL: {current_url}")
            
            if current_url != self.site_config['login_url']:
                logger.info("✅ 页面已跳转，可能登录成功")
                await asyncio.sleep(5)
                return await self.enhanced_check_login_status()
            
            # 检查错误消息
            error_selectors = ['.alert-error', '.error', '.flash-error', '.alert.alert-error']
            for selector in error_selectors:
                error_elem = await self.page.query_selector(selector)
                if error_elem:
                    error_text = await error_elem.inner_text()
                    logger.error(f"❌ 登录错误: {error_text}")
                    return False
            
            # 如果还在登录页面但没有错误，尝试强制刷新
            logger.warning("⚠️ 仍在登录页面，但没有明显错误，尝试强制刷新并检查状态")
            await self.page.goto(self.site_config['base_url'], wait_until='networkidle', timeout=60000)
            await asyncio.sleep(5)
            
            return await self.enhanced_check_login_status()
                
        except Exception as e:
            logger.error(f"{self.site_config['name']} 登录过程异常: {e}")
            return False

    async def clear_caches(self):
        try:
            cache_files = [
                self.site_config['cf_cookies_file'],
                self.site_config['browser_state_file'],
                self.site_config['session_file'],
                self.site_config['final_status_file']
            ]
            
            for cache_file in cache_files:
                path = EnhancedCacheManager.get_cache_path(cache_file)
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"🗑️ 已清除缓存: {cache_file}")
            
            self.session_data = {}
            logger.info(f"✅ {self.site_config['name']} 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    async def clear_login_caches_only(self):
        """只清除登录相关缓存，保留Cloudflare cookies"""
        try:
            cache_files = [
                self.site_config['browser_state_file'],
                self.site_config['session_file'],
                self.site_config['final_status_file']
            ]
            
            for cache_file in cache_files:
                path = EnhancedCacheManager.get_cache_path(cache_file)
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"🗑️ 已清除缓存: {cache_file}")
            
            self.session_data = {}
            logger.info(f"✅ {self.site_config['name']} 登录缓存已清除，保留Cloudflare cookies")
            
        except Exception as e:
            logger.error(f"清除登录缓存失败: {str(e)}")

    async def save_all_caches(self):
        try:
            await self.save_cf_cookies()
            
            if self.context:
                state = await self.context.storage_state()
                EnhancedCacheManager.save_browser_state(state, self.site_config['name'])
            
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'retry_count': self.retry_count,
                'cf_passed': self.cf_passed,
            })
            EnhancedCacheManager.save_session_data(self.session_data, self.site_config['name'])
            
            logger.info(f"✅ {self.site_config['name']} 所有缓存已保存")
        except Exception as e:
            logger.error(f"{self.site_config['name']} 保存缓存失败: {str(e)}")

    async def save_final_status(self, success=False):
        final_status = {
            'success': success,
            'timestamp': datetime.now().isoformat(),
            'retry_count': self.retry_count,
            'login_status': 'success' if success else 'failed',
            'cf_passed': self.cf_passed,
            'message': '任务执行完成' if success else '任务执行失败',
            'session_data': self.session_data
        }
        EnhancedCacheManager.save_final_status(final_status, self.site_config['name'])

    async def save_cf_cookies(self):
        try:
            all_cookies = await self.context.cookies()
            target_domain = self.site_config['base_url'].replace('https://', '')
            cf_cookies = [
                cookie for cookie in all_cookies 
                if cookie.get('domain', '').endswith(target_domain) and 
                   (cookie.get('name') == 'cf_clearance' or 'cloudflare' in cookie.get('name', ''))
            ]
            
            if cf_cookies:
                EnhancedCacheManager.save_cf_cookies(cf_cookies, self.site_config['name'])
                logger.info(f"✅ {self.site_config['name']} Cloudflare Cookies 已保存: {len(cf_cookies)} 个")
                
        except Exception as e:
            logger.error(f"❌ 保存 {self.site_config['name']} Cloudflare cookies 失败: {e}")

    async def close_context(self):
        try:
            if self.context:
                state = await self.context.storage_state()
                EnhancedCacheManager.save_browser_state(state, self.site_config['name'])
                await self.context.close()
                logger.info(f"✅ {self.site_config['name']} 浏览器上下文已关闭")
                
        except Exception as e:
            logger.debug(f"{self.site_config['name']} 关闭浏览器上下文异常: {str(e)}")

    async def browse_topics(self):
        try:
            logger.info(f"📖 开始 {self.site_config['name']} 主题浏览")
            
            browse_history = self.session_data.get('browse_history', [])
            
            logger.info(f"🔄 导航到最新主题页面: {self.site_config['latest_topics_url']}")
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000, wait_until='networkidle')
            
            # 检查页面内容
            page_content = await self.page.content()
            logger.info(f"📄 页面内容长度: {len(page_content)} 字符")
            
            # 尝试多种选择器
            topic_selectors = ['a.title', '.title a', 'a.topic-title', '.topic-list-item a', 'tr.topic-list-item a.title']
            topic_links = []
            
            for selector in topic_selectors:
                links = await self.page.query_selector_all(selector)
                if links:
                    logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(links)} 个主题链接")
                    topic_links = links
                    break
                else:
                    logger.info(f"❌ 选择器 '{selector}' 未找到主题链接")
            
            if not topic_links:
                logger.warning(f"{self.site_config['name']} 未找到主题链接")
                return
            
            browse_count = min(random.randint(9, 15), len(topic_links))
            selected_topics = random.sample(topic_links, browse_count)
            
            logger.info(f"📚 {self.site_config['name']} 计划浏览 {browse_count} 个主题")
            
            success_count = 0
            for idx, topic in enumerate(selected_topics, 1):
                success = await self.browse_single_topic(topic, idx, browse_count, browse_history)
                if success:
                    success_count += 1
                    
                if idx < browse_count:
                    await asyncio.sleep(random.uniform(5, 10))
            
            self.session_data['browse_history'] = browse_history[-50:]
            self.session_data['last_browse'] = datetime.now().isoformat()
            self.session_data['total_browsed'] = self.session_data.get('total_browsed', 0) + success_count
            
            logger.success(f"✅ {self.site_config['name']} 主题浏览完成: 成功 {success_count} 个主题")

        except Exception as e:
            logger.error(f"{self.site_config['name']} 主题浏览流程失败: {str(e)}")
            traceback.print_exc()

    async def browse_single_topic(self, topic, topic_idx, total_topics, browse_history):
        try:
            title = (await topic.text_content() or "").strip()[:60]
            href = await topic.get_attribute('href')
            
            if not href:
                return False
            
            topic_url = f"{self.site_config['base_url']}{href}" if href.startswith('/') else href
            
            if href in browse_history:
                return False
            
            logger.info(f"🌐 {self.site_config['name']} 浏览主题 {topic_idx}/{total_topics}: {title}")
            
            tab = await self.context.new_page()
            try:
                await tab.goto(topic_url, timeout=45000, wait_until='domcontentloaded')
                await asyncio.sleep(random.uniform(20, 40))
                browse_history.append(href)
                return True
                
            except Exception as e:
                logger.error(f"{self.site_config['name']} 浏览单个主题失败: {str(e)}")
                return False
            finally:
                await tab.close()
                
        except Exception as e:
            logger.error(f"{self.site_config['name']} 准备浏览主题失败: {str(e)}")
            return False

# ======================== 主执行函数 ========================
async def main():
    logger.info("🚀 LinuxDo多站点自动化脚本启动")
    
    browser, playwright = await BrowserManager.init_browser()
    
    try:
        results = []
        
        for site_config in SITES:
            logger.info(f"🎯 开始处理站点: {site_config['name']}")
            
            automator = LinuxDoAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)
            
            results.append({
                'site': site_config['name'],
                'success': success,
                'login_status': automator.is_logged_in,
                'cf_passed': automator.cf_passed,
                'retry_count': automator.retry_count
            })
            
            # 站点间延迟
            if site_config != SITES[-1]:
                delay = random.uniform(10, 20)
                logger.info(f"⏳ 站点间延迟 {delay:.1f} 秒")
                await asyncio.sleep(delay)
        
        # 输出最终结果
        logger.info("📊 所有站点执行结果:")
        table_data = []
        for result in results:
            status_icon = "✅" if result['success'] else "❌"
            login_status = "已登录" if result['login_status'] else "未登录"
            cf_status = "通过" if result['cf_passed'] else "失败"
            table_data.append([
                result['site'], 
                status_icon, 
                login_status, 
                cf_status, 
                result['retry_count']
            ])
        
        print(tabulate(table_data, 
                      headers=['站点', '状态', '登录', 'Cloudflare', '重试次数'],
                      tablefmt='grid'))
        
        success_count = sum(1 for r in results if r['success'])
        logger.success(f"🎉 脚本执行完成: {success_count}/{len(results)} 个站点成功")
        
    except Exception as e:
        logger.critical(f"💥 主执行流程异常: {str(e)}")
        traceback.print_exc()
    finally:
        await browser.close()
        await playwright.stop()
        logger.info("🔚 浏览器已关闭，脚本结束")

if __name__ == "__main__":
    asyncio.run(main())
