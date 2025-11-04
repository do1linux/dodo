import os
import random
import time
import json
from urllib.parse import urljoin
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_random
from PIL import Image
from drissionpage import DrissionPage as dp

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

PAGE_TIMEOUT = 120000
RETRY_TIMES = 2
MAX_TOPICS_TO_BROWSE = 3

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768}
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

class CloudflareHandler:
    @staticmethod
    def wait_for_cloudflare(page, timeout=30):
        logger.info("等待 Cloudflare 验证...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                title = page.title
                current_url = page.current_url

                if "请稍候" not in title and "Checking" not in title and "challenges" not in current_url:
                    logger.success("Cloudflare 验证已通过")
                    return True

                time.sleep(2)

            except Exception as e:
                logger.debug(f"等待 Cloudflare 时出错: {str(e)}")
                time.sleep(2)

        logger.warning("Cloudflare 等待超时，继续执行")
        return False

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.page = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.context = None

    def init_browser(self):
        user_agent = random.choice(USER_AGENTS)
        viewport = random.choice(VIEWPORT_SIZES)

        self.browser = dp(
            user_agent=user_agent,
            viewport=viewport,
            headless=HEADLESS_MODE,
            auto_close=False
        )
        logger.info("浏览器已启动")

    def create_context(self):
        storage_state = CacheManager.load_cache(self.site_config['browser_state_file'])
        cf_cookies = CacheManager.load_cache(self.site_config['cf_cookies_file'])

        self.context = self.browser.new_context(storage_state=storage_state)
        if cf_cookies:
            self.context.add_cookies(cf_cookies)
            logger.info(f"已加载 {len(cf_cookies)} 个缓存 cookies")

    def run_for_site(self):
        self.init_browser()
        self.create_context()

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

    @retry(stop=stop_after_attempt(3), wait=wait_random(min=2, max=5))
    def smart_login_approach(self):
        try:
            if self.try_direct_access():
                return True

            if self.full_login_process():
                return True

            return False
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    def try_direct_access(self):
        try:
            logger.info("尝试直接访问...")
            self.page = self.browser.get(self.site_config['latest_topics_url'], timeout=60000)
            time.sleep(3)

            if self.check_login_status():
                logger.success("缓存登录成功")
                return True

            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    def full_login_process(self):
        try:
            logger.info("开始完整登录流程")
            self.page = self.browser.get(self.site_config['login_url'], timeout=90000)
            time.sleep(3)

            CloudflareHandler.wait_for_cloudflare(self.page)

            if not self.wait_for_login_form():
                logger.error("登录表单加载失败")
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

    def wait_for_login_form(self, max_wait=30):
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
                    if element and element.is_visible():
                        logger.success(f"找到登录表单: {selector}")
                        return True

                time.sleep(2)

            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                time.sleep(2)

        logger.error("登录表单等待超时")
        return False

    def fill_login_form(self, username, password):
        try:
            username_selectors = ['#login-account-name', '#username', 'input[name="username"]']
            for selector in username_selectors:
                element = self.page.ele(selector)
                if element:
                    element.click()
                    time.sleep(0.3 + random.random() * 0.2)
                    element.send_keys(username)
                    logger.info("已填写用户名")
                    break

            password_selectors = ['#login-account-password', '#password', 'input[name="password"]']
            for selector in password_selectors:
                element = self.page.ele(selector)
                if element:
                    element.click()
                    time.sleep(0.3 + random.random() * 0.2)
                    element.send_keys(password)
                    logger.info("已填写密码")
                    break

            time.sleep(2 + random.random())

        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    def submit_login(self):
        try:
            login_buttons = [
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")'
            ]

            for selector in login_buttons:
                button = self.page.ele(selector)
                if button and button.is_visible():
                    logger.info(f"找到登录按钮: {selector}")
                    button.click()
                    logger.info("已点击登录按钮")

                    time.sleep(8 + random.random() * 2)
                    return True

            logger.error("未找到登录按钮")
            return False

        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    def verify_login_result(self):
        logger.info("验证登录结果...")

        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info("页面已跳转，可能登录成功")
            return self.check_login_status()

        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger']
        for selector in error_selectors:
            error_element = self.page.ele(selector)
            if error_element:
                error_text = error_element.text
                logger.error(f"登录错误: {error_text}")
                return False

        return self.check_login_status()

    def check_login_status(self):
        try:
            username = self.credentials['username']

            if username.lower() in self.page.html.lower():
                logger.success(f"在页面内容中找到用户名: {username}")
                return True

            user_indicators = ['img.avatar', '.current-user', '[data-user-menu]', '.header-dropdown-toggle']
            for selector in user_indicators:
                element = self.page.ele(selector)
                if element and element.is_visible():
                    logger.success(f"找到用户元素: {selector}")
                    return True

            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page = self.browser.get(profile_url, timeout=30000)
            time.sleep(3)

            if username.lower() in self.page.html.lower():
                logger.success(f"在个人资料页面验证用户名: {username}")
                self.page.back()
                return True

            logger.warning("无法验证登录状态")
            return False

        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    def perform_browsing_actions(self):
        logger.info("开始浏览主题帖...")
        self.page = self.browser.get(self.site_config['latest_topics_url'], timeout=60000)
        time.sleep(3 + random.random() * 2)

        topic_list = self.page.eles(".topic-list-item")
        logger.info(f"发现 {len(topic_list)} 个主题帖，将随机选择浏览")

        browsed_topics = 0
        for topic in random.sample(topic_list, min(len(topic_list), MAX_TOPICS_TO_BROWSE)):
            link = topic.ele(".title a")
            if link:
                topic_url = link.get_attribute("href")
                topic_url = urljoin(self.site_config['base_url'], topic_url)

                logger.info(f"正在浏览: {topic_url}")
                self.browse_topic(topic_url)
                browsed_topics += 1

        logger.success(f"完成浏览 {browsed_topics} 个主题帖")

    def browse_topic(self, topic_url):
        try:
            self.page = self.browser.get(topic_url, timeout=60000)
            time.sleep(3 + random.random() * 2)

            self.simulate_human_behavior()

        except Exception as e:
            logger.error(f"浏览主题帖失败: {str(e)}")

    def simulate_human_behavior(self):
        try:
            # 随机滚动页面
            for _ in range(5):
                scroll_distance = random.randint(550, 650)
                self.page.execute_script(f"window.scrollBy(0, {scroll_distance})")
                time.sleep(random.uniform(0.5, 1))

            # 随机鼠标移动
            for _ in range(10):
                x_offset = random.randint(-100, 100)
                y_offset = random.randint(-100, 100)
                self.page.mouse_move(x_offset, y_offset)
                time.sleep(random.uniform(0.1, 0.3))

            # 检查是否到达页面底部
            at_bottom = self.page.execute_script(
                "return window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            if at_bottom:
                logger.info("已到达页面底部")

            # 随机停留一段时间
            wait_time = random.uniform(2, 4)
            time.sleep(wait_time)

        except Exception as e:
            logger.error(f"模拟人类行为失败: {str(e)}")

    def print_connect_info(self):
        logger.info("获取连接信息")
        try:
            self.page = self.browser.get(self.site_config['connect_url'], timeout=30000)
            time.sleep(5 + random.random() * 2)

            rows = self.page.eles("table tr")

            info = []
            for row in rows:
                cells = row.eles("td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    info.append([project, current, requirement])

            logger.info("连接信息:")
            for item in info:
                logger.info(f"项目: {item[0]}, 当前: {item[1]}, 要求: {item[2]}")

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def save_session_data(self):
        try:
            state = self.browser.context.storage_state()
            CacheManager.save_cache(state, self.site_config['browser_state_file'])

            cf_cookies = [cookie for cookie in self.browser.context.cookies() if 'cf_' in cookie.get('name', '')]
            if cf_cookies:
                CacheManager.save_cache(cf_cookies, self.site_config['cf_cookies_file'])
                logger.info(f"保存 {len(cf_cookies)} 个 Cloudflare cookies")

            logger.info("会话数据已保存")

        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    def cleanup(self):
        try:
            if self.browser:
                self.browser.quit()
        except Exception as e:
            logger.error(f"清理资源失败: {str(e)}")

def main():
    try:
        logger.info("LinuxDo 自动化脚本启动")

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

            if site_config != target_sites[-1]:
                time.sleep(random.uniform(10, 20))

        logger.info("执行结果:")
        for result in results:
            logger.info(f"站点: {result['site']}, 状态: {'成功' if result['success'] else '失败'}")

        success_count = sum(1 for r in results if r['success'])
        logger.success(f"完成: {success_count}/{len(results)} 个站点成功")

    except Exception as e:
        logger.critical(f"主流程异常: {str(e)}")
    finally:
        if 'automator' in locals():
            automator.cleanup()
        logger.info("脚本结束")

if __name__ == "__main__":
    main()
