import os
import random
import time
import re
import json
from datetime import datetime, timedelta
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError
from tabulate import tabulate

# ======================== 配置常量 ========================
# 站点认证信息配置
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

# 站点配置列表
SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do'
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com'
    }
]

# 全局配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]

# Cookie有效期设置（天）
COOKIE_VALIDITY_DAYS = 7

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类"""
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(current_dir, "cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            cache_dir = current_dir
        return cache_dir

    @staticmethod
    def get_cache_file_path(file_name):
        """获取缓存文件的完整路径"""
        cache_dir = CacheManager.get_cache_directory()
        return os.path.join(cache_dir, file_name)

    @staticmethod
    def load_cache(file_name):
        """从文件加载缓存数据"""
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
                return data
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存数据到缓存文件"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_cf_cookies(site_name):
        """加载Cloudflare cookies缓存并检查有效期"""
        cache_data = CacheManager.load_cache(f"{site_name}_cf_cookies.json")
        if not cache_data:
            return None
        # 检查缓存有效期
        cache_time_str = cache_data.get('cache_time')
        if cache_time_str:
            try:
                cache_time = datetime.fromisoformat(cache_time_str)
                if datetime.now() - cache_time > timedelta(days=COOKIE_VALIDITY_DAYS):
                    logger.warning("🕒 Cloudflare cookies已过期")
                    return None
            except Exception as e:
                logger.warning(f"缓存时间解析失败: {str(e)}")
        return cache_data.get('cookies')

    @staticmethod
    def save_cf_cookies(cookies, site_name):
        """只保存Cloudflare相关的cookies到缓存"""
        # 过滤出Cloudflare相关的cookies
        cf_cookies = [cookie for cookie in cookies 
                     if cookie.get('name', '').startswith('cf_')]
        
        if not cf_cookies:
            logger.warning("⚠️ 没有找到Cloudflare cookies")
            return False
            
        cache_data = {
            'cookies': cf_cookies,
            'cache_time': datetime.now().isoformat(),
            'site': site_name
        }
        return CacheManager.save_cache(cache_data, f"{site_name}_cf_cookies.json")

    @staticmethod
    def cookies_exist(site_name):
        """检查cookies文件是否存在"""
        file_path = CacheManager.get_cache_file_path(f"{site_name}_cf_cookies.json")
        return os.path.exists(file_path)

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类"""
    @staticmethod
    def is_cf_cookie_valid(cookies):
        """检查Cloudflare cookie是否有效"""
        try:
            if not cookies:
                return False
            for cookie in cookies:
                if cookie.get('name') == 'cf_clearance':
                    expires = cookie.get('expires', 0)
                    # 检查cookie是否过期
                    if expires == -1 or expires > time.time():
                        return True
            return False
        except Exception:
            return False

    @staticmethod
    def handle_cloudflare(page, max_attempts=8, timeout=180):
        """处理Cloudflare验证"""
        start_time = time.time()
        logger.info("🛡️ 开始处理 Cloudflare验证")
        # 完整验证流程
        logger.info("🔄 开始完整Cloudflare验证流程")
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = page.title()
                # 检查页面是否已经正常加载
                if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                # 等待验证
                wait_time = random.uniform(8, 15)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时")
                    break
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)
        # 最终检查
        try:
            page_title = page.title()
            if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                logger.success("✅ 最终验证: Cloudflare验证通过")
                return True
            else:
                logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
                return True
        except Exception:
            logger.warning("⚠️ 无法获取页面标题，继续后续流程")
            return True

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.login_attempts = 0
        self.max_login_attempts = 2
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def create_cf_context(self):
        """创建只保留 Cloudflare 相关 cookies 的新上下文"""
        # 获取当前所有 cookies
        if self.browser:
            storage_state = self.browser.storage_state()
            cookies = storage_state.get("cookies", [])

            # 只保留 Cloudflare 相关 cookies
            cf_cookies = [
                cookie for cookie in cookies
                if re.search(r"__cf_|cf_clearance", cookie.get("name", ""), re.I)
            ]
        else:
            cf_cookies = []

        # 创建新上下文，只注入 CF cookies
        context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            storage_state={"cookies": cf_cookies, "origins": []} if cf_cookies else None
        )
        return context

    def init_browser(self):
        """初始化浏览器"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=VizDisplayCompositor",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-dev-shm-usage",
                "--lang=zh-CN,zh;q=0.9,en;q=0.8"
            ]
        )
        
        # 创建上下文
        cached_cf_cookies = CacheManager.load_cf_cookies(self.site_name)
        if cached_cf_cookies:
            logger.info("🔄 尝试使用Cloudflare缓存cookies")
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                storage_state={"cookies": cached_cf_cookies, "origins": []}
            )
        else:
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                locale="zh-CN",
                timezone_id="Asia/Shanghai"
            )
        
        self.page = self.context.new_page()
        # 设置页面属性以避免被检测
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { 
                get: () => [1, 2, 3, 4, 5],
                configurable: true
            });
            Object.defineProperty(navigator, 'languages', { 
                get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
            });
            window.chrome = { 
                runtime: {},
                loadTimes: function() {},
                csi: function() {}, 
                app: {isInstalled: false}
            };
            Object.defineProperty(document, 'hidden', { get: () => false });
            Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
        """)

    def enhanced_strict_check_login_status(self):
        """增强的严格登录状态验证 - 必须检测到用户名才算登录成功"""
        logger.info("🔍 严格验证登录状态 - 必须检测到用户名...")

        try:
            # 首先确保在latest页面
            if not self.page.url.endswith('/latest'):
                self.page.goto(self.site_config['latest_url'])
                time.sleep(3)

            # 处理可能的Cloudflare
            CloudflareHandler.handle_cloudflare(self.page)

            # 方法1: 检查当前页面的用户名
            page_content = self.page.content()
            if self.username and self.username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                return True

            # 方法2: 尝试访问用户个人资料页面
            logger.info("🔄 尝试访问用户个人资料页面验证...")
            try:
                profile_url = f"{self.site_config['base_url']}/u/{self.username}"
                self.page.goto(profile_url)
                time.sleep(3)

                profile_content = self.page.content()
                if self.username and self.username.lower() in profile_content.lower():
                    logger.success(f"✅ 在个人资料页面找到用户名: {self.username}")
                    # 返回latest页面
                    self.page.goto(self.site_config['latest_url'])
                    time.sleep(3)
                    return True
                else:
                    logger.warning("❌ 个人资料页面验证失败")
                    # 返回latest页面
                    self.page.goto(self.site_config['latest_url'])
                    time.sleep(3)
            except Exception as e:
                logger.warning(f"访问个人资料页面失败: {str(e)}")
                # 返回latest页面
                self.page.goto(self.site_config['latest_url'])
                time.sleep(3)

            # 方法3: 检查用户头像和菜单
            avatar_selectors = [
                'img.avatar',
                '.user-avatar',
                '.current-user img',
                '[class*="avatar"]',
                'img[src*="avatar"]'
            ]

            for selector in avatar_selectors:
                try:
                    avatar_element = self.page.query_selector(selector)
                    if avatar_element:
                        logger.success(f"✅ 找到用户头像元素: {selector}")
                        # 如果有头像，尝试点击查看用户名
                        try:
                            avatar_element.click()
                            time.sleep(2)
                            menu_content = self.page.content()
                            if self.username and self.username.lower() in menu_content.lower():
                                logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                                # 点击其他地方关闭菜单
                                self.page.click('body')
                                return True
                            self.page.click('body')
                        except:
                            pass
                except:
                    continue

            # 方法4: 检查用户菜单直接查找用户名
            user_menu_selectors = [
                '#current-user',
                '.current-user',
                '.header-dropdown-toggle',
                '[data-user-menu]',
                '.user-menu'
            ]

            for selector in user_menu_selectors:
                try:
                    user_element = self.page.query_selector(selector)
                    if user_element:
                        user_element.click()
                        time.sleep(2)

                        menu_content = self.page.content()
                        if self.username and self.username.lower() in menu_content.lower():
                            logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                            # 点击其他地方关闭菜单
                            self.page.click('body')
                            return True
                        self.page.click('body')
                except:
                    pass

            # 方法5: 检查登录按钮（反证未登录）
            login_selectors = [
                '.login-button', 
                'button:has-text("登录")', 
                '#login-button',
                'a[href*="/login"]',
                '.btn-login'
            ]

            for selector in login_selectors:
                try:
                    login_btn = self.page.query_selector(selector)
                    if login_btn:
                        logger.error(f"❌ 检测到登录按钮: {selector}")
                        return False
                except:
                    continue

            logger.error(f"❌ 严格验证失败，未找到用户名: {self.username}")
            return False
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

    def attempt_login(self):
        """强制登录 - 每次都执行完整登录流程"""
        logger.info("🔐 执行强制登录...")

        # 导航到登录页面
        self.page.goto(self.site_config['login_url'])
        time.sleep(3)

        # 处理Cloudflare验证
        cf_success = CloudflareHandler.handle_cloudflare(self.page)
        if not cf_success:
            logger.warning("⚠️ Cloudflare验证可能未完全通过，但继续登录流程")

        # 填写登录信息
        try:
            # 等待登录表单加载
            time.sleep(2)

            # 尝试多种可能的表单选择器
            username_selectors = [
                "[id='login-account-name']", "[id='username']", "[id='login']", "[id='email']",
                "input[name='username']", "input[name='login']", "input[name='email']",
                "input[type='text']", "input[placeholder*='用户名']", "input[placeholder*='邮箱']"
            ]

            password_selectors = [
                "[id='login-account-password']", "[id='password']", "[id='passwd']", 
                "input[name='password']", "input[name='passwd']",
                "input[type='password']", "input[placeholder*='密码']"
            ]

            login_button_selectors = [
                "[id='login-button']", "button[type='submit']", "input[type='submit']",
                "button:has-text('登录')", "button:has-text('Log In')", "button:has-text('Sign In')",
                ".btn-login", ".btn-primary"
            ]

            username_field = None
            password_field = None
            login_button = None

            # 查找用户名字段
            for selector in username_selectors:
                try:
                    username_field = self.page.query_selector(selector)
                    if username_field:
                        logger.info(f"✅ 找到用户名字段: {selector}")
                        break
                except:
                    continue

            # 查找密码字段
            for selector in password_selectors:
                try:
                    password_field = self.page.query_selector(selector)
                    if password_field:
                        logger.info(f"✅ 找到密码字段: {selector}")
                        break
                except:
                    continue

            # 查找登录按钮
            for selector in login_button_selectors:
                try:
                    login_button = self.page.query_selector(selector)
                    if login_button:
                        logger.info(f"✅ 找到登录按钮: {selector}")
                        break
                except:
                    continue

            if username_field and password_field and login_button:
                username_field.fill(self.username)
                password_field.fill(self.password)

                # 点击登录按钮
                login_button.click()
                time.sleep(15)  # 增加等待时间确保登录完成

                # 检查是否有错误消息
                error_selectors = ['.alert-error', '.error', '.flash-error', '.alert.alert-error']
                for selector in error_selectors:
                    try:
                        error_element = self.page.query_selector(selector)
                        if error_element:
                            error_text = error_element.text_content()
                            logger.error(f"❌ 登录错误: {error_text}")
                            return False
                    except:
                        continue

                # 必须检测到用户名才算登录成功
                login_success = self.enhanced_strict_check_login_status()
                if login_success:
                    logger.success("✅ 登录成功 - 检测到用户名")
                    # 只保存Cloudflare cookies，不保存登录状态相关cookies
                    all_cookies = self.context.cookies()
                    success = CacheManager.save_cf_cookies(all_cookies, self.site_name)
                    if success:
                        logger.info("✅ Cloudflare cookies缓存已保存")
                    else:
                        logger.warning("⚠️ Cloudflare cookies缓存保存失败")
                    return True
                else:
                    logger.error("❌ 登录失败 - 未检测到用户名")
                    return False
            else:
                logger.error("❌ 找不到登录表单元素")
                return False
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def ensure_logged_in(self):
        """确保用户已登录 - 强制每次都登录"""
        logger.info("🎯 强制登录流程 - 每次都执行完整登录")
        
        # 强制执行登录流程
        return self.attempt_login()

    def click_one_topic(self, topic_url):
        """浏览单个主题 - 确保网站能收集浏览记录"""
        new_page = self.context.new_page()
        try:
            new_page.goto(topic_url)
            time.sleep(3)

            # 随机决定是否点赞 (0.5%概率)
            if random.random() < 0.005:
                self.click_like(new_page)

            # 浏览帖子内容 - 确保网站能收集浏览记录
            self.browse_post(new_page)
            new_page.close()
            return True
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            try:
                new_page.close()
            except:
                pass
            return False

    def click_like(self, page):
        """点赞帖子"""
        try:
            like_button = page.query_selector(".discourse-reactions-reaction-button")
            if like_button:
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                logger.info("点赞成功")
                time.sleep(random.uniform(1, 2))
            else:
                logger.info("帖子可能已经点过赞了")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    def browse_post(self, page):
        """浏览帖子内容 - 确保网站能收集浏览记录"""
        prev_url = None

        # 开始自动滚动，最多滚动8次
        for i in range(8):
            # 随机滚动一段距离
            scroll_distance = random.randint(400, 800)
            page.evaluate(f"window.scrollBy(0, {scroll_distance})")
            if random.random() < 0.03:
                break
            # 检查是否到达页面底部
            at_bottom = page.evaluate(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                break
            # 动态随机等待
            wait_time = random.uniform(2, 4)
            time.sleep(wait_time)

    def click_topic(self):
        """点击浏览主题 - 确保网站能收集浏览记录"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return True

        logger.info("🌐 开始浏览主题 - 确保网站能收集浏览记录")

        # 确保在latest页面
        if not self.page.url.endswith('/latest'):
            self.page.goto(self.site_config['latest_url'])
            time.sleep(5)

        try:
            # 获取主题列表
            topic_list = self.page.query_selector_all(".:title")
            if not topic_list:
                logger.error("❌ 没有找到主题列表")
                return False

            # 随机选择5-8个主题
            browse_count = min(random.randint(5, 8), len(topic_list))
            selected_topics = random.sample(topic_list, browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择 {browse_count} 个")

            for i, topic in enumerate(selected_topics):
                topic_url = topic.get_attribute("href")
                if not topic_url:
                    continue

                if not topic_url.startswith('http'):
                    topic_url = self.site_config['base_url'] + topic_url

                logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")

                if self.click_one_topic(topic_url):
                    success_count += 1

                # 随机等待
                if i < browse_count - 1:
                    wait_time = random.uniform(5, 12)
                    time.sleep(wait_time)

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count > 0
        except Exception as e:
            logger.error(f"获取主题列表失败: {str(e)}")
            return False

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息（强制重新登录）")
        
        # 创建一个干净的页面用于获取连接信息
        page = self.context.new_page()
        try:
            page.goto("https://connect.linux.do/")
            time.sleep(5)

            # 检查是否已登录：查找用户名元素（根据实际页面结构调整）
            username_element = page.query_selector("header .user-menu a, .navbar-user, [data-username]")
            
            if not username_element:
                logger.warning("未检测到登录状态，尝试自动登录...")
                # 重新确保已登录
                if not self.ensure_logged_in():
                    logger.error("❌ 登录失败，无法获取连接信息")
                    return
                # 重新访问连接页面
                page.goto("https://connect.linux.do/")
                time.sleep(5)

            # 检查用户名
            username_elements = page.query_selector_all("header .user-menu a, .navbar-user, [data-username]")
            if username_elements:
                username = username_elements[0].text_content().strip()
                if self.username.lower() in username.lower():
                    logger.info(f"✅ 登录成功，用户名: {username}")
                else:
                    logger.warning(f"⚠️ 用户名不匹配: 期望 {self.username}, 实际 {username}")
            else:
                logger.warning("⚠️ 无法获取用户名")

            # 现在可以安全抓取表格
            rows = page.query_selector_all("table tr")
            info = []
            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    project = cells[0].text_content().strip()
                    current = cells[1].text_content().strip()
                    requirement = cells[2].text_content().strip()
                    info.append([project, current, requirement])

            print("\n" + "="*50)
            print(f"📊 {self.site_name.upper()} 连接信息")
            print("="*50)
            print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
            print("="*50 + "\n")

            # 可选：保存当前 cookies（仅用于下次保留 CF 验证）
            new_cookies = self.context.cookies()
            cf_only_cookies = [c for c in new_cookies if re.search(r"__cf_|cf_clearance", c["name"], re.I)]
            if cf_only_cookies:
                CacheManager.save_cf_cookies(cf_only_cookies, self.site_name)

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
        finally:
            page.close()

    def run(self):
        """执行完整的自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 初始化浏览器
            self.init_browser()

            # 1. 强制登录（每次都要执行完整登录流程）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False

            # 2. 浏览主题（确保网站能收集浏览记录）
            if not self.click_topic():
                logger.warning(f"⚠️ {self.site_name} 浏览主题失败")

            # 3. 打印连接信息
            self.print_connect_info()

            logger.success(f"✅ {self.site_name} 处理完成")
            return True
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
        finally:
            # 关闭浏览器
            try:
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
            except:
                pass

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动")
    # 设置环境变量
    os.environ.pop("DISPLAY", None)
    success_sites = []
    failed_sites = []

    # 遍历所有站点
    for site_config in SITES:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        # 检查凭证是否存在
        if not credentials.get('username') or not credentials.get('password'):
            logger.warning(f"⏭️ 跳过 {site_name} - 未配置凭证")
            continue

        logger.info(f"🔧 初始化 {site_name} 浏览器")
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

        # 站点间随机等待
        if site_config != SITES[-1]:
            wait_time = random.uniform(10, 30)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个站点...")
            time.sleep(wait_time)

    # 输出总结
    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功站点: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败站点: {', '.join(failed_sites) if failed_sites else '无'}")

    # 如果有成功站点，不算完全失败
    if success_sites:
        logger.success("🎉 部分任务完成")
        sys.exit(0)
    else:
        logger.error("💥 所有任务失败")
        sys.exit(1)

if __name__ == "__main__":
    main()
