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
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
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

PAGE_TIMEOUT = 180000  # 3分钟超时
RETRY_TIMES = 2  # 重试次数

# ======================== 反检测配置 ========================
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
    async def handle_cloudflare(page: Page, site_config, max_attempts=8, timeout=180):
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
    async def is_cf_clearance_valid(context: BrowserContext, domain):
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
    async def create_context(browser: Browser, site_name):
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
    async def load_caches_into_context(context: BrowserContext, site_name):
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

    async def run_for_site(self, browser: Browser, playwright):
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
                    traceback.print_exc()
                    
                    if self.retry_count == 0:
                        logger.info(f"🔄 {self.site_config['name']} 清除缓存并重试")
                        await self.clear_caches()
                    
                    self.retry_count += 1
                    if self.retry_count <= RETRY_TIMES:
                        wait_time = 15 + self.retry_count * 10
                        logger.warning(f"将在 {wait_time} 秒后重试 ({self.retry_count}/{RETRY_TIMES})")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"❌ {self.site_config['name']} 达到最大重试次数，任务失败")
                        await self.save_final_status(success=False)
                        return False
            return self.is_logged_in
            
        except Exception as e:
            logger.error(f"{self.site_config['name']} 主流程发生致命错误: {str(e)}")
            traceback.print_exc()
            await self.save_final_status(success=False)
            return False
        finally:
            # 确保资源正确释放
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            logger.info(f"🔚 {self.site_config['name']} 自动化流程结束")

    async def enhanced_check_login_status(self):
        """增强版登录状态检查"""
        try:
            # 尝试访问个人主页或登录后才能访问的页面
            profile_indicators = [
                '//a[contains(@href, "/u/")]',  # 用户名链接
                '//button[contains(text(), "退出") or contains(text(), "登出")]',  # 退出按钮
                '//span[contains(@class, "username")]'  # 用户名显示
            ]
            
            # 先检查当前页面
            for indicator in profile_indicators:
                if await self.page.query_selector(indicator):
                    logger.success("✅ 在当前页面检测到登录状态")
                    return True
            
            # 访问最新主题页再次检查
            logger.info("🔍 访问最新主题页验证登录状态")
            await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
            await asyncio.sleep(3)
            
            for indicator in profile_indicators:
                if await self.page.query_selector(indicator):
                    logger.success("✅ 在最新主题页检测到登录状态")
                    return True
            
            # 检查是否有登录表单（反证未登录）
            login_form = await self.page.query_selector('form[action*="/login"]')
            if login_form:
                logger.warning("❌ 检测到登录表单，确认未登录")
                return False
                
            logger.warning("❌ 未明确检测到登录状态")
            return False
        except Exception as e:
            logger.error(f"检查登录状态时出错: {str(e)}")
            return False

    async def try_cache_first_approach(self):
        """尝试使用缓存直接访问站点"""
        try:
            logger.info(f"🔍 尝试缓存优先访问 {self.site_config['name']}")
            await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
            await asyncio.sleep(5)
            
            # 处理Cloudflare
            cf_success = await CloudflareHandler.handle_cloudflare(self.page, self.site_config)
            self.cf_passed = cf_success
            
            if not cf_success:
                logger.warning("⚠️ Cloudflare验证未通过，无法使用缓存访问")
                return False
            
            # 检查登录状态
            login_status = await self.enhanced_check_login_status()
            if login_status:
                logger.success(f"✅ {self.site_config['name']} 缓存登录状态有效")
                return True
            
            logger.warning(f"⚠️ {self.site_config['name']} 缓存登录状态无效")
            return False
        except Exception as e:
            logger.error(f"缓存优先访问失败: {str(e)}")
            return False

    async def full_verification_process(self):
        """完整的登录验证流程"""
        try:
            logger.info(f"🔄 开始 {self.site_config['name']} 完整登录流程")
            
            # 1. 访问登录页
            await self.page.goto(self.site_config['login_url'], wait_until='networkidle', timeout=60000)
            await asyncio.sleep(3)
            
            # 2. 处理Cloudflare
            cf_success = await CloudflareHandler.handle_cloudflare(self.page, self.site_config)
            self.cf_passed = cf_success
            if not cf_success:
                logger.error("❌ Cloudflare验证失败，无法继续登录")
                return False
            
            # 3. 执行登录操作
            login_success = await self.perform_login()
            if not login_success:
                logger.error("❌ 登录操作执行失败")
                return False
            
            # 4. 验证登录状态
            final_status = await self.enhanced_check_login_status()
            if final_status:
                logger.success(f"✅ {self.site_config['name']} 完整登录流程成功")
                await self.save_all_caches()
                return True
            
            logger.error(f"❌ {self.site_config['name']} 完整登录流程验证失败")
            return False
        except Exception as e:
            logger.error(f"完整验证流程出错: {str(e)}")
            return False

    async def perform_login(self):
        """执行实际登录操作"""
        try:
            # 定位用户名和密码字段（适配常见论坛结构）
            username_field = await self.page.query_selector('input[name="username"], input[name="login"]')
            password_field = await self.page.query_selector('input[name="password"]')
            submit_button = await self.page.query_selector('button[type="submit"], input[type="submit"][value*="登录"]')
            
            if not all([username_field, password_field, submit_button]):
                logger.error("❌ 无法定位登录表单元素")
                return False
            
            # 输入凭据
            await username_field.fill(self.credentials['username'])
            await password_field.fill(self.credentials['password'])
            await asyncio.sleep(random.uniform(1, 2))  # 模拟人类输入间隔
            
            # 提交表单
            await submit_button.click()
            await self.page.wait_for_load_state('networkidle', timeout=60000)
            await asyncio.sleep(5)  # 等待跳转完成
            
            return True
        except Exception as e:
            logger.error(f"执行登录时出错: {str(e)}")
            return False

    async def browse_topics(self):
        """浏览主题页面（模拟用户行为）"""
        try:
            logger.info(f"🔍 开始浏览 {self.site_config['name']} 主题")
            
            # 随机滚动页面
            for _ in range(random.randint(3, 5)):
                scroll_height = random.randint(300, 800)
                await self.page.evaluate(f"window.scrollBy(0, {scroll_height})")
                await asyncio.sleep(random.uniform(1.5, 3))
            
            # 随机点击1-2个主题
            topics = await self.page.query_selector_all('a[href*="/t/"]')
            if topics:
                select_count = random.randint(1, min(2, len(topics)))
                selected_topics = random.sample(topics, select_count)
                
                for topic in selected_topics:
                    topic_url = await topic.get_attribute('href')
                    full_url = urljoin(self.site_config['base_url'], topic_url)
                    logger.info(f"📄 浏览主题: {full_url}")
                    
                    await topic.click()
                    await self.page.wait_for_load_state('networkidle', timeout=60000)
                    await asyncio.sleep(random.uniform(3, 5))
                    
                    # 在主题页内随机滚动
                    for _ in range(random.randint(2, 4)):
                        scroll_height = random.randint(200, 600)
                        await self.page.evaluate(f"window.scrollBy(0, {scroll_height})")
                        await asyncio.sleep(random.uniform(1, 2))
                    
                    # 返回列表页
                    await self.page.go_back()
                    await self.page.wait_for_load_state('networkidle', timeout=60000)
                    await asyncio.sleep(2)
            
            logger.success(f"✅ {self.site_config['name']} 主题浏览完成")
        except Exception as e:
            logger.warning(f"浏览主题时出错: {str(e)}，继续执行后续流程")

    async def save_all_caches(self):
        """保存所有缓存数据"""
        if self.cache_saved:
            logger.info("ℹ️ 缓存已保存，跳过重复保存")
            return
        
        try:
            # 保存浏览器状态
            storage_state = await self.context.storage_state()
            UltimateCacheManager.save_site_cache(storage_state, self.site_config['name'], 'browser_state')
            
            # 保存Cloudflare cookies
            cf_cookies = await self.context.cookies()
            UltimateCacheManager.save_site_cache(cf_cookies, self.site_config['name'], 'cf_cookies')
            
            # 保存会话数据
            self.session_data.update({
                'last_login': datetime.now().isoformat(),
                'is_logged_in': self.is_logged_in,
                'cf_passed': self.cf_passed
            })
            UltimateCacheManager.save_site_cache(self.session_data, self.site_config['name'], 'session_data')
            
            self.cache_saved = True
            logger.success(f"✅ {self.site_config['name']} 所有缓存已保存")
        except Exception as e:
            logger.error(f"保存缓存时出错: {str(e)}")

    async def clear_caches(self):
        """清除所有相关缓存"""
        try:
            cache_types = ['browser_state', 'cf_cookies', 'session_data']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{self.site_config['name']}.json"
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已删除缓存 {file_name}")
            logger.success(f"✅ {self.site_config['name']} 所有缓存已清除")
        except Exception as e:
            logger.error(f"清除缓存时出错: {str(e)}")

    async def clear_login_caches_only(self):
        """只清除登录相关缓存（保留Cloudflare验证）"""
        try:
            # 保留cf_cookies，清除其他登录相关缓存
            cache_types = ['browser_state', 'session_data']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{self.site_config['name']}.json"
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已删除登录相关缓存 {file_name}")
            logger.success(f"✅ {self.site_config['name']} 登录缓存已清除")
        except Exception as e:
            logger.error(f"清除登录缓存时出错: {str(e)}")

    async def save_final_status(self, success: bool):
        """保存最终执行状态"""
        status_data = {
            'site': self.site_config['name'],
            'success': success,
            'timestamp': datetime.now().isoformat(),
            'retry_count': self.retry_count,
            'cf_passed': self.cf_passed,
            'run_id': os.getenv('GITHUB_RUN_ID', 'local')
        }
        UltimateCacheManager.save_cache(status_data, self.site_config['final_status_file'])
        logger.info(f"📊 已保存 {self.site_config['name']} 最终状态: {'成功' if success else '失败'}")

