"""
cron: 0 * * * *
new Env("Linux.Do 多站点自动浏览")
"""
import os
import random
import time
import json
import functools
import sys
import base64
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium, SessionPage
from tabulate import tabulate
from urllib.parse import urljoin
import re

# ---------------- 基本配置 ----------------
HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"false", "0", "off"}
BROWSE_ENABLED = os.getenv("BROWSE_ENABLED", "true").lower() not in {"false", "0", "off"}
SELECTOR = os.getenv("SITE_SELECTOR", "all")
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# ---------------- 缓存管理 ----------------
def cache_file(name):
    return CACHE_DIR / f"{name}_cookies.json"

def load_cookies(name):
    """加载缓存的cookies"""
    f = cache_file(name)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf8"))
        cache_time = datetime.fromisoformat(data["cache_time"])
        if datetime.now() - cache_time > timedelta(days=7):
            logger.warning(f"🕒 {name} 的 Cookies 已过期")
            return None
        logger.info(f"📦 加载 {name} 的缓存cookies")
        return data["cookies"]
    except Exception as e:
        logger.warning(f"❌ 加载 {name} 缓存失败: {e}")
        return None

def save_cookies(name, cookies):
    """保存cookies到缓存"""
    try:
        cache_data = {
            "cookies": cookies,
            "cache_time": datetime.now().isoformat()
        }
        cache_file(name).write_text(
            json.dumps(cache_data, ensure_ascii=False, indent=2),
            encoding="utf8"
        )
        logger.info(f"💾 保存 {name} 的cookies到缓存")
        return True
    except Exception as e:
        logger.error(f"❌ 保存 {name} 缓存失败: {e}")
        return False

