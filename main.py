#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 智能优化版本
主要优化：智能缓存管理、Turnstile双重保护、浏览记录收集优化、单标签页操作
"""

import os
import random
import time
import sys
import json
import re
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions
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

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'private_topic_url': 'https://linux.do/t/topic/2362',
        'latest_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do',
        'user_url': 'https://linux.do/u',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'session_file': "session_data_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json"
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'private_topic_url': 'https://idcflare.com/t/topic/24',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'session_file': "session_data_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json"
    }
]

# 环境变量配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
USE_TURNSTILE_PATCH = os.environ.get("USE_TURNSTILE_PATCH", "true").strip().lower() in ["true", "1", "on"]

# ======================== 智能缓存管理器 ========================
class CacheManager:
    @staticmethod
    def get_cache_directory():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        cache_dir = CacheManager.get_cache_directory()
        return os.path.join(cache_dir, file_name)

    @staticmethod
    def load_cache(file_name):
        """加载缓存"""
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 成功加载缓存: {file_name}")
                return data
            except Exception as e:
                logger.warning(f"⚠️ 缓存加载失败 {file_name}: {str(e)}")
                try:
                    os.remove(file_path)
                    logger.info(f"🗑️ 已删除损坏的缓存文件: {file_name}")
                except:
                    pass
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存缓存"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"❌ 缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.save_cache(data, file_name)

    @staticmethod
    def smart_cache_management():
        """智能缓存管理：只在需要时清除缓存"""
        try:
            # 检查是否有过期的会话数据
            session_files = [
                "session_data_linux_do.json", 
                "session_data_idcflare.json"
            ]
            
            for session_file in session_files:
                file_path = CacheManager.get_cache_file_path(session_file)
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "r", encoding='utf-8') as f:
                            session_data = json.load(f)
                        
                        # 检查会话是否过期（超过24小时）
                        last_success = session_data.get('last_success')
                        if last_success:
                            last_time = datetime.fromisoformat(last_success)
                            time_diff = datetime.now() - last_time
                            if time_diff.total_seconds() > 24 * 3600:  # 24小时
                                logger.info(f"🗑️ 清除过期会话缓存: {session_file}")
                                os.remove(file_path)
                                
                    except Exception as e:
                        logger.warning(f"⚠️ 检查会话缓存失败 {session_file}: {str(e)}")
                        # 删除损坏的缓存文件
                        try:
                            os.remove(file_path)
                        except:
                            pass
            
            logger.info("✅ 智能缓存管理完成")
            
        except Exception as e:
            logger.error(f"❌ 智能缓存管理失败: {str(e)}")

    @staticmethod
    def clear_site_cache_on_failure(site_name):
        """登录失败时清除该站点的缓存"""
        try:
            cache_types = ['cf_cookies', 'session_data', 'browser_state']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{site_name}.json"
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 登录失败，已清除缓存: {file_name}")
            
            logger.info(f"✅ {site_name} 站点缓存已清除")
            
        except Exception as e:
            logger.error(f"❌ 清除站点缓存失败: {str(e)}")

# ======================== Turnstile双重保护 ========================
class TurnstileDualProtection:
    """Turnstile双重保护：扩展 + JavaScript注入"""
    
    @staticmethod
    def get_extension_path():
        """获取TurnstilePatch扩展路径"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        extension_path = os.path.join(base_dir, "turnstilePatch")
        
        if os.path.exists(extension_path):
            logger.info(f"✅ 找到TurnstilePatch扩展: {extension_path}")
            return extension_path
        else:
            logger.warning("⚠️ 未找到TurnstilePatch扩展目录")
            return None

    @staticmethod
    def inject_turnstile_simulation(page):
        """注入Turnstile模拟脚本 - 与扩展互补"""
        try:
            turnstile_script = """
            // Turnstile 模拟脚本 - 与扩展互补
            if (typeof window.turnstile === 'undefined') {
                window.turnstile = {
                    ready: (callback) => {
                        console.log('Turnstile ready simulated');
                        setTimeout(callback, 50);
                    },
                    render: (element, options) => {
                        console.log('Turnstile render simulated:', options);
                        return 'simulated-widget-' + Date.now();
                    },
                    execute: (element, options) => {
                        console.log('Turnstile execute simulated');
                        return Promise.resolve('simulated-token-' + Date.now());
                    },
                    getResponse: () => {
                        const response = 'simulated-cf-response-' + Date.now();
                        console.log('Turnstile getResponse returning:', response);
                        return response;
                    },
                    reset: () => console.log('Turnstile reset simulated'),
                    remove: () => console.log('Turnstile remove simulated')
                };
                console.log('✅ Turnstile 模拟脚本已加载');
            }
            
            // 额外的Cloudflare绕过
            Object.defineProperty(navigator, 'webdriver', { 
                get: () => undefined 
            });
            
            // 确保统计请求被发送
            const originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(...args) {
                const url = this._url || '';
                if (url.includes('analytics') || url.includes('track') || url.includes('count')) {
                    console.log('📊 确保统计请求发送:', url);
                }
                return originalSend.apply(this, args);
            };
            
            // 页面浏览事件模拟
            window.dispatchEvent(new Event('pageview'));
            """
            
            page.run_js(turnstile_script)
            logger.info("✅ Turnstile模拟脚本已注入（双重保护）")
            
        except Exception as e:
            logger.warning(f"⚠️ 注入Turnstile模拟脚本失败: {str(e)}")

    @staticmethod
    def setup_dual_protection(co):
        """设置双重保护：扩展 + 脚本注入"""
        # 1. 首先配置扩展
        extension_path = TurnstileDualProtection.get_extension_path()
        if extension_path and USE_TURNSTILE_PATCH:
            try:
                co.set_argument(f"--disable-extensions-except={extension_path}")
                co.set_argument(f"--load-extension={extension_path}")
                logger.info("✅ TurnstilePatch扩展已加载")
            except Exception as e:
                logger.error(f"❌ 加载TurnstilePatch扩展失败: {str(e)}")

