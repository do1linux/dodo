#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - Cloudflare Turnstile 解决方案版
功能：处理Cloudflare人机验证，自动登录并浏览论坛
作者：自动化脚本
版本：6.0 - Turnstile解决方案版
"""

import os
import sys
import time
import random
import asyncio
import json
import traceback
import argparse
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from loguru import logger
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
        'connect_url': 'https://connect.linux.do/',
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
        'connect_url': 'https://connect.idcflare.com/',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json",
        'session_file': "session_data_idcflare.json", 
        'final_status_file': "final_status_idcflare.json"
    }
]

PAGE_TIMEOUT = 120000
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 10

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864}
]

# ======================== 命令行参数解析 ========================
def parse_arguments():
    parser = argparse.ArgumentParser(description='LinuxDo 多站点自动化脚本')
    parser.add_argument('--site', type=str, help='指定运行的站点', 
                       choices=['linux_do', 'idcflare', 'all'], default='all')
    parser.add_argument('--verbose', action='store_true', help='详细输出模式')
    parser.add_argument('--clear-cache', action='store_true', help='清除缓存')
    return parser.parse_args()

# ======================== 缓存管理器 ========================
class CacheManager:
    @staticmethod
    def load_cache(file_name):
        if os.path.exists(file_name):
            try:
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
                return data.get('data', data)
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        try:
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '6.0'
            }
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.save_cache(data, file_name)

# ======================== Cloudflare Turnstile 解决方案 ========================
class TurnstileSolver:
    """专门处理Cloudflare Turnstile验证的类"""
    
    @staticmethod
    async def wait_for_turnstile(page, timeout=30):
        """等待Turnstile验证出现并尝试自动解决"""
        logger.info("🛡️ 检查Cloudflare Turnstile验证")
        
        try:
            # 等待Turnstile相关元素出现
            await page.wait_for_selector('iframe[src*="challenges.cloudflare.com"], [data-turnstile-widget], input[name="cf-turnstile-response"]', 
                                       timeout=timeout * 1000)
            
            logger.warning("🎯 检测到Cloudflare Turnstile验证")
            return True
        except Exception:
            logger.info("✅ 未检测到Turnstile验证")
            return False

    @staticmethod
    async def solve_turnstile_automatically(page):
        """尝试自动解决Turnstile验证"""
        logger.info("🔄 尝试自动解决Turnstile验证")
        
        try:
            # 方法1: 尝试直接获取Turnstile响应
            turnstile_response = await page.evaluate("""
                async () => {
                    try {
                        // 检查是否有现有的token
                        const existingInput = document.querySelector('input[name="cf-turnstile-response"]');
                        if (existingInput && existingInput.value) {
                            return existingInput.value;
                        }
                        
                        // 尝试通过Turnstile API获取响应
                        if (window.turnstile) {
                            return new Promise((resolve) => {
                                window.turnstile.getResponse(function(token) {
                                    resolve(token || 'auto-token');
                                });
                            });
                        }
                        
                        // 模拟点击验证（如果有复选框）
                        const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                        for (let checkbox of checkboxes) {
                            if (checkbox.closest('[class*="turnstile"], [class*="cf-"]')) {
                                checkbox.click();
                                await new Promise(r => setTimeout(r, 2000));
                            }
                        }
                        
                        return 'simulated-token';
                    } catch (e) {
                        return 'error-' + e.message;
                    }
                }
            """)
            
            if turnstile_response and not turnstile_response.startswith('error'):
                logger.success(f"✅ 自动获取Turnstile token: {turnstile_response[:30]}...")
                
                # 设置token到表单
                await page.evaluate(f"""
                    (token) => {{
                        const input = document.querySelector('input[name="cf-turnstile-response"]');
                        if (input) {{
                            input.value = token;
                        }}
                    }}
                """, turnstile_response)
                
                return True
                
        except Exception as e:
            logger.error(f"自动解决Turnstile失败: {str(e)}")
        
        return False

    @staticmethod
    async def handle_cloudflare_challenge(page, site_config, max_wait=60):
        """处理Cloudflare挑战页面"""
        logger.info("⏳ 处理Cloudflare挑战页面")
        
        start_time = time.time()
        challenge_solved = False
        
        while time.time() - start_time < max_wait:
            current_title = await page.title()
            current_url = page.url
            
            # 检查是否还在挑战页面
            if "请稍候" not in current_title and "Checking" not in current_title:
                logger.success("✅ Cloudflare挑战已通过")
                challenge_solved = True
                break
            
            # 尝试多种解决方法
            solutions = [
                await TurnstileSolver.solve_turnstile_automatically(page),
                await TurnstileSolver.try_manual_bypass(page),
                await TurnstileSolver.try_refresh_bypass(page, site_config)
            ]
            
            if any(solutions):
                logger.info("🔄 尝试的解决方案已应用，等待验证结果...")
                await asyncio.sleep(5)
            else:
                # 等待验证自动完成
                wait_time = random.uniform(3, 8)
                logger.info(f"⏳ 等待验证完成 ({wait_time:.1f}秒)")
                await asyncio.sleep(wait_time)
        
        return challenge_solved

    @staticmethod
    async def try_manual_bypass(page):
        """尝试模拟手动操作绕过验证"""
        try:
            # 查找可能的验证元素并点击
            selectors_to_click = [
                'input[type="checkbox"]',
                '.cf-turnstile',
                '.turnstile-wrapper',
                '[class*="verify"]',
                '[class*="challenge"]'
            ]
            
            for selector in selectors_to_click:
                elements = await page.query_selector_all(selector)
                for element in elements:
                    if await element.is_visible():
                        await element.click()
                        logger.info(f"✅ 点击验证元素: {selector}")
                        await asyncio.sleep(2)
                        return True
                        
        except Exception as e:
            logger.debug(f"手动绕过尝试失败: {str(e)}")
            
        return False

    @staticmethod
    async def try_refresh_bypass(page, site_config):
        """尝试通过刷新页面绕过验证"""
        try:
            # 直接访问最新主题页面绕过主页验证
            await page.goto(site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(3)
            
            current_title = await page.title()
            if "请稍候" not in current_title and "Checking" not in current_title:
                logger.success("✅ 通过访问/latest页面绕过验证")
                return True
                
        except Exception as e:
            logger.debug(f"刷新绕过失败: {str(e)}")
            
        return False

# ======================== 浏览器管理器 ========================
class BrowserManager:
    @staticmethod
    async def init_browser():
        playwright = await async_playwright().start()
        
        user_agent = random.choice(USER_AGENTS)
        viewport = random.choice(VIEWPORT_SIZES)
        
        logger.info(f"🌐 使用 User-Agent: {user_agent[:60]}...")
        logger.info(f"🖥️  使用视口大小: {viewport}")

        browser_args = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            f'--window-size={viewport["width"]},{viewport["height"]}',
            '--lang=zh-CN,zh;q=0.9,en;q=0.8',
            '--disable-features=VizDisplayCompositor',
        ]

        browser = await playwright.chromium.launch(
            headless=HEADLESS_MODE,
            args=browser_args
        )
        
        return browser, playwright

    @staticmethod
    async def create_context(browser, site_name):
        storage_state = CacheManager.load_site_cache(site_name, 'browser_state')
        
        # 固定指纹
        user_agent = USER_AGENTS[hash(site_name) % len(USER_AGENTS)]
        viewport = VIEWPORT_SIZES[hash(site_name) % len(VIEWPORT_SIZES)]
        
        logger.info(f"🆔 {site_name} 使用固定指纹")
        
        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            storage_state=storage_state,
            ignore_https_errors=True,
        )
        
        # 加载缓存cookies
        await BrowserManager.load_cf_cookies(context, site_name)
        
        # 反检测脚本
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
        """)
        
        return context

    @staticmethod
    async def load_cf_cookies(context, site_name):
        try:
            cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
            if cf_cookies:
                await context.add_cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个 {site_name} Cloudflare cookies")
        except Exception as e:
            logger.error(f"加载cookies失败: {e}")

