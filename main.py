import os
import random
import time
import functools
import sys
import json
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from loguru import logger

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
CACHE_DIR = os.environ.get("CACHE_DIR", "cache")

# DoH 服务器配置
DOH_SERVER = os.environ.get("DOH_SERVER", "https://ld.ddd.oaifree.com/query-dns")

# Cookie有效期设置（天）
COOKIE_VALIDITY_DAYS = 7

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类"""
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), CACHE_DIR)
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"创建缓存目录失败 {cache_dir}: {str(e)}")
            cache_dir = os.path.dirname(os.path.abspath(__file__))
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
        cache_data = CacheManager.load_cache(f"cf_cookies_{site_name}.json")
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
        return CacheManager.save_cache(cache_data, f"cf_cookies_{site_name}.json")

    @staticmethod
    def cookies_exist(site_name):
        """检查cookies文件是否存在"""
        file_path = CacheManager.get_cache_file_path(f"cf_cookies_{site_name}.json")
        return os.path.exists(file_path)

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类 - 使用DoH服务器版本"""
    
    @staticmethod
    def query_doh(domain, doh_server=DOH_SERVER):
        """通过DoH服务器查询DNS记录"""
        try:
            query_url = f"{doh_server}?name={domain}&type=A"
            headers = {
                'Accept': 'application/dns-json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(query_url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'Answer' in data:
                    ips = [answer['data'] for answer in data['Answer'] if answer['type'] == 1]
                    if ips:
                        logger.info(f"✅ DoH解析 {domain} -> {ips[0]}")
                        return ips
            logger.warning(f"⚠️ DoH无法解析 {domain}")
            return None
        except Exception as e:
            logger.warning(f"DoH查询失败 {domain}: {str(e)}")
            return None

    @staticmethod
    def handle_cloudflare_with_doh(driver, doh_server=DOH_SERVER, max_attempts=12, timeout=240):
        """使用DoH处理Cloudflare验证"""
        start_time = time.time()
        logger.info(f"🛡️ 开始处理 Cloudflare验证 (使用DoH: {doh_server})")
        
        # 首先尝试通过DoH解析关键域名
        critical_domains = [
            'linux.do',
            'idcflare.com', 
            'challenges.cloudflare.com',
            'cloudflare.com'
        ]
        
        for domain in critical_domains:
            CloudflareHandler.query_doh(domain, doh_server)

        for attempt in range(max_attempts):
            try:
                current_url = driver.current_url
                page_title = driver.title.lower() if driver.title else ""
                
                # 检查页面状态
                if page_title and "just a moment" not in page_title and "checking" not in page_title and "please wait" not in page_title:
                    # 检查是否重定向到目标页面
                    if any(x in current_url for x in ['/latest', '/login', 'connect.']):
                        logger.success("✅ Cloudflare验证通过，已跳转到目标页面")
                        return True
                    
                    # 检查页面内容是否正常加载
                    page_source = driver.page_source.lower()
                    if len(page_source) > 1000:  # 页面内容足够长
                        logger.success("✅ 页面内容已正常加载，Cloudflare验证通过")
                        return True

                # 动态等待时间，逐渐增加
                base_wait = 5
                if attempt > 5:
                    base_wait = 10
                if attempt > 8:
                    base_wait = 15
                    
                wait_time = random.uniform(base_wait, base_wait + 5)
                elapsed = time.time() - start_time
                
                logger.info(f"⏳ 等待Cloudflare验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts} [已耗时: {elapsed:.0f}秒]")
                time.sleep(wait_time)
                
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
                # 偶尔刷新页面
                if attempt % 3 == 2:
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面以重新触发验证")
                        time.sleep(3)
                    except:
                        pass
                        
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)

        # 最终检查 - 即使验证未完全通过也尝试继续
        try:
            final_url = driver.current_url
            final_title = driver.title.lower() if driver.title else ""
            
            if "just a moment" in final_title or "checking" in final_title:
                logger.warning("⚠️ Cloudflare验证仍未通过，但强制继续流程")
                # 尝试强制跳转到登录页面
                if "linux.do" in final_url:
                    driver.get("https://linux.do/login")
                elif "idcflare.com" in final_url:
                    driver.get("https://idcflare.com/login")
                time.sleep(5)
                return True
            else:
                logger.success("✅ 最终检查: 页面已加载，继续流程")
                return True
                
        except Exception as e:
            logger.warning(f"⚠️ 最终检查异常: {str(e)}，强制继续流程")
            return True

    @staticmethod
    def handle_cloudflare(driver, max_attempts=8, timeout=180):
        """保持原有接口兼容性"""
        return CloudflareHandler.handle_cloudflare_with_doh(
            driver, 
            doh_server=DOH_SERVER,
            max_attempts=max_attempts, 
            timeout=timeout
        )

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

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.login_attempts = 0
        self.max_login_attempts = 2
        
        # Chrome配置 - 使用DoH优化
        chrome_options = Options()
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        chrome_options.add_argument('--disable-background-timer-throttling')
        chrome_options.add_argument('--disable-backgrounding-occluded-windows')
        chrome_options.add_argument('--disable-renderer-backgrounding')
        chrome_options.add_argument('--lang=zh-CN,zh;q=0.9,en;q=0.8')
        chrome_options.add_argument(f'--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')
        
        # DoH相关配置
        chrome_options.add_argument('--dns-over-https=off')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception as e:
            logger.error(f"Chrome驱动初始化失败: {str(e)}")
            raise
        
        self.wait = WebDriverWait(self.driver, 20)

    def generate_browser_state(self, success=True, browse_count=0):
        """生成浏览器状态文件"""
        try:
            state_data = {
                'site': self.site_name,
                'last_updated': datetime.now().isoformat(),
                'status': 'completed' if success else 'failed',
                'version': '1.0',
                'browse_count': browse_count,
                'login_success': success,
                'execution_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            CacheManager.save_cache(state_data, f"browser_state_{self.site_name}.json")
            logger.info(f"✅ 生成浏览器状态文件: browser_state_{self.site_name}.json")
        except Exception as e:
            logger.error(f"生成浏览器状态文件失败: {str(e)}")

    def get_all_cookies(self):
        """获取所有cookies"""
        try:
            cookies = self.driver.get_cookies()
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
                # 只保存Cloudflare相关的cookies
                cf_cookies = [cookie for cookie in cookies if 'cf_' in cookie['name'].lower()]
                success = CacheManager.save_cookies(cf_cookies, self.site_name)
                if success:
                    logger.info("✅ Cloudflare Cookies缓存已保存")
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
            cache_files = [f"cf_cookies_{self.site_name}.json", f"browser_state_{self.site_name}.json"]
            for file_name in cache_files:
                file_path = os.path.join(cache_dir, file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")

            logger.info("✅ 所有缓存已清除")
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    def enhanced_strict_check_login_status(self):
        """增强的严格登录状态验证 - 多种方式验证用户名"""
        logger.info("🔍 增强严格验证登录状态...")

        try:
            # 首先确保在latest页面
            if not self.driver.current_url.endswith('/latest'):
                self.driver.get(self.site_config['latest_url'])
                time.sleep(3)

            # 处理可能的Cloudflare
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 方法1: 检查当前页面的用户名
            page_content = self.driver.page_source
            if self.username and self.username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                return True

            # 方法2: 尝试访问用户个人资料页面
            logger.info("🔄 尝试访问用户个人资料页面验证...")
            try:
                profile_url = f"{self.site_config['base_url']}/u/{self.username}"
                self.driver.get(profile_url)
                time.sleep(3)

                profile_content = self.driver.page_source
                if self.username and self.username.lower() in profile_content.lower():
                    logger.success(f"✅ 在个人资料页面找到用户名: {self.username}")
                    # 返回latest页面
                    self.driver.get(self.site_config['latest_url'])
                    time.sleep(3)
                    return True
                else:
                    logger.warning("❌ 个人资料页面验证失败")
                    # 返回latest页面
                    self.driver.get(self.site_config['latest_url'])
                    time.sleep(3)
            except Exception as e:
                logger.warning(f"访问个人资料页面失败: {str(e)}")
                # 返回latest页面
                self.driver.get(self.site_config['latest_url'])
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
                    avatar_element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    if avatar_element.is_displayed():
                        logger.success(f"✅ 找到用户头像元素: {selector}")
                        # 如果有头像，尝试点击查看用户名
                        try:
                            avatar_element.click()
                            time.sleep(2)
                            menu_content = self.driver.page_source
                            if self.username and self.username.lower() in menu_content.lower():
                                logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                                # 点击其他地方关闭菜单
                                self.driver.find_element(By.TAG_NAME, 'body').click()
                                return True
                            self.driver.find_element(By.TAG_NAME, 'body').click()
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
                    user_element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    if user_element.is_displayed():
                        user_element.click()
                        time.sleep(2)

                        menu_content = self.driver.page_source
                        if self.username and self.username.lower() in menu_content.lower():
                            logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                            # 点击其他地方关闭菜单
                            self.driver.find_element(By.TAG_NAME, 'body').click()
                            return True
                        self.driver.find_element(By.TAG_NAME, 'body').click()
                except:
                    pass

            # 方法5: 检查登录按钮（反证未登录）
            login_selectors = [
                '.login-button', 
                'button:contains("登录")', 
                '#login-button',
                'a[href*="/login"]',
                '.btn-login'
            ]

            for selector in login_selectors:
                try:
                    login_btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if login_btn.is_displayed():
                        logger.error(f"❌ 检测到登录按钮: {selector}")
                        return False
                except:
                    continue

            logger.error(f"❌ 所有验证方法都失败，未找到用户名: {self.username}")
            return False
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

    def attempt_login(self):
        """尝试登录 - 使用DoH的改进版本"""
        logger.info("🔐 尝试登录...")

        # 导航到登录页面
        login_url = self.site_config['login_url']
        logger.info(f"🌐 访问登录页面: {login_url}")
        
        self.driver.get(login_url)
        time.sleep(3)

        # 使用带DoH的Cloudflare处理
        cf_success = CloudflareHandler.handle_cloudflare_with_doh(
            self.driver, 
            doh_server=DOH_SERVER,
            max_attempts=10,
            timeout=200
        )
        
        if not cf_success:
            logger.warning("⚠️ Cloudflare验证可能未完全通过，但继续登录流程")

        # 填写登录信息
        try:
            # 等待登录表单加载
            time.sleep(5)
            
            # 记录当前页面状态
            current_url = self.driver.current_url
            page_title = self.driver.title
            logger.info(f"📄 当前页面: {page_title} | {current_url}")

            # 如果被重定向到其他页面，尝试回到登录页面
            if 'login' not in current_url:
                logger.info("🔄 被重定向，尝试回到登录页面")
                self.driver.get(self.site_config['login_url'])
                time.sleep(5)
                CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 更全面的表单选择器
            username_selectors = [
                "#login-account-name", "#username", "#login", "#email",
                "input[name='username']", "input[name='login']", "input[name='email']",
                "input[type='text']", "input[placeholder*='用户名']", "input[placeholder*='邮箱']",
                "input[autocomplete='username']", "input[autocomplete='email']"
            ]

            password_selectors = [
                "#login-account-password", "#password", "#passwd", 
                "input[name='password']", "input[name='passwd']",
                "input[type='password']", "input[placeholder*='密码']",
                "input[autocomplete='current-password']"
            ]

            login_button_selectors = [
                "#login-button", "button[type='submit']", "input[type='submit']",
                ".btn-login", ".btn-primary", "button.btn"
            ]

            # 查找表单元素
            username_field = None
            for selector in username_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        username_field = element
                        logger.info(f"✅ 找到用户名字段: {selector}")
                        break
                except:
                    continue

            password_field = None
            for selector in password_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        password_field = element
                        logger.info(f"✅ 找到密码字段: {selector}")
                        break
                except:
                    continue

            login_button = None
            for selector in login_button_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        login_button = element
                        logger.info(f"✅ 找到登录按钮: {selector}")
                        break
                except:
                    continue

            # 如果通过CSS选择器没找到，尝试通过文本查找按钮
            if not login_button:
                try:
                    buttons = self.driver.find_elements(By.TAG_NAME, "button")
                    for btn in buttons:
                        btn_text = btn.text.lower()
                        if any(text in btn_text for text in ['登录', 'log in', 'sign in']):
                            if btn.is_displayed() and btn.is_enabled():
                                login_button = btn
                                logger.info("✅ 找到登录按钮 (通过文本)")
                                break
                except:
                    pass

            if not username_field:
                # 保存页面源码用于调试
                page_source = self.driver.page_source
                debug_file = f"login_debug_{self.site_name}_{int(time.time())}.html"
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(page_source)
                logger.error(f"❌ 找不到用户名字段，已保存页面源码到: {debug_file}")
                return False

            if not password_field:
                logger.error("❌ 找不到密码字段")
                return False

            if not login_button:
                logger.error("❌ 找不到登录按钮")
                return False

            # 模拟真实输入
            logger.info("⌨️ 输入用户名...")
            username_field.clear()
            for char in self.username:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))
            
            logger.info("⌨️ 输入密码...")
            password_field.clear()
            for char in self.password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            # 点击登录按钮
            logger.info("🖱️ 点击登录按钮...")
            login_button.click()
            
            # 等待登录完成
            logger.info("⏳ 等待登录完成...")
            time.sleep(10)

            # 处理登录后的Cloudflare验证（如果需要）
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 检查登录是否成功
            login_success = self.enhanced_strict_check_login_status()
            if login_success:
                logger.success("✅ 登录成功")
                # 保存cookies
                self.save_cookies_to_cache()
                return True
            else:
                logger.error("❌ 登录失败")
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            # 保存错误页面
            try:
                page_source = self.driver.page_source
                error_file = f"login_error_{self.site_name}_{int(time.time())}.html"
                with open(error_file, "w", encoding="utf-8") as f:
                    f.write(page_source)
                logger.info(f"📄 错误页面已保存到: {error_file}")
            except:
                pass
            return False

    def ensure_logged_in(self):
        """确保用户已登录 - 强制每次登录"""
        logger.info("🎯 开始登录流程")
        return self.attempt_login()

    @retry_decorator()
    def click_one_topic(self, topic_url):
        """浏览单个主题 - 真实用户行为模拟"""
        original_window = self.driver.current_window_handle
        
        # 在新标签页中打开主题
        self.driver.execute_script(f"window.open('{topic_url}', '_blank');")
        # 切换到新标签页
        for handle in self.driver.window_handles:
            if handle != original_window:
                self.driver.switch_to.window(handle)
                break
        
        try:
            time.sleep(3)
            
            # 随机决定是否点赞 (0.5%概率) - 增加真实性
            if random.random() < 0.005:
                self.click_like()

            # 浏览帖子内容 - 真实用户滚动行为
            self.browse_post()
            
            # 关闭当前标签页
            self.driver.close()
            # 切换回原标签页
            self.driver.switch_to.window(original_window)
            return True
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            # 确保切换回原标签页
            try:
                self.driver.close()
                self.driver.switch_to.window(original_window)
            except:
                pass
            return False

    def click_like(self):
        """点赞帖子 - 真实用户行为"""
        try:
            like_button = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".discourse-reactions-reaction-button")))
            if like_button.is_displayed():
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                logger.info("点赞成功")
                time.sleep(random.uniform(1, 2))
            else:
                logger.info("帖子可能已经点过赞了")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    def browse_post(self):
        """浏览帖子内容 - 真实用户滚动行为模拟"""
        # 开始自动滚动，最多滚动8次
        for i in range(8):
            # 随机滚动一段距离 - 模拟真实用户
            scroll_distance = random.randint(400, 800)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
            
            # 随机决定是否提前退出 - 真实用户行为
            if random.random() < 0.03:
                break
                
            # 检查是否到达页面底部
            at_bottom = self.driver.execute_script(
                "return window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            if at_bottom:
                break
                
            # 动态随机等待 - 模拟真实阅读时间
            wait_time = random.uniform(2, 4)
            time.sleep(wait_time)

    def click_topic(self):
        """点击浏览主题 - 真实用户浏览流程"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题 - 模拟真实用户行为")

        # 确保在latest页面
        if not self.driver.current_url.endswith('/latest'):
            self.driver.get(self.site_config['latest_url'])
            time.sleep(5)

        try:
            # 获取主题列表
            topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
            if not topic_elements:
                logger.error("❌ 没有找到主题列表")
                return 0

            # 随机选择5-8个主题 - 模拟真实用户随机浏览
            browse_count = min(random.randint(5, 8), len(topic_elements))
            selected_topics = random.sample(topic_elements, browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题帖，随机选择 {browse_count} 个进行浏览")

            for i, topic in enumerate(selected_topics):
                topic_url = topic.get_attribute("href")
                if not topic_url:
                    continue

                if not topic_url.startswith('http'):
                    topic_url = self.site_config['base_url'] + topic_url

                logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")

                if self.click_one_topic(topic_url):
                    success_count += 1

                # 随机等待 - 模拟真实用户思考时间
                if i < browse_count - 1:
                    wait_time = random.uniform(5, 12)
                    time.sleep(wait_time)

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            return 0

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息")
        try:
            self.driver.get(self.site_config['connect_url'])
            time.sleep(5)

            # 处理可能的Cloudflare验证
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            
            # 等待更长时间确保页面加载
            time.sleep(8)
            
            # 更全面的表格选择器
            table_selectors = [
                "table",
                ".table",
                "table.table",
                ".topic-list",
                ".container table",
                ".wrap table",
                "div.table-container table",
                "[class*='connection'] table",
                "[class*='connect'] table"
            ]

            table_element = None
            for selector in table_selectors:
                try:
                    table_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if table_element and table_element.is_displayed():
                        logger.info(f"✅ 找到表格: {selector}")
                        break
                    table_element = None
                except:
                    continue

            if not table_element:
                logger.warning("⚠️ 无法找到连接信息表格，尝试保存页面源码")
                # 保存页面源码用于调试
                page_source = self.driver.page_source
                debug_file = f"connect_debug_{self.site_name}_{int(time.time())}.html"
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(page_source)
                logger.info(f"📄 保存页面源码到: {debug_file}")
                return

            # 获取表格数据
            rows = table_element.find_elements(By.TAG_NAME, "tr")
            info = []
            
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip() 
                    requirement = cells[2].text.strip()
                    if project and current:  # 确保不是空行
                        info.append([project, current, requirement])

            if info:
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
                # 简单表格显示，不依赖外部库
                print(f"{'项目':<20} {'当前':<15} {'要求':<15}")
                print("-" * 50)
                for item in info:
                    print(f"{item[0]:<20} {item[1]:<15} {item[2]:<15}")
                print("="*60 + "\n")
            else:
                logger.warning("⚠️ 表格中没有找到有效数据")

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def run(self):
        """执行完整的自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 1. 强制登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                self.generate_browser_state(False, 0)
                return False

            # 2. 浏览主题并获取成功数量
            browse_success_count = self.click_topic()

            # 3. 打印连接信息
            self.print_connect_info()

            # 4. 生成浏览器状态文件
            self.generate_browser_state(True, browse_success_count)

            logger.success(f"✅ {self.site_name} 处理完成")
            return True
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            self.generate_browser_state(False, 0)
            return False
        finally:
            # 关闭浏览器
            try:
                self.driver.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (Selenium版) - 真实用户行为模拟")
    
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

        # 站点间随机等待 - 模拟真实用户切换站点行为
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
