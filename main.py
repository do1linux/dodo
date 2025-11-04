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
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from urllib.parse import urljoin

# ======================== 全局配置 ========================
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
SELECTOR = os.environ.get("SITE_SELECTOR", "all")
COOKIE_VALIDITY_DAYS = 7

# ======================== 站点配置 ========================
SITES = [
    {
        "name": "linux_do",
        "base_url": "https://linux.do",
        "login_url": "https://linux.do/login",
        "latest_topics_url": "https://linux.do/latest",
        "connect_url": "https://connect.linux.do",
        "username": os.environ.get("LINUXDO_USERNAME"),
        "password": os.environ.get("LINUXDO_PASSWORD")
    },
    {
        "name": "idcflare",
        "base_url": "https://idcflare.com", 
        "login_url": "https://idcflare.com/login",
        "latest_topics_url": "https://idcflare.com/latest",
        "connect_url": "https://connect.idcflare.com",
        "username": os.environ.get("IDCFLARE_USERNAME"),
        "password": os.environ.get("IDCFLARE_PASSWORD")
    }
]

# 站点选择过滤
if SELECTOR != "all":
    SITES = [s for s in SITES if s["name"] == SELECTOR]

# 检查账号密码配置
for site in SITES:
    if not (site["username"] and site["password"]):
        logger.error(f"❌ {site['name']} 账号或密码未配置")
        sys.exit(1)

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
    def load_cookies(site_name):
        """加载cookies缓存并检查有效期"""
        cache_data = CacheManager.load_cache(f"{site_name}_cookies.json")
        if not cache_data:
            return None
            
        # 检查缓存有效期
        cache_time_str = cache_data.get('cache_time')
        if cache_time_str:
            try:
                cache_time = datetime.fromisoformat(cache_time_str)
                if datetime.now() - cache_time > timedelta(days=COOKIE_VALIDITY_DAYS):
                    logger.warning("🕒 Cookies已过期")
                    return None
            except Exception as e:
                logger.warning(f"缓存时间解析失败: {str(e)}")
        
        return cache_data.get('cookies')

    @staticmethod
    def save_cookies(cookies, site_name):
        """保存cookies到缓存"""
        cache_data = {
            'cookies': cookies,
            'cache_time': datetime.now().isoformat(),
            'site': site_name
        }
        return CacheManager.save_cache(cache_data, f"{site_name}_cookies.json")

    @staticmethod
    def cookies_exist(site_name):
        """检查cookies文件是否存在"""
        file_path = CacheManager.get_cache_file_path(f"{site_name}_cookies.json")
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
                page_title = page.title
                
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
            page_title = page.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                logger.success("✅ 最终验证: Cloudflare验证通过")
                return True
            else:
                logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
                return True
        except Exception:
            logger.warning("⚠️ 无法获取页面标题，继续后续流程")
            return True

