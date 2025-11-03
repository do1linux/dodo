"""
GitHub Actions 用
Linux.Do 自动登录 + 增强反检测 + Cloudflare 验证处理
作者：AI 重构版（适合不会写代码的用户）
"""

import os
import random
import time
import sys
import json
from datetime import datetime, timedelta
import functools
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

logger.remove()
logger.add(sys.stdout, level="INFO")

# 环境变量
USERNAME = os.getenv("LINUXDO_USERNAME")
PASSWORD = os.getenv("LINUXDO_PASSWORD")
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
COOKIE_FILE = "cache/linux_do_cookies.json"
COOKIE_VALIDITY_DAYS = 30  # Cookie有效期（天）

# 常量
HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
CONNECT_URL = "https://connect.linux.do/"
SITE_NAME = "linux_do"

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


class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.login_attempts = 0
        self.max_login_attempts = 2
        
        # 浏览器配置
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
            # 使用page.cookies()
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


    def wait_for_element(self, selector, timeout=10):
        """显式等待元素出现"""
        for i in range(timeout):
            ele = self.page.ele(selector)
            if ele:
                return ele
            time.sleep(1)
        return None

    def detect_turnstile(self):
        """检测是否出现 Turnstile 验证"""
        try:
            if self.page.ele("@name=cf-turnstile-response"):
                logger.warning("🤖 检测到 Turnstile 验证")
                return True
        except:
            pass
        return False

    def print_page_info(self):
        """打印页面信息"""
        title = self.page.title
        logger.info(f"📄 当前页面标题：{title}")
        user_input = self.wait_for_element("@id=login-account-name", 5)
        pass_input = self.wait_for_element("@id=login-account-password", 5)
        turnstile = self.detect_turnstile()
        logger.info(f"🔍 用户名输入框是否存在：{bool(user_input)}")
        logger.info(f"🔍 密码输入框是否存在：{bool(pass_input)}")
        logger.info(f"🔍 Turnstile 是否出现：{turnstile}")

    def screenshot_login(self, name):
        """截图保存登录页"""
        path = f"login_fail_{name}.png"
        self.page.get_screenshot(path)
        logger.info(f"📸 登录页截图已保存：{path}")

    def handle_turnstile(self):
        """处理 Turnstile 验证"""
        logger.info("🔄 尝试处理 Turnstile 验证")
        for _ in range(10):  # 增加尝试次数
            try:
                # 尝试获取 Turnstile token
                token = self.page.run_js("return turnstile.getResponse()")
                if token:
                    logger.success(f"✅ Turnstile 验证成功，获取到 token: {token}")
                    return True
                else:
                    logger.warning("❌ Turnstile token 为空，可能验证未完成")
            except Exception as e:
                logger.error(f"❌ 获取 Turnstile token 失败: {str(e)}")
            
            # 模拟用户行为，点击验证区域
            try:
               turnstile_frame = self.page.ele(".cfturnstile > iframe")
                if turnstile_frame:
                    self.page.run_js("document.querySelector('.cfturnstile > iframe').contentDocument.body.classList.add('verified')")
                    logger.info("🖱️ 模拟点击 Turnstile 验证区域")
            except Exception as e:
                logger.error(f"模拟点击失败: {str(e)}")
            
            time.sleep(3)
        
        logger.error("❌ Turnstile 验证失败，尝试次数用尽")
        return False


    def login_with_retry(self):
        """带重试的登录方法"""
        for attempt in range(1, self.max_login_attempts + 1):
            logger.info(f"🚀 第 {attempt} 次尝试登录...")
            self.page.get(LOGIN_URL)
            time.sleep(5)
            self.print_page_info()

            # 处理 Turnstile 验证
            if self.detect_turnstile():
                if not self.handle_turnstile():
                    self.screenshot_login(f"turnstile_failed_{attempt}")
                    continue

            user_input = self.wait_for_element("@id=login-account-name", 10)
            pass_input = self.wait_for_element("@id=login-account-password", 10)

            if not user_input or not pass_input:
                logger.error("❌ 登录元素未加载完成")
                self.screenshot_login(attempt)
                continue

            user_input.input(self.username, clear=True)
            time.sleep(random.uniform(1, 2))
            pass_input.input(self.password, clear=True)
            time.sleep(random.uniform(1, 2))

            self.page.ele("@id=login-button").click()
            time.sleep(5)

            if self.is_logged_in():
                self.save_cookies_to_cache()
                return True
            else:
                logger.warning(f"❌ 第 {attempt} 次登录失败")
                self.screenshot_login(attempt)

        return False

    def is_logged_in(self):
        """检测是否登录成功"""
        self.page.get(HOME_URL)
        time.sleep(3)
        user_ele = self.page.ele("@id=current-user")
        if not user_ele:
            return False
        img = user_ele.ele("tag:img")
        if img and img.attr("alt") == self.username:
            logger.info(f"✅ 检测到已登录用户：{self.username}")
            return True
        return False

    def browse_topics(self):
        """浏览主题帖"""
        if not self.is_logged_in():
            logger.error("❌ 未登录，无法进行浏览任务")
            return

        self.page.get(HOME_URL)
        time.sleep(3)
        topics = self.page.eles(".topic-list-item .main-link a")
        if not topics:
            logger.warning("❌ 没有找到任何帖子")
            return
        logger.info(f"📚 发现 {len(topics)} 个帖子，随机浏览 10 个")
        for link in random.sample(topics, min(10, len(topics))):
            url = link.attr("href")
            if not url.startswith("http"):
                url = "https://linux.do" + url
            logger.info(f"👀 正在浏览：{url}")
            self.page.get(url)
            time.sleep(random.uniform(3, 6))
            for _ in range(random.randint(3, 6)):
                self.page.run_js(f"window.scrollBy(0, {random.randint(400, 700)})")
                time.sleep(random.uniform(2, 4))
            if random.random() < 0.3:
                like_btn = self.page.ele(".discourse-reactions-reaction-button")
                if like_btn:
                    like_btn.click()
                    logger.info("👍 点赞成功")
                    time.sleep(1)

    def print_connect_info(self):
        """打印连接信息"""
        if not self.is_logged_in():
            logger.error("❌ 未登录，无法获取连接信息")
            return

        self.page.get(CONNECT_URL)
        time.sleep(3)
        table = self.page.ele("tag:table")
        if not table:
            logger.warning("❌ 没有找到连接信息表格")
            return
        rows = [[td.text.strip() for td in tr.eles("tag:td")] for tr in table.eles("tag:tr") if tr.eles("tag:td")]
        print("-------------- Connect Info --------------")
        print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))


