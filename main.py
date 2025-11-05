from DrissionPage import ChromiumPage, ChromiumOptions
from loguru import logger
import random
import json
import os
import time
from urllib.parse import urljoin
import re
from datetime import datetime, timedelta

# 配置常量
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
HEADLESS_MODE = os.getenv('HEADLESS', 'true').lower() == 'true'

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
        'profile_url': 'https://linux.do/u/{username}',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json",
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
        'profile_url': 'https://idcflare.com/u/{username}',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json",
    }
]

PAGE_TIMEOUT = 180  # 延长超时时间
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 8  # 增加浏览主题数量，确保记录被收集
MIN_BROWSE_TIME = 60  # 单次浏览最少时间（秒）

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.2088.61'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864},
    {'width': 1280, 'height': 720}
]

class CacheManager:
    @staticmethod
    def load_cache(file_name):
        try:
            if os.path.exists(file_name):
                # 检查缓存是否过期（24小时）
                file_time = os.path.getmtime(file_name)
                if datetime.now().timestamp() - file_time < 86400:
                    with open(file_name, "r", encoding='utf-8') as f:
                        data = json.load(f)
                    logger.info(f"加载缓存: {file_name}")
                    return data
                else:
                    logger.info(f"缓存 {file_name} 已过期")
                    os.remove(file_name)
            return None
        except Exception as e:
            logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
            return None

    @staticmethod
    def save_cache(data, file_name):
        try:
            # 添加时间戳
            data['cache_timestamp'] = datetime.now().timestamp()
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
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
    def delete_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        if os.path.exists(file_name):
            os.remove(file_name)
            logger.info(f"已删除缓存: {file_name}")

