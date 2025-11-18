#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# 集成turnstilePatch扩展和反检测功能
# 保持双重验证机制（私有主题访问+用户名确认）
# 使用浏览器上下文保持更持久的会话,
# 主题浏览: 在主标签页打开最新页面，并保持这个标签页不动，循环中：新开标签页打开主题URL -> 在新标签页中浏览 -> 关闭新标签页,使用了href模式获取主题列表
# 连接信息: 新标签页,使用 tabulate 库美化表格显示,使用选择器 'tag:table' 找到表格，,在idcflare上失败不影响                                
# 登录成功时保存缓存，登录失败时清除对应站点缓存，避免盲目清除所有缓存                                         
# 深度滚动浏览，交互事件触发，模拟真实的阅读行为，确保网站正确收集浏览记录    
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

# 环境变量配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
TURNSTILE_PATCH_ENABLED = os.environ.get("TURNSTILE_PATCH_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
OCR_API_KEY = os.getenv("OCR_API_KEY")

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
        self.main_tab = None
        self.cache_saved = False
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器 - 集成反检测和指纹优化，加载turnstilePatch扩展"""
        try:
            co = ChromiumOptions()
            
            # 基础配置
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
            co.set_argument("--disable-background-timer-throttling")
            co.set_argument("--disable-backgrounding-occluded-windows")
            co.set_argument("--disable-renderer-backgrounding")
            co.set_argument("--disable-web-security")
            co.set_argument("--disable-features=TranslateUI")
            co.set_argument("--disable-ipc-flooding-protection")
            co.set_argument("--no-default-browser-check")
            co.set_argument("--disable-component-extensions-with-background-pages")
            co.set_argument("--disable-default-apps")
            co.set_argument("--disable-extensions")
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
                # 检查扩展是否有效
                if os.path.exists(os.path.join(TURNSTILE_PATCH_PATH, "manifest.json")):
                    logger.info("✅ turnstilePatch扩展完整")
                else:
                    logger.warning("⚠️ turnstilePatch扩展可能不完整")
            else:
                logger.warning(f"⚠️ 未加载turnstilePatch扩展，路径存在: {os.path.exists(TURNSTILE_PATCH_PATH)}")
        
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 执行增强版指纹优化
            self.enhance_github_actions_fingerprint()
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
        
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def enhance_github_actions_fingerprint(self):
        """增强版指纹优化"""
        try:
            self.page.run_js("""
                // 深度修改 navigator 属性
                Object.defineProperties(navigator, {
                    webdriver: { get: () => undefined },
                    language: { get: () => 'zh-CN' },
                    languages: { get: () => ['zh-CN', 'zh', 'en'] },
                    platform: { get: () => 'Win32' },
                    hardwareConcurrency: { get: () => 8 },
                    deviceMemory: { get: () => 16 },
                    
                    // 修改插件信息 - 更真实的插件列表
                    plugins: {
                        get: () => [
                            { 
                                name: 'Chrome PDF Plugin', 
                                filename: 'internal-pdf-viewer',
                                description: 'Portable Document Format'
                            },
                            { 
                                name: 'Chrome PDF Viewer', 
                                filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                                description: 'Portable Document Format'
                            },
                            { 
                                name: 'Native Client', 
                                filename: 'internal-nacl-plugin',
                                description: 'Native Client Executable'
                            }
                        ]
                    },
                    
                    // 添加更多属性
                    userAgent: {
                        get: () => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                });

                // 修改屏幕属性
                Object.defineProperty(screen, 'width', { get: () => 1920 });
                Object.defineProperty(screen, 'height', { get: () => 1080 });
                Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
                Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
                
                // Canvas 指纹伪装
                const originalGetContext = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(contextType, ...args) {
                    const context = originalGetContext.call(this, contextType, ...args);
                    if (contextType === '2d') {
                        const originalFillText = context.fillText;
                        context.fillText = function(...fillTextArgs) {
                            // 微调文本渲染，增加随机性
                            if (fillTextArgs.length > 3) {
                                fillTextArgs[3] = fillTextArgs[3] + Math.random() * 0.1 - 0.05;
                            }
                            return originalFillText.apply(this, fillTextArgs);
                        };
                    }
                    return context;
                };

                // WebGL 指纹伪装
                const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) {
                        return 'Google Inc. (Intel)';
                    }
                    if (parameter === 37446) {
                        return 'Intel Iris OpenGL Engine';
                    }
                    return originalGetParameter.call(this, parameter);
                };

                // 移除自动化特征
                Object.defineProperty(window, 'chrome', {
                    value: {
                        runtime: {},
                        loadTimes: () => {},
                        csi: () => {},
                        app: {}
                    },
                });
                
                // 覆盖权限
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // 覆盖时区和语言
                Object.defineProperty(Intl, 'DateTimeFormat', {
                    value: class extends Intl.DateTimeFormat {
                        constructor(locales, options) {
                            super(locales || ['zh-CN', 'zh', 'en-US'], options);
                        }
                    }
                });

                // 添加更频繁的随机交互
                document.addEventListener('DOMContentLoaded', function() {
                    // 更频繁的鼠标移动
                    setInterval(() => {
                        ['mousemove', 'mouseover', 'mousedown', 'mouseup'].forEach(eventType => {
                            document.dispatchEvent(new MouseEvent(eventType, {
                                bubbles: true,
                                cancelable: true,
                                clientX: Math.random() * window.innerWidth,
                                clientY: Math.random() * window.innerHeight
                            }));
                        });
                    }, 5000 + Math.random() * 10000);
                    
                    // 随机键盘事件
                    setInterval(() => {
                        document.dispatchEvent(new KeyboardEvent('keydown', { 
                            key: ' ', 
                            bubbles: true 
                        }));
                    }, 8000 + Math.random() * 12000);
                });

                // 覆盖连接属性
                Object.defineProperty(navigator, 'connection', {
                    value: {
                        downlink: 10,
                        effectiveType: "4g",
                        rtt: 50,
                        saveData: false
                    }
                });
            """)
            logger.debug("✅ 增强版指纹优化脚本已应用")
        except Exception as e:
            logger.debug(f"指纹优化异常: {str(e)}")

    def handle_cloudflare(self, timeout=30):
        """处理Cloudflare验证，包括验证码挑战"""
        start_time = time.time()
        logger.info("🛡️ 处理Cloudflare验证")
        
        while time.time() - start_time < timeout:
            try:
                page_title = self.page.title
                page_content = self.page.html
                
                # 如果页面标题不包含等待信息，并且没有验证码挑战，则认为通过
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    # 检查是否有验证码挑战
                    if self.is_captcha_page():
                        logger.info("🛡️ 检测到验证码挑战，尝试处理...")
                        if self.handle_captcha_challenge():
                            # 处理完验证码后，继续等待，因为提交验证码后可能还有挑战
                            time.sleep(5)
                            continue
                        else:
                            logger.error("❌ 验证码处理失败")
                            return False
                    else:
                        logger.success("✅ Cloudflare验证通过")
                        return True
                
                # 如果页面是验证码挑战，直接处理
                if self.is_captcha_page():
                    logger.info("🛡️ 检测到验证码挑战，尝试处理...")
                    if self.handle_captcha_challenge():
                        time.sleep(5)
                        continue
                    else:
                        logger.error("❌ 验证码处理失败")
                        return False
                
                wait_time = random.uniform(2, 4)
                logger.debug(f"⏳ 等待验证 {wait_time:.1f}秒")
                time.sleep(wait_time)
                    
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(2)
        
        logger.warning("⚠️ Cloudflare处理超时，继续执行")
        return True

    def is_captcha_page(self):
        """检查当前页面是否是验证码挑战页面"""
        # 检查是否有验证码图片和输入框
        captcha_img = self.page.ele('img[src*="challenge"]') or self.page.ele('img[src*="captcha"]')
        captcha_input = self.page.ele('input[name="cf_captcha_answer"]') or self.page.ele('input[type="text"]@@placeholder*=captcha', timeout=2)
        
        return captcha_img and captcha_input

    def handle_captcha_challenge(self):
        """处理验证码挑战"""
        try:
            # 获取验证码图片
            captcha_img = self.page.ele('img[src*="challenge"]') or self.page.ele('img[src*="captcha"]')
            if not captcha_img:
                logger.error("❌ 找不到验证码图片")
                return False

            # 获取图片的src属性
            img_src = captcha_img.attr('src')

            # 如果src是base64数据，直接使用；如果是URL，则下载
            if img_src.startswith('data:image'):
                base64_data = img_src
            else:
                # 如果是相对路径，补全URL
                if not img_src.startswith('http'):
                    img_src = self.site_config['base_url'] + img_src
                # 下载图片并转换为base64
                response = requests.get(img_src)
                if response.status_code != 200:
                    logger.error("❌ 下载验证码图片失败")
                    return False
                base64_data = "data:image/png;base64," + base64.b64encode(response.content).decode('utf-8')

            # 调用OCR.space API
            if not OCR_API_KEY:
                logger.error("❌ 未设置OCR_API_KEY环境变量")
                return False

            ocr_result = self.call_ocr_space_api(base64_data, OCR_API_KEY)
            if not ocr_result:
                logger.error("❌ OCR识别失败")
                return False

            # 填写验证码
            captcha_input = self.page.ele('input[name="cf_captcha_answer"]') or self.page.ele('input[type="text"]@@placeholder*=captcha')
            if not captcha_input:
                logger.error("❌ 找不到验证码输入框")
                return False

            captcha_input.input(ocr_result)
            time.sleep(1)

            # 提交验证码
            submit_btn = self.page.ele('button[type="submit"]') or self.page.ele('input[type="submit"]')
            if not submit_btn:
                logger.error("❌ 找不到提交按钮")
                return False

            submit_btn.click()
            logger.info("✅ 已提交验证码")
            return True

        except Exception as e:
            logger.error(f"❌ 处理验证码挑战时出错: {str(e)}")
            return False

    def call_ocr_space_api(self, base64_image, api_key, retries=3):
        """
        调用OCR.Space API识别验证码
        """
        for attempt in range(retries):
            try:
                url = "https://api.ocr.space/parse/image"
                payload = {
                    "apikey": api_key,
                    "base64Image": base64_image,
                    "language": "eng",
                    "OCREngine": "2",
                }

                response = requests.post(url, data=payload, timeout=30)
                result = response.json()

                if result.get("IsErroredOnProcessing"):
                    error_msg = result.get("ErrorMessage", "Unknown error")
                    logger.warning(f"⚠️ OCR API 错误: {error_msg}")
                    continue

                parsed_results = result.get("ParsedResults", [])
                if parsed_results:
                    parsed_text = parsed_results[0].get("ParsedText", "").strip()
                    if parsed_text:
                        logger.info(f"🔍 OCR 识别结果: {parsed_text}")
                        return parsed_text

                logger.warning(f"⚠️ 第 {attempt + 1} 次OCR尝试未识别出文本")

            except Exception as e:
                logger.warning(f"⚠️ 第 {attempt + 1} 次OCR尝试失败: {str(e)}")

            if attempt < retries - 1:
                wait_time = (attempt + 1) * 5
                logger.info(f"⏳ {wait_time}秒后重试OCR...")
                time.sleep(wait_time)

        return None

    def save_caches(self):
        """保存缓存"""
        if self.cache_saved:
            return
            
        try:
            # 保存cookies
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info(f"✅ 保存 {len(cookies)} 个Cookies")
            
            # 保存会话数据
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
        """尝试使用缓存登录"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录，跳过缓存")
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            logger.warning("⚠️ 无有效缓存Cookies")
            return False
        
        try:
            logger.info("🎯 尝试缓存登录...")
            
            self.page.get(self.site_config['base_url'])
            time.sleep(2)
            
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            self.page.refresh()
            time.sleep(2)
            
            self.handle_cloudflare()
            
            if self.verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def verify_login_status(self):
        """验证登录状态 - 双重验证机制"""
        logger.info("🔍 验证登录状态...")
        
        try:
            # 第一重验证：访问私有主题
            private_url = self.site_config['private_topic_url']
            logger.info(f"📍 访问私有主题: {private_url}")
            self.page.get(private_url)
            time.sleep(3)
            
            self.handle_cloudflare()
            time.sleep(2)
            
            page_content = self.page.html
            page_title = self.page.title
            
            logger.info(f"📄 页面标题: {page_title}")
            
            # 检查是否有错误提示
            if "Page Not Found" in page_content or "页面不存在" in page_content:
                logger.error("❌ 私有主题访问失败")
                return False
            
            logger.success("✅ 私有主题访问成功")
            
            # 第二重验证：验证用户名存在
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
        
        self.handle_cloudflare()
        time.sleep(2)
        
        try:
            # 等待表单元素出现
            time.sleep(2)
            
            # 查找并填写用户名
            username_field = self.page.ele("#login-account-name")
            if not username_field:
                logger.error("❌ 找不到用户名字段")
                return False
            
            logger.info("⌨️ 输入用户名...")
            username_field.input(self.username)
            time.sleep(random.uniform(0.5, 1))
            
            # 查找并填写密码
            password_field = self.page.ele("#login-account-password")
            if not password_field:
                logger.error("❌ 找不到密码字段")
                return False
            
            logger.info("⌨️ 输入密码...")
            password_field.input(self.password)
            time.sleep(random.uniform(0.5, 1))
            
            # 点击登录按钮
            login_button = self.page.ele("#login-button")
            if not login_button:
                logger.error("❌ 找不到登录按钮")
                return False
            
            logger.info("🔑 点击登录按钮...")
            login_button.click()
            time.sleep(8)
            
            self.handle_cloudflare()
            time.sleep(3)
            
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
        # 尝试缓存登录
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login():
            return True
        
        # 执行手动登录
        login_success = self.login()
        if not login_success:
            # 登录失败时清除缓存
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    def find_topic_elements(self):
        """主题元素查找 - 使用href模式"""
        logger.info("🎯 查找主题...")
        
        try:
            all_links = self.page.eles('tag:a')
            topic_links = []
            seen_urls = set()
            
            for link in all_links:
                href = link.attr('href')
                if not href:
                    continue
                
                # 使用href模式过滤主题链接
                if '/t/' in href and not any(exclude in href for exclude in ['/tags/', '/c/', '/u/']):
                    # 确保URL完整
                    if not href.startswith('http'):
                        href = self.site_config['base_url'] + href
                    
                    # 去重：提取基础主题URL
                    base_url = re.sub(r'/t/topic/(\d+)(/\d+)?', r'/t/topic/\1', href)
                    
                    if base_url not in seen_urls:
                        seen_urls.add(base_url)
                        topic_links.append(base_url)
            
            logger.info(f"🔗 找到 {len(topic_links)} 个主题")
            return topic_links
            
        except Exception as e:
            logger.error(f"❌ 查找主题失败: {str(e)}")
            return []

    def browse_topics_optimized(self):
        """优化版主题浏览 - 多标签页策略 + 持久会话"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        # 浏览前验证登录状态
        if not self.verify_login_status():
            logger.error("❌ 浏览前验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始优化浏览 {self.site_name} 主题...")
            
            # 主标签页：保持最新页面作为会话锚点
            self.main_tab = self.page
            self.main_tab.get(self.site_config['latest_url'])
            time.sleep(5)
            
            self.handle_cloudflare()
            time.sleep(3)
            
            # 在主标签页查找主题（避免频繁跳转）
            topic_urls = self.find_topic_elements()
            if not topic_urls:
                logger.error("❌ 无法找到主题")
                return 0
            
            logger.info(f"📚 发现 {len(topic_urls)} 个主题")
            
            # 减少浏览数量，增加随机性
            browse_count = min(random.randint(2, 3), len(topic_urls))
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划浏览 {browse_count} 个主题")
            
            # 记录主标签页的cookies用于新标签页
            main_cookies = self.main_tab.cookies()
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    # 🔄 关键改进：在新标签页打开主题，保持主标签页会话
                    new_tab = self.main_tab.new_tab()
                    
                    # 在新标签页设置相同的cookies
                    new_tab.set.cookies(main_cookies)
                    
                    # 在新标签页访问主题
                    new_tab.get(topic_url)
                    time.sleep(5)
                    
                    # 在新标签页处理 Cloudflare
                    original_page = self.page
                    self.page = new_tab  # 临时切换到新标签页
                    cloudflare_passed = self.handle_cloudflare(timeout=20)
                    self.page = original_page  # 切换回主标签页
                    
                    if not cloudflare_passed:
                        logger.warning(f"⚠️ 主题 {i+1} Cloudflare验证失败，跳过")
                        new_tab.close()
                        continue
                    
                    time.sleep(3)
                    
                    # 在新标签页进行深度浏览
                    self.page = new_tab
                    self.enhanced_deep_scroll()
                    self.page = original_page
                    
                    success_count += 1
                    logger.info(f"✅ 成功浏览主题 {i+1}")
                    
                    # 关闭主题标签页，回到主标签页
                    new_tab.close()
                    
                    # 主题间等待（保持主标签页活跃）
                    if i < browse_count - 1:
                        wait_time = random.uniform(30, 60)  # 更长的等待时间
                        logger.info(f"⏳ 等待 {wait_time:.1f} 秒维持会话...")
                        self.keep_main_tab_active(wait_time)
                            
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    # 确保回到主标签页
                    self.page = self.main_tab
                    continue
            
            logger.success(f"✅ 浏览完成: {success_count}/{browse_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def keep_main_tab_active(self, total_wait_time):
        """保持主标签页活跃状态"""
        start_time = time.time()
        
        while time.time() - start_time < total_wait_time:
            try:
                # 随机轻微滚动
                scroll_distance = random.randint(50, 200)
                self.main_tab.run_js(f"""
                    window.scrollBy({{
                        top: {scroll_distance},
                        behavior: 'smooth'
                    }});
                """)
                
                # 随机触发轻微交互
                if random.random() < 0.3:
                    self.main_tab.run_js("""
                        document.dispatchEvent(new MouseEvent('mousemove', {
                            bubbles: true,
                            clientX: Math.random() * window.innerWidth,
                            clientY: Math.random() * window.innerHeight
                        }));
                    """)
                
                # 等待一段时间
                wait_chunk = random.uniform(5, 10)
                time.sleep(min(wait_chunk, total_wait_time - (time.time() - start_time)))
                
            except Exception as e:
                logger.debug(f"保持活跃状态异常: {str(e)}")
                time.sleep(5)

    def enhanced_deep_scroll(self):
        """增强版深度滚动浏览 - 更真实的阅读行为"""
        try:
            # 多次深度滚动
            scroll_count = random.randint(6, 10)
            logger.debug(f"📖 增强深度滚动浏览: {scroll_count} 次")
            
            for i in range(scroll_count):
                # 随机滚动距离和速度
                scroll_distance = random.randint(500, 800)
                
                # 平滑滚动
                self.page.run_js(f"""
                    window.scrollBy({{
                        top: {scroll_distance},
                        behavior: 'smooth'
                    }});
                """)
                
                # 随机阅读时间
                read_time = random.uniform(3, 7)
                time.sleep(read_time)
                
                # 随机触发交互事件
                if random.random() < 0.4:
                    self.trigger_interaction_events()
            
            # 最终触发完整的事件序列
            self.trigger_complete_interaction_sequence()
            
            logger.debug("✅ 增强深度阅读完成")
            
        except Exception as e:
            logger.debug(f"增强深度阅读异常: {str(e)}")

    def trigger_interaction_events(self):
        """触发交互事件"""
        try:
            self.page.run_js("""
                // 鼠标移动
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
                
                // 点击事件
                document.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
            """)
        except:
            pass

    def trigger_complete_interaction_sequence(self):
        """触发完整的交互事件序列"""
        try:
            self.page.run_js("""
                // 滚动事件
                window.dispatchEvent(new Event('scroll'));
                
                // 焦点事件
                window.dispatchEvent(new Event('focus'));
                document.dispatchEvent(new Event('focus'));
                
                // 鼠标悬停
                const elements = document.querySelectorAll('a, button, .topic-body');
                if (elements.length > 0) {
                    const randomElement = elements[Math.floor(Math.random() * elements.length)];
                    randomElement.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                    randomElement.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                }
                
                // 键盘事件
                document.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
                document.dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true }));
            """)
        except:
            pass

    def print_connect_info(self):
        """连接信息获取"""
        logger.info("🔗 获取连接信息...")
        try:
            # 在新标签页打开连接页面
            connect_tab = self.page.new_tab()
            connect_tab.get(self.site_config['connect_url'])
            time.sleep(3)
            
            self.handle_cloudflare()
            time.sleep(2)
            
            # 简化选择器：只使用tag:table
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
                    if project and current and requirement:
                        info.append([project, current, requirement])
            
            if info:
                # 使用 tabulate 美化表格显示
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("="*60 + "\n")
                
                # 统计达标情况
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
                total = len(info)
                logger.success(f"📈 统计: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 关闭连接页面标签
            connect_tab.close()
            logger.info("✅ 连接信息获取完成")
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理 {self.site_name}")
            
            # 1. 确保登录（双重验证）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 2. 主题浏览（多标签页策略）
            browse_count = self.browse_topics_optimized()
            
            # 3. 连接信息获取
            self.print_connect_info()
            
            # 4. 保存缓存
            self.save_caches()
            
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
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (多标签页优化版)")
    logger.info("=" * 80)
    
    # 检查扩展
    if TURNSTILE_PATCH_ENABLED:
        if os.path.exists(TURNSTILE_PATCH_PATH):
            logger.info(f"✅ turnstilePatch扩展路径: {TURNSTILE_PATCH_PATH}")
            ext_files = os.listdir(TURNSTILE_PATCH_PATH)
            logger.info(f"📁 扩展文件: {ext_files}")
            if 'manifest.json' in ext_files:
                logger.info("✅ manifest.json 存在")
            else:
                logger.warning("⚠️ manifest.json 不存在，扩展可能无效")
        else:
            logger.warning(f"⚠️ turnstilePatch扩展目录不存在: {TURNSTILE_PATCH_PATH}")
    
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    
    success_sites = []
    failed_sites = []

    # 检查凭证配置
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
            wait_time = random.uniform(15, 30)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒...")
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
        logger.warning(f"⚠️ 环境变量未设置: {', '.join(missing_vars)}")
    
    main()
