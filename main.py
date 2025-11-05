import os
import sys
import time
import random
import asyncio
import json
import traceback
import argparse
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from loguru import logger
from tabulate import tabulate

# 配置
SITE_CREDENTIALS = {
    'linux_do': {
        'username': os.getenv('LINUXDO_USERNAME'),
        'password': os.getenv('LINUXDO_PASSWORD')
    }
}

IS_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'
HEADLESS_MODE = True if IS_GITHUB_ACTIONS else False

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json",
    }
]

PAGE_TIMEOUT = 120000
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 10

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768}
]

# 工具类
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

class HumanBehaviorSimulator:
    @staticmethod
    async def random_delay(min_seconds=1.0, max_seconds=3.0):
        delay = random.uniform(min_seconds, max_seconds)
        await asyncio.sleep(delay)

    @staticmethod
    async def simulate_typing(element, text):
        for char in text:
            await element.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.2))

    @staticmethod
    async def simulate_mouse_movement(page):
        viewport = page.viewport_size
        if viewport:
            for _ in range(random.randint(2, 5)):
                x = random.randint(100, viewport['width'] - 100)
                y = random.randint(100, viewport['height'] - 100)
                await page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.1, 0.5))

    @staticmethod
    async def simulate_scroll_behavior(page):
        scroll_steps = random.randint(3, 8)
        for _ in range(scroll_steps):
            scroll_amount = random.randint(200, 500)
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(0.5, 2.0))

class CloudflareHandler:
    @staticmethod
    async def wait_for_cloudflare(page, timeout=30):
        logger.info("等待Cloudflare验证...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                title = await page.title()
                current_url = page.url

                turnstile_frame = await page.query_selector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]')
                if turnstile_frame:
                    logger.warning("检测到Cloudflare Turnstile验证")
                    if await CloudflareHandler.handle_turnstile_challenge(page):
                        logger.success("Turnstile验证处理完成")
                        return True
                
                if "请稍候" not in title and "Checking" not in title and "challenges" not in current_url:
                    logger.success("Cloudflare验证已通过")
                    return True

                await asyncio.sleep(2)

            except Exception as e:
                logger.debug(f"等待Cloudflare时出错: {str(e)}")
                await asyncio.sleep(2)

        logger.warning("Cloudflare等待超时，继续执行")
        return False

    @staticmethod
    async def handle_turnstile_challenge(page):
        try:
            logger.info("尝试处理Turnstile验证...")
            
            await page.wait_for_selector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]', timeout=10000)
            
            turnstile_script = """
            async function getTurnstileResponse() {
                return new Promise((resolve) => {
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
                    
                    const checkField = setInterval(() => {
                        const field = document.querySelector('input[name="cf-turnstile-response"]');
                        if (field && field.value) {
                            clearInterval(checkField);
                            resolve(field.value);
                        }
                    }, 500);
                    
                    setTimeout(() => {
                        clearInterval(checkField);
                        resolve(null);
                    }, 15000);
                });
            }
            return getTurnstileResponse();
            """
            
            token = await page.evaluate(turnstile_script)
            
            if token:
                logger.success(f"获取到Turnstile Token: {token[:20]}...")
                
                await page.evaluate(f"""
                (token) => {{
                    const field = document.querySelector('input[name="cf-turnstile-response"]');
                    if (field) {{
                        field.value = token;
                    }}
                    if (field) {{
                        field.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}
                """, token)
                
                await asyncio.sleep(3)
                return True
            else:
                logger.warning("无法获取Turnstile Token，尝试备用方案")
                for i in range(30):
                    token_field = await page.query_selector('input[name="cf-turnstile-response"]')
                    if token_field:
                        token_value = await token_field.evaluate('el => el.value')
                        if token_value and len(token_value) > 10:
                            logger.success(f"检测到Turnstile响应: {token_value[:20]}...")
                            return True
                    await asyncio.sleep(1)
                
                return False
                
        except Exception as e:
            logger.error(f"处理Turnstile验证失败: {str(e)}")
            return False

