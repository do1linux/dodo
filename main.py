import os
import random
import time
import sys
import json
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

# DoH 服务器配置
DOH_SERVER = os.environ.get("DOH_SERVER", "https://ld.ddd.oaifree.com/query-dns")

# turnstilePatch 扩展路径
TURNSTILE_PATCH_PATH = os.path.abspath("turnstilePatch")

# Cookies过期时间（小时）
COOKIES_EXPIRY_HOURS = 24

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类 - 缓存Cloudflare相关Cookies和浏览器状态"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录（当前目录）"""
        return os.path.dirname(os.path.abspath(__file__))

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
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                logger.info(f"✅ 缓存文件验证: {file_name} ({file_size} 字节)")
            else:
                logger.error(f"❌ 缓存文件保存失败: {file_name}")
                
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def is_cache_valid(file_name, expiry_hours=COOKIES_EXPIRY_HOURS):
        """检查缓存是否有效（未过期且存在）"""
        file_path = CacheManager.get_cache_file_path(file_name)
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

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    @staticmethod
    def query_doh(domain, doh_server=DOH_SERVER):
        """通过DoH服务器查询DNS"""
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
    def handle_cloudflare_with_doh(driver, doh_server=DOH_SERVER, max_attempts=12, timeout=180):
        """使用DoH处理Cloudflare验证 - 增强版本"""
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
            CloudflareHandler.query_doh(domain, doh_server)

        for attempt in range(max_attempts):
            try:
                current_url = driver.current_url
                page_title = driver.title.lower() if driver.title else ""
                page_source = driver.page_source.lower() if driver.page_source else ""
                
                # 检查验证状态 - 更严格的检查
                cloudflare_indicators = ["just a moment", "checking", "please wait", "ddos protection", "cloudflare", "verifying"]
                is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                
                # 检查是否重定向到挑战页面
                is_challenge_page = "challenge" in current_url or "challenges" in current_url
                
                if not is_cloudflare_page and not is_challenge_page:
                    # 额外检查：等待页面完全加载
                    time.sleep(3)
                    # 再次检查
                    page_title = driver.title.lower() if driver.title else ""
                    page_source = driver.page_source.lower() if driver.page_source else ""
                    is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                    
                    if not is_cloudflare_page:
                        logger.success("✅ Cloudflare验证通过")
                        return True

                # 动态调整等待时间
                base_wait = 5 + (attempt * 1.5)  # 逐渐增加等待时间
                wait_time = min(base_wait, 15)  # 最大不超过15秒
                elapsed = time.time() - start_time
                
                logger.info(f"⏳ 等待验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts} [耗时: {elapsed:.0f}秒]")
                time.sleep(wait_time)
                
                # 超时检查
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
                # 定期刷新 - 更智能的刷新策略
                if attempt % 3 == 2:  # 每3次尝试刷新一次
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面")
                        time.sleep(3)
                    except:
                        pass
                        
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(5)

        logger.warning("⚠️ Cloudflare验证可能未完全通过，强制继续")
        return False  # 返回False表示验证可能未通过

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.driver = None
        self.wait = None
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器"""
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
                "notifications": 2
            }
        })
        
        # 加载turnstilePatch扩展
        if os.path.exists(TURNSTILE_PATCH_PATH):
            chrome_options.add_argument(f'--load-extension={TURNSTILE_PATCH_PATH}')
            logger.info(f"✅ 已加载turnstilePatch扩展: {TURNSTILE_PATCH_PATH}")
        else:
            logger.warning(f"⚠️ 未找到turnstilePatch扩展目录: {TURNSTILE_PATCH_PATH}")
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            # 隐藏webdriver属性
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # 伪造其他指纹特征
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['zh-CN', 'zh', 'en']
                    });
                    Object.defineProperty(navigator, 'mimeTypes', {
                        get: () => [1, 2]
                    });
                    window.chrome = {
                        runtime: {}
                    };
                    delete navigator.__proto__.connection;
                '''
            })
            
        except Exception as e:
            logger.error(f"Chrome驱动初始化失败: {str(e)}")
            raise
            
        self.wait = WebDriverWait(self.driver, 25)

    def robust_username_check(self, max_retries=3):
        """增强的用户名检查 - 确保登录状态真实有效"""
        logger.info("🔍 增强验证登录状态 - 多维度检测用户名...")
        
        for retry in range(max_retries):
            try:
                # 检查多个关键页面
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
                        
                        # 处理可能的Cloudflare验证
                        cf_passed = CloudflareHandler.handle_cloudflare_with_doh(self.driver)
                        if not cf_passed:
                            logger.warning(f"⚠️ {page_name} Cloudflare验证可能有问题")
                        
                        time.sleep(random.uniform(2, 3))
                        
                        # 获取页面内容进行多重检查
                        page_content = self.driver.page_source
                        current_url = self.driver.current_url
                        
                        # 多重检查：用户名在页面内容中
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
                    # 额外验证：检查是否有登录相关的元素
                    try:
                        # 检查是否有退出按钮或用户菜单
                        logout_indicators = ["logout", "sign out", "退出", "登出"]
                        page_lower = self.driver.page_source.lower()
                        if any(indicator in page_lower for indicator in logout_indicators):
                            logger.success("✅ 找到退出按钮，确认登录状态有效")
                        return True
                    except:
                        pass
                    
                    return True
                
                logger.warning(f"❌ 在所有页面中都未找到用户名 (尝试 {retry + 1}/{max_retries})")
                
                # 重试前等待
                if retry < max_retries - 1:
                    wait_time = random.uniform(8, 12)
                    logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                logger.error(f"登录状态检查异常: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(8)
        
        logger.error(f"❌ 增强验证失败: 在所有重试后都未找到用户名")
        return False

    def print_connect_info(self):
        """打印连接信息 - 增强版本"""
        logger.info("🔗 获取连接信息")
        max_retries = 2
        for retry in range(max_retries):
            try:
                self.driver.get(self.site_config['connect_url'])
                time.sleep(6)

                # 处理可能的Cloudflare验证
                CloudflareHandler.handle_cloudflare_with_doh(self.driver)
                time.sleep(4)

                # 获取页面内容进行解析
                page_source = self.driver.page_source
                
                # 保存页面用于调试
                if retry == max_retries - 1:
                    with open(f"connect_debug_{self.site_name}.html", "w", encoding='utf-8') as f:
                        f.write(page_source)
                
                # 使用BeautifulSoup解析HTML
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page_source, 'html.parser')
                
                # 查找所有表格
                tables = soup.find_all('table')
                if not tables:
                    logger.warning("⚠️ 未找到表格元素")
                    if retry < max_retries - 1:
                        continue
                    return
                    
                # 查找包含统计信息的表格
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
                    
                # 提取表格数据
                stats_data = []
                rows = stats_table.find_all('tr')
                
                for row in rows[1:]:  # 跳过表头
                    cols = row.find_all(['td', 'th'])
                    if len(cols) >= 3:
                        item = cols[0].get_text(strip=True)
                        current = cols[1].get_text(strip=True)
                        requirement = cols[2].get_text(strip=True)
                        
                        # 判断状态
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
                        # 备用显示方式
                        print(f"{'项目':<25} {'当前':<30} {'要求':<20} {'状态':<10}")
                        print("-" * 80)
                        for item in stats_data:
                            print(f"{item[0]:<25} {item[1]:<30} {item[2]:<20} {item[3]:<10}")
                    
                    print("="*80 + "\n")
                    
                    # 统计达标情况
                    passed = sum(1 for item in stats_data if item[3] == '✅')
                    total = len(stats_data)
                    logger.success(f"📊 连接信息统计: {passed}/{total} 项达标")
                    
                    # 记录关键指标
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

    def save_cookies_to_cache(self):
        """将当前Cookies保存到缓存"""
        try:
            cookies = self.driver.get_cookies()
            cookie_data = {
                'cookies': cookies,
                'timestamp': datetime.now().isoformat(),
                'username': self.username
            }
            CacheManager.save_cache(cookie_data, f"cf_cookies_{self.site_name}.json")
            logger.success(f"✅ Cookies已缓存: cf_cookies_{self.site_name}.json")
            return True
        except Exception as e:
            logger.error(f"Cookies缓存失败: {str(e)}")
            return False

    def load_cookies_from_cache(self):
        """从缓存加载Cookies"""
        cache_file = f"cf_cookies_{self.site_name}.json"
        
        # 检查缓存是否存在且有效
        if not CacheManager.is_cache_valid(cache_file, COOKIES_EXPIRY_HOURS):
            logger.warning("⚠️ Cookies缓存无效或不存在")
            return False
        
        try:
            cookie_data = CacheManager.load_cache(cache_file)
            if not cookie_data or 'cookies' not in cookie_data:
                return False
            
            # 加载Cookies到浏览器
            self.driver.get(self.site_config['base_url'])
            time.sleep(3)
            
            for cookie in cookie_data['cookies']:
                try:
                    # 清理cookie字典，只保留必要字段
                    clean_cookie = {
                        'name': cookie.get('name'),
                        'value': cookie.get('value'),
                        'domain': cookie.get('domain', '.linux.do' if 'linux' in self.site_name else '.idcflare.com'),
                        'path': cookie.get('path', '/'),
                        'secure': cookie.get('secure', True),
                        'httpOnly': cookie.get('httpOnly', False)
                    }
                    # 删除可能存在的过期时间字段，让浏览器自动管理
                    if 'expiry' in clean_cookie:
                        del clean_cookie['expiry']
                    if 'expires' in clean_cookie:
                        del clean_cookie['expires']
                    
                    self.driver.add_cookie(clean_cookie)
                except Exception as e:
                    logger.debug(f"单个Cookie加载失败: {str(e)}")
                    continue
            
            logger.success(f"✅ Cookies已从缓存加载: {len(cookie_data['cookies'])}个")
            return True
        except Exception as e:
            logger.error(f"Cookies加载失败: {str(e)}")
            return False

    def ensure_logged_in(self):
        """确保用户已登录 - 增强版本"""
        # 第一步：尝试使用Cookies缓存登录（如果启用且未强制重新登录）
        if not FORCE_LOGIN_EVERY_TIME:
            logger.info("🎯 尝试使用Cookies缓存登录...")
            if self.load_cookies_from_cache():
                # 使用增强验证检查登录状态
                if self.robust_username_check():
                    logger.success("✅ Cookies缓存登录成功")
                    return True
                else:
                    logger.warning("⚠️ Cookies缓存无效，尝试重新登录")
        
        # 第二步：如果缓存失败或强制登录，执行手动登录
        logger.info("🔐 执行手动登录流程...")
        login_success = self.attempt_login()
        
        # 登录成功后保存Cookies
        if login_success:
            self.save_cookies_to_cache()
        
        return login_success

    def attempt_login(self):
        """尝试登录 - 增强版本"""
        logger.info("🔐 开始登录流程...")
        
        try:
            # 访问登录页面
            self.driver.get(self.site_config['login_url'])
            time.sleep(random.uniform(4, 6))

            # 处理Cloudflare验证
            cf_passed = CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            if not cf_passed:
                logger.warning("⚠️ Cloudflare验证可能有问题，继续尝试登录")
            time.sleep(random.uniform(3, 5))

            # 记录当前页面状态
            current_url = self.driver.current_url
            page_title = self.driver.title
            logger.info(f"📄 当前页面: {page_title} | {current_url}")

            # 如果被重定向，回到登录页面
            if 'login' not in current_url and 'signin' not in current_url:
                logger.info("🔄 被重定向，尝试回到登录页面")
                self.driver.get(self.site_config['login_url'])
                time.sleep(random.uniform(4, 6))
                CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 查找表单元素
            username_field = None
            password_field = None
            login_button = None

            # 尝试多种选择器
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

            # 备选：通过文本查找登录按钮
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
                # 保存页面源码用于调试
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
            
            # 模拟人类输入速度
            for char in self.username:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))
            
            # 随机停顿
            time.sleep(random.uniform(1, 2))
            
            logger.info("⌨️ 输入密码...")
            password_field.clear()
            time.sleep(random.uniform(0.5, 1.2))
            
            # 模拟人类输入速度
            for char in self.password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            # 随机思考时间
            think_time = random.uniform(2, 4)
            logger.info(f"🤔 思考 {think_time:.1f} 秒...")
            time.sleep(think_time)

            # 模拟鼠标移动到按钮
            actions = ActionChains(self.driver)
            actions.move_to_element(login_button).perform()
            time.sleep(random.uniform(0.5, 1))
            
            # 点击登录按钮
            logger.info("🖱️ 点击登录按钮...")
            login_button.click()
            
            # 等待登录完成
            logger.info("⏳ 等待登录完成...")
            time.sleep(random.uniform(6, 10))

            # 处理登录后的Cloudflare验证
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(4, 6))

            # 检查登录是否成功 - 使用增强验证
            login_success = self.robust_username_check()
            if login_success:
                logger.success("✅ 登录成功")
                # 登录成功后立即保存Cookies
                self.save_cookies_to_cache()
                return True
            else:
                logger.error("❌ 登录失败")
                # 保存错误页面
                with open(f"login_error_{self.site_name}.html", "w", encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def click_like(self):
        """点赞当前帖子 - 增强版本"""
        try:
            # 多种点赞按钮选择器
            like_selectors = [
                ".discourse-reactions-reaction-button",
                ".like-button",
                ".btn-like",
                "button[title*='Like']",
                "button[title*='点赞']"
            ]
            
            for selector in like_selectors:
                try:
                    like_buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for like_button in like_buttons:
                        if like_button.is_displayed() and like_button.is_enabled():
                            # 检查是否已经点赞
                            button_class = like_button.get_attribute('class')
                            if 'has-like' not in button_class and 'liked' not in button_class:
                                logger.info("👍 找到未点赞的帖子，准备点赞")
                                # 滚动到按钮位置
                                self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", like_button)
                                time.sleep(1)
                                
                                # 模拟鼠标移动
                                actions = ActionChains(self.driver)
                                actions.move_to_element(like_button).perform()
                                time.sleep(0.5)
                                
                                like_button.click()
                                logger.success("✅ 点赞成功")
                                time.sleep(random.uniform(2, 4))
                                return True
                            else:
                                logger.info("ℹ️ 帖子已经点过赞了")
                                return False
                except:
                    continue
            
            logger.info("ℹ️ 未找到可点赞的按钮")
            return False
            
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")
            return False

    def simulate_reading_behavior(self, stay_time=30):
        """模拟真实阅读行为 - 增强版本"""
        logger.info(f"📖 模拟阅读行为，停留 {stay_time:.1f} 秒...")
        start_time = time.time()
        
        # 随机滚动次数
        scroll_count = random.randint(6, 12)
        scrolls_done = 0
        
        while time.time() - start_time < stay_time:
            try:
                # 随机滚动距离
                scroll_distance = random.randint(200, 800)
                self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                scrolls_done += 1
                
                # 随机阅读时间
                if random.random() < 0.5:  # 50%概率长时间阅读
                    read_time = random.uniform(3, 8)
                    logger.debug(f"📚 深度阅读 {read_time:.1f} 秒...")
                    time.sleep(read_time)
                else:
                    time.sleep(random.uniform(1, 3))
                
                # 随机回滚模拟重新阅读
                if random.random() < 0.25:
                    back_scroll = random.randint(100, 300)
                    self.driver.execute_script(f"window.scrollBy(0, -{back_scroll})")
                    time.sleep(random.uniform(1, 2))
                
                # 随机点赞 (2%概率)
                if random.random() < 0.02:
                    self.click_like()
                
                # 随机暂停思考
                if random.random() < 0.15:
                    pause_time = random.uniform(2, 5)
                    logger.debug(f"⏸️ 思考暂停 {pause_time:.1f} 秒")
                    time.sleep(pause_time)
                    
            except Exception as e:
                logger.debug(f"阅读行为模拟异常: {str(e)}")
                time.sleep(1)
        
        logger.debug(f"📊 阅读完成: {scrolls_done} 次滚动")

    def click_topic(self):
        """浏览主题 - 增强版本，包含更多活跃行为"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题...")
        
        try:
            # 访问最新页面
            self.driver.get(self.site_config['latest_url'])
            time.sleep(random.uniform(4, 6))
            
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(3, 5))

            # 查找主题元素 - 使用更稳定的方式
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

            # 随机选择8-100个主题浏览
            browse_count = min(random.randint(8, 100), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题，随机浏览 {browse_count} 个")

            for i, idx in enumerate(selected_indices):
                try:
                    # 每次重新获取主题元素，避免stale element
                    current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                    if not current_topic_elements or idx >= len(current_topic_elements):
                        logger.warning("⚠️ 主题元素已更新，重新获取...")
                        # 重新导航到最新页面
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(3)
                        current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                        if not current_topic_elements:
                            logger.error("❌ 重新获取主题列表失败")
                            break
                        # 重新选择剩余的主题
                        remaining_indices = selected_indices[i:]
                        if not remaining_indices:
                            break
                        # 更新为新的随机选择
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
                    
                    # 第3个主题浏览前进行用户名检测
                    if i == 2:  # 第3个主题
                        logger.info("===== 第3个主题前进行用户名检测 =====")
                        if not self.robust_username_check():
                            logger.warning("⚠️ 第3个主题前未找到用户名，尝试重新登录...")
                            if self.ensure_logged_in():
                                logger.success("✅ 重新登录成功，继续浏览")
                                # 重新导航到最新页面
                                self.driver.get(self.site_config['latest_url'])
                                time.sleep(4)
                                # 重新获取主题元素
                                current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                                if not current_topic_elements:
                                    logger.error("❌ 重新登录后未找到主题列表")
                                    return success_count
                                # 重新选择剩余的主题
                                remaining_indices = selected_indices[i:]
                                if not remaining_indices:
                                    logger.warning("⚠️ 重新登录后没有剩余主题可浏览")
                                    return success_count
                                # 更新为新的随机选择
                                new_browse_count = min(len(remaining_indices), len(current_topic_elements))
                                selected_indices = random.sample(range(len(current_topic_elements)), new_browse_count)
                                idx = selected_indices[0]
                                browse_count = new_browse_count
                                i = 0  # 重置索引
                                topic = current_topic_elements[idx]
                                topic_url = topic.get_attribute("href")
                                if not topic_url:
                                    continue
                                if not topic_url.startswith('http'):
                                    topic_url = self.site_config['base_url'] + topic_url
                            else:
                                logger.error("❌ 重新登录失败，停止浏览")
                                return success_count
                    
                    # 在同一标签页打开主题
                    self.driver.get(topic_url)
                    time.sleep(random.uniform(3, 5))
                    
                    # 模拟真实浏览行为
                    page_stay_time = random.uniform(30, 50)  # 增加停留时间
                    self.simulate_reading_behavior(page_stay_time)
                    
                    # 返回主题列表页面
                    self.driver.back()
                    time.sleep(random.uniform(3, 5))
                    
                    success_count += 1
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(12, 20)
                        logger.info(f"⏳ 浏览间隔等待 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except StaleElementReferenceException:
                    logger.warning("⚠️ 主题元素已过时，跳过当前主题")
                    continue
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
                    # 尝试返回主题列表页面
                    try:
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(3)
                    except:
                        pass
                    continue

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            
            # 浏览后再次验证登录状态
            logger.info("===== 浏览主题后再次验证登录状态 =====")
            if not self.robust_username_check():
                logger.warning("⚠️ 浏览后登录状态验证失败，尝试重新登录...")
                if self.ensure_logged_in():
                    logger.success("✅ 重新登录成功")
                else:
                    logger.error("❌ 重新登录失败")
                    return 0
            
            return success_count
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            return 0

    def get_user_stats(self):
        """获取用户信任级别统计信息 - 从connect_url获取"""
        logger.info("📊 获取用户信任级别统计信息")
        
        try:
            # 访问连接页面获取统计信息
            connect_url = self.site_config['connect_url']
            logger.info(f"📍 访问连接页面: {connect_url}")
            self.driver.get(connect_url)
            time.sleep(random.uniform(6, 9))
            
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(4, 6))
            
            # 获取页面源码
            page_source = self.driver.page_source
            
            # 解析HTML表格
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # 查找包含要求的表格
            stats_table = None
            tables = soup.find_all('table')
            for table in tables:
                if table.find('td', string=lambda text: text and '访问次数' in text):
                    stats_table = table
                    break
            
            if not stats_table:
                logger.warning("⚠️ 未找到信任级别统计表格")
                # 尝试直接解析关键信息
                return self._parse_stats_fallback()
            
            # 提取表格数据
            stats_data = []
            rows = stats_table.find_all('tr')
            
            for row in rows[1:]:  # 跳过表头
                cols = row.find_all(['td', 'th'])
                if len(cols) >= 3:
                    item = cols[0].get_text(strip=True)
                    current = cols[1].get_text(strip=True)
                    requirement = cols[2].get_text(strip=True)
                    
                    # 判断颜色（达标/未达标）
                    col_class = cols[1].get('class', [])
                    if isinstance(col_class, list):
                        col_class = ' '.join(col_class)
                    color = 'green' if 'text-green' in col_class or 'green' in col_class else 'red' if 'text-red' in col_class or 'red' in col_class else 'black'
                    
                    stats_data.append([item, current, requirement, color])
            
            if stats_data:
                print("\n" + "="*80)
                print(f"📈 {self.site_name.upper()} 信任级别要求统计")
                print("="*80)
                
                # 打印表格
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
                
                # 统计达标情况
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
            # 直接通过XPath或CSS选择器查找关键元素
            logger.info("尝试备用解析方法...")
            
            # 查找所有包含统计信息的元素
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
                for item in stats_data[:10]:  # 最多显示10条
                    print(f"{item[0]}: {item[1]} / {item[2]}")
                print("="*60 + "\n")
                return True
            
            return False
        except:
            return False

    def perform_additional_activities(self):
        """执行额外的活跃行为来提升信任等级"""
        logger.info("🎯 执行额外活跃行为提升信任等级...")
        
        activities_performed = 0
        
        try:
            # 1. 访问更多页面增加访问次数
            additional_pages = [
                "/categories",
                "/top",
                "/about"
            ]
            
            for page in additional_pages[:2]:  # 只访问前2个额外页面
                try:
                    url = self.site_config['base_url'] + page
                    self.driver.get(url)
                    time.sleep(random.uniform(8, 15))
                    self.simulate_reading_behavior(random.uniform(10, 20))
                    activities_performed += 1
                    logger.info(f"✅ 访问额外页面: {page}")
                except:
                    pass
            
            # 2. 在最新页面进行更深入的浏览
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
        """执行完整自动化流程 - 增强版本"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 1. 登录（使用增强验证）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                self.generate_browser_state(False, 0)
                return False

            # 2. 执行额外活跃行为
            additional_activities = self.perform_additional_activities()

            # 3. 浏览主题 (8-10个)
            browse_success_count = self.click_topic()
            if browse_success_count == 0:
                logger.error("❌ 浏览主题失败或登录状态丢失")
                self.generate_browser_state(False, 0)
                return False

            # 4. 获取用户统计信息
            self.get_user_stats()

            # 5. 打印连接信息
            self.print_connect_info()

            # 6. 生成状态文件
            total_activities = browse_success_count + additional_activities
            self.generate_browser_state(True, total_activities)

            logger.success(f"✅ {self.site_name} 处理完成 - 总计 {total_activities} 项活动")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            self.generate_browser_state(False, 0)
            return False
            
        finally:
            try:
                if self.driver:
                    self.driver.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (增强版)")
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

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

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
