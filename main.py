#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整版：集成验证规避策略 + 单标签页连接信息 + 反检测功能
双重验证机制 + 智能会话管理 + 深度行为模拟
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
        'private_topic_url': 'https://linux.do/t/topic/2362',
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

# GitHub Actions 环境优化配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
TURNSTILE_PATCH_ENABLED = os.environ.get("TURNSTILE_PATCH_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
OCR_API_KEY = os.getenv("OCR_API_KEY")

# GitHub Actions 特定优化
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# ======================== 扩展路径配置 ========================
TURNSTILE_PATCH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turnstilePatch")

# ======================== 缓存管理器 ========================
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
        self.cache_saved = False
        self.session_start_time = time.time()
        self.request_count = 0
        self.browsing_active = True
        self.initialize_browser()

    def initialize_browser(self):
        """GitHub Actions 优化版浏览器初始化"""
        try:
            co = ChromiumOptions()
            
            # GitHub Actions 环境特殊配置
            if GITHUB_ACTIONS:
                logger.info("🎯 GitHub Actions 环境优化配置")
                # 在CI环境中强制无头模式
                co.headless(True)
                # 减少内存使用
                co.set_argument("--disable-dev-shm-usage")
                co.set_argument("--disable-gpu")
                co.set_argument("--no-sandbox")
                co.set_argument("--disable-software-rasterizer")
                co.set_argument("--disable-background-timer-throttling")
                co.set_argument("--disable-backgrounding-occluded-windows")
                co.set_argument("--disable-renderer-backgrounding")
            else:
                if HEADLESS:
                    co.headless(True)
                else:
                    co.headless(False)
                
            co.incognito(True)
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            
            # 反检测配置
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
        
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 执行GitHub Actions优化版指纹优化
            self.enhance_github_actions_fingerprint()
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
        
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def enhance_github_actions_fingerprint(self):
        """GitHub Actions 环境专用指纹优化"""
        try:
            self.page.run_js("""
                // GitHub Actions 环境专用指纹优化
                Object.defineProperties(navigator, {
                    webdriver: { get: () => undefined },
                    language: { get: () => 'zh-CN' },
                    languages: { get: () => ['zh-CN', 'zh', 'en'] },
                    platform: { get: () => 'Win32' },
                    hardwareConcurrency: { get: () => 2 },  // GitHub Actions通常2核
                    deviceMemory: { get: () => 4 },         // 适中的内存配置
                    
                    plugins: {
                        get: () => [
                            { 
                                name: 'Chrome PDF Plugin', 
                                filename: 'internal-pdf-viewer'
                            },
                            { 
                                name: 'Chrome PDF Viewer', 
                                filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'
                            }
                        ]
                    }
                });

                // 修改屏幕属性
                Object.defineProperty(screen, 'width', { get: () => 1920 });
                Object.defineProperty(screen, 'height', { get: () => 1080 });
                Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
                
                // 移除自动化特征
                Object.defineProperty(window, 'chrome', {
                    value: {
                        runtime: {},
                    },
                });
                
                // 简化的随机交互
                let interactionCount = 0;
                const maxInteractions = 8; // 限制交互次数
                
                document.addEventListener('DOMContentLoaded', function() {
                    setInterval(() => {
                        if (interactionCount < maxInteractions) {
                            document.dispatchEvent(new MouseEvent('mousemove', {
                                bubbles: true,
                                clientX: Math.random() * window.innerWidth,
                                clientY: Math.random() * window.innerHeight
                            }));
                            interactionCount++;
                        }
                    }, 25000 + Math.random() * 15000); // 增加间隔时间
                });
            """)
            logger.debug("✅ GitHub Actions指纹优化已应用")
        except Exception as e:
            logger.debug(f"指纹优化异常: {str(e)}")

    def enhanced_cloudflare_evasion(self):
        """增强的Cloudflare规避策略"""
        logger.debug("🛡️ 应用Cloudflare规避策略")
        
        # 1. 智能延迟系统
        self.smart_delay_system()
        
        # 2. 多样化滚动模式
        self.varied_scrolling_behavior()
        
        # 3. 人类行为模拟
        self.human_behavior_simulation()
        
        # 4. 会话健康监控
        self.session_health_monitoring()

    def smart_delay_system(self):
        """智能延迟系统"""
        base_delay = random.uniform(2, 5)
        
        # 根据请求频率调整延迟
        request_density = self.request_count / (time.time() - self.session_start_time + 1)
        if request_density > 0.5:  # 请求过于密集
            base_delay *= random.uniform(1.5, 3.0)
            logger.debug("📊 检测到密集请求，增加延迟")
        
        # 添加随机扰动
        jitter = random.uniform(0.8, 1.2)
        final_delay = base_delay * jitter
        
        time.sleep(final_delay)
        self.request_count += 1

    def varied_scrolling_behavior(self):
        """多样化滚动行为"""
        scroll_patterns = [
            # 模式1: 平滑滚动到底部
            lambda: self.page.run_js("""
                window.scrollTo({
                    top: document.body.scrollHeight,
                    behavior: 'smooth'
                });
            """),
            
            # 模式2: 分段滚动
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
            
            # 模式3: 随机探索式滚动
            lambda: self.page.run_js("""
                const scrollPositions = [
                    window.innerHeight * 0.3,
                    window.innerHeight * 1.2, 
                    window.innerHeight * 2.5,
                    document.body.scrollHeight * 0.6
                ];
                
                scrollPositions.forEach((pos, index) => {
                    setTimeout(() => {
                        window.scrollTo({
                            top: pos,
                            behavior: 'smooth'
                        });
                    }, index * 1200 + Math.random() * 800);
                });
            """)
        ]
        
        # 随机选择滚动模式
        chosen_pattern = random.choice(scroll_patterns)
        chosen_pattern()
        
        # 滚动后等待时间也随机化
        post_scroll_wait = random.uniform(3, 8)
        time.sleep(post_scroll_wait)

    def human_behavior_simulation(self):
        """人类行为模拟"""
        behaviors = [
            self.micro_interactions,
            self.focus_switching,
            self.reading_pattern_simulation,
            self.mouse_movement_emulation
        ]
        
        # 每次执行1-2个随机行为
        num_behaviors = random.randint(1, 2)
        selected_behaviors = random.sample(behaviors, num_behaviors)
        
        for behavior in selected_behaviors:
            behavior()

    def micro_interactions(self):
        """微交互"""
        try:
            self.page.run_js("""
                // 随机点击非交互元素
                const nonInteractiveElements = document.querySelectorAll('p, div, span');
                if (nonInteractiveElements.length > 0) {
                    const randomElement = nonInteractiveElements[Math.floor(Math.random() * nonInteractiveElements.length)];
                    randomElement.click();
                }
                
                // 轻微鼠标移动
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
                // 切换焦点
                if (document.activeElement) {
                    document.activeElement.blur();
                }
                
                // 随机Tab键导航
                document.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Tab',
                    keyCode: 9,
                    which: 9,
                    bubbles: true
                }));
            """)
            time.sleep(random.uniform(0.3, 1.0))
        except:
            pass

    def reading_pattern_simulation(self):
        """阅读模式模拟"""
        try:
            # 模拟阅读时的暂停和继续
            read_pauses = random.randint(2, 4)
            for _ in range(read_pauses):
                pause_duration = random.uniform(2, 5)
                time.sleep(pause_duration)
                
                # 轻微滚动表示继续阅读
                self.page.run_js("window.scrollBy(0, 100);")
        except:
            pass

    def mouse_movement_emulation(self):
        """鼠标移动模拟"""
        try:
            self.page.run_js("""
                // 生成人类般的鼠标移动轨迹
                function generateHumanMousePath(startX, startY, endX, endY, steps) {
                    const path = [];
                    for (let i = 0; i <= steps; i++) {
                        const t = i / steps;
                        // 贝塞尔曲线添加自然抖动
                        const x = startX + (endX - startX) * t + Math.sin(t * Math.PI * 4) * 10;
                        const y = startY + (endY - startY) * t + Math.cos(t * Math.PI * 3) * 8;
                        path.push({x, y});
                    }
                    return path;
                }
                
                const startX = Math.random() * window.innerWidth;
                const startY = Math.random() * window.innerHeight;
                const endX = Math.random() * window.innerWidth;
                const endY = Math.random() * window.innerHeight;
                
                const mousePath = generateHumanMousePath(startX, startY, endX, endY, 10);
                
                mousePath.forEach((point, index) => {
                    setTimeout(() => {
                        document.dispatchEvent(new MouseEvent('mousemove', {
                            bubbles: true,
                            clientX: point.x,
                            clientY: point.y
                        }));
                    }, index * 50);
                });
            """)
        except:
            pass

    def session_health_monitoring(self):
        """会话健康监控"""
        try:
            current_time = time.time()
            session_duration = current_time - self.session_start_time
            
            # 长时间运行后主动刷新
            if session_duration > 1800:  # 30分钟
                logger.info("🔄 长时间运行，主动刷新会话")
                self.page.refresh()
                time.sleep(5)
                self.session_start_time = current_time
                self.request_count = 0
                
            # 检测可能的验证页面
            page_title = self.page.title.lower()
            suspicious_indicators = ["checking", "verifying", "just a moment", "please wait"]
            
            if any(indicator in page_title for indicator in suspicious_indicators):
                logger.warning("⚠️ 检测到可能验证页面，执行规避")
                self.evasive_maneuvers()
                
        except Exception as e:
            logger.debug(f"会话监控异常: {e}")

    def evasive_maneuvers(self):
        """规避操作"""
        try:
            # 策略1: 后退并等待
            self.page.back()
            time.sleep(random.uniform(8, 15))
            
            # 策略2: 刷新页面
            self.page.refresh()
            time.sleep(random.uniform(5, 10))
            
            # 策略3: 访问其他页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(random.uniform(3, 7))
            
        except Exception as e:
            logger.warning(f"规避操作失败: {e}")

    def is_captcha_page(self):
        """检查验证码页面"""
        captcha_img = self.page.ele('img[src*="challenge"]') or self.page.ele('img[src*="captcha"]')
        captcha_input = self.page.ele('input[name="cf_captcha_answer"]') or self.page.ele('input[type="text"]@@placeholder*=captcha', timeout=1)
        return captcha_img and captcha_input

    def handle_captcha_challenge(self):
        """处理验证码挑战"""
        try:
            captcha_img = self.page.ele('img[src*="challenge"]') or self.page.ele('img[src*="captcha"]')
            if not captcha_img:
                return False

            img_src = captcha_img.attr('src')

            if img_src.startswith('data:image'):
                base64_data = img_src
            else:
                if not img_src.startswith('http'):
                    img_src = self.site_config['base_url'] + img_src
                response = requests.get(img_src)
                if response.status_code != 200:
                    return False
                base64_data = "data:image/png;base64," + base64.b64encode(response.content).decode('utf-8')

            if not OCR_API_KEY:
                logger.error("❌ 未设置OCR_API_KEY")
                return False

            ocr_result = self.call_ocr_space_api(base64_data, OCR_API_KEY)
            if not ocr_result:
                return False

            captcha_input = self.page.ele('input[name="cf_captcha_answer"]') or self.page.ele('input[type="text"]@@placeholder*=captcha')
            if not captcha_input:
                return False

            captcha_input.input(ocr_result)
            time.sleep(0.5)

            submit_btn = self.page.ele('button[type="submit"]') or self.page.ele('input[type="submit"]')
            if not submit_btn:
                return False

            submit_btn.click()
            logger.info("✅ 已提交验证码")
            return True

        except Exception as e:
            logger.error(f"❌ 验证码处理失败: {str(e)}")
            return False

    def call_ocr_space_api(self, base64_image, api_key, retries=2):
        """OCR API调用（GitHub Actions优化）"""
        for attempt in range(retries):
            try:
                url = "https://api.ocr.space/parse/image"
                payload = {
                    "apikey": api_key,
                    "base64Image": base64_image,
                    "language": "eng",
                    "OCREngine": "2",
                }

                response = requests.post(url, data=payload, timeout=20)
                result = response.json()

                if result.get("IsErroredOnProcessing"):
                    continue

                parsed_results = result.get("ParsedResults", [])
                if parsed_results:
                    parsed_text = parsed_results[0].get("ParsedText", "").strip()
                    if parsed_text:
                        logger.info(f"🔍 OCR识别: {parsed_text}")
                        return parsed_text

            except Exception as e:
                logger.warning(f"⚠️ OCR尝试{attempt+1}失败: {str(e)}")

            if attempt < retries - 1:
                time.sleep(3)

        return None

    def handle_cloudflare_quick_check(self, timeout=10):
        """快速Cloudflare检查"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                page_title = self.page.title
                
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    if self.is_captcha_page():
                        logger.info("🛡️ 检测到验证码挑战")
                        if self.handle_captcha_challenge():
                            time.sleep(2)
                            continue
                        else:
                            return False
                    else:
                        return True
                
                if self.is_captcha_page():
                    logger.info("🛡️ 检测到验证码挑战")
                    if self.handle_captcha_challenge():
                        time.sleep(2)
                        continue
                    else:
                        return False
                
                time.sleep(1)
                    
            except Exception as e:
                logger.debug(f"快速Cloudflare检查异常: {str(e)}")
                time.sleep(1)
        
        logger.warning("⚠️ 快速Cloudflare检查超时，继续执行")
        return True

    def save_caches(self):
        """保存缓存"""
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
            time.sleep(1)
            
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            self.page.refresh()
            time.sleep(2)
            
            self.handle_cloudflare_quick_check()
            
            if self.verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def verify_login_status(self):
        """双重验证机制"""
        logger.info("🔍 验证登录状态...")
        
        try:
            private_url = self.site_config['private_topic_url']
            logger.info(f"📍 访问私有主题: {private_url}")
            self.page.get(private_url)
            time.sleep(2)
            
            self.handle_cloudflare_quick_check()
            time.sleep(1)
            
            page_content = self.page.html
            page_title = self.page.title
            
            logger.info(f"📄 页面标题: {page_title}")
            
            if "Page Not Found" in page_content or "页面不存在" in page_content:
                logger.error("❌ 私有主题访问失败")
                return False
            
            logger.success("✅ 私有主题访问成功")
            
            if self.username.lower() in page_content.lower():
                logger.success(f"✅ 找到用户名: {self.username}")
                logger.success("🎉 双重验证通过")
                return True
            else:
                logger.error(f"❌ 未找到用户名: {self.username}")
                return False
            
        except Exception as e:
            logger.error(f"❌ 登录验证异常: {str(e)}")
            return False

    def login(self):
        """执行登录流程"""
        self.page.set.cookies([])
        logger.info("🔐 执行登录...")
        
        self.page.get(self.site_config['login_url'])
        time.sleep(2)
        
        self.handle_cloudflare_quick_check()
        time.sleep(1)
        
        try:
            time.sleep(1)
            
            username_field = self.page.ele("#login-account-name")
            if not username_field:
                logger.error("❌ 找不到用户名字段")
                return False
            
            logger.info("⌨️ 输入用户名...")
            username_field.input(self.username)
            time.sleep(0.3)
            
            password_field = self.page.ele("#login-account-password")
            if not password_field:
                logger.error("❌ 找不到密码字段")
                return False
            
            logger.info("⌨️ 输入密码...")
            password_field.input(self.password)
            time.sleep(0.3)
            
            login_button = self.page.ele("#login-button")
            if not login_button:
                logger.error("❌ 找不到登录按钮")
                return False
            
            logger.info("🔑 点击登录按钮...")
            login_button.click()
            time.sleep(5)
            
            self.handle_cloudflare_quick_check()
            time.sleep(2)
            
            if self.verify_login_status():
                logger.success("✅ 登录成功")
                self.save_caches()
                return True
            else:
                logger.error("❌ 登录失败")
                return False
                
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
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
            return []

    def browse_topics_enhanced(self):
        """增强的主题浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0

        try:
            logger.info(f"🌐 开始增强浏览 {self.site_name} 主题...")
            
            self.page.get(self.site_config['latest_url'])
            self.enhanced_cloudflare_evasion()  # 应用规避策略
            
            topic_urls = self.find_topic_elements()
            if not topic_urls:
                return 0
            
            browse_count = min(random.randint(2, 3), len(topic_urls))
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划浏览 {browse_count} 个主题")
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    self.page.get(topic_url)
                    self.enhanced_cloudflare_evasion()  # 每次页面跳转都应用
                    
                    # 增强的浏览行为
                    self.enhanced_browsing_behavior()
                    
                    success_count += 1
                    logger.info(f"✅ 成功浏览主题 {i+1}")
                    
                    # 主题间等待 + 规避策略
                    if i < browse_count - 1:
                        wait_time = random.uniform(25, 40)
                        logger.info(f"⏳ 等待 {wait_time:.1f} 秒...")
                        
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(3)
                        
                        # 在等待期间执行轻度规避行为
                        remaining_wait = wait_time - 3
                        while remaining_wait > 0:
                            chunk = min(remaining_wait, random.uniform(5, 10))
                            self.light_evasion_behavior()
                            time.sleep(chunk)
                            remaining_wait -= chunk
                            
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 增强浏览失败: {str(e)}")
            return 0

    def enhanced_browsing_behavior(self):
        """增强的浏览行为"""
        # 随机浏览深度
        browse_depth = random.choice(["shallow", "medium", "deep"])
        
        if browse_depth == "shallow":
            scroll_count = random.randint(2, 4)
        elif browse_depth == "medium":
            scroll_count = random.randint(4, 6)
        else:  # deep
            scroll_count = random.randint(6, 8)
        
        for i in range(scroll_count):
            self.varied_scrolling_behavior()
            
            # 滚动间随机行为
            if random.random() < 0.4:
                self.human_behavior_simulation()

    def light_evasion_behavior(self):
        """轻度规避行为"""
        try:
            # 轻微活动保持会话
            self.page.run_js("window.scrollBy(0, 10);")
            
            if random.random() < 0.3:
                self.micro_interactions()
                
        except:
            pass

    def print_connect_info_single_tab(self):
        """单标签页获取连接信息"""
        logger.info("🔗 单标签页获取连接信息...")
        
        try:
            # 保存当前URL以便返回
            current_url = self.page.url
            
            # 访问连接信息页面
            self.page.get(self.site_config['connect_url'])
            time.sleep(3)
            
            # 应用规避策略
            self.enhanced_cloudflare_evasion()
            
            # 查找表格
            table = self.page.ele("tag:table")
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                # 返回原页面
                self.page.get(current_url)
                time.sleep(2)
                return
            
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
                print("="*60 + "\n")
                
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
                total = len(info)
                logger.success(f"📈 统计: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 返回原页面
            self.page.get(current_url)
            time.sleep(2)
            
            logger.info("✅ 连接信息获取完成")
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")
            # 尝试恢复会话
            try:
                self.page.get(self.site_config['latest_url'])
                time.sleep(2)
            except:
                pass

    def run_enhanced(self):
        """增强的执行流程"""
        try:
            logger.info(f"🚀 开始增强处理 {self.site_name}")
            
            # 1. 确保登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 2. 增强主题浏览
            browse_count = self.browse_topics_enhanced()
            
            # 3. 单标签页连接信息
            self.print_connect_info_single_tab()
            
            # 4. 保存缓存
            self.save_caches()
            
            logger.success(f"✅ {self.site_name} 增强处理完成 - 浏览 {browse_count} 个主题")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
            
        finally:
            self.browsing_active = False
            try:
                if self.page:
                    self.page.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    logger.info("🚀 Linux.Do 增强规避策略版启动")
    logger.info("=" * 80)
    
    if GITHUB_ACTIONS:
        logger.info("🎯 检测到GitHub Actions环境，应用优化配置")
    
    # 检查扩展
    if TURNSTILE_PATCH_ENABLED and os.path.exists(TURNSTILE_PATCH_PATH):
        logger.info(f"✅ turnstilePatch扩展已配置")
    else:
        logger.warning("⚠️ turnstilePatch扩展未加载")
    
    logger.info("🛡️ 启用增强Cloudflare规避策略")
    logger.info("🔗 使用单标签页获取连接信息")
    
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    
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

        logger.info("-" * 80)
        logger.info(f"🔧 初始化 {site_name}")
        
        try:
            browser = LinuxDoBrowser(site_config, credentials)
            success = browser.run_enhanced()

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
    logger.info("📊 增强规避策略执行总结:")
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
    
    main()
