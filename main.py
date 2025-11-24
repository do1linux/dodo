#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Linux.do 自动化浏览工具 - 完整集成版 v2.1
========================================
修复日志:
- 增强登录验证机制，支持多种检测方式
- 增加重试逻辑，提高稳定性
- 优化等待时间，适应慢速网络
- 修复 run_complete_process 方法中缺失的 browse_topics_hybrid 调用
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
        'private_topic_url': 'https://linux.do/t/topic/187640',
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
        'private_topic_url': 'https://idcflare.com/t/topic/24',
        'latest_url': 'https://idcflare.com/latest',
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
TURNSTILE_PATCH_ENABLED = os.environ.get("TURNSTILE_PATCH_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
OCR_API_KEY = os.getenv("OCR_API_KEY")
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
TURNSTILE_PATCH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turnstilePatch")

# ======================== 缓存管理器 ========================
class CacheManager:
    @staticmethod
    def get_cache_directory():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        return os.path.join(CacheManager.get_cache_directory(), file_name)

    @staticmethod
    def load_cache(file_name):
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
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
            logger.info(f"💾 保存缓存: {file_name}")
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
    def clear_site_cache_on_failure(site_name):
        """登录失败时清除该站点的缓存"""
        try:
            cache_types = ['cf_cookies', 'session_data']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{site_name}.json"
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 清除缓存: {file_name}")
            
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
        self.browser = None  # 需要保存browser实例
        self.cache_saved = False
        self.session_start_time = time.time()
        self.request_count = 0
        self.browsing_active = True
        self.initialize_browser()

    def initialize_browser(self):
        """浏览器初始化 - 集成反检测和扩展"""
        try:
            co = ChromiumOptions()
            
            # GitHub Actions 环境特殊配置
            if GITHUB_ACTIONS:
                logger.info("🎯 GitHub Actions 环境优化配置")
                co.headless(True)
                co.set_argument("--disable-dev-shm-usage")
                co.set_argument("--disable-gpu")
                co.set_argument("--no-sandbox")
                co.set_argument("--disable-software-rasterizer")
                co.set_argument("--disable-background-timer-throttling")
                co.set_argument("--disable-backgrounding-occluded-windows")
                co.set_argument("--disable-renderer-backgrounding")
            else:
                co.headless(HEADLESS)
                
            co.incognito(True)
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            
            # 基础反检测配置
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-features=VizDisplayCompositor")
            co.set_argument("--disable-web-security")
            co.set_argument("--disable-features=TranslateUI")
            co.set_argument("--disable-ipc-flooding-protection")
            co.set_argument("--no-default-browser-check")
            co.set_argument("--disable-component-extensions-with-background-pages")
            co.set_argument("--disable-default-apps")
            co.set_argument("--disable-popup-blocking")
            co.set_argument("--disable-prompt-on-repost")
            co.set_argument("--disable-background-networking")
            co.set_argument("--disable-sync")
            co.set_argument("--disable-translate")
            co.set_argument("--metrics-recording-only")
            co.set_argument("--safebrowsing-disable-auto-update")
            co.set_argument("--disable-client-side-phishing-detection")
            co.set_argument("--disable-hang-monitor")
            co.set_argument("--disable-crash-reporter")
            
            # 用户代理和窗口设置
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            co.set_argument("--window-size=1920,1080")
            co.set_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
            
            # 加载turnstilePatch扩展
            if TURNSTILE_PATCH_ENABLED and os.path.exists(TURNSTILE_PATCH_PATH):
                co.set_argument(f"--load-extension={TURNSTILE_PATCH_PATH}")
                logger.info(f"✅ 加载turnstilePatch扩展，路径: {TURNSTILE_PATCH_PATH}")
            else:
                logger.warning(f"⚠️ 未加载turnstilePatch扩展，路径存在: {os.path.exists(TURNSTILE_PATCH_PATH)}")
        
            # 保存browser实例以创建新标签页
            self.browser = ChromiumPage(addr_or_opts=co)
            self.page = self.browser.new_tab()
            
            # 执行指纹优化
            self.enhance_browser_fingerprint()
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
        
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def enhance_browser_fingerprint(self):
        """浏览器指纹优化"""
        try:
            resolutions = [(1920,1080), (1366,768), (2560,1440)]
            cores = [4, 8, 12, 16]
            mem = [4, 8, 16]
            width, height = random.choice(resolutions)
            core_count = random.choice(cores)
            mem_size = random.choice(mem)
        
            js_code = f"""
                Object.defineProperties(navigator, {{
                    webdriver: {{ get: () => false }},
                    language: {{ get: () => 'zh-CN' }},
                    languages: {{ get: () => ['zh-CN', 'zh', 'en'] }},
                    platform: {{ get: () => 'Win32' }},
                    hardwareConcurrency: {{ get: () => {core_count} }},
                    deviceMemory: {{ get: () => {mem_size} }},
                    maxTouchPoints: {{ get: () => 0 }},
                    cookieEnabled: {{ get: () => true }},
                    doNotTrack: {{ get: () => null }},
                    vendor: {{ get: () => 'Google Inc.' }},
                    productSub: {{ get: () => '20030107' }},
                
                    plugins: {{
                        get: () => [
                            {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }},
                            {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' }},
                            {{ name: 'Native Client', filename: 'internal-nacl-plugin' }}
                        ]
                    }}
                }});

                Object.defineProperty(screen, 'width', {{get: () => {width}}});
                Object.defineProperty(screen, 'height', {{get: () => {height}}});
                Object.defineProperty(screen, 'colorDepth', {{get: () => 24}});
            
                Object.defineProperty(window, 'chrome', {{
                    value: {{
                        runtime: {{}},
                        loadTimes: () => {{}},
                        csi: () => {{}},
                        app: {{}}
                    }},
                }});

                const originalQuery = Permissions.prototype.query;
                Permissions.prototype.query = function(parameters) {{
                    return Promise.resolve({{ state: 'granted' }});
                }};

                const getContext = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(type) {{
                    const ctx = getContext.apply(this, arguments);
                    if (type === '2d') {{
                        const origFill = ctx.fillText;
                        ctx.fillText = function(text, x, y) {{
                            return origFill.call(this, text, x + Math.random() * 0.5, y);
                        }};
                    }}
                    return ctx;
                }};

                setInterval(() => {{
                    document.dispatchEvent(new MouseEvent('mousemove', {{
                        bubbles: true,
                        clientX: Math.random() * window.innerWidth,
                        clientY: Math.random() * window.innerHeight
                    }}));
                }}, 30000 + Math.random() * 20000);
            """
            self.page.run_js(js_code)
            logger.debug("✅ 浏览器指纹优化已应用")
        except Exception as e:
            logger.debug(f"指纹优化异常: {str(e)}")

    def random_sleep(self):
        """增加随机休眠"""
        if random.random() < 0.3:
            sleep_time = random.uniform(60, 180)
            time.sleep(sleep_time)
            logger.info("🛌 随机休眠模拟")

    def apply_evasion_strategy(self):
        """应用验证规避策略"""
        self.smart_delay_system()
        self.varied_scrolling_behavior()
        self.human_behavior_simulation()
        self.session_health_monitoring()

    def smart_delay_system(self):
        """智能延迟系统"""
        base_delay = random.uniform(2, 5)
        request_density = self.request_count / (time.time() - self.session_start_time + 1)
        if request_density > 0.5:
            base_delay *= random.uniform(1.5, 3.0)
            logger.debug("📊 检测到密集请求，增加延迟")
        
        if random.random() < 0.1:
            base_delay = random.uniform(30, 90)
            logger.info("🛌 模拟长时间阅读")
        
        final_delay = base_delay * random.uniform(0.8, 1.2)
        time.sleep(final_delay)
        self.request_count += 1

    def varied_scrolling_behavior(self):
        """多样化滚动行为"""
        scroll_patterns = [
            lambda: self.page.run_js("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});"),
            lambda: self.page.run_js("""
                let currentPosition = 0;
                const scrollHeight = document.body.scrollHeight;
                const scrollStep = scrollHeight / 5;
                
                function scrollStepByStep() {
                    if (currentPosition < scrollHeight) {
                        currentPosition += scrollStep;
                        window.scrollTo(0, currentPosition);
                        setTimeout(scrollStepByStep, 800 + Math.random() * 500);
                    }
                }
                scrollStepByStep();
            """),
            lambda: self.page.run_js("""
                const scrollPositions = [
                    window.innerHeight * 0.3,
                    window.innerHeight * 1.2, 
                    window.innerHeight * 2.5,
                    document.body.scrollHeight * 0.6
                ];
                
                scrollPositions.forEach((pos, index) => {
                    setTimeout(() => {
                        window.scrollTo({top: pos, behavior: 'smooth'});
                    }, index * 1200 + Math.random() * 800);
                });
            """)
        ]
        
        chosen_pattern = random.choice(scroll_patterns)
        chosen_pattern()
        time.sleep(random.uniform(3, 8))

    def human_behavior_simulation(self):
        """人类行为模拟"""
        behaviors = [
            self.micro_interactions,
            self.focus_switching,
            self.reading_pattern_simulation,
            self.mouse_movement_emulation
        ]
        
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
                
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
            """)
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

    def focus_switching(self):
        """焦点切换模拟"""
        try:
            self.page.run_js("""
                if (document.activeElement) document.activeElement.blur();
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true}));
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

    def mouse_movement_emulation(self):
        """增强版鼠标移动模拟"""
        try:
            self.page.run_js("""
                function generateMousePath(startX, startY, endX, endY, steps = 15) {
                    const cp1x = startX + (endX - startX) * 0.3;
                    const cp1y = startY + (endY - startY) * 0.7;
                    
                    const path = [];
                    for (let i = 0; i <= steps; i++) {
                        const t = i / steps;
                        const x = Math.pow(1-t, 2) * startX + 
                                 2 * (1-t) * t * cp1x + 
                                 Math.pow(t, 2) * endX;
                        const y = Math.pow(1-t, 2) * startY + 
                                 2 * (1-t) * t * cp1y + 
                                 Math.pow(t, 2) * endY;
                        path.push({x, y});
                    }
                    return path;
                }
                
                const startX = Math.random() * window.innerWidth;
                const startY = Math.random() * window.innerHeight;
                const endX = Math.random() * window.innerWidth;
                const endY = Math.random() * window.innerHeight;
                const path = generateMousePath(startX, startY, endX, endY);
                
                path.forEach((point, index) => {
                    setTimeout(() => {
                        document.dispatchEvent(new MouseEvent('mousemove', {
                            bubbles: true,
                            clientX: point.x,
                            clientY: point.y
                        }));
                    }, index * 40);
                });
            """)
        except Exception as e:
            logger.debug(f"鼠标轨迹模拟失败: {e}")

    def session_health_monitoring(self):
        """会话健康监控"""
        try:
            session_duration = time.time() - self.session_start_time
            
            if session_duration > 1800:
                logger.info("🔄 长时间运行，主动刷新会话")
                self.page.refresh()
                time.sleep(5)
                self.session_start_time = time.time()
                self.request_count = 0
                
            page_title = self.page.title.lower()
            if any(indicator in page_title for indicator in ["checking", "verifying", "just a moment"]):
                logger.warning("⚠️ 检测到可能验证页面，执行规避")
                self.evasive_maneuvers()
                
        except Exception as e:
            logger.debug(f"会话监控异常: {e}")

    def evasive_maneuvers(self):
        """规避操作"""
        try:
            self.page.back()
            time.sleep(random.uniform(8, 15))
            self.page.refresh()
            time.sleep(random.uniform(5, 10))
            self.page.get(self.site_config['latest_url'])
            time.sleep(random.uniform(3, 7))
        except Exception as e:
            logger.warning(f"规避操作失败: {e}")

    def handle_cloudflare_check(self, timeout=20):  # 增加超时时间
        """处理Cloudflare检查"""
        start_time = time.time()
        check_count = 0
        
        while time.time() - start_time < timeout:
            try:
                page_title = self.page.title
                check_count += 1
                
                logger.debug(f"Cloudflare检查 {check_count}: {page_title}")
                
                # 如果标题正常且不是检查页面
                if page_title and "Checking" not in page_title and "Just a moment" not in page_title:
                    # 额外检查是否有内容加载
                    body_length = len(self.page.html)
                    if body_length > 1000:  # 确保页面有足够内容
                        logger.info(f"✅ Cloudflare检查通过，页面长度: {body_length}")
                        return True
                
                # 如果是检查页面，继续等待
                if page_title and ("Checking" in page_title or "Just a moment" in page_title):
                   # logger.info(f"⏳ Cloudflare检查中... ({check_count})")
                
                time.sleep(1)
                    
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(1)
        
        logger.warning(f"⚠️ Cloudflare检查超时 ({timeout}秒)，继续执行")
        # 超时后也尝试继续，可能页面已经加载
        return True

    def is_captcha_page(self):
        """检查验证码页面"""
        try:
            captcha_img = self.page.ele('img[src*="challenge"]', timeout=2) or \
                         self.page.ele('img[src*="captcha"]', timeout=2)
            captcha_input = self.page.ele('input[name="cf_captcha_answer"]', timeout=1) or \
                           self.page.ele('input[type="text"]@@placeholder*=captcha', timeout=1)
            return captcha_img and captcha_input
        except:
            return False

    def handle_captcha_challenge(self):
        """处理验证码挑战"""
        try:
            logger.info("🛡️ 检测到验证码，尝试OCR识别...")
            
            captcha_img = self.page.ele('img[src*="challenge"]', timeout=5) or \
                         self.page.ele('img[src*="captcha"]', timeout=5)
            if not captcha_img:
                logger.warning("⚠️ 未找到验证码图片")
                return False

            img_src = captcha_img.attr('src')
            base64_data = None
            
            if img_src.startswith('data:image'):
                base64_data = img_src
            else:
                if not img_src.startswith('http'):
                    img_src = self.site_config['base_url'] + img_src
                response = requests.get(img_src, timeout=10)
                if response.status_code != 200:
                    logger.error(f"❌ 验证码图片下载失败: {response.status_code}")
                    return False
                base64_data = "data:image/png;base64," + base64.b64encode(response.content).decode('utf-8')

            if not OCR_API_KEY:
                logger.error("❌ 未设置OCR_API_KEY")
                return False

            ocr_result = self.call_ocr_api(base64_data, OCR_API_KEY)
            if not ocr_result:
                logger.warning("⚠️ OCR识别失败")
                return False

            captcha_input = self.page.ele('input[name="cf_captcha_answer"]', timeout=3) or \
                           self.page.ele('input[type="text"]@@placeholder*=captcha', timeout=3)
            if not captcha_input:
                logger.warning("⚠️ 未找到验证码输入框")
                return False

            logger.info(f"🔍 OCR识别结果: {ocr_result}")
            captcha_input.clear()
            captcha_input.input(ocr_result)
            time.sleep(1)

            submit_btn = self.page.ele('button[type="submit"]', timeout=2) or \
                        self.page.ele('input[type="submit"]', timeout=2)
            if not submit_btn:
                logger.warning("⚠️ 未找到提交按钮")
                return False

            submit_btn.click()
            logger.info("✅ 已提交验证码")
            time.sleep(3)  # 等待验证结果
            return True

        except Exception as e:
            logger.error(f"❌ 验证码处理失败: {str(e)}")
            return False

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
                            logger.info(f"🔍 OCR识别成功: {parsed_text}")
                            return parsed_text
                else:
                    error_msg = result.get("ErrorMessage", "未知错误")
                    logger.warning(f"⚠️ OCR处理错误: {error_msg}")

            except Exception as e:
                logger.warning(f"⚠️ OCR尝试{attempt+1}失败: {str(e)}")

            if attempt < retries - 1:
                time.sleep(3)

        return None

    def save_caches(self):
        """保存缓存 - 登录成功时调用"""
        if self.cache_saved:
            return
            
        try:
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info(f"✅ 保存 {len(cookies)} 个Cookies")
            
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
            logger.error(f"❌ 保存缓存失败: {str(e)}")

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
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def verify_login_status(self, max_retries=3):
        """增强的双重验证机制 - 带重试和多种验证方式"""
        logger.info("🔍 验证登录状态...")
        
        for attempt in range(max_retries):
            try:
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题 (尝试 {attempt+1}/{max_retries}): {private_url}")
                
                self.page.get(private_url)
                time.sleep(3)  # 增加初始等待时间
                
                # 处理Cloudflare验证
                self.handle_cloudflare_check()
                time.sleep(2)  # Cloudflare后额外等待
                
                # 等待页面关键元素加载
                self.page.wait.eles_loaded('body', timeout=10)
                
                # 方法1：检查用户名元素（最可靠）
                user_element = self.page.ele(f'text:{self.username}') or \
                              self.page.ele(f'@data-user-card:{self.username}') or \
                              self.page.ele(f'a[href*="{self.username}"]')
                
                if user_element:
                    logger.success(f"✅ 找到用户名元素: {self.username}")
                    logger.success("🎉 双重验证通过")
                    return True
                
                # 方法2：检查页面内容（备用）
                page_content = self.page.html.lower()
                if self.username.lower() in page_content:
                    logger.success(f"✅ 找到用户名文本: {self.username}")
                    logger.success("🎉 双重验证通过")
                    return True
                
                # 方法3：检查JS变量（最后的手段）
                js_check = self.page.run_js(f"""
                    return (window.currentUser && window.currentUser.username === '{self.username}') || 
                           (window.Discourse && window.Discourse.User && 
                            window.Discourse.User.current() && 
                            window.Discourse.User.current().username === '{self.username}');
                """)
                if js_check:
                    logger.success(f"✅ JS变量中找到用户名: {self.username}")
                    logger.success("🎉 双重验证通过")
                    return True
                
                # 方法4：检查右上角用户菜单
                user_menu = self.page.ele('#current-user') or self.page.ele('.user-menu')
                if user_menu and self.username.lower() in user_menu.html.lower():
                    logger.success(f"✅ 用户菜单中找到用户名: {self.username}")
                    logger.success("🎉 双重验证通过")
                    return True
                
                logger.warning(f"⚠️ 未找到用户名 {self.username}，继续尝试...")
                time.sleep(2)
                
            except Exception as e:
                logger.warning(f"验证尝试 {attempt+1} 出现异常: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(3)
        
        logger.error(f"❌ 经过 {max_retries} 次尝试，登录验证失败")
        # 截图用于调试（仅GitHub Actions）
        if GITHUB_ACTIONS:
            try:
                self.page.save_screenshot(f'login_failure_{self.site_name}.png')
                logger.info(f"📸 已保存失败截图: login_failure_{self.site_name}.png")
            except:
                pass
        return False

    def login(self, max_retries=2):
        """增强的登录流程 - 带重试机制"""
        self.page.set.cookies([])
        
        for attempt in range(max_retries):
            try:
                logger.info(f"🔐 执行登录 (尝试 {attempt+1}/{max_retries})...")
                
                self.page.get(self.site_config['login_url'])
                time.sleep(3)  # 增加初始等待
                
                # 等待登录表单加载
                self.page.wait.ele_displayed('#login-account-name', timeout=10)
                
                self.handle_cloudflare_check()
                time.sleep(1)
                
                # 清除可能存在的旧数据
                self.page.ele("#login-account-name").clear()
                self.page.ele("#login-account-password").clear()
                time.sleep(0.5)
                
                logger.info("⌨️ 输入用户名...")
                self.page.ele("#login-account-name").input(self.username)
                time.sleep(0.5)  # 增加输入间隔
                
                logger.info("⌨️ 输入密码...")
                self.page.ele("#login-account-password").input(self.password)
                time.sleep(0.5)
                
                logger.info("🔑 点击登录按钮...")
                self.page.ele("#login-button").click()
                time.sleep(8)  # 增加等待时间，给登录更多时间
                
                self.handle_cloudflare_check()
                time.sleep(2)
                
                # 验证登录状态
                if self.verify_login_status():
                    logger.success("✅ 登录成功")
                    self.save_caches()
                    return True
                else:
                    logger.warning(f"⚠️ 登录验证失败，尝试 {attempt+1}/{max_retries}")
                    if attempt < max_retries - 1:
                        time.sleep(5)  # 重试前等待
                
            except Exception as e:
                logger.error(f"❌ 登录过程出错 (尝试 {attempt+1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(5)
        
        logger.error("❌ 所有登录尝试均失败")
        return False

    def ensure_logged_in(self):
        """确保用户已登录"""
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login():
            return True
        
        login_success = self.login()
        if not login_success:
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    def find_topic_elements(self):
        """使用href模式获取主题列表"""
        logger.info("🎯 查找主题...")
        
        try:
            # 等待主题列表加载
            self.page.wait.ele_displayed('#list-area', timeout=10)
            
            all_links = self.page.eles('tag:a')
            topic_links = []
            seen_urls = set()
            
            for link in all_links:
                href = link.attr('href')
                if not href:
                    continue
                
                if '/t/' in href and not any(exclude in href for exclude in ['/tags/', '/c/', '/u/']):
                    if not href.startswith('http'):
                        href = self.site_config['base_url'] + href
                    
                    base_url = re.sub(r'/t/topic/(\d+)(/\d+)?', r'/t/topic/\1', href)
                    
                    if base_url not in seen_urls:
                        seen_urls.add(base_url)
                        topic_links.append(base_url)
            
            logger.info(f"🔗 找到 {len(topic_links)} 个主题")
            return topic_links
            
        except Exception as e:
            logger.error(f"❌ 查找主题失败: {str(e)}")
            # 截图用于调试
            if GITHUB_ACTIONS:
                try:
                    self.page.save_screenshot(f'topic_find_failure_{self.site_name}.png')
                    logger.info(f"📸 已保存失败截图: topic_find_failure_{self.site_name}.png")
                except:
                    pass
            return []

    def browse_topics_hybrid(self):
        """混合架构：主标签页列表 + 子标签页浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0

        try:
            logger.info(f"🌐 开始混合架构浏览 {self.site_name} 主题...")
            
            # 主标签页：获取主题列表
            self.page.get(self.site_config['latest_url'])
            self.apply_evasion_strategy()
            
            topic_urls = self.find_topic_elements()
            if not topic_urls:
                logger.warning("❌ 未找到可浏览的主题")
                return 0
            
            browse_count = min(random.randint(8, 11), len(topic_urls))
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划浏览 {browse_count} 个主题")
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    # 创建子标签页：关键！为每个主题提供独立上下文
                    topic_page = self.browser.new_tab()
                    
                    # 复制cookies保持登录状态
                    topic_page.set.cookies(self.page.cookies())
                    
                    # 访问主题
                    topic_page.get(topic_url)
                    time.sleep(3)  # 增加初始等待
                    
                    # 应用规避策略
                    self.apply_evasion_strategy_to_page(topic_page)
                    
                    # 深度浏览（优化版）
                    self.deep_scroll_browsing_v2(topic_page)
                    
                    # 随机点赞（5%概率）
                    if random.random() < 0.05:
                        logger.info("🎲 尝试随机点赞...")
                        self.click_like_if_available_in_page(topic_page)
                    
                    # 微导航（在子标签页内）
                    if random.random() < 0.15:
                        self.micronavigation_in_page(topic_page)
                    
                    # 关键：关闭前等待确保数据提交
                    time.sleep(random.uniform(3, 5))  # 增加关闭前等待
                    topic_page.close()
                    
                    success_count += 1
                    logger.success(f"✅ 成功浏览主题 {i+1}")
                    
                    # 主题间等待（在主标签页）
                    if i < browse_count - 1:
                        wait_time = random.uniform(25, 40)  # 增加间隔
                        logger.info(f"⏳ 主题间等待 {wait_time:.1f} 秒...")
                        
                        # 返回列表页并等待
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(3)
                        
                        remaining_wait = wait_time - 3
                        while remaining_wait > 0:
                            chunk = min(remaining_wait, random.uniform(8, 12))
                            self.keep_session_active()
                            time.sleep(chunk)
                            remaining_wait -= chunk
                        
                        # 随机休眠（30%概率）
                        if random.random() < 0.3:
                            self.random_sleep()
                            
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    # 确保关闭标签页
                    try:
                        topic_page.close()
                    except:
                        pass
                    continue
            
            logger.success(f"🎉 共成功浏览 {success_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 主题浏览失败: {str(e)}")
            return 0

    def apply_evasion_strategy_to_page(self, page):
        """为指定页面应用规避策略"""
        try:
            # 智能延迟
            base_delay = random.uniform(2, 5)
            time.sleep(base_delay)
            
            # 多样化滚动
            self.varied_scrolling_behavior_in_page(page)
            
            # 人类行为模拟
            self.human_behavior_simulation_in_page(page)
            
        except Exception as e:
            logger.debug(f"规避策略应用异常: {e}")

    def varied_scrolling_behavior_in_page(self, page):
        """在指定页面执行多样化滚动"""
        scroll_patterns = [
            lambda p: p.run_js("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});"),
            lambda p: p.run_js("""
                let currentPosition = 0;
                const scrollHeight = document.body.scrollHeight;
                const scrollStep = scrollHeight / 5;
                
                function scrollStepByStep() {
                    if (currentPosition < scrollHeight) {
                        currentPosition += scrollStep;
                        window.scrollTo(0, currentPosition);
                        setTimeout(scrollStepByStep, 800 + Math.random() * 500);
                    }
                }
                scrollStepByStep();
            """),
        ]
        
        chosen_pattern = random.choice(scroll_patterns)
        chosen_pattern(page)
        time.sleep(random.uniform(3, 6))

    def deep_scroll_browsing_v2(self, page):
        """优化的深度滚动浏览 - 更贴近真实用户"""
        # 随机决定滚动次数（3-7次）
        scroll_count = random.randint(3, 7)
        logger.info(f"📜 计划滚动 {scroll_count} 次")
        
        for i in range(scroll_count):
            # 随机滚动距离（300-800px）
            scroll_distance = random.randint(300, 800)
            page.run_js(f"window.scrollBy(0, {scroll_distance});")
          #  logger.info(f"⬇️ 第{i+1}次滚动: {scroll_distance}px")
            
            # 随机等待（2-5秒）
            wait_time = random.uniform(2, 5)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒...")
            time.sleep(wait_time)
            
            # 检查是否到达底部
            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight - 100"
            )
            if at_bottom:
                logger.success("✅ 已到达页面底部")
                
                # 在底部停留5-8秒（确保已读计数）
                bottom_wait = random.uniform(5, 8)
                logger.info(f"⏳ 在底部停留 {bottom_wait:.1f} 秒...")
                time.sleep(bottom_wait)
                break
            
            # 偶尔触发微交互
            if random.random() < 0.3:
                self.micro_interactions_in_page(page)

    def click_like_if_available_in_page(self, page):
        """在指定页面点赞"""
        try:
            like_button = page.ele('.discourse-reactions-reaction-button:not(.has-reacted)')
            if like_button and like_button.states.is_visible:
                logger.info("👍 找到未点赞的帖子...")
                like_button.scroll.to_see()
                time.sleep(0.5)
                like_button.click()
                time.sleep(1)
                logger.success("✅ 点赞成功")
                return True
        except Exception as e:
            logger.debug(f"点赞失败: {e}")
        return False

    def micronavigation_in_page(self, page):
        """在指定页面执行微导航"""
        try:
            internal_links = page.eles('a[href*="/t/"]')
            if internal_links:
                random_link = random.choice(internal_links)
                link_url = random_link.attr('href')
                if link_url and '/t/' in link_url:
                    logger.info(f"🔗 微导航到: {link_url}")
                    random_link.click()
                    time.sleep(random.uniform(4, 8))
                    page.back()
                    time.sleep(2)
                    logger.info("✅ 微导航完成")
        except Exception as e:
            logger.debug(f"微导航失败: {e}")

    def human_behavior_simulation_in_page(self, page):
        """在指定页面模拟人类行为"""
        try:
            # 随机鼠标移动
            if random.random() < 0.5:
                page.run_js("""
                    document.dispatchEvent(new MouseEvent('mousemove', {
                        bubbles: true,
                        clientX: Math.random() * window.innerWidth,
                        clientY: Math.random() * window.innerHeight
                    }));
                """)
            
            # 随机点击空白处
            if random.random() < 0.3:
                page.run_js("""
                    const elements = document.querySelectorAll('p, div, span');
                    if (elements.length > 0) {
                        elements[Math.floor(Math.random() * elements.length)].click();
                    }
                """)
            
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

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

    def keep_session_active(self):
        """保持会话活跃"""
        try:
            self.page.run_js("window.scrollBy(0, 10);")
            if random.random() < 0.3:
                self.micro_interactions()
        except:
            pass

    def get_connect_info_single_tab(self):
        """单标签页获取连接信息"""
        logger.info("🔗 单标签页获取连接信息...")
        
        try:
            current_url = self.page.url
            
            self.page.get(self.site_config['connect_url'])
            time.sleep(3)
            
            self.apply_evasion_strategy()
            
            table = self.page.ele("tag:table")
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                if self.site_name == 'idcflare':
                    logger.info("ℹ️ idcflare连接信息获取失败，但不影响继续执行")
                self.page.get(current_url)
                time.sleep(2)
                return True
            
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
                self.page.get(self.site_config['latest_url'])
                time.sleep(2)
            except:
                pass
            return False

    def run_complete_process(self):
        """执行完整流程"""
        try:
            logger.info(f"🚀 开始完整处理 {self.site_name}")
            
            # 1. 确保登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
                                    
            # 2. 单标签页连接信息
            connect_success = self.get_connect_info_single_tab()
            if not connect_success and self.site_name != 'idcflare':
                logger.warning(f"⚠️ {self.site_name} 连接信息获取失败")

            # 3. 混合架构主题浏览（关键修复：添加这行代码）
            browse_count = self.browse_topics_hybrid()
            
            # 4. 保存缓存
            self.save_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 浏览 {browse_count} 个主题")
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
    logger.info("🚀 Linux.Do 完整集成版 v2.1 启动")
  
    if GITHUB_ACTIONS:
        logger.info("🎯 GitHub Actions 环境检测")
    
    # 检查扩展
    if TURNSTILE_PATCH_ENABLED and os.path.exists(TURNSTILE_PATCH_PATH):
        logger.info(f"✅ turnstilePatch扩展已加载")
    else:
        logger.warning("⚠️ turnstilePatch扩展未加载")
    
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
    logger.info("=" * 80)
    logger.info("📊 完整执行总结:")
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
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 必需环境变量未设置: {', '.join(missing_vars)}")
    
    if not OCR_API_KEY:
        logger.warning("⚠️ 未配置OCR_API_KEY，验证码处理将不可用")
    
    main()




