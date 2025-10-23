#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本
功能：自动登录 Linux.do 和 IDCFlare 论坛，浏览主题，模拟人类行为
作者：自动化脚本
版本：4.2
"""

import os
import sys
import time
import random
import asyncio
import json
import math
import traceback
import argparse
from datetime import datetime, timedelta
from urllib.parse import urljoin
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from tabulate import tabulate

# ======================== 配置常量 ========================
# 站点认证信息配置
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

# 无头模式配置
HEADLESS_MODE = os.getenv('HEADLESS', 'true').lower() == 'true'

# 站点配置列表
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

# 超时和重试配置
PAGE_TIMEOUT = 180000
RETRY_TIMES = 2

# ======================== 反检测配置 ========================
# 用户代理列表
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/127.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/126.0.0.0 Safari/537.36'
]

# 视口尺寸列表
VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864},
    {'width': 1440, 'height': 900},
    {'width': 1280, 'height': 720}
]


# ======================== 命令行参数解析 ========================
def parse_arguments():
    """
    解析命令行参数
    
    Returns:
        argparse.Namespace: 解析后的命令行参数
    """
    parser = argparse.ArgumentParser(description='LinuxDo 多站点自动化脚本')
    parser.add_argument('--site', type=str, help='指定运行的站点', 
                       choices=['linux_do', 'idcflare', 'all'], default='all')
    parser.add_argument('--verbose', action='store_true', help='详细输出模式')
    parser.add_argument('--clear-cache', action='store_true', help='清除缓存')
    return parser.parse_args()


# ======================== 终极缓存管理器 ========================
class UltimateCacheManager:
    """高级缓存管理类，负责缓存文件的读写和管理"""
    
    @staticmethod
    def get_file_age_hours(file_path):
        """
        获取文件年龄（小时）
        
        Args:
            file_path (str): 文件路径
            
        Returns:
            float or None: 文件年龄（小时），如果文件不存在返回None
        """
        if not os.path.exists(file_path):
            return None
        file_mtime = os.path.getmtime(file_path)
        current_time = time.time()
        age_hours = (current_time - file_mtime) / 3600
        return age_hours

    @staticmethod
    def load_cache(file_name):
        """
        从文件加载缓存数据
        
        Args:
            file_name (str): 缓存文件名
            
        Returns:
            any: 缓存数据，如果加载失败返回None
        """
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
        """
        保存数据到缓存文件
        
        Args:
            data (any): 要保存的数据
            file_name (str): 缓存文件名
            
        Returns:
            bool: 保存成功返回True，否则返回False
        """
        try:
            # 强制更新文件时间戳，确保覆盖旧缓存
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '4.2',  # 版本更新
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

    @staticmethod
    def load_site_cache(site_name, cache_type):
        """
        加载特定站点的缓存
        
        Args:
            site_name (str): 站点名称
            cache_type (str): 缓存类型
            
        Returns:
            any: 缓存数据
        """
        file_name = f"{cache_type}_{site_name}.json"
        return UltimateCacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        """
        保存特定站点的缓存
        
        Args:
            data (any): 要保存的数据
            site_name (str): 站点名称
            cache_type (str): 缓存类型
            
        Returns:
            bool: 保存成功返回True，否则返回False
        """
        file_name = f"{cache_type}_{site_name}.json"
        return UltimateCacheManager.save_cache(data, file_name)


# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类"""
    
    @staticmethod
    async def handle_cloudflare(page, site_config, max_attempts=8, timeout=180):
        """
        处理Cloudflare验证
        
        Args:
            page: Playwright页面对象
            site_config (dict): 站点配置
            max_attempts (int): 最大尝试次数
            timeout (int): 超时时间（秒）
            
        Returns:
            bool: 验证通过返回True，否则返回False
        """
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
        """
        检查缓存的Cloudflare cookie是否有效
        
        Args:
            site_name (str): 站点名称
            
        Returns:
            bool: 缓存有效返回True，否则返回False
        """
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
        """
        检查cf_clearance cookie是否有效
        
        Args:
            context: Playwright上下文对象
            domain (str): 域名
            
        Returns:
            bool: cookie有效返回True，否则返回False
        """
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
    """浏览器管理类，负责浏览器的初始化和上下文创建"""
    
    @staticmethod
    async def init_browser():
        """
        初始化浏览器实例
        
        Returns:
            tuple: (browser, playwright) 元组
        """
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
        """
        创建浏览器上下文
        
        Args:
            browser: 浏览器实例
            site_name (str): 站点名称
            
        Returns:
            context: 浏览器上下文对象
        """
        has_browser_state = UltimateCacheManager.load_site_cache(site_name, 'browser_state') is not None
        has_cf_cookies = UltimateCacheManager.load_site_cache(site_name, 'cf_cookies') is not None
        
        logger.info(f"🔍 {site_name} 缓存状态 - 浏览器状态: {'✅' if has_browser_state else '❌'}, Cloudflare Cookies: {'✅' if has_cf_cookies else '❌'}")
        
        storage_state = UltimateCacheManager.load_site_cache(site_name, 'browser_state')
        
        # 为每个站点固定 User-Agent 和视口，保持指纹一致性
        user_agent = USER_AGENTS[hash(site_name) % len(USER_AGENTS)]
        viewport = VIEWPORT_SIZES[hash(site_name) % len(VIEWPORT_SIZES)]
        
        logger.info(f"🆔 {site_name} 使用固定指纹 - UA: {user_agent[:50]}..., 视口: {viewport}")
        
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
        await context.add_init_script(BrowserManager.get_enhanced_anti_detection_script())
        
        return context

    @staticmethod
    async def load_caches_into_context(context, site_name):
        """
        将缓存加载到浏览器上下文中
        
        Args:
            context: 浏览器上下文对象
            site_name (str): 站点名称
        """
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
    def get_enhanced_anti_detection_script():
        """
        获取增强的反检测脚本
        
        Returns:
            str: 反检测JavaScript代码
        """
        return """
            // 增强的反检测脚本 - 保持指纹一致性
            Object.defineProperty(navigator, 'webdriver', { 
                get: () => undefined,
                configurable: true
            });
            
            // 覆盖插件信息
            Object.defineProperty(navigator, 'plugins', { 
                get: () => [1, 2, 3, 4, 5],
                configurable: true
            });
            
            Object.defineProperty(navigator, 'languages', { 
                get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
            });
            
            // 屏蔽自动化特征
            window.chrome = { 
                runtime: {},
                loadTimes: function() {},
                csi: function() {}, 
                app: {isInstalled: false}
            };
            
            Object.defineProperty(navigator, 'platform', { 
                get: () => ['Win32', 'MacIntel', 'Linux x86_64'][Math.floor(Math.random() * 3)] 
            });
            
            Object.defineProperty(navigator, 'hardwareConcurrency', { 
                get: () => [4, 8, 12, 16][Math.floor(Math.random() * 4)] 
            });
            
            // 覆盖权限API
            const originalQuery = navigator.permissions.query;
            navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // 模拟真实的电池状态
            navigator.getBattery = async function() {
                return {
                    level: 0.7 + Math.random() * 0.3,
                    charging: Math.random() > 0.7,
                    chargingTime: Math.floor(Math.random() * 3600),
                    dischargingTime: Math.floor(Math.random() * 3600) + 3600,
                    addEventListener: function() {},
                    removeEventListener: function() {}
                };
            };
            
            // 覆盖日期和时间相关函数，避免被检测到脚本运行时间模式
            const originalDateNow = Date.now;
            Date.now = function() {
                return originalDateNow() + Math.floor(Math.random() * 100) - 50;
            };
        """


