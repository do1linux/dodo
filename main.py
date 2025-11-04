#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - GitHub Actions 优化版
专门针对GitHub Actions环境和Cloudflare挑战优化
版本：7.0 - GitHub Actions专用版
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

# GitHub Actions环境特殊配置
IS_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'
HEADLESS_MODE = True  # GitHub Actions必须使用无头模式

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json", 
    },
    {
        'name': 'idcflare', 
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json",
    }
]

# GitHub Actions环境优化配置
PAGE_TIMEOUT = 120000
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 5  # 减少主题数量以节省时间

# GitHub Actions专用User-Agent
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768}
]

def parse_arguments():
    parser = argparse.ArgumentParser(description='LinuxDo 多站点自动化脚本')
    parser.add_argument('--site', type=str, help='指定运行的站点', 
                       choices=['linux_do', 'idcflare', 'all'], default='all')
    parser.add_argument('--verbose', action='store_true', help='详细输出模式')
    return parser.parse_args()

# ======================== GitHub Actions 缓存管理器 ========================
class GitHubCacheManager:
    """专门为GitHub Actions优化的缓存管理器"""
    
    @staticmethod
    def load_cache(file_name):
        """加载缓存文件"""
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
                return data
            return None
        except Exception as e:
            logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
            return None

    @staticmethod
    def save_cache(data, file_name):
        """保存缓存文件"""
        try:
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return GitHubCacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return GitHubCacheManager.save_cache(data, file_name)

