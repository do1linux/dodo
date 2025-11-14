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
MAX_CACHE_AGE_HOURS = int(os.environ.get("MAX_CACHE_AGE_HOURS", "168"))

# turnstilePatch 扩展路径
TURNSTILE_PATCH_PATH = os.path.abspath("turnstilePatch")

# ======================== 极速缓存管理器 ========================
class FastCacheManager:
    """极速缓存管理类"""
    
    @staticmethod
    def get_cache_directory():
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    @staticmethod
    def get_sessions_directory():
        sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        return sessions_dir

    @staticmethod
    def get_cache_file_path(file_name, subdirectory=""):
        if subdirectory:
            base_dir = os.path.join(FastCacheManager.get_cache_directory(), subdirectory)
            os.makedirs(base_dir, exist_ok=True)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, file_name)

    @staticmethod
    def generate_session_id(site_name, username):
        unique_string = f"{site_name}_{username}_{os.getenv('GITHUB_SHA', 'local')}"
        return hashlib.md5(unique_string.encode()).hexdigest()[:12]

    @staticmethod
    def load_cache(file_name, subdirectory=""):
        file_path = FastCacheManager.get_cache_file_path(file_name, subdirectory)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
                return data
            except:
                pass
        return None

    @staticmethod
    def save_cache(data, file_name, subdirectory=""):
        try:
            file_path = FastCacheManager.get_cache_file_path(file_name, subdirectory)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except:
            return False

    @staticmethod
    def is_cache_valid(file_name, expiry_hours=MAX_CACHE_AGE_HOURS, subdirectory=""):
        file_path = FastCacheManager.get_cache_file_path(file_name, subdirectory)
        if not os.path.exists(file_path):
            return False
        
        try:
            file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            time_diff = datetime.now() - file_modified_time
            return time_diff.total_seconds() < expiry_hours * 3600
        except:
            return False

    @staticmethod
    def save_comprehensive_session(driver, site_name, username, additional_data=None):
        try:
            session_id = FastCacheManager.generate_session_id(site_name, username)
            session_data = {
                'session_id': session_id,
                'site_name': site_name,
                'username': username,
                'timestamp': datetime.now().isoformat(),
                'cookies': driver.get_cookies(),
                'additional_data': additional_data or {}
            }
            
            file_name = f"session_{site_name}_{session_id}.json"
            return FastCacheManager.save_cache(session_data, file_name, "sessions")
        except:
            return False

    @staticmethod
    def load_comprehensive_session(driver, site_name, username):
        try:
            session_id = FastCacheManager.generate_session_id(site_name, username)
            file_name = f"session_{site_name}_{session_id}.json"
            
            if not FastCacheManager.is_cache_valid(file_name, MAX_CACHE_AGE_HOURS, "sessions"):
                return False
            
            session_data = FastCacheManager.load_cache(file_name, "sessions")
            if not session_data:
                return False
            
            # 恢复cookies
            driver.get(session_data.get('base_url', 'https://linux.do'))
            time.sleep(1)
            
            for cookie in session_data.get('cookies', []):
                try:
                    driver.add_cookie(cookie)
                except:
                    continue
            
            logger.info(f"✅ 会话已恢复")
            return True
        except:
            return False

# ======================== 极速Cloudflare处理器 ========================
class FastCloudflareHandler:
    @staticmethod
    def quick_bypass_check(driver, timeout=8):
        """极速绕过Cloudflare检查"""
        start_time = time.time()
        
        for attempt in range(2):  # 只尝试2次
            try:
                current_url = driver.current_url
                page_source = driver.page_source.lower() if driver.page_source else ""
                
                # 检查是否是Cloudflare页面
                cloudflare_indicators = ["just a moment", "checking", "please wait", "ddos protection"]
                is_cloudflare_page = any(indicator in page_source for indicator in cloudflare_indicators)
                
                if not is_cloudflare_page:
                    return True
                
                # 如果是Cloudflare页面，等待很短时间
                wait_time = 2
                if time.time() - start_time > timeout:
                    break
                    
                time.sleep(wait_time)
                
                # 第一次尝试后刷新
                if attempt == 0:
                    try:
                        driver.refresh()
                        time.sleep(1)
                    except:
                        pass
                        
            except:
                time.sleep(1)

        # 无论如何都继续，不阻塞流程
        logger.info("⏩ 跳过Cloudflare等待，继续流程")
        return True