# ======================== Cloudflare优化处理器 ========================
class CloudflareHandler:
    @staticmethod
    def handle_cloudflare_fast(page, max_attempts=2, timeout=30):
        """快速Cloudflare处理 - 配合TurnstilePatch"""
        start_time = time.time()
        logger.info("🛡️ 快速Cloudflare验证处理")
        
        # 快速检查是否已经通过
        try:
            page_title = page.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                logger.success("✅ 页面已正常加载")
                return True
        except:
            pass
        
        for attempt in range(max_attempts):
            try:
                page_title = page.title
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    logger.success("✅ Cloudflare验证通过")
                    return True
                
                wait_time = random.uniform(2, 4)  # 更短的等待
                logger.info(f"⏳ 等待Cloudflare验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时，继续执行")
                    break
                    
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(2)
        
        return True

# ======================== 主浏览器类 - 智能优化版本 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.page = None
        self.cache_saved = False
        self.initialize_browser_with_dual_protection()

    def initialize_browser_with_dual_protection(self):
        """初始化浏览器并加载Turnstile双重保护"""
        try:
            co = ChromiumOptions()
            if HEADLESS:
                co.headless(True)
            else:
                co.headless(False)
                
            co.incognito(True)
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-features=VizDisplayCompositor")
            co.set_argument("--disable-background-timer-throttling")
            co.set_argument("--disable-renderer-backgrounding")
            
            # 加载Turnstile双重保护
            TurnstileDualProtection.setup_dual_protection(co)
            
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            # 注入Turnstile模拟脚本
            TurnstileDualProtection.inject_turnstile_simulation(self.page)
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成 (Turnstile双重保护: {'✅' if USE_TURNSTILE_PATCH else '❌'})")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def save_smart_caches(self):
        """智能缓存保存"""
        if self.cache_saved:
            return
            
        try:
            # 保存cookies
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info(f"✅ 已保存 {len(cookies)} 个Cookies")
            
            # 保存会话数据
            session_data = {
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '7.0',
                'site_name': self.site_name,
                'username_hash': hash(self.username) if self.username else 0,
                'total_runs': self.session_data.get('total_runs', 0) + 1,
                'last_url': self.page.url,
                'user_agent': self.page.run_js("return navigator.userAgent;"),
                'turnstile_patch_enabled': USE_TURNSTILE_PATCH
            }
            CacheManager.save_site_cache(session_data, self.site_name, 'session_data')
            
            # 保存浏览器状态
            browser_state = {
                'timestamp': datetime.now().isoformat(),
                'url': self.page.url,
                'title': self.page.title,
                'cookies_count': len(cookies) if cookies else 0,
                'window_size': self.page.run_js("return {width: window.innerWidth, height: window.innerHeight};")
            }
            CacheManager.save_site_cache(browser_state, self.site_name, 'browser_state')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 智能缓存保存完成")
            
        except Exception as e:
            logger.error(f"❌ 保存缓存失败: {str(e)}")

    def try_cache_login_enhanced(self):
        """增强的缓存登录尝试"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录，跳过缓存")
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            logger.warning("⚠️ 无有效缓存Cookies")
            return False
        
        try:
            logger.info("🎯 尝试增强缓存登录...")
            
            self.page.get(self.site_config['base_url'])
            time.sleep(2)
            
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            self.page.refresh()
            time.sleep(2)
            
            CloudflareHandler.handle_cloudflare_fast(self.page)
            
            if self.quick_verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def quick_verify_login_status(self):
        """快速登录状态验证"""
        try:
            private_url = self.site_config['private_topic_url']
            self.page.get(private_url)
            time.sleep(2)
            
            CloudflareHandler.handle_cloudflare_fast(self.page)
            time.sleep(1)
            
            page_content = self.page.html
            page_title = self.page.title
            
            if 'login' in self.page.url or 'signin' in self.page.url:
                return False
            
            error_indicators = ["Page Not Found", "糟糕！该页面不存在或者是一个不公开页面。"]
            for indicator in error_indicators:
                if indicator.lower() in page_content.lower():
                    return False
            
            if self.username.lower() in page_content.lower():
                logger.success("✅ 快速验证通过")
                return True
            return False
            
        except Exception as e:
            logger.debug(f"快速验证异常: {str(e)}")
            return False

    def login_optimized(self):
        """优化的登录流程"""
        self.page.set.cookies([])
        logger.info("🔐 执行优化登录流程...")
        
        self.page.get(self.site_config['login_url'])
        time.sleep(2)
        
        CloudflareHandler.handle_cloudflare_fast(self.page)
        time.sleep(1)
        
        try:
            username_field = self.page.ele("#login-account-name")
            password_field = self.page.ele("#login-account-password")
            login_button = self.page.ele("#login-button")
            
            if not all([username_field, password_field, login_button]):
                logger.error("❌ 登录表单元素未找到")
                return False
            
            logger.info("⌨️ 优化输入登录信息...")
            username_field.input(self.username)
            time.sleep(0.2)
            
            password_field.input(self.password)
            time.sleep(0.2)
            
            logger.info("🔑 点击登录按钮...")
            login_button.click()
            time.sleep(5)
            
            CloudflareHandler.handle_cloudflare_fast(self.page)
            time.sleep(2)
            
            if self.quick_verify_login_status():
                logger.success("✅ 优化登录成功")
                self.save_smart_caches()
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 优化登录失败: {str(e)}")
            return False

    def ensure_logged_in_smart(self):
        """智能登录确保"""
        # 首先尝试缓存登录
        if not FORCE_LOGIN_EVERY_TIME:
            cache_success = self.try_cache_login_enhanced()
            if cache_success:
                return True
            else:
                # 缓存登录失败，清除该站点缓存
                CacheManager.clear_site_cache_on_failure(self.site_name)
    
        # 执行新登录
        login_success = self.login_optimized()
        if not login_success:
            # 新登录也失败，清除缓存
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    def browse_for_tracking_optimized(self):
        """为浏览记录收集优化的浏览策略"""
        try:
            logger.info("🎯 开始优化浏览记录收集...")
            
            # 访问最新页面开始
            self.page.get(self.site_config['latest_url'])
            time.sleep(2)
            
            CloudflareHandler.handle_cloudflare_fast(self.page)
            time.sleep(1)
            
            # 查找主题
            topic_elements = self.find_topic_elements_fast()
            if not topic_elements:
                logger.error("❌ 未找到主题列表")
                return 0
            
            # 选择更多主题进行浏览记录收集
            browse_count = min(random.randint(8, 12), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0
            
            logger.info(f"📊 为浏览记录收集选择 {browse_count} 个主题")
            
            for i, idx in enumerate(selected_indices):
                try:
                    if idx >= len(topic_elements):
                        continue
                    
                    topic = topic_elements[idx]
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                    
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 收集浏览记录 {i+1}/{browse_count}")
                    
                    # 在当前标签页打开（确保浏览记录被关联）
                    self.page.get(topic_url)
                    time.sleep(1)
                    
                    CloudflareHandler.handle_cloudflare_fast(self.page)
                    time.sleep(1)
                    
                    # 深度浏览以确保记录被收集
                    self.deep_browse_for_tracking()
                    
                    success_count += 1
                    
                    # 每浏览3个主题返回一次列表页，模拟真实用户行为
                    if (i + 1) % 3 == 0 and i < browse_count - 1:
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(1)
                        CloudflareHandler.handle_cloudflare_fast(self.page)
                        topic_elements = self.find_topic_elements_fast()
                        
                    # 随机间隔，模拟真实用户
                    if i < browse_count - 1:
                        wait_time = random.uniform(3, 8)
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览记录收集失败: {str(e)}")
                    continue
            
            logger.success(f"✅ 浏览记录收集完成: {success_count}/{browse_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览记录收集失败: {str(e)}")
            return 0

    def deep_browse_for_tracking(self):
        """深度浏览以确保浏览记录被收集"""
        try:
            # 模拟阅读行为
            scroll_actions = random.randint(5, 8)
            logger.debug(f"📖 深度浏览: {scroll_actions} 次滚动")
            
            for i in range(scroll_actions):
                # 随机滚动距离
                scroll_distance = random.randint(300, 600)
                self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
                
                # 随机停留时间，模拟阅读
                stay_time = random.uniform(1.5, 3.5)
                time.sleep(stay_time)
                
                # 偶尔触发交互事件
                if random.random() < 0.2:
                    self.trigger_interaction_events()
            
            # 确保页面完全加载
            self.page.run_js("""
                // 触发可能延迟加载的内容
                window.dispatchEvent(new Event('scroll'));
                window.dispatchEvent(new Event('resize'));
            """)
            
            time.sleep(1)
            
        except Exception as e:
            logger.debug(f"深度浏览异常: {str(e)}")

    def trigger_interaction_events(self):
        """触发交互事件以增强浏览记录"""
        try:
            # 随机鼠标移动
            self.page.run_js("""
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
            """)
            
            # 偶尔点击
            if random.random() < 0.1:
                self.page.run_js("""
                    document.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                """)
                
        except Exception as e:
            logger.debug(f"触发交互事件异常: {str(e)}")

    def find_topic_elements_fast(self):
        """快速主题元素查找"""
        selectors = ["a.title", ".topic-list-item a", "[data-topic-id] a"]
        
        for selector in selectors:
            try:
                elements = self.page.eles(selector)
                if elements:
                    valid_elements = []
                    for elem in elements:
                        href = elem.attr('href')
                        if href and '/t/' in href and not href.endswith('/latest'):
                            valid_elements.append(elem)
                    
                    if valid_elements:
                        return valid_elements
            except Exception:
                continue
        
        return []

    def print_connect_info_single_tab(self):
        """单标签页获取连接信息"""
        logger.info("🔗 单标签页获取连接信息...")
        try:
            # 保存当前URL
            current_url = self.page.url
            
            # 在当前标签页打开连接页面
            self.page.get(self.site_config['connect_url'])
            time.sleep(2)
            
            CloudflareHandler.handle_cloudflare_fast(self.page)
            time.sleep(1)
            
            # 获取连接信息
            connect_info = self.extract_connect_info()
            if connect_info:
                self.display_connect_info(connect_info)
            else:
                logger.warning("⚠️ 未获取到连接信息")
            
            # 返回原页面
            self.page.get(current_url)
            time.sleep(1)
            
            logger.info("✅ 连接信息获取完成")
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def extract_connect_info(self):
        """提取连接信息"""
        try:
            # 尝试多种表格选择器
            table_selectors = ["tag:table", ".connect-table", ".requirements-table"]
            
            for selector in table_selectors:
                table = self.page.ele(selector)
                if table:
                    return self.parse_connect_table(table)
            
            # 如果没有表格，尝试从页面内容提取
            return self.extract_connect_info_from_content()
            
        except Exception as e:
            logger.error(f"❌ 提取连接信息失败: {str(e)}")
            return None

    def parse_connect_table(self, table):
        """解析连接信息表格"""
        try:
            rows = table.eles("tag:tr")
            info = []
            
            for row in rows:
                cells = row.eles("tag:td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    if project and current and requirement:
                        info.append([project, current, requirement])
            
            return info if info else None
            
        except Exception as e:
            logger.error(f"❌ 解析表格失败: {str(e)}")
            return None

    def extract_connect_info_from_content(self):
        """从页面内容提取连接信息"""
        try:
            page_content = self.page.html
            info = []
            
            # 简单的关键词匹配提取
            patterns = {
                '访问次数': r'访问次数.*?(\d+%?\s*\(\d+\s*/\s*\d+\s*天数\)|\d+%?)',
                '回复的话题': r'回复的话题.*?([≥\d]+)',
                '浏览的话题': r'浏览的话题.*?(\d+)',
                '已读帖子': r'已读帖子.*?(\d+)',
                '点赞': r'点赞.*?(\d+)',
                '获赞': r'获赞.*?(\d+)'
            }
            
            for name, pattern in patterns.items():
                match = re.search(pattern, page_content)
                if match:
                    info.append([name, match.group(1), "未知"])
            
            return info if info else None
            
        except Exception as e:
            logger.error(f"❌ 从内容提取信息失败: {str(e)}")
            return None

    def display_connect_info(self, info):
        """显示连接信息"""
        if not info:
            return
            
        print("\n" + "="*60)
        print(f"📊 {self.site_name.upper()} 连接信息")
        print("="*60)
        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        print("="*60 + "\n")
        
        # 统计达标情况
        passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
        total = len(info)
        logger.success(f"📈 统计完成: {passed}/{total} 项达标")

    def run_optimized_for_tracking(self):
        """为浏览记录收集优化的完整流程"""
        try:
            logger.info(f"🚀 开始优化处理站点: {self.site_name} (浏览记录收集)")
            
            # 智能缓存管理（不清除有效缓存）
            CacheManager.smart_cache_management()
            
            # 确保登录状态
            if not self.ensure_logged_in_smart():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 主要目的：收集浏览记录
            browse_count = self.browse_for_tracking_optimized()
            
            # 次要目的：获取连接信息（单标签页）
            self.print_connect_info_single_tab()
            
            # 保存智能缓存（登录成功时才保存）
            self.save_smart_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 收集 {browse_count} 个浏览记录")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
            
        finally:
            try:
                if self.page:
                    self.page.quit()
            except:
                pass

# ======================== 主函数 - 智能优化版本 ========================
def main():
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (智能优化版)")
    logger.info("=" * 80)
    
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    
    success_sites = []
    failed_sites = []

    # 检查凭证配置
    for site_name, creds in SITE_CREDENTIALS.items():
        if not creds.get('username') or not creds.get('password'):
            logger.warning(f"⏭️ {site_name} 的用户名或密码未配置")

    # 站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    target_sites = SITES if site_selector == "all" else [s for s in SITES if s['name'] == site_selector]

    if not target_sites:
        logger.error(f"❌ 未找到匹配的站点: {site_selector}")
        sys.exit(1)

    logger.info(f"🎯 目标站点: {', '.join([s['name'] for s in target_sites])}")
    logger.info(f"🔧 Turnstile双重保护: {'✅ 启用' if USE_TURNSTILE_PATCH else '❌ 禁用'}")
    logger.info(f"💾 智能缓存管理: ✅")
    logger.info(f"🎯 主要目的: 浏览记录收集")

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
            success = browser.run_optimized_for_tracking()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(5, 10)
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
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD', 'IDCFLARE_USERNAME', 'IDCFLARE_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 以下环境变量未设置: {', '.join(missing_vars)}")
    
    main()
