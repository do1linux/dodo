#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 完整修复版
功能：自动登录 Linux.do 和 IDCFlare 论坛，严格验证登录状态，自动处理Cloudflare
特点：双重验证机制（私有主题访问 + 用户名确认），增强反检测，确保浏览记录被收集
"""

import os
import sys
import time
import random
import json
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# ======================== 配置常量 ========================
# 站点认证信息配置 - 请确保环境变量已设置
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

# 站点配置列表
SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'private_topic_url': 'https://linux.do/t/topic/1164438',  # 用于验证登录状态的私有主题
        'latest_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do',
        'user_url': 'https://linux.do/u',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'session_file': "session_data_linux_do.json"
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'private_topic_url': 'https://idcflare.com/t/topic/24',  # 用于验证登录状态的私有主题
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'session_file': "session_data_idcflare.json"
    }
]

# 配置项
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
COOKIES_EXPIRY_HOURS = int(os.environ.get("COOKIES_EXPIRY_HOURS", "24"))
MAX_CACHE_AGE_HOURS = int(os.environ.get("MAX_CACHE_AGE_HOURS", "168"))

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类 - 管理Cookies和Session数据"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        """获取缓存文件的完整路径"""
        cache_dir = CacheManager.get_cache_directory()
        return os.path.join(cache_dir, file_name)

    @staticmethod
    def load_cache(file_name):
        """从文件加载缓存数据"""
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 成功加载缓存: {file_name}")
                return data
            except Exception as e:
                logger.warning(f"⚠️ 缓存加载失败 {file_name}: {str(e)}")
                # 删除损坏的缓存文件
                try:
                    os.remove(file_path)
                    logger.info(f"🗑️ 已删除损坏的缓存文件: {file_name}")
                except:
                    pass
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存数据到缓存文件"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            file_size = os.path.getsize(file_path)
            logger.info(f"💾 缓存已保存: {file_name} (大小: {file_size} 字节)")
            return True
        except Exception as e:
            logger.error(f"❌ 缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def is_cache_valid(file_name, expiry_hours=COOKIES_EXPIRY_HOURS):
        """检查缓存是否有效（未过期且存在）"""
        file_path = CacheManager.get_cache_file_path(file_name)
        if not os.path.exists(file_path):
            return False
        
        try:
            file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            time_diff = datetime.now() - file_modified_time
            is_valid = time_diff.total_seconds() < expiry_hours * 3600
            
            if is_valid:
                logger.info(f"✅ 缓存有效: {file_name} (年龄: {time_diff.total_seconds()/3600:.1f}小时)")
            else:
                logger.warning(f"⚠️ 缓存过期: {file_name} (已存在{time_diff.total_seconds()/3600:.1f}小时)")
            
            return is_valid
        except Exception as e:
            logger.error(f"❌ 缓存验证失败: {str(e)}")
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

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类"""
    
    @staticmethod
    def is_cf_cookie_valid(cookies):
        """检查Cloudflare cookie是否有效"""
        try:
            if not cookies:
                return False
                
            for cookie in cookies:
                if cookie.get('name') == 'cf_clearance':
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > time.time():
                        logger.success("✅ 检测到有效的cf_clearance cookie")
                        return True
            return False
        except Exception:
            return False

    @staticmethod
    def handle_cloudflare(page, max_attempts=8, timeout=180):
        """
        处理Cloudflare验证
        
        Args:
            page: 页面对象
            max_attempts (int): 最大尝试次数
            timeout (int): 超时时间（秒）
            
        Returns:
            bool: 验证通过返回True，否则返回False
        """
        start_time = time.time()
        logger.info("🛡️ 开始处理Cloudflare验证")
        
        # 检查缓存的Cloudflare cookies
        cached_cookies = CacheManager.load_cache("cf_cookies.json")
        cached_cf_valid = CloudflareHandler.is_cf_cookie_valid(cached_cookies)
        
        if cached_cf_valid:
            logger.success("✅ 检测到有效的缓存Cloudflare cookie")
            try:
                # 尝试使用缓存cookies访问
                if cached_cookies:
                    page.set.cookies(cached_cookies)
                    page.get("https://linux.do")
                    time.sleep(5)
                    
                    page_title = page.title
                    if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                        logger.success("✅ 使用缓存成功绕过Cloudflare验证")
                        return True
            except Exception as e:
                logger.warning(f"⚠️ 使用缓存绕过失败: {str(e)}")
        
        # 完整验证流程
        logger.info("🔄 开始完整Cloudflare验证流程")
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = page.title
                
                # 检查页面是否已经正常加载
                if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                
                # 等待验证
                wait_time = random.uniform(8, 15)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
                # 偶尔刷新页面
                if attempt % 3 == 0 and attempt > 0:
                    logger.info("🔄 刷新页面")
                    page.refresh()
                    time.sleep(5)
                    
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)
        
        # 最终检查
        try:
            page_title = page.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                logger.success("✅ 最终验证: Cloudflare验证通过")
                return True
            else:
                logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
                return True
        except Exception:
            logger.warning("⚠️ 无法获取页面标题，继续后续流程")
            return True

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.browser = None
        self.page = None
        self.cache_saved = False
        
        # 初始化浏览器
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器 - 使用DrissionPage"""
        try:
            # 配置浏览器选项
            co = ChromiumOptions()
            
            if HEADLESS:
                co.headless(True)
            else:
                co.headless(False)
                
            # 反检测核心配置
            co.incognito(True)  # 使用隐身模式避免缓存干扰
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-features=VizDisplayCompositor")
            co.set_argument("--disable-background-timer-throttling")
            co.set_argument("--disable-backgrounding-occluded-windows")
            co.set_argument("--disable-renderer-backgrounding")
            co.set_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
            
            # 固定Windows用户代理
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            
            # 初始化浏览器
            self.browser = Chromium(co)
            self.page = self.browser.new_tab()
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            # 注入增强脚本
            self.inject_enhanced_script()
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def inject_enhanced_script(self):
        """注入增强的反检测脚本和Turnstile模拟"""
        enhanced_script = """
        // 增强的反检测脚本
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
        // 模拟完整的浏览器环境
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
        
        // 页面可见性API
        Object.defineProperty(document, 'hidden', { get: () => false });
        Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
        
        // Turnstile 模拟 - 在页面加载前就定义
        if (typeof window.turnstile === 'undefined') {
            Object.defineProperty(window, 'turnstile', {
                get: () => ({
                    ready: (fn) => {
                        console.log('Turnstile ready called');
                        setTimeout(fn, 100);
                    },
                    render: (element, options) => {
                        console.log('Turnstile render called', options);
                        return 'mock-widget-id-' + Date.now();
                    },
                    execute: (element, options) => {
                        console.log('Turnstile execute called', options);
                        return Promise.resolve('mock-token-' + Date.now());
                    },
                    reset: () => console.log('Turnstile reset called'),
                    getResponse: () => {
                        const response = 'mock-cf-turnstile-response-' + Date.now();
                        console.log('Turnstile getResponse called, returning:', response);
                        return response;
                    },
                    remove: () => console.log('Turnstile remove called')
                })
            });
            console.log('✅ Turnstile 模拟已加载');
        }
        
        // 统计请求拦截和确保
        const originalFetch = window.fetch;
        window.fetch = function(...args) {
            const url = args[0];
            if (typeof url === 'string' && 
                (url.includes('analytics') || url.includes('statistics') || 
                 url.includes('track') || url.includes('count'))) {
                console.log('📊 统计请求被发送:', url);
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
        
        // 用户行为事件模拟
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                setTimeout(() => {
                    window.dispatchEvent(new Event('pageview'));
                    if (typeof window.onPageView === 'function') {
                        window.onPageView();
                    }
                }, 1000);
            });
        } else {
            window.dispatchEvent(new Event('pageview'));
        }
        
        console.log('🔧 增强的JS环境模拟和Turnstile模拟已加载');
        """
        
        try:
            self.page.run_js(enhanced_script)
            logger.info("✅ 增强的反检测脚本和Turnstile模拟已注入")
        except Exception as e:
            logger.warning(f"⚠️ 注入脚本失败: {str(e)}")

    def get_all_cookies(self):
        """获取所有cookies"""
        try:
            # 使用DrissionPage的cookies方法
            cookies = self.browser.cookies()
            if cookies:
                logger.info(f"✅ 成功获取 {len(cookies)} 个cookies")
                return cookies
            else:
                logger.warning("⚠️ 未获取到cookies")
                return []
        except Exception as e:
            logger.error(f"❌ 获取cookies失败: {str(e)}")
            return []

    def save_all_caches(self, force_save=False):
        """统一保存所有缓存"""
        if self.cache_saved and not force_save:
            return
            
        try:
            # 保存cookies
            cookies = self.get_all_cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info("✅ Cloudflare Cookies已缓存")
            
            # 保存浏览器状态
            state_data = {
                'timestamp': datetime.now().isoformat(),
                'url': self.page.url,
                'title': self.page.title
            }
            CacheManager.save_site_cache(state_data, self.site_name, 'browser_state')
            
            # 更新并保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '4.0',
                'total_saved': self.session_data.get('total_saved', 0) + 1,
                'last_url': self.page.url
            })
            CacheManager.save_site_cache(self.session_data, self.site_name, 'session_data')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 所有缓存已保存")
            
        except Exception as e:
            logger.error(f"❌ 保存缓存失败: {str(e)}")

    def clear_caches(self):
        """清除所有缓存文件"""
        try:
            cache_types = ['session_data', 'browser_state', 'cf_cookies']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{self.site_name}.json"
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info(f"✅ {self.site_name} 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"❌ 清除缓存失败: {str(e)}")

    def try_cache_login(self):
        """尝试使用缓存登录"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录，跳过缓存")
            return False
            
        # 加载缓存的cookies
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies or not CloudflareHandler.is_cf_cookie_valid(cookies):
            logger.warning("⚠️ 无有效缓存Cookies")
            return False
        
        try:
            logger.info("🎯 尝试使用Cookies缓存登录...")
            
            # 访问首页
            self.page.get(self.site_config['base_url'])
            time.sleep(3)
            
            # 设置cookies
            self.page.set.cookies(cookies)
            time.sleep(2)
            
            # 刷新页面
            self.page.refresh()
            time.sleep(3)
            
            # 验证登录状态
            if self.strict_verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            else:
                logger.warning("⚠️ 缓存登录失败，需要重新登录")
                return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def strict_verify_login_status(self, max_retries=3):
        """
        严格的登录状态验证 - 双重验证机制
        1. 验证私有主题可访问且无错误提示
        2. 验证用户名存在于页面中
        """
        logger.info("🔍 执行严格登录状态验证...")
        
        for retry in range(max_retries):
            try:
                # 第一步：访问私有主题
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题: {private_url}")
                self.page.get(private_url)
                time.sleep(5)
                
                # 处理可能的Cloudflare验证
                CloudflareHandler.handle_cloudflare(self.page)
                time.sleep(3)
                
                # 获取页面内容和标题
                page_content = self.page.html
                page_title = self.page.title
                current_url = self.page.url
                
                logger.info(f"📄 私有主题页面标题: {page_title}")
                logger.info(f"🌐 当前URL: {current_url}")
                
                # 检查是否在登录页面
                if 'login' in current_url or 'signin' in current_url:
                    logger.warning(f"❌ 被重定向到登录页面 (尝试 {retry + 1}/{max_retries})")
                    if retry < max_retries - 1:
                        time.sleep(3)
                        continue
                    return False
                
                # 检查是否有错误提示
                error_indicators = [
                    "Page Not Found",
                    "糟糕！该页面不存在或者是一个不公开页面。",
                    "Oops! This page doesn't exist or is not a public page.",
                    "page doesn't exist",
                    "not a public page"
                ]
                
                for indicator in error_indicators:
                    if indicator.lower() in page_content.lower():
                        logger.error(f"❌ 私有主题访问失败: {indicator}")
                        return False
                
                logger.success("✅ 私有主题访问成功 - 无错误提示")
                
                # 第二步：验证用户名存在
                if self.username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面中找到用户名: {self.username}")
                    # 双重验证通过
                    logger.success("🎉 双重验证通过 - 登录状态有效")
                    return True
                else:
                    logger.warning(f"❌ 在页面中未找到用户名: {self.username} (尝试 {retry + 1}/{max_retries})")
                    if retry < max_retries - 1:
                        wait_time = random.uniform(3, 6)
                        logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                
            except Exception as e:
                logger.error(f"❌ 登录状态验证异常: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(3)
        
        logger.error("❌ 所有登录状态验证尝试均失败")
        return False

    def login(self):
        """执行登录流程"""
        # 清除旧cookies
        self.page.set.cookies([])
        
        logger.info("🔐 执行登录流程...")
        self.page.get(self.site_config['login_url'])
        time.sleep(3)
        
        # 处理Cloudflare验证
        CloudflareHandler.handle_cloudflare(self.page)
        time.sleep(3)
        
        # 注入脚本
        self.inject_enhanced_script()
        
        # 填写登录信息
        try:
            # 等待表单元素出现
            time.sleep(2)
            
            # 查找并填写用户名
            username_field = self.page.ele("#login-account-name")
            if not username_field:
                logger.error("❌ 找不到用户名字段")
                return False
            
            # 模拟人类输入速度
            logger.info("⌨️ 输入用户名...")
            for char in self.username:
                username_field.input(char)
                time.sleep(random.uniform(0.05, 0.15))
            time.sleep(random.uniform(0.5, 1))
            
            # 查找并填写密码
            password_field = self.page.ele("#login-account-password")
            if not password_field:
                logger.error("❌ 找不到密码字段")
                return False
            
            logger.info("⌨️ 输入密码...")
            for char in self.password:
                password_field.input(char)
                time.sleep(random.uniform(0.05, 0.15))
            time.sleep(random.uniform(0.5, 1))
            
            # 点击登录按钮
            login_button = self.page.ele("#login-button")
            if not login_button:
                logger.error("❌ 找不到登录按钮")
                return False
            
            logger.info("🔑 点击登录按钮...")
            login_button.click()
            time.sleep(10)  # 等待登录完成
            
            # 处理登录后的Cloudflare验证
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(5)
            
            # 严格验证登录状态
            if self.strict_verify_login_status():
                logger.success("✅ 登录成功")
                self.save_all_caches()
                return True
            else:
                logger.error("❌ 登录失败")
                # 登录失败时清除缓存
                self.clear_caches()
                return False
                
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            self.clear_caches()
            return False

    def ensure_logged_in(self):
        """确保用户已登录"""
        # 尝试缓存登录
        if not FORCE_LOGIN_EVERY_TIME:
            if self.try_cache_login():
                return True
        
        # 执行手动登录
        return self.login()

    def enhanced_browse_post(self, page, stay_time=35):
        """
        增强的浏览行为，确保统计被正确计数
        基于内容长度计算停留时间，模拟真实阅读
        """
        try:
            # 获取页面内容信息
            content_info = page.run_js("""
                function getContentInfo() {
                    const content = document.querySelector('.topic-post .cooked') || 
                                   document.querySelector('.post-content') ||
                                   document.querySelector('.post-body') ||
                                   document.body;
                    return {
                        length: content ? content.textContent.length : 500,
                        height: content ? content.scrollHeight : 2000,
                        wordCount: content ? content.textContent.split(/\\s+/).length : 100,
                        imageCount: content ? content.querySelectorAll('img').length : 0
                    };
                }
                return getContentInfo();
            """)
            
            if not content_info:
                content_info = {'length': 500, 'height': 2000, 'wordCount': 100, 'imageCount': 0}
            
            # 基于内容计算阅读时间（更长的停留）
            base_time = max(30, min(120, content_info['length'] / 15))
            read_time = base_time * random.uniform(0.9, 1.4)
            
            logger.info(f"📖 预计阅读时间: {read_time:.1f}秒 (内容长度:{content_info['length']}字符, 图片:{content_info['imageCount']}张)")
            
            # 分段滚动模拟
            scroll_segments = random.randint(6, 12)
            time_per_segment = read_time / scroll_segments
            
            for segment in range(scroll_segments):
                # 计算滚动位置
                scroll_ratio = (segment + 1) / scroll_segments
                scroll_pos = content_info['height'] * scroll_ratio
                
                # 平滑滚动
                page.run_js(f"""
                    window.scrollTo({{
                        top: {scroll_pos},
                        behavior: 'smooth'
                    }});
                """)
                
                # 模拟用户交互（阅读过程中的小动作）
                if random.random() < 0.4:
                    self.simulate_user_interaction(page)
                
                # 分段停留
                segment_wait = time_per_segment * random.uniform(0.8, 1.3)
                time.sleep(segment_wait)
            
            # 最终滚动到底部
            page.run_js("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
            time.sleep(random.uniform(3, 6))
            
            logger.info("✅ 深度浏览完成 - 确保活动被记录")
            
        except Exception as e:
            logger.error(f"❌ 增强浏览失败: {str(e)}")
            # 降级到基础浏览
            self.fallback_browse_post(page)

    def fallback_browse_post(self, page):
        """降级浏览行为"""
        try:
            scroll_count = random.randint(8, 15)
            logger.info(f"📜 执行基础浏览: {scroll_count} 次滚动")
            
            for i in range(scroll_count):
                # 更自然的滚动距离
                scroll_distance = random.randint(400, 900)
                page.run_js(f"window.scrollBy(0, {scroll_distance})")
                
                # 随机交互
                if random.random() < 0.3:
                    self.simulate_user_interaction(page)
                
                # 动态等待时间
                wait_time = random.uniform(2, 4)
                time.sleep(wait_time)
            
            logger.info("✅ 基础浏览完成")
        except Exception as e:
            logger.error(f"❌ 基础浏览失败: {str(e)}")

    def simulate_user_interaction(self, page):
        """模拟用户交互行为"""
        try:
            # 随机交互类型
            interaction_type = random.choice(['mousemove', 'click', 'scroll'])
            
            if interaction_type == 'mousemove':
                page.run_js("""
                    document.dispatchEvent(new MouseEvent('mousemove', { 
                        bubbles: true, 
                        clientX: Math.random() * window.innerWidth, 
                        clientY: Math.random() * window.innerHeight 
                    }));
                """)
            elif interaction_type == 'click':
                page.run_js("document.dispatchEvent(new MouseEvent('click', { bubbles: true }));")
            else:
                page.run_js("window.dispatchEvent(new Event('scroll'));")
                
            time.sleep(0.1)
                
        except Exception as e:
            logger.debug(f"模拟交互失败: {str(e)}")

    def click_like(self, page):
        """点赞当前帖子"""
        try:
            # 查找未点赞的按钮
            like_buttons = page.eles(".discourse-reactions-reaction-button")
            for button in like_buttons:
                try:
                    if button and button.states.is_enabled:
                        # 检查是否已点赞
                        button_class = button.get_attribute('class')
                        if button_class and 'has-like' not in button_class:
                            logger.info("👍 找到未点赞按钮，准备点赞")
                            button.click()
                            time.sleep(random.uniform(1, 3))
                            logger.success("✅ 点赞成功")
                            return True
                        else:
                            logger.info("ℹ️ 按钮已点赞过")
                            return False
                except:
                    continue
            logger.info("ℹ️ 未找到可点赞的按钮")
        except Exception as e:
            logger.error(f"❌ 点赞失败: {str(e)}")
        return False

    def browse_topics(self):
        """浏览主题 - 确保活动被记录"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        # 浏览前严格验证登录状态
        if not self.strict_verify_login_status():
            logger.error("❌ 浏览前登录状态验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始浏览 {self.site_name} 主题...")
            
            # 访问最新页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(5)
            
            # 处理Cloudflare验证
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(3)
            
            # 查找主题元素
            topic_elements = self.page.eles(".title")
            if not topic_elements:
                logger.error("❌ 未找到主题列表")
                return 0
            
            # 随机选择6-10个主题
            browse_count = min(random.randint(6, 10), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0
            
            logger.info(f"📊 发现 {len(topic_elements)} 个主题，计划浏览 {browse_count} 个")
            
            for i, idx in enumerate(selected_indices):
                try:
                    # 重新获取主题元素列表（避免stale element）
                    current_topics = self.page.eles(".title")
                    if not current_topics or idx >= len(current_topics):
                        logger.warning("⚠️ 主题元素已更新，重新获取...")
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(3)
                        current_topics = self.page.eles(".title")
                        if not current_topics:
                            logger.error("❌ 重新获取主题列表失败")
                            return success_count
                    
                    topic = current_topics[idx]
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                    
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}: {topic_url}")
                    
                    # 在新标签页打开主题
                    topic_tab = self.browser.new_tab()
                    try:
                        topic_tab.get(topic_url)
                        time.sleep(3)
                        
                        # 增强浏览行为
                        self.enhanced_browse_post(topic_tab, stay_time=random.uniform(30, 50))
                        
                        # 随机点赞（5%概率）
                        if random.random() < 0.05:
                            self.click_like(topic_tab)
                        
                        success_count += 1
                        
                    finally:
                        topic_tab.close()
                    
                    # 主题间等待 - 确保活动被记录
                    if i < browse_count - 1:
                        wait_time = random.uniform(10, 18)
                        logger.info(f"⏳ 主题间延迟 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"✅ 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            
            # 浏览后再次严格验证登录状态
            if not self.strict_verify_login_status():
                logger.warning("⚠️ 浏览后登录状态丢失，尝试重新登录...")
                if not self.login():
                    logger.error("❌ 重新登录失败")
                    return 0
            
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息...")
        info_tab = self.browser.new_tab()
        try:
            info_tab.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 处理Cloudflare验证
            CloudflareHandler.handle_cloudflare(info_tab)
            time.sleep(3)
            
            # 提取表格数据
            rows = info_tab.eles("tag:tr")
            info = []
            
            for row in rows:
                cells = row.eles("tag:td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    info.append([project, current, requirement])
            
            if info:
                print("\n" + "="*80)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*80)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="grid"))
                print("="*80 + "\n")
                
                # 统计达标情况
                passed = sum(1 for item in info if 'text-green' in str(item[1]) or '✅' in str(item[1]))
                total = len(info)
                logger.success(f"📈 统计完成: {passed}/{total} 项达标")
                
            else:
                logger.warning("⚠️ 未找到连接信息表格")
                
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")
        finally:
            info_tab.close()

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")
            
            # 1. 确保登录（严格验证）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 2. 浏览主题（确保活动被记录）
            browse_count = self.browse_topics()
            
            # 3. 打印连接信息
            self.print_connect_info()
            
            # 4. 保存最终状态
            self.save_all_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 浏览 {browse_count} 个主题")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
            
        finally:
            try:
                if self.browser:
                    self.browser.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (完整修复版)")
    logger.info("=" * 80)
    
    # 配置日志
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    
    success_sites = []
    failed_sites = []

    # 检查凭证配置
    for site_name, creds in SITE_CREDENTIALS.items():
        if not creds.get('username') or not creds.get('password'):
            logger.warning(f"⏭️ {site_name} 的用户名或密码未配置，将跳过该站点")

    # 站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    target_sites = SITES if site_selector == "all" else [s for s in SITES if s['name'] == site_selector]

    if not target_sites:
        logger.error(f"❌ 未找到匹配的站点: {site_selector}")
        sys.exit(1)

    logger.info(f"🎯 目标站点: {', '.join([s['name'] for s in target_sites])}")

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        if not credentials.get('username') or not credentials.get('password'):
            logger.warning(f"⏭️ 跳过 {site_name} - 凭证未配置")
            failed_sites.append(site_name)
            continue

        logger.info("-" * 80)
        logger.info(f"🔧 初始化 {site_name}")
        
        try:
            browser = LinuxDoBrowser(site_config, credentials)
            success = browser.run()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(15, 25)
            logger.info(f"⏳ 站点间延迟 {wait_time:.1f} 秒...")
            time.sleep(wait_time)

    # 最终总结
    logger.info("=" * 80)
    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功站点: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败站点: {', '.join(failed_sites) if failed_sites else '无'}")
    logger.info("=" * 80)

    if success_sites:
        logger.success(f"🎉 任务完成: {len(success_sites)}/{len(target_sites)} 个站点成功")
        sys.exit(0)
    else:
        logger.error("💥 任务失败: 所有站点均未成功")
        sys.exit(1)

if __name__ == "__main__":
    # 检查环境变量
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD', 'IDCFLARE_USERNAME', 'IDCFLARE_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 以下环境变量未设置: {', '.join(missing_vars)}")
        logger.warning("请确保在运行前设置所有必要的环境变量")
    
    main()