# ======================== 重试装饰器 ========================
def retry_decorator(retries=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    time.sleep(2)
            return None
        return wrapper
    return decorator

# ======================== 登录验证器 ========================
class LoginValidator:
    """登录验证类"""
    
    @staticmethod
    def enhanced_strict_check_login_status(page, username, site_config):
        """增强的严格登录状态验证 - 多种方式验证用户名"""
        logger.info("🔍 增强严格验证登录状态...")
        
        try:
            # 首先确保在latest页面
            if not page.url.endswith('/latest'):
                page.get(site_config['latest_topics_url'])
                time.sleep(5)
            
            # 处理可能的Cloudflare
            CloudflareHandler.handle_cloudflare(page)
            
            # 方法1: 检查当前页面的用户名
            page_content = page.html
            if username and username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {username}")
                return True
            
            # 方法2: 尝试访问用户个人资料页面
            logger.info("🔄 尝试访问用户个人资料页面验证...")
            try:
                profile_url = f"{site_config['base_url']}/u/{username}"
                page.get(profile_url)
                time.sleep(3)
                
                profile_content = page.html
                if username and username.lower() in profile_content.lower():
                    logger.success(f"✅ 在个人资料页面找到用户名: {username}")
                    # 返回latest页面
                    page.get(site_config['latest_topics_url'])
                    time.sleep(3)
                    return True
                else:
                    logger.warning("❌ 个人资料页面验证失败")
                    # 返回latest页面
                    page.get(site_config['latest_topics_url'])
                    time.sleep(3)
            except Exception as e:
                logger.warning(f"访问个人资料页面失败: {str(e)}")
                # 返回latest页面
                page.get(site_config['latest_topics_url'])
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
                    avatar_element = page.ele(selector, timeout=3)
                    if avatar_element and avatar_element.is_displayed:
                        logger.success(f"✅ 找到用户头像元素: {selector}")
                        # 如果有头像，尝试点击查看用户名
                        try:
                            avatar_element.click()
                            time.sleep(2)
                            menu_content = page.html
                            if username and username.lower() in menu_content.lower():
                                logger.success(f"✅ 在用户菜单中找到用户名: {username}")
                                # 点击其他地方关闭菜单
                                page.ele('body').click()
                                return True
                            page.ele('body').click()
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
                    user_element = page.ele(selector, timeout=3)
                    if user_element and user_element.is_displayed:
                        user_element.click()
                        time.sleep(2)
                        
                        menu_content = page.html
                        if username and username.lower() in menu_content.lower():
                            logger.success(f"✅ 在用户菜单中找到用户名: {username}")
                            page.ele('body').click()
                            return True
                        page.ele('body').click()
                except:
                    continue
            
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
                    login_btn = page.ele(selector, timeout=3)
                    if login_btn and login_btn.is_displayed:
                        logger.error(f"❌ 检测到登录按钮: {selector}")
                        return False
                except:
                    continue
            
            # 如果以上方法都失败，但页面显示正常内容，尝试最后的验证
            page_title = page.title
            if page_title and "登录" not in page_title and "Login" not in page_title:
                # 检查是否有主题列表
                topic_list = page.eles(".:title")
                if topic_list and len(topic_list) > 0:
                    logger.warning("⚠️ 页面显示正常内容且有主题列表，但无法验证用户名，假设已登录")
                    return True
            
            logger.error(f"❌ 所有验证方法都失败，未找到用户名: {username}")
            return False
            
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = site_config['username']
        self.password = site_config['password']
        self.login_attempts = 0
        self.max_login_attempts = 2
        
        # 初始化浏览器
        self._setup_browser()
        
    def _setup_browser(self):
        """配置浏览器设置"""
        platformIdentifier = "Windows NT 10.0; Win64; x64"

        co = (
            ChromiumOptions()
            .headless(HEADLESS)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-features=VizDisplayCompositor")
            .set_argument("--disable-background-timer-throttling")
            .set_argument("--disable-backgrounding-occluded-windows")
            .set_argument("--disable-renderer-backgrounding")
            .set_argument("--disable-dev-shm-usage")
            .set_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # 立即注入增强的反检测脚本
        self.inject_enhanced_script()

    def inject_enhanced_script(self, page=None):
        """注入增强的反检测脚本"""
        if page is None:
            page = self.page
            
        enhanced_script = """
        // 增强的反检测脚本
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
        // 模拟完整的浏览器环境
        Object.defineProperty(navigator, 'plugins', { 
            get: () => [1, 2, 3, 4, 5],
            configurable: true
        });
        
        Object.defineProperty(navigator, 'languages', { 
            get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
        });
        
        // 屏蔽自动化特征
        window.chrome = { 
            runtime: {},
            loadTimes: function() {},
            csi: function() {}, 
            app: {isInstalled: false}
        };
        
        // 页面可见性API
        Object.defineProperty(document, 'hidden', { get: () => false });
        Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
        
        console.log('🔧 增强的JS环境模拟已加载');
        """
        
        try:
            page.run_js(enhanced_script)
            logger.info("✅ 增强的反检测脚本已注入")
            return True
        except Exception as e:
            logger.warning(f"注入脚本失败: {str(e)}")
            return False

    def get_all_cookies(self):
        """获取所有cookies"""
        try:
            cookies = self.page.cookies()
            if cookies:
                logger.info(f"✅ 获取到 {len(cookies)} 个cookies")
                return cookies
            
            logger.warning("❌ 无法获取cookies")
            return None
            
        except Exception as e:
            logger.error(f"获取cookies时出错: {str(e)}")
            return None

    def save_cookies_to_cache(self):
        """保存cookies到缓存"""
        try:
            # 等待一段时间确保cookies设置完成
            time.sleep(3)
            
            # 保存cookies
            cookies = self.get_all_cookies()
            if cookies:
                logger.info(f"🔍 成功获取到 {len(cookies)} 个cookies")
                success = CacheManager.save_cookies(cookies, self.site_name)
                if success:
                    logger.info("✅ Cookies缓存已保存")
                else:
                    logger.warning("⚠️ Cookies缓存保存失败")
            else:
                logger.warning("⚠️ 无法获取cookies，检查浏览器状态")
                    
            return True
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")
            return False

    def clear_caches(self):
        """清除所有缓存文件"""
        try:
            cache_dir = CacheManager.get_cache_directory()
            cache_files = [f"{self.site_name}_cookies.json"]
            for file_name in cache_files:
                file_path = os.path.join(cache_dir, file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            logger.info("✅ 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    @retry_decorator(retries=2)
    def attempt_login_with_cookies(self):
        """尝试使用缓存的cookies登录"""
        logger.info(f"🔐 尝试使用缓存cookies登录 {self.site_name}")
        
        cached_cookies = CacheManager.load_cookies(self.site_name)
        if not cached_cookies:
            logger.warning("❌ 没有可用的缓存cookies")
            return False
        
        # 设置cookies
        try:
            self.page.get(self.site_config['base_url'])
            time.sleep(3)
            
            for cookie in cached_cookies:
                self.page.set.cookie(cookie)
            
            # 验证登录状态
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
            
            if LoginValidator.enhanced_strict_check_login_status(self.page, self.username, self.site_config):
                logger.success("🎉 缓存cookies登录成功")
                return True
            else:
                logger.warning("🔄 缓存cookies失效，需要重新登录")
                return False
                
        except Exception as e:
            logger.error(f"缓存登录失败: {str(e)}")
            return False

    @retry_decorator(retries=2)
    def perform_full_login(self):
        """执行完整登录流程"""
        logger.info("🔐 开始完整登录流程...")
        
        # 导航到登录页面
        self.page.get(self.site_config['login_url'])
        time.sleep(5)
        
        # 重新注入脚本以确保在登录页面生效
        self.inject_enhanced_script()
        
        # 处理Cloudflare验证
        cf_success = CloudflareHandler.handle_cloudflare(self.page)
        if not cf_success:
            logger.warning("⚠️ Cloudflare验证可能未完全通过，但继续登录流程")
        
        # 查找并填写登录表单
        if not self._fill_login_form():
            return False
        
        # 提交登录
        if not self._submit_login():
            return False
        
        # 等待登录完成
        time.sleep(5)
        
        # 验证登录成功
        if LoginValidator.enhanced_strict_check_login_status(self.page, self.username, self.site_config):
            logger.success("✅ 登录成功")
            
            # 保存cookies
            self.save_cookies_to_cache()
            return True
        else:
            logger.error("❌ 登录验证失败")
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
                try:
                    username_field = self.page.ele(selector, timeout=3)
                    if username_field:
                        logger.info(f"✅ 找到用户名输入框: {selector}")
                        break
                except:
                    continue
            
            if not username_field:
                logger.error("❌ 未找到用户名输入框")
                return False
            
            # 模拟人类输入用户名
            self._human_type(username_field, self.username)
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
                try:
                    password_field = self.page.ele(selector, timeout=3)
                    if password_field:
                        logger.info(f"✅ 找到密码输入框: {selector}")
                        break
                except:
                    continue
            
            if not password_field:
                logger.error("❌ 未找到密码输入框")
                return False
            
            # 模拟人类输入密码
            self._human_type(password_field, self.password)
            time.sleep(random.uniform(1, 2))
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 填写登录表单失败: {e}")
            return False

    def _human_type(self, element, text):
        """模拟人类输入"""
        element.clear()
        time.sleep(0.5)
        
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.15))

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
                try:
                    login_button = self.page.ele(selector, timeout=3)
                    if login_button:
                        logger.info(f"✅ 找到登录按钮: {selector}")
                        break
                except:
                    continue
            
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
        time.sleep(random.uniform(0.5, 1.5))
        element.click()
        time.sleep(random.uniform(1, 3))

    def browse_topics(self):
        """浏览主题模拟用户行为"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return
        
        logger.info("🌐 开始浏览主题")
        
        try:
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            # 查找主题链接
            theme_links = self.page.eles('.title.raw-link.raw-topic-link')[:15]
            if not theme_links:
                logger.warning("📭 未找到主题链接")
                return
            
            logger.info(f"🔗 找到 {len(theme_links)} 个主题链接")
            
            # 随机选择主题浏览
            selected_themes = random.sample(theme_links, min(10, len(theme_links)))
            logger.info(f"🎯 选择浏览 {len(selected_themes)} 个主题")
            
            for i, link in enumerate(selected_themes, 1):
                theme_url = link.attr("href")
                if not theme_url.startswith('http'):
                    theme_url = urljoin(self.site_config['base_url'], theme_url)
                
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

    @retry_decorator(retries=2)
    def _browse_single_theme(self, url):
        """浏览单个主题"""
        tab = self.browser.new_tab()
        try:
            tab.get(url)
            time.sleep(random.uniform(2, 4))
            
            # 随机点赞（3%概率）
            if random.random() < 0.03:
                try:
                    like_button = tab.ele('.discourse-reactions-reaction-button', timeout=2)
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
            logger.info(f"📊 获取 {self.site_name} 的连接信息")
            self.page.get(self.site_config['connect_url'])
            time.sleep(3)
            
            # 查找表格数据
            rows = []
            table_selectors = ['table', '.table', '.connect-table']
            
            for selector in table_selectors:
                try:
                    tables = self.page.eles(selector)
                    for table in tables:
                        for tr in table.eles('tag:tr')[1:]:  # 跳过表头
                            tds = tr.eles('tag:td')[:3]
                            if len(tds) >= 3:
                                row_data = [td.text.strip() for td in tds]
                                rows.append(row_data)
                except:
                    continue
            
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
        logger.info(f"🎬 开始处理 {self.site_name}")
        
        try:
            # 1. 尝试使用缓存cookies登录
            if CacheManager.cookies_exist(self.site_name):
                if self.attempt_login_with_cookies():
                    logger.info("✅ 缓存登录成功")
                else:
                    # 缓存失效，执行完整登录
                    logger.info("🔄 缓存登录失败，执行完整登录")
                    if not self.perform_full_login():
                        raise Exception("完整登录失败")
            else:
                # 无缓存，执行完整登录
                logger.info("🔄 无缓存，执行完整登录")
                if not self.perform_full_login():
                    raise Exception("完整登录失败")
            
            # 2. 浏览主题（模拟用户行为）
            self.browse_topics()
            
            # 3. 获取连接信息
            self.get_connect_info()
            
            logger.success(f"✅ {self.site_name} 处理完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 处理失败: {e}")
            # 截图保存错误信息
            try:
                self.page.get_screenshot(f"{self.site_name}_error.png")
                logger.info(f"📸 错误截图已保存: {self.site_name}_error.png")
            except:
                pass
            return False
        
        finally:
            # 关闭浏览器
            try:
                if self.browser:
                    self.browser.quit()
                    logger.info(f"🔚 关闭 {self.site_name} 浏览器")
            except Exception as e:
                logger.warning(f"⚠️ 关闭浏览器时出错: {e}")

# ======================== 主入口 ========================
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
            browser = LinuxDoBrowser(site)
            if browser.run():
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
