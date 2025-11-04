#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 重构版
功能：自动登录 Linux.do 和 IDCFlare 论坛，浏览主题，模拟人类行为，突破Cloudflare验证
作者：自动化脚本
版本：5.0 - 完整重构版
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

# 超时和重试配置
PAGE_TIMEOUT = 180000
RETRY_TIMES = 2

# 浏览配置
BROWSE_ENABLED = True
MAX_TOPICS_TO_BROWSE = 10
SCROLL_ATTEMPTS = 10

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
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='LinuxDo 多站点自动化脚本')
    parser.add_argument('--site', type=str, help='指定运行的站点', 
                       choices=['linux_do', 'idcflare', 'all'], default='all')
    parser.add_argument('--verbose', action='store_true', help='详细输出模式')
    parser.add_argument('--clear-cache', action='store_true', help='清除缓存')
    return parser.parse_args()

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类"""
    
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
        """从文件加载缓存数据"""
        if os.path.exists(file_name):
            try:
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                
                age_hours = CacheManager.get_file_age_hours(file_name)
                if age_hours is not None:
                    age_status = "全新" if age_hours < 0.1 else "较新" if age_hours < 6 else "较旧"
                    logger.info(f"📦 加载缓存 {file_name} (年龄: {age_hours:.3f}小时, {age_status})")
                
                return data.get('data', data)
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存数据到缓存文件"""
        try:
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '5.0',
                'file_created': time.time(),
                'run_id': os.getenv('GITHUB_RUN_ID', 'local')
            }
            
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            current_time = time.time()
            os.utime(file_name, (current_time, current_time))
            
            new_age = CacheManager.get_file_age_hours(file_name)
            file_size = os.path.getsize(file_name)
            logger.info(f"💾 缓存已保存到 {file_name} (新年龄: {new_age:.3f}小时, 大小: {file_size} 字节)")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_site_cache(site_name, cache_type):
        """加载特定站点的缓存"""
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        """保存特定站点的缓存"""
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.save_cache(data, file_name)