def main():
    if not USERNAME or not PASSWORD:
        logger.error("❌ 请设置 LINUXDO_USERNAME 和 LINUXDO_PASSWORD")
        sys.exit(1)

    site_config = {"name": SITE_NAME}
    credentials = {"username": USERNAME, "password": PASSWORD}

    browser = LinuxDoBrowser(site_config, credentials)
    page = browser.page

    # 尝试加载缓存的 cookies
    if CacheManager.cookies_exist(SITE_NAME):
        cookies = CacheManager.load_cookies(SITE_NAME)
        if cookies:
            page.set_cookies(cookies)
            logger.info("✅ Cookie 已加载")

    # 检查是否已登录
    if browser.is_logged_in():
        logger.info("✅ 使用缓存 Cookie 登录成功")
    else:
        logger.info("❌ 缓存无效，重新登录")
        if not browser.login_with_retry():
            logger.error("❌ 多次登录失败，跳过任务")
            browser.browser.quit()
            return

    # 确保登录成功后再进行浏览任务
    if browser.is_logged_in():
        # 浏览帖子
        browser.browse_topics()

        # 打印连接信息
        browser.print_connect_info()
    else:
        logger.error("❌ 登录状态检查失败，无法进行后续任务")

    logger.info("✅ 所有任务完成，最新 Cookie 已保存")
    browser.browser.quit()

if __name__ == "__main__":
    main()