class CloudflareHandler:
    @staticmethod
    def wait_for_cloudflare(page, timeout=120):
        """等待 Cloudflare 验证通过，延长超时时间"""
        logger.info("检测 Cloudflare 验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                title = page.title
                current_url = page.url
                page_html = page.html
                
                # Cloudflare 验证页面特征
                cloudflare_indicators = [
                    'challenge' in current_url,
                    'checking' in title.lower(),
                    'please wait' in title.lower(),
                    'verifying' in title.lower(),
                    'ddos-guard' in page_html.lower(),
                    'cf-browser-verification' in page_html,
                    'turnstile' in page_html
                ]
                
                if any(cloudflare_indicators):
                    logger.warning("检测到 Cloudflare/Turnstile 验证，等待中...")
                    
                    # 尝试处理Turnstile验证
                    if 'turnstile' in page_html:
                        CloudflareHandler.handle_turnstile_challenge(page)
                    
                    time.sleep(5)
                    continue
                
                # 检查是否包含登录相关元素，表示已通过验证
                login_elements = page.eles('input[type="text"], input[type="password"], #login-account-name, #username')
                if login_elements:
                    logger.success("Cloudflare 验证已通过")
                    return True
                    
                # 检查是否有错误页面
                if 'error' in title.lower() or 'unavailable' in title.lower():
                    logger.error("网站暂时不可用")
                    return False
                    
                time.sleep(3)
                
            except Exception as e:
                logger.debug(f"等待 Cloudflare 时出错: {str(e)}")
                time.sleep(3)
        
        logger.warning("Cloudflare 等待超时，继续执行")
        return False

    @staticmethod
    def handle_turnstile_challenge(page):
        """处理 Turnstile 验证 - 通过注入JS获取token"""
        try:
            logger.info("尝试处理 Turnstile 验证...")
            
            # 检查是否有Turnstile容器
            turnstile_container = page.ele('.cf-turnstile')
            if turnstile_container:
                logger.info("找到Turnstile容器，尝试注入JS获取token")
                
                # 注入JS获取Turnstile响应
                js_script = """
                function getTurnstileToken() {
                    if (window.turnstile) {
                        return new Promise((resolve) => {
                            turnstile.render('.cf-turnstile', {
                                callback: function(token) {
                                    resolve(token);
                                }
                            });
                        });
                    }
                    return null;
                }
                getTurnstileToken();
                """
                
                # 执行JS并获取token
                token = page.run_js(js_script, timeout=30)
                
                if token:
                    logger.success(f"成功获取Turnstile token: {token[:20]}...")
                    
                    # 查找并设置响应字段
                    response_field = page.ele('input[name="cf-turnstile-response"]')
                    if response_field:
                        response_field.input(token)
                        logger.info("已设置cf-turnstile-response字段")
                        return True
                    else:
                        logger.warning("未找到cf-turnstile-response字段，手动创建并添加到表单")
                        page.run_js("""
                            const input = document.createElement('input');
                            input.type = 'hidden';
                            input.name = 'cf-turnstile-response';
                            input.value = arguments[0];
                            document.forms[0].appendChild(input);
                        """, token)
                        return True
            
            # 尝试点击验证框
            checkboxes = page.eles('.cf-turnstile, [data-sitekey], .challenge-form')
            for checkbox in checkboxes:
                if checkbox and checkbox.is_displayed():
                    try:
                        checkbox.click()
                        logger.info("已点击验证框")
                        time.sleep(5)
                        break
                    except Exception as e:
                        logger.debug(f"点击验证框失败: {str(e)}")
            
            # 等待验证完成
            for i in range(30):
                time.sleep(2)
                # 检查验证是否完成
                page_html = page.html
                if 'cf-turnstile-response' in page_html:
                    logger.success("Turnstile 验证可能已完成")
                    return True
                    
            logger.warning("Turnstile 验证处理超时")
            return False
            
        except Exception as e:
            logger.error(f"处理 Turnstile 验证失败: {str(e)}")
            return False

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.page = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.csrf_token = None
        self.start_time = None  # 用于跟踪浏览总时间
        
    def setup_browser(self):
        """设置浏览器配置，更像真实浏览器"""
        try:
            co = ChromiumOptions()
            
            # 设置用户代理
            user_agent = random.choice(USER_AGENTS)
            co.set_user_agent(user_agent)
            
            # 设置视口大小
            viewport = random.choice(VIEWPORT_SIZES)
            co.set_argument(f"--window-size={viewport['width']},{viewport['height']}")
            
            # 禁用自动化特征
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-web-security")
            co.set_argument("--allow-running-insecure-content")
            co.set_argument("--disable-extensions")
            co.set_argument("--disable-infobars")
            co.set_argument("--disable-notifications")
            co.set_argument("--remote-debugging-port=9222")
            
            # 随机设置语言
            languages = ["en-US,en;q=0.9", "zh-CN,zh;q=0.9", "zh-TW,zh;q=0.8"]
            co.set_argument(f"--lang={random.choice(languages)}")
            
            # 模拟真实浏览器指纹
            co.set_argument("--disable-features=UserAgentClientHint")
            
            if HEADLESS_MODE:
                co.headless()
                # 无头模式下添加更多参数模拟真实环境
                co.set_argument("--window-position=0,0")
            
            # 初始化浏览器
            self.page = ChromiumPage(addr_driver_opts=co)
            self.page.set.timeouts(page_load=PAGE_TIMEOUT)
            
            # 设置额外的浏览器属性，避免被检测为自动化
            self.page.run_js("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            """)
            
            logger.info(f"浏览器已初始化: {user_agent}")
            return True
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {str(e)}")
            return False

    def run_for_site(self):
        """执行站点自动化流程"""
        if not self.credentials.get('username') or not self.credentials.get('password'):
            logger.error(f"{self.site_config['name']} 用户名或密码未设置")
            return False

        try:
            self.start_time = time.time()
            
            if not self.setup_browser():
                return False

            # 尝试使用缓存登录
            cached_login = self.try_cached_login()
            
            if cached_login:
                logger.success(f"{self.site_config['name']} 缓存登录成功")
                self.perform_browsing_actions()
                self.print_connect_info()
                self.save_session_data()
                return True
            else:
                # 执行完整登录流程
                login_success = self.smart_login_approach()
                
                if login_success:
                    logger.success(f"{self.site_config['name']} 登录成功")
                    self.perform_browsing_actions()
                    self.print_connect_info()
                    self.save_session_data()
                    return True
                else:
                    logger.error(f"{self.site_config['name']} 登录失败")
                    # 登录失败时删除缓存，避免下次使用无效缓存
                    CacheManager.delete_site_cache(self.site_config['name'], 'browser_state')
                    return False

        except Exception as e:
            logger.error(f"{self.site_config['name']} 执行异常: {str(e)}", exc_info=True)
            return False
        finally:
            self.cleanup()

    def try_cached_login(self):
        """尝试使用缓存登录，增强验证"""
        try:
            # 加载浏览器状态
            state_data = CacheManager.load_site_cache(self.site_config['name'], 'browser_state')
            if state_data and 'cookies' in state_data:
                logger.info("尝试使用缓存登录...")
                
                # 清除现有cookies
                self.page.clear_cookies()
                
                # 设置缓存cookies
                for cookie in state_data['cookies']:
                    try:
                        # 修复可能的cookie格式问题
                        if 'sameSite' not in cookie:
                            cookie['sameSite'] = 'Lax'
                        self.page.set.cookies(cookie)
                    except Exception as e:
                        logger.debug(f"设置cookie失败: {cookie.get('name')} - {str(e)}")
                
                # 访问网站验证登录状态
                self.page.get(self.site_config['latest_topics_url'])
                time.sleep(random.uniform(3, 5))
                
                # 验证登录状态
                if self.check_login_status():
                    logger.success("缓存登录验证成功")
                    return True
                else:
                    logger.warning("缓存登录验证失败，需要重新登录")
                    CacheManager.delete_site_cache(self.site_config['name'], 'browser_state')
                    
            return False
            
        except Exception as e:
            logger.warning(f"缓存登录尝试失败: {str(e)}")
            CacheManager.delete_site_cache(self.site_config['name'], 'browser_state')
            return False

    def smart_login_approach(self):
        """智能登录方法，增加重试和恢复机制"""
        for attempt in range(RETRY_TIMES):
            logger.info(f"登录尝试 {attempt + 1}/{RETRY_TIMES}")
            
            try:
                if self.full_login_process():
                    return True
                    
            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")
                
            if attempt < RETRY_TIMES - 1:
                self.clear_cache()
                # 指数退避等待
                wait_time = 10 * (attempt + 1)
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                
        return False

    def full_login_process(self):
        """完整登录流程，增强动态内容处理"""
        try:
            logger.info("开始完整登录流程")
            
            # 访问登录页面
            self.page.get(self.site_config['login_url'])
            time.sleep(random.uniform(3, 5))
            
            # 处理 Cloudflare 验证
            if not CloudflareHandler.wait_for_cloudflare(self.page):
                logger.warning("Cloudflare 验证可能未完全通过，继续尝试...")
            
            # 检查并处理 Turnstile 验证
            if self.detect_turnstile_challenge():
                CloudflareHandler.handle_turnstile_challenge(self.page)
            
            # 分析登录页面状态，打印检测到的元素
            self.analyze_login_page()
            
            if not self.wait_for_login_form():
                logger.error("登录表单加载失败")
                return False
            
            # 获取 CSRF Token (Discourse 必需)
            self.extract_csrf_token()
            if not self.csrf_token:
                logger.warning("未找到CSRF Token，尝试继续登录")
            
            username = self.credentials['username']
            password = self.credentials['password']
            
            # 模拟人类输入
            self.fill_login_form_with_behavior(username, password)
            
            # 如果存在CSRF Token字段但未自动填充，手动设置
            if self.csrf_token:
                csrf_fields = self.page.eles('input[name="authenticity_token"], input[name="csrf_token"]')
                for field in csrf_fields:
                    if field and field.is_displayed() and not field.attr('value'):
                        field.input(self.csrf_token)
                        logger.info("已手动设置CSRF Token字段")
            
            if not self.submit_login():
                return False
            
            # 验证登录结果，必须检测到用户名
            return self.verify_login_result()
            
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}", exc_info=True)
            return False

    def detect_turnstile_challenge(self):
        """检测 Turnstile 验证"""
        try:
            page_html = self.page.html
            turnstile_indicators = [
                'cf-turnstile' in page_html,
                'challenges.cloudflare.com' in page_html,
                'turnstile' in page_html.lower(),
                'cloudflare challenge' in page_html.lower()
            ]
            return any(turnstile_indicators)
        except Exception as e:
            logger.debug(f"检测 Turnstile 验证失败: {str(e)}")
            return False

    def analyze_login_page(self):
        """分析登录页面状态，打印检测到的机器人验证和登录元素"""
        try:
            logger.info("分析登录页面元素...")
            
            # 检查机器人验证元素
            bot_detection_selectors = [
                ('.cf-turnstile', 'Cloudflare Turnstile'),
                ('.g-recaptcha', 'Google reCAPTCHA'),
                ('[data-sitekey]', '验证码SiteKey'),
                ('.h-captcha', 'hCaptcha'),
                ('.challenge-form', '挑战表单'),
                ('#cf-challenge', 'Cloudflare挑战'),
                ('.verification-form', '验证表单')
            ]
            
            found_bot_elements = []
            for selector, name in bot_detection_selectors:
                elements = self.page.eles(selector)
                if elements:
                    found_bot_elements.append(name)
                    logger.warning(f"发现机器人验证: {name}")
            
            if found_bot_elements:
                logger.warning(f"页面包含的机器人验证: {', '.join(found_bot_elements)}")
            else:
                logger.info("未发现明显的机器人验证元素")
            
            # 检查登录相关元素
            login_selectors = [
                ('#login-account-name', '用户名输入框'),
                ('#username', '用户名输入框'),
                ('input[name="username"]', '用户名输入框'),
                ('input[type="text"]', '文本输入框'),
                ('#login-account-password', '密码输入框'),
                ('#password', '密码输入框'),
                ('input[name="password"]', '密码输入框'),
                ('input[type="password"]', '密码输入框'),
                ('#login-button', '登录按钮'),
                ('button[type="submit"]', '提交按钮'),
                ('input[type="submit"]', '提交按钮')
            ]
            
            found_login_elements = []
            for selector, name in login_selectors:
                elements = self.page.eles(selector)
                if elements:
                    found_login_elements.append(name)
                    logger.info(f"发现登录元素: {name}")
            
            logger.info(f"登录页面分析完成，发现 {len(found_login_elements)} 个登录相关元素")
            
        except Exception as e:
            logger.error(f"登录页面分析失败: {str(e)}")

    def wait_for_login_form(self, max_wait=60):
        """等待登录表单加载，增加超时时间"""
        logger.info("等待登录表单加载...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                username_selectors = [
                    '#login-account-name',
                    '#username',
                    'input[name="username"]',
                    'input[type="text"]'
                ]
                
                for selector in username_selectors:
                    element = self.page.ele(selector, timeout=2)
                    if element and element.is_displayed():
                        logger.success(f"找到登录表单: {selector}")
                        return True
                
                # 检查是否有错误或验证页面
                if self.detect_turnstile_challenge():
                    logger.info("检测到验证页面，等待处理...")
                    time.sleep(5)
                    continue
                    
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                time.sleep(2)
        
        logger.error("登录表单等待超时")
        return False

    def extract_csrf_token(self):
        """提取 CSRF Token (Discourse 必需)"""
        try:
            # 从 meta 标签提取
            meta_token = self.page.ele('meta[name="csrf-token"]')
            if meta_token:
                self.csrf_token = meta_token.attr('content')
                logger.info(f"找到 CSRF Token: {self.csrf_token[:20]}...")
                return True
            
            # 从 input 字段提取
            input_token = self.page.ele('input[name="authenticity_token"]')
            if input_token:
                self.csrf_token = input_token.attr('value')
                logger.info(f"找到 Authenticity Token: {self.csrf_token[:20]}...")
                return True
                
            # 从JS变量提取
            page_html = self.page.html
            match = re.search(r'csrfToken\s*=\s*"([^"]+)"', page_html)
            if match:
                self.csrf_token = match.group(1)
                logger.info(f"从JS中找到 CSRF Token: {self.csrf_token[:20]}...")
                return True
                
            logger.warning("未找到 CSRF Token，可能不需要或隐藏在其他地方")
            return False
            
        except Exception as e:
            logger.warning(f"提取 CSRF Token 失败: {str(e)}")
            return False

    def fill_login_form_with_behavior(self, username, password):
        """模拟人类行为填写登录表单，更真实的输入模式"""
        try:
            logger.info("模拟人类行为填写登录表单...")
            
            # 随机鼠标移动到表单区域
            self.random_mouse_movement()
            
            # 查找用户名输入框
            username_selectors = ['#login-account-name', '#username', 'input[name="username"]', 'input[type="text"]']
            username_field = None
            
            for selector in username_selectors:
                element = self.page.ele(selector)
                if element and element.is_displayed():
                    username_field = element
                    break
            
            if username_field:
                # 模拟人类输入用户名
                self.simulate_human_typing(username_field, username)
                logger.info("已填写用户名")
                time.sleep(random.uniform(0.8, 1.5))
                
                # 随机停顿后可能点击其他地方
                if random.random() > 0.5:
                    self.page.ele('body').click()
                    time.sleep(random.uniform(0.3, 0.7))
                    username_field.click()
                    time.sleep(random.uniform(0.2, 0.5))
            
            # 查找密码输入框
            password_selectors = ['#login-account-password', '#password', 'input[name="password"]', 'input[type="password"]']
            password_field = None
            
            for selector in password_selectors:
                element = self.page.ele(selector)
                if element and element.is_displayed():
                    password_field = element
                    break
            
            if password_field:
                # 模拟人类输入密码
                self.simulate_human_typing(password_field, password)
                logger.info("已填写密码")
                time.sleep(random.uniform(0.8, 1.5))
            
        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    def simulate_human_typing(self, element, text):
        """模拟更真实的人类输入，包括可能的错误和修正"""
        try:
            element.click()
            time.sleep(random.uniform(0.3, 0.7))
            
            typed_text = ""
            for i, char in enumerate(text):
                # 随机概率犯错然后修正
                if random.random() < 0.05 and i > 0:
                    # 删除最后一个字符
                    element.input('\b')
                    typed_text = typed_text[:-1]
                    time.sleep(random.uniform(0.2, 0.5))
                
                element.input(char)
                typed_text += char
                # 随机延时，模拟人类输入节奏，包含思考时间
                delay = random.uniform(0.05, 0.2)
                # 空格键和标点符号后停顿更长
                if char in [' ', '.', ',', '!', '?']:
                    delay += random.uniform(0.1, 0.3)
                time.sleep(delay)
                
        except Exception as e:
            # 如果逐字输入失败，尝试一次性输入
            try:
                logger.warning(f"逐字输入失败，尝试一次性输入: {str(e)}")
                element.input(text)
            except Exception as e2:
                logger.error(f"输入文本失败: {str(e2)}")

    def submit_login(self):
        """提交登录，增加多种提交方式"""
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
                button = self.page.ele(selector)
                if button and button.is_displayed():
                    logger.info(f"找到登录按钮: {selector}")
                    
                    # 模拟人类点击前的小延迟和鼠标移动
                    self.move_mouse_to_element(button)
                    time.sleep(random.uniform(0.5, 1.5))
                    
                    # 可能先悬停再点击
                    button.hover()
                    time.sleep(random.uniform(0.2, 0.5))
                    
                    button.click()
                    logger.info("已点击登录按钮")
                    
                    # 等待登录处理，Discourse登录可能需要更长时间
                    time.sleep(random.uniform(5, 10))
                    return True
            
            # 如果找不到按钮，尝试按Enter键提交
            logger.info("未找到登录按钮，尝试按Enter键提交")
            self.page.press('Enter')
            time.sleep(random.uniform(5, 10))
            return True
            
        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    def verify_login_result(self):
        """验证登录结果，必须检测到用户名"""
        logger.info("验证登录结果...")
        
        # 检查是否跳转到其他页面
        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info(f"页面已跳转到: {current_url}")
        
        # 检查错误信息
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger', '.login-error']
        for selector in error_selectors:
            error_element = self.page.ele(selector)
            if error_element and error_element.is_displayed():
                error_text = error_element.text
                logger.error(f"登录错误: {error_text}")
                return False
        
        # 必须检测到用户名才算成功
        return self.check_login_status()

    def check_login_status(self):
        """检查登录状态 - 必须检测到用户名"""
        username = self.credentials['username']
        if not username:
            logger.error("用户名为空，无法验证")
            return False
            
        logger.info(f"验证用户名: {username}")
        
        # 尝试多种方法检测用户名
        detection_methods = [
            self._check_username_in_page_content,
            self._check_username_in_elements,
            self._check_username_in_profile
        ]
        
        for method in detection_methods:
            if method(username):
                self.is_logged_in = True
                return True
        
        logger.error(f"未在页面中找到用户名: {username}，登录失败")
        return False
    
    def _check_username_in_page_content(self, username):
        """检查页面内容中是否包含用户名"""
        try:
            content = self.page.html.lower()
            if username.lower() in content:
                logger.success(f"在页面内容中找到用户名: {username}")
                return True
            return False
        except Exception as e:
            logger.debug(f"检查页面内容中的用户名失败: {str(e)}")
            return False
    
    def _check_username_in_elements(self, username):
        """检查特定元素中是否包含用户名"""
        try:
            user_indicators = [
                f'[data-username="{username}"]',
                f'[title="{username}"]',
                f'.username[data-username="{username}"]',
                f'a[href*="/u/{username}"]',
                f'.current-user:contains("{username}")',
                f'.user-menu:contains("{username}")'
            ]
            
            for selector in user_indicators:
                element = self.page.ele(selector)
                if element and element.is_displayed():
                    logger.success(f"找到用户元素: {selector}")
                    return True
            return False
        except Exception as e:
            logger.debug(f"检查元素中的用户名失败: {str(e)}")
            return False
    
    def _check_username_in_profile(self, username):
        """访问个人资料页面检查用户名"""
        try:
            profile_url = self.site_config['profile_url'].format(username=username)
            logger.info(f"访问个人资料页面验证: {profile_url}")
            
            self.page.get(profile_url)
            time.sleep(random.uniform(3, 5))
            
            profile_content = self.page.html.lower()
            if username.lower() in profile_content:
                logger.success(f"在个人资料页面验证用户名: {username}")
                # 返回上一页
                self.page.back()
                time.sleep(2)
                return True
            
            return False
        except Exception as e:
            logger.debug(f"检查个人资料中的用户名失败: {str(e)}")
            return False

    def perform_browsing_actions(self):
        """执行浏览行为 - 确保网站收集浏览记录"""
        logger.info("开始浏览主题帖以收集浏览记录...")
        
        # 记录开始时间，确保最少浏览时间
        browse_start_time = time.time()
        
        # 访问最新主题页面
        self.page.get(self.site_config['latest_topics_url'])
        time.sleep(random.uniform(4, 7))
        
        # 随机滚动页面顶部
        self.simulate_human_reading_behavior()
        
        # 获取主题列表
        topic_list = self.page.eles(".topic-list-item")
        if not topic_list:
            topic_list = self.page.eles(".topic-list .topic-list-body tr")
        if not topic_list:
            topic_list = self.page.eles(".posts .post-item")
        
        logger.info(f"发现 {len(topic_list)} 个主题帖，将随机选择浏览")
        
        # 随机选择要浏览的主题数量
        topics_to_browse = random.randint(3, min(len(topic_list), MAX_TOPICS_TO_BROWSE))
        logger.info(f"计划浏览 {topics_to_browse} 个主题帖")
        
        browsed_topics = 0
        
        # 随机选择主题
        for topic in random.sample(topic_list, min(len(topic_list), topics_to_browse)):
            try:
                # 查找主题链接
                link = topic.ele(".title a")
                if not link:
                    link = topic.ele("a.raw-topic-link")
                if not link:
                    link = topic.ele("a.title")
                if not link:
                    link = topic.ele("a")
                
                if link:
                    topic_url = link.attr("href")
                    if topic_url:
                        full_topic_url = urljoin(self.site_config['base_url'], topic_url)
                        
                        # 确保URL有效
                        if full_topic_url.startswith(('http://', 'https://')):
                            logger.info(f"正在浏览: {full_topic_url}")
                            
                            # 鼠标移动到链接并悬停
                            self.move_mouse_to_element(link)
                            time.sleep(random.uniform(0.5, 1.5))
                            
                            self.browse_topic(full_topic_url)
                            browsed_topics += 1
                            
                            # 主题间随机间隔
                            if browsed_topics < topics_to_browse:
                                wait_time = random.uniform(4, 8)
                                logger.info(f"浏览下一个主题前等待 {wait_time:.1f} 秒")
                                time.sleep(wait_time)
            
            except Exception as e:
                logger.error(f"浏览主题帖时出错: {str(e)}")
                continue
        
        # 确保总浏览时间足够长
        elapsed_time = time.time() - browse_start_time
        if elapsed_time < MIN_BROWSE_TIME:
            remaining_time = MIN_BROWSE_TIME - elapsed_time
            logger.info(f"浏览时间不足，额外等待 {remaining_time:.1f} 秒")
            time.sleep(remaining_time)
        
        logger.success(f"完成浏览 {browsed_topics} 个主题帖，浏览记录已收集")

    def browse_topic(self, topic_url):
        """浏览单个主题帖，更真实的阅读行为"""
        try:
            # 访问主题页面
            self.page.get(topic_url)
            time.sleep(random.uniform(3, 6))
            
            # 随机停留时间
            topic_stay_time = random.uniform(40, 90)  # 每个主题停留40-90秒
            start_time = time.time()
            
            # 模拟人类阅读行为
            self.simulate_human_reading_behavior()
            
            # 可能的互动：点赞、查看评论等
            self.possible_topic_interactions()
            
            # 确保在主题页面停留足够时间
            elapsed_time = time.time() - start_time
            if elapsed_time < topic_stay_time:
                remaining_time = topic_stay_time - elapsed_time
                logger.info(f"在主题页面额外停留 {remaining_time:.1f} 秒")
                time.sleep(remaining_time)
            
        except Exception as e:
            logger.error(f"浏览主题帖失败: {str(e)}")

    def simulate_human_reading_behavior(self):
        """模拟更真实的人类阅读行为"""
        try:
            # 随机滚动模式
            scroll_patterns = [
                self.smooth_scroll_reading,
                self.quick_scroll_reading,
                self.detailed_reading,
                self.intermittent_reading
            ]
            
            pattern = random.choice(scroll_patterns)
            logger.debug(f"使用阅读模式: {pattern.__name__}")
            pattern()
            
        except Exception as e:
            logger.error(f"模拟阅读行为失败: {str(e)}")

    def smooth_scroll_reading(self):
        """平滑滚动阅读模式"""
        total_scrolls = random.randint(8, 15)
        for i in range(total_scrolls):
            scroll_distance = random.randint(200, 500)
            self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
            time.sleep(random.uniform(0.8, 1.8))
            
            # 随机鼠标移动和暂停
            if random.random() > 0.6:
                self.random_mouse_movement()
            if random.random() > 0.7:
                time.sleep(random.uniform(1, 3))  # 思考停顿

    def quick_scroll_reading(self):
        """快速滚动浏览模式"""
        # 快速滚动到中部
        self.page.run_js("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(random.uniform(1, 2))
        
        # 随机阅读几处内容
        for _ in range(random.randint(3, 6)):
            scroll_distance = random.randint(200, 400)
            self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
            time.sleep(random.uniform(0.5, 1.2))
            
            # 偶尔停顿
            if random.random() > 0.8:
                time.sleep(random.uniform(1.5, 3))

    def detailed_reading(self):
        """详细阅读模式"""
        # 分段仔细阅读
        segments = random.randint(5, 10)
        for i in range(segments):
            scroll_distance = random.randint(150, 300)
            self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
            
            # 模拟阅读时间，段落越长停留越久
            read_time = random.uniform(2, 5)
            time.sleep(read_time)
            
            # 高概率的鼠标移动和悬停
            if random.random() > 0.3:
                self.random_mouse_movement()
            if random.random() > 0.5:
                self.hover_over_random_element()

    def intermittent_reading(self):
        """间歇性阅读模式（读一会儿停一会儿）"""
        # 先阅读一部分
        self.smooth_scroll_reading()
        
        # 停顿较长时间
        pause_time = random.uniform(3, 7)
        logger.debug(f"阅读暂停 {pause_time:.1f} 秒")
        time.sleep(pause_time)
        
        # 继续阅读
        self.smooth_scroll_reading()

    def possible_topic_interactions(self):
        """可能的主题互动，增加真实性"""
        try:
            # 随机点赞
            if random.random() > 0.8:  # 20%概率点赞
                like_buttons = self.page.eles('.like-button, .btn-like, button.like')
                if like_buttons:
                    like_button = random.choice(like_buttons)
                    if like_button and like_button.is_displayed():
                        self.move_mouse_to_element(like_button)
                        time.sleep(random.uniform(0.3, 0.8))
                        like_button.click()
                        logger.info("模拟点赞行为")
                        time.sleep(random.uniform(1, 2))
            
            # 随机查看评论
            if random.random() > 0.7:  # 30%概率查看评论
                comment_links = self.page.eles('.comments-link, a:has-text("评论"), a:has-text("回复")')
                if comment_links:
                    comment_link = random.choice(comment_links)
                    if comment_link and comment_link.is_displayed():
                        self.move_mouse_to_element(comment_link)
                        time.sleep(random.uniform(0.5, 1))
                        comment_link.click()
                        logger.info("模拟查看评论")
                        time.sleep(random.uniform(3, 6))
                        self.smooth_scroll_reading()
            
        except Exception as e:
            logger.debug(f"模拟互动行为失败: {str(e)}")

    def scroll_to_bottom(self):
        """滚动到页面底部"""
        try:
            # 平滑滚动到底部
            self.page.run_js("""
                const scrollToBottom = () => {
                    const scrollHeight = document.body.scrollHeight;
                    const currentPosition = window.scrollY;
                    const distance = scrollHeight - currentPosition;
                    const step = distance / 20;
                    let position = currentPosition;
                    
                    const timer = setInterval(() => {
                        position += step;
                        window.scrollTo(0, position);
                        if (position >= scrollHeight) {
                            clearInterval(timer);
                        }
                    }, 30);
                };
                scrollToBottom();
            """)
            time.sleep(random.uniform(1, 2))
            logger.debug("已滚动到页面底部")
        except Exception as e:
            logger.debug(f"滚动到底部失败: {str(e)}")

    def random_mouse_movement(self):
        """更真实的随机鼠标移动"""
        try:
            # 获取视口大小
            viewport = self.page.run_js("return {width: window.innerWidth, height: window.innerHeight};")
            if viewport:
                # 随机生成几个点，模拟曲线移动
                points = []
                for _ in range(random.randint(3, 6)):
                    x = random.randint(50, viewport['width'] - 50)
                    y = random.randint(50, viewport['height'] - 50)
                    points.append((x, y))
                
                # 移动到每个点
                for x, y in points:
                    self.page.mouse.move(x, y)
                    time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass

    def move_mouse_to_element(self, element):
        """将鼠标移动到元素位置"""
        try:
            rect = element.rect
            if rect:
                # 计算元素中心位置
                x = rect['x'] + rect['width'] / 2
                y = rect['y'] + rect['height'] / 2
                
                # 随机偏移一点，更像人类行为
                x += random.randint(-10, 10)
                y += random.randint(-10, 10)
                
                # 移动鼠标
                self.page.mouse.move(x, y)
                time.sleep(random.uniform(0.1, 0.3))
        except Exception as e:
            logger.debug(f"移动鼠标到元素失败: {str(e)}")

    def hover_over_random_element(self):
        """随机悬停在页面元素上"""
        try:
            elements = self.page.eles('a, button, .post-body, .topic-title')
            if elements:
                element = random.choice(elements)
                if element and element.is_displayed():
                    self.move_mouse_to_element(element)
                    time.sleep(random.uniform(0.5, 1.5))
        except Exception:
            pass

    def print_connect_info(self):
        """打印连接信息，无需登录"""
        logger.info("获取连接信息...")
        try:
            # connect 页面不需要登录验证，直接访问
            self.page.get(self.site_config['connect_url'])
            time.sleep(random.uniform(3, 5))
            
            # 随机浏览一下页面
            self.simulate_human_reading_behavior()
            
            # 尝试多种表格选择器
            table_selectors = [
                'table',
                '.table',
                '.connect-table',
                '.user-table',
                'div[role="table"]'
            ]
            
            table_found = False
            for selector in table_selectors:
                table = self.page.ele(selector)
                if table and table.is_displayed():
                    table_found = True
                    
                    # 获取所有行
                    rows = table.eles('tr')
                    logger.info(f"发现表格，共 {len(rows)} 行")
                    
                    info = []
                    for row in rows:
                        cells = row.eles('td, th')
                        if len(cells) >= 3:
                            project = cells[0].text if cells[0] else "N/A"
                            current = cells[1].text if cells[1] else "N/A"
                            requirement = cells[2].text if cells[2] else "N/A"
                            info.append([project, current, requirement])
                    
                    if info:
                        logger.info("连接信息:")
                        for item in info:
                            logger.info(f"项目: {item[0]}, 当前: {item[1]}, 要求: {item[2]}")
                    else:
                        logger.info("未找到具体的连接信息")
                    
                    break
            
            if not table_found:
                logger.info("未找到表格，显示页面主要内容:")
                main_content = self.page.ele('main, .container, .wrap, .contents')
                if main_content:
                    content_text = main_content.text
                    if content_text:
                        lines = content_text.split('\n')
                        for line in lines[:10]:  # 只显示前10行
                            if line.strip():
                                logger.info(f"内容: {line.strip()}")
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def save_session_data(self):
        """保存会话数据，确保下次可以使用"""
        try:
            # 获取当前所有cookies
            cookies = self.page.cookies()
            browser_state = {
                'cookies': cookies,
                'url': self.page.url,
                'timestamp': time.time(),
                'username': self.credentials.get('username')
            }
            
            CacheManager.save_site_cache(browser_state, self.site_config['name'], 'browser_state')
            logger.info("会话数据已保存")
            
        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    def clear_cache(self):
        """清除缓存"""
        cache_files = [
            f"browser_state_{self.site_config['name']}.json",
            f"cf_cookies_{self.site_config['name']}.json"
        ]
        
        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"清除缓存: {file}")

    def cleanup(self):
        """清理资源"""
        try:
            if self.page:
                # 随机关闭前的浏览行为
                if self.is_logged_in and random.random() > 0.3:
                    self.page.get(self.site_config['base_url'])
                    time.sleep(random.uniform(2, 4))
                
                self.page.quit()
                logger.info("浏览器已关闭")
        except Exception as e:
            logger.debug(f"清理资源时出错: {str(e)}")

