import os
import random
import time
import sys
import json
import pickle
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from loguru import logger
import hashlib

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

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do',
        'user_url': 'https://linux.do/u'
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u'
    }
]

# 配置项
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = False
MAX_CACHE_AGE_HOURS = int(os.environ.get("MAX_CACHE_AGE_HOURS", "168"))  # 7天默认

# DoH 服务器配置
DOH_SERVER = os.environ.get("DOH_SERVER", "https://ld.ddd.oaifree.com/query-dns")

# turnstilePatch 扩展路径
TURNSTILE_PATCH_PATH = os.path.abspath("turnstilePatch")

# ======================== 增强缓存管理器 ========================
class EnhancedCacheManager:
    """增强的缓存管理类 - 管理所有类型的缓存"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    @staticmethod
    def get_sessions_directory():
        """获取会话目录"""
        sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        return sessions_dir

    @staticmethod
    def get_cloudflare_directory():
        """获取Cloudflare状态目录"""
        cf_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflare")
        os.makedirs(cf_dir, exist_ok=True)
        return cf_dir

    @staticmethod
    def get_browser_states_directory():
        """获取浏览器状态目录"""
        states_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_states")
        os.makedirs(states_dir, exist_ok=True)
        return states_dir

    @staticmethod
    def get_cache_file_path(file_name, subdirectory=""):
        """获取缓存文件的完整路径"""
        if subdirectory:
            if subdirectory == "cloudflare":
                base_dir = EnhancedCacheManager.get_cloudflare_directory()
            elif subdirectory == "browser_states":
                base_dir = EnhancedCacheManager.get_browser_states_directory()
            elif subdirectory == "sessions":
                base_dir = EnhancedCacheManager.get_sessions_directory()
            else:
                base_dir = os.path.join(EnhancedCacheManager.get_cache_directory(), subdirectory)
            os.makedirs(base_dir, exist_ok=True)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, file_name)

    @staticmethod
    def generate_session_id(site_name, username):
        """生成会话ID"""
        unique_string = f"{site_name}_{username}_{os.getenv('GITHUB_SHA', 'local')}"
        return hashlib.md5(unique_string.encode()).hexdigest()[:16]

    @staticmethod
    def load_cache(file_name, subdirectory=""):
        """从文件加载缓存数据"""
        file_path = EnhancedCacheManager.get_cache_file_path(file_name, subdirectory)
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
    def save_cache(data, file_name, subdirectory=""):
        """保存数据到缓存文件"""
        try:
            file_path = EnhancedCacheManager.get_cache_file_path(file_name, subdirectory)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def is_cache_valid(file_name, expiry_hours=MAX_CACHE_AGE_HOURS, subdirectory=""):
        """检查缓存是否有效"""
        file_path = EnhancedCacheManager.get_cache_file_path(file_name, subdirectory)
        if not os.path.exists(file_path):
            return False
        
        try:
            file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            time_diff = datetime.now() - file_modified_time
            is_valid = time_diff.total_seconds() < expiry_hours * 3600
            
            if is_valid:
                logger.info(f"✅ 缓存有效: {file_name} (未超过{expiry_hours}小时)")
            else:
                logger.warning(f"⚠️ 缓存过期: {file_name} (已存在{time_diff.total_seconds()/3600:.1f}小时)")
            
            return is_valid
        except Exception as e:
            logger.error(f"缓存验证失败: {str(e)}")
            return False

    @staticmethod
    def save_comprehensive_session(driver, site_name, username, additional_data=None):
        """保存综合会话数据"""
        try:
            session_id = EnhancedCacheManager.generate_session_id(site_name, username)
            session_data = {
                'session_id': session_id,
                'site_name': site_name,
                'username': username,
                'timestamp': datetime.now().isoformat(),
                'cookies': driver.get_cookies(),
                'local_storage': driver.execute_script("return Object.assign({}, window.localStorage);"),
                'session_storage': driver.execute_script("return Object.assign({}, window.sessionStorage);"),
                'user_agent': driver.execute_script("return navigator.userAgent;"),
                'additional_data': additional_data or {}
            }
            
            file_name = f"session_{site_name}_{session_id}.json"
            return EnhancedCacheManager.save_cache(session_data, file_name, "sessions")
        except Exception as e:
            logger.error(f"综合会话保存失败: {str(e)}")
            return False

    @staticmethod
    def load_comprehensive_session(driver, site_name, username):
        """加载综合会话数据"""
        try:
            session_id = EnhancedCacheManager.generate_session_id(site_name, username)
            file_name = f"session_{site_name}_{session_id}.json"
            
            if not EnhancedCacheManager.is_cache_valid(file_name, MAX_CACHE_AGE_HOURS, "sessions"):
                return False
            
            session_data = EnhancedCacheManager.load_cache(file_name, "sessions")
            if not session_data:
                return False
            
            # 恢复cookies
            driver.get(session_data.get('base_url', 'https://linux.do'))
            time.sleep(2)
            
            for cookie in session_data.get('cookies', []):
                try:
                    driver.add_cookie(cookie)
                except Exception as e:
                    logger.debug(f"Cookie恢复失败: {str(e)}")
            
            # 恢复localStorage
            if session_data.get('local_storage'):
                driver.execute_script("""
                    var storage = arguments[0];
                    for (var key in storage) {
                        if (storage.hasOwnProperty(key)) {
                            localStorage.setItem(key, storage[key]);
                        }
                    }
                """, session_data['local_storage'])
            
            # 恢复sessionStorage
            if session_data.get('session_storage'):
                driver.execute_script("""
                    var storage = arguments[0];
                    for (var key in storage) {
                        if (storage.hasOwnProperty(key)) {
                            sessionStorage.setItem(key, storage[key]);
                        }
                    }
                """, session_data['session_storage'])
            
            logger.success(f"✅ 综合会话已恢复: {session_id}")
            return True
        except Exception as e:
            logger.error(f"综合会话恢复失败: {str(e)}")
            return False

# ======================== 优化的Cloudflare处理器 ========================
class OptimizedCloudflareHandler:
    @staticmethod
    def quick_check_cloudflare(driver, timeout=30):
        """快速检查Cloudflare状态 - 优化版本"""
        start_time = time.time()
        logger.info("🔍 快速检查Cloudflare状态...")
        
        for attempt in range(3):  # 只尝试3次
            try:
                current_url = driver.current_url
                page_title = driver.title.lower() if driver.title else ""
                page_source = driver.page_source.lower() if driver.page_source else ""
                
                # 检查验证状态
                cloudflare_indicators = ["just a moment", "checking", "please wait", "ddos protection", "cloudflare", "verifying"]
                is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                
                # 检查是否重定向到挑战页面
                is_challenge_page = "challenge" in current_url or "challenges" in current_url
                
                if not is_cloudflare_page and not is_challenge_page:
                    logger.success("✅ Cloudflare验证快速通过")
                    return True
                
                # 如果检测到Cloudflare页面，等待较短时间
                wait_time = min(5 + (attempt * 3), 10)
                elapsed = time.time() - start_time
                
                if elapsed > timeout:
                    logger.warning(f"⚠️ Cloudflare检查超时 ({timeout}秒)")
                    break
                    
                logger.info(f"⏳ 等待验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/3")
                time.sleep(wait_time)
                
                # 刷新页面
                if attempt == 1:
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面")
                        time.sleep(3)
                    except:
                        pass
                        
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(3)

        logger.warning("⚠️ Cloudflare验证可能需要更多时间")
        return False

    @staticmethod
    def handle_cloudflare_efficient(driver, max_attempts=8, timeout=120):
        """高效的Cloudflare验证处理"""
        start_time = time.time()
        logger.info("🛡️ 开始高效Cloudflare验证处理")
        
        # 首先尝试快速检查
        if OptimizedCloudflareHandler.quick_check_cloudflare(driver, 20):
            return True

        # 完整验证流程
        for attempt in range(max_attempts):
            try:
                current_url = driver.current_url
                page_title = driver.title.lower() if driver.title else ""
                page_source = driver.page_source.lower() if driver.page_source else ""
                
                # 检查验证状态
                cloudflare_indicators = ["just a moment", "checking", "please wait", "ddos protection", "cloudflare", "verifying"]
                is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                
                # 检查是否重定向到挑战页面
                is_challenge_page = "challenge" in current_url or "challenges" in current_url
                
                if not is_cloudflare_page and not is_challenge_page:
                    # 额外检查
                    time.sleep(2)
                    current_url = driver.current_url
                    page_title = driver.title.lower() if driver.title else ""
                    is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators)
                    
                    if not is_cloudflare_page:
                        logger.success("✅ Cloudflare验证通过")
                        return True

                # 动态调整等待时间 - 更短的等待
                base_wait = 3 + (attempt * 2)
                wait_time = min(base_wait, 12)
                elapsed = time.time() - start_time
                
                logger.info(f"⏳ 等待验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts} [耗时: {elapsed:.0f}秒]")
                time.sleep(wait_time)
                
                # 超时检查
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
                # 定期刷新
                if attempt % 3 == 2:
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面")
                        time.sleep(3)
                    except:
                        pass
                        
            except Exception as e:
                logger.debug(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(3)

        logger.warning("⚠️ Cloudflare验证可能未完全通过，继续流程")
        return False

# ======================== 优化的浏览器类 ========================
class OptimizedLinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.driver = None
        self.wait = None
        self.session_id = EnhancedCacheManager.generate_session_id(self.site_name, self.username)
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器 - 优化版本"""
        chrome_options = Options()
        
        # 配置Headless模式
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
        
        # 反检测核心配置
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--lang=zh-CN,zh;q=0.9,en;q=0.8')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        
        # 固定使用Windows用户代理
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # 排除自动化特征
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # 添加实验选项增强隐蔽性
        chrome_options.add_experimental_option("prefs", {
            "profile.default_content_setting_values": {
                "images": 1,
                "cookies": 1,
                "notifications": 2,
                "geolocation": 2,
            },
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False
        })
        
        # 加载turnstilePatch扩展
        if os.path.exists(TURNSTILE_PATCH_PATH):
            chrome_options.add_argument(f'--load-extension={TURNSTILE_PATCH_PATH}')
            logger.info(f"✅ 已加载turnstilePatch扩展")
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            
            # 执行反检测脚本
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
        except Exception as e:
            logger.error(f"Chrome驱动初始化失败: {str(e)}")
            raise
            
        self.wait = WebDriverWait(self.driver, 20)  # 减少等待时间

    def save_comprehensive_state(self, success=True, activity_count=0, additional_info=None):
        """保存综合状态信息"""
        try:
            browser_state = {
                'site': self.site_name,
                'username': self.username,
                'session_id': self.session_id,
                'last_updated': datetime.now().isoformat(),
                'status': 'completed' if success else 'failed',
                'activity_count': activity_count,
                'login_success': success,
                'execution_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'additional_info': additional_info or {},
                'version': '2.1'
            }
            
            EnhancedCacheManager.save_cache(browser_state, f"browser_state_{self.site_name}.json")
            
            # 保存会话数据
            EnhancedCacheManager.save_comprehensive_session(
                self.driver, 
                self.site_name, 
                self.username,
                {
                    'browser_state': browser_state,
                    'last_activity': activity_count
                }
            )
            
            logger.success(f"✅ 综合状态已保存")
            return True
        except Exception as e:
            logger.error(f"状态保存失败: {str(e)}")
            return False

    def load_comprehensive_state(self):
        """加载综合状态信息"""
        if EnhancedCacheManager.load_comprehensive_session(self.driver, self.site_name, self.username):
            logger.success("✅ 综合会话状态已恢复")
            return True
        return False

    def save_cookies_to_cache(self):
        """将当前Cookies保存到缓存"""
        try:
            cookies = self.driver.get_cookies()
            cookie_data = {
                'cookies': cookies,
                'timestamp': datetime.now().isoformat(),
                'username': self.username,
                'session_id': self.session_id,
                'site': self.site_name
            }
            EnhancedCacheManager.save_cache(cookie_data, f"cf_cookies_{self.site_name}.json")
            logger.success(f"✅ Cookies已缓存")
            return True
        except Exception as e:
            logger.error(f"Cookies缓存失败: {str(e)}")
            return False

    def load_cookies_from_cache(self):
        """从缓存加载Cookies"""
        cache_file = f"cf_cookies_{self.site_name}.json"
        
        if not EnhancedCacheManager.is_cache_valid(cache_file, MAX_CACHE_AGE_HOURS):
            return False
        
        try:
            cookie_data = EnhancedCacheManager.load_cache(cache_file)
            if not cookie_data or 'cookies' not in cookie_data:
                return False
            
            # 加载Cookies到浏览器
            self.driver.get(self.site_config['base_url'])
            time.sleep(2)
            
            for cookie in cookie_data['cookies']:
                try:
                    self.driver.add_cookie(cookie)
                except Exception as e:
                    continue
            
            logger.success(f"✅ Cookies已从缓存加载")
            return True
        except Exception as e:
            logger.error(f"Cookies加载失败: {str(e)}")
            return False

    def quick_username_check(self):
        """快速用户名检查"""
        logger.info("🔍 快速验证登录状态...")
        
        try:
            # 只检查用户主页
            user_url = f"{self.site_config['user_url']}/{self.username}"
            self.driver.get(user_url)
            time.sleep(3)
            
            # 快速检查Cloudflare
            OptimizedCloudflareHandler.quick_check_cloudflare(self.driver, 15)
            time.sleep(2)
            
            page_content = self.driver.page_source
            current_url = self.driver.current_url
            
            if self.username.lower() in page_content.lower():
                logger.success(f"✅ 找到用户名: {self.username}")
                return True
            else:
                logger.warning("❌ 未找到用户名")
                return False
                
        except Exception as e:
            logger.error(f"快速验证异常: {str(e)}")
            return False

    def ensure_logged_in(self):
        """确保用户已登录 - 优化版本"""
        # 第一步：尝试使用综合状态恢复
        if not FORCE_LOGIN_EVERY_TIME:
            if self.load_comprehensive_state():
                if self.quick_username_check():
                    logger.success("✅ 综合状态恢复成功")
                    return True

        # 第二步：尝试Cookies缓存
        if not FORCE_LOGIN_EVERY_TIME:
            if self.load_cookies_from_cache():
                if self.quick_username_check():
                    logger.success("✅ Cookies缓存登录成功")
                    self.save_comprehensive_state(True, 0)
                    return True

        # 第三步：手动登录
        logger.info("🔐 执行手动登录流程...")
        login_success = self.attempt_login()
        
        if login_success:
            self.save_cookies_to_cache()
            self.save_comprehensive_state(True, 0)
        
        return login_success

    def attempt_login(self):
        """尝试登录 - 优化版本"""
        logger.info("🔐 开始登录流程...")
        
        try:
            self.driver.get(self.site_config['login_url'])
            time.sleep(3)

            # 使用高效的Cloudflare处理
            OptimizedCloudflareHandler.handle_cloudflare_efficient(self.driver)
            time.sleep(2)

            # 查找表单元素
            username_field = None
            password_field = None
            login_button = None

            # 简化选择器
            username_selectors = ["#login-account-name", "#username", "input[name='username']"]
            password_selectors = ["#login-account-password", "#password", "input[name='password']"]
            login_button_selectors = ["#login-button", "button[type='submit']"]

            for selector in username_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        username_field = element
                        break
                except:
                    continue

            for selector in password_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        password_field = element
                        break
                except:
                    continue

            for selector in login_button_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        login_button = element
                        break
                except:
                    continue

            if not all([username_field, password_field, login_button]):
                logger.error("❌ 找不到登录表单元素")
                return False

            # 快速输入
            logger.info("⌨️ 输入凭据...")
            username_field.clear()
            username_field.send_keys(self.username)
            time.sleep(1)
            
            password_field.clear()
            password_field.send_keys(self.password)
            time.sleep(1)

            logger.info("🖱️ 点击登录按钮...")
            login_button.click()
            
            logger.info("⏳ 等待登录完成...")
            time.sleep(5)

            # 快速检查登录状态
            login_success = self.quick_username_check()
            if login_success:
                logger.success("✅ 登录成功")
                return True
            else:
                logger.error("❌ 登录失败")
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def simulate_quick_reading(self, stay_time=20):
        """快速模拟阅读行为"""
        logger.info(f"📖 快速阅读 {stay_time:.1f} 秒...")
        start_time = time.time()
        
        scrolls_done = 0
        while time.time() - start_time < stay_time:
            try:
                scroll_distance = random.randint(200, 600)
                self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                scrolls_done += 1
                time.sleep(random.uniform(1, 3))
            except:
                break
        
        logger.debug(f"📊 快速阅读完成: {scrolls_done} 次滚动")

    def click_topic_optimized(self):
        """优化浏览主题"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题...")
        
        try:
            self.driver.get(self.site_config['latest_url'])
            time.sleep(3)
            
            # 快速Cloudflare检查
            OptimizedCloudflareHandler.quick_check_cloudflare(self.driver)
            time.sleep(2)

            # 查找主题元素
            topic_elements = []
            for selector in [".title", "a.title"]:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        topic_elements = [elem for elem in elements if elem.get_attribute('href') and '/t/' in elem.get_attribute('href')]
                        if topic_elements:
                            break
                except:
                    continue

            if not topic_elements:
                logger.error("❌ 没有找到主题列表")
                return 0

            # 减少浏览数量
            browse_count = min(5, len(topic_elements))  # 只浏览5个主题
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题，快速浏览 {browse_count} 个")

            for i, idx in enumerate(selected_indices):
                try:
                    # 重新获取当前主题列表
                    current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                    if not current_topic_elements or idx >= len(current_topic_elements):
                        continue

                    topic = current_topic_elements[idx]
                    topic_url = topic.get_attribute("href")
                    if not topic_url:
                        continue
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url

                    logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")
                    
                    self.driver.get(topic_url)
                    time.sleep(3)
                    
                    # 快速阅读
                    page_stay_time = random.uniform(15, 25)
                    self.simulate_quick_reading(page_stay_time)
                    
                    self.driver.back()
                    time.sleep(3)
                    
                    success_count += 1
                    
                    # 简化的间隔等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(5, 10)
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.debug(f"浏览主题失败: {str(e)}")
                    try:
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(2)
                    except:
                        pass
                    continue

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            return 0

    def print_connect_info_fast(self):
        """快速获取连接信息"""
        logger.info("🔗 快速获取连接信息")
        
        try:
            self.driver.get(self.site_config['connect_url'])
            time.sleep(4)
            
            # 快速Cloudflare检查
            OptimizedCloudflareHandler.quick_check_cloudflare(self.driver, 15)
            time.sleep(2)
            
            try:
                table = self.driver.find_element(By.TAG_NAME, 'table')
                rows = table.find_elements(By.TAG_NAME, 'tr')
                info = []
                
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, 'td')
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        info.append([project, current, requirement])
                
                if info:
                    print("\n" + "="*50)
                    print(f"📊 {self.site_name.upper()} 连接信息")
                    print("="*50)
                    
                    try:
                        from tabulate import tabulate
                        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="simple"))
                    except ImportError:
                        for item in info:
                            print(f"{item[0]:<15} {item[1]:<15} {item[2]:<15}")
                    
                    print("="*50 + "\n")
                    logger.success(f"✅ 成功获取 {len(info)} 项连接信息")
                else:
                    logger.warning("⚠️ 未找到连接信息")
                    
            except Exception as e:
                logger.warning(f"解析表格失败: {str(e)}")
                
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def perform_quick_activities(self):
        """执行快速活跃行为"""
        logger.info("🎯 执行快速活跃行为...")
        
        activities_performed = 0
        
        try:
            # 只访问一个额外页面
            additional_pages = ["/categories"]
            
            for page in additional_pages:
                try:
                    url = self.site_config['base_url'] + page
                    self.driver.get(url)
                    time.sleep(5)
                    self.simulate_quick_reading(10)
                    activities_performed += 1
                    break
                except:
                    pass
            
            logger.success(f"✅ 完成 {activities_performed} 项快速活跃行为")
            return activities_performed
            
        except Exception as e:
            logger.error(f"执行活跃行为失败: {str(e)}")
            return activities_performed

    def run_optimized(self):
        """执行优化后的完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 1. 快速登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                self.save_comprehensive_state(False, 0, {'error': '登录失败'})
                return False

            # 2. 快速活跃行为
            additional_activities = self.perform_quick_activities()

            # 3. 优化浏览主题
            browse_success_count = self.click_topic_optimized()
            if browse_success_count == 0:
                logger.error("❌ 浏览主题失败")
                return False

            # 4. 快速获取连接信息
            self.print_connect_info_fast()

            # 5. 保存最终状态
            total_activities = browse_success_count + additional_activities
            self.save_comprehensive_state(True, total_activities, {
                'browse_count': browse_success_count,
                'additional_activities': additional_activities,
                'total_activities': total_activities
            })

            logger.success(f"✅ {self.site_name} 处理完成 - 总计 {total_activities} 项活动")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            self.save_comprehensive_state(False, 0, {'error': str(e)})
            return False
            
        finally:
            try:
                if self.driver:
                    self.driver.quit()
            except:
                pass

