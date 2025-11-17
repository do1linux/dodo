#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 修复主题浏览版本
主题浏览: 单标签页,使用了@id=list-area和.:title来获取主题列表
连接信息: 新标签页,使用 tabulate 库美化表格显示
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
                except:
                    pass
        return None

    @staticmethod
    def save_cache(data, file_name):
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
            session_files = ["session_data_linux_do.json", "session_data_idcflare.json"]
            
            for session_file in session_files:
                file_path = CacheManager.get_cache_file_path(session_file)
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "r", encoding='utf-8') as f:
                            session_data = json.load(f)
                        
                        last_success = session_data.get('last_success')
                        if last_success:
                            last_time = datetime.fromisoformat(last_success)
                            time_diff = datetime.now() - last_time
                            if time_diff.total_seconds() > 24 * 3600:
                                logger.info(f"🗑️ 清除过期会话缓存: {session_file}")
                                os.remove(file_path)
                                
                    except Exception as e:
                        logger.warning(f"⚠️ 检查会话缓存失败 {session_file}: {str(e)}")
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
    @staticmethod
    def get_extension_path():
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
        try:
            turnstile_script = """
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
            
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            const originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(...args) {
                const url = this._url || '';
                if (url.includes('analytics') || url.includes('track') || url.includes('count')) {
                    console.log('📊 确保统计请求发送:', url);
                }
                return originalSend.apply(this, args);
            };
            
            window.dispatchEvent(new Event('pageview'));
            """
            
            page.run_js(turnstile_script)
            logger.info("✅ Turnstile模拟脚本已注入（双重保护）")
            
        except Exception as e:
            logger.warning(f"⚠️ 注入Turnstile模拟脚本失败: {str(e)}")

    @staticmethod
    def setup_dual_protection(co):
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
        start_time = time.time()
        logger.info("🛡️ 快速Cloudflare验证处理")
        
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
                
                wait_time = random.uniform(2, 4)
                logger.info(f"⏳ 等待Cloudflare验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时，继续执行")
                    break
                    
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(2)
        
        return True

