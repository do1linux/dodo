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

# 配置项
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
# 强制每次登录（已固定为True）
FORCE_LOGIN_EVERY_TIME = True

# DoH 服务器配置
DOH_SERVER = os.environ.get("DOH_SERVER", "https://ld.ddd.oaifree.com/query-dns")

# turnstilePatch 扩展路径（与GitHub Actions中创建的目录匹配）
TURNSTILE_PATCH_PATH = os.path.abspath("turnstilePatch")

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类 - 仅缓存Cloudflare相关Cookies"""
    
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
            
            # 验证文件保存结果
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
    def load_cookies(site_name):
        """加载cookies缓存并检查有效期（仅Cloudflare相关）"""
        cache_data = CacheManager.load_cache(f"cf_cookies_{site_name}.json")
        if not cache_data:
            return None
            
        cache_time_str = cache_data.get('cache_time')
        if cache_time_str:
            try:
                cache_time = datetime.fromisoformat(cache_time_str)
                # Cloudflare Cookies有效期设为7天
                if datetime.now() - cache_time > timedelta(days=7):
                    logger.warning("🕒 Cloudflare Cookies已过期")
                    return None
            except Exception as e:
                logger.warning(f"缓存时间解析失败: {str(e)}")
        return cache_data.get('cookies')

    @staticmethod
    def save_cookies(cookies, site_name):
        """保存cookies到缓存（仅保留Cloudflare相关）"""
        # 过滤仅保留Cloudflare相关Cookies（包含cf_前缀）
        cf_cookies = [cookie for cookie in cookies if 'cf_' in cookie['name'].lower()]
        cache_data = {
            'cookies': cf_cookies,
            'cache_time': datetime.now().isoformat(),
            'site': site_name
        }
        return CacheManager.save_cache(cache_data, f"cf_cookies_{site_name}.json")

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
        # 基础配置
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--lang=zh-CN,zh;q=0.9,en;q=0.8')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--enable-javascript')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        # 加载turnstilePatch扩展（关键配置）
        if os.path.exists(TURNSTILE_PATCH_PATH):
            chrome_options.add_argument(f'--load-extension={TURNSTILE_PATCH_PATH}')
            logger.info(f"✅ 已加载turnstilePatch扩展: {TURNSTILE_PATCH_PATH}")
        else:
            logger.warning(f"⚠️ 未找到turnstilePatch扩展目录: {TURNSTILE_PATCH_PATH}")
        
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

    def ensure_logged_in(self):
        """确保用户已登录 - 强制每次登录（忽略缓存）"""
        logger.info("🎯 强制执行登录流程（每次运行都重新登录）")
        return self.attempt_login()

    def attempt_login(self):
        """尝试登录"""
        logger.info("🔐 开始登录流程...")
        self.driver.get(self.site_config['login_url'])
        time.sleep(3)

        # 处理Cloudflare验证
        cf_success = CloudflareHandler.handle_cloudflare_with_doh(
            self.driver, 
            doh_server=DOH_SERVER,
            max_attempts=10,
            timeout=200
        )
        
        if not cf_success:
            logger.warning("⚠️ Cloudflare验证可能未完全通过，继续登录流程")

        # 填写登录信息
        try:
            time.sleep(5)
            
            # 记录当前页面状态
            current_url = self.driver.current_url
            page_title = self.driver.title
            logger.info(f"📄 当前页面: {page_title} | {current_url}")

            # 如果被重定向，回到登录页面
            if 'login' not in current_url:
                logger.info("🔄 被重定向，尝试回到登录页面")
                self.driver.get(self.site_config['login_url'])
                time.sleep(5)
                CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 查找表单元素
            username_selectors = ["#login-account-name", "#username", "input[name='username']"]
            password_selectors = ["#login-account-password", "#password", "input[name='password']"]
            login_button_selectors = ["#login-button", "button[type='submit']"]

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

            # 处理登录后的Cloudflare验证
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)

            # 检查登录是否成功
            login_success = self.enhanced_strict_check_login_status()
            if login_success:
                logger.success("✅ 登录成功")
                # 保存Cloudflare Cookies
                self.save_cookies_to_cache()
                return True
            else:
                logger.error("❌ 登录失败")
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def enhanced_strict_check_login_status(self):
        """增强的登录状态验证"""
        logger.info("🔍 验证登录状态...")
        try:
            if not self.driver.current_url.endswith('/latest'):
                self.driver.get(self.site_config['latest_url'])
                time.sleep(3)

            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            page_content = self.driver.page_source
            
            if self.username and self.username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                return True

            # 尝试访问个人资料页验证
            try:
                profile_url = f"{self.site_config['base_url']}/u/{self.username}"
                self.driver.get(profile_url)
                time.sleep(3)
                profile_content = self.driver.page_source
                
                if self.username and self.username.lower() in profile_content.lower():
                    logger.success(f"✅ 在个人资料页面找到用户名: {self.username}")
                    self.driver.get(self.site_config['latest_url'])
                    time.sleep(3)
                    return True
                else:
                    logger.warning("❌ 个人资料页面验证失败")
                    self.driver.get(self.site_config['latest_url'])
                    time.sleep(3)
            except Exception as e:
                logger.warning(f"访问个人资料页面失败: {str(e)}")
                self.driver.get(self.site_config['latest_url'])
                time.sleep(3)

            logger.error(f"❌ 未找到用户名: {self.username}，登录失败")
            return False
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

    def save_cookies_to_cache(self):
        """保存Cookies到缓存（仅保留Cloudflare相关）"""
        try:
            time.sleep(3)
            cookies = self.driver.get_cookies()
            if cookies:
                logger.info(f"🔍 获取到 {len(cookies)} 个Cookies")
                # 只保存Cloudflare相关Cookies（已在CacheManager中处理）
                success = CacheManager.save_cookies(cookies, self.site_name)
                if success:
                    logger.info("✅ Cloudflare Cookies已保存")
                else:
                    logger.warning("⚠️ Cookies保存失败")
            else:
                logger.warning("⚠️ 无法获取Cookies")
            return True
        except Exception as e:
            logger.error(f"保存Cookies失败: {str(e)}")
            return False

    def click_topic(self):
        """浏览主题"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题...")
        if not self.driver.current_url.endswith('/latest'):
            self.driver.get(self.site_config['latest_url'])
            time.sleep(5)

        try:
            topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title")
            if not topic_elements:
                logger.error("❌ 没有找到主题列表")
                return 0

            browse_count = min(random.randint(5, 8), len(topic_elements))
            selected_topics = random.sample(topic_elements, browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题，随机浏览 {browse_count} 个")

            for i, topic in enumerate(selected_topics):
                topic_url = topic.get_attribute("href")
                if not topic_url:
                    continue
                if not topic_url.startswith('http'):
                    topic_url = self.site_config['base_url'] + topic_url

                logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")
                if self.click_one_topic(topic_url):
                    success_count += 1

                if i < browse_count - 1:
                    wait_time = random.uniform(5, 12)
                    time.sleep(wait_time)

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            return 0

    def click_one_topic(self, topic_url):
        """浏览单个主题"""
        original_window = self.driver.current_window_handle
        self.driver.execute_script(f"window.open('{topic_url}', '_blank');")
        for handle in self.driver.window_handles:
            if handle != original_window:
                self.driver.switch_to.window(handle)
                break
        
        try:
            time.sleep(3)
            # 模拟真实滚动浏览
            self.browse_post()
            self.driver.close()
            self.driver.switch_to.window(original_window)
            return True
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            try:
                self.driver.close()
                self.driver.switch_to.window(original_window)
            except:
                pass
            return False

    def browse_post(self):
        """模拟真实用户滚动行为"""
        for i in range(8):
            scroll_distance = random.randint(400, 800)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
            
            if random.random() < 0.03:
                break
                
            at_bottom = self.driver.execute_script(
                "return window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            if at_bottom:
                break
                
            wait_time = random.uniform(2, 4)
            time.sleep(wait_time)

    def print_connect_info(self):
        """打印连接信息（修复表格查找+添加Cloudflare处理+tabulate兼容）"""
        logger.info("🔗 获取连接信息")
        try:
            self.driver.get(self.site_config['connect_url'])
            time.sleep(5)
        
            # 关键：处理connect页面的Cloudflare验证（之前漏掉了）
            CloudflareHandler.handle_cloudflare_with_doh(self.driver)
            time.sleep(8)

            # 扩展表格选择器，提高查找成功率
            table_selectors = [
                "table",
                ".table",
                "table.table",
                ".topic-list",
                ".container table",
                ".wrap table",
                "#content table",
                ".post-body table",
                "div.table-responsive table"
            ]
        
            table = None
            for selector in table_selectors:
                try:
                    table = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if table.is_displayed():
                        logger.info(f"✅ 找到连接信息表格: {selector}")
                        break
                except NoSuchElementException:
                    continue
        
            if not table:
                logger.warning("⚠️ 无法找到连接信息表格")
                # 保存页面源码用于调试
                with open(f"connect_debug_{self.site_name}.html", "w", encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                logger.info(f"📄 已保存页面源码到 connect_debug_{self.site_name}.html")
                return

            rows = table.find_elements(By.TAG_NAME, "tr")
            info = []

            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                # 兼容<th>标签（有些表格表头用th，内容用td）
                if len(cells) < 3:
                    cells = row.find_elements(By.TAG_NAME, "th")
            
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    # 过滤空行（避免无效数据）
                    if project and current:
                        info.append([project, current, requirement])

            if info:
                print("\n" + "="*50)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*50)
                # 兼容tabulate库，确保格式正常
                try:
                    from tabulate import tabulate
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                except ImportError:
                    # 降级方案：如果没有tabulate，用原始格式打印
                    print(f"{'项目':<20} {'当前':<15} {'要求':<15}")
                    print("-" * 50)
                    for item in info:
                        print(f"{item[0]:<20} {item[1]:<15} {item[2]:<15}")
                print("="*50 + "\n")
            else:
                logger.warning("⚠️ 表格中未找到有效连接信息")
                # 保存页面源码用于调试
                with open(f"connect_empty_{self.site_name}.html", "w", encoding='utf-8') as f:
                    f.write(self.driver.page_source)

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
            # 保存错误页面源码，方便排查
            with open(f"connect_error_{self.site_name}.html", "w", encoding='utf-8') as f:
                f.write(self.driver.page_source)

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")

            # 1. 强制登录（忽略缓存）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                self.generate_browser_state(False, 0)
                return False

            # 2. 浏览主题
            browse_success_count = self.click_topic()

            # 3. 获取连接信息
            self.print_connect_info()

            # 4. 生成状态文件
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
    def handle_cloudflare_with_doh(driver, doh_server=DOH_SERVER, max_attempts=12, timeout=240):
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
                
                # 检查验证状态
                if page_title and "just a moment" not in page_title and "checking" not in page_title and "please wait" not in page_title:
                    if any(x in current_url for x in ['/latest', '/login', 'connect.']):
                        logger.success("✅ Cloudflare验证通过")
                        return True
                    
                    page_source = driver.page_source.lower()
                    if len(page_source) > 1000:
                        logger.success("✅ 页面正常加载，验证通过")
                        return True

                # 动态等待时间
                base_wait = 5
                if attempt > 5:
                    base_wait = 10
                if attempt > 8:
                    base_wait = 15
                    
                wait_time = random.uniform(base_wait, base_wait + 5)
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
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)

        # 最终检查
        try:
            final_url = driver.current_url
            final_title = driver.title.lower() if driver.title else ""
            
            if "just a moment" in final_title or "checking" in final_title:
                logger.warning("⚠️ 验证未通过，强制继续")
                if "linux.do" in final_url:
                    driver.get("https://linux.do/login")
                elif "idcflare.com" in final_url:
                    driver.get("https://idcflare.com/login")
                time.sleep(5)
                return True
            else:
                logger.success("✅ 最终检查通过")
                return True
                
        except Exception as e:
            logger.warning(f"⚠️ 最终检查异常: {str(e)}")
            return True

    @staticmethod
    def handle_cloudflare(driver, max_attempts=8, timeout=180):
        """兼容旧接口"""
        return CloudflareHandler.handle_cloudflare_with_doh(
            driver, 
            doh_server=DOH_SERVER,
            max_attempts=max_attempts, 
            timeout=timeout
        )

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (Selenium版)")
    os.environ.pop("DISPLAY", None)
    success_sites = []
    failed_sites = []

    # 处理站点选择（支持GitHub Actions的输入参数）
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

        if site_config != target_sites[-1]:
            wait_time = random.uniform(10, 30)
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


