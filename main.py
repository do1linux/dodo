#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Linux.do 自动化浏览工具 - 修复版 v4.4
====================================
修复内容：
1. ✅ 修复 CacheManager 递归调用错误
2. ✅ 集成浏览记录收集功能
3. ✅ 增强阅读行为模拟
4. ✅ 保持所有优化功能
"""

import os
import random
import time
import sys
import json
import re
import base64
import requests
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions
from tabulate import tabulate

# 日志配置 - 只保留INFO及以上级别
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

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
        'private_topic_url': 'https://linux.do/t/topic/870130',
        'unread_url': 'https://linux.do/unread',
        'connect_url': 'https://connect.linux.do',
        'user_url': 'https://linux.do/u',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'session_file': "session_data_linux_do.json"
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'private_topic_url': 'https://idcflare.com/t/topic/24',
        'unread_url': 'https://idcflare.com/unread',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'session_file': "session_data_idcflare.json"
    }
]

# 环境配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
BEHAVIOR_INJECTION_ENABLED = os.environ.get("BEHAVIOR_INJECTION_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
EXTERNAL_LINKS_NEW_TAB = os.environ.get("EXTERNAL_LINKS_NEW_TAB", "true").strip().lower() not in ["false", "0", "off"]
OCR_API_KEY = os.getenv("OCR_API_KEY")
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# ======================== UserScript注入系统 ========================
class UserScriptInjector:
    """融合 Discourse UserScript 核心逻辑的外部链接处理器"""
    
    def __init__(self, page, site_config):
        self.page = page
        self.site_config = site_config
        self.injected = False
        
    def inject_external_link_handler(self):
        """注入处理外部链接的UserScript"""
        try:
            try:
                self.page.wait.doc_loaded()
            except:
                pass
            
            js_code = """
            (function() {
                'use strict';
                if (window.discourseUserScriptInjected) return;
                window.discourseUserScriptInjected = true;
                
                function isExternalLink(url) {
                    if (!url || url.startsWith('#')) return false;
                    try {
                        const linkHost = new URL(url, window.location.origin).host;
                        return linkHost !== window.location.host;
                    } catch (e) {
                        return false;
                    }
                }
                
                document.addEventListener('click', function(e) {
                    if (!e.isTrusted) return;
                    const link = e.target.closest('a');
                    if (!link) return;
                    
                    const href = link.getAttribute('href');
                    if (!href) return;
                    
                    const baseUrl = '%s';
                    const fullUrl = href.startsWith('http') ? href : baseUrl + href;
                    
                    if (isExternalLink(fullUrl)) {
                        e.preventDefault();
                        e.stopPropagation();
                        setTimeout(() => {
                            window.open(fullUrl, '_blank', 'noopener,noreferrer');
                        }, 50 + Math.random() * 150);
                        
                        link.style.opacity = '0.75';
                        setTimeout(() => {
                            link.style.opacity = '';
                        }, 120);
                        return false;
                    }
                }, true);
            })();
            """ % self.site_config['base_url']
            
            self.page.run_js(js_code)
            self.injected = True
            return True
            
        except Exception as e:
            try:
                self.page.run_js(js_code)
                self.injected = True
                return True
            except:
                return False
    
    def inject_mouse_behavior(self):
        """补充低频率鼠标移动"""
        try:
            js_code = """
            (function() {
                if (window.mouseBehaviorInjected) return;
                window.mouseBehaviorInjected = true;
                
                setInterval(() => {
                    if (Math.random() < 0.2) return;
                    const x = Math.random() * window.innerWidth;
                    const y = Math.random() * window.innerHeight;
                    
                    document.dispatchEvent(new MouseEvent('mousemove', {
                        view: window,
                        bubbles: true,
                        cancelable: true,
                        clientX: x,
                        clientY: y
                    }));
                }, 3000 + Math.random() * 7000);
            })();
            """
            
            self.page.run_js(js_code)
            return True
            
        except:
            return False

# ======================== 缓存管理器 ========================
class CacheManager:
    @staticmethod
    def get_cache_directory():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        # 修复：直接返回文件路径，而不是递归调用
        return os.path.join(CacheManager.get_cache_directory(), file_name)

    @staticmethod
    def load_cache(file_name):
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                return data
            except Exception as e:
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
            return True
        except Exception as e:
            logger.error(f"❌ 缓存保存失败: {str(e)}")
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
    def clear_site_cache_on_failure(site_name):
        """登录失败时清除该站点的缓存"""
        try:
            cache_types = ['cf_cookies', 'session_data']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{site_name}.json"
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            logger.info(f"✅ {site_name} 缓存已清除")
            
        except Exception as e:
            logger.error(f"❌ 清除缓存失败: {str(e)}")

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.page = None
        self.browser = None
        self.cache_saved = False
        self.session_start_time = time.time()
        self.request_count = 0
        self.browsing_active = True
        self.user_script = None
        self.initialize_browser()

    def initialize_browser(self):
        """浏览器初始化 - 专注反检测，不加载外部扩展"""
        try:
            co = ChromiumOptions()
            
            # GitHub Actions 环境特殊配置
            if GITHUB_ACTIONS:
                co.headless(True)
                co.set_argument("--no-sandbox")
                co.set_argument("--disable-dev-shm-usage")
                co.set_argument("--disable-gpu")
                co.set_argument("--disable-software-rasterizer")
            else:
                co.headless(HEADLESS)
                
            co.incognito(True)
            
            # 基础反检测配置
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-web-security")
            co.set_argument("--disable-features=TranslateUI")
            co.set_argument("--disable-background-networking")
            co.set_argument("--disable-sync")
            co.set_argument("--disable-translate")
            
            # 用户代理和窗口设置
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            co.set_argument("--window-size=1920,1080")
        
            # 保存browser实例
            self.browser = ChromiumPage(addr_or_opts=co)
            self.page = self.browser.new_tab()
            
            # 初始化 UserScript 注入器
            self.user_script = UserScriptInjector(self.page, self.site_config)
            
            # 执行指纹优化
            self.enhance_browser_fingerprint()
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            logger.info("✅ 浏览器初始化成功")
        
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def enhance_browser_fingerprint(self):
        """浏览器指纹优化 - 新增Canvas噪声注入"""
        try:
            # 使用更真实的硬件参数
            resolutions = [(1920,1080), (1366,768), (1536,864), (1440,900)]
            cores = [4, 6, 8]
            mem = [8, 16]
            width, height = random.choice(resolutions)
            core_count = random.choice(cores)
            mem_size = random.choice(mem)
        
            js_code = f"""
            // 增强指纹隐藏
            Object.defineProperties(navigator, {{
                webdriver: {{ get: () => undefined }},
                platform: {{ get: () => 'Win32' }},
                hardwareConcurrency: {{ get: () => {core_count} }},
                deviceMemory: {{ get: () => {mem_size} }},
                maxTouchPoints: {{ get: () => 0 }},
            
                plugins: {{
                    get: () => [
                        {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }},
                        {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' }},
                        {{ name: 'Native Client', filename: 'internal-nacl-plugin' }}
                    ]
                }}
            }});

            // 屏幕属性
            Object.defineProperty(screen, 'width', {{get: () => {width}}});
            Object.defineProperty(screen, 'height', {{get: () => {height}}});

            // Canvas指纹噪声注入
            const originalGetContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(...args) {{
                const context = originalGetContext.apply(this, args);
                if (context && args[0] === '2d') {{
                    const originalFillText = context.fillText;
                    context.fillText = function(...textArgs) {{
                        textArgs[0] = textArgs[0] + ' ' + Math.random().toString(36).substr(2, 1);
                        return originalFillText.apply(this, textArgs);
                    }};
                }}
                return context;
            }};
            """
            self.page.run_js(js_code)
            
            # 注入 UserScript 处理外部链接
            if BEHAVIOR_INJECTION_ENABLED:
                self.user_script.inject_external_link_handler()
                self.user_script.inject_mouse_behavior()
        
        except Exception as e:
            logger.debug(f"指纹优化异常: {str(e)}")

    def smart_delay_system(self):
        """智能延迟系统"""
        base_delay = random.uniform(2, 5)
        request_density = self.request_count / (time.time() - self.session_start_time + 1)
        if request_density > 0.5:
            base_delay *= random.uniform(1.5, 3.0)
        
        if random.random() < 0.1:
            base_delay = random.uniform(30, 90)
        
        final_delay = base_delay * random.uniform(0.8, 1.2)
        time.sleep(final_delay)
        self.request_count += 1

    def apply_evasion_strategy(self):
        """应用验证规避策略"""
        self.smart_delay_system()
        self.varied_scrolling_behavior()
        self.human_behavior_simulation()
        self.session_health_monitoring()

    def varied_scrolling_behavior(self):
        """多样化滚动行为"""
        scroll_patterns = [
            lambda: self.page.run_js("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});"),
            lambda: self.page.run_js("window.scrollBy(0, 300 + Math.random() * 500);"),
            lambda: self.page.run_js("window.scrollTo({top: Math.random() * document.body.scrollHeight, behavior: 'smooth'});")
        ]
        
        chosen_pattern = random.choice(scroll_patterns)
        chosen_pattern()
        time.sleep(random.uniform(3, 8))

    def human_behavior_simulation(self):
        """人类行为模拟（Python层）"""
        behaviors = [
            self.micro_interactions,
            self.focus_switching,
            self.reading_pattern_simulation
        ]
        
        # 随机选择1-2个行为
        for behavior in random.sample(behaviors, random.randint(1, 2)):
            behavior()

    def micro_interactions(self):
        """微交互"""
        try:
            self.page.run_js("""
                const elements = document.querySelectorAll('p, div, span');
                if (elements.length > 0) {
                    elements[Math.floor(Math.random() * elements.length)].click();
                }
            """)
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

    def focus_switching(self):
        """焦点切换模拟"""
        try:
            self.page.run_js("""
                if (document.activeElement) document.activeElement.blur();
            """)
            time.sleep(random.uniform(0.3, 1.0))
        except:
            pass

    def reading_pattern_simulation(self):
        """阅读模式模拟"""
        try:
            for _ in range(random.randint(2, 4)):
                time.sleep(random.uniform(2, 5))
                self.page.run_js("window.scrollBy(0, 100);")
        except:
            pass

    def session_health_monitoring(self):
        """会话健康监控"""
        try:
            session_duration = time.time() - self.session_start_time
            
            if session_duration > 1800:
                self.page.refresh()
                time.sleep(5)
                self.session_start_time = time.time()
                self.request_count = 0
                
            page_title = self.page.title.lower()
            if any(indicator in page_title for indicator in ["checking", "verifying", "just a moment"]):
                self.evasive_maneuvers()
                
        except Exception as e:
            pass

    def evasive_maneuvers(self):
        """规避操作"""
        try:
            self.page.back()
            time.sleep(random.uniform(8, 15))
            self.page.refresh()
            time.sleep(random.uniform(5, 10))
            self.page.get(self.site_config['unread_url'])
            time.sleep(random.uniform(3, 7))
        except Exception as e:
            logger.warning(f"规避操作失败: {e}")

    def handle_cloudflare_check(self, timeout=20):
        """处理Cloudflare检查"""
        start_time = time.time()
        check_count = 0
        
        while time.time() - start_time < timeout:
            try:
                page_title = self.page.title
                check_count += 1
                
                if page_title and "Checking" not in page_title and "Just a moment" not in page_title:
                    body_length = len(self.page.html)
                    if body_length > 1000:
                        return True
                
                if page_title and ("Checking" in page_title or "Just a moment" in page_title):
                    logger.debug(f"Cloudflare检查中... ({check_count})")
                
                time.sleep(1)
                    
            except Exception as e:
                time.sleep(1)
        
        logger.warning(f"Cloudflare检查超时 ({timeout}秒)，继续执行")
        return True

    def call_ocr_api(self, base64_image, api_key, retries=2):
        """OCR API调用"""
        for attempt in range(retries):
            try:
                url = "https://api.ocr.space/parse/image"
                payload = {
                    "apikey": api_key, 
                    "base64Image": base64_image, 
                    "language": "eng", 
                    "OCREngine": "2"
                }
                response = requests.post(url, data=payload, timeout=20)
                result = response.json()

                if not result.get("IsErroredOnProcessing"):
                    parsed_results = result.get("ParsedResults", [])
                    if parsed_results:
                        parsed_text = parsed_results[0].get("ParsedText", "").strip()
                        if parsed_text:
                            return parsed_text

            except Exception as e:
                logger.warning(f"OCR尝试{attempt+1}失败: {str(e)}")

            if attempt < retries - 1:
                time.sleep(3)

        return None

    def save_caches(self):
        """保存缓存"""
        if self.cache_saved:
            return
            
        try:
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
            
            session_data = {
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'site_name': self.site_name,
                'username_hash': hash(self.username) if self.username else 0,
                'total_runs': self.session_data.get('total_runs', 0) + 1
            }
            CacheManager.save_site_cache(session_data, self.site_name, 'session_data')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 缓存保存完成")
            
        except Exception as e:
            logger.error(f"❌ 缓存保存失败: {str(e)}")

    def try_cache_login(self):
        """尝试缓存登录"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录")
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            return False
        
        try:
            logger.info("🎯 尝试缓存登录...")
            self.page.get(self.site_config['base_url'])
            time.sleep(2)
            
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            self.page.refresh()
            time.sleep(3)
            
            self.handle_cloudflare_check()
            
            if self.verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"缓存登录异常: {str(e)}")
            return False

    def verify_login_status(self, max_retries=3):
        """验证登录状态 - 双重验证"""
        logger.info("🔍 验证登录状态...")
        
        # 验证标志
        private_topic_ok = False
        username_ok = False
        
        for attempt in range(max_retries):
            try:
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题 (尝试 {attempt+1}/{max_retries})")
                
                self.page.get(private_url)
                time.sleep(5)
                
                self.handle_cloudflare_check()
                time.sleep(3)
                
                self.page.wait.eles_loaded('body', timeout=10)
                
                # 1. 私有主题验证 - 只要能访问且不出现登录页即成功
                content = self.page.html
                # 检查是否包含主题内容特征
                if "topic" in content.lower() or "类别" in content or len(content) > 500000:
                    private_topic_ok = True
                    logger.debug("✅ 私有主题验证通过")
                
                # 2. 用户名验证
                user_element = self.page.ele(f'text:{self.username}') or \
                              self.page.ele(f'@data-user-card:{self.username}') or \
                              self.page.ele(f'a[href*="{self.username}"]')
                
                if user_element:
                    username_ok = True
                    logger.debug(f"✅ 用户名验证通过: {self.username}")
                
                # JS变量检查作为备用
                if not username_ok:
                    js_check = self.page.run_js(f"""
                        return (window.currentUser && window.currentUser.username === '{self.username}') || 
                               (window.Discourse && window.Discourse.User && 
                                window.Discourse.User.current() && 
                                window.Discourse.User.current().username === '{self.username}');
                    """)
                    if js_check:
                        username_ok = True
                        logger.debug(f"✅ JS用户名验证通过")
                
                # 双重验证必须都通过
                if private_topic_ok and username_ok:
                    logger.success("🎉 双重验证通过")
                    return True
                
                time.sleep(2)
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"验证尝试 {attempt+1} 异常，重试中...")
                    time.sleep(3)
        
        logger.error(f"❌ 登录验证失败")
        return False

    def login(self, max_retries=2):
        """登录流程"""
        self.page.set.cookies([])
        
        for attempt in range(max_retries):
            try:
                logger.info(f"🔐 执行登录 (尝试 {attempt+1}/{max_retries})")
                
                self.page.get(self.site_config['login_url'])
                time.sleep(3)
                
                self.page.wait.ele_displayed('#login-account-name', timeout=10)
                
                self.handle_cloudflare_check()
                time.sleep(1)
                
                self.page.ele("#login-account-name").clear()
                self.page.ele("#login-account-password").clear()
                time.sleep(0.5)
                
                logger.info("⌨️ 输入用户名...")
                self.page.ele("#login-account-name").input(self.username)
                time.sleep(0.5)
                
                logger.info("⌨️ 输入密码...")
                self.page.ele("#login-account-password").input(self.password)
                time.sleep(0.5)
                
                logger.info("🔑 点击登录按钮...")
                self.page.ele("#login-button").click()
                time.sleep(12)
                
                self.handle_cloudflare_check()
                time.sleep(3)
                
                if self.verify_login_status():
                    logger.success("✅ 登录成功")
                    self.save_caches()
                    return True
                else:
                    time.sleep(5)
                
            except Exception as e:
                logger.error(f"❌ 登录出错 (尝试 {attempt+1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(5)
        
        logger.error("❌ 所有登录尝试均失败")
        return False

    def ensure_logged_in(self):
        """确保登录"""
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login():
            return True
        
        login_success = self.login()
        if not login_success:
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    def find_topic_elements(self):
        """简洁版主题查找 - 基于tag:a扫描 + 正则提取"""
        logger.info("🎯 查找主题...")
        
        try:
            # 等待页面加载
            self.page.wait.doc_loaded()
            time.sleep(3)
            
            # 获取所有链接
            all_links = self.page.eles('tag:a', timeout=10)
            if not all_links:
                logger.warning("⚠️ 未找到任何链接")
                return []
            
            seen_ids = set()
            topic_urls = []
            
            for link in all_links:
                href = link.attr('href')
                if not href:
                    continue
                
                # 排除非主题链接
                if any(exclude in href.lower() for exclude in ['/tags/', '/c/', '/u/', '/uploads/', '.png', '.jpg', '.gif']):
                    continue
                
                # 提取主题ID
                match = re.search(r'/t/(?:topic/)?(\d+)', href)
                if match:
                    topic_id = match.group(1)
                    if topic_id not in seen_ids:
                        seen_ids.add(topic_id)
                        full_url = f"{self.site_config['base_url'].rstrip('/')}/t/topic/{topic_id}"
                        topic_urls.append(full_url)
            
            logger.info(f"🔗 找到 {len(topic_urls)} 个主题")
            return topic_urls
            
        except Exception as e:
            logger.error(f"❌ 查找主题失败: {str(e)}")
            return []

    # ======================== 新增浏览记录收集功能 ========================

    def inject_read_behavior(self):
        """注入阅读行为标记系统 - 关键改造"""
        try:
            js_code = """
            (function() {
                'use strict';
                
                // 设置阅读标记
                localStorage.setItem('read', 'true');
                localStorage.setItem('isFirstRun', 'false');
                
                // 创建阅读时间记录
                window.readingStartTime = Date.now();
                
                // 监听滚动事件来记录阅读行为
                let lastScrollTime = 0;
                let scrollCount = 0;
                
                window.addEventListener('scroll', function() {
                    const now = Date.now();
                    if (now - lastScrollTime > 1000) { // 至少1秒间隔
                        scrollCount++;
                        lastScrollTime = now;
                        
                        // 记录滚动深度
                        const scrollDepth = (window.scrollY + window.innerHeight) / document.body.scrollHeight;
                        localStorage.setItem('lastScrollDepth', scrollDepth.toFixed(2));
                        localStorage.setItem('scrollCount', scrollCount);
                        
                        // 触发自定义事件，让网站知道用户在阅读
                        document.dispatchEvent(new CustomEvent('userReading', {
                            detail: {
                                scrollDepth: scrollDepth,
                                scrollCount: scrollCount,
                                timestamp: now
                            }
                        }));
                    }
                });
                
                // 模拟阅读时间计算
                setInterval(() => {
                    const readingTime = Math.floor((Date.now() - window.readingStartTime) / 1000);
                    localStorage.setItem('readingTime', readingTime);
                    
                    // 定期触发活动事件
                    if (readingTime % 30 === 0) { // 每30秒
                        document.dispatchEvent(new Event('visibilitychange'));
                        window.dispatchEvent(new Event('focus'));
                    }
                }, 1000);
                
                console.log('阅读行为系统已注入');
            })();
            """
            self.page.run_js(js_code)
            return True
        except Exception as e:
            logger.error(f"❌ 阅读行为注入失败: {str(e)}")
            return False

    def browse_topic_enhanced_with_recording(self, topic_url):
        """增强版主题浏览 - 确保网站记录浏览痕迹"""
        try:
            logger.info(f"📖 深度浏览主题: {topic_url.split('/')[-1]}")
            
            # 访问主题
            self.page.get(topic_url)
            time.sleep(random.uniform(4, 8))
            
            # 注入阅读行为系统
            self.inject_read_behavior()
            time.sleep(2)
            
            # 应用规避策略
            self.apply_evasion_strategy()
            
            # 执行深度阅读流程
            reading_success = self.deep_reading_flow()
            
            # 1%概率点赞
            if random.random() < 0.01:
                self.click_like()
            
            # 确保阅读时间足够被记录
            total_reading_time = random.uniform(25, 60)  # 25-60秒阅读时间
            logger.info(f"⏱️ 确保阅读时间: {total_reading_time:.1f}秒")
            time.sleep(total_reading_time)
            
            # 最终滚动确认
            self.final_scroll_confirmation()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 深度浏览主题失败: {str(e)}")
            return False

    def deep_reading_flow(self):
        """深度阅读流程 - 模拟真实用户阅读模式"""
        try:
            # 1. 初始阅读阶段
            logger.debug("📚 初始阅读阶段")
            self.simulate_initial_reading()
            
            # 2. 深度滚动阶段
            logger.debug("🔄 深度滚动阶段")
            self.simulate_deep_scrolling()
            
            # 3. 重点内容停留
            logger.debug("🎯 重点内容停留")
            self.simulate_content_engagement()
            
            # 4. 最终确认阶段
            logger.debug("✅ 最终确认阶段")
            self.simulate_reading_completion()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 深度阅读流程异常: {str(e)}")
            return False

    def simulate_initial_reading(self):
        """模拟初始阅读 - 关键的第一印象"""
        # 缓慢滚动开始
        for i in range(3):
            scroll_amount = random.randint(200, 400)
            self.page.run_js(f"window.scrollBy(0, {scroll_amount});")
            time.sleep(random.uniform(3, 6))  # 较长的阅读停留
            
            # 偶尔触发微交互
            if random.random() < 0.3:
                self.trigger_micro_interaction()

    def simulate_deep_scrolling(self):
        """模拟深度滚动 - 确保覆盖整个页面"""
        scroll_sequences = [
            lambda: self.page.run_js("window.scrollTo(0, document.body.scrollHeight * 0.3);"),
            lambda: self.page.run_js("window.scrollTo(0, document.body.scrollHeight * 0.6);"),
            lambda: self.page.run_js("window.scrollTo(0, document.body.scrollHeight * 0.8);"),
            lambda: self.page.run_js("window.scrollTo(0, document.body.scrollHeight);")
        ]
        
        for scroll_func in scroll_sequences:
            scroll_func()
            # 关键：在重要位置停留较长时间
            stay_time = random.uniform(5, 12)
            time.sleep(stay_time)
            
            # 触发阅读事件
            self.trigger_reading_events()

    def simulate_content_engagement(self):
        """模拟内容互动 - 让网站知道用户对内容感兴趣"""
        # 随机回到某些部分重新阅读
        if random.random() < 0.6:  # 60%概率重新阅读某些内容
            re_read_positions = [0.2, 0.4, 0.7]
            for position in random.sample(re_read_positions, random.randint(1, 2)):
                self.page.run_js(f"window.scrollTo(0, document.body.scrollHeight * {position});")
                time.sleep(random.uniform(4, 8))

    def simulate_reading_completion(self):
        """模拟阅读完成 - 确认用户已读完"""
        # 滚动到底部并停留
        self.page.run_js("window.scrollTo(0, document.body.scrollHeight);")
        completion_stay = random.uniform(8, 15)
        time.sleep(completion_stay)
        
        # 触发完成事件
        self.trigger_completion_events()

    def trigger_reading_events(self):
        """触发阅读相关事件"""
        try:
            js_code = """
            // 触发阅读相关事件
            document.dispatchEvent(new Event('visibilitychange'));
            window.dispatchEvent(new Event('focus'));
            window.dispatchEvent(new Event('scroll'));
            
            // 模拟用户活动
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                clientX: Math.random() * window.innerWidth,
                clientY: Math.random() * window.innerHeight
            }));
            
            // 更新阅读时间
            if (window.readingStartTime) {
                const readingTime = Math.floor((Date.now() - window.readingStartTime) / 1000);
                localStorage.setItem('totalReadingTime', readingTime);
            }
            """
            self.page.run_js(js_code)
        except:
            pass

    def trigger_completion_events(self):
        """触发阅读完成事件"""
        try:
            js_code = """
            // 标记阅读完成
            localStorage.setItem('readingComplete', 'true');
            localStorage.setItem('lastReadTime', new Date().toISOString());
            
            // 触发自定义完成事件
            document.dispatchEvent(new CustomEvent('readingFinished', {
                detail: {
                    timestamp: Date.now(),
                    scrollDepth: localStorage.getItem('lastScrollDepth') || '1.0',
                    totalTime: localStorage.getItem('totalReadingTime') || '0'
                }
            }));
            
            // 确保焦点在页面
            window.focus();
            """
            self.page.run_js(js_code)
        except:
            pass

    def trigger_micro_interaction(self):
        """触发微交互"""
        try:
            # 随机点击段落或图片
            self.page.run_js("""
                const clickable = document.querySelector('p, img, .post-content, .topic-body');
                if (clickable) {
                    clickable.click();
                }
            """)
            time.sleep(0.5)
        except:
            pass

    def final_scroll_confirmation(self):
        """最终滚动确认 - 确保网站记录完整的阅读行为"""
        try:
            # 快速滚动确认用户活跃
            self.page.run_js("window.scrollTo(0, 0);")
            time.sleep(1)
            self.page.run_js("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        except:
            pass

    def browse_topics_with_recording(self):
        """改造版主题浏览 - 确保网站收集浏览记录"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0

        try:
            logger.info(f"🌐 开始深度浏览 {self.site_name} 主题...")
            
            # 注入UserScript
            if BEHAVIOR_INJECTION_ENABLED and self.user_script:
                self.user_script.inject_external_link_handler()
            
            # 获取主题列表
            self.page.get(self.site_config['unread_url'])
            self.apply_evasion_strategy()
            
            topic_urls = self.find_topic_elements()
            if not topic_urls:
                logger.warning("❌ 未找到可浏览的主题")
                return 0
            
            # 选择要浏览的主题 - 数量减少但时间更长
            browse_count = min(random.randint(2, 4), len(topic_urls))  # 减少数量
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划深度浏览 {browse_count} 个主题")
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 深度浏览主题 {i+1}/{browse_count}")
                    
                    # 使用改造后的深度浏览方法
                    if self.browse_topic_enhanced_with_recording(topic_url):
                        success_count += 1
                        logger.success(f"✅ 主题 {i+1} 浏览完成")
                    else:
                        logger.warning(f"⚠️ 主题 {i+1} 浏览异常")
                    
                    # 返回列表页
                    self.page.get(self.site_config['unread_url'])
                    time.sleep(3)
                    
                    # 主题间等待 - 模拟真实用户间隔
                    if i < browse_count - 1:
                        interval = random.uniform(30, 60)
                        logger.info(f"⏳ 主题间等待 {interval:.1f} 秒...")
                        time.sleep(interval)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"🎉 共成功深度浏览 {success_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 主题浏览失败: {str(e)}")
            return 0

    # ======================== 优化功能方法 ========================

    def click_like(self):
        """点赞功能 - 1%概率触发"""
        try:
            # 查找点赞按钮 - 使用最初代码中的选择器
            like_button = self.page.ele('.discourse-reactions-reaction-button')
            if like_button:
                logger.info("👍 尝试点赞...")
                like_button.click()
                # 点赞后短暂停留
                time.sleep(random.uniform(1, 2))
                logger.success("✅ 点赞成功")
                return True
            else:
                logger.debug("⚠️ 未找到点赞按钮")
                return False
        except Exception as e:
            logger.debug(f"⚠️ 点赞操作失败（可能已点赞或元素未找到）: {e}")
            return False

    def force_mark_read(self, page=None):
        """强制标记为已读 - 5次滚动到底部确保网站记录阅读行为"""
        if page is None:
            page = self.page
            
        logger.debug("📖 强制标记为已读...")
        for i in range(5):
            try:
                # 滚动到底部
                page.run_js("window.scrollTo(0, document.body.scrollHeight);")
                # 关键：长时间停留让网站记录阅读行为
                wait_time = random.uniform(3, 8)
                time.sleep(wait_time)
                
                # 偶尔滚动回中间模拟真实阅读
                if random.random() < 0.3:
                    page.run_js("window.scrollTo(0, document.body.scrollHeight * 0.3);")
                    time.sleep(2)
                    
            except Exception as e:
                logger.debug(f"滚动异常: {e}")
        
        logger.debug("✅ 强制标记完成")

    def prove_page_activity(self, page=None):
        """页面活性证明 - 主动触发浏览器事件证明用户活跃"""
        if page is None:
            page = self.page
            
        try:
            js_code = """
            // 触发 visibilitychange 事件
            document.dispatchEvent(new Event('visibilitychange'));
            
            // 触发 focus 事件
            window.dispatchEvent(new Event('focus'));
            
            // 触发 scroll 事件
            window.dispatchEvent(new Event('scroll'));
            
            // 触发鼠标移动事件
            document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                cancelable: true,
                clientX: 100,
                clientY: 100
            }));
            """
            page.run_js(js_code)
            time.sleep(1)
        except Exception as e:
            logger.debug(f"页面活性证明异常: {e}")

    def micro_navigation_in_topic(self):
        """主题内微导航 - 15%概率点击相关链接"""
        if random.random() < 0.15:
            try:
                # 在主题内点击相关链接（但不离开当前主题）
                internal_links = self.page.eles('tag:a[href*="/t/"]')
                if internal_links:
                    link = random.choice(internal_links)
                    link_text = link.text[:20] + "..." if len(link.text) > 20 else link.text
                    logger.info(f"🔗 主题内微导航: {link_text}")
                    link.click()
                    time.sleep(random.uniform(5, 10))
                    
                    # 返回原主题
                    self.page.back()
                    time.sleep(3)
                    return True
            except Exception as e:
                logger.debug(f"主题内微导航异常: {e}")
        
        return False

    def smart_sleep(self):
        """智能休眠系统 - 30%概率长休眠模拟真实用户行为"""
        if random.random() < 0.3:
            sleep_time = random.uniform(60, 180)
            logger.info(f"💤 智能休眠 {sleep_time:.1f} 秒")
            time.sleep(sleep_time)
            return True
        return False

    def early_exit(self):
        """提前退出机制 - 5%概率模拟用户离开"""
        if random.random() < 0.05:
            logger.info("🚪 模拟用户提前离开")
            return True
        return False

    def deep_scroll_browsing_enhanced(self, page=None):
        """增强版深度滚动浏览 - 集成所有优化功能"""
        if page is None:
            page = self.page
        
        # 1. 先证明页面活性
        self.prove_page_activity(page)
        
        # 2. 随机滚动次数
        scroll_count = random.randint(3, 7)
        
        for i in range(scroll_count):
            scroll_distance = random.randint(300, 800)
            page.run_js(f"window.scrollBy(0, {scroll_distance});")
            
            wait_time = random.uniform(2, 6)
            time.sleep(wait_time)
            
            # 3. 偶尔微导航
            if random.random() < 0.1:  # 10%概率
                if self.micro_navigation_in_topic():
                    # 如果发生了导航，重新开始滚动
                    break
            
            # 4. 检查是否到达底部
            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight - 100"
            )
            if at_bottom:
                bottom_wait = random.uniform(5, 8)
                time.sleep(bottom_wait)
                break
            
            # 5. 偶尔微交互
            if random.random() < 0.3:
                self.micro_interactions_in_page(page)
        
        # 6. 强制标记为已读（关键！）
        self.force_mark_read(page)
        
        # 7. 智能休眠（30%概率长休眠）
        self.smart_sleep()
        
        # 8. 提前退出机制（5%概率）
        if self.early_exit():
            return True
        
        return False

    def micro_interactions_in_page(self, page):
        """在指定页面的微交互"""
        try:
            page.run_js("""
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
                
                const elements = document.querySelectorAll('p, div, span');
                if (elements.length > 0) {
                    elements[Math.floor(Math.random() * elements.length)].click();
                }
            """)
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

    def get_connect_info_single_tab(self):
        """单标签页获取连接信息 - 恢复表格打印"""
        logger.info("🔗 获取连接信息...")
        
        try:
            current_url = self.page.url
            
            # 访问连接页面并应用规避策略
            self.page.get(self.site_config['connect_url'])
            time.sleep(5)  # 增加初始等待时间
            
            # 应用规避策略确保页面完全加载
            self.apply_evasion_strategy()
            
            # 等待表格出现
            table = None
            for i in range(5):
                table = self.page.ele("tag:table", timeout=5)
                if table:
                    break
                time.sleep(2)
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                if self.site_name == 'idcflare':
                    logger.info("ℹ️ idcflare连接信息获取失败，不影响主流程")
                self.page.get(current_url)
                time.sleep(2)
                return True
            
            # 解析表格数据
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
            
            if info:
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("="*60 + "\n", flush=True)
                
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
                total = len(info)
                logger.success(f"📈 统计: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 返回原页面
            self.page.get(current_url)
            time.sleep(2)
            
            logger.info("✅ 连接信息获取完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")
            if self.site_name == 'idcflare':
                logger.info("ℹ️ idcflare连接信息异常，但不影响继续执行")
                return True
            try:
                self.page.get(self.site_config['unread_url'])
                time.sleep(2)
            except:
                pass
            return False

    def run_complete_process(self):
        """执行完整流程 - 使用改造后的浏览方法"""
        try:
            logger.info(f"🚀 开始处理 {self.site_name}")
            
            # 1. 确保登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
                                
            # 2. 单标签页连接信息
            connect_success = self.get_connect_info_single_tab()
            if not connect_success and self.site_name != 'idcflare':
                logger.warning(f"⚠️ {self.site_name} 连接信息获取失败")

            # 3. 使用改造后的主题浏览方法
            browse_count = self.browse_topics_with_recording()
            
            # 4. 保存缓存
            self.save_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 深度浏览 {browse_count} 个主题")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
            
        finally:
            self.browsing_active = False
            try:
                if self.browser:
                    self.browser.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    logger.info("🚀 Linux.Do 自动化 v4.4 修复版启动")
    
    if GITHUB_ACTIONS:
        logger.info("🎯 GitHub Actions 环境")
    
    success_sites = []
    failed_sites = []

    # 检查凭证
    for site_name, creds in SITE_CREDENTIALS.items():
        if not creds.get('username') or not creds.get('password'):
            logger.warning(f"⏭️ {site_name} 凭证未配置")

    # 站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    target_sites = SITES if site_selector == "all" else [s for s in SITES if s['name'] == site_selector]

    if not target_sites:
        logger.error(f"❌ 未找到站点: {site_selector}")
        sys.exit(1)

    logger.info(f"🎯 目标站点: {', '.join([s['name'] for s in target_sites])}")

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        if not credentials.get('username') or not credentials.get('password'):
            logger.warning(f"⏭️ 跳过 {site_name} - 凭证未配置")
            failed_sites.append(site_name)
            continue

        logger.info(f"🔧 处理站点: {site_name}")
        
        try:
            browser = LinuxDoBrowser(site_config, credentials)
            success = browser.run_complete_process()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(10, 20)
            logger.info(f"⏳ 站点间等待 {wait_time:.1f} 秒...")
            time.sleep(wait_time)

    # 总结
    logger.info("=" * 60)
    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败: {', '.join(failed_sites) if failed_sites else '无'}")
    logger.info("=" * 60)

    if success_sites:
        logger.success(f"🎉 任务完成: {len(success_sites)}/{len(target_sites)} 个站点成功")
        sys.exit(0)
    else:
        logger.error("💥 任务失败: 所有站点均未成功")
        sys.exit(1)

if __name__ == "__main__":
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 必需环境变量未设置: {', '.join(missing_vars)}")
    
    if not OCR_API_KEY:
        logger.warning("⚠️ 未配置OCR_API_KEY，验证码处理将不可用")
    
    main()
