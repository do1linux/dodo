import os
import sys
import time
import random
import json
import traceback
import argparse
from datetime import datetime
from urllib.parse import urljoin
from loguru import logger
from tabulate import tabulate
from DrissionPage import ChromiumPage, ChromiumOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

IS_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
        'cookies_file': "cookies_linux_do.json",
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
        'cookies_file': "cookies_idcflare.json",
    }
]

PAGE_TIMEOUT = 60
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 10

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864}
]

def parse_arguments():
    parser = argparse.ArgumentParser(description='LinuxDo 多站点自动化脚本')
    parser.add_argument('--site', type=str, help='指定运行的站点', 
                       choices=['linux_do', 'idcflare', 'all'], default='all')
    parser.add_argument('--verbose', action='store_true', help='详细输出模式')
    return parser.parse_args()

class CacheManager:
    @staticmethod
    def load_cookies(site_name):
        file_name = f"cookies_{site_name}.json"
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    cookies = json.load(f)
                logger.info(f"📦 加载cookies: {file_name}")
                return cookies
            return None
        except Exception as e:
            logger.warning(f"cookies加载失败 {file_name}: {str(e)}")
            return None

    @staticmethod
    def save_cookies(cookies, site_name):
        file_name = f"cookies_{site_name}.json"
        try:
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 cookies已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"cookies保存失败 {file_name}: {str(e)}")
            return False

class HumanBehaviorSimulator:
    @staticmethod
    def random_delay(min_seconds=1.0, max_seconds=3.0):
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    @staticmethod
    def simulate_typing(element, text):
        """模拟人类打字节奏"""
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.2))

    @staticmethod
    def simulate_mouse_movement(page):
        """模拟随机鼠标移动"""
        try:
            # 在页面内随机移动鼠标
            for _ in range(random.randint(3, 7)):
                x = random.randint(100, 1800)
                y = random.randint(100, 900)
                page.run_js(f"document.elementFromPoint({x}, {y})?.focus()")
                time.sleep(random.uniform(0.1, 0.5))
        except Exception as e:
            logger.debug(f"模拟鼠标移动时出错: {str(e)}")

    @staticmethod
    def simulate_scroll_behavior(page):
        """模拟人类滚动行为"""
        try:
            scroll_steps = random.randint(5, 12)
            for i in range(scroll_steps):
                scroll_amount = random.randint(300, 800)
                page.scroll.down(scroll_amount)
                time.sleep(random.uniform(0.8, 2.5))
                
                # 偶尔向上滚动一点
                if random.random() < 0.2:
                    page.scroll.up(random.randint(100, 300))
                    time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            logger.debug(f"模拟滚动行为时出错: {str(e)}")

