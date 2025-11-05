import os
import sys
import time
import random
import json
import traceback
from datetime import datetime
from urllib.parse import urljoin
from DrissionPage import ChromiumPage, SessionPage, ChromiumOptions
from loguru import logger
from tabulate import tabulate
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

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
MAX_TOPICS_TO_BROWSE = 10

# 只使用一个 Windows User Agent
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768},
    {'width': 1536, 'height': 864}
]

class CacheManager:
    @staticmethod
    def load_cache(file_name):
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
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
            logger.info(f"💾 缓存已保存: {file_name}")
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

class HumanBehaviorSimulator:
    @staticmethod
    def random_delay(min_seconds=1.0, max_seconds=3.0):
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    @staticmethod
    def simulate_typing(element, text):
        """模拟人类打字节奏"""
        element.clear()
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.2))

    @staticmethod
    def simulate_mouse_movement(page):
        """模拟随机鼠标移动"""
        try:
            viewport = page.get_viewport_size()
            for _ in range(random.randint(2, 5)):
                x = random.randint(100, viewport['width'] - 100)
                y = random.randint(100, viewport['height'] - 100)
                page.run_js(f"document.elementFromPoint({x}, {y})?.focus()")
                time.sleep(random.uniform(0.1, 0.5))
        except Exception:
            pass

    @staticmethod
    def simulate_scroll_behavior(page):
        """模拟人类滚动行为"""
        scroll_steps = random.randint(3, 8)
        for _ in range(scroll_steps):
            scroll_amount = random.randint(200, 500)
            page.scroll.down(scroll_amount)
            time.sleep(random.uniform(0.5, 2.0))

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
                turnstile_frame = page.ele('xpath://iframe[contains(@src, "challenges.cloudflare.com") or contains(@src, "turnstile")]', timeout=2)
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
                page.run_js(f"""
                (token) => {{
                    const field = document.querySelector('input[name="cf-turnstile-response"]');
                    if (field) {{
                        field.value = token;
                    }}
                    // 触发change事件
                    if (field) {{
                        field.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}
                """, token)
                
                time.sleep(3)
                return True
            else:
                logger.warning("❌ 无法获取Turnstile Token，尝试备用方案")
                # 备用方案：等待手动验证完成
                for i in range(30):
                    token_field = page.ele('xpath://input[@name="cf-turnstile-response"]', timeout=1)
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
        """初始化浏览器并返回页面对象"""
        try:
            # 创建浏览器配置
            co = ChromiumOptions()
            
            # 设置浏览器参数
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
            ]
            
            for arg in browser_args:
                co.set_argument(arg)
            
            # 设置 User Agent
            co.set_user_agent(USER_AGENT)
            
            # 设置视口大小
            viewport = random.choice(VIEWPORT_SIZES)
            co.set_argument(f"--window-size={viewport['width']},{viewport['height']}")
            
            # 创建页面对象
            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=PAGE_TIMEOUT)
            
            # 注入反检测脚本
            page.run_js("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
            delete navigator.__proto__.webdriver;
            """)
            
            logger.info("🚀 浏览器已启动")
            return page
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {str(e)}")
            raise

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.page = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.detected_bot_checks = []
        self.detected_login_elements = []

    def run_for_site(self):
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False

        try:
            # 初始化浏览器
            self.page = BrowserManager.init_browser()

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
            
            # 加载缓存的 cookies
            cf_cookies = CacheManager.load_site_cache(self.site_config['name'], 'cf_cookies')
            if cf_cookies:
                self.page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个缓存cookies")
            
            self.page.get(self.site_config['latest_topics_url'])
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

            self.page.get(self.site_config['login_url'])
            time.sleep(5)

            # 检测机器人验证和登录元素
            self.detect_bot_checks_and_login_elements()
            
            CloudflareHandler.wait_for_cloudflare(self.page, timeout=30)

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
                elements = self.page.eles(selector)
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
            'button:contains("登录")',
            'button:contains("Log In")'
        ]
        
        for selector in login_element_selectors:
            try:
                elements = self.page.eles(selector)
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
                    element = self.page.ele(selector, timeout=1)
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
                    element = self.page.ele(selector, timeout=1)
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
            HumanBehaviorSimulator.simulate_mouse_movement(self.page)
            
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
                try:
                    element = self.page.ele(selector)
                    if element and element.displayed:
                        element.click()
                        time.sleep(0.5)
                        HumanBehaviorSimulator.simulate_typing(element, username)
                        username_filled = True
                        logger.info("✅ 已填写用户名")
                        break
                except:
                    continue

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
                try:
                    element = self.page.ele(selector)
                    if element and element.displayed:
                        element.click()
                        time.sleep(0.5)
                        HumanBehaviorSimulator.simulate_typing(element, password)
                        password_filled = True
                        logger.info("✅ 已填写密码")
                        break
                except:
                    continue
            
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
                'button:contains("登录")',
                'button:contains("Log In")',
                'button:contains("Sign In")'
            ]

            for selector in login_buttons:
                try:
                    button = self.page.ele(selector)
                    if button and button.displayed:
                        logger.info(f"✅ 找到登录按钮: {selector}")
                        
                        # 模拟鼠标移动和点击前暂停
                        HumanBehaviorSimulator.random_delay(0.5, 1.5)
                        button.click()
                        logger.info("✅ 已点击登录按钮")

                        # 等待登录处理
                        time.sleep(8)
                        return True
                except:
                    continue

            logger.error("❌ 未找到登录按钮")
            return False

        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    def verify_login_result(self):
        logger.info("🔍 验证登录结果...")

        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info("✅ 页面已跳转，可能登录成功")
            return self.check_login_status()

        # 检查错误信息
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger', '.login-error']
        for selector in error_selectors:
            try:
                error_element = self.page.ele(selector, timeout=1)
                if error_element:
                    error_text = error_element.text
                    logger.error(f"❌ 登录错误: {error_text}")
                    return False
            except:
                continue

        return self.check_login_status()

    def check_login_status(self):
        """严格检查登录状态，必须检测到用户名"""
        try:
            username = self.credentials['username']
            logger.info(f"🔍 严格检查登录状态，查找用户名: {username}")

            # 方法1: 检查页面内容中的用户名
            content = self.page.html
            if username.lower() in content.lower():
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
                try:
                    element = self.page.ele(selector, timeout=1)
                    if element and element.displayed:
                        element_text = element.text
                        if username.lower() in element_text.lower():
                            logger.success(f"✅ 在用户元素中找到用户名: {selector}")
                            return True
                except:
                    continue

            # 方法3: 访问个人资料页面
            profile_urls = [
                f"{self.site_config['base_url']}/u/{username}",
                f"{self.site_config['base_url']}/users/{username}",
                f"{self.site_config['base_url']}/user/{username}"
            ]

            for profile_url in profile_urls:
                try:
                    self.page.get(profile_url)
                    time.sleep(3)
                    
                    profile_content = self.page.html
                    if username.lower() in profile_content.lower():
                        logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                        # 返回之前页面
                        self.page.back()
                        return True
                except Exception:
                    continue

            logger.error(f"❌ 无法在页面中找到用户名: {username}")
            return False

        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    def perform_browsing_actions(self):
        """执行浏览操作：定位主题列表 → 随机筛选主题 → 打开主题页 → 模拟滚动浏览"""
        try:
            logger.info("🌐 开始浏览操作...")
            
            # 访问最新主题页面
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            # 定位主题列表
            topic_links = self.get_topic_links()
            if not topic_links:
                logger.warning("❌ 未找到主题链接")
                return
            
            logger.info(f"📚 找到 {len(topic_links)} 个主题")
            
            # 随机筛选主题
            topics_to_browse = min(MAX_TOPICS_TO_BROWSE, len(topic_links))
            selected_topics = random.sample(topic_links, topics_to_browse)
            
            logger.info(f"🎯 随机选择 {topics_to_browse} 个主题进行浏览")
            
            # 打开主题页并模拟滚动浏览
            for i, topic_link in enumerate(selected_topics, 1):
                logger.info(f"📖 浏览主题 {i}/{topics_to_browse}: {topic_link}")
                self.browse_topic_page(topic_link)
                
                if i < topics_to_browse:
                    HumanBehaviorSimulator.random_delay(2, 5)
            
            logger.success("✅ 浏览操作完成")
            
        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    def get_topic_links(self):
        """获取主题链接列表"""
        try:
            # 多种选择器尝试获取主题链接
            topic_selectors = [
                'a.title[href*="/t/"]',
                '.topic-list-item a[href*="/t/"]',
                '.topic-list a[href*="/t/"]',
                'a[href*="/t/"]:has(.topic-title)'
            ]
            
            for selector in topic_selectors:
                try:
                    links = self.page.eles(selector)
                    if links:
                        hrefs = []
                        for link in links:
                            href = link.attr('href')
                            if href and '/t/' in href:
                                full_url = urljoin(self.site_config['base_url'], href)
                                hrefs.append(full_url)
                        return list(set(hrefs))  # 去重
                except:
                    continue
            
            return []
            
        except Exception as e:
            logger.error(f"获取主题链接失败: {str(e)}")
            return []

    def browse_topic_page(self, topic_url):
        """浏览单个主题页面"""
        try:
            # 在新标签页打开主题
            new_tab = self.page.new_tab()
            self.page.wait.new_tab()
            self.page.to_tab(1)  # 切换到新标签页
            
            self.page.get(topic_url)
            time.sleep(3)
            
            # 模拟滚动浏览
            self.simulate_topic_browsing()
            
            # 随机点赞（0.3%概率）
            if random.random() < 0.003:
                self.click_like()
            
            # 关闭标签页并返回主标签页
            self.page.close_tab()
            self.page.to_tab(0)
            
        except Exception as e:
            logger.error(f"浏览主题页面失败: {str(e)}")
            # 确保返回主标签页
            try:
                self.page.to_tab(0)
            except:
                pass

    def simulate_topic_browsing(self):
        """模拟真实用户的滚动浏览行为"""
        try:
            prev_scroll_pos = 0
            same_position_count = 0
            
            # 开始自动滚动，最多滚动10次
            for scroll_count in range(10):
                # 随机滚动一段距离
                scroll_distance = random.randint(300, 800)
                self.page.scroll.down(scroll_distance)
                
                current_scroll_pos = self.page.scroll.height
                logger.info(f"📜 滚动 {scroll_count + 1}/10, 距离: {scroll_distance}px")
                
                # 检查是否到达页面底部
                if current_scroll_pos == prev_scroll_pos:
                    same_position_count += 1
                    if same_position_count >= 2:
                        logger.success("已到达页面底部，退出浏览")
                        break
                else:
                    same_position_count = 0
                
                prev_scroll_pos = current_scroll_pos
                
                # 随机退出概率
                if random.random() < 0.03:
                    logger.success("随机退出浏览")
                    break
                
                # 动态随机等待
                wait_time = random.uniform(2, 5)
                logger.info(f"⏳ 等待 {wait_time:.2f} 秒...")
                time.sleep(wait_time)
                
        except Exception as e:
            logger.error(f"模拟滚动浏览失败: {str(e)}")

    def click_like(self):
        """点赞操作"""
        try:
            # 查找未点赞的按钮
            like_selectors = [
                '.discourse-reactions-reaction-button',
                '.like-button',
                '.btn-like',
                'button[title*="Like"]',
                'button[title*="点赞"]'
            ]
            
            for selector in like_selectors:
                try:
                    like_button = self.page.ele(selector, timeout=1)
                    if like_button and like_button.displayed:
                        logger.info("找到未点赞的帖子，准备点赞")
                        like_button.click()
                        logger.info("点赞成功")
                        time.sleep(random.uniform(1, 2))
                        return True
                except:
                    continue
            
            logger.info("帖子可能已经点过赞了")
            return False
            
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")
            return False

    def print_connect_info(self):
        """获取并打印连接信息"""
        try:
            logger.info("🔗 获取连接信息")
            
            # 使用SessionPage避免浏览器开销
            session_page = SessionPage()
            session_page.get(self.site_config['connect_url'])
            
            # 解析表格数据
            try:
                table = session_page.ele('tag:table')
                rows = table.eles('tag:tr')
                
                info = []
                for row in rows:
                    cells = row.eles('tag:td')
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        info.append([project, current, requirement])
                
                if info:
                    print("--------------Connect Info-----------------")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                    logger.success("✅ 连接信息获取成功")
                else:
                    logger.warning("⚠️ 未找到连接信息")
                    
            except Exception as e:
                logger.error(f"解析连接信息失败: {str(e)}")
                
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def save_session_data(self):
        """保存会话数据用于下次运行"""
        try:
            # 保存cookies
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_config['name'], 'cf_cookies')
                logger.info(f"💾 保存 {len(cookies)} 个cookies")

            logger.success("✅ 会话数据已保存")

        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    def clear_cache(self):
        """清除缓存数据"""
        cache_files = [
            f"browser_state_{self.site_config['name']}.json",
            f"cf_cookies_{self.site_config['name']}.json"
        ]

        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"🗑️ 已清除: {file}")

    def cleanup(self):
        """清理资源"""
        try:
            if self.page:
                self.page.quit()
        except Exception:
            pass

def main():
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    logger.info("🚀 LinuxDo自动化脚本启动 (DrissionPage版本)")

    # 确定目标站点
    target_sites = SITES

    results = []

    try:
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

    except Exception as e:
        logger.critical(f"💥 主流程异常: {str(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