# ======================== 优化主函数 ========================
def main_optimized():
    """优化主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (优化快速版)")
    
    # 配置日志
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")
    logger.add("automation_quick.log", rotation="5 MB", retention=2)
    
    os.environ.pop("DISPLAY", None)
    success_sites = []
    failed_sites = []

    # 处理站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    logger.info(f"🔍 站点选择: {site_selector}")

    # 筛选需要处理的站点
    target_sites = []
    if site_selector == "all":
        target_sites = SITES
    else:
        for site in SITES:
            if site['name'] == site_selector:
                target_sites.append(site)
                break

    # 显示缓存状态
    logger.info("📊 缓存状态检查:")
    for site in target_sites:
        site_name = site['name']
        cache_files = [f"cf_cookies_{site_name}.json", f"browser_state_{site_name}.json"]
        
        for cache_file in cache_files:
            if EnhancedCacheManager.is_cache_valid(cache_file, MAX_CACHE_AGE_HOURS):
                logger.info(f"  ✅ {cache_file} - 有效")

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        if not credentials.get('username') or not credentials.get('password'):
            logger.warning(f"⏭️ 跳过 {site_name} - 未配置凭证")
            continue

        logger.info(f"🔧 初始化 {site_name} 浏览器")
        try:
            browser = OptimizedLinuxDoBrowser(site_config, credentials)
            success = browser.run_optimized()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间短暂等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(10, 15)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个站点...")
            time.sleep(wait_time)

    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功站点: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败站点: {', '.join(failed_sites) if failed_sites else '无'}")

    if success_sites:
        logger.success("🎉 任务完成")
        sys.exit(0)
    else:
        logger.error("💥 所有任务失败")
        sys.exit(1)

if __name__ == "__main__":
    main_optimized()
