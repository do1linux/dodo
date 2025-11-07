import os
import sys
import time
import random
import json
import traceback
import functools
from datetime import datetime
from urllib.parse import urljoin
from DrissionPage import ChromiumPage, ChromiumOptions
from loguru import logger
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

# 检测 GitHub Actions 环境
IS_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
    }
]

PAGE_TIMEOUT = 60
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 10

# 平台检测 - 固定为Windows
PLATFORM_IDENTIFIER = "Windows NT 10.0; Win64; x64"
USER_AGENT = f'Mozilla/5.0 ({PLATFORM_IDENTIFIER}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

# 扩展路径 - 检查是否存在，如果不存在则跳过
EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)

# 检查扩展目录是否存在
EXTENSION_ENABLED = os.path.exists(EXTENSION_PATH)

# 改进的 Cloudflare Turnstile 处理脚本
TURNSTILE_SCRIPT = """
async function handleTurnstile() {
    return new Promise((resolve) => {
        console.log('开始处理 Turnstile 验证...');
        
        // 方法1: 检查全局 turnstile 对象
        if (window.turnstile) {
            console.log('检测到 window.turnstile 对象');
            try {
                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]');
                if (iframe) {
                    const widgetId = iframe.getAttribute('data-turnstile-widget-id') || iframe.id;
                    if (widgetId) {
                        turnstile.getResponse(widgetId).then((token) => {
                            if (token) {
                                console.log('通过 turnstile.getResponse() 获取到 token');
                                resolve({success: true, token: token});
                                return;
                            }
                        });
                    }
                }
            } catch (e) {
                console.log('turnstile.getResponse() 出错:', e);
            }
        }
        
        // 方法2: 轮询等待隐藏字段被填充
        let attempts = 0;
        const maxAttempts = 20;
        
        function checkToken() {
            attempts++;
            console.log(`检查 token (${attempts}/${maxAttempts})`);
            
            const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
            if (hiddenInput && hiddenInput.value) {
                console.log('检测到 Turnstile token');
                resolve({success: true, token: hiddenInput.value});
                return;
            }
            
            if (attempts >= maxAttempts) {
                console.log('达到最大尝试次数，未找到 token');
                resolve({success: false, error: '轮询超时'});
                return;
            }
            
            setTimeout(checkToken, 2000);
        }
        
        checkToken();
    });
}

return handleTurnstile();
"""

# 备用 Turnstile 处理脚本 - 模拟用户交互
TURNSTILE_SCRIPT_ALTERNATIVE = """
async function alternativeTurnstileHandler() {
    return new Promise((resolve) => {
        console.log('使用备用方法处理 Turnstile...');
        
        // 模拟用户与 Turnstile 交互
        function simulateInteraction() {
            const turnstileElement = document.querySelector('.cf-turnstile, [data-sitekey]');
            if (turnstileElement) {
                console.log('找到 Turnstile 元素，模拟交互');
                
                // 模拟鼠标移动
                const rect = turnstileElement.getBoundingClientRect();
                const mouseMoveEvent = new MouseEvent('mousemove', {
                    clientX: rect.left + rect.width / 2,
                    clientY: rect.top + rect.height / 2,
                    bubbles: true
                });
                turnstileElement.dispatchEvent(mouseMoveEvent);
                
                // 模拟点击
                const clickEvent = new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true
                });
                turnstileElement.dispatchEvent(clickEvent);
            }
        }
        
        // 立即模拟交互
        simulateInteraction();
        
        // 等待并检查 token
        let attempts = 0;
        const maxAttempts = 25;
        
        function waitForToken() {
            attempts++;
            console.log(`等待 token (${attempts}/${maxAttempts})`);
            
            const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
            if (hiddenInput && hiddenInput.value) {
                console.log('备用方法检测到 token');
                resolve({success: true, token: hiddenInput.value});
                return;
            }
            
            // 每5次尝试重新模拟交互
            if (attempts % 5 === 0) {
                simulateInteraction();
            }
            
            if (attempts >= maxAttempts) {
                console.log('备用方法超时');
                resolve({success: false, error: '备用方法超时'});
                return;
            }
            
            setTimeout(waitForToken, 2000);
        }
        
        waitForToken();
    });
}

return alternativeTurnstileHandler();
"""