# ======================== 主浏览器类 - 修复主题浏览版本 ========================
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
            
            TurnstileDualProtection.setup_dual_protection(co)
            
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            
            self.page = ChromiumPage(addr_or_opts=co)
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            TurnstileDualProtection.inject_turnstile_simulation(self.page)
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成 (Turnstile双重保护: {'✅' if USE_TURNSTILE_PATCH else '❌'})")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def save_smart_caches(self):
        if self.cache_saved:
            return
            
        try:
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info(f"✅ 已保存 {len(cookies)} 个Cookies")
            
            session_data = {
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '8.0',
                'site_name': self.site_name,
                'username_hash': hash(self.username) if self.username else 0,
                'total_runs': self.session_data.get('total_runs', 0) + 1,
                'last_url': self.page.url,
                'turnstile_patch_enabled': USE_TURNSTILE_PATCH
            }
            CacheManager.save_site_cache(session_data, self.site_name, 'session_data')
            
            browser_state = {
                'timestamp': datetime.now().isoformat(),
                'url': self.page.url,
                'title': self.page.title,
                'cookies_count': len(cookies) if cookies else 0
            }
            CacheManager.save_site_cache(browser_state, self.site_name, 'browser_state')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 智能缓存保存完成")
            
        except Exception as e:
            logger.error(f"❌ 保存缓存失败: {str(e)}")

    def try_cache_login_enhanced(self):
        if FORCE_LOGIN_EVERY_TIME:
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
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
            
            if self.strict_verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def strict_verify_login_status(self, max_retries=2):
        """严格的登录状态验证 - 双重验证机制"""
        logger.info("🔍 执行严格登录状态验证...")
        
        for retry in range(max_retries):
            try:
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题: {private_url}")
                self.page.get(private_url)
                time.sleep(3)
                
                CloudflareHandler.handle_cloudflare_fast(self.page)
                time.sleep(2)
                
                page_content = self.page.html
                page_title = self.page.title
                current_url = self.page.url
                
                logger.info(f"📄 私有主题页面标题: {page_title}")
                logger.info(f"🌐 当前URL: {current_url}")
                
                # 检查是否在登录页面
                if 'login' in current_url or 'signin' in current_url:
                    logger.warning(f"❌ 被重定向到登录页面 (尝试 {retry + 1}/{max_retries})")
                    continue
                
                # 检查是否有错误提示
                error_indicators = ["Page Not Found", "糟糕！该页面不存在或者是一个不公开页面。"]
                for indicator in error_indicators:
                    if indicator.lower() in page_content.lower():
                        logger.error(f"❌ 私有主题访问失败: {indicator}")
                        return False
                
                logger.success("✅ 私有主题访问成功 - 无错误提示")
                
                # 验证用户名存在
                if self.username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面中找到用户名: {self.username}")
                    logger.success("🎉 双重验证通过 - 登录状态有效")
                    return True
                else:
                    logger.warning(f"❌ 在页面中未找到用户名: {self.username}")
                    continue
                
            except Exception as e:
                logger.error(f"❌ 登录状态验证异常: {str(e)}")
        
        return False

    def login_optimized(self):
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
            
            if self.strict_verify_login_status():
                logger.success("✅ 优化登录成功")
                self.save_smart_caches()
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 优化登录失败: {str(e)}")
            return False

    def ensure_logged_in_smart(self):
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login_enhanced():
            return True
        
        login_success = self.login_optimized()
        if not login_success:
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    def find_topic_elements_comprehensive(self):
        """全面主题元素查找 - 修复版本"""
        topic_elements = []
        
        # 更全面的选择器列表
        selectors = [
                "@id=list-area",
                ".topic-list",
                "tr.topic-list-item",
                "[data-topic-id]"
            ]
        
        for selector in selectors:
            try:
                elements = self.page.eles(selector)
                if elements:
                    valid_elements = []
                    for elem in elements:
                        href = elem.attr('href')
                        if href and '/t/' in href and not any(x in href for x in ['/latest', '/c/', '/tag/', '/u/']):
                            valid_elements.append(elem)
                    
                    if valid_elements:
                        topic_elements = valid_elements
                        logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(topic_elements)} 个主题")
                        return topic_elements
            except Exception as e:
                logger.debug(f"选择器 {selector} 查找失败: {str(e)}")
                continue
        
        # 如果选择器都失败，尝试调试页面结构
        self.debug_page_structure()
        
        return topic_elements

    def debug_page_structure(self):
        """调试页面结构"""
        try:
            logger.info("🐛 开始调试页面结构...")
            
            # 检查常见的选择器
            debug_selectors = [
                '.title', '#list-area', '.topic-list', '.topic-list-item',
                '.main-link', '.raw-topic-link', '[data-topic-id]'
            ]
            
            for selector in debug_selectors:
                elements = self.page.eles(selector)
                if elements:
                    logger.info(f"🔍 找到 {len(elements)} 个 '{selector}' 元素")
            
            # 检查链接
            all_links = self.page.eles('tag:a')
            topic_links = [link for link in all_links if link.attr('href') and '/t/' in link.attr('href')]
            logger.info(f"🔗 找到 {len(topic_links)} 个包含 '/t/' 的链接")
            
            # 打印一些链接示例
            for i, link in enumerate(topic_links[:5]):
                href = link.attr('href')
                text = link.text
                logger.info(f"📎 链接示例 {i+1}: {text} -> {href}")
                
        except Exception as e:
            logger.error(f"❌ 调试页面结构失败: {str(e)}")

    def browse_topics_single_tab(self):
        """单标签页主题浏览 - 修复版本"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        if not self.strict_verify_login_status():
            logger.error("❌ 浏览前登录状态验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始单标签页浏览 {self.site_name} 主题...")
            
            # 访问最新页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare_fast(self.page)
            time.sleep(2)
            
            # 查找主题元素
            topic_elements = self.find_topic_elements_comprehensive()
            if not topic_elements:
                logger.error("❌ 未找到主题列表")
                return 0
            
            # 选择主题浏览
            browse_count = min(random.randint(6, 10), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0
            
            logger.info(f"📊 发现 {len(topic_elements)} 个主题，计划浏览 {browse_count} 个")
            
            for i, idx in enumerate(selected_indices):
                try:
                    if idx >= len(topic_elements):
                        continue
                    
                    topic = topic_elements[idx]
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                    
                    # 确保URL完整
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    # 在当前标签页打开主题（单标签页）
                    self.page.get(topic_url)
                    time.sleep(2)
                    
                    CloudflareHandler.handle_cloudflare_fast(self.page)
                    time.sleep(1)
                    
                    # 深度浏览以确保浏览记录被收集
                    self.deep_browse_for_tracking()
                    
                    success_count += 1
                    
                    # 每浏览几个主题返回一次列表页
                    if (i + 1) % 3 == 0 and i < browse_count - 1:
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(2)
                        CloudflareHandler.handle_cloudflare_fast(self.page)
                        topic_elements = self.find_topic_elements_comprehensive()
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(5, 10)
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"✅ 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def deep_browse_for_tracking(self):
        """深度浏览以确保浏览记录被收集"""
        try:
            scroll_count = random.randint(6, 10)
            logger.debug(f"📖 深度浏览: {scroll_count} 次滚动")
            
            for i in range(scroll_count):
                scroll_distance = random.randint(300, 600)
                self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
                
                stay_time = random.uniform(1.5, 3.5)
                time.sleep(stay_time)
                
                # 触发交互事件
                if random.random() < 0.2:
                    self.trigger_interaction_events()
            
            # 触发页面事件
            self.page.run_js("""
                window.dispatchEvent(new Event('scroll'));
                window.dispatchEvent(new Event('resize'));
            """)
            
            time.sleep(1)
            
        except Exception as e:
            logger.debug(f"深度浏览异常: {str(e)}")

    def trigger_interaction_events(self):
        """触发交互事件"""
        try:
            self.page.run_js("""
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
            """)
            
        except Exception as e:
            logger.debug(f"触发交互事件异常: {str(e)}")

    def print_connect_info(self):
        """打印连接信息 - 基于参考代码的实现"""
        logger.info("🔗 获取连接信息...")
        try:
            # 在新标签页打开连接页面
            connect_tab = self.page.new_tab()
            connect_tab.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 处理Cloudflare验证
            CloudflareHandler.handle_cloudflare(connect_tab)
            time.sleep(3)
            
            # 查找表格
            table = connect_tab.ele("tag:table")
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                connect_tab.close()
                return
            
            # 提取表格数据
            rows = table.eles("tag:tr")
            info = []
            
            for row in rows:
                cells = row.eles("tag:td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    info.append([project, current, requirement])
            
            if info:
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("="*60 + "\n")
                
                # 统计达标情况
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥']))
                total = len(info)
                logger.success(f"📈 统计完成: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 关闭连接页面标签
            connect_tab.close()
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def run_fixed_version(self):
        """修复版本的完整流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")
            
            # 智能缓存管理
            CacheManager.smart_cache_management()
            
            # 确保登录状态（双重验证）
            if not self.ensure_logged_in_smart():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 单标签页浏览主题（确保浏览记录收集）
            browse_count = self.browse_topics_single_tab()
            
            # 新标签页获取连接信息
            self.print_connect_info_new_tab()
            
            # 保存智能缓存
            self.save_smart_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 浏览 {browse_count} 个主题")
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

# ======================== 主函数 ========================
def main():
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (修复主题浏览版)")
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
    logger.info("💾 智能缓存管理: ✅")
    logger.info("🎯 主要目的: 浏览记录收集")
    logger.info("📑 主题浏览: 单标签页")
    logger.info("🔗 连接信息: 新标签页")

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
            success = browser.run_fixed_version()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(8, 15)
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