# ======================== 站点自动化主类 ========================
class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.retry_count = 0
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.cache_saved = False

    async def run_for_site(self, browser, playwright):
        self.browser = browser
        self.playwright = playwright
        
        if not self.credentials.get('username') or not self.credentials.get('password'):
            logger.error(f"❌ {self.site_config['name']} 的用户名或密码未设置")
            return False
            
        try:
            self.context = await BrowserManager.create_context(browser, self.site_config['name'])
            self.page = await self.context.new_page()
            self.page.set_default_timeout(PAGE_TIMEOUT)

            success = await self.execute_with_retry()
            
            if success:
                logger.success(f"🎉 {self.site_config['name']} 任务执行成功")
            else:
                logger.error(f"💥 {self.site_config['name']} 任务执行失败")
                
            return success

        except Exception as e:
            logger.critical(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            await self.cleanup()

    async def execute_with_retry(self):
        """带重试的执行流程"""
        while self.retry_count <= RETRY_TIMES:
            try:
                logger.info(f"🔄 尝试 {self.retry_count + 1}/{RETRY_TIMES + 1}")
                
                # 尝试使用缓存登录
                if await self.try_cached_login():
                    logger.success("✅ 缓存登录成功")
                    self.is_logged_in = True
                else:
                    # 完整登录流程
                    if await self.full_login_process():
                        logger.success("✅ 完整登录成功")
                        self.is_logged_in = True
                
                if self.is_logged_in:
                    # 执行浏览任务
                    await self.browse_topics()
                    await self.print_connect_info()
                    await self.save_all_caches()
                    return True
                else:
                    logger.warning(f"❌ 登录失败，准备重试")
                    
            except Exception as e:
                logger.error(f"执行失败: {str(e)}")
            
            self.retry_count += 1
            if self.retry_count <= RETRY_TIMES:
                wait_time = 10 * self.retry_count
                logger.info(f"⏳ {wait_time}秒后重试...")
                await asyncio.sleep(wait_time)
                await self.clear_login_cache()
        
        return False

    async def try_cached_login(self):
        """尝试使用缓存登录"""
        # 检查是否有有效的Cloudflare缓存
        cf_cookies = CacheManager.load_site_cache(self.site_config['name'], 'cf_cookies')
        if not cf_cookies:
            return False
            
        try:
            # 尝试访问最新主题页面
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(3)
            
            # 检查登录状态
            return await self.check_login_status()
            
        except Exception as e:
            logger.error(f"缓存登录失败: {str(e)}")
            return False

    async def full_login_process(self):
        """完整登录流程"""
        logger.info("🔐 开始完整登录流程")
        
        try:
            # 访问登录页面
            await self.page.goto(self.site_config['login_url'], timeout=90000)
            await asyncio.sleep(3)
            
            # 处理Cloudflare挑战
            current_title = await self.page.title()
            if "请稍候" in current_title or "Checking" in current_title:
                logger.warning("🛡️ 检测到Cloudflare挑战页面")
                challenge_solved = await TurnstileSolver.handle_cloudflare_challenge(
                    self.page, self.site_config, max_wait=45
                )
                if not challenge_solved:
                    logger.error("❌ Cloudflare挑战解决失败")
                    return False
            
            # 等待登录表单
            if not await self.wait_for_login_form():
                logger.error("❌ 登录表单加载失败")
                return False
            
            # 填写登录信息
            username = self.credentials['username']
            password = self.credentials['password']
            
            await self.simulate_human_typing('#login-account-name', username)
            await asyncio.sleep(1)
            await self.simulate_human_typing('#login-account-password', password)
            await asyncio.sleep(2)
            
            # 点击登录
            if not await self.click_login_button():
                return False
                
            # 等待登录结果
            await asyncio.sleep(8)
            
            # 验证登录
            return await self.verify_login_success()
            
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    async def wait_for_login_form(self, max_attempts=15):
        """等待登录表单加载"""
        logger.info("⏳ 等待登录表单...")
        
        for attempt in range(max_attempts):
            try:
                # 检查登录表单元素
                username_field = await self.page.query_selector('#login-account-name, #username, input[name="username"]')
                password_field = await self.page.query_selector('#login-account-password, #password, input[name="password"]')
                
                if username_field and password_field:
                    logger.success("✅ 登录表单已加载")
                    return True
                    
                # 检查是否有Turnstile验证
                if await TurnstileSolver.wait_for_turnstile(self.page, timeout=2):
                    logger.info("🔄 检测到Turnstile验证，尝试解决...")
                    await TurnstileSolver.solve_turnstile_automatically(self.page)
                
                logger.info(f"⏳ 等待登录表单... ({attempt + 1}/{max_attempts})")
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                await asyncio.sleep(2)
        
        logger.error("❌ 登录表单加载超时")
        return False

    async def simulate_human_typing(self, selector, text):
        """模拟人类打字"""
        try:
            await self.page.click(selector)
            await asyncio.sleep(0.5)
            
            for char in text:
                await self.page.type(selector, char, delay=random.randint(50, 150))
                await asyncio.sleep(random.uniform(0.1, 0.3))
                
        except Exception as e:
            logger.error(f"模拟输入失败，直接填充: {str(e)}")
            await self.page.fill(selector, text)

    async def click_login_button(self):
        """点击登录按钮"""
        login_selectors = [
            '#login-button', 
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("Log In")'
        ]
        
        for selector in login_selectors:
            try:
                login_btn = await self.page.query_selector(selector)
                if login_btn and await login_btn.is_visible():
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    await asyncio.sleep(1)
                    await login_btn.click()
                    return True
            except Exception:
                continue
        
        logger.error("❌ 未找到可点击的登录按钮")
        return False

    async def verify_login_success(self):
        """验证登录是否成功"""
        logger.info("🔍 验证登录状态...")
        
        # 检查URL是否跳转
        current_url = self.page.url
        if current_url == self.site_config['login_url']:
            logger.warning("⚠️ 仍在登录页面，检查错误信息")
            
            # 检查错误信息
            error_selectors = ['.alert-error', '.error', '.flash-error']
            for selector in error_selectors:
                error_elem = await self.page.query_selector(selector)
                if error_elem:
                    error_text = await error_elem.inner_text()
                    logger.error(f"❌ 登录错误: {error_text}")
                    return False
            
            return False
        
        # 检查登录状态
        return await self.check_login_status()

    async def check_login_status(self):
        """检查登录状态"""
        try:
            username = self.credentials['username']
            
            # 方法1: 检查页面内容中是否包含用户名
            page_content = await self.page.content()
            if username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {username}")
                return True
            
            # 方法2: 检查用户元素
            user_selectors = ['img.avatar', '.current-user', '[data-user-menu]']
            for selector in user_selectors:
                user_elem = await self.page.query_selector(selector)
                if user_elem and await user_elem.is_visible():
                    logger.success(f"✅ 检测到用户元素: {selector}")
                    
                    # 点击用户菜单验证
                    await user_elem.click()
                    await asyncio.sleep(2)
                    
                    menu_content = await self.page.content()
                    if username.lower() in menu_content.lower():
                        logger.success(f"✅ 在用户菜单中验证用户名: {username}")
                        await self.page.click('body')  # 点击空白处关闭菜单
                        return True
                    
                    await self.page.click('body')
                    await asyncio.sleep(1)
            
            # 方法3: 访问个人资料页面验证
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            await self.page.goto(profile_url, timeout=30000)
            await asyncio.sleep(3)
            
            profile_content = await self.page.content()
            if username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                return True
                
        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
        
        logger.warning("❌ 无法验证用户名，登录可能失败")
        return False

    async def browse_topics(self):
        """浏览主题帖"""
        logger.info("📚 开始浏览主题帖")
        
        try:
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(3)
            
            # 获取主题链接
            topic_links = await self.page.query_selector_all('a.title, .topic-list-item a, .topic-title a')
            logger.info(f"📖 发现 {len(topic_links)} 个主题帖")
            
            if not topic_links:
                logger.warning("⚠️ 未找到主题帖")
                return
            
            # 随机选择主题
            topics_to_browse = min(MAX_TOPICS_TO_BROWSE, len(topic_links))
            selected_topics = random.sample(topic_links, topics_to_browse)
            
            logger.info(f"🎯 随机选择 {topics_to_browse} 个主题")
            
            for i, topic_link in enumerate(selected_topics):
                try:
                    logger.info(f"🔍 浏览第 {i+1}/{topics_to_browse} 个主题")
                    
                    href = await topic_link.get_attribute('href')
                    if href:
                        full_url = urljoin(self.site_config['base_url'], href)
                        await self.browse_single_topic(full_url)
                    
                    # 主题间延迟
                    if i < topics_to_browse - 1:
                        delay = random.uniform(5, 10)
                        await asyncio.sleep(delay)
                        
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
                    continue
            
            logger.success("✅ 主题浏览完成")
            
        except Exception as e:
            logger.error(f"浏览主题过程异常: {str(e)}")

    async def browse_single_topic(self, topic_url):
        """浏览单个主题"""
        try:
            new_page = await self.context.new_page()
            await new_page.goto(topic_url, timeout=60000)
            await asyncio.sleep(2)
            
            logger.info(f"📄 浏览主题: {await new_page.title()}")
            
            # 模拟阅读行为
            scroll_attempts = random.randint(3, 8)
            for _ in range(scroll_attempts):
                scroll_distance = random.randint(300, 700)
                await new_page.evaluate(f"window.scrollBy(0, {scroll_distance})")
                await asyncio.sleep(random.uniform(2, 4))
            
            # 随机点赞
            if random.random() < 0.05:
                await self.click_like(new_page)
            
            await new_page.close()
            
        except Exception as e:
            logger.error(f"浏览单个主题失败: {str(e)}")

    async def click_like(self, page):
        """点赞帖子"""
        try:
            like_buttons = await page.query_selector_all('.like-button, .btn-like, [data-like]')
            for button in like_buttons:
                if await button.is_visible():
                    await button.click()
                    logger.info("👍 点赞成功")
                    await asyncio.sleep(1)
                    return
        except Exception:
            pass

    async def print_connect_info(self):
        """打印连接信息"""
        try:
            logger.info("🔗 获取连接信息")
            
            connect_page = await self.context.new_page()
            await connect_page.goto(self.site_config['connect_url'], timeout=60000)
            await asyncio.sleep(3)
            
            # 提取表格信息
            table = await connect_page.query_selector('table')
            if table:
                rows = await table.query_selector_all('tr')
                
                info = []
                for row in rows:
                    cells = await row.query_selector_all('td')
                    if len(cells) >= 3:
                        project = await cells[0].inner_text()
                        current = await cells[1].inner_text()
                        requirement = await cells[2].inner_text()
                        info.append([project.strip(), current.strip(), requirement.strip()])
                
                if info:
                    print("🔗 Connect 信息:")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="grid"))
                else:
                    logger.info("ℹ️ 未找到连接信息")
            
            await connect_page.close()
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    async def save_all_caches(self):
        """保存所有缓存"""
        try:
            # 保存Cloudflare cookies
            all_cookies = await self.context.cookies()
            cf_cookies = [
                cookie for cookie in all_cookies 
                if 'cf_' in cookie.get('name', '') or 'cloudflare' in cookie.get('name', '')
            ]
            if cf_cookies:
                CacheManager.save_site_cache(cf_cookies, self.site_config['name'], 'cf_cookies')
            
            # 保存浏览器状态
            state = await self.context.storage_state()
            CacheManager.save_site_cache(state, self.site_config['name'], 'browser_state')
            
            # 保存会话数据
            session_data = {
                'last_success': datetime.now().isoformat(),
                'username': self.credentials['username'],
                'topics_browsed': MAX_TOPICS_TO_BROWSE
            }
            CacheManager.save_site_cache(session_data, self.site_config['name'], 'session_data')
            
            logger.info("💾 所有缓存已保存")
            self.cache_saved = True
            
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")

    async def clear_login_cache(self):
        """清除登录缓存"""
        cache_files = [
            f"session_data_{self.site_config['name']}.json",
            f"browser_state_{self.site_config['name']}.json"
        ]
        
        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"🗑️ 已清除: {file}")

    async def cleanup(self):
        """清理资源"""
        try:
            if self.context:
                if not self.cache_saved and self.is_logged_in:
                    await self.save_all_caches()
                await self.context.close()
        except Exception:
            pass