# ======================== Cloudflare和Turnstile处理器 ========================
class SecurityHandler:
    """安全验证处理类"""
    
    @staticmethod
    async def handle_cloudflare(page, site_config, max_attempts=8, timeout=180):
        """处理Cloudflare验证"""
        domain = site_config['base_url'].replace('https://', '')
        start_time = time.time()
        logger.info(f"🛡️ 开始处理 {domain} Cloudflare验证")
        
        # 检查缓存中的Cloudflare cookies
        cached_cf_valid = await SecurityHandler.is_cached_cf_valid(site_config['name'])
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
                cf_valid = await SecurityHandler.is_cf_clearance_valid(page.context, domain)
                
                if cf_valid:
                    logger.success(f"✅ 检测到有效的 cf_clearance cookie")
                    
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
                    cf_valid_after_wait = await SecurityHandler.is_cf_clearance_valid(page.context, domain)
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
        final_cf_valid = await SecurityHandler.is_cf_clearance_valid(page.context, domain)
        page_title = await page.title()
        
        if final_cf_valid or (page_title != "请稍候…" and "Checking" not in page_title):
            logger.success(f"✅ 最终验证: {domain} Cloudflare验证通过")
            return True
        else:
            logger.warning(f"⚠️ 最终验证: {domain} Cloudflare验证未完全通过，但继续后续流程")
            return True

    @staticmethod
    async def handle_turnstile_verification(page, site_config):
        """处理Turnstile验证"""
        try:
            logger.info("🔍 检查Turnstile验证")
            
            # 检查是否存在Turnstile iframe
            turnstile_iframe = await page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            turnstile_element = await page.query_selector('[data-turnstile-widget]')
            cf_response_input = await page.query_selector('input[name="cf-turnstile-response"]')
            
            if turnstile_iframe or turnstile_element or cf_response_input:
                logger.warning("🛡️ 检测到Cloudflare Turnstile验证")
                
                # 等待一段时间让Turnstile自动验证
                logger.info("⏳ 等待Turnstile自动验证完成...")
                await asyncio.sleep(10)
                
                # 尝试获取Turnstile响应token
                turnstile_response = await page.evaluate("""
                    () => {
                        // 尝试从隐藏字段获取token
                        const input = document.querySelector('input[name="cf-turnstile-response"]');
                        if (input && input.value) {
                            return input.value;
                        }
                        
                        // 尝试调用Turnstile API获取响应
                        if (window.turnstile) {
                            return new Promise((resolve) => {
                                window.turnstile.getResponse(function(token) {
                                    resolve(token);
                                });
                            });
                        }
                        
                        return null;
                    }
                """)
                
                if turnstile_response:
                    logger.success(f"✅ 成功获取Turnstile token: {turnstile_response[:20]}...")
                    
                    # 确保token被设置到表单中
                    if cf_response_input:
                        await page.evaluate(f"""
                            () => {{
                                const input = document.querySelector('input[name="cf-turnstile-response"]');
                                if (input) {{
                                    input.value = "{turnstile_response}";
                                }}
                            }}
                        """)
                    
                    return True
                else:
                    logger.warning("⚠️ 无法自动获取Turnstile token，可能需要手动干预")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"处理Turnstile验证时出错: {str(e)}")
            return False

    @staticmethod
    async def is_cached_cf_valid(site_name):
        """检查缓存的Cloudflare cookie是否有效"""
        try:
            cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
            if not cf_cookies:
                return False
            
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
        """检查cf_clearance cookie是否有效"""
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
    """浏览器管理类"""
    
    @staticmethod
    async def init_browser():
        """初始化浏览器实例"""
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
        """创建浏览器上下文"""
        has_browser_state = CacheManager.load_site_cache(site_name, 'browser_state') is not None
        has_cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies') is not None
        
        logger.info(f"🔍 {site_name} 缓存状态 - 浏览器状态: {'✅' if has_browser_state else '❌'}, Cloudflare Cookies: {'✅' if has_cf_cookies else '❌'}")
        
        storage_state = CacheManager.load_site_cache(site_name, 'browser_state')
        
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
        """将缓存加载到浏览器上下文中"""
        try:
            cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
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
        """获取增强的反检测脚本"""
        return """
            // 基础反检测
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // 模拟人类行为模式
            const originalDateNow = Date.now;
            Date.now = function() {
                return originalDateNow() + Math.floor(Math.random() * 100);
            };
            
            // 性能API模拟
            if (!window.performance) {
                window.performance = {
                    memory: {
                        usedJSHeapSize: Math.floor(Math.random() * 100000000),
                        totalJSHeapSize: Math.floor(Math.random() * 200000000),
                        jsHeapSizeLimit: Math.floor(Math.random() * 400000000)
                    },
                    timing: {
                        navigationStart: originalDateNow() - Math.floor(Math.random() * 5000),
                        loadEventEnd: originalDateNow() - Math.floor(Math.random() * 3000),
                        domLoading: originalDateNow() - Math.floor(Math.random() * 4000)
                    }
                };
            }
            
            // 请求拦截和模拟
            const originalFetch = window.fetch;
            window.fetch = function(...args) {
                const url = args[0];
                if (typeof url === 'string' && 
                    (url.includes('analytics') || url.includes('statistics') || 
                     url.includes('track') || url.includes('count'))) {
                    return originalFetch.apply(this, args).catch(() => {
                        return Promise.resolve(new Response(null, {status: 200}));
                    });
                }
                return originalFetch.apply(this, args);
            };
            
            // XMLHttpRequest拦截
            const originalXHROpen = XMLHttpRequest.prototype.open;
            const originalXHRSend = XMLHttpRequest.prototype.send;
            
            XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                this._url = url;
                return originalXHROpen.apply(this, [method, url, ...rest]);
            };
            
            XMLHttpRequest.prototype.send = function(...args) {
                if (this._url && (this._url.includes('analytics') || 
                    this._url.includes('statistics') || this._url.includes('count'))) {
                    this.addEventListener('load', () => {
                        console.log('统计请求完成:', this._url);
                    });
                    this.addEventListener('error', () => {
                        console.log('统计请求失败，但继续执行:', this._url);
                    });
                }
                return originalXHRSend.apply(this, args);
            };
            
            // 页面可见性API
            Object.defineProperty(document, 'hidden', { get: () => false });
            Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
            
            // 用户行为事件监听器触发
            document.addEventListener('DOMContentLoaded', () => {
                setTimeout(() => {
                    window.dispatchEvent(new Event('pageview'));
                    if (typeof window.onPageView === 'function') {
                        window.onPageView();
                    }
                }, 1000);
            });
            
            // 鼠标移动和点击事件模拟
            let lastMoveTime = 0;
            document.addEventListener('mousemove', (e) => {
                const now = Date.now();
                if (now - lastMoveTime > 1000) {
                    lastMoveTime = now;
                    window.dispatchEvent(new CustomEvent('userActivity', {
                        detail: { type: 'mousemove', x: e.clientX, y: e.clientY }
                    }));
                }
            });
            
            document.addEventListener('click', (e) => {
                window.dispatchEvent(new CustomEvent('userActivity', {
                    detail: { type: 'click', target: e.target.tagName }
                }));
            });
            
            // 滚动事件统计
            let lastScrollTime = 0;
            window.addEventListener('scroll', () => {
                const now = Date.now();
                if (now - lastScrollTime > 500) {
                    lastScrollTime = now;
                    window.dispatchEvent(new CustomEvent('scrollActivity', {
                        detail: { 
                            scrollY: window.scrollY,
                            scrollPercent: (window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100
                        }
                    }));
                }
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
            
            console.log('🔧 增强的JS环境模拟已加载');
        """

