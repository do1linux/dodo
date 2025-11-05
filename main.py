from DrissionPage import ChromiumPage, ChromiumOptions
from loguru import logger
import random
import json
import os
import time
from urllib.parse import urljoin
import re
import sys

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
HEADLESS_MODE = True

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json",
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json",
    }
]

PAGE_TIMEOUT = 120
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 5

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

class CacheManager:
    @staticmethod
    def load_cache(file_name):
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"加载缓存: {file_name}")
                return data
            return None
        except Exception as e:
            logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
            return None

    @staticmethod
    def save_cache(data, file_name):
        try:
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

class CloudflareHandler:
    @staticmethod
    def wait_for_cloudflare(page, timeout=60):
        """等待 Cloudflare 验证通过"""
        logger.info("检测 Cloudflare 验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 检查是否有 Cloudflare 挑战页面
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

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.page = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.csrf_token = None
        
    def setup_browser(self):
        """设置浏览器配置 - 使用正确的 DrissionPage API"""
        try:
            logger.info("初始化浏览器...")
            
            # 创建配置对象
            co = ChromiumOptions()
            
            # 设置用户代理
            user_agent = random.choice(USER_AGENTS)
            co.set_user_agent(user_agent)
            
            # 在 GitHub Actions 环境中必须的配置
            # 使用正确的 API 方法
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-dev-shm-usage')
            co.set_argument('--disable-gpu')
            co.set_argument('--disable-web-security')
            co.set_argument('--allow-running-insecure-content')
            co.set_argument('--disable-blink-features=AutomationControlled')
            co.set_argument('--disable-extensions')
            
            if HEADLESS_MODE:
                co.set_argument('--headless=new')
            
            # 设置远程调试端口
            co.set_local_port(9222)
            
            # 使用正确的参数名
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 设置超时
            self.page.set.timeouts(base=PAGE_TIMEOUT)
            
            logger.info(f"浏览器已初始化: {user_agent}")
            return True
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {str(e)}")
            # 尝试备用方案
            return self.setup_browser_fallback()
    
    def setup_browser_fallback(self):
        """备用浏览器初始化方案 - 使用字符串参数"""
        try:
            logger.info("尝试备用浏览器初始化...")
            
            # 构建参数字符串
            args = []
            args.append('--no-sandbox')
            args.append('--disable-dev-shm-usage')
            args.append('--disable-gpu')
            args.append('--disable-web-security')
            args.append('--allow-running-insecure-content')
            args.append('--disable-blink-features=AutomationControlled')
            args.append('--disable-extensions')
            
            if HEADLESS_MODE:
                args.append('--headless=new')
            
            # 将参数列表转换为字符串
            args_str = ' '.join(args)
            
            # 设置用户代理
            user_agent = random.choice(USER_AGENTS)
            
            # 使用参数字符串初始化
            self.page = ChromiumPage(addr_or_opts=args_str)
            
            # 设置用户代理
            self.page.set.user_agent(user_agent)
            
            # 设置超时
            self.page.set.timeouts(base=PAGE_TIMEOUT)
            
            logger.info(f"备用浏览器初始化成功: {user_agent}")
            return True
            
        except Exception as e:
            logger.error(f"备用浏览器初始化也失败: {str(e)}")
            # 最后尝试最简单的初始化
            return self.setup_browser_simple()

    def setup_browser_simple(self):
        """最简单的浏览器初始化方式"""
        try:
            logger.info("尝试最简单的浏览器初始化...")
            
            # 直接创建页面，使用默认配置
            self.page = ChromiumPage()
            self.page.set.timeouts(base=PAGE_TIMEOUT)
            
            logger.info("简单浏览器初始化成功")
            return True
            
        except Exception as e:
            logger.error(f"简单浏览器初始化也失败: {str(e)}")
            return False

    def run_for_site(self):
        """执行站点自动化流程"""
        if not self.credentials.get('username'):
            logger.error(f"{self.site_config['name']} 用户名未设置")
            return False

        try:
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
                    return False

        except Exception as e:
            logger.error(f"{self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            self.cleanup()

    def try_cached_login(self):
        """尝试使用缓存登录"""
        try:
            # 加载浏览器状态
            state_data = CacheManager.load_site_cache(self.site_config['name'], 'browser_state')
            if state_data and 'cookies' in state_data:
                logger.info("尝试使用缓存登录...")
                
                # 设置 cookies
                for cookie in state_data['cookies']:
                    try:
                        self.page.set.cookies(cookie)
                    except Exception as e:
                        logger.debug(f"设置cookie失败: {str(e)}")
                
                # 访问网站验证登录状态
                self.page.get(self.site_config['latest_topics_url'])
                time.sleep(5)
                
                if self.check_login_status():
                    logger.success("缓存登录验证成功")
                    return True
                else:
                    logger.warning("缓存登录验证失败，需要重新登录")
                    
            return False
            
        except Exception as e:
            logger.warning(f"缓存登录尝试失败: {str(e)}")
            return False

    def smart_login_approach(self):
        """智能登录方法"""
        for attempt in range(RETRY_TIMES):
            logger.info(f"登录尝试 {attempt + 1}/{RETRY_TIMES}")
            
            try:
                if self.full_login_process():
                    return True
                    
            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")
                
            if attempt < RETRY_TIMES - 1:
                self.clear_cache()
                time.sleep(10 * (attempt + 1))
                
        return False

    def full_login_process(self):
        """完整登录流程"""
        try:
            logger.info("开始完整登录流程")
            
            # 访问登录页面
            self.page.get(self.site_config['login_url'])
            time.sleep(5)
            
            # 处理 Cloudflare 验证
            if not CloudflareHandler.wait_for_cloudflare(self.page):
                logger.warning("Cloudflare 验证可能未完全通过，继续尝试...")
            
            # 分析登录页面状态
            self.analyze_login_page()
            
            if not self.wait_for_login_form():
                logger.error("登录表单加载失败")
                return False
            
            # 获取 CSRF Token
            self.extract_csrf_token()
            
            username = self.credentials['username']
            password = self.credentials['password']
            
            # 模拟人类输入
            self.fill_login_form_with_behavior(username, password)
            
            if not self.submit_login():
                return False
            
            return self.verify_login_result()
            
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    def analyze_login_page(self):
        """分析登录页面状态"""
        try:
            logger.info("分析登录页面...")
            
            # 检查机器人验证元素
            bot_detection_selectors = [
                '.cf-turnstile',
                '.g-recaptcha',
                '[data-sitekey]',
                '.h-captcha',
                '.challenge-form',
                '#cf-challenge',
                '.verification-form'
            ]
            
            found_bot_elements = []
            for selector in bot_detection_selectors:
                elements = self.page.eles(selector)
                if elements:
                    found_bot_elements.append(selector)
                    logger.warning(f"发现机器人验证元素: {selector}")
            
            if found_bot_elements:
                logger.warning(f"页面包含以下机器人验证: {', '.join(found_bot_elements)}")
            else:
                logger.info("未发现明显的机器人验证元素")
            
            # 检查登录相关元素
            login_selectors = [
                '#login-account-name',
                '#username', 
                'input[name="username"]',
                'input[type="text"]',
                '#login-account-password',
                '#password',
                'input[name="password"]',
                'input[type="password"]',
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]'
            ]
            
            found_login_elements = []
            for selector in login_selectors:
                elements = self.page.eles(selector)
                if elements:
                    found_login_elements.append(selector)
                    logger.info(f"发现登录元素: {selector}")
            
            logger.info(f"登录页面分析完成，发现 {len(found_login_elements)} 个登录相关元素")
            
        except Exception as e:
            logger.error(f"登录页面分析失败: {str(e)}")

    def wait_for_login_form(self, max_wait=30):
        """等待登录表单加载"""
        logger.info("等待登录表单...")
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
                    element = self.page.ele(selector)
                    if element and element.is_displayed:
                        logger.success(f"找到登录表单: {selector}")
                        return True
                
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                time.sleep(2)
        
        logger.error("登录表单等待超时")
        return False

    def extract_csrf_token(self):
        """提取 CSRF Token"""
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
                
            logger.warning("未找到 CSRF Token，可能不需要")
            return False
            
        except Exception as e:
            logger.warning(f"提取 CSRF Token 失败: {str(e)}")
            return False

    def fill_login_form_with_behavior(self, username, password):
        """模拟人类行为填写登录表单"""
        try:
            logger.info("模拟人类行为填写登录表单...")
            
            # 查找用户名输入框
            username_selectors = ['#login-account-name', '#username', 'input[name="username"]']
            username_field = None
            
            for selector in username_selectors:
                element = self.page.ele(selector)
                if element and element.is_displayed:
                    username_field = element
                    break
            
            if username_field:
                # 模拟人类输入用户名
                self.simulate_human_typing(username_field, username)
                logger.info("已填写用户名")
                time.sleep(random.uniform(1, 2))
            
            # 查找密码输入框
            password_selectors = ['#login-account-password', '#password', 'input[name="password"]']
            password_field = None
            
            for selector in password_selectors:
                element = self.page.ele(selector)
                if element and element.is_displayed:
                    password_field = element
                    break
            
            if password_field:
                # 模拟人类输入密码
                self.simulate_human_typing(password_field, password)
                logger.info("已填写密码")
                time.sleep(random.uniform(1, 2))
            
        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    def simulate_human_typing(self, element, text):
        """模拟人类输入"""
        try:
            element.click()
            time.sleep(0.5)
            
            for char in text:
                element.input(char)
                # 随机延时，模拟人类输入节奏
                time.sleep(random.uniform(0.05, 0.2))
                
        except Exception as e:
            # 如果逐字输入失败，尝试一次性输入
            try:
                element.input(text)
            except Exception as e2:
                logger.error(f"输入文本失败: {str(e2)}")

    def submit_login(self):
        """提交登录"""
        try:
            login_buttons = [
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]'
            ]
            
            # 先尝试通过选择器查找
            for selector in login_buttons:
                button = self.page.ele(selector)
                if button and button.is_displayed:
                    logger.info(f"找到登录按钮: {selector}")
                    time.sleep(random.uniform(0.5, 1.5))
                    button.click()
                    logger.info("已点击登录按钮")
                    time.sleep(8)
                    return True
            
            # 然后尝试通过文本查找
            button_texts = ['登录', 'Log In', 'Sign In', 'Login']
            for text in button_texts:
                buttons = self.page.eles(f'button:contains("{text}")')
                if buttons:
                    for button in buttons:
                        if button.is_displayed:
                            logger.info(f"找到登录按钮(文本): {text}")
                            time.sleep(random.uniform(0.5, 1.5))
                            button.click()
                            logger.info("已点击登录按钮")
                            time.sleep(8)
                            return True
            
            logger.error("未找到登录按钮")
            return False
            
        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    def verify_login_result(self):
        """验证登录结果"""
        logger.info("验证登录结果...")
        
        # 检查是否跳转到其他页面
        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info(f"页面已跳转到: {current_url}")
        
        # 检查错误信息
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger', '.login-error']
        for selector in error_selectors:
            error_element = self.page.ele(selector)
            if error_element and error_element.is_displayed:
                error_text = error_element.text
                logger.error(f"登录错误: {error_text}")
                return False
        
        # 必须检测到用户名才算成功
        return self.check_login_status()

    def check_login_status(self):
        """检查登录状态 - 必须检测到用户名"""
        try:
            username = self.credentials['username']
            logger.info(f"验证用户名: {username}")
            
            # 方法1: 在页面内容中搜索用户名
            content = self.page.html
            if username.lower() in content.lower():
                logger.success(f"在页面内容中找到用户名: {username}")
                return True
            
            # 方法2: 检查用户头像或菜单
            user_indicators = [
                f'[data-username="{username}"]',
                f'[title="{username}"]',
                f'.username[data-username="{username}"]'
            ]
            
            for selector in user_indicators:
                element = self.page.ele(selector)
                if element and element.is_displayed:
                    logger.success(f"找到用户元素: {selector}")
                    return True
            
            # 方法3: 查找包含用户名的链接
            user_links = self.page.eles(f'a[href*="/u/{username}"]')
            for link in user_links:
                if link and link.is_displayed:
                    logger.success(f"找到用户链接: {link.attr('href')}")
                    return True
            
            # 方法4: 访问个人资料页面验证
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            current_url = self.page.url
            self.page.get(profile_url)
            time.sleep(3)
            
            profile_content = self.page.html
            if username.lower() in profile_content.lower():
                logger.success(f"在个人资料页面验证用户名: {username}")
                # 返回原页面
                self.page.get(current_url)
                time.sleep(2)
                return True
            
            logger.error(f"未在页面中找到用户名: {username}")
            return False
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    def perform_browsing_actions(self):
        """执行浏览行为 - 核心功能：让网站收集浏览记录"""
        logger.info("开始浏览主题帖以收集浏览记录...")
        
        # 访问最新主题页面
        self.page.get(self.site_config['latest_topics_url'])
        time.sleep(5)
        
        # 获取主题列表
        topic_list = self.page.eles(".topic-list-item")
        if not topic_list:
            topic_list = self.page.eles(".topic-list .topic-list-body tr")
        
        logger.info(f"发现 {len(topic_list)} 个主题帖，将随机选择浏览")
        
        browsed_topics = 0
        topics_to_browse = min(len(topic_list), MAX_TOPICS_TO_BROWSE)
        
        # 随机选择主题
        selected_topics = random.sample(topic_list, topics_to_browse) if topic_list else []
        
        for topic in selected_topics:
            try:
                # 查找主题链接
                link = topic.ele(".title a")
                if not link:
                    link = topic.ele("a.raw-topic-link")
                if not link:
                    link = topic.ele("a.title")
                
                if link:
                    topic_url = link.attr("href")
                    if topic_url:
                        full_topic_url = urljoin(self.site_config['base_url'], topic_url)
                        
                        logger.info(f"正在浏览: {full_topic_url}")
                        self.browse_topic(full_topic_url)
                        browsed_topics += 1
                        
                        # 主题间随机间隔
                        if browsed_topics < topics_to_browse:
                            time.sleep(random.uniform(3, 8))
                
            except Exception as e:
                logger.error(f"浏览主题帖时出错: {str(e)}")
                continue
        
        logger.success(f"完成浏览 {browsed_topics} 个主题帖，浏览记录已收集")

    def browse_topic(self, topic_url):
        """浏览单个主题帖"""
        try:
            self.page.get(topic_url)
            time.sleep(random.uniform(3, 6))
            
            # 模拟人类浏览行为
            self.simulate_human_reading_behavior()
            
            # 随机决定是否滚动到页面底部
            if random.random() > 0.3:  # 70%的概率滚动到底部
                self.scroll_to_bottom()
            
        except Exception as e:
            logger.error(f"浏览主题帖失败: {str(e)}")

    def simulate_human_reading_behavior(self):
        """模拟人类阅读行为"""
        try:
            # 随机滚动模式
            scroll_patterns = [
                self.smooth_scroll_reading,
                self.quick_scroll_reading,
                self.detailed_reading
            ]
            
            pattern = random.choice(scroll_patterns)
            pattern()
            
        except Exception as e:
            logger.error(f"模拟阅读行为失败: {str(e)}")

    def smooth_scroll_reading(self):
        """平滑滚动阅读模式"""
        total_scrolls = random.randint(8, 15)
        for i in range(total_scrolls):
            scroll_distance = random.randint(300, 600)
            self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
            time.sleep(random.uniform(0.8, 1.5))

    def quick_scroll_reading(self):
        """快速滚动浏览模式"""
        # 快速滚动到中部
        self.page.run_js("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(random.uniform(1, 2))
        
        # 随机阅读几处内容
        for _ in range(random.randint(3, 6)):
            scroll_distance = random.randint(200, 400)
            self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
            time.sleep(random.uniform(0.5, 1))

    def detailed_reading(self):
        """详细阅读模式"""
        # 分段仔细阅读
        segments = random.randint(5, 10)
        for i in range(segments):
            scroll_distance = random.randint(150, 300)
            self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
            
            # 模拟阅读时间
            read_time = random.uniform(2, 4)
            time.sleep(read_time)

    def scroll_to_bottom(self):
        """滚动到页面底部"""
        try:
            self.page.run_js("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(random.uniform(1, 2))
            logger.debug("已滚动到页面底部")
        except Exception as e:
            logger.debug(f"滚动到底部失败: {str(e)}")

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("获取连接信息...")
        try:
            # connect 页面不需要登录验证，直接访问
            self.page.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 尝试多种表格选择器
            table_selectors = [
                'table',
                '.table',
                '.connect-table',
                '.user-table'
            ]
            
            table_found = False
            for selector in table_selectors:
                table = self.page.ele(selector)
                if table and table.is_displayed:
                    table_found = True
                    
                    # 获取所有行
                    rows = table.eles('tr')
                    logger.info(f"发现表格，共 {len(rows)} 行")
                    
                    info = []
                    for row in rows:
                        cells = row.eles('td')
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
        """保存会话数据"""
        try:
            # 获取当前所有cookies
            cookies = self.page.cookies()
            browser_state = {
                'cookies': cookies,
                'url': self.page.url,
                'timestamp': time.time()
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
                self.page.quit()
        except Exception as e:
            logger.debug(f"清理资源时出错: {str(e)}")

def main():
    """主函数"""
    try:
        logger.info("LinuxDo 多站点自动化脚本启动")
        
        # 确定目标站点
        target_sites = SITES
        
        results = []
        
        for site_config in target_sites:
            logger.info(f"处理站点: {site_config['name']}")
            
            automator = SiteAutomator(site_config)
            success = automator.run_for_site()
            
            results.append({
                'site': site_config['name'],
                'success': success
            })
            
            # 站点间延迟（最后一个站点不需要）
            if site_config != target_sites[-1]:
                delay = random.uniform(10, 20)
                logger.info(f"等待 {delay:.1f} 秒后处理下一个站点...")
                time.sleep(delay)
        
        # 输出最终结果
        logger.info("执行结果汇总:")
        for result in results:
            status = "成功" if result['success'] else "失败"
            logger.info(f"站点: {result['site']}, 状态: {status}")
        
        success_count = sum(1 for r in results if r['success'])
        logger.success(f"完成: {success_count}/{len(results)} 个站点成功")
        
        return success_count == len(results)
        
    except Exception as e:
        logger.critical(f"主流程异常: {str(e)}")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