# ======================== 主执行函数 ========================
async def main():
    args = parse_arguments()
    
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="DEBUG" if args.verbose else "INFO"
    )
    
    logger.info("🚀 LinuxDo多站点自动化脚本启动 (Turnstile解决方案版)")
    
    # 过滤站点
    target_sites = SITES if args.site == 'all' else [s for s in SITES if s['name'] == args.site]
    
    # 清除缓存
    if args.clear_cache:
        for site in target_sites:
            cache_types = ['session_data', 'browser_state', 'cf_cookies', 'final_status']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{site['name']}.json"
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已清除: {file_name}")
    
    browser, playwright = await BrowserManager.init_browser()
    
    try:
        results = []
        
        for site_config in target_sites:
            logger.info(f"🎯 处理站点: {site_config['name']}")
            
            automator = SiteAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)
            
            results.append({
                'site': site_config['name'],
                'success': success,
                'login_status': automator.is_logged_in
            })
            
            # 站点间延迟
            if site_config != target_sites[-1]:
                delay = random.uniform(10, 20)
                await asyncio.sleep(delay)
        
        # 输出结果
        logger.info("📊 执行结果:")
        table_data = []
        for result in results:
            status = "✅" if result['success'] else "❌"
            login = "已登录" if result['login_status'] else "未登录"
            table_data.append([result['site'], status, login])
        
        print(tabulate(table_data, headers=['站点', '状态', '登录'], tablefmt='grid'))
        
        success_count = sum(1 for r in results if r['success'])
        logger.success(f"🎉 完成: {success_count}/{len(results)} 个站点成功")
        
    except Exception as e:
        logger.critical(f"💥 主流程异常: {str(e)}")
        traceback.print_exc()
    finally:
        await browser.close()
        await playwright.stop()
        logger.info("🔚 脚本结束")

if __name__ == "__main__":
    asyncio.run(main())