class CloudflareHandler:
    @staticmethod
    def wait_for_cloudflare(page, timeout=30):
        logger.info("⏳ 等待Cloudflare验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                title = page.title
                current_url = page.url
                
                # 检查是否有Turnstile验证
                turnstile_frame = page.ele('tag:iframe[src*="challenges.cloudflare.com"], tag:iframe[src*="turnstile"]', timeout=0)
                if turnstile_frame:
                    logger.warning("🛡️ 检测到Cloudflare Turnstile验证")
                    if CloudflareHandler.handle_turnstile_challenge(page):
                        logger.success("✅ Turnstile验证处理完成")
                        return True
                
                if "请稍候" not in title and "Checking" not in title and "challenges" not in current_url:
                    logger.success("✅ Cloudflare验证已通过")
                    return True
                
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待Cloudflare时出错: {str(e)}")
                time.sleep(2)
        
        logger.warning("⚠️ Cloudflare等待超时，继续执行")
        return False

    @staticmethod
    def handle_turnstile_challenge(page):
        """处理Cloudflare Turnstile验证"""
        try:
            logger.info("🛡️ 尝试处理Turnstile验证...")
            
            # 注入JS来获取Turnstile响应
            turnstile_script = """
            async function getTurnstileResponse() {
                return new Promise((resolve) => {
                    // 尝试从全局对象获取响应
                    if (window.turnstile) {
                        const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]');
                        if (iframe) {
                            const widgetId = iframe.getAttribute('data-turnstile-widget-id') || iframe.id;
                            if (widgetId) {
                                turnstile.getResponse(widgetId).then(resolve);
                                return;
                            }
                        }
                    }
                    
                    // 备用方法：等待表单字段被填充
                    const checkField = setInterval(() => {
                        const field = document.querySelector('input[name="cf-turnstile-response"]');
                        if (field && field.value) {
                            clearInterval(checkField);
                            resolve(field.value);
                        }
                    }, 500);
                    
                    // 超时后备
                    setTimeout(() => {
                        clearInterval(checkField);
                        resolve(null);
                    }, 15000);
                });
            }
            return getTurnstileResponse();
            """
            
            token = page.run_js(turnstile_script)
            
            if token:
                logger.success(f"✅ 获取到Turnstile Token: {token[:20]}...")
                
                # 设置token到表单字段
                set_token_script = f"""
                (function(token) {{
                    const field = document.querySelector('input[name="cf-turnstile-response"]');
                    if (field) {{
                        field.value = token;
                    }}
                    // 触发change事件
                    if (field) {{
                        field.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }})('{token}');
                """
                page.run_js(set_token_script)
                
                time.sleep(3)
                return True
            else:
                logger.warning("❌ 无法获取Turnstile Token，尝试备用方案")
                # 备用方案：等待手动验证完成
                for i in range(30):
                    token_field = page.ele('input[name="cf-turnstile-response"]', timeout=0)
                    if token_field:
                        token_value = token_field.value
                        if token_value and len(token_value) > 10:
                            logger.success(f"✅ 检测到Turnstile响应: {token_value[:20]}...")
                            return True
                    time.sleep(1)
                
                return False
                
        except Exception as e:
            logger.error(f"处理Turnstile验证失败: {str(e)}")
            return False

class BrowserManager:
    @staticmethod
    def init_browser():
        """初始化浏览器配置"""
        co = ChromiumOptions()
        
        # 浏览器参数
        browser_args = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--disable-features=VizDisplayCompositor',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-default-apps',
            '--disable-translate',
            '--disable-extensions',
            '--disable-sync',
            '--disable-web-security',
            '--disable-features=TranslateUI',
            f'--user-agent={random.choice(USER_AGENTS)}'
        ]

        for arg in browser_args:
            co.set_argument(arg)
        
        # 设置视口大小
        viewport = random.choice(VIEWPORT_SIZES)
        co.set_argument(f'--window-size={viewport["width"]},{viewport["height"]}')
        
        # 创建浏览器实例
        browser = ChromiumPage(addr_driver_opts=co)
        
        # 设置页面超时
        browser.set.timeouts(page_load=PAGE_TIMEOUT * 1000)
        
        logger.info("🚀 浏览器已启动 (DrissionPage + Chromium)")
        return browser

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.detected_bot_checks = []
        self.detected_login_elements = []
        
    def run_for_site(self):
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False
            
        try:
            self.browser = BrowserManager.init_browser()
            
            # 加载cookies
            cookies = CacheManager.load_cookies(self.site_config['name'])
            if cookies:
                self.browser.set.cookies(cookies)
                logger.info(f"✅ 已加载缓存cookies")
            
            login_success = self.smart_login_approach()
            
            if login_success:
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                self.perform_browsing_actions()
                self.print_connect_info()
                self.save_session_data()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False
                
        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            traceback.print_exc()
            return False
        finally:
            self.cleanup()

    def smart_login_approach(self):
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")
            
            try:
                if self.try_direct_access():
                    return True
                
                if self.full_login_process():
                    return True
                    
            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")
            
            if attempt < RETRY_TIMES - 1:
                self.clear_cache()
                time.sleep(10 * (attempt + 1))
        
        return False

    def try_direct_access(self):
        try:
            logger.info("🔍 尝试直接访问...")
            self.browser.get(self.site_config['latest_topics_url'])
            time.sleep(5)
            
            if self.check_login_status():
                logger.success("✅ 缓存登录成功")
                return True
                
            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    def full_login_process(self):
        try:
            logger.info("🔐 开始完整登录流程")
            
            self.browser.get(self.site_config['login_url'])
            time.sleep(5)
            
            # 检测机器人验证和登录元素
            self.detect_bot_checks_and_login_elements()
            
            CloudflareHandler.wait_for_cloudflare(self.browser, timeout=30)
            
            if not self.wait_for_login_form():
                logger.error("❌ 登录表单加载失败")
                return False
            
            username = self.credentials['username']
            password = self.credentials['password']
            
            self.fill_login_form(username, password)
            
            if not self.submit_login():
                return False
            
            return self.verify_login_result()
            
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    def detect_bot_checks_and_login_elements(self):
        """检测机器人验证和登录元素"""
        logger.info("🔍 检测页面元素...")
        
        # 检测机器人验证
        bot_check_selectors = [
            'iframe[src*="cloudflare"]',
            'iframe[src*="challenges"]',
            'iframe[src*="turnstile"]',
            '.cf-challenge',
            '#cf-challenge',
            '.turnstile-wrapper',
            '[data-sitekey]',
            '.g-recaptcha',
            '.h-captcha'
        ]
        
        for selector in bot_check_selectors:
            try:
                elements = self.browser.eles(selector)
                if elements:
                    for element in elements:
                        self.detected_bot_checks.append(selector)
                        logger.warning(f"🤖 检测到机器人验证: {selector}")
            except:
                pass
        
        # 检测登录相关元素
        login_element_selectors = [
            'input[type="text"]',
            'input[type="password"]',
            'input[name="username"]',
            'input[name="password"]',
            '#username',
            '#password',
            'button[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("Log In")'
        ]
        
        for selector in login_element_selectors:
            try:
                elements = self.browser.eles(selector)
                if elements:
                    for element in elements:
                        if element.displayed:
                            self.detected_login_elements.append(selector)
                            logger.info(f"🔑 检测到登录元素: {selector}")
            except:
                pass
        
        # 打印检测结果
        if self.detected_bot_checks:
            logger.warning(f"🚨 检测到的机器人验证: {list(set(self.detected_bot_checks))}")
        if self.detected_login_elements:
            logger.info(f"✅ 检测到的登录元素: {list(set(self.detected_login_elements))}")

    def wait_for_login_form(self, max_wait=30):
        logger.info("⏳ 等待登录表单...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                username_selectors = [
                    '#login-account-name',
                    '#username', 
                    'input[name="username"]',
                    'input[type="text"]',
                    'input[placeholder*="用户名"]',
                    'input[placeholder*="username"]'
                ]
                
                for selector in username_selectors:
                    element = self.browser.ele(selector, timeout=0)
                    if element and element.displayed:
                        logger.success(f"✅ 找到登录表单: {selector}")
                        return True
                
                # 检查是否有CSRF token
                csrf_selectors = [
                    'input[name="authenticity_token"]',
                    'input[name="csrf_token"]',
                    'meta[name="csrf-token"]'
                ]
                
                for selector in csrf_selectors:
                    element = self.browser.ele(selector, timeout=0)
                    if element:
                        logger.info(f"🔐 找到CSRF Token元素: {selector}")
                
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                time.sleep(2)
        
        logger.error("❌ 登录表单等待超时")
        return False

    def fill_login_form(self, username, password):
        try:
            HumanBehaviorSimulator.simulate_mouse_movement(self.browser)
            
            # 查找并填写用户名
            username_selectors = [
                '#login-account-name', 
                '#username', 
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="用户名"]'
            ]
            
            username_filled = False
            for selector in username_selectors:
                element = self.browser.ele(selector, timeout=0)
                if element and element.displayed:
                    element.click()
                    time.sleep(0.5)
                    element.clear()
                    HumanBehaviorSimulator.simulate_typing(element, username)
                    username_filled = True
                    logger.info("✅ 已填写用户名")
                    break
            
            if not username_filled:
                logger.error("❌ 未找到用户名输入框")
                return
            
            HumanBehaviorSimulator.random_delay(1, 2)
            
            # 查找并填写密码
            password_selectors = [
                '#login-account-password', 
                '#password', 
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]'
            ]
            
            password_filled = False
            for selector in password_selectors:
                element = self.browser.ele(selector, timeout=0)
                if element and element.displayed:
                    element.click()
                    time.sleep(0.5)
                    element.clear()
                    HumanBehaviorSimulator.simulate_typing(element, password)
                    password_filled = True
                    logger.info("✅ 已填写密码")
                    break
            
            if not password_filled:
                logger.error("❌ 未找到密码输入框")
                return
            
            HumanBehaviorSimulator.random_delay(1, 3)
            
        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    def submit_login(self):
        try:
            login_buttons = [
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")',
                'button:has-text("Sign In")'
            ]
            
            for selector in login_buttons:
                button = self.browser.ele(selector, timeout=0)
                if button and button.displayed:
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    
                    # 模拟鼠标移动和点击前暂停
                    HumanBehaviorSimulator.random_delay(0.5, 1.5)
                    button.click()
                    logger.info("✅ 已点击登录按钮")
                    
                    # 等待登录处理
                    time.sleep(8)
                    return True
            
            logger.error("❌ 未找到登录按钮")
            return False
            
        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    def verify_login_result(self):
        logger.info("🔍 验证登录结果...")
        
        current_url = self.browser.url
        if current_url != self.site_config['login_url']:
            logger.info("✅ 页面已跳转，可能登录成功")
        
        # 检查错误信息
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger', '.login-error']
        for selector in error_selectors:
            error_element = self.browser.ele(selector, timeout=0)
            if error_element:
                error_text = error_element.text
                logger.error(f"❌ 登录错误: {error_text}")
                return False
        
        return self.check_login_status()

    def check_login_status(self):
        """严格检查登录状态，必须检测到用户名"""
        try:
            username = self.credentials['username']
            logger.info(f"🔍 严格检查登录状态，查找用户名: {username}")
            
            # 方法1: 检查页面内容中的用户名
            page_content = self.browser.html
            if username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {username}")
                return True
            
            # 方法2: 检查用户相关元素
            user_indicators = [
                f'a[href*="/u/{username}"]',
                f'a[href*="/users/{username}"]',
                '.current-user',
                '[data-current-user]',
                '.header-dropdown-toggle',
                '.user-menu'
            ]
            
            for selector in user_indicators:
                element = self.browser.ele(selector, timeout=0)
                if element and element.displayed:
                    element_text = element.text
                    if username.lower() in element_text.lower():
                        logger.success(f"✅ 在用户元素中找到用户名: {selector}")
                        return True
            
            # 方法3: 访问个人资料页面
            profile_urls = [
                f"{self.site_config['base_url']}/u/{username}",
                f"{self.site_config['base_url']}/users/{username}",
                f"{self.site_config['base_url']}/user/{username}"
            ]
            
            current_url = self.browser.url
            for profile_url in profile_urls:
                try:
                    self.browser.get(profile_url)
                    time.sleep(3)
                    
                    profile_content = self.browser.html
                    if username.lower() in profile_content.lower():
                        logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                        # 返回之前页面
                        self.browser.back()
                        return True
                except Exception:
                    continue
            
            logger.error(f"❌ 无法在页面中找到用户名: {username}")
            return False
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    def perform_browsing_actions(self):
        """执行浏览行为模拟真实用户"""
        try:
            logger.info("🌐 开始模拟用户浏览行为...")
            
            # 访问最新主题页面
            self.browser.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            # 模拟滚动行为
            HumanBehaviorSimulator.simulate_scroll_behavior(self.browser)
            
            # 获取主题列表 - 使用DrissionPage的选择器
            topic_links = self.browser.eles('a.title, a.topic-title, a[href*="/t/"]')
            valid_topics = []
            
            for link in topic_links:
                href = link.attr('href')
                if href and '/t/' in href and not href.endswith('/t/about'):
                    full_url = urljoin(self.site_config['base_url'], href)
                    valid_topics.append((link, full_url))
            
            logger.info(f"📚 找到 {len(valid_topics)} 个有效主题")
            
            # 随机选择部分主题进行浏览
            topics_to_browse = min(MAX_TOPICS_TO_BROWSE, len(valid_topics))
            selected_topics = random.sample(valid_topics, topics_to_browse) if valid_topics else []
            
            for i, (link, url) in enumerate(selected_topics):
                logger.info(f"📖 浏览主题 {i+1}/{topics_to_browse}: {url}")
                
                try:
                    # 在新标签页中打开主题
                    new_tab = self.browser.new_tab()
                    new_tab.get(url)
                    time.sleep(3)
                    
                    # 在新页面中模拟浏览行为
                    self.simulate_topic_browsing(new_tab)
                    
                    # 随机决定是否点赞
                    if random.random() < 0.2:  # 20%的概率点赞
                        self.simulate_like_behavior(new_tab)
                    
                    # 随机浏览时间
                    browse_time = random.uniform(15, 45)
                    time.sleep(browse_time)
                    
                    new_tab.close()
                    logger.info(f"✅ 完成浏览主题 {i+1}")
                    
                    # 主题间随机间隔
                    if i < len(selected_topics) - 1:
                        time.sleep(random.uniform(5, 15))
                        
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"🎉 完成浏览 {len(selected_topics)} 个主题")
            
        except Exception as e:
            logger.error(f"执行浏览行为失败: {str(e)}")

    def simulate_topic_browsing(self, page):
        """在主题页面中模拟真实浏览行为"""
        try:
            # 模拟鼠标移动
            HumanBehaviorSimulator.simulate_mouse_movement(page)
            
            # 模拟滚动阅读
            scroll_steps = random.randint(5, 12)
            for step in range(scroll_steps):
                scroll_amount = random.randint(300, 800)
                page.scroll.down(scroll_amount)
                
                # 随机暂停模拟阅读
                pause_time = random.uniform(1, 4)
                time.sleep(pause_time)
                
                # 偶尔随机点击空白处
                if random.random() < 0.1:
                    try:
                        # 在可见区域内随机点击
                        page.run_js("""
                            const x = Math.random() * (window.innerWidth - 200) + 100;
                            const y = Math.random() * (window.innerHeight - 200) + 100;
                            document.elementFromPoint(x, y)?.click();
                        """)
                        time.sleep(1)
                    except:
                        pass
            
            # 可能滚动回顶部
            if random.random() < 0.3:
                page.scroll.to_top()
                time.sleep(2)
                
        except Exception as e:
            logger.debug(f"模拟浏览行为时出错: {str(e)}")

    def simulate_like_behavior(self, page):
        """模拟点赞行为"""
        try:
            like_selectors = [
                '.like-button',
                '.btn-like',
                '[data-action="like"]',
                'button[title*="Like"]',
                'button[title*="喜欢"]'
            ]
            
            for selector in like_selectors:
                like_btn = page.ele(selector, timeout=0)
                if like_btn and like_btn.displayed:
                    # 检查是否已经点赞
                    class_attr = like_btn.attr('class') or ''
                    data_attr = like_btn.attr('data-liked') or ''
                    is_liked = 'has-like' in class_attr or data_attr == 'true'
                    
                    if not is_liked:
                        HumanBehaviorSimulator.simulate_mouse_movement(page)
                        like_btn.click()
                        logger.info("👍 模拟点赞行为")
                        time.sleep(2)
                    break
                    
        except Exception as e:
            logger.debug(f"模拟点赞失败: {str(e)}")

    def print_connect_info(self):
        """获取并打印连接信息"""
        try:
            logger.info("🔗 获取连接信息...")
            
            # 在新页面中打开连接信息页面
            new_tab = self.browser.new_tab()
            new_tab.get(self.site_config['connect_url'])
            time.sleep(3)
            
            # 等待页面加载完成
            time.sleep(2)
            
            # 使用DrissionPage的选择器获取表格数据
            table_selectors = ['table', '.table', '#connect-table', '.connect-table']
            table_data = []
            
            for selector in table_selectors:
                table = new_tab.ele(selector, timeout=0)
                if table:
                    rows = table.eles('tag:tr')
                    
                    for row in rows:
                        cells = row.eles('tag:td, tag:th')
                        if cells and len(cells) >= 3:
                            row_data = []
                            for cell in cells:
                                text = cell.text
                                row_data.append(text.strip() if text else "")
                            table_data.append(row_data)
                    
                    if table_data:
                        break
            
            if table_data:
                print("\n" + "="*60)
                print(f"🔗 {self.site_config['name'].upper()} 连接信息")
                print("="*60)
                headers = table_data[0] if len(table_data) > 0 else ["项目", "当前", "要求"]
                rows = table_data[1:] if len(table_data) > 1 else table_data
                print(tabulate(rows, headers=headers, tablefmt="grid"))
                print("="*60)
            else:
                logger.warning("❌ 未找到连接信息表格")
            
            new_tab.close()
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def save_session_data(self):
        """保存会话数据用于下次运行"""
        try:
            # 保存cookies
            cookies = self.browser.cookies()
            CacheManager.save_cookies(cookies, self.site_config['name'])
            
            logger.info("💾 会话数据已保存")
            
        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    def clear_cache(self):
        """清除缓存数据"""
        cache_file = f"cookies_{self.site_config['name']}.json"
        
        if os.path.exists(cache_file):
            os.remove(cache_file)
            logger.info(f"🗑️ 已清除: {cache_file}")

    def cleanup(self):
        """清理资源"""
        try:
            if self.browser:
                self.browser.quit()
        except Exception:
            pass

def main():
    args = parse_arguments()
    
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="DEBUG" if args.verbose else "INFO"
    )
    
    logger.info("🚀 LinuxDo自动化脚本启动 (DrissionPage版本)")
    
    # 确定目标站点
    target_sites = SITES if args.site == 'all' else [s for s in SITES if s['name'] == args.site]
    
    results = []
    
    for site_config in target_sites:
        logger.info(f"🎯 处理站点: {site_config['name']}")
        
        automator = SiteAutomator(site_config)
        success = automator.run_for_site()
        
        results.append({
            'site': site_config['name'],
            'success': success
        })
        
        # 站点间随机间隔
        if site_config != target_sites[-1]:
            time.sleep(random.uniform(10, 20))
    
    # 输出执行结果
    logger.info("📊 执行结果:")
    table_data = [[r['site'], "✅ 成功" if r['success'] else "❌ 失败"] for r in results]
    print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))
    
    success_count = sum(1 for r in results if r['success'])
    logger.success(f"🎉 完成: {success_count}/{len(results)} 个站点成功")

if __name__ == "__main__":
    main()