# ======================== 站点自动化主类 ========================
class SiteAutomator:
    """站点自动化主类"""
    
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.retry_count = 0
        self.session_data = CacheManager.load_site_cache(site_config['name'], 'session_data') or {}
        self.cf_passed = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.domain = site_config['base_url'].replace('https://', '')
        self.cache_saved = False
        self.viewport = VIEWPORT_SIZES[hash(site_config['name']) % len(VIEWPORT_SIZES)]

    async def run_for_site(self, browser, playwright):
        """为指定站点运行自动化流程"""
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
                        await self.save_all_caches()
                    else:
                        # 缓存失败，进行完整验证流程
                        logger.warning(f"⚠️ {self.site_config['name']} 缓存优先流程失败，开始完整验证")
                        full_success = await self.full_verification_process()
                        self.is_logged_in = full_success

                    if self.is_logged_in:
                        logger.success(f"✅ {self.site_config['name']} 登录成功，开始执行后续任务")
                        
                        # 浏览主题
                        await self.browse_topics()
                        
                        # 获取连接信息
                        await self.print_connect_info()
                        
                        # 保存最终状态
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
        """尝试缓存优先访问策略"""
        try:
            cf_cache_valid = await SecurityHandler.is_cached_cf_valid(self.site_config['name'])
            
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
                    return await self.enhanced_login_process()
            else:
                logger.info(f"📭 无有效Cloudflare缓存")
                return False
                
        except Exception as e:
            logger.error(f"缓存优先流程异常: {str(e)}")
            return False

    async def full_verification_process(self):
        """执行完整的验证流程"""
        try:
            # Cloudflare验证
            await self.page.goto(self.site_config['base_url'], wait_until='networkidle', timeout=120000)
            
            self.cf_passed = await SecurityHandler.handle_cloudflare(
                self.page, self.site_config, max_attempts=8, timeout=180
            )
            
            if self.cf_passed:
                logger.success(f"✅ {self.site_config['name']} Cloudflare验证通过")
            
            # 检查登录状态
            cached_login_success = await self.enhanced_check_login_status()
            if cached_login_success:
                logger.success(f"✅ {self.site_config['name']} 缓存登录成功")
                if not self.cache_saved:
                    await self.save_all_caches()
                return True
            else:
                logger.warning(f"⚠️ 需要重新登录")
                login_success = await self.enhanced_login_process()
                if login_success and not self.cache_saved:
                    await self.save_all_caches()
                return login_success
                
        except Exception as e:
            logger.error(f"完整验证流程异常: {str(e)}")
            return False

    async def enhanced_login_process(self):
        """增强的登录流程，处理动态内容和验证"""
        try:
            logger.info(f"🔐 开始 {self.site_config['name']} 增强登录流程")
            
            # 清除可能的旧会话
            await self.page.context.clear_cookies()
            
            # 导航到登录页面
            await self.page.goto(self.site_config['login_url'], wait_until='networkidle', timeout=90000)
            await asyncio.sleep(5)
            
            # 检查机器人验证
            await self.detect_bot_verifications()
            
            # 处理Turnstile验证
            turnstile_success = await SecurityHandler.handle_turnstile_verification(self.page, self.site_config)
            if not turnstile_success:
                logger.warning("⚠️ Turnstile验证处理可能失败，继续尝试登录")
            
            # 等待登录表单动态加载
            login_form_loaded = await self.wait_for_login_form()
            if not login_form_loaded:
                logger.error("❌ 登录表单加载失败")
                return False
            
            # 获取CSRF Token
            csrf_token = await self.extract_csrf_token()
            if csrf_token:
                logger.info(f"✅ 成功获取CSRF Token: {csrf_token[:20]}...")
            
            # 填写登录信息
            username = self.credentials['username']
            password = self.credentials['password']
            
            # 模拟人类输入
            await self.simulate_human_typing('#login-account-name', username)
            await asyncio.sleep(random.uniform(1, 2))
            await self.simulate_human_typing('#login-account-password', password)
            await asyncio.sleep(2)
            
            # 点击登录按钮
            login_success = await self.click_login_button()
            if not login_success:
                return False
            
            # 等待登录结果
            await asyncio.sleep(10)
            
            # 验证登录成功
            return await self.verify_login_success()
                
        except Exception as e:
            logger.error(f"{self.site_config['name']} 登录过程异常: {e}")
            return False

    async def detect_bot_verifications(self):
        """检测机器人验证"""
        logger.info("🔍 检测页面上的机器人验证机制")
        
        # 检查常见的验证机制
        verifications = {
            'cloudflare_turnstile': await self.page.query_selector('iframe[src*="challenges.cloudflare.com"]'),
            'recaptcha': await self.page.query_selector('.g-recaptcha, [data-sitekey]'),
            'hcaptcha': await self.page.query_selector('.h-captcha, iframe[src*="hcaptcha.com"]'),
            'cloudflare_challenge': "请稍候…" in await self.page.title() or "Checking your browser" in await self.page.content(),
            'login_form': await self.page.query_selector('#login-account-name, #username, input[name="username"]')
        }
        
        detected = []
        for name, element in verifications.items():
            if element or (name == 'cloudflare_challenge' and verifications[name]):
                detected.append(name)
                logger.warning(f"🛡️ 检测到 {name} 验证")
        
        if detected:
            logger.warning(f"⚠️ 页面包含以下验证机制: {', '.join(detected)}")
        else:
            logger.info("✅ 未检测到明显的机器人验证机制")
        
        return detected

    async def wait_for_login_form(self, max_attempts=10):
        """等待登录表单加载完成"""
        logger.info("⏳ 等待登录表单加载...")
        
        for attempt in range(max_attempts):
            # 检查登录表单元素
            username_field = await self.page.query_selector('#login-account-name, #username, input[name="username"]')
            password_field = await self.page.query_selector('#login-account-password, #password, input[name="password"]')
            login_button = await self.page.query_selector('#login-button, button[type="submit"], input[type="submit"]')
            
            if username_field and password_field:
                logger.success("✅ 登录表单已加载完成")
                return True
            
            logger.info(f"⏳ 等待登录表单... ({attempt + 1}/{max_attempts})")
            await asyncio.sleep(2)
        
        logger.error("❌ 登录表单加载超时")
        return False

    async def extract_csrf_token(self):
        """提取CSRF Token"""
        try:
            # 从meta标签获取
            csrf_token = await self.page.evaluate("""
                () => {
                    const meta = document.querySelector('meta[name="csrf-token"]');
                    return meta ? meta.content : null;
                }
            """)
            
            if not csrf_token:
                # 从隐藏字段获取
                csrf_token = await self.page.evaluate("""
                    () => {
                        const input = document.querySelector('input[name="authenticity_token"]');
                        return input ? input.value : null;
                    }
                """)
            
            return csrf_token
        except Exception as e:
            logger.warning(f"提取CSRF Token失败: {str(e)}")
            return None

    async def simulate_human_typing(self, selector, text):
        """模拟人类打字"""
        try:
            await self.page.click(selector)
            await asyncio.sleep(random.uniform(0.5, 1))
            
            for char in text:
                await self.page.type(selector, char, delay=random.uniform(50, 150))
                await asyncio.sleep(random.uniform(0.1, 0.3))
                
        except Exception as e:
            logger.error(f"模拟输入失败: {str(e)}")
            # 失败时直接填充
            await self.page.fill(selector, text)

    async def click_login_button(self):
        """点击登录按钮"""
        login_button_selectors = [
            '#login-button', 
            'button[type="submit"]', 
            'input[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("Log In")',
            '.btn-login',
            '.login-button'
        ]
        
        for selector in login_button_selectors:
            try:
                login_btn = await self.page.query_selector(selector)
                if login_btn and await login_btn.is_visible():
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    
                    # 模拟人类点击前的小延迟
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    await login_btn.click()
                    return True
            except Exception as e:
                logger.debug(f"尝试选择器 {selector} 失败: {str(e)}")
                continue
        
        logger.error("❌ 未找到可点击的登录按钮")
        return False

    async def verify_login_success(self):
        """验证登录是否成功"""
        logger.info("🔍 验证登录状态...")
        
        # 检查URL是否跳转
        current_url = self.page.url
        if current_url == self.site_config['login_url']:
            logger.warning("⚠️ 仍在登录页面，可能登录失败")
            
            # 检查错误信息
            error_selectors = ['.alert-error', '.error', '.flash-error', '.alert.alert-error']
            for selector in error_selectors:
                error_elem = await self.page.query_selector(selector)
                if error_elem:
                    error_text = await error_elem.inner_text()
                    logger.error(f"❌ 登录错误: {error_text}")
                    return False
            
            return False
        
        # 使用增强的登录状态检查
        return await self.enhanced_check_login_status()

    async def enhanced_check_login_status(self):
        """增强的登录状态检查"""
        try:
            current_url = self.page.url
            page_title = await self.page.title()
            
            # 如果页面卡在Cloudflare验证，但cookie有效，尝试绕过
            if page_title == "请稍候…":
                cf_valid = await SecurityHandler.is_cf_clearance_valid(self.page.context, self.domain)
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
                        return await self.verify_username_displayed()
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
            
            # 如果无法确定状态，检查页面内容
            page_content = await self.page.content()
            if "请稍候" not in page_title and "Checking" not in page_title:
                username = self.credentials['username']
                if username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面内容中找到用户名: {username}")
                    return True
                
                if len(page_content) > 1000:
                    logger.success("✅ 页面显示正常内容，可能已登录")
                    return await self.verify_username_displayed()
            
            logger.warning(f"⚠️ 登录状态不确定，默认认为未登录。页面标题: {page_title}")
            return False
            
        except Exception as e:
            logger.warning(f"{self.site_config['name']} 检查登录状态时出错: {str(e)}")
            return False

    async def verify_username_displayed(self):
        """验证用户名是否显示在页面上"""
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

    async def browse_topics(self):
        """浏览主题帖"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return
        
        try:
            logger.info("📚 开始浏览主题帖")
            
            # 导航到最新主题页面
            await self.page.goto(self.site_config['latest_topics_url'], wait_until='networkidle', timeout=60000)
            await asyncio.sleep(3)
            
            # 获取主题列表
            topic_links = await self.page.query_selector_all('a.title, .topic-list-item a, .topic-title a')
            logger.info(f"📖 发现 {len(topic_links)} 个主题帖")
            
            if not topic_links:
                logger.warning("⚠️ 未找到主题帖链接")
                return
            
            # 随机选择主题
            topics_to_browse = min(MAX_TOPICS_TO_BROWSE, len(topic_links))
            selected_topics = random.sample(topic_links, topics_to_browse)
            
            logger.info(f"🎯 随机选择 {topics_to_browse} 个主题进行浏览")
            
            for i, topic_link in enumerate(selected_topics):
                try:
                    logger.info(f"🔍 浏览第 {i+1}/{topics_to_browse} 个主题")
                    
                    # 获取主题链接
                    href = await topic_link.get_attribute('href')
                    if not href:
                        continue
                    
                    full_url = urljoin(self.site_config['base_url'], href)
                    
                    # 在新标签页中打开主题
                    await self.browse_single_topic(full_url)
                    
                    # 主题间延迟
                    if i < topics_to_browse - 1:
                        delay = random.uniform(5, 15)
                        logger.info(f"⏳ 主题间延迟 {delay:.1f} 秒")
                        await asyncio.sleep(delay)
                        
                except Exception as e:
                    logger.error(f"浏览主题时出错: {str(e)}")
                    continue
            
            logger.success("✅ 主题浏览完成")
            
            # 浏览完成后保存最新缓存
            await self.save_all_caches()
            logger.info("💾 浏览完成，缓存已更新")
            
        except Exception as e:
            logger.error(f"浏览主题过程异常: {str(e)}")

    async def browse_single_topic(self, topic_url):
        """浏览单个主题帖"""
        try:
            # 创建新页面
            new_page = await self.context.new_page()
            await new_page.goto(topic_url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)
            
            logger.info(f"📄 浏览主题: {await new_page.title()}")
            
            # 随机点赞
            if random.random() < 0.003:
                await self.click_like(new_page)
            
            # 浏览帖子内容
            await self.browse_post_content(new_page)
            
            # 关闭页面
            await new_page.close()
            
        except Exception as e:
            logger.error(f"浏览单个主题时出错: {str(e)}")

    async def browse_post_content(self, page):
        """浏览帖子内容，模拟人类阅读行为"""
        prev_scroll = 0
        scroll_attempts = 0
        
        logger.info("👀 开始模拟阅读行为")
        
        while scroll_attempts < SCROLL_ATTEMPTS:
            try:
                # 随机滚动距离
                scroll_distance = random.randint(300, 800)
                await page.evaluate(f"window.scrollBy(0, {scroll_distance})")
                
                # 随机等待时间
                wait_time = random.uniform(2, 6)
                await asyncio.sleep(wait_time)
                
                # 检查是否到达底部
                current_scroll = await page.evaluate("window.scrollY")
                page_height = await page.evaluate("document.body.scrollHeight")
                window_height = await page.evaluate("window.innerHeight")
                
                if current_scroll + window_height >= page_height - 100:
                    logger.info("📜 已到达页面底部")
                    break
                
                # 检查是否卡住
                if abs(current_scroll - prev_scroll) < 10:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                
                prev_scroll = current_scroll
                
                # 随机退出概率
                if random.random() < 0.05:
                    logger.info("🎲 随机退出浏览")
                    break
                    
            except Exception as e:
                logger.error(f"滚动浏览时出错: {str(e)}")
                break
        
        logger.info("✅ 帖子浏览完成")

    async def click_like(self, page):
        """点赞帖子"""
        try:
            like_buttons = await page.query_selector_all('.like-button, .btn-like, [data-like]')
            for button in like_buttons:
                if await button.is_visible():
                    await button.click()
                    logger.info("👍 点赞成功")
                    await asyncio.sleep(random.uniform(1, 2))
                    return
            logger.info("ℹ️ 未找到可点赞的按钮或已点赞")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    async def print_connect_info(self):
        """打印连接信息"""
        try:
            logger.info("🔗 获取连接信息")
            
            # 创建新页面访问connect页面
            connect_page = await self.context.new_page()
            await connect_page.goto(self.site_config['connect_url'], wait_until='networkidle', timeout=60000)
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
                    print("--------------Connect Info-----------------")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                else:
                    logger.warning("⚠️ 未找到连接信息")
            else:
                logger.warning("⚠️ 未找到信息表格")
            
            await connect_page.close()
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

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
        """统一保存所有缓存"""
        try:
            # 保存 Cloudflare cookies
            await self.save_cf_cookies()
            
            # 保存浏览器状态
            if self.context:
                state = await self.context.storage_state()
                CacheManager.save_site_cache(state, self.site_config['name'], 'browser_state')
            
            # 更新并保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'retry_count': self.retry_count,
                'cf_passed': self.cf_passed,
                'last_updated': datetime.now().isoformat(),
                'topics_browsed': MAX_TOPICS_TO_BROWSE,
                'cache_strategy': 'always_overwrite_latest'
            })
            CacheManager.save_site_cache(self.session_data, self.site_config['name'], 'session_data')
            
            logger.info(f"✅ {self.site_config['name']} 所有缓存已保存（覆盖旧缓存）")
            self.cache_saved = True
        except Exception as e:
            logger.error(f"{self.site_config['name']} 保存缓存失败: {str(e)}")

    async def save_final_status(self, success=False):
        """保存最终状态"""
        final_status = {
            'success': success,
            'timestamp': datetime.now().isoformat(),
            'retry_count': self.retry_count,
            'login_status': 'success' if success else 'failed',
            'cf_passed': self.cf_passed,
            'topics_browsed': MAX_TOPICS_TO_BROWSE if success else 0,
            'message': '任务执行完成' if success else '任务执行失败',
            'cache_strategy': 'always_overwrite_latest'
        }
        CacheManager.save_site_cache(final_status, self.site_config['name'], 'final_status')

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
                CacheManager.save_site_cache(cf_cookies, self.site_config['name'], 'cf_cookies')
                logger.info(f"✅ {self.site_config['name']} Cloudflare Cookies 已保存: {len(cf_cookies)} 个")
                
        except Exception as e:
            logger.error(f"❌ 保存 {self.site_config['name']} Cloudflare cookies 失败: {e}")

    async def close_context(self):
        """关闭浏览器上下文"""
        try:
            if self.context:
                if not self.cache_saved and self.is_logged_in:
                    await self.save_all_caches()
                await self.context.close()
                logger.info(f"✅ {self.site_config['name']} 浏览器上下文已关闭")
        except Exception as e:
            logger.error(f"关闭上下文失败: {str(e)}")

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
    
    logger.info("🚀 LinuxDo多站点自动化脚本启动 (完整重构版)")
    
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
            
            automator = SiteAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)
            
            results.append({
                'site': site_config['name'],
                'success': success,
                'login_status': automator.is_logged_in,
                'cf_passed': automator.cf_passed,
                'retry_count': automator.retry_count
            })
            
            # 站点间延迟
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