# ======================== 终极主自动化类 ========================
class UltimateSiteAutomator:
    """站点自动化主类，负责完整的自动化流程"""
    
    def __init__(self, site_config):
        """
        初始化站点自动化器
        
        Args:
            site_config (dict): 站点配置
        """
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
        self.viewport = VIEWPORT_SIZES[hash(site_config['name']) % len(VIEWPORT_SIZES)]  # 固定视口大小

    async def run_for_site(self, browser, playwright):
        """
        为指定站点运行自动化流程
        
        Args:
            browser: 浏览器实例
            playwright: Playwright实例
            
        Returns:
            bool: 执行成功返回True，否则返回False
        """
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
        """
        尝试缓存优先访问策略
        
        Returns:
            bool: 缓存访问成功返回True，否则返回False
        """
        try:
            # 检查是否有有效的Cloudflare缓存
            cf_cache_valid = await CloudflareHandler.is_cached_cf_valid(self.site_config['name'])
            
            if cf_cache_valid:
                logger.info(f"✅ 检测到有效的Cloudflare缓存，尝试直接访问")
                await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                await asyncio.sleep(5)
                
                login_status = await self.enhanced_check_login_status()
                if login_status:
                    logger.success(f"✅ 缓存优先流程成功 - 已登录")
                    return True
                else:
                    logger.warning(f"⚠️ Cloudflare缓存有效但未登录，尝试登录")
                    return await self.optimized_login()
            else:
                logger.info(f"📭 无有效Cloudflare缓存")
                return False
                
        except Exception as e:
            logger.error(f"缓存优先流程异常: {str(e)}")
            return False

    async def full_verification_process(self):
        """
        执行完整的验证流程
        
        Returns:
            bool: 验证成功返回True，否则返回False
        """
        try:
            # Cloudflare验证
            await self.page.goto(self.site_config['base_url'], wait_until='networkidle', timeout=120000)
            
            self.cf_passed = await CloudflareHandler.handle_cloudflare(
                self.page, self.site_config, max_attempts=8, timeout=180
            )
            
            if self.cf_passed:
                logger.success(f"✅ {self.site_config['name']} Cloudflare验证通过")
            
            # 检查登录状态
            cached_login_success = await self.enhanced_check_login_status()
            if cached_login_success:
                logger.success(f"✅ {self.site_config['name']} 缓存登录成功")
                # 关键修复：缓存登录成功也保存缓存
                if not self.cache_saved:
                    await self.save_all_caches()
                return True
            else:
                logger.warning(f"⚠️ 需要重新登录")
                login_success = await self.optimized_login()
                if login_success and not self.cache_saved:
                    await self.save_all_caches()
                return login_success
                
        except Exception as e:
            logger.error(f"完整验证流程异常: {str(e)}")
            return False

    async def enhanced_check_login_status(self):
        """
        增强的登录状态检查
        
        Returns:
            bool: 已登录返回True，否则返回False
        """
        try:
            current_url = self.page.url
            page_title = await self.page.title()
            
            # 如果页面卡在Cloudflare验证，但cookie有效，尝试绕过
            if page_title == "请稍候…":
                cf_valid = await CloudflareHandler.is_cf_clearance_valid(self.page.context, self.domain)
                if cf_valid:
                    logger.info("🔄 页面卡住但Cloudflare cookie有效，尝试访问/latest页面")
                    await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
                    await asyncio.sleep(5)
                    current_url = self.page.url
                    page_title = await self.page.title()
            
            # 检查用户相关元素
            user_indicators = [
                '#current-user', '#toggle-current-user', '.header-dropdown-toggle.current-user',
                'img.avatar', '.user-menu', '[data-user-menu]'
            ]
            
            for selector in user_indicators:
                try:
                    user_elem = await self.page.query_selector(selector)
                    if user_elem and await user_elem.is_visible():
                        logger.success(f"✅ 检测到用户元素: {selector}")
                        return await self.verify_username()
                except Exception:
                    continue
            
            # 检查登录按钮
            login_buttons = [
                '.login-button', 'button:has-text("登录")', 
                'button:has-text("Log In")', '.btn.btn-icon-text.login-button'
            ]
            
            for selector in login_buttons:
                try:
                    login_btn = await self.page.query_selector(selector)
                    if login_btn and await login_btn.is_visible():
                        logger.warning(f"❌ 检测到登录按钮: {selector}")
                        return False
                except Exception:
                    continue
            
            # 如果无法确定状态
            page_content = await self.page.content()
            if "请稍候" not in page_title and "Checking" not in page_title:
                username = self.credentials['username']
                if username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面内容中找到用户名: {username}")
                    return True
                
                if len(page_content) > 1000:
                    logger.success("✅ 页面显示正常内容，可能已登录")
                    return True
            
            logger.warning(f"⚠️ 登录状态不确定，默认认为未登录。页面标题: {page_title}")
            return False
            
        except Exception as e:
            logger.warning(f"{self.site_config['name']} 检查登录状态时出错: {str(e)}")
            return False

    async def verify_username(self):
        """
        验证用户名是否显示在页面上
        
        Returns:
            bool: 用户名验证成功返回True，否则返回False
        """
        username = self.credentials['username']
        
        # 方法1: 页面内容检查
        page_content = await self.page.content()
        if username.lower() in page_content.lower():
            logger.success(f"✅ 在页面内容中找到用户名: {username}")
            return True
        
        # 方法2: 用户菜单点击
        try:
            user_click_selectors = ['img.avatar', '.current-user', '[data-user-menu]', '.header-dropdown-toggle']
            for selector in user_click_selectors:
                user_elem = await self.page.query_selector(selector)
                if user_elem and await user_elem.is_visible():
                    await user_elem.click()
                    await asyncio.sleep(2)
                    
                    user_menu_content = await self.page.content()
                    if username.lower() in user_menu_content.lower():
                        logger.success(f"✅ 在用户菜单中找到用户名: {username}")
                        await self.page.click('body')
                        return True
                    
                    await self.page.click('body')
                    await asyncio.sleep(1)
                    break
        except Exception:
            pass
        
        # 方法3: 个人资料页面验证
        try:
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            await self.page.goto(profile_url, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(3)
            
            profile_content = await self.page.content()
            if username.lower() in profile_content.lower() or "个人资料" in await self.page.title():
                logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                await self.page.go_back(wait_until='networkidle')
                return True
                
            await self.page.go_back(wait_until='networkidle')
        except Exception:
            pass
        
        logger.warning(f"⚠️ 检测到用户元素但无法验证用户名 {username}，默认认为未登录")
        return False

    async def optimized_login(self):
        """
        优化的登录流程
        
        Returns:
            bool: 登录成功返回True，否则返回False
        """
        try:
            logger.info(f"🔐 开始 {self.site_config['name']} 优化登录流程")
            
            # 清除可能的旧会话
            await self.page.context.clear_cookies()
            
            # 导航到登录页面
            await self.page.goto(self.site_config['login_url'], wait_until='networkidle', timeout=90000)
            await asyncio.sleep(5)
            
            # 等待登录表单
            for i in range(5):
                try:
                    await self.page.wait_for_selector('#login-account-name', timeout=10000)
                    await self.page.wait_for_selector('#login-account-password', timeout=10000)
                    break
                except:
                    if i == 4:
                        logger.error("❌ 登录表单加载超时")
                        return False
                    await asyncio.sleep(3)
            
            # 填写登录信息
            username = self.credentials['username']
            password = self.credentials['password']
            
            await self.page.fill('#login-account-name', username)
            await self.page.fill('#login-account-password', password)
            await asyncio.sleep(2)
            
            # 点击登录按钮
            login_button_selectors = ['#login-button', 'button[type="submit"]', 'input[type="submit"]']
            for selector in login_button_selectors:
                try:
                    login_btn = await self.page.query_selector(selector)
                    if login_btn and await login_btn.is_visible():
                        await login_btn.click()
                        break
                except:
                    continue
            
            # 等待登录结果
            await asyncio.sleep(20)
            
            # 检查登录后的页面状态
            current_url = self.page.url
            
            if current_url != self.site_config['login_url']:
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
            await self.page.goto(self.site_config['base_url'], wait_until='networkidle', timeout=60000)
            await asyncio.sleep(5)
            
            return await self.enhanced_check_login_status()
                
        except Exception as e:
            logger.error(f"{self.site_config['name']} 登录过程异常: {e}")
            return False

    async def clear_caches(self):
        """清除所有缓存文件"""
        try:
            cache_types = ['session_data', 'browser_state', 'cf_cookies', 'final_status']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{self.site_config['name']}.json"
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info(f"✅ {self.site_config['name']} 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    async def clear_login_caches_only(self):
        """仅清除登录相关缓存，保留Cloudflare cookies"""
        try:
            cache_types = ['session_data', 'browser_state', 'final_status']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{self.site_config['name']}.json"
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info(f"✅ {self.site_config['name']} 登录缓存已清除，保留Cloudflare cookies")
            
        except Exception as e:
            logger.error(f"清除登录缓存失败: {str(e)}")

    async def save_all_caches(self):
        """统一保存所有缓存，确保每次运行都覆盖旧缓存"""
        try:
            # 保存 Cloudflare cookies
            await self.save_cf_cookies()
            
            # 保存浏览器状态
            if self.context:
                state = await self.context.storage_state()
                UltimateCacheManager.save_site_cache(state, self.site_config['name'], 'browser_state')
            
            # 更新并保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'retry_count': self.retry_count,
                'cf_passed': self.cf_passed,
                'last_updated': datetime.now().isoformat(),
                'cache_strategy': 'always_overwrite_latest'  # 明确标记覆盖策略
            })
            UltimateCacheManager.save_site_cache(self.session_data, self.site_config['name'], 'session_data')
            
            logger.info(f"✅ {self.site_config['name']} 所有缓存已保存（覆盖旧缓存）")
            self.cache_saved = True
        except Exception as e:
            logger.error(f"{self.site_config['name']} 保存缓存失败: {str(e)}")

    async def save_final_status(self, success=False):
        """
        保存最终状态
        
        Args:
            success (bool): 是否成功
        """
        final_status = {
            'success': success,
            'timestamp': datetime.now().isoformat(),
            'retry_count': self.retry_count,
            'login_status': 'success' if success else 'failed',
            'cf_passed': self.cf_passed,
            'message': '任务执行完成' if success else '任务执行失败',
            'cache_strategy': 'always_overwrite_latest'
        }
        UltimateCacheManager.save_site_cache(final_status, self.site_config['name'], 'final_status')

    async def save_cf_cookies(self):
        """保存Cloudflare cookies到缓存"""
        try:
            all_cookies = await self.context.cookies()
            target_domain = self.site_config['base_url'].replace('https://', '')
            cf_cookies = [
                cookie for cookie in all_cookies 
                if cookie.get('domain', '').endswith(target_domain) and 
                   (cookie.get('name') == 'cf_clearance' or 'cloudflare' in cookie.get('name', ''))
            ]
            
            if cf_cookies:
                UltimateCacheManager.save_site_cache(cf_cookies, self.site_config['name'], 'cf_cookies')
                logger.info(f"✅ {self.site_config['name']} Cloudflare Cookies 已保存: {len(cf_cookies)} 个")
                
        except Exception as e:
            logger.error(f"❌ 保存 {self.site_config['name']} Cloudflare cookies 失败: {e}")

    async def close_context(self):
        """关闭浏览器上下文"""
        try:
            if self.context:
                # 只在关闭时保存一次缓存，确保最终状态被保存
                if not self.cache_saved and self.is_logged_in:
                    await self.save_all_caches()
                await self.context.close()
                logger.info(f"✅ {self.site_config['name']} 浏览器上下文已关闭")
        except Exception as e:
            logger.error(f"关闭上下文失败: {str(e)}")

    async def browse_topics(self):
        """浏览论坛主题"""
        try:
            logger.info(f"📖 开始 {self.site_config['name']} 主题浏览")
            
            # 强化登录验证：在浏览前再次检查登录状态
            login_verified = await self.enhanced_check_login_status()
            if not login_verified:
                logger.error(f"❌ {self.site_config['name']} 浏览前登录验证失败，尝试重新登录")
                login_success = await self.optimized_login()
                if not login_success:
                    logger.error(f"❌ {self.site_config['name']} 重新登录失败，放弃浏览")
                    return
            
            browse_history = self.session_data.get('browse_history', [])
            
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000, wait_until='networkidle')
            await asyncio.sleep(random.uniform(3, 7))  # 等待页面稳定
            
            # 查找主题链接
            topic_links = await self.find_topic_links()
            
            if not topic_links:
                logger.warning(f"{self.site_config['name']} 未找到主题链接")
                return
            
            # 减少浏览数量，增加质量
            browse_count = min(random.randint(3, 5), len(topic_links))
            selected_topics = random.sample(topic_links, browse_count)
            
            logger.info(f"📚 {self.site_config['name']} 计划浏览 {browse_count} 个主题")
            
            success_count = 0
            for idx, topic in enumerate(selected_topics, 1):
                success = await self.browse_single_topic(topic, idx, browse_count, browse_history)
                if success:
                    success_count += 1
                    
                # 增加主题间延迟，避免模式化
                if idx < browse_count:
                    delay = random.uniform(15, 90)  # 15-90秒延迟
                    logger.info(f"⏳ 主题间延迟 {delay:.1f} 秒")
                    await asyncio.sleep(delay)
            
            # 更新会话数据
            self.session_data['browse_history'] = browse_history[-30:]  # 只保留最近30条
            self.session_data['last_browse'] = datetime.now().isoformat()
            self.session_data['total_browsed'] = self.session_data.get('total_browsed', 0) + success_count
            
            # 主题浏览完成后保存一次缓存
            if not self.cache_saved:
                await self.save_all_caches()
            
            logger.success(f"✅ {self.site_config['name']} 主题浏览完成: 成功 {success_count} 个主题")

        except Exception as e:
            logger.error(f"{self.site_config['name']} 主题浏览流程失败: {str(e)}")

    async def find_topic_links(self):
        """
        查找主题链接
        
        Returns:
            list: 主题链接元素列表
        """
        topic_selectors = [
            'a.title',
            '.title a', 
            'a.topic-title',
            '.topic-list-item a',
            'tr.topic-list-item a.title',
            '[data-topic-id] a'
        ]
        
        for selector in topic_selectors:
            try:
                links = await self.page.query_selector_all(selector)
                if links:
                    valid_links = []
                    for link in links:
                        href = await link.get_attribute('href')
                        if href and not href.startswith(('/user/', '/u/', '/tag/')):
                            valid_links.append(link)
                    
                    if valid_links:
                        logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(valid_links)} 个有效主题链接")
                        return valid_links
            except Exception:
                continue
        
        return []

    async def browse_single_topic(self, topic, topic_idx, total_topics, browse_history):
        """
        浏览单个主题
        
        Args:
            topic: 主题元素
            topic_idx (int): 主题索引
            total_topics (int): 总主题数
            browse_history (list): 浏览历史列表
            
        Returns:
            bool: 浏览成功返回True，否则返回False
        """
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
                # 移除User-Agent切换，保持指纹一致性
                await tab.goto(topic_url, timeout=45000, wait_until='domcontentloaded')
                
                # 更自然的初始等待
                await asyncio.sleep(random.uniform(2, 5))
                
                # 模拟更真实的阅读行为
                success = await self.simulate_human_reading(tab, title)
                
                if success:
                    browse_history.append(href)
                    return True
                return False
                    
            finally:
                await tab.close()
                
        except Exception as e:
            logger.error(f"{self.site_config['name']} 浏览单个主题失败: {str(e)}")
            return False

    async def simulate_human_reading(self, tab, title):
        """
        模拟真实的人类阅读行为
        
        Args:
            tab: 标签页对象
            title (str): 主题标题
            
        Returns:
            bool: 模拟成功返回True，否则返回False
        """
        try:
            # 1. 初始随机观察
            initial_pause = random.uniform(3, 8)
            logger.info(f"⏳ 初始观察 {initial_pause:.1f}秒")
            await asyncio.sleep(initial_pause)
            
            # 2. 获取页面内容长度，决定阅读时间
            content_length = await tab.evaluate("""
                () => {
                    const content = document.querySelector('.topic-post .cooked') || 
                                   document.querySelector('.post-content') ||
                                   document.body;
                    return content.textContent.length;
                }
            """)
            
            # 3. 基于内容长度计算合理阅读时间
            base_read_time = max(30, min(300, content_length / 50))  # 每50字符1秒，最小30秒，最大5分钟
            read_time_variation = random.uniform(0.7, 1.3)  # ±30% 变化
            total_read_time = base_read_time * read_time_variation
            
            logger.info(f"📖 预计阅读时间: {total_read_time:.1f}秒 (内容长度: {content_length}字符)")
            
            # 4. 分段滚动和停留
            scroll_steps = random.randint(3, 8)
            time_per_step = total_read_time / scroll_steps
            
            for step in range(scroll_steps):
                # 随机滚动位置
                scroll_ratio = random.uniform(0.1, 0.95)
                await tab.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_ratio});")
                
                # 随机停留时间
                stay_time = time_per_step * random.uniform(0.8, 1.2)
                await asyncio.sleep(stay_time)
                
                # 偶尔的随机交互
                if random.random() < 0.3:  # 30% 概率有额外交互
                    await self.random_interaction(tab)
            
            # 5. 最终滚动到底部并短暂停留
            await tab.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(random.uniform(2, 5))
            
            return True
            
        except Exception as e:
            logger.error(f"模拟阅读行为失败: {str(e)}")
            return False

    async def random_interaction(self, tab):
        """
        随机交互行为
        
        Args:
            tab: 标签页对象
        """
        try:
            actions = [
                self.simulate_mouse_movement,
                self.simulate_scroll_behavior,
                self.simulate_focus_behavior,
                self.simulate_selection_behavior
            ]
            
            # 随机选择1-2个交互行为
            selected_actions = random.sample(actions, random.randint(1, 2))
            for action in selected_actions:
                await action(tab)
                await asyncio.sleep(random.uniform(0.5, 2))
                
        except Exception as e:
            logger.debug(f"随机交互执行失败: {str(e)}")

    async def simulate_mouse_movement(self, tab):
        """
        模拟鼠标移动
        
        Args:
            tab: 标签页对象
        """
        viewport = self.viewport
        await tab.mouse.move(
            x=random.randint(100, viewport['width'] - 100),
            y=random.randint(100, viewport['height'] - 100),
            steps=random.randint(10, 25)  # 更平滑的移动
        )

    async def simulate_scroll_behavior(self, tab):
        """
        模拟滚动行为
        
        Args:
            tab: 标签页对象
        """
        scroll_amount = random.randint(100, 400)
        scroll_direction = 1 if random.random() > 0.5 else -1
        await tab.evaluate(f"window.scrollBy(0, {scroll_amount * scroll_direction});")

    async def simulate_focus_behavior(self, tab):
        """
        模拟焦点行为
        
        Args:
            tab: 标签页对象
        """
        await tab.evaluate("""
            () => {
                const elements = document.querySelectorAll('p, div, span');
                if (elements.length > 0) {
                    const randomElement = elements[Math.floor(Math.random() * elements.length)];
                    randomElement.focus();
                }
            }
        """)

    async def simulate_selection_behavior(self, tab):
        """
        模拟文本选择行为
        
        Args:
            tab: 标签页对象
        """
        await tab.evaluate("""
            () => {
                const selection = window.getSelection();
                selection.removeAllRanges();
                
                const elements = document.querySelectorAll('p, div');
                if (elements.length > 0) {
                    const element = elements[Math.floor(Math.random() * elements.length)];
                    if (element.textContent.length > 10) {
                        const range = document.createRange();
                        const start = Math.floor(Math.random() * (element.textContent.length - 10));
                        range.setStart(element.firstChild, start);
                        range.setEnd(element.firstChild, start + 5);
                        selection.addRange(range);
                    }
                }
            }
        """)


# ======================== 主执行函数 ========================
async def main():
    """主执行函数"""
    args = parse_arguments()
    
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="DEBUG" if args.verbose else "INFO"
    )
    
    logger.info("🚀 LinuxDo多站点自动化脚本启动")
    
    # 根据参数过滤站点
    target_sites = SITES
    if args.site != 'all':
        target_sites = [site for site in SITES if site['name'] == args.site]
        if not target_sites:
            logger.error(f"未找到站点: {args.site}")
            return
    
    # 清除缓存逻辑
    if args.clear_cache:
        for site_config in target_sites:
            cache_types = ['session_data', 'browser_state', 'cf_cookies', 'final_status']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{site_config['name']}.json"
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
    
    browser, playwright = await BrowserManager.init_browser()
    
    try:
        results = []
        
        for site_config in target_sites:
            logger.info(f"🎯 开始处理站点: {site_config['name']}")
            
            automator = UltimateSiteAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)
            
            results.append({
                'site': site_config['name'],
                'success': success,
                'login_status': automator.is_logged_in,
                'cf_passed': automator.cf_passed,
                'retry_count': automator.retry_count
            })
            
            # 站点间延迟 - 增加随机性
            if site_config != target_sites[-1]:
                delay = random.uniform(10, 30)
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
