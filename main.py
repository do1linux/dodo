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
    def get_cache_file_path(file_name, subdirectory=""):
        """获取缓存文件的完整路径"""
        if subdirectory:
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
    def load_pickle_cache(file_name, subdirectory=""):
        """加载pickle格式缓存"""
        file_path = EnhancedCacheManager.get_cache_file_path(file_name, subdirectory)
        if os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f:
                    data = pickle.load(f)
                logger.info(f"📦 加载pickle缓存: {file_name}")
                return data
            except Exception as e:
                logger.warning(f"Pickle缓存加载失败 {file_name}: {str(e)}")
        return None

    @staticmethod
    def save_pickle_cache(data, file_name, subdirectory=""):
        """保存pickle格式缓存"""
        try:
            file_path = EnhancedCacheManager.get_cache_file_path(file_name, subdirectory)
            with open(file_path, "wb") as f:
                pickle.dump(data, f)
            logger.info(f"💾 Pickle缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"Pickle缓存保存失败 {file_name}: {str(e)}")
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

# ======================== Cloudflare处理器 ========================
class EnhancedCloudflareHandler:
    @staticmethod
    def query_doh(domain, doh_server=DOH_SERVER):
        """通过DoH服务器查询DNS"""
        try:
            query_url = f"{doh_server}?name={domain}&type=A"
            headers = {
                'Accept': 'application/dns-json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
    def handle_cloudflare_with_doh(driver, doh_server=DOH_SERVER, max_attempts=15, timeout=300):
        """增强的Cloudflare验证处理"""
        start_time = time.time()
        logger.info(f"🛡️ 开始处理Cloudflare验证 (DoH: {doh_server})")
        
        # 解析关键域名
        critical_domains = [
            'linux.do',
            'idcflare.com', 
            'challenges.cloudflare.com',
            'cloudflare.com'
        ]
        
        for domain in critical_domains:
            EnhancedCloudflareHandler.query_doh(domain, doh_server)

        # 保存Cloudflare状态
        cf_state = {
            'last_processed': datetime.now().isoformat(),
            'domains_resolved': critical_domains,
            'attempts': 0
        }
        EnhancedCacheManager.save_cache(cf_state, f"cloudflare_state.json", "cloudflare")

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
                    time.sleep(3)
                    page_title = driver.title.lower() if driver.title else ""
                    page_source = driver.page_source.lower() if driver.page_source else ""
                    is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                    
                    if not is_cloudflare_page:
                        logger.success("✅ Cloudflare验证通过")
                        
                        # 保存成功的Cloudflare状态
                        cf_state['success'] = True
                        cf_state['final_attempts'] = attempt + 1
                        cf_state['total_time'] = time.time() - start_time
                        EnhancedCacheManager.save_cache(cf_state, f"cloudflare_state.json", "cloudflare")
                        
                        return True

                # 动态调整等待时间
                base_wait = 5 + (attempt * 2)
                wait_time = min(base_wait, 20)
                elapsed = time.time() - start_time
                
                logger.info(f"⏳ 等待验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts} [耗时: {elapsed:.0f}秒]")
                time.sleep(wait_time)
                
                # 超时检查
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
                # 定期刷新
                if attempt % 4 == 3:
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面")
                        time.sleep(4)
                    except:
                        pass
                        
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(5)

        logger.warning("⚠️ Cloudflare验证可能未完全通过")
        
        # 保存失败的Cloudflare状态
        cf_state['success'] = False
        cf_state['final_attempts'] = max_attempts
        cf_state['total_time'] = time.time() - start_time
        EnhancedCacheManager.save_cache(cf_state, f"cloudflare_state.json", "cloudflare")
        
        return False

# ======================== 增强浏览器类 ========================
class EnhancedLinuxDoBrowser:
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
        """初始化浏览器 - 增强版本"""
        chrome_options = Options()
        
        # 配置Headless模式
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-software-rasterizer')
        
        # 反检测核心配置
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--lang=zh-CN,zh;q=0.9,en;q=0.8')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--disable-features=IsolateOrigins,site-per-process')
        chrome_options.add_argument('--disable-site-isolation-trials')
        
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
                "media_stream": 2
            },
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False
        })
        
        # 加载turnstilePatch扩展
        if os.path.exists(TURNSTILE_PATCH_PATH):
            chrome_options.add_argument(f'--load-extension={TURNSTILE_PATCH_PATH}')
            logger.info(f"✅ 已加载turnstilePatch扩展: {TURNSTILE_PATCH_PATH}")
        else:
            logger.warning(f"⚠️ 未找到turnstilePatch扩展目录: {TURNSTILE_PATCH_PATH}")
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            
            # 执行反检测脚本
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # 伪造其他指纹特征
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    // 增强反检测
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['zh-CN', 'zh', 'en', 'en-US'],
                    });
                    Object.defineProperty(navigator, 'mimeTypes', {
                        get: () => [1, 2],
                    });
                    
                    // 模拟真实浏览器
                    window.chrome = {
                        runtime: {},
                        loadTimes: function() {},
                        csi: function() {},
                        app: {
                            isInstalled: false,
                            InstallState: {
                                DISABLED: 'disabled',
                                INSTALLED: 'installed',
                                NOT_INSTALLED: 'not_installed'
                            },
                            RunningState: {
                                CANNOT_RUN: 'cannot_run',
                                READY_TO_RUN: 'ready_to_run',
                                RUNNING: 'running'
                            }
                        }
                    };
                    
                    // 删除自动化痕迹
                    delete navigator.__proto__.connection;
                    
                    // 覆盖权限API
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                    );
                    
                    console.log('🔧 Enhanced anti-detection scripts loaded');
                '''
            })
            
        except Exception as e:
            logger.error(f"Chrome驱动初始化失败: {str(e)}")
            raise
            
        self.wait = WebDriverWait(self.driver, 30)

    def save_comprehensive_state(self, success=True, activity_count=0, additional_info=None):
        """保存综合状态信息"""
        try:
            # 保存浏览器状态
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
                'version': '2.0'
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
            
            logger.success(f"✅ 综合状态已保存: {self.session_id}")
            return True
        except Exception as e:
            logger.error(f"状态保存失败: {str(e)}")
            return False

    def load_comprehensive_state(self):
        """加载综合状态信息"""
        # 首先尝试加载完整会话
        if EnhancedCacheManager.load_comprehensive_session(self.driver, self.site_name, self.username):
            logger.success("✅ 综合会话状态已恢复")
            return True
        
        # 备用：加载Cookies缓存
        return self.load_cookies_from_cache()

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
            logger.success(f"✅ Cookies已缓存: {self.session_id}")
            return True
        except Exception as e:
            logger.error(f"Cookies缓存失败: {str(e)}")
            return False

    def load_cookies_from_cache(self):
        """从缓存加载Cookies"""
        cache_file = f"cf_cookies_{self.site_name}.json"
        
        if not EnhancedCacheManager.is_cache_valid(cache_file, MAX_CACHE_AGE_HOURS):
            logger.warning("⚠️ Cookies缓存无效或不存在")
            return False
        
        try:
            cookie_data = EnhancedCacheManager.load_cache(cache_file)
            if not cookie_data or 'cookies' not in cookie_data:
                return False
            
            # 加载Cookies到浏览器
            self.driver.get(self.site_config['base_url'])
            time.sleep(3)
            
            for cookie in cookie_data['cookies']:
                try:
                    clean_cookie = {
                        'name': cookie.get('name'),
                        'value': cookie.get('value'),
                        'domain': cookie.get('domain', '.linux.do' if 'linux' in self.site_name else '.idcflare.com'),
                        'path': cookie.get('path', '/'),
                        'secure': cookie.get('secure', True),
                        'httpOnly': cookie.get('httpOnly', False)
                    }
                    if 'expiry' in clean_cookie:
                        del clean_cookie['expiry']
                    if 'expires' in clean_cookie:
                        del clean_cookie['expires']
                    
                    self.driver.add_cookie(clean_cookie)
                except Exception as e:
                    continue
            
            logger.success(f"✅ Cookies已从缓存加载: {len(cookie_data['cookies'])}个")
            return True
        except Exception as e:
            logger.error(f"Cookies加载失败: {str(e)}")
            return False

    def robust_username_check(self, max_retries=3):
        """增强的用户名检查"""
        logger.info("🔍 增强验证登录状态...")
        
        for retry in range(max_retries):
            try:
                check_pages = [
                    (self.site_config['latest_url'], "最新话题页面"),
                    (f"{self.site_config['user_url']}/{self.username}", "用户主页"),
                    (self.site_config['base_url'], "首页")
                ]
                
                username_found = False
                for url, page_name in check_pages:
                    try:
                        logger.info(f"📍 检查 {page_name}: {url}")
                        self.driver.get(url)
                        time.sleep(random.uniform(4, 6))
                        
                        cf_passed = EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)
                        if not cf_passed:
                            logger.warning(f"⚠️ {page_name} Cloudflare验证可能有问题")
                        
                        time.sleep(random.uniform(2, 3))
                        
                        page_content = self.driver.page_source
                        current_url = self.driver.current_url
                        
                        if self.username.lower() in page_content.lower():
                            logger.success(f"✅ 在 {page_name} 中找到用户名: {self.username}")
                            username_found = True
                            break
                        else:
                            logger.warning(f"❌ 在 {page_name} 中未找到用户名")
                            
                    except Exception as e:
                        logger.warning(f"检查 {page_name} 失败: {str(e)}")
                        continue
                
                if username_found:
                    try:
                        logout_indicators = ["logout", "sign out", "退出", "登出"]
                        page_lower = self.driver.page_source.lower()
                        if any(indicator in page_lower for indicator in logout_indicators):
                            logger.success("✅ 找到退出按钮，确认登录状态有效")
                        return True
                    except:
                        pass
                    
                    return True
                
                logger.warning(f"❌ 在所有页面中都未找到用户名 (尝试 {retry + 1}/{max_retries})")
                
                if retry < max_retries - 1:
                    wait_time = random.uniform(8, 12)
                    logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                logger.error(f"登录状态检查异常: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(8)
        
        logger.error(f"❌ 增强验证失败")
        return False

    def ensure_logged_in(self):
        """确保用户已登录 - 增强版本"""
        # 第一步：尝试使用综合状态恢复
        if not FORCE_LOGIN_EVERY_TIME:
            logger.info("🎯 尝试使用综合状态恢复...")
            if self.load_comprehensive_state():
                if self.robust_username_check():
                    logger.success("✅ 综合状态恢复成功")
                    return True
                else:
                    logger.warning("⚠️ 综合状态无效，尝试Cookies恢复")
        
        # 第二步：尝试Cookies缓存
        if not FORCE_LOGIN_EVERY_TIME:
            logger.info("🎯 尝试使用Cookies缓存登录...")
            if self.load_cookies_from_cache():
                if self.robust_username_check():
                    logger.success("✅ Cookies缓存登录成功")
                    # 保存综合状态
                    self.save_comprehensive_state(True, 0)
                    return True
                else:
                    logger.warning("⚠️ Cookies缓存无效，尝试重新登录")
        
        # 第三步：手动登录
        logger.info("🔐 执行手动登录流程...")
        login_success = self.attempt_login()
        
        if login_success:
            # 登录成功后保存所有状态
            self.save_cookies_to_cache()
            self.save_comprehensive_state(True, 0)
        
        return login_success

    def attempt_login(self):
        """尝试登录"""
        logger.info("🔐 开始登录流程...")
        
        try:
            self.driver.get(self.site_config['login_url'])
            time.sleep(random.uniform(4, 6))

            cf_passed = EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)
            if not cf_passed:
                logger.warning("⚠️ Cloudflare验证可能有问题，继续尝试登录")
            time.sleep(random.uniform(3, 5))

            current_url = self.driver.current_url
            page_title = self.driver.title
            logger.info(f"📄 当前页面: {page_title} | {current_url}")

            if 'login' not in current_url and 'signin' not in current_url:
                logger.info("🔄 被重定向，尝试回到登录页面")
                self.driver.get(self.site_config['login_url'])
                time.sleep(random.uniform(4, 6))
                EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 查找表单元素
            username_field = None
            password_field = None
            login_button = None

            username_selectors = [
                "#login-account-name", "#username", "input[name='username']", 
                "input[name='login']", "input[type='text']", "input[placeholder*='name']"
            ]
            password_selectors = [
                "#login-account-password", "#password", "input[name='password']", 
                "input[type='password']", "input[placeholder*='password']"
            ]
            login_button_selectors = [
                "#login-button", "button[type='submit']", "input[type='submit']",
                "button[name='login']", ".btn-login", ".btn-primary"
            ]

            for selector in username_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        username_field = element
                        logger.info(f"✅ 找到用户名字段: {selector}")
                        break
                except:
                    continue

            for selector in password_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        password_field = element
                        logger.info(f"✅ 找到密码字段: {selector}")
                        break
                except:
                    continue

            for selector in login_button_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        login_button = element
                        logger.info(f"✅ 找到登录按钮: {selector}")
                        break
                except:
                    continue

            if not login_button:
                try:
                    buttons = self.driver.find_elements(By.TAG_NAME, "button")
                    for btn in buttons:
                        btn_text = btn.text.lower()
                        if any(text in btn_text for text in ['登录', 'log in', 'sign in', 'login']):
                            if btn.is_displayed() and btn.is_enabled():
                                login_button = btn
                                logger.info("✅ 找到登录按钮 (通过文本)")
                                break
                except:
                    pass

            if not username_field:
                logger.error("❌ 找不到用户名字段")
                with open(f"login_debug_{self.site_name}.html", "w", encoding='utf-8') as f:
                    f.write(self.driver.page_source)
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
            time.sleep(random.uniform(0.5, 1.2))
            
            for char in self.username:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))
            
            time.sleep(random.uniform(1, 2))
            
            logger.info("⌨️ 输入密码...")
            password_field.clear()
            time.sleep(random.uniform(0.5, 1.2))
            
            for char in self.password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            think_time = random.uniform(2, 4)
            logger.info(f"🤔 思考 {think_time:.1f} 秒...")
            time.sleep(think_time)

            actions = ActionChains(self.driver)
            actions.move_to_element(login_button).perform()
            time.sleep(random.uniform(0.5, 1))
            
            logger.info("🖱️ 点击登录按钮...")
            login_button.click()
            
            logger.info("⏳ 等待登录完成...")
            time.sleep(random.uniform(6, 10))

            EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(4, 6))

            login_success = self.robust_username_check()
            if login_success:
                logger.success("✅ 登录成功")
                return True
            else:
                logger.error("❌ 登录失败")
                with open(f"login_error_{self.site_name}.html", "w", encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def simulate_reading_behavior(self, stay_time=30):
        """模拟真实阅读行为"""
        logger.info(f"📖 模拟阅读行为，停留 {stay_time:.1f} 秒...")
        start_time = time.time()
        
        scroll_count = random.randint(6, 12)
        scrolls_done = 0
        
        while time.time() - start_time < stay_time:
            try:
                scroll_distance = random.randint(200, 800)
                self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                scrolls_done += 1
                
                if random.random() < 0.5:
                    read_time = random.uniform(3, 8)
                    logger.debug(f"📚 深度阅读 {read_time:.1f} 秒...")
                    time.sleep(read_time)
                else:
                    time.sleep(random.uniform(1, 3))
                
                if random.random() < 0.25:
                    back_scroll = random.randint(100, 300)
                    self.driver.execute_script(f"window.scrollBy(0, -{back_scroll})")
                    time.sleep(random.uniform(1, 2))
                
                if random.random() < 0.15:
                    pause_time = random.uniform(2, 5)
                    logger.debug(f"⏸️ 思考暂停 {pause_time:.1f} 秒")
                    time.sleep(pause_time)
                    
            except Exception as e:
                logger.debug(f"阅读行为模拟异常: {str(e)}")
                time.sleep(1)
        
        logger.debug(f"📊 阅读完成: {scrolls_done} 次滚动")

    def click_topic(self):
        """浏览主题"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题...")
        
        try:
            self.driver.get(self.site_config['latest_url'])
            time.sleep(random.uniform(4, 6))
            
            EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(3, 5))

            topic_elements = []
            topic_selectors = [".title", "a.title", "tr.topic-list-item a", ".topic-list-body a"]
            
            for selector in topic_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        topic_elements = [elem for elem in elements if elem.get_attribute('href') and '/t/' in elem.get_attribute('href')]
                        if topic_elements:
                            logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(topic_elements)} 个主题")
                            break
                except:
                    continue

            if not topic_elements:
                logger.error("❌ 没有找到主题列表")
                return 0

            browse_count = min(random.randint(8, 15), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题，随机浏览 {browse_count} 个")

            for i, idx in enumerate(selected_indices):
                try:
                    current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                    if not current_topic_elements or idx >= len(current_topic_elements):
                        logger.warning("⚠️ 主题元素已更新，重新获取...")
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(3)
                        current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                        if not current_topic_elements:
                            logger.error("❌ 重新获取主题列表失败")
                            break
                        remaining_indices = selected_indices[i:]
                        if not remaining_indices:
                            break
                        new_browse_count = min(len(remaining_indices), len(current_topic_elements))
                        selected_indices = random.sample(range(len(current_topic_elements)), new_browse_count)
                        idx = selected_indices[0]
                        browse_count = new_browse_count

                    topic = current_topic_elements[idx]
                    topic_url = topic.get_attribute("href")
                    if not topic_url:
                        continue
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url

                    logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")
                    
                    # 第3个主题浏览前进行状态检查
                    if i == 2:
                        logger.info("===== 第3个主题前进行状态检查 =====")
                        if not self.robust_username_check():
                            logger.warning("⚠️ 第3个主题前状态检查失败，尝试恢复...")
                            if self.ensure_logged_in():
                                logger.success("✅ 状态恢复成功，继续浏览")
                                self.driver.get(self.site_config['latest_url'])
                                time.sleep(4)
                                current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                                if not current_topic_elements:
                                    logger.error("❌ 状态恢复后未找到主题列表")
                                    return success_count
                                remaining_indices = selected_indices[i:]
                                if not remaining_indices:
                                    logger.warning("⚠️ 状态恢复后没有剩余主题可浏览")
                                    return success_count
                                new_browse_count = min(len(remaining_indices), len(current_topic_elements))
                                selected_indices = random.sample(range(len(current_topic_elements)), new_browse_count)
                                idx = selected_indices[0]
                                browse_count = new_browse_count
                                i = 0
                                topic = current_topic_elements[idx]
                                topic_url = topic.get_attribute("href")
                                if not topic_url:
                                    continue
                                if not topic_url.startswith('http'):
                                    topic_url = self.site_config['base_url'] + topic_url
                            else:
                                logger.error("❌ 状态恢复失败，停止浏览")
                                return success_count
                    
                    self.driver.get(topic_url)
                    time.sleep(random.uniform(3, 5))
                    
                    page_stay_time = random.uniform(30, 50)
                    self.simulate_reading_behavior(page_stay_time)
                    
                    self.driver.back()
                    time.sleep(random.uniform(3, 5))
                    
                    success_count += 1
                    
                    if i < browse_count - 1:
                        wait_time = random.uniform(12, 20)
                        logger.info(f"⏳ 浏览间隔等待 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except StaleElementReferenceException:
                    logger.warning("⚠️ 主题元素已过时，跳过当前主题")
                    continue
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
                    try:
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(3)
                    except:
                        pass
                    continue

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            
            # 浏览后状态验证和保存
            logger.info("===== 浏览主题后状态验证 =====")
            if not self.robust_username_check():
                logger.warning("⚠️ 浏览后状态验证失败，尝试恢复...")
                if self.ensure_logged_in():
                    logger.success("✅ 状态恢复成功")
                else:
                    logger.error("❌ 状态恢复失败")
                    return 0
            
            return success_count
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            return 0

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息")
        max_retries = 2
        for retry in range(max_retries):
            try:
                self.driver.get(self.site_config['connect_url'])
                time.sleep(6)

                EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)
                time.sleep(4)

                page_source = self.driver.page_source
                
                if retry == max_retries - 1:
                    with open(f"connect_debug_{self.site_name}.html", "w", encoding='utf-8') as f:
                        f.write(page_source)
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page_source, 'html.parser')
                
                tables = soup.find_all('table')
                if not tables:
                    logger.warning("⚠️ 未找到表格元素")
                    if retry < max_retries - 1:
                        continue
                    return
                    
                stats_table = None
                for table in tables:
                    table_text = table.get_text()
                    if any(keyword in table_text for keyword in ['访问次数', '回复的话题', '浏览的话题', '已读帖子']):
                        stats_table = table
                        break
                
                if not stats_table:
                    logger.warning("⚠️ 未找到统计表格")
                    if retry < max_retries - 1:
                        continue
                    return
                    
                stats_data = []
                rows = stats_table.find_all('tr')
                
                for row in rows[1:]:
                    cols = row.find_all(['td', 'th'])
                    if len(cols) >= 3:
                        item = cols[0].get_text(strip=True)
                        current = cols[1].get_text(strip=True)
                        requirement = cols[2].get_text(strip=True)
                        
                        col_class = cols[1].get('class', [])
                        if isinstance(col_class, list):
                            col_class = ' '.join(col_class)
                        status = '✅' if 'text-green' in col_class or 'green' in col_class else '❌' if 'text-red' in col_class or 'red' in col_class else '➖'
                        
                        stats_data.append([item, current, requirement, status])
                
                if stats_data:
                    print("\n" + "="*80)
                    print(f"📊 {self.site_name.upper()} 连接信息")
                    print("="*80)
                    
                    try:
                        from tabulate import tabulate
                        print(tabulate(stats_data, headers=["项目", "当前", "要求", "状态"], tablefmt="grid"))
                    except ImportError:
                        print(f"{'项目':<25} {'当前':<30} {'要求':<20} {'状态':<10}")
                        print("-" * 80)
                        for item in stats_data:
                            print(f"{item[0]:<25} {item[1]:<30} {item[2]:<20} {item[3]:<10}")
                    
                    print("="*80 + "\n")
                    
                    passed = sum(1 for item in stats_data if item[3] == '✅')
                    total = len(stats_data)
                    logger.success(f"📊 连接信息统计: {passed}/{total} 项达标")
                    
                    for item in stats_data:
                        if '访问天数' in item[0] or '访问次数' in item[0]:
                            logger.info(f"📈 关键指标 - {item[0]}: {item[1]}")
                    break
                else:
                    logger.warning("⚠️ 无法解析连接信息表格")
                    if retry < max_retries - 1:
                        continue

            except Exception as e:
                logger.error(f"获取连接信息失败: {str(e)}")
                if retry < max_retries - 1:
                    logger.info(f"🔄 重试获取连接信息 ({retry+1}/{max_retries})")
                    time.sleep(5)

    def perform_additional_activities(self):
        """执行额外的活跃行为"""
        logger.info("🎯 执行额外活跃行为提升信任等级...")
        
        activities_performed = 0
        
        try:
            additional_pages = [
                "/categories",
                "/top",
                "/about"
            ]
            
            for page in additional_pages[:2]:
                try:
                    url = self.site_config['base_url'] + page
                    self.driver.get(url)
                    time.sleep(random.uniform(8, 15))
                    self.simulate_reading_behavior(random.uniform(10, 20))
                    activities_performed += 1
                    logger.info(f"✅ 访问额外页面: {page}")
                except:
                    pass
            
            self.driver.get(self.site_config['latest_url'])
            time.sleep(3)
            self.simulate_reading_behavior(20)
            activities_performed += 1
            
            logger.success(f"✅ 完成 {activities_performed} 项额外活跃行为")
            return activities_performed
            
        except Exception as e:
            logger.error(f"执行额外活跃行为失败: {str(e)}")
            return activities_performed

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 1. 登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                self.save_comprehensive_state(False, 0, {'error': '登录失败'})
                return False

            # 2. 额外活跃行为
            additional_activities = self.perform_additional_activities()

            # 3. 浏览主题
            browse_success_count = self.click_topic()
            if browse_success_count == 0:
                logger.error("❌ 浏览主题失败或登录状态丢失")
                self.save_comprehensive_state(False, 0, {'error': '浏览主题失败'})
                return False

            # 4. 获取统计信息
            self.get_user_stats()

            # 5. 打印连接信息
            self.print_connect_info()

            # 6. 保存最终状态
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

    def get_user_stats(self):
        """获取用户信任级别统计信息"""
        logger.info("📊 获取用户信任级别统计信息")
        
        try:
            connect_url = self.site_config['connect_url']
            logger.info(f"📍 访问连接页面: {connect_url}")
            self.driver.get(connect_url)
            time.sleep(random.uniform(6, 9))
            
            EnhancedCloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(4, 6))
            
            page_source = self.driver.page_source
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page_source, 'html.parser')
            
            stats_table = None
            tables = soup.find_all('table')
            for table in tables:
                if table.find('td', string=lambda text: text and '访问次数' in text):
                    stats_table = table
                    break
            
            if not stats_table:
                logger.warning("⚠️ 未找到信任级别统计表格")
                return self._parse_stats_fallback()
            
            stats_data = []
            rows = stats_table.find_all('tr')
            
            for row in rows[1:]:
                cols = row.find_all(['td', 'th'])
                if len(cols) >= 3:
                    item = cols[0].get_text(strip=True)
                    current = cols[1].get_text(strip=True)
                    requirement = cols[2].get_text(strip=True)
                    
                    col_class = cols[1].get('class', [])
                    if isinstance(col_class, list):
                        col_class = ' '.join(col_class)
                    color = 'green' if 'text-green' in col_class or 'green' in col_class else 'red' if 'text-red' in col_class or 'red' in col_class else 'black'
                    
                    stats_data.append([item, current, requirement, color])
            
            if stats_data:
                print("\n" + "="*80)
                print(f"📈 {self.site_name.upper()} 信任级别要求统计")
                print("="*80)
                
                try:
                    from tabulate import tabulate
                    print(tabulate(stats_data, headers=["项目", "当前", "要求", "状态"], tablefmt="grid"))
                except ImportError:
                    print(f"{'项目':<25} {'当前':<30} {'要求':<20} {'状态':<10}")
                    print("-" * 80)
                    for item in stats_data:
                        status = "✅" if item[3] == 'green' else "❌" if item[3] == 'red' else "➖"
                        print(f"{item[0]:<25} {item[1]:<30} {item[2]:<20} {status}")
                
                print("="*80 + "\n")
                
                passed = sum(1 for item in stats_data if item[3] == 'green')
                total = len(stats_data)
                logger.success(f"📊 统计完成: {passed}/{total} 项达标")
                
                return True
            else:
                logger.warning("⚠️ 未提取到统计信息")
                return False
            
        except Exception as e:
            logger.error(f"获取统计信息失败: {str(e)}")
            return False
    
    def _parse_stats_fallback(self):
        """备用解析方法"""
        try:
            logger.info("尝试备用解析方法...")
            
            stats_elements = self.driver.find_elements(By.CSS_SELECTOR, "tr")
            stats_data = []
            
            for element in stats_elements:
                text = element.text
                if any(keyword in text for keyword in ['访问次数', '回复的话题', '浏览的话题', '已读帖子']):
                    parts = text.split('\n')
                    if len(parts) >= 3:
                        stats_data.append([parts[0], parts[1], parts[2], 'unknown'])
            
            if stats_data:
                print("\n" + "="*60)
                print(f"📈 {self.site_name.upper()} 统计信息 (备用模式)")
                print("="*60)
                for item in stats_data[:10]:
                    print(f"{item[0]}: {item[1]} / {item[2]}")
                print("="*60 + "\n")
                return True
            
            return False
        except:
            return False

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (增强缓存版)")
    
    # 配置日志
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")
    logger.add("automation.log", rotation="10 MB", retention=3)
    
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
        cache_files = [
            f"cf_cookies_{site_name}.json",
            f"browser_state_{site_name}.json"
        ]
        
        for cache_file in cache_files:
            if EnhancedCacheManager.is_cache_valid(cache_file, MAX_CACHE_AGE_HOURS):
                logger.info(f"  ✅ {cache_file} - 有效")
            else:
                logger.info(f"  ❌ {cache_file} - 无效或不存在")

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        if not credentials.get('username') or not credentials.get('password'):
            logger.warning(f"⏭️ 跳过 {site_name} - 未配置凭证")
            continue

        logger.info(f"🔧 初始化 {site_name} 浏览器")
        try:
            browser = EnhancedLinuxDoBrowser(site_config, credentials)
            success = browser.run()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(20, 30)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个站点...")
            time.sleep(wait_time)

    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功站点: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败站点: {', '.join(failed_sites) if failed_sites else '无'}")

    if success_sites:
        logger.success("🎉 部分任务完成")
        sys.exit(0)
    else:
        logger.error("💥 所有任务失败")
        sys.exit(1)

if __name__ == "__main__":
    main()