# ======================== 主函数 ========================
async def main():
    # 配置日志
    logger.add(
        f"automation_{datetime.now().strftime('%Y%m%d')}.log",
        rotation="1 day",
        level="INFO",
        encoding="utf-8"
    )
    logger.info("🚀 多站点自动化脚本启动")

    # 获取要运行的站点（从命令行参数或默认值）
    site_selector = sys.argv[1] if len(sys.argv) > 1 else 'all'
    logger.info(f"🎯 运行模式: {site_selector}")

    # 筛选要处理的站点
    target_sites = [site for site in SITES if site_selector == 'all' or site['name'] == site_selector]
    if not target_sites:
        logger.error("❌ 未找到匹配的站点配置")
        return

    # 初始化浏览器
    browser, playwright = await BrowserManager.init_browser()
    try:
        # 逐个处理站点
        for site in target_sites:
            logger.info(f"\n{'='*50}\n📌 开始处理站点: {site['name']}\n{'='*50}")
            automator = UltimateSiteAutomator(site)
            success = await automator.run_for_site(browser, playwright)
            logger.info(f"📊 站点 {site['name']} 处理结果: {'成功' if success else '失败'}\n")

    finally:
        # 确保资源释放
        await browser.close()
        await playwright.stop()
        logger.info("🏁 多站点自动化脚本全部结束")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"脚本致命错误: {str(e)}")
        traceback.print_exc()
        sys.exit(1)