def main():
    """主函数"""
    try:
        logger.info("LinuxDo 多站点自动化脚本启动")
        
        # 确定目标站点
        target_sites = SITES
        site_selector = os.getenv('SITE_SELECTOR', 'all').lower()
        
        if site_selector != 'all':
            target_sites = [site for site in SITES if site['name'] == site_selector]
            if not target_sites:
                logger.error(f"未找到站点: {site_selector}")
                return False
        
        results = []
        
        for site_config in target_sites:
            logger.info(f"===== 开始处理站点: {site_config['name']} =====")
            
            automator = SiteAutomator(site_config)
            success = automator.run_for_site()
            
            results.append({
                'site': site_config['name'],
                'success': success
            })
            
            # 站点间延迟（最后一个站点不需要）
            if site_config != target_sites[-1]:
                delay = random.uniform(15, 30)
                logger.info(f"等待 {delay:.1f} 秒后处理下一个站点...")
                time.sleep(delay)
        
        # 输出最终结果
        logger.info("===== 执行结果汇总 =====")
        for result in results:
            status = "成功" if result['success'] else "失败"
            logger.info(f"站点: {result['site']}, 状态: {status}")
        
        success_count = sum(1 for r in results if r['success'])
        logger.success(f"完成: {success_count}/{len(results)} 个站点成功")
        
        return success_count == len(results)
        
    except Exception as e:
        logger.critical(f"主流程异常: {str(e)}", exc_info=True)
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