# ======================== 极速浏览器类 ========================
class FastLinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.driver = None
        self.session_id = FastCacheManager.generate_session_id(self.site_name, self.username)
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器 - 极速版本"""
        chrome_options = Options()
        
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
        
        # 最小化反检测配置
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # 加载扩展
        if os.path.exists(TURNSTILE_PATCH_PATH):
            chrome_options.add_argument(f'--load-extension={TURNSTILE_PATCH_PATH}')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception as e:
            logger.error(f"浏览器初始化失败: {str(e)}")
            raise

    def save_state(self, success=True, activity_count=0):
        """保存状态"""
        try:
            browser_state = {
                'site': self.site_name,
                'username': self.username,
                'last_updated': datetime.now().isoformat(),
                'status': 'completed' if success else 'failed',
                'activity_count': activity_count
            }
            
            FastCacheManager.save_cache(browser_state, f"browser_state_{self.site_name}.json")
            FastCacheManager.save_comprehensive_session(
                self.driver, 
                self.site_name, 
                self.username,
                {'browser_state': browser_state}
            )
            
            # 单独保存cookies
            cookies_data = {
                'cookies': self.driver.get_cookies(),
                'timestamp': datetime.now().isoformat()
            }
            FastCacheManager.save_cache(cookies_data, f"cf_cookies_{self.site_name}.json")
            
            return True
        except:
            return False

    def load_state(self):
        """加载状态"""
        if FastCacheManager.load_comprehensive_session(self.driver, self.site_name, self.username):
            logger.info("✅ 状态恢复成功")
            return True
        return False

    def quick_login_check(self):
        """快速登录检查"""
        try:
            # 直接检查用户主页
            user_url = f"{self.site_config['user_url']}/{self.username}"
            self.driver.get(user_url)
            time.sleep(2)
            
            FastCloudflareHandler.quick_bypass_check(self.driver, 5)
            time.sleep(1)
            
            page_content = self.driver.page_source
            return self.username.lower() in page_content.lower()
                
        except:
            return False

    def ensure_logged_in_fast(self):
        """确保登录 - 极速版本"""
        # 尝试恢复状态
        if not FORCE_LOGIN_EVERY_TIME and self.load_state():
            if self.quick_login_check():
                logger.info("✅ 缓存登录成功")
                return True

        # 手动登录
        logger.info("🔐 执行快速登录...")
        return self.fast_login()

    def fast_login(self):
        """快速登录"""
        try:
            self.driver.get(self.site_config['login_url'])
            time.sleep(2)

            # 快速绕过Cloudflare
            FastCloudflareHandler.quick_bypass_check(self.driver, 5)
            time.sleep(1)

            # 快速查找表单
            username_field = self.driver.find_element(By.CSS_SELECTOR, "#login-account-name")
            password_field = self.driver.find_element(By.CSS_SELECTOR, "#login-account-password")
            login_button = self.driver.find_element(By.CSS_SELECTOR, "#login-button")

            # 快速输入
            username_field.clear()
            username_field.send_keys(self.username)
            time.sleep(0.5)
            
            password_field.clear()
            password_field.send_keys(self.password)
            time.sleep(0.5)

            login_button.click()
            time.sleep(3)

            # 快速检查登录状态
            if self.quick_login_check():
                logger.info("✅ 登录成功")
                self.save_state(True, 0)
                return True
            else:
                logger.error("❌ 登录失败")
                return False

        except Exception as e:
            logger.error(f"登录失败: {str(e)}")
            return False

    def quick_browse_topics(self):
        """快速浏览主题"""
        if not BROWSE_ENABLED:
            return 3  # 返回模拟的成功计数

        try:
            self.driver.get(self.site_config['latest_url'])
            time.sleep(2)
            FastCloudflareHandler.quick_bypass_check(self.driver, 3)

            # 查找主题
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
                return 0

            # 只浏览5个主题，每个主题快速访问
            browse_count = min(5, len(topic_elements))
            success_count = 0

            for i in range(browse_count):
                try:
                    # 重新获取元素避免过时
                    current_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                    if i >= len(current_elements):
                        break

                    topic = current_elements[i]
                    topic_url = topic.get_attribute("href")
                    if not topic_url:
                        continue
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url

                    # 快速访问主题
                    self.driver.get(topic_url)
                    time.sleep(2)
                    
                    # 快速滚动模拟阅读
                    for _ in range(2):
                        self.driver.execute_script("window.scrollBy(0, 500)")
                        time.sleep(1)
                    
                    self.driver.back()
                    time.sleep(2)
                    
                    success_count += 1
                    
                    # 短暂间隔
                    if i < browse_count - 1:
                        time.sleep(3)
                        
                except:
                    try:
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(2)
                    except:
                        pass
                    continue

            logger.info(f"✅ 浏览完成: {success_count} 个主题")
            return success_count
            
        except:
            return 0

    def get_connect_info_fast(self):
        """快速获取连接信息"""
        try:
            self.driver.get(self.site_config['connect_url'])
            time.sleep(3)
            FastCloudflareHandler.quick_bypass_check(self.driver, 5)
            time.sleep(2)
            
            # 尝试多种表格选择器
            table_selectors = [
                "table",
                ".table",
                "table.stats-table"
            ]
            
            table = None
            for selector in table_selectors:
                try:
                    table = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if table:
                        break
                except:
                    continue
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                return
            
            rows = table.find_elements(By.TAG_NAME, "tr")
            info = []
            
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 3:
                        project = cells[0].text.strip()[:20]  # 限制长度
                        current = cells[1].text.strip()[:15]
                        requirement = cells[2].text.strip()[:15]
                        info.append([project, current, requirement])
                except:
                    continue
            
            if info:
                print(f"\n📊 {self.site_name.upper()} 连接信息:")
                print("-" * 50)
                for item in info[:6]:  # 只显示前6项
                    print(f"{item[0]:<20} {item[1]:<15} {item[2]:<15}")
                print("-" * 50)
            else:
                logger.warning("⚠️ 未解析到连接信息")
                
        except Exception as e:
            logger.debug(f"获取连接信息失败: {str(e)}")

    def run_ultra_fast(self):
        """执行极速自动化流程"""
        try:
            logger.info(f"🚀 开始处理: {self.site_name}")

            # 1. 极速登录
            if not self.ensure_logged_in_fast():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False

            # 2. 快速浏览主题
            browse_count = self.quick_browse_topics()

            # 3. 快速获取连接信息
            self.get_connect_info_fast()

            # 4. 保存状态
            self.save_state(True, browse_count)

            logger.success(f"✅ {self.site_name} 完成 - {browse_count} 个主题")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 异常: {str(e)}")
            return False
            
        finally:
            try:
                if self.driver:
                    self.driver.quit()
            except:
                pass

# ======================== 极速主函数 ========================
def main_ultra_fast():
    """极速主函数"""
    logger.info("⚡ Linux.Do 极速自动化脚本启动")
    
    # 极简日志配置
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")
    
    success_sites = []
    failed_sites = []

    # 站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    target_sites = SITES if site_selector == "all" else [s for s in SITES if s['name'] == site_selector]

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        if not credentials.get('username'):
            logger.warning(f"⏭️ 跳过 {site_name}")
            continue

        logger.info(f"🔧 初始化 {site_name}")
        try:
            browser = FastLinuxDoBrowser(site_config, credentials)
            success = browser.run_ultra_fast()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 异常: {str(e)}")
            failed_sites.append(site_name)

        # 短暂站点间等待
        if site_config != target_sites[-1]:
            time.sleep(8)

    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败: {', '.join(failed_sites) if failed_sites else '无'}")

    if success_sites:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main_ultra_fast()
