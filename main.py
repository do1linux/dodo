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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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
    def handle_cloudflare_with_doh(driver, doh_server=DOH_SERVER, max_attempts=8, timeout=120):
        """使用DoH处理Cloudflare验证"""
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
                
                # 检查验证状态
                cloudflare_indicators = ["just a moment", "checking", "please wait", "ddos protection", "cloudflare"]
                is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                
                if not is_cloudflare_page:
                    logger.success("✅ Cloudflare验证通过")
                    return True

                # 等待时间
                wait_time = random.uniform(3, 6)
                elapsed = time.time() - start_time
                
                logger.info(f"⏳ 等待验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts} [耗时: {elapsed:.0f}秒]")
                time.sleep(wait_time)
                
                # 超时检查
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
                # 定期刷新
                if attempt % 2 == 1:
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面")
                        time.sleep(2)
                    except:
                        pass
                        
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(5)

        logger.warning("⚠️ Cloudflare验证可能未完全通过，强制继续")
        return True

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        
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
                "cookies": 1
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
            
        self.wait = WebDriverWait(self.driver, 20)

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息")
        try:
            self.driver.get(self.site_config['connect_url'])
            time.sleep(5)

            # 查找表格元素
            table = self.driver.find_element(By.CSS_SELECTOR, "table")
            rows = table.find_elements(By.TAG_NAME, "tr")
            info = []

            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    info.append([project, current, requirement])

            if info:
                print("\n" + "="*50)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*50)
                from tabulate import tabulate
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("="*50 + "\n")
            else:
                logger.warning("⚠️ 无法获取连接信息")

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

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
            time.sleep(2)
            
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
        """确保用户已登录 - 优先使用Cookies缓存"""
        # 第一步：尝试使用Cookies缓存登录（如果启用且未强制重新登录）
        if not FORCE_LOGIN_EVERY_TIME:
            logger.info("🎯 尝试使用Cookies缓存登录...")
            if self.load_cookies_from_cache():
                # 验证Cookies是否有效
                if self.strict_username_login_check():
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
        """尝试登录"""
        logger.info("🔐 开始登录流程...")
        
        try:
            # 访问登录页面
            self.driver.get(self.site_config['login_url'])
            time.sleep(random.uniform(3, 5))

            # 处理Cloudflare验证
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(2, 4))

            # 记录当前页面状态
            current_url = self.driver.current_url
            page_title = self.driver.title
            logger.info(f"📄 当前页面: {page_title} | {current_url}")

            # 如果被重定向，回到登录页面
            if 'login' not in current_url:
                logger.info("🔄 被重定向，尝试回到登录页面")
                self.driver.get(self.site_config['login_url'])
                time.sleep(random.uniform(3, 5))
                CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 查找表单元素
            username_field = None
            password_field = None
            login_button = None

            # 尝试多种选择器
            username_selectors = ["#login-account-name", "#username", "input[name='username']", "input[name='login']"]
            password_selectors = ["#login-account-password", "#password", "input[name='password']"]
            login_button_selectors = ["#login-button", "button[type='submit']", "input[type='submit']"]

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
                        if any(text in btn_text for text in ['登录', 'log in', 'sign in']):
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
                time.sleep(random.uniform(0.05, 0.2))
            
            # 随机停顿
            time.sleep(random.uniform(1, 2))
            
            logger.info("⌨️ 输入密码...")
            password_field.clear()
            time.sleep(random.uniform(0.5, 1.2))
            
            # 模拟人类输入速度
            for char in self.password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.2))

            # 随机思考时间
            think_time = random.uniform(1.5, 3)
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
            time.sleep(random.uniform(5, 8))

            # 处理登录后的Cloudflare验证
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(3, 5))

            # 检查登录是否成功 - 严格验证用户名
            login_success = self.strict_username_login_check()
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

    def strict_username_login_check(self, context=""):
        """严格登录状态检查 - 必须检测到用户名"""
        if context:
            logger.info(f"🔍 {context} - 严格检测用户名...")
        else:
            logger.info("🔍 严格验证登录状态 - 检测用户名...")
        
        max_retries = 3
        for retry in range(max_retries):
            try:
                # 检查多个可能的页面来寻找用户名
                check_urls = [
                    self.site_config['latest_url'],
                    f"{self.site_config['user_url']}/{self.username}"
                ]
                
                for check_url in check_urls:
                    try:
                        logger.info(f"📍 检查页面: {check_url}")
                        self.driver.get(check_url)
                        time.sleep(random.uniform(3, 5))
                        
                        # 处理可能的Cloudflare验证
                        CloudflareHandler.handle_cloudflare_with_doh(self.driver)
                        time.sleep(random.uniform(2, 3))
                        
                        # 获取页面内容
                        page_content = self.driver.page_source
                        current_url = self.driver.current_url
                        
                        # 严格检查用户名是否在页面中 - 这是唯一标准
                        if self.username.lower() in page_content.lower():
                            logger.success(f"✅ 在页面中找到用户名: {self.username}")
                            return True
                                
                    except Exception as e:
                        logger.warning(f"检查页面 {check_url} 失败: {str(e)}")
                        continue
                
                logger.warning(f"❌ 未找到用户名 (尝试 {retry + 1}/{max_retries})")
                
                # 重试前等待
                if retry < max_retries - 1:
                    wait_time = random.uniform(5, 10)
                    logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                logger.error(f"登录状态检查异常: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(5)
        
        logger.error(f"❌ 在所有页面中都未找到用户名: {self.username}")
        return False

    def click_topic(self):
        """浏览主题 - 8-10个主题，模拟更真实的人类行为"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题...")
        
        try:
            # 访问最新页面
            self.driver.get(self.site_config['latest_url'])
            time.sleep(random.uniform(3, 5))
            
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(2, 3))

            # 查找主题元素
            topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
            if not topic_elements:
                logger.error("❌ 没有找到主题列表")
                return 0

            # 随机选择8-10个主题浏览
            browse_count = min(random.randint(8, 10), len(topic_elements))
            selected_topics = random.sample(topic_elements, browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题，随机浏览 {browse_count} 个")

            for i, topic in enumerate(selected_topics):
                try:
                    # 第3个主题浏览前进行用户名检测
                    if i == 2:  # 第3个主题（索引从0开始）
                        logger.info("===== 第3个主题前进行用户名检测 =====")
                        # 在当前页面检查用户名，不跳转页面
                        page_content = self.driver.page_source
                        if self.username.lower() in page_content.lower():
                            logger.success(f"✅ 在第3个主题前找到用户名: {self.username}")
                        else:
                            logger.warning("⚠️ 第3个主题前未找到用户名，尝试重新登录...")
                            # 重新登录并验证
                            if self.ensure_logged_in():
                                logger.success("✅ 重新登录成功，继续浏览")
                                # 重新获取主题元素，因为页面可能已刷新
                                topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                                if not topic_elements:
                                    logger.error("❌ 重新登录后未找到主题列表")
                                    return success_count
                                
                                # 重新选择剩余的主题
                                remaining_topics = topic_elements[i:]
                                if not remaining_topics:
                                    logger.warning("⚠️ 重新登录后没有剩余主题可浏览")
                                    return success_count
                                
                                # 更新selected_topics为剩余主题
                                selected_topics = remaining_topics
                                # 重置循环索引
                                i = 0
                                browse_count = len(selected_topics)
                                logger.info(f"🔄 重新开始浏览，剩余 {browse_count} 个主题")
                            else:
                                logger.error("❌ 重新登录失败，停止浏览")
                                return success_count
                    
                    topic_url = topic.get_attribute("href")
                    if not topic_url:
                        continue
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url

                    logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")
                    
                    # 在新标签页打开
                    original_window = self.driver.current_window_handle
                    self.driver.execute_script(f"window.open('{topic_url}', '_blank');")
                    
                    # 切换到新标签页
                    for handle in self.driver.window_handles:
                        if handle != original_window:
                            self.driver.switch_to.window(handle)
                            break
                    
                    # 模拟真实浏览行为
                    page_stay_time = random.uniform(25, 40)
                    logger.info(f"⏱️ 停留 {page_stay_time:.1f} 秒...")
                    
                    # 多次滚动模拟阅读
                    scroll_times = random.randint(5, 10)
                    for scroll_idx in range(scroll_times):
                        scroll_distance = random.randint(300, 900)
                        self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                        
                        # 模拟阅读时间
                        if random.random() < 0.4:
                            read_time = random.uniform(4, 8)
                            logger.debug(f"📖 模拟阅读 {read_time:.1f} 秒...")
                            time.sleep(read_time)
                        else:
                            time.sleep(random.uniform(1, 3))
                        
                        # 随机回滚模拟重新阅读
                        if random.random() < 0.3:
                            back_scroll = random.randint(100, 400)
                            self.driver.execute_script(f"window.scrollBy(0, -{back_scroll})")
                            time.sleep(random.uniform(1, 2))
                    
                    # 随机交互行为
                    if random.random() < 0.2:
                        try:
                            images = self.driver.find_elements(By.CSS_SELECTOR, "img")
                            if images:
                                img = random.choice(images)
                                actions = ActionChains(self.driver)
                                actions.move_to_element(img).click().perform()
                                time.sleep(random.uniform(2, 4))
                                logger.debug("🖱️ 随机点击图片")
                        except:
                            pass
                    
                    # 模拟随机暂停
                    if random.random() < 0.15:
                        pause_time = random.uniform(2, 5)
                        logger.debug(f"⏸️ 随机暂停 {pause_time:.1f} 秒")
                        time.sleep(pause_time)
                    
                    # 关闭标签页
                    self.driver.close()
                    self.driver.switch_to.window(original_window)
                    
                    success_count += 1
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(10, 18)
                        logger.info(f"⏳ 浏览间隔等待 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
                    try:
                        self.driver.switch_to.window(original_window)
                    except:
                        pass
                    continue

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            
            # 浏览后再次验证登录状态
            logger.info("===== 浏览主题后再次验证登录状态 =====")
            if not self.strict_username_login_check("浏览主题后"):
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
            time.sleep(random.uniform(3, 5))
            
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(random.uniform(2, 3))
            
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
                    color = 'green' if 'text-green' in str(col_class) else 'red' if 'text-red' in str(col_class) else 'black'
                    
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

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 1. 登录（优先使用Cookies缓存）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                self.generate_browser_state(False, 0)
                return False

            # 2. 浏览主题 (8-10个)
            browse_success_count = self.click_topic()
            if browse_success_count == 0:
                logger.error("❌ 浏览主题失败或登录状态丢失")
                self.generate_browser_state(False, 0)
                return False

            # 3. 获取用户统计信息 - 从connect_url获取
            self.get_user_stats()

            # 4. 打印连接信息
            self.print_connect_info()

            # 5. 生成状态文件
            self.generate_browser_state(True, browse_success_count)

            logger.success(f"✅ {self.site_name} 处理完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            self.generate_browser_state(False, 0)
            return False
            
        finally:
            try:
                self.driver.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (Selenium版)")
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
            wait_time = random.uniform(15, 25)
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