# 重试装饰器
def retry_decorator(max_retries=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"重试 {func.__name__} ({attempt + 1}/{max_retries}): {str(e)}")
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator

class CacheManager:
    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    return json.load(f)
            return None
        except Exception:
            return None

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        try:
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @staticmethod
    def load_turnstile_cache(site_name):
        """加载 Turnstile 验证缓存"""
        return CacheManager.load_site_cache(site_name, 'turnstile')

    @staticmethod
    def save_turnstile_cache(data, site_name):
        """保存 Turnstile 验证缓存"""
        return CacheManager.save_site_cache(data, site_name, 'turnstile')
    
    @staticmethod
    def load_cf_cookies(site_name):
        """加载 Cloudflare cookies 缓存"""
        return CacheManager.load_site_cache(site_name, 'cf_cookies')

    @staticmethod
    def save_cf_cookies(data, site_name):
        """保存 Cloudflare cookies 缓存"""
        return CacheManager.save_site_cache(data, site_name, 'cf_cookies')

class HumanBehaviorSimulator:
    """模拟人类行为"""
    
    @staticmethod
    def random_delay(min_seconds=1.0, max_seconds=3.0):
        """随机延迟"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    @staticmethod
    def simulate_typing(element, text):
        """模拟人类打字"""
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.2))

    @staticmethod
    def simulate_mouse_movement(page):
        """模拟鼠标移动"""
        try:
            for _ in range(random.randint(2, 5)):
                x = random.randint(100, 800)
                y = random.randint(100, 600)
                page.run_js(f"""
                var elem = document.elementFromPoint({x}, {y});
                if (elem) {{
                    var event = new MouseEvent('mousemove', {{
                        clientX: {x},
                        clientY: {y},
                        bubbles: true
                    }});
                    elem.dispatchEvent(event);
                }}
                """)
                time.sleep(random.uniform(0.1, 0.5))
        except Exception as e:
            logger.debug(f"模拟鼠标移动失败: {str(e)}")

    @staticmethod
    def simulate_scroll_behavior(page):
        """模拟滚动行为"""
        try:
            scroll_steps = random.randint(5, 10)
            for i in range(scroll_steps):
                scroll_amount = random.randint(300, 700)
                page.scroll.down(scroll_amount)
                time.sleep(random.uniform(0.5, 2.0))
                
                if random.random() < 0.2:
                    page.scroll.up(random.randint(100, 300))
                    time.sleep(random.uniform(0.3, 1.0))
        except Exception as e:
            logger.debug(f"模拟滚动失败: {str(e)}")

class EnhancedBrowserManager:
    @staticmethod
    def init_browser(site_name):
        try:
            co = ChromiumOptions()
            
            # 优化的浏览器参数 - 减少自动化特征
            browser_args = [
                '--no-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--headless=new', 
                '--disable-gpu',
                '--disable-extensions',
                '--disable-plugins',
                '--disable-web-security',
                '--allow-running-insecure-content',
                '--disable-features=VizDisplayCompositor',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--no-first-run',
                '--no-default-browser-check'
            ]
            
            for arg in browser_args:
                co.set_argument(arg)
            
            # 只有在扩展存在时才加载
            if EXTENSION_ENABLED:
                logger.info(f"🔧 加载扩展: {EXTENSION_PATH}")
                try:
                    co.add_extension(EXTENSION_PATH)
                except Exception as e:
                    logger.warning(f"⚠️ 扩展加载失败，继续无扩展运行: {str(e)}")
            else:
                logger.warning("⚠️ 扩展目录不存在，跳过扩展加载")
            
            co.set_user_agent(USER_AGENT)
            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=PAGE_TIMEOUT)
            
            # 只加载 Cloudflare cookies 缓存，不加载登录状态缓存
            cf_cookies = CacheManager.load_cf_cookies(site_name)
            if cf_cookies:
                page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个 Cloudflare 缓存cookies")
            
            # 增强的反自动化检测
            page.run_js("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
            delete navigator.__proto__.webdriver;
            """)
            
            return page
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