# ======================== Cloudflare 绕过策略 ========================
class CloudflareBypass:
    """Cloudflare绕过策略 - 专门针对GitHub Actions环境"""
    
    @staticmethod
    async def wait_for_cloudflare(page, timeout=60):
        """等待Cloudflare验证通过"""
        logger.info("⏳ 等待Cloudflare验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                title = await page.title()
                current_url = page.url
                
                # 检查是否已通过验证
                if "请稍候" not in title and "Checking" not in title and "challenges" not in current_url:
                    logger.success("✅ Cloudflare验证已通过")
                    return True
                
                # 检查是否有cf_clearance cookie
                cookies = await page.context.cookies()
                cf_cookie = any(cookie.get('name') == 'cf_clearance' for cookie in cookies)
                
                if cf_cookie:
                    logger.info("✅ 检测到cf_clearance cookie，尝试刷新页面")
                    await page.reload(timeout=30000)
                    await asyncio.sleep(3)
                    
                    new_title = await page.title()
                    if "请稍候" not in new_title:
                        logger.success("✅ 通过cookie刷新绕过Cloudflare")
                        return True
                
                # 随机等待
                wait_time = random.uniform(5, 10)
                logger.info(f"⏳ 等待Cloudflare验证 ({wait_time:.1f}秒)")
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                logger.debug(f"等待Cloudflare时出错: {str(e)}")
                await asyncio.sleep(5)
        
        logger.warning("⚠️ Cloudflare等待超时，继续执行")
        return False

    @staticmethod
    async def handle_possible_challenge(page, site_config):
        """处理可能的挑战页面"""
        try:
            current_url = page.url
            title = await page.title()
            
            # 如果是挑战页面，尝试直接访问最新主题页面
            if "challenges" in current_url or "请稍候" in title:
                logger.info("🔄 检测到挑战页面，尝试绕过...")
                await page.goto(site_config['latest_topics_url'], timeout=60000)
                await asyncio.sleep(5)
                return True
            return False
        except Exception as e:
            logger.error(f"处理挑战页面失败: {str(e)}")
            return False

    @staticmethod
    async def save_cloudflare_cookies(context, site_name):
        """保存Cloudflare cookies"""
        try:
            cookies = await context.cookies()
            cf_cookies = [cookie for cookie in cookies if 'cf_' in cookie.get('name', '')]
            
            if cf_cookies:
                GitHubCacheManager.save_site_cache(cf_cookies, site_name, 'cf_cookies')
                logger.info(f"✅ 保存 {len(cf_cookies)} 个Cloudflare cookies")
                return True
        except Exception as e:
            logger.error(f"保存cookies失败: {str(e)}")
        return False

# ======================== GitHub Actions 浏览器管理器 ========================
class GitHubBrowserManager:
    """专门为GitHub Actions优化的浏览器管理器"""
    
    @staticmethod
    async def init_browser():
        """初始化浏览器 - GitHub Actions专用配置"""
        playwright = await async_playwright().start()
        
        # GitHub Actions专用配置
        browser_args = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--disable-features=VizDisplayCompositor',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-features=TranslateUI',
            '--disable-ipc-flooding-protection',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-default-apps',
            '--disable-component-extensions-with-background-pages',
            '--disable-component-update',
            '--disable-domain-reliability',
            '--disable-sync',
            '--disable-client-side-phishing-detection',
            '--disable-hang-monitor',
            '--disable-prompt-on-repost',
            '--disable-background-networking',
            '--disable-extensions',
            '--disable-software-rasterizer',
            '--disable-background-timer-throttling',
            '--disable-renderer-backgrounding',
            '--disable-field-trial-config',
            '--disable-back-forward-cache',
            '--disable-partial-raster',
            '--disable-checker-imaging',
            '--disable-composited-antialiasing',
            '--disable-gl-drawing-for-tests',
            '--metrics-recording-only',
            '--mute-audio',
            '--no-zygote',
            '--window-position=0,0',
            '--ignore-certificate-errors',
            '--ignore-certificate-errors-spki-list',
            '--ignore-ssl-errors',
            '--disable-web-security',
            '--allow-running-insecure-content',
            '--disable-site-isolation-trials',
            '--disable-features=BlockInsecurePrivateNetworkRequests',
            '--disable-features=SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure',
        ]

        browser = await playwright.chromium.launch(
            headless=HEADLESS_MODE,
            args=browser_args
        )
        
        logger.info("🚀 浏览器已启动 (GitHub Actions优化配置)")
        return browser, playwright

    @staticmethod
    async def create_context(browser, site_name):
        """创建浏览器上下文"""
        # 加载缓存状态
        storage_state = GitHubCacheManager.load_site_cache(site_name, 'browser_state')
        cf_cookies = GitHubCacheManager.load_site_cache(site_name, 'cf_cookies')
        
        # 固定指纹
        user_agent = USER_AGENTS[hash(site_name) % len(USER_AGENTS)]
        viewport = VIEWPORT_SIZES[hash(site_name) % len(VIEWPORT_SIZES)]
        
        logger.info(f"🆔 {site_name} - UA: {user_agent[:50]}...")
        
        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            storage_state=storage_state,
            ignore_https_errors=True,
            java_script_enabled=True,
            bypass_csp=True
        )
        
        # 加载Cloudflare cookies
        if cf_cookies:
            await context.add_cookies(cf_cookies)
            logger.info(f"✅ 已加载 {len(cf_cookies)} 个缓存cookies")
        
        # 反检测脚本
        await context.add_init_script("""
            // 基础反检测
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            
            // 覆盖chrome运行时
            window.chrome = { runtime: {} };
            
            // 覆盖权限
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        return context

# ======================== 简化的站点自动化器 ========================
class SimpleSiteAutomator:
    """简化的站点自动化器 - 专注于核心功能"""
    
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        
    async def run_for_site(self, browser, playwright):
        """运行站点自动化"""
        self.browser = browser
        self.playwright = playwright
        
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False
            
        try:
            # 初始化浏览器环境
            self.context = await GitHubBrowserManager.create_context(browser, self.site_config['name'])
            self.page = await self.context.new_page()
            self.page.set_default_timeout(PAGE_TIMEOUT)
            
            # 尝试登录流程
            login_success = await self.smart_login_approach()
            
            if login_success:
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                await self.perform_browsing_actions()
                await self.save_session_data()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False
                
        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            await self.cleanup()

    async def smart_login_approach(self):
        """智能登录策略"""
        max_retries = 2
        
        for attempt in range(max_retries):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{max_retries}")
            
            try:
                # 方法1: 尝试使用缓存直接访问
                if await self.try_direct_access():
                    return True
                
                # 方法2: 完整登录流程
                if await self.full_login_process():
                    return True
                    
            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")
            
            # 清除缓存重试
            if attempt < max_retries - 1:
                await self.clear_cache()
                wait_time = 10 * (attempt + 1)
                logger.info(f"⏳ {wait_time}秒后重试...")
                await asyncio.sleep(wait_time)
        
        return False

    async def try_direct_access(self):
        """尝试直接访问（使用缓存）"""
        try:
            logger.info("🔍 尝试直接访问...")
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(5)
            
            # 检查登录状态
            if await self.check_login_status():
                logger.success("✅ 缓存登录成功")
                return True
                
            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    async def full_login_process(self):
        """完整登录流程"""
        try:
            logger.info("🔐 开始完整登录流程")
            
            # 访问登录页面
            await self.page.goto(self.site_config['login_url'], timeout=90000)
            await asyncio.sleep(5)
            
            # 处理Cloudflare验证
            await CloudflareBypass.wait_for_cloudflare(self.page, timeout=45)
            
            # 检查是否在挑战页面，尝试绕过
            await CloudflareBypass.handle_possible_challenge(self.page, self.site_config)
            
            # 等待登录表单
            if not await self.wait_for_login_form():
                logger.error("❌ 登录表单加载失败")
                return False
            
            # 填写登录信息
            username = self.credentials['username']
            password = self.credentials['password']
            
            await self.fill_login_form(username, password)
            
            # 提交登录
            if not await self.submit_login():
                return False
            
            # 验证登录结果
            return await self.verify_login_result()
            
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    async def wait_for_login_form(self, max_wait=30):
        """等待登录表单"""
        logger.info("⏳ 等待登录表单...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                # 检查用户名字段
                username_selectors = [
                    '#login-account-name',
                    '#username', 
                    'input[name="username"]',
                    'input[type="text"]',
                    'input[placeholder*="用户名"]',
                    'input[placeholder*="username"]'
                ]
                
                for selector in username_selectors:
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        logger.success(f"✅ 找到登录表单: {selector}")
                        return True
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                await asyncio.sleep(2)
        
        logger.error("❌ 登录表单等待超时")
        return False

    async def fill_login_form(self, username, password):
        """填写登录表单"""
        try:
            # 找到并填写用户名
            username_selectors = ['#login-account-name', '#username', 'input[name="username"]']
            for selector in username_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    await element.click()
                    await asyncio.sleep(0.5)
                    await element.fill(username)
                    logger.info("✅ 已填写用户名")
                    break
            
            # 找到并填写密码
            password_selectors = ['#login-account-password', '#password', 'input[name="password"]']
            for selector in password_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    await element.click()
                    await asyncio.sleep(0.5)
                    await element.fill(password)
                    logger.info("✅ 已填写密码")
                    break
            
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    async def submit_login(self):
        """提交登录"""
        try:
            login_buttons = [
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")',
                '.btn-primary',
                '.btn-login'
            ]
            
            for selector in login_buttons:
                button = await self.page.query_selector(selector)
                if button and await button.is_visible():
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    await button.click()
                    logger.info("✅ 已点击登录按钮")
                    
                    # 等待登录处理
                    await asyncio.sleep(8)
                    return True
            
            logger.error("❌ 未找到登录按钮")
            return False
            
        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    async def verify_login_result(self):
        """验证登录结果"""
        logger.info("🔍 验证登录结果...")
        
        # 检查是否跳转到其他页面
        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info("✅ 页面已跳转，可能登录成功")
            return await self.check_login_status()
        
        # 检查错误信息
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger']
        for selector in error_selectors:
            error_element = await self.page.query_selector(selector)
            if error_element:
                error_text = await error_element.text_content()
                logger.error(f"❌ 登录错误: {error_text}")
                return False
        
        # 最终检查登录状态
        return await self.check_login_status()

    async def check_login_status(self):
        """检查登录状态"""
        try:
            username = self.credentials['username']
            
            # 方法1: 检查页面内容
            content = await self.page.content()
            if username.lower() in content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {username}")
                return True
            
            # 方法2: 检查用户元素
            user_indicators = ['img.avatar', '.current-user', '[data-user-menu]', '.header-dropdown-toggle']
            for selector in user_indicators:
                element = await self.page.query_selector(selector)
                if element and await element.is_visible():
                    logger.success(f"✅ 找到用户元素: {selector}")
                    return True
            
            # 方法3: 访问个人资料页面
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            await self.page.goto(profile_url, timeout=30000)
            await asyncio.sleep(3)
            
            profile_content = await self.page.content()
            if username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                # 返回原页面
                await self.page.go_back(timeout=30000)
                return True
            
            logger.warning("❌ 无法验证登录状态")
            return False
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    async def perform_browsing_actions(self):
        """执行浏览动作"""
        if not await self.check_login_status():
            logger.error("❌ 未登录，跳过浏览")
            return
        
        try:
            logger.info("📚 开始浏览动作")
            
            # 访问最新主题
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(3)
            
            # 获取主题链接
            topic_links = await self.page.query_selector_all('a.title, .topic-list-item a, .topic-title a')
            logger.info(f"📖 找到 {len(topic_links)} 个主题")
            
            if not topic_links:
                logger.warning("⚠️ 未找到主题链接")
                return
            
            # 随机选择少量主题浏览（GitHub Actions优化）
            browse_count = min(MAX_TOPICS_TO_BROWSE, len(topic_links), 3)  # 最多3个
            selected_topics = random.sample(topic_links, browse_count)
            
            logger.info(f"🎯 浏览 {browse_count} 个主题")
            
            for i, topic in enumerate(selected_topics):
                try:
                    logger.info(f"🔍 浏览第 {i+1}/{browse_count} 个主题")
                    
                    href = await topic.get_attribute('href')
                    if href:
                        topic_url = urljoin(self.site_config['base_url'], href)
                        await self.browse_topic(topic_url)
                    
                    # 主题间延迟
                    if i < browse_count - 1:
                        await asyncio.sleep(random.uniform(3, 8))
                        
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
            
            logger.success("✅ 浏览完成")
            
        except Exception as e:
            logger.error(f"浏览过程异常: {str(e)}")

    async def browse_topic(self, topic_url):
        """浏览单个主题"""
        try:
            new_page = await self.context.new_page()
            await new_page.goto(topic_url, timeout=60000)
            await asyncio.sleep(2)
            
            logger.info(f"📄 浏览: {await new_page.title()}")
            
            # 简单滚动模拟阅读
            for _ in range(random.randint(2, 4)):
                scroll_amount = random.randint(300, 600)
                await new_page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                await asyncio.sleep(random.uniform(2, 4))
            
            await new_page.close()
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")

    async def save_session_data(self):
        """保存会话数据"""
        try:
            # 保存浏览器状态
            state = await self.context.storage_state()
            GitHubCacheManager.save_site_cache(state, self.site_config['name'], 'browser_state')
            
            # 保存Cloudflare cookies
            await CloudflareBypass.save_cloudflare_cookies(self.context, self.site_config['name'])
            
            logger.info("💾 会话数据已保存")
            
        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    async def clear_cache(self):
        """清除缓存"""
        cache_files = [
            f"browser_state_{self.site_config['name']}.json",
            f"cf_cookies_{self.site_config['name']}.json"
        ]
        
        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"🗑️ 已清除: {file}")

    async def cleanup(self):
        """清理资源"""
        try:
            if self.context:
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
    
    logger.info("🚀 LinuxDo自动化脚本启动 (GitHub Actions专用版)")
    
    # 选择站点
    target_sites = SITES if args.site == 'all' else [s for s in SITES if s['name'] == args.site]
    
    # 初始化浏览器
    browser, playwright = await GitHubBrowserManager.init_browser()
    
    try:
        results = []
        
        for site_config in target_sites:
            logger.info(f"🎯 处理站点: {site_config['name']}")
            
            automator = SimpleSiteAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)
            
            results.append({
                'site': site_config['name'],
                'success': success
            })
            
            # 站点间延迟
            if site_config != target_sites[-1]:
                await asyncio.sleep(random.uniform(10, 20))
        
        # 输出结果
        logger.info("📊 执行结果:")
        table_data = [[r['site'], "✅" if r['success'] else "❌"] for r in results]
        print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))
        
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