# ---------------- 重试装饰器 ----------------
def retry(retries=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if i == retries:
                        logger.error(f"❌ {func.__name__} 最终失败: {e}")
                        raise
                    logger.warning(f"⚠️ {func.__name__} 第{i}/{retries}次失败: {e}")
                    time.sleep(delay)
        return wrapper
    return decorator

# ---------------- 站点配置 ----------------
SITES = [
    {
        "name": "linux_do",
        "base": "https://linux.do",
        "login": "https://linux.do/login",
        "latest": "https://linux.do/latest",
        "connect": "https://connect.linux.do",
        "user": os.getenv("LINUXDO_USERNAME"),
        "pass": os.getenv("LINUXDO_PASSWORD")
    },
    {
        "name": "idcflare", 
        "base": "https://idcflare.com",
        "login": "https://idcflare.com/login", 
        "latest": "https://idcflare.com/latest",
        "connect": "https://connect.idcflare.com",
        "user": os.getenv("IDCFLARE_USERNAME"),
        "pass": os.getenv("IDCFLARE_PASSWORD")
    }
]

# 站点选择过滤
if SELECTOR != "all":
    SITES = [s for s in SITES if s["name"] == SELECTOR]

# 检查账号密码配置
for site in SITES:
    if not (site["user"] and site["pass"]):
        logger.error(f"❌ {site['name']} 账号或密码未配置")
        sys.exit(1)

# ---------------- Cloudflare Turnstile 处理器 ----------------
class CloudflareTurnstileHandler:
    """处理Cloudflare Turnstile验证"""
    
    def __init__(self, page):
        self.page = page
        
    def detect_turnstile(self):
        """检测Turnstile验证是否存在"""
        turnstile_selectors = [
            'iframe[src*="challenges.cloudflare.com"]',
            'iframe[src*="turnstile"]',
            '.cf-turnstile',
            '[data-sitekey]',
            '#cf-challenge-wrapper'
        ]
        
        for selector in turnstile_selectors:
            if self.page(selector, timeout=2):
                logger.info(f"🛡️ 检测到Cloudflare验证元素: {selector}")
                return True
        return False
    
    def get_turnstile_response_token(self):
        """尝试获取Turnstile响应token"""
        try:
            # 等待Turnstile iframe加载
            iframe = self.page('iframe[src*="challenges.cloudflare.com"]', timeout=10)
            if iframe:
                logger.info("🔄 发现Turnstile iframe，等待验证完成...")
                
                # 切换到iframe内部
                with self.page.frame(iframe.attr('src')):
                    # 等待验证完成
                    for i in range(30):  # 最多等待30秒
                        time.sleep(1)
                        # 检查是否通过验证
                        if self.page.run_js('return window.turnstile && turnstile.getResponse'):
                            token = self.page.run_js('return turnstile.getResponse()')
                            if token:
                                logger.success("✅ 成功获取Turnstile token")
                                return token
                        # 检查是否有成功标记
                        if self.page('.verify-success', timeout=1):
                            logger.info("✅ Turnstile验证成功")
                            return "auto_success"
                logger.warning("⏰ Turnstile验证超时")
        except Exception as e:
            logger.warning(f"⚠️ 获取Turnstile token失败: {e}")
        return None
    
    def bypass_turnstile(self):
        """尝试绕过Turnstile验证"""
        logger.info("🛡️ 尝试处理Cloudflare Turnstile验证...")
        
        if not self.detect_turnstile():
            logger.info("✅ 未检测到Turnstile验证")
            return True
            
        token = self.get_turnstile_response_token()
        if token:
            # 设置token到隐藏字段
            try:
                self.page.run_js(f'''
                    document.querySelectorAll('input[name="cf-turnstile-response"]').forEach(input => {{
                        input.value = "{token}";
                    }});
                ''')
                logger.success("✅ 已设置Turnstile响应token")
                return True
            except Exception as e:
                logger.warning(f"⚠️ 设置token失败: {e}")
        
        # 如果自动获取失败，等待手动验证
        logger.info("⏳ 等待手动验证完成...")
        for i in range(60):  # 最多等待60秒
            time.sleep(1)
            if not self.detect_turnstile():
                logger.success("✅ Cloudflare验证已完成")
                return True
            if i % 10 == 0:
                logger.info(f"⏰ 等待验证中... ({i+1}/60秒)")
        
        logger.error("❌ Cloudflare验证超时")
        return False

# ---------------- 登录页面分析器 ----------------
class LoginPageAnalyzer:
    """分析登录页面元素和验证情况"""
    
    def __init__(self, page):
        self.page = page
        
    def analyze_page_elements(self):
        """分析页面上的所有元素"""
        logger.info("🔍 分析登录页面元素...")
        
        # 检测验证元素
        self.detect_verification_elements()
        
        # 检测登录表单元素
        self.detect_login_elements()
        
        # 检测动态加载元素
        self.detect_dynamic_elements()
    
    def detect_verification_elements(self):
        """检测各种验证机制"""
        verification_patterns = {
            "Cloudflare Turnstile": [
                'iframe[src*="challenges.cloudflare.com"]',
                '.cf-turnstile',
                '[data-sitekey]'
            ],
            "reCAPTCHA": [
                'iframe[src*="google.com/recaptcha"]',
                '.g-recaptcha',
                '[data-sitekey*="6L"]'
            ],
            "hCAPTCHA": [
                'iframe[src*="hcaptcha.com"]',
                '.h-captcha'
            ],
            "验证码": [
                'img[src*="captcha"]',
                '.captcha',
                '#captcha'
            ],
            "滑动验证": [
                '.slider',
                '.drag',
                '.verify-bar'
            ]
        }
        
        found_verifications = []
        for verification_type, selectors in verification_patterns.items():
            for selector in selectors:
                if self.page(selector, timeout=1):
                    found_verifications.append(verification_type)
                    logger.warning(f"🛡️ 检测到 {verification_type} 验证: {selector}")
                    break
        
        if not found_verifications:
            logger.info("✅ 未检测到明显的验证机制")
        else:
            logger.info(f"📋 检测到的验证机制: {', '.join(set(found_verifications))}")
    
    def detect_login_elements(self):
        """检测登录表单元素"""
        login_selectors = {
            "用户名输入框": [
                'input[name="username"]',
                'input[name="user"]', 
                'input[type="text"]',
                '#username',
                '#user',
                '#login-account-name'
            ],
            "密码输入框": [
                'input[type="password"]',
                'input[name="password"]',
                '#password',
                '#login-account-password'
            ],
            "登录按钮": [
                'button[type="submit"]',
                'input[type="submit"]',
                '.login-button',
                '#login-button',
                'button:contains("登录")',
                'button:contains("Sign in")'
            ],
            "CSRF Token": [
                'input[name="authenticity_token"]',
                'input[name="_token"]',
                'input[name="csrf_token"]',
                'meta[name="csrf-token"]'
            ]
        }
        
        found_elements = {}
        for element_type, selectors in login_selectors.items():
            for selector in selectors:
                try:
                    if self.page(selector, timeout=1):
                        found_elements[element_type] = selector
                        logger.info(f"✅ 找到 {element_type}: {selector}")
                        break
                except:
                    continue
        
        missing_elements = set(login_selectors.keys()) - set(found_elements.keys())
        if missing_elements:
            logger.warning(f"⚠️ 未找到的元素: {', '.join(missing_elements)}")
        
        return found_elements
    
    def detect_dynamic_elements(self):
        """检测动态加载的元素"""
        # 检查是否有动态加载的脚本
        dynamic_scripts = self.page.eles('script[src*="challenge"]') + \
                         self.page.eles('script[src*="captcha"]') + \
                         self.page.eles('script[src*="turnstile"]')
        
        if dynamic_scripts:
            logger.info(f"🔄 检测到 {len(dynamic_scripts)} 个验证相关脚本")
        
        # 检查AJAX加载
        if self.page.run_js('return typeof jQuery !== "undefined"'):
            logger.info("📡 页面使用jQuery，可能存在AJAX动态加载")

# ---------------- 自动浏览器 ----------------
class AutoBrowser:
    def __init__(self, site):
        self.site = site
        self.name = site["name"]
        self.user = site["user"]
        self.pw = site["pass"]
        self.browser = None
        self.page = None
        self.turnstile_handler = None
        self.login_analyzer = None
    
    def start_browser(self):
        """启动浏览器"""
        logger.info(f"🚀 启动浏览器访问 {self.name}")
        
        co = (ChromiumOptions()
              .headless(HEADLESS)
              .incognito(True)
              .set_argument("--no-sandbox")
              .set_argument("--disable-dev-shm-usage")
              .set_argument("--disable-blink-features=AutomationControlled")
              .set_argument("--disable-features=VizDisplayCompositor")
              .set_argument("--disable-background-timer-throttling")
              .set_argument("--disable-renderer-backgrounding"))
        
        # 设置更真实的用户代理
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
        co.set_user_agent(random.choice(user_agents))
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # 隐藏自动化特征
        self._hide_automation()
        
        self.turnstile_handler = CloudflareTurnstileHandler(self.page)
        self.login_analyzer = LoginPageAnalyzer(self.page)
    
    def _hide_automation(self):
        """隐藏浏览器自动化特征"""
        try:
            self.page.run_js("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            """)
        except Exception as e:
            logger.warning(f"⚠️ 隐藏自动化特征失败: {e}")
    
    def check_login_success(self):
        """检查是否登录成功"""
        try:
            # 检查页面中是否包含用户名
            page_text = self.page.html.lower()
            username_lower = self.user.lower()
            
            if username_lower in page_text:
                logger.success(f"✅ 登录成功 - 检测到用户名: {self.user}")
                return True
            
            # 检查用户菜单或登录状态元素
            user_indicators = [
                f'.username:contains("{self.user}")',
                '.current-user',
                '.header-user',
                '.user-menu',
                '[data-current-user]'
            ]
            
            for indicator in user_indicators:
                if self.page(indicator, timeout=1):
                    logger.success(f"✅ 登录成功 - 检测到用户指示器: {indicator}")
                    return True
            
            logger.warning("⚠️ 未检测到登录成功标志")
            return False
            
        except Exception as e:
            logger.error(f"❌ 检查登录状态失败: {e}")
            return False
    
    @retry(retries=2, delay=5)
    def login_with_cookies(self):
        """使用缓存的cookies登录"""
        logger.info(f"🔐 尝试使用缓存cookies登录 {self.name}")
        
        self.page.get(self.site["base"])
        time.sleep(3)
        
        # 访问最新页面检查登录状态
        self.page.get(self.site["latest"])
        time.sleep(3)
        
        if self.check_login_success():
            logger.success("🎉 缓存cookies有效，跳过登录")
            return True
        else:
            logger.warning("🔄 缓存cookies失效，需要重新登录")
            return False
    
    def perform_login(self):
        """执行登录流程"""
        logger.info(f"🔐 开始登录 {self.name}")
        
        # 访问登录页面
        self.page.get(self.site["login"])
        time.sleep(5)  # 等待页面加载
        
        # 分析登录页面
        self.login_analyzer.analyze_page_elements()
        
        # 处理Cloudflare验证
        if not self.turnstile_handler.bypass_turnstile():
            logger.error("❌ Cloudflare验证处理失败")
            return False
        
        # 等待额外的加载时间
        logger.info("⏳ 等待页面元素加载...")
        time.sleep(3)
        
        # 查找并填写登录表单
        if not self._fill_login_form():
            return False
        
        # 提交登录表单
        if not self._submit_login():
            return False
        
        # 等待登录完成
        time.sleep(5)
        
        # 验证登录成功
        if self.check_login_success():
            logger.success("✅ 登录流程完成")
            
            # 获取主题数量后保存最新cookies
            theme_count = self._get_theme_count()
            if theme_count > 0:
                logger.info(f"📊 获取到 {theme_count} 个主题，保存最新cookies")
                save_cookies(self.name, self.page.cookies())
            else:
                logger.warning("⚠️ 未获取到主题，可能登录未完全成功")
            
            return True
        else:
            logger.error("❌ 登录失败")
            self.page.get_screenshot(f"{self.name}_login_failed.png")
            return False
    
    def _fill_login_form(self):
        """填写登录表单"""
        try:
            # 查找用户名输入框
            username_selectors = [
                'input[name="username"]',
                'input[name="user"]',
                'input[type="text"]',
                '#username',
                '#user',
                '#login-account-name'
            ]
            
            username_field = None
            for selector in username_selectors:
                username_field = self.page(selector, timeout=2)
                if username_field:
                    logger.info(f"✅ 找到用户名输入框: {selector}")
                    break
            
            if not username_field:
                logger.error("❌ 未找到用户名输入框")
                return False
            
            # 模拟人类输入用户名
            self._human_type(username_field, self.user)
            time.sleep(random.uniform(1, 2))
            
            # 查找密码输入框
            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                '#password',
                '#login-account-password'
            ]
            
            password_field = None
            for selector in password_selectors:
                password_field = self.page(selector, timeout=2)
                if password_field:
                    logger.info(f"✅ 找到密码输入框: {selector}")
                    break
            
            if not password_field:
                logger.error("❌ 未找到密码输入框")
                return False
            
            # 模拟人类输入密码
            self._human_type(password_field, self.pw)
            time.sleep(random.uniform(1, 2))
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 填写登录表单失败: {e}")
            return False
    
    def _human_type(self, element, text):
        """模拟人类输入"""
        element.clear()
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.2))  # 随机延迟模拟人类输入
    
    def _submit_login(self):
        """提交登录表单"""
        try:
            # 查找登录按钮
            login_button_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                '.login-button',
                '#login-button',
                'button:contains("登录")',
                'button:contains("Sign in")',
                'button:contains("Log in")'
            ]
            
            login_button = None
            for selector in login_button_selectors:
                login_button = self.page(selector, timeout=2)
                if login_button:
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    break
            
            if not login_button:
                logger.error("❌ 未找到登录按钮")
                return False
            
            # 模拟人类点击
            self._human_click(login_button)
            return True
            
        except Exception as e:
            logger.error(f"❌ 提交登录失败: {e}")
            return False
    
    def _human_click(self, element):
        """模拟人类点击"""
        # 先移动鼠标到元素位置
        element.click()
        time.sleep(random.uniform(1, 3))
    
    def _get_theme_count(self):
        """获取主题数量"""
        try:
            self.page.get(self.site["latest"])
            time.sleep(3)
            
            # 查找主题链接
            theme_selectors = [
                '.title.raw-link.raw-topic-link',
                '.topic-list-item .main-link a',
                '.topic-list .topic-title a'
            ]
            
            for selector in theme_selectors:
                themes = self.page.eles(selector)
                if themes:
                    logger.info(f"📝 找到 {len(themes)} 个主题")
                    return len(themes)
            
            logger.warning("⚠️ 未找到主题元素")
            return 0
            
        except Exception as e:
            logger.error(f"❌ 获取主题数量失败: {e}")
            return 0
    
    def browse_themes(self):
        """浏览主题模拟用户行为"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return
        
        logger.info("🌐 开始浏览主题")
        
        try:
            self.page.get(self.site["latest"])
            time.sleep(3)
            
            # 查找主题链接
            theme_links = self.page.eles('.title.raw-link.raw-topic-link')[:15]
            if not theme_links:
                logger.warning("📭 未找到主题链接")
                return
            
            logger.info(f"🔗 找到 {len(theme_links)} 个主题链接")
            
            # 随机选择10个主题浏览
            selected_themes = random.sample(theme_links, min(10, len(theme_links)))
            logger.info(f"🎯 选择浏览 {len(selected_themes)} 个主题")
            
            for i, link in enumerate(selected_themes, 1):
                theme_url = link.attr("href")
                if not theme_url.startswith('http'):
                    theme_url = urljoin(self.site["base"], theme_url)
                
                logger.info(f"📖 浏览第{i}/{len(selected_themes)}个主题: {theme_url}")
                self._browse_single_theme(theme_url)
                
                # 主题间随机间隔
                if i < len(selected_themes):
                    interval = random.uniform(5, 15)
                    logger.info(f"⏳ 等待 {interval:.1f} 秒后浏览下一个主题")
                    time.sleep(interval)
            
            logger.success("✅ 主题浏览完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {e}")
    
    @retry(retries=2, delay=2)
    def _browse_single_theme(self, url):
        """浏览单个主题"""
        tab = self.browser.new_tab()
        try:
            tab.get(url)
            time.sleep(random.uniform(2, 4))
            
            # 随机点赞（3%概率）
            if random.random() < 0.03:
                try:
                    like_button = tab('.discourse-reactions-reaction-button', timeout=2)
                    if like_button:
                        like_button.click()
                        logger.success("👍 随机点赞成功")
                        time.sleep(1)
                except:
                    pass
            
            # 模拟阅读行为
            read_time = random.randint(8, 20)  # 阅读8-20秒
            scroll_actions = random.randint(3, 8)  # 滚动3-8次
            
            logger.info(f"📚 模拟阅读 {read_time} 秒，滚动 {scroll_actions} 次")
            
            start_time = time.time()
            actions_completed = 0
            
            while time.time() - start_time < read_time and actions_completed < scroll_actions:
                # 随机滚动
                scroll_distance = random.randint(300, 800)
                tab.run_js(f"window.scrollBy(0, {scroll_distance})")
                actions_completed += 1
                
                # 随机停留
                stay_time = random.uniform(1, 3)
                time.sleep(stay_time)
                
                # 3%概率提前退出
                if random.random() < 0.03:
                    logger.info("🎲 随机提前退出阅读")
                    break
            
            # 最后滚回顶部或底部
            if random.random() < 0.5:
                tab.run_js("window.scrollTo(0, 0)")
            else:
                tab.run_js("window.scrollTo(0, document.body.scrollHeight)")
            
            time.sleep(1)
            
        finally:
            tab.close()
    
    def get_connect_info(self):
        """获取连接信息"""
        try:
            logger.info(f"📊 获取 {self.name} 的连接信息")
            self.page.get(self.site["connect"])
            time.sleep(3)
            
            # 查找表格数据
            rows = []
            table_selectors = ['table', '.table', '.connect-table']
            
            for selector in table_selectors:
                tables = self.page.eles(selector)
                for table in tables:
                    for tr in table.eles('tag:tr')[1:]:  # 跳过表头
                        tds = tr.eles('tag:td')[:3]
                        if len(tds) >= 3:
                            row_data = [td.text.strip() for td in tds]
                            rows.append(row_data)
            
            if rows:
                logger.info("📋 连接信息表格:")
                print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("-" * 50)
            else:
                logger.info("📭 未找到连接信息表格")
                
        except Exception as e:
            logger.warning(f"⚠️ 获取连接信息失败: {e}")
    
    def run(self):
        """主运行流程"""
        logger.info(f"🎬 开始处理 {self.name}")
        
        try:
            # 1. 启动浏览器
            self.start_browser()
            
            # 2. 尝试使用缓存cookies登录
            cached_cookies = load_cookies(self.name)
            if cached_cookies:
                self.page.set.cookies(cached_cookies)
                if self.login_with_cookies():
                    pass  # 缓存登录成功
                else:
                    # 缓存失效，重新登录
                    if not self.perform_login():
                        raise Exception("登录失败")
            else:
                # 无缓存，执行完整登录
                if not self.perform_login():
                    raise Exception("登录失败")
            
            # 3. 浏览主题（模拟用户行为）
            self.browse_themes()
            
            # 4. 获取连接信息
            self.get_connect_info()
            
            logger.success(f"✅ {self.name} 处理完成")
            
        except Exception as e:
            logger.error(f"❌ {self.name} 处理失败: {e}")
            # 截图保存错误信息
            try:
                self.page.get_screenshot(f"{self.name}_error.png")
                logger.info(f"📸 错误截图已保存: {self.name}_error.png")
            except:
                pass
            raise
        
        finally:
            # 关闭浏览器
            try:
                if self.browser:
                    self.browser.quit()
                    logger.info(f"🔚 关闭 {self.name} 浏览器")
            except Exception as e:
                logger.warning(f"⚠️ 关闭浏览器时出错: {e}")

# ---------------- 主入口 ----------------
def main():
    """主函数"""
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
    logger.add(
        "run.log",
        rotation="10 MB",
        retention="7 days",
        encoding="utf8",
        level="INFO"
    )
    
    logger.info("=" * 60)
    logger.info("🚀 Linux.Do 多站点自动浏览脚本启动")
    logger.info("=" * 60)
    
    # 显示配置信息
    logger.info(f"📋 配置信息:")
    logger.info(f"   - 无头模式: {'是' if HEADLESS else '否'}")
    logger.info(f"   - 浏览功能: {'启用' if BROWSE_ENABLED else '禁用'}")
    logger.info(f"   - 站点选择: {SELECTOR}")
    logger.info(f"   - 处理站点: {[s['name'] for s in SITES]}")
    
    # 依次处理每个站点
    success_count = 0
    for site in SITES:
        try:
            browser = AutoBrowser(site)
            browser.run()
            success_count += 1
        except Exception as e:
            logger.error(f"❌ 站点 {site['name']} 执行失败: {e}")
            continue
    
    # 总结报告
    logger.info("=" * 60)
    logger.info(f"📊 执行总结: {success_count}/{len(SITES)} 个站点成功")
    logger.info("=" * 60)
    
    if success_count == len(SITES):
        logger.success("🎉 所有站点处理完成！")
    else:
        logger.warning(f"⚠️ 有 {len(SITES) - success_count} 个站点处理失败")

if __name__ == "__main__":
    main()