class BrowserManager:
    @staticmethod
    async def init_browser():
        playwright = await async_playwright().start()

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
            '--user-agent=' + USER_AGENTS[0]
        ]

        browser = await playwright.chromium.launch(
            headless=HEADLESS_MODE,
            args=browser_args
        )

        logger.info("浏览器已启动")
        return browser, playwright

    @staticmethod
    async def create_context(browser, site_name):
        storage_state = CacheManager.load_cache(site_name + '_browser_state.json')
        cf_cookies = CacheManager.load_cache(site_name + '_cf_cookies.json')

        user_agent = USER_AGENTS[0]
        viewport = VIEWPORT_SIZES[0]

        logger.info(f"{site_name} - UA: {user_agent[:50]}...")

        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            storage_state=storage_state,
            ignore_https_errors=True,
            java_script_enabled=True,
        )

        if cf_cookies:
            await context.add_cookies(cf_cookies)
            logger.info(f"已加载 {len(cf_cookies)} 个缓存cookies")

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
            delete navigator.__proto__.webdriver;
        """)

        return context

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.detected_bot_checks = []
        self.detected_login_elements = []

    async def run_for_site(self, browser, playwright):
        self.browser = browser
        self.playwright = playwright

        if not self.credentials.get('username'):
            logger.error(f"{self.site_config['name']} 用户名未设置")
            return False

        try:
            self.context = await BrowserManager.create_context(browser, self.site_config['name'])
            self.page = await self.context.new_page()
            self.page.set_default_timeout(PAGE_TIMEOUT)

            login_success = await self.smart_login_approach()

            if login_success:
                logger.success(f"{self.site_config['name']} 登录成功")
                await self.perform_browsing_actions()
                await self.print_connect_info()
                await self.save_session_data()
                return True
            else:
                logger.error(f"{self.site_config['name']} 登录失败")
                return False

        except Exception as e:
            logger.error(f"{self.site_config['name']} 执行异常: {str(e)}")
            traceback.print_exc()
            return False
        finally:
            await self.cleanup()

    async def smart_login_approach(self):
        for attempt in range(RETRY_TIMES):
            logger.info(f"登录尝试 {attempt + 1}/{RETRY_TIMES}")

            try:
                if await self.try_direct_access():
                    return True

                if await self.full_login_process():
                    return True

            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")

            if attempt < RETRY_TIMES - 1:
                await self.clear_cache()
                await asyncio.sleep(10 * (attempt + 1))

        return False

    async def try_direct_access(self):
        try:
            logger.info("尝试直接访问...")
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(5)

            if await self.check_login_status():
                logger.success("缓存登录成功")
                return True

            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    async def full_login_process(self):
        try:
            logger.info("开始完整登录流程")

            await self.page.goto(self.site_config['login_url'], timeout=90000)
            await asyncio.sleep(5)

            await self.detect_bot_checks_and_login_elements()
            
            await CloudflareHandler.wait_for_cloudflare(self.page, timeout=30)

            if not await self.wait_for_login_form():
                logger.error("登录表单加载失败")
                return False

            username = self.credentials['username']
            password = self.credentials['password']

            await self.fill_login_form(username, password)

            if not await self.submit_login():
                return False

            return await self.verify_login_result()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    async def detect_bot_checks_and_login_elements(self):
        logger.info("检测页面元素...")
        
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
            elements = await self.page.query_selector_all(selector)
            if elements:
                for element in elements:
                    self.detected_bot_checks.append(selector)
                    logger.warning(f"检测到机器人验证: {selector}")
        
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
            elements = await self.page.query_selector_all(selector)
            if elements:
                for element in elements:
                    if await element.is_visible():
                        self.detected_login_elements.append(selector)
                        logger.info(f"检测到登录元素: {selector}")
        
        if self.detected_bot_checks:
            logger.warning(f"检测到的机器人验证: {list(set(self.detected_bot_checks))}")
        if self.detected_login_elements:
            logger.info(f"检测到的登录元素: {list(set(self.detected_login_elements))}")

    async def wait_for_login_form(self, max_wait=30):
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
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        logger.success(f"找到登录表单: {selector}")
                        return True

                await asyncio.sleep(2)

            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                await asyncio.sleep(2)

        logger.error("登录表单等待超时")
        return False

    async def fill_login_form(self, username, password):
        try:
            username_selectors = [
                '#login-account-name', 
                '#username', 
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="用户名"]'
            ]
            
            username_filled = False
            for selector in username_selectors:
                element = await self.page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    await asyncio.sleep(0.5)
                    await element.fill(username)
                    await HumanBehaviorSimulator.simulate_typing(element, username)
                    username_filled = True
                    logger.info("已填写用户名")
                    break

            password_selectors = [
                '#login-account-password', 
                '#password', 
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]'
            ]
            
            if not username_filled:
                logger.error("未找到用户名输入框")
                return
            
            await HumanBehaviorSimulator.random_delay(1, 2)
            
            password_filled = False
            for selector in password_selectors:
                element = await self.page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    await asyncio.sleep(0.5)
                    await element.fill(password)
                    await HumanBehaviorSimulator.simulate_typing(element, password)
                    password_filled = True
                    logger.info("已填写密码")
                    break

            await asyncio.sleep(2)
            if not password_filled:
                logger.error("未找到密码输入框")
                return
            
            await HumanBehaviorSimulator.random_delay(1, 3)

        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    async def submit_login(self):
        try:
            login_buttons = [
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")'
            ]

            for selector in login_buttons:
                button = await self.page.query_selector(selector)
                if button and await button.is_visible():
                    logger.info(f"找到登录按钮: {selector}")
                    
                    await HumanBehaviorSimulator.random_delay(0.5, 1.5)
                    await button.click()
                    logger.info("已点击登录按钮")

                    await asyncio.sleep(8)
                    return True

            logger.error("未找到登录按钮")
            return False

        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    async def verify_login_result(self):
        logger.info("验证登录结果...")

        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info("页面已跳转，可能登录成功")
            return await self.check_login_status()

        error_selectors = [
            '.alert-error', 
            '.error', 
            '.flash-error', 
            '.alert-danger', 
            '.login-error'
        ]
        for selector in error_selectors:
            error_element = await self.page.query_selector(selector)
            if error_element:
                error_text = await error_element.text_content()
                logger.error(f"登录错误: {error_text}")
                return False

        return await self.check_login_status()

    async def check_login_status(self):
        try:
            username = self.credentials['username']
            logger.info(f"严格检查登录状态，查找用户名: {username}")

            content = await self.page.content()
            if username.lower() in content.lower():
                logger.success(f"在页面内容中找到用户名: {username}")
                return True

            user_indicators = [
                f'a[href*="/u/{username}"]',
                f'a[href*="/users/{username}"]',
                '.current-user',
                '[data-current-user]',
                '.header-dropdown-toggle',
                '.user-menu'
            ]
            
            for selector in user_indicators:
                element = await self.page.query_selector(selector)
                if element and await element.is_visible():
                    logger.success(f"找到用户元素: {selector}")
                    element_text = await element.text_content()
                    if username.lower() in element_text.lower():
                        logger.success(f"在用户元素中找到用户名: {selector}")
                        return True

            profile_url = f"{self.site_config['base_url']}/u/{username}"
            await self.page.goto(profile_url, timeout=30000)
            await asyncio.sleep(3)
            profile_content = await self.page.content()
            if username.lower() in profile_content.lower():
                logger.success(f"在个人资料页面验证用户名: {username}")
                await self.page.go_back(timeout=30000)
                return True
            else:
                logger.error(f"无法在个人资料页面找到用户名: {username}")
                return False

        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    async def perform_browsing_actions(self):
        try:
            logger.info("开始浏览操作...")
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(5)

            topic_list = await self.page.query_selector_all(".topic-list-item")
            logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择 {MAX_TOPICS_TO_BROWSE} 个")
            
            for topic in random.sample(topic_list, min(MAX_TOPICS_TO_BROWSE, len(topic_list))):
                topic_url = await topic.get_attribute("href")
                topic_url = urljoin(self.site_config['base_url'], topic_url)
                
                await self.click_one_topic(topic_url)

        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    async def click_one_topic(self, topic_url):
        logger.info(f"打开主题页: {topic_url}")
        try:
            await self.page.goto(topic_url, timeout=60000)
            await asyncio.sleep(random.uniform(3, 5))

            await HumanBehaviorSimulator.simulate_mouse_movement(self.page)
            await HumanBehaviorSimulator.simulate_scroll_behavior(self.page)

            like_button = await self.page.query_selector(".discourse-reactions-reaction-button")
            if like_button and await like_button.is_visible():
                logger.info("找到点赞按钮，准备点赞")
                await like_button.click()
                logger.info("点赞成功")
                await asyncio.sleep(random.uniform(1, 2))

        except Exception as e:
            logger.error(f"浏览主题页失败: {str(e)}")

    async def print_connect_info(self):
        logger.info("获取连接信息")
        try:
            await self.page.goto(self.site_config['connect_url'], timeout=60000)
            await asyncio.sleep(5)

            table_rows = await self.page.query_selector_all("table tr")
            info = []

            for row in table_rows[1:]:  # 跳过表头
                cells = await row.query_selector_all("td")
                if len(cells) >= 3:
                    project = await cells[0].text_content()
                    current = await cells[1].text_content()
                    requirement = await cells[2].text_content()
                    info.append([project, current, requirement])

            logger.info("--------------Connect Info-----------------")
            print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    async def save_session_data(self):
        try:
            state = await self.context.storage_state()
            CacheManager.save_cache(state, self.site_config['name'] + '_browser_state.json')

            cookies = await self.context.cookies()
            cf_cookies = [cookie for cookie in cookies if 'cf_' in cookie.get('name', '')]
            if cf_cookies:
                CacheManager.save_cache(cf_cookies, self.site_config['name'] + '_cf_cookies.json')

            logger.info("会话数据已保存")

        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    async def clear_cache(self):
        cache_files = [
            self.site_config['name'] + '_browser_state.json',
            self.site_config['name'] + '_cf_cookies.json'
        ]

        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"已清除: {file}")

    async def cleanup(self):
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass

async def main():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    logger.info("LinuxDo自动化脚本启动")

    browser, playwright = await BrowserManager.init_browser()

    try:
        results = []

        for site_config in SITES:
            logger.info(f"处理站点: {site_config['name']}")

            automator = SiteAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)

            results.append({
                'site': site_config['name'],
                'success': success
            })

            if site_config != SITES[-1]:
                await asyncio.sleep(random.uniform(10, 20))

        logger.info("执行结果:")
        table_data = [[r['site'], "✅ 成功" if r['success'] else "❌ 失败"] for r in results]
        print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))

        success_count = sum(1 for r in results if r['success'])
        logger.success(f"完成: {success_count}/{len(results)} 个站点成功")

    except Exception as e:
        logger.critical(f"主流程异常: {str(e)}")
        traceback.print_exc()
    finally:
        await browser.close()
        await playwright.stop()
        logger.info("脚本结束")

if __name__ == "__main__":
    asyncio.run(main())
