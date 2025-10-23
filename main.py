import os
import sys
import time
import random
import asyncio
import json
import math
import traceback
from datetime import datetime, timedelta
from urllib.parse import urljoin
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright, MouseButton
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from tabulate import tabulate

# ======================== 配置常量 ========================
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

# ======================== 反检测配置 ========================
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864},
    {'width': 1440, 'height': 900},
    {'width': 1280, 'height': 720},
]

# ======================== 终极缓存管理器 ========================
class UltimateCacheManager:
    @staticmethod
    def get_file_age_hours(file_path):
        """获取文件年龄（小时）"""
        if not os.path.exists(file_path):
            return None
        file_mtime = os.path.getmtime(file_path)
        current_time = time.time()
        age_hours = (current_time - file_mtime) / 3600
        return age_hours

    @staticmethod
    def load_cache(file_name):
        if os.path.exists(file_name):
            try:
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                
                # 使用文件系统时间而不是缓存内部时间戳
                age_hours = UltimateCacheManager.get_file_age_hours(file_name)
                if age_hours is not None:
                    age_status = "全新" if age_hours < 0.1 else "较新" if age_hours < 6 else "较旧"
                    logger.info(f"📦 加载缓存 {file_name} (年龄: {age_hours:.3f}小时, {age_status})")
                
                # 返回数据部分
                return data.get('data', data)
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        try:
            # 强制更新文件时间戳，确保覆盖旧缓存
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '4.1',  # 版本更新
                'file_created': time.time(),
                'run_id': os.getenv('GITHUB_RUN_ID', 'local')
            }
            
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            # 强制更新文件系统时间戳，确保缓存被正确覆盖
            current_time = time.time()
            os.utime(file_name, (current_time, current_time))
            
            # 验证文件时间戳是否更新
            new_age = UltimateCacheManager.get_file_age_hours(file_name)
            file_size = os.path.getsize(file_name)
            logger.info(f"💾 缓存已保存到 {file_name} (新年龄: {new_age:.3f}小时, 大小: {file_size} 字节)")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    # 站点特定的缓存方法
    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return UltimateCacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return UltimateCacheManager.save_cache(data, file_name)

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    @staticmethod
    async def handle_cloudflare(page, site_config, max_attempts=8, timeout=180):
        domain = site_config['base_url'].replace('https://', '')
        start_time = time.time()
        logger.info(f"🛡️ 开始处理 {domain} Cloudflare验证")
        
        # 检查缓存中的Cloudflare cookies
        cached_cf_valid = await CloudflareHandler.is_cached_cf_valid(site_config['name'])
        if cached_cf_valid:
            logger.success(f"✅ 检测到有效的缓存Cloudflare cookie，尝试直接绕过验证")
            try:
                await page.goto(site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                await asyncio.sleep(5)
                
                page_title = await page.title()
                if page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ 使用缓存成功绕过Cloudflare验证")
                    return True
            except Exception as e:
                logger.warning(f"使用缓存绕过失败: {str(e)}")
        
        # 完整验证流程
        logger.info(f"🔄 开始完整Cloudflare验证流程")
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = await page.title()
                
                # 检查是否有有效的cf_clearance cookie
                cf_valid = await CloudflareHandler.is_cf_clearance_valid(page.context, domain)
                
                if cf_valid:
                    logger.success(f"✅ 检测到有效的 cf_clearance cookie")
                    
                    # 如果cookie有效但页面卡住，尝试强制解决方案
                    if page_title == "请稍候…" or "Checking your browser" in await page.content():
                        logger.info("🔄 Cookie有效但页面卡住，尝试强制解决方案")
                        try:
                            await page.goto(site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                            await asyncio.sleep(5)
                            
                            new_title = await page.title()
                            if new_title != "请稍候…":
                                logger.success("✅ 通过访问/latest页面成功绕过卡住的主页")
                                return True
                        except Exception:
                            logger.warning("访问/latest页面失败")
                    
                    else:
                        logger.success(f"✅ {domain} 页面已正常加载")
                        return True
                else:
                    # 检查页面是否已经正常加载
                    if page_title != "请稍候…" and "Checking" not in page_title:
                        logger.success(f"✅ {domain} 页面已正常加载，Cloudflare验证通过")
                        return True
                    
                    # 等待验证
                    wait_time = random.uniform(8, 15)
                    logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                    await asyncio.sleep(wait_time)
                    
                    # 检查cookie是否变得有效
                    cf_valid_after_wait = await CloudflareHandler.is_cf_clearance_valid(page.context, domain)
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
        
        # 最终检查
        final_cf_valid = await CloudflareHandler.is_cf_clearance_valid(page.context, domain)
        page_title = await page.title()
        
        if final_cf_valid or (page_title != "请稍候…" and "Checking" not in page_title):
            logger.success(f"✅ 最终验证: {domain} Cloudflare验证通过")
            return True
        else:
            logger.warning(f"⚠️ 最终验证: {domain} Cloudflare验证未完全通过，但继续后续流程")
            return True

    @staticmethod
    async def is_cached_cf_valid(site_name):
        try:
            cf_cookies = UltimateCacheManager.load_site_cache(site_name, 'cf_cookies')
            if not cf_cookies:
                return False
            
            # 检查是否有cf_clearance cookie且未过期
            for cookie in cf_cookies:
                if cookie.get('name') == 'cf_clearance':
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > time.time():
                        logger.info(f"✅ {site_name} 缓存中的Cloudflare cookie有效")
                        return True
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
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > time.time():
                        return True
            return False
        except Exception:
            return False

# ======================== 浏览器管理器 ========================
class BrowserManager:
    @staticmethod
    async def init_browser():
        playwright = await async_playwright().start()
        
        user_agent = random.choice(USER_AGENTS)
        viewport = random.choice(VIEWPORT_SIZES)
        
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
        has_browser_state = UltimateCacheManager.load_site_cache(site_name, 'browser_state') is not None
        has_cf_cookies = UltimateCacheManager.load_site_cache(site_name, 'cf_cookies') is not None
        
        logger.info(f"🔍 {site_name} 缓存状态 - 浏览器状态: {'✅' if has_browser_state else '❌'}, Cloudflare Cookies: {'✅' if has_cf_cookies else '❌'}")
        
        storage_state = UltimateCacheManager.load_site_cache(site_name, 'browser_state')
        
        user_agent = random.choice(USER_AGENTS)
        viewport = random.choice(VIEWPORT_SIZES)
        
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
            cf_cookies = UltimateCacheManager.load_site_cache(site_name, 'cf_cookies')
            if cf_cookies:
                current_time = time.time()
                valid_cookies = []
                for cookie in cf_cookies:
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > current_time:
                        valid_cookies.append(cookie)
                
                if valid_cookies:
                    await context.add_cookies(valid_cookies)
                    logger.info(f"✅ 已从缓存加载 {len(valid_cookies)} 个 {site_name} Cloudflare cookies")
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

    @staticmethod
    async def set_random_user_agent(page):
        """为页面设置随机User-Agent"""
        user_agent = random.choice(USER_AGENTS)
        await page.set_extra_http_headers({"User-Agent": user_agent})
        # 同时更新navigator.userAgent
        await page.add_init_script(f"Object.defineProperty(navigator, 'userAgent', {{get: () => '{user_agent}'}});")
        logger.info(f"🔄 已切换User-Agent: {user_agent[:50]}...")

# ======================== 终极主自动化类 ========================
class UltimateSiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.retry_count = 0
        self.session_data = UltimateCacheManager.load_site_cache(site_config['name'], 'session_data') or {}
        self.cf_passed = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.domain = site_config['base_url'].replace('https://', '')
        self.cache_saved = False  # 防止重复保存

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
                    # 尝试使用缓存直接访问
                    cache_success = await self.try_cache_first_approach()
                    if cache_success:
                        logger.success(f"✅ {self.site_config['name']} 缓存优先流程成功")
                        self.is_logged_in = True
                        self.cf_passed = True
                        # 关键修复：登录成功后立即保存缓存
                        await self.save_all_caches()
                    else:
                        # 缓存失败，进行完整验证流程
                        logger.warning(f"⚠️ {self.site_config['name']} 缓存优先流程失败，开始完整验证")
                        full_success = await self.full_verification_process()
                        self.is_logged_in = full_success

                    if self.is_logged_in:
                        logger.success(f"✅ {self.site_config['name']} 登录成功，开始执行后续任务")
                        await self.browse_topics()
                        await self.save_final_status(success=True)
                        break
                    else:
                        logger.error(f"❌ {self.site_config['name']} 登录失败")
                        
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

    async def enhanced_check_login_status(self):
        """增强的登录状态检查"""
        try:
            # 检查页面是否包含登录相关元素
            login_buttons = await self.page.query_selector_all('a[href*="/login"], button:has-text("登录"), button:has-text("Sign in")')
            if len(login_buttons) > 0:
                logger.warning("⚠️ 检测到登录按钮，可能未登录")
                return False
                
            # 检查页面是否包含用户相关元素
            user_elements = await self.page.query_selector_all('a[href*="/user"], .user-avatar, .current-user')
            if len(user_elements) > 0:
                logger.success("✅ 检测到用户元素，已登录")
                return True
                
            # 检查页面标题和内容
            page_title = await self.page.title()
            page_content = await self.page.content()
            
            if "登录" in page_title or "Sign in" in page_title:
                logger.warning("⚠️ 页面标题包含登录信息，可能未登录")
                return False
                
            # 作为最后的手段，检查是否能访问需要登录的页面
            try:
                await self.page.goto(self.site_config['latest_topics_url'], timeout=60000, wait_until='networkidle')
                await asyncio.sleep(2)
                
                # 再次检查登录状态
                new_login_buttons = await self.page.query_selector_all('a[href*="/login"], button:has-text("登录")')
                if len(new_login_buttons) == 0:
                    logger.success("✅ 页面显示正常内容，可能已登录")
                    return True
                else:
                    logger.warning("⚠️ 访问最新主题页后仍检测到登录按钮")
                    return False
            except Exception as e:
                logger.error(f"检查登录状态时访问页面失败: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"增强型登录状态检查失败: {str(e)}")
            return False

    async def browse_topics(self):
        try:
            logger.info(f"📖 开始 {self.site_config['name']} 主题浏览")
            
            # 强化登录验证：在浏览前检查登录状态
            is_logged_in = await self.enhanced_check_login_status()
            if not is_logged_in:
                logger.warning("⚠️ 检测到未登录状态，尝试重新登录")
                # 尝试重新登录
                login_success = await self.perform_login()  # 假设存在这个登录方法
                if not login_success:
                    logger.error("❌ 重新登录失败，无法继续浏览主题")
                    return
                logger.success("✅ 重新登录成功，继续浏览主题")
            
            browse_history = self.session_data.get('browse_history', [])
            
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000, wait_until='networkidle')
            
            # 尝试多种选择器
            topic_selectors = ['a.title', '.title a', 'a.topic-title', '.topic-list-item a', 'tr.topic-list-item a.title']
            topic_links = []
            
            for selector in topic_selectors:
                links = await self.page.query_selector_all(selector)
                if links:
                    logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(links)} 个主题链接")
                    topic_links = links
                    break
            
            if not topic_links:
                logger.warning(f"{self.site_config['name']} 未找到主题链接")
                return
            
            browse_count = min(random.randint(5, 9), len(topic_links))
            selected_topics = random.sample(topic_links, browse_count)
            
            logger.info(f"📚 {self.site_config['name']} 计划浏览 {browse_count} 个主题")
            
            success_count = 0
            for idx, topic in enumerate(selected_topics, 1):
                success = await self.browse_single_topic(topic, idx, browse_count, browse_history)
                if success:
                    success_count += 1
                    
                if idx < browse_count:
                    # 随机化浏览间隔，使间隔更不规律
                    wait_time = random.choice([
                        random.uniform(3, 5),
                        random.uniform(7, 12),
                        random.uniform(15, 20),
                        random.uniform(25, 35)
                    ])
                    logger.info(f"⏳ 主题间等待 {wait_time:.1f} 秒")
                    await asyncio.sleep(wait_time)
            
            self.session_data['browse_history'] = browse_history[-50:]
            self.session_data['last_browse'] = datetime.now().isoformat()
            self.session_data['total_browsed'] = self.session_data.get('total_browsed', 0) + success_count
            
            # 主题浏览完成后保存一次缓存
            if not self.cache_saved:
                await self.save_all_caches()
            
            logger.success(f"✅ {self.site_config['name']} 主题浏览完成: 成功 {success_count} 个主题")

        except Exception as e:
            logger.error(f"{self.site_config['name']} 主题浏览流程失败: {str(e)}")

    async def browse_single_topic(self, topic, topic_idx, total_topics, browse_history):
        """浏览单个主题并模拟更真实的用户行为"""
        try:
            title = (await topic.text_content() or "").strip()[:60]
            href = await topic.get_attribute('href')
            
            if not href:
                return False
            
            topic_url = f"{self.site_config['base_url']}{href}" if href.startswith('/') else href
            
            if href in browse_history:
                logger.info(f"🔄 {self.site_config['name']} 主题 {topic_idx}/{total_topics} 已浏览过，跳过")
                return False
            
            logger.info(f"🌐 {self.site_config['name']} 浏览主题 {topic_idx}/{total_topics}: {title}")
            
            # 创建新页面并设置随机User-Agent
            tab = await self.context.new_page()
            await BrowserManager.set_random_user_agent(tab)
            
            try:
                # 随机微小延迟后再访问
                await asyncio.sleep(random.uniform(0.5, 2.0))
                
                await tab.goto(topic_url, timeout=45000, wait_until='domcontentloaded')
                
                # 模拟随机鼠标移动到主题标题
                topic_element = await tab.query_selector('h1, .topic-title')
                if topic_element:
                    box = await topic_element.bounding_box()
                    if box:
                        # 随机移动路径
                        start_x = random.uniform(50, 200)
                        start_y = random.uniform(50, 200)
                        await tab.mouse.move(start_x, start_y, steps=random.randint(5, 15))
                        
                        # 移动到元素
                        await tab.mouse.move(
                            box['x'] + box['width'] / 2 + random.uniform(-10, 10),
                            box['y'] + box['height'] / 2 + random.uniform(-10, 10),
                            steps=random.randint(10, 30)
                        )
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                
                # 模拟阅读时间和滚动行为，延长最短停留时间
                total_read_time = random.choice([
                    random.uniform(45, 70),  # 短阅读
                    random.uniform(80, 120), # 中等阅读
                    random.uniform(150, 240) # 长阅读
                ])
                logger.info(f"⏳ 计划阅读时间: {total_read_time:.1f} 秒")
                
                scroll_interval = random.uniform(2, 8)  # 每次滚动间隔
                total_scroll_steps = math.ceil(total_read_time / scroll_interval)
                
                # 先等待3-8秒再开始滚动，模拟用户先看标题
                initial_wait = random.uniform(3, 8)
                logger.info(f"⏳ 初始阅读等待: {initial_wait:.1f} 秒")
                await asyncio.sleep(initial_wait)
                
                # 逐步滚动到页面底部
                for step in range(total_scroll_steps):
                    # 随机决定是否在这一步添加额外行为
                    if random.random() < 0.3:  # 30%的概率
                        # 随机点击页面空白处
                        if random.random() < 0.5:
                            page_width = await tab.evaluate("document.body.scrollWidth")
                            page_height = await tab.evaluate("document.body.scrollHeight")
                            
                            click_x = random.uniform(page_width * 0.1, page_width * 0.9)
                            click_y = random.uniform(page_height * 0.1, page_height * 0.9)
                            
                            await tab.mouse.move(click_x, click_y, steps=random.randint(5, 20))
                            await asyncio.sleep(random.uniform(0.1, 0.5))
                            await tab.mouse.click(click_x, click_y, button=MouseButton.LEFT)
                            logger.info(f"🖱️ 随机点击位置: ({click_x:.0f}, {click_y:.0f})")
                            await asyncio.sleep(random.uniform(1, 3))
                    
                    # 计算当前滚动位置 (0.0 到 1.0)，加入一些随机性
                    scroll_position = min(step / total_scroll_steps + random.uniform(-0.05, 0.05), 1.0)
                    scroll_position = max(scroll_position, 0.0)
                    
                    # 使用JavaScript滚动到相应位置
                    await tab.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_position});")
                    
                    # 随机微小停顿，模拟阅读行为
                    await asyncio.sleep(scroll_interval + random.uniform(-0.5, 0.5))
                
                # 到达底部后再停留5-10秒
                final_wait = random.uniform(5, 10)
                logger.info(f"⏳ 底部停留时间: {final_wait:.1f} 秒")
                await asyncio.sleep(final_wait)
                
                browse_history.append(href)
                return True
            finally:
                # 关闭标签页前随机延迟
                await asyncio.sleep(random.uniform(0.5, 2.0))
                await tab.close()
                
        except Exception as e:
            logger.error(f"{self.site_config['name']} 浏览单个主题失败: {str(e)}")
            return False

    # 以下是假设存在的其他方法，保持原有逻辑
    async def try_cache_first_approach(self):
        # 原有逻辑保持不变
        # 检测到有效的Cloudflare缓存，尝试直接访问
        cf_valid = await CloudflareHandler.is_cached_cf_valid(self.site_config['name'])
        if cf_valid:
            logger.info("✅ 检测到有效的Cloudflare缓存，尝试直接访问")
            try:
                await self.page.goto(self.site_config['latest_topics_url'], timeout=60000, wait_until='networkidle')
                await asyncio.sleep(3)
                
                # 检查登录状态
                if await self.enhanced_check_login_status():
                    logger.success("✅ 缓存优先流程成功 - 已登录")
                    return True
                else:
                    logger.warning("⚠️ 缓存优先流程 - 未登录")
                    return False
            except Exception as e:
                logger.error(f"缓存优先流程失败: {str(e)}")
                return False
        return False

    async def full_verification_process(self):
        # 原有逻辑保持不变
        try:
            # 处理Cloudflare验证
            cf_success = await CloudflareHandler.handle_cloudflare(self.page, self.site_config)
            self.cf_passed = cf_success
            
            if not cf_success:
                logger.error("❌ Cloudflare验证失败")
                return False
                
            # 检查登录状态
            if await self.enhanced_check_login_status():
                logger.success("✅ 已登录，无需重新登录")
                return True
                
            # 执行登录
            return await self.perform_login()
        except Exception as e:
            logger.error(f"完整验证流程失败: {str(e)}")
            return False

    async def perform_login(self):
        # 原有登录逻辑保持不变
        try:
            logger.info(f"🔑 开始 {self.site_config['name']} 登录流程")
            await self.page.goto(self.site_config['login_url'], timeout=60000, wait_until='networkidle')
            
            # 这里添加实际登录逻辑，根据网站表单字段调整
            await self.page.fill('#login-username', self.credentials['username'])
            await asyncio.sleep(random.uniform(1, 2))
            await self.page.fill('#login-password', self.credentials['password'])
            await asyncio.sleep(random.uniform(1, 2))
            
            await self.page.click('button[type="submit"]')
            await self.page.wait_for_load_state('networkidle', timeout=60000)
            
            # 验证登录是否成功
            if await self.enhanced_check_login_status():
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False
        except Exception as e:
            logger.error(f"{self.site_config['name']} 登录过程出错: {str(e)}")
            return False

    async def save_all_caches(self):
        # 保存Cloudflare cookies
        cf_cookies = await self.context.cookies()
        UltimateCacheManager.save_site_cache(cf_cookies, self.site_config['name'], 'cf_cookies')
        logger.info(f"✅ {self.site_config['name']} Cloudflare Cookies 已保存: {len(cf_cookies)} 个")
        
        # 保存浏览器状态
        state = await self.context.storage_state()
        UltimateCacheManager.save_site_cache(state, self.site_config['name'], 'browser_state')
        
        # 保存会话数据
        UltimateCacheManager.save_site_cache(self.session_data, self.site_config['name'], 'session_data')
        
        self.cache_saved = True
        logger.info(f"✅ {self.site_config['name']} 所有缓存已保存（覆盖旧缓存）")

    async def save_final_status(self, success):
        status_data = {
            'success': success,
            'timestamp': datetime.now().isoformat(),
            'site': self.site_config['name'],
            'login_status': self.is_logged_in,
            'cf_passed': self.cf_passed,
            'retry_count': self.retry_count
        }
        UltimateCacheManager.save_cache(status_data, self.site_config['final_status_file'])

    async def clear_caches(self):
        # 清除所有缓存
        cache_types = ['cf_cookies', 'browser_state', 'session_data']
        for cache_type in cache_types:
            file_name = f"{cache_type}_{self.site_config['name']}.json"
            if os.path.exists(file_name):
                os.remove(file_name)
                logger.info(f"🗑️ 已删除 {self.site_config['name']} {cache_type} 缓存")

    async def clear_login_caches_only(self):
        # 只清除登录相关缓存，保留Cloudflare缓存
        cache_types = ['browser_state', 'session_data']
        for cache_type in cache_types:
            file_name = f"{cache_type}_{self.site_config['name']}.json"
            if os.path.exists(file_name):
                os.remove(file_name)
                logger.info(f"🗑️ 已删除 {self.site_config['name']} {cache_type} 缓存")

    async def close_context(self):
        if self.context:
            await self.context.close()
            logger.info(f"✅ {self.site_config['name']} 浏览器上下文已关闭")