class CloudflareTurnstileHandler:
    """专门处理 Cloudflare Turnstile 验证"""
    
    @staticmethod
    def wait_for_cloudflare(page, timeout=30):
        """等待 Cloudflare 验证完成"""
        logger.info("⏳ 等待 Cloudflare 验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 检查页面标题和URL
                title = page.title.lower() if page.title else ""
                current_url = page.url.lower()
                
                # 如果不再显示验证页面，说明验证通过
                if ("just a moment" not in title and "checking your browser" not in title 
                    and "challenges" not in current_url):
                    logger.success("✅ Cloudflare 验证已通过")
                    return True
                
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待 Cloudflare 时出错: {str(e)}")
                time.sleep(2)
        
        logger.warning("⚠️ Cloudflare 等待超时，继续执行")
        return False

    @staticmethod
    def detect_turnstile_challenge(page):
        """检测 Turnstile 验证"""
        try:
            turnstile_selectors = [
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]',
                'div[class*="turnstile"]',
                'input[name="cf-turnstile-response"]',
                '.cf-turnstile',
                '[data-sitekey]'
            ]
            
            for selector in turnstile_selectors:
                elements = page.eles(selector)
                if elements:
                    logger.info(f"✅ 检测到 Turnstile 元素: {selector}")
                    return True
            
            # 检查页面内容
            page_text = page.html.lower()
            turnstile_keywords = ['cloudflare', 'turnstile', 'challenge', 'verifying', 'captcha']
            if any(keyword in page_text for keyword in turnstile_keywords):
                logger.info("✅ 检测到 Turnstile 相关关键词")
                return True
                
            return False
        except Exception as e:
            logger.debug(f"检测 Turnstile 验证失败: {str(e)}")
            return False

    @staticmethod
    def handle_turnstile_automated(page):
        """自动化处理 Turnstile 验证"""
        try:
            logger.info("🔄 开始自动化处理 Turnstile 验证...")
            
            # 等待 Turnstile 加载
            time.sleep(5)
            
            # 首先尝试主脚本
            logger.info("🔄 尝试主 Turnstile 处理脚本...")
            result = page.run_js(TURNSTILE_SCRIPT)
            
            if result and result.get('success'):
                token = result.get('token')
                logger.info(f"✅ 成功获取 Turnstile token: {token[:20]}...")
                
                # 设置 token 到表单
                set_script = f"""
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input) {{
                    input.value = '{token}';
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                """
                page.run_js(set_script)
                
                # 保存到缓存
                turnstile_data = {
                    'token': token,
                    'timestamp': datetime.now().isoformat(),
                    'site': 'current'
                }
                CacheManager.save_turnstile_cache(turnstile_data, 'current')
                logger.info("💾 Turnstile token 已保存到缓存")
                
                return True
            
            # 如果主脚本失败，尝试备用脚本
            logger.info("🔄 尝试备用 Turnstile 处理脚本...")
            result2 = page.run_js(TURNSTILE_SCRIPT_ALTERNATIVE)
            
            if result2 and result2.get('success'):
                token = result2.get('token')
                logger.info(f"✅ 备用脚本成功获取 Turnstile token: {token[:20]}...")
                
                # 设置 token 到表单
                set_script = f"""
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input) {{
                    input.value = '{token}';
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                """
                page.run_js(set_script)
                
                return True
            
            # 如果都失败，尝试等待自动完成
            logger.info("🔄 尝试等待 Turnstile 自动完成...")
            return CloudflareTurnstileHandler.wait_for_turnstile_auto_complete(page)
                
        except Exception as e:
            logger.error(f"❌ 处理 Turnstile 验证时发生异常: {str(e)}")
            return False

    @staticmethod
    def wait_for_turnstile_auto_complete(page, timeout=40):
        """等待 Turnstile 自动完成"""
        logger.info("⏳ 等待 Turnstile 自动完成验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 检查是否还有 Turnstile 元素
                if not CloudflareTurnstileHandler.detect_turnstile_challenge(page):
                    logger.success("✅ Turnstile 验证似乎已完成")
                    return True
                
                # 检查是否有 token
                token_input = page.ele('input[name="cf-turnstile-response"]', timeout=0)
                if token_input and token_input.value:
                    logger.info("✅ 检测到自动填充的 Turnstile token")
                    return True
                
                logger.info(f"⏳ 等待 Turnstile 完成... ({int(time.time() - start_time)}/{timeout}秒)")
                time.sleep(3)
                
            except Exception as e:
                logger.debug(f"等待 Turnstile 时出错: {str(e)}")
                time.sleep(3)
        
        logger.error("❌ Turnstile 等待超时")
        return False

class EnhancedSiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.page = None
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.topic_count = 0
        self.successful_browsed = 0

    def run_for_site(self):
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False

        try:
            self.page = EnhancedBrowserManager.init_browser(self.site_config['name'])
            
            # 强制每次都必须登录
            if self.force_login_required():
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                self.perform_browsing_actions_improved()
                self.get_connect_info_fixed()
                self.save_verification_data_only()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False

        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            self.cleanup()

    def force_login_required(self):
        """强制要求每次都必须登录"""
        logger.info("🔐 强制登录流程 - 每次都必须重新登录")
        
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")
            
            if self.enhanced_login_process():
                return True

            if attempt < RETRY_TIMES - 1:
                wait_time = 10 * (attempt + 1)
                logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)

        return False

    def enhanced_login_process(self):
        """增强的登录流程"""
        try:
            logger.info("🔐 开始完整登录流程")
            
            # 清除可能的旧会话
            self.page.get("about:blank")
            time.sleep(2)
            
            self.page.get(self.site_config['login_url'])
            time.sleep(5)

            # 等待 Cloudflare 验证
            CloudflareTurnstileHandler.wait_for_cloudflare(self.page)
            
            # 检查并处理 Turnstile 验证
            if CloudflareTurnstileHandler.detect_turnstile_challenge(self.page):
                logger.info("🛡️ 检测到 Cloudflare Turnstile 验证")
                if not CloudflareTurnstileHandler.handle_turnstile_automated(self.page):
                    logger.error("❌ Turnstile 验证处理失败")
                    return False
                else:
                    logger.info("✅ Turnstile 验证处理成功")

            # 查找登录表单元素
            username_field = self.find_login_field('username')
            password_field = self.find_login_field('password')
            login_button = self.find_login_button()

            if not all([username_field, password_field, login_button]):
                logger.error("❌ 登录表单元素未找到")
                return False

            username = self.credentials['username']
            password = self.credentials['password']

            # 模拟人类输入
            HumanBehaviorSimulator.simulate_mouse_movement(self.page)
            self.fill_field_safely(username_field, username)
            HumanBehaviorSimulator.random_delay(1, 2)
            self.fill_field_safely(password_field, password)
            HumanBehaviorSimulator.random_delay(1, 2)

            # 再次检查是否有新的 Turnstile 验证
            if CloudflareTurnstileHandler.detect_turnstile_challenge(self.page):
                logger.info("🛡️ 输入后检测到 Turnstile 验证")
                CloudflareTurnstileHandler.handle_turnstile_automated(self.page)

            # 点击登录
            login_button.click()
            time.sleep(8)

            # 验证登录结果
            return self.verify_login_result()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            traceback.print_exc()
            return False

    def find_login_field(self, field_type):
        """查找登录字段"""
        selectors_map = {
            'username': [
                '#login-account-name',
                '#username', 
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="用户名"]',
                'input[placeholder*="email"]'
            ],
            'password': [
                '#login-account-password',
                '#password', 
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]'
            ]
        }
        
        for selector in selectors_map[field_type]:
            try:
                element = self.page.ele(selector, timeout=5)
                if element and element.displayed:
                    logger.info(f"✅ 找到{field_type}字段: {selector}")
                    return element
            except:
                continue
        
        logger.error(f"❌ 未找到{field_type}字段")
        return None

    def find_login_button(self):
        """查找登录按钮"""
        button_selectors = [
            '#login-button',
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("Log In")',
            'button:has-text("Sign In")'
        ]
        
        for selector in button_selectors:
            try:
                button = self.page.ele(selector, timeout=5)
                if button and button.displayed:
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    return button
            except:
                continue
        
        logger.error("❌ 未找到登录按钮")
        return None

    def fill_field_safely(self, element, text):
        """安全地填写字段"""
        try:
            element.clear()
            time.sleep(0.5)
            HumanBehaviorSimulator.simulate_typing(element, text)
        except Exception as e:
            logger.warning(f"填写字段失败，使用备用方法: {str(e)}")
            element.input(text)

    def verify_login_result(self):
        """验证登录结果"""
        logger.info("🔍 验证登录结果...")
        
        # 检查错误信息
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger']
        for selector in error_selectors:
            error_element = self.page.ele(selector, timeout=0)
            if error_element:
                error_text = error_element.text
                logger.error(f"❌ 登录错误: {error_text}")
                return False
        
        return self.check_login_status()

    def check_login_status(self):
        """检查登录状态"""
        username = self.credentials['username']
        logger.info(f"🔍 检查登录状态，查找用户名: {username}")

        # 方法1: 检查用户菜单或用户名显示
        user_indicators = [
            '@id=current-user',
            '.current-user',
            '.user-menu',
            f'a[href*="/u/{username}"]'
        ]
        
        for selector in user_indicators:
            try:
                element = self.page.ele(selector, timeout=5)
                if element:
                    element_text = element.text.lower() if element.text else ""
                    if username.lower() in element_text:
                        logger.info(f"✅ 通过用户菜单验证登录成功: {selector}")
                        return True
            except:
                continue

        # 方法2: 访问个人资料页面
        try:
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page.get(profile_url)
            time.sleep(3)
            
            profile_content = self.page.html.lower()
            if username.lower() in profile_content:
                logger.info("✅ 通过个人资料页面验证登录成功")
                # 返回最新主题页面
                self.page.get(self.site_config['latest_topics_url'])
                return True
        except Exception as e:
            logger.debug(f"个人资料页面验证失败: {str(e)}")

        logger.error(f"❌ 登录状态检查失败")
        return False

    # 其余方法保持不变（浏览操作、连接信息获取等）
    def perform_browsing_actions_improved(self):
        """改进的浏览操作"""
        try:
            logger.info("🌐 开始浏览操作...")
            
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            topic_list = self.get_topic_list_improved()
            if not topic_list:
                logger.warning("❌ 未找到主题链接")
                return
            
            self.topic_count = len(topic_list)
            logger.info(f"📚 发现 {self.topic_count} 个主题帖")
            
            browse_count = min(MAX_TOPICS_TO_BROWSE, len(topic_list))
            selected_topics = random.sample(topic_list, browse_count)
            
            logger.info(f"🎯 准备浏览 {browse_count} 个主题")
            
            for i, topic in enumerate(selected_topics, 1):
                logger.info(f"📖 浏览进度: {i}/{browse_count}")
                if self.browse_topic_safe(topic):
                    self.successful_browsed += 1
                
                if i < browse_count:
                    delay = random.uniform(3, 8)
                    logger.info(f"⏳ 等待 {delay:.1f} 秒后浏览下一个主题...")
                    time.sleep(delay)
            
            logger.success(f"✅ 完成浏览 {self.successful_browsed}/{browse_count} 个主题")
            
        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    def get_topic_list_improved(self):
        """获取主题列表"""
        try:
            list_area = self.page.ele("@id=list-area", timeout=10)
            if list_area:
                topics = list_area.eles(".:title")
                if topics:
                    logger.info(f"✅ 使用主要选择器找到 {len(topics)} 个主题")
                    return topics
            
            all_links = self.page.eles('tag:a')
            topic_links = []
            for link in all_links:
                href = link.attr("href", "")
                if href and '/t/' in href and len(link.text.strip()) > 5:
                    topic_links.append(link)
            
            if topic_links:
                logger.info(f"✅ 使用链接过滤找到 {len(topic_links)} 个主题")
                return topic_links
                
            logger.warning("❌ 未找到主题链接")
            return []
            
        except Exception as e:
            logger.error(f"获取主题列表失败: {str(e)}")
            return []

    def browse_topic_safe(self, topic):
        """安全浏览主题"""
        try:
            topic_href = topic.attr("href")
            if not topic_href:
                return False
                
            if topic_href.startswith('/'):
                full_url = urljoin(self.site_config['base_url'], topic_href)
            else:
                full_url = topic_href
                
            logger.info(f"🔗 访问: {full_url}")
            
            new_tab = self.page.new_tab()
            new_tab.get(full_url)
            time.sleep(3)
            
            # 模拟浏览行为
            HumanBehaviorSimulator.simulate_scroll_behavior(new_tab)
            HumanBehaviorSimulator.simulate_mouse_movement(new_tab)
            
            # 随机点赞
            if random.random() < 0.002:
                self.safe_like_action(new_tab)
            
            new_tab.close()
            logger.info(f"✅ 成功浏览主题")
            return True
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            try:
                if 'new_tab' in locals():
                    new_tab.close()
            except:
                pass
            return False

    def safe_like_action(self, page):
        """安全点赞"""
        try:
            like_buttons = page.eles('.like-button, .discourse-reactions-reaction-button')
            for button in like_buttons:
                class_attr = button.attr('class', '')
                if class_attr and 'has-like' not in class_attr:
                    button.click()
                    logger.info("👍 执行点赞")
                    time.sleep(1)
                    break
        except:
            pass

    def get_connect_info_fixed(self):
        """获取连接信息"""
        logger.info("🔗 获取连接信息")
        
        if not self.check_login_status():
            logger.warning("⚠️ 需要重新登录")
            if not self.enhanced_login_process():
                logger.error("❌ 重新登录失败")
                return
        
        try:
            logger.info(f"🔗 访问连接信息页面: {self.site_config['connect_url']}")
            self.page.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 简单提取表格数据
            info = self.extract_connect_data_simple(self.page)
            if info:
                self.display_connect_info(info, "简单提取")
            else:
                logger.warning("❌ 未找到连接信息")
                
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def extract_connect_data_simple(self, page):
        """提取连接数据"""
        try:
            tables = page.eles("tag:table")
            
            for table in tables:
                rows = table.eles("tag:tr")
                info = []
                
                for row in rows:
                    cells = row.eles("tag:td")
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        
                        if project and (current or requirement):
                            info.append([project, current, requirement])
                
                if info:
                    return info
                    
            return []
        except Exception as e:
            logger.debug(f"简单提取失败: {str(e)}")
            return []

    def display_connect_info(self, info, method):
        """显示连接信息"""
        print("=" * 60)
        print(f"📊 {self.site_config['name']} Connect 连接信息 ({method})")
        print("=" * 60)
        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="grid"))
        print("=" * 60)
        logger.success(f"✅ 连接信息获取成功 - 找到 {len(info)} 个项目")

    def save_verification_data_only(self):
        """只保存验证数据"""
        try:
            cookies = self.page.cookies()
            if cookies:
                cf_cookies = []
                for cookie in cookies:
                    if any(keyword in cookie.get('name', '').lower() for keyword in 
                          ['cf_', 'cloudflare', '__cf', '_cf']):
                        cf_cookies.append(cookie)
                
                if cf_cookies:
                    CacheManager.save_cf_cookies(cf_cookies, self.site_config['name'])
                    logger.info(f"💾 保存 {len(cf_cookies)} 个 Cloudflare 验证cookies")
            
            logger.success(f"✅ 验证数据已保存")

        except Exception as e:
            logger.error(f"保存验证数据失败: {str(e)}")

    def cleanup(self):
        try:
            if self.page:
                self.page.quit()
        except Exception as e:
            logger.debug(f"清理资源: {str(e)}")

def main():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    logger.info("🚀 LinuxDo自动化脚本启动 - 改进版")
    logger.info(f"🔧 GitHub Actions 环境: {'是' if IS_GITHUB_ACTIONS else '否'}")

    target_sites = SITES
    results = []

    try:
        for site_config in target_sites:
            logger.info(f"🎯 处理站点: {site_config['name']}")

            automator = EnhancedSiteAutomator(site_config)
            success = automator.run_for_site()

            results.append({
                'site': site_config['name'],
                'success': success
            })

            if site_config != target_sites[-1]:
                delay = random.uniform(15, 30)
                logger.info(f"⏳ 等待 {delay:.1f} 秒后处理下一个站点...")
                time.sleep(delay)

        # 输出结果
        logger.info("📊 执行结果汇总:")
        table_data = [[r['site'], "✅ 成功" if r['success'] else "❌ 失败"] for r in results]
        print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))

        success_count = sum(1 for r in results if r['success'])
        logger.success(f"🎉 完成: {success_count}/{len(results)} 个站点成功")

    except Exception as e:
        logger.critical(f"💥 主流程异常: {str(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
