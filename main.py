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
        'user_url': 'https://linux.do/u',
        # 必须登录才能访问的测试链接
        'private_topic_url': 'https://linux.do/t/topic/1164438'
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        # 需要为idcflare也设置一个私有主题链接
        'private_topic_url': 'https://idcflare.com/t/topic/1'  # 需要替换为实际的私有主题
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

# ======================== 改进的Cloudflare处理器 ========================
class ImprovedCloudflareHandler:
    @staticmethod
    def wait_for_cloudflare(driver, timeout=30):
        """等待Cloudflare验证通过"""
        start_time = time.time()
        logger.info("🛡️ 等待Cloudflare验证...")
        
        while time.time() - start_time < timeout:
            try:
                current_url = driver.current_url
                page_title = driver.title.lower() if driver.title else ""
                page_source = driver.page_source.lower() if driver.page_source else ""
                
                # 检查是否还在Cloudflare验证页面
                cloudflare_indicators = ["just a moment", "checking", "please wait", "ddos protection"]
                is_cloudflare_page = any(indicator in page_title for indicator in cloudflare_indicators) or any(indicator in page_source for indicator in cloudflare_indicators)
                
                if not is_cloudflare_page:
                    logger.success("✅ Cloudflare验证通过")
                    return True
                
                # 显示等待进度
                elapsed = time.time() - start_time
                remaining = timeout - elapsed
                logger.info(f"⏳ 等待Cloudflare验证... ({elapsed:.0f}/{timeout}秒)")
                
                time.sleep(3)
                
                # 每10秒刷新一次
                if int(elapsed) % 10 == 0:
                    try:
                        driver.refresh()
                        logger.info("🔄 刷新页面")
                        time.sleep(2)
                    except:
                        pass
                        
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(3)

        logger.warning(f"⚠️ Cloudflare验证超时 ({timeout}秒)")
        return False

# ======================== 改进的浏览器类 ========================
class ImprovedLinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.driver = None
        self.session_id = FastCacheManager.generate_session_id(self.site_name, self.username)
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器"""
        chrome_options = Options()
        
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
        
        # 反检测配置
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

    def verify_login_status(self, max_retries=2):
        """核心登录状态验证 - 使用私有主题链接作为主要标准"""
        logger.info("🔍 验证登录状态...")
        
        for retry in range(max_retries):
            try:
                # 主要方案：访问必须登录才能访问的私有主题
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题: {private_url}")
                self.driver.get(private_url)
                time.sleep(3)
                
                # 等待Cloudflare验证
                ImprovedCloudflareHandler.wait_for_cloudflare(self.driver, 20)
                time.sleep(2)
                
                # 获取页面内容
                page_content = self.driver.page_source
                current_url = self.driver.current_url
                
                # 检查是否能够访问私有主题
                error_indicators = [
                    "糟糕！该页面不存在或者是一个不公开页面。",
                    "Oops! This page doesn't exist or is not a public page.",
                    "page doesn't exist",
                    "not a public page"
                ]
                
                # 如果页面中没有错误提示，说明登录成功
                has_error = any(indicator in page_content for indicator in error_indicators)
                
                if not has_error:
                    logger.success("✅ 私有主题访问成功 - 登录状态有效")
                    return True
                else:
                    logger.warning(f"❌ 私有主题访问失败 - 登录状态无效 (尝试 {retry + 1}/{max_retries})")
                    
                    # 备用方案：检查用户主页
                    logger.info("🔄 尝试备用验证方案...")
                    if self.verify_username_backup():
                        logger.success("✅ 备用验证成功 - 登录状态有效")
                        return True
                    
            except Exception as e:
                logger.error(f"登录状态验证异常: {str(e)}")
            
            # 如果不是最后一次尝试，等待后重试
            if retry < max_retries - 1:
                wait_time = random.uniform(3, 5)
                logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                time.sleep(wait_time)
        
        logger.error("❌ 所有登录状态验证方法均失败")
        return False

    def verify_username_backup(self):
        """备用用户名验证方案"""
        try:
            # 访问用户主页作为备用验证
            user_url = f"{self.site_config['user_url']}/{self.username}"
            logger.info(f"📍 备用验证: 访问用户主页")
            self.driver.get(user_url)
            time.sleep(2)
            
            ImprovedCloudflareHandler.wait_for_cloudflare(self.driver, 15)
            time.sleep(1)
            
            page_content = self.driver.page_source
            return self.username.lower() in page_content.lower()
                
        except Exception as e:
            logger.debug(f"备用验证异常: {str(e)}")
            return False

    def ensure_logged_in_fast(self):
        """确保登录"""
        # 尝试恢复状态
        if not FORCE_LOGIN_EVERY_TIME and self.load_state():
            if self.verify_login_status():
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

            # 等待Cloudflare验证
            ImprovedCloudflareHandler.wait_for_cloudflare(self.driver, 30)
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

            # 核心验证：使用私有主题链接检查登录状态
            if self.verify_login_status():
                logger.info("✅ 登录成功")
                self.save_state(True, 0)
                return True
            else:
                logger.error("❌ 登录失败 - 私有主题验证未通过")
                return False

        except Exception as e:
            logger.error(f"登录失败: {str(e)}")
            return False

    def enhanced_browse_topics(self):
        """增强的浏览主题 - 确保被记录"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0

        try:
            self.driver.get(self.site_config['latest_url'])
            time.sleep(3)
            ImprovedCloudflareHandler.wait_for_cloudflare(self.driver, 20)

            # 查找主题
            topic_elements = []
            for selector in [".title", "a.title", "tr.topic-list-item a"]:
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

            # 浏览4-5个主题，每个主题有足够的停留时间
            browse_count = min(random.randint(4, 5), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0

            logger.info(f"发现 {len(topic_elements)} 个主题，增强浏览 {browse_count} 个")

            for i, idx in enumerate(selected_indices):
                try:
                    # 重新获取当前主题列表避免元素过时
                    self.driver.get(self.site_config['latest_url'])
                    time.sleep(2)
                    current_topic_elements = self.driver.find_elements(By.CSS_SELECTOR, ".title, a.title")
                    if not current_topic_elements or idx >= len(current_topic_elements):
                        continue

                    topic = current_topic_elements[idx]
                    topic_url = topic.get_attribute("href")
                    if not topic_url:
                        continue
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url

                    logger.info(f"📖 深度浏览第 {i+1}/{browse_count} 个主题")
                    
                    # 访问主题页面
                    self.driver.get(topic_url)
                    time.sleep(3)
                    
                    # 增强的阅读行为 - 确保被记录
                    self.enhanced_reading_behavior()
                    
                    # 返回主题列表
                    self.driver.back()
                    time.sleep(2)
                    
                    success_count += 1
                    
                    # 合理的间隔时间
                    if i < browse_count - 1:
                        wait_time = random.uniform(8, 12)
                        logger.info(f"⏳ 浏览间隔等待 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.debug(f"浏览主题失败: {str(e)}")
                    try:
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(2)
                    except:
                        pass
                    continue

            logger.info(f"✅ 浏览完成: {success_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            return 0

    def enhanced_reading_behavior(self, stay_time=25):
        """增强的阅读行为 - 确保被网站记录"""
        logger.info(f"📖 深度阅读行为，停留 {stay_time:.1f} 秒...")
        start_time = time.time()
        
        scroll_actions = 0
        read_sessions = 0
        
        while time.time() - start_time < stay_time:
            try:
                # 随机滚动距离
                scroll_distance = random.randint(300, 800)
                self.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                scroll_actions += 1
                
                # 模拟阅读停顿
                if random.random() < 0.6:  # 60%的概率深度阅读
                    read_time = random.uniform(4, 8)
                    logger.debug(f"📚 深度阅读 {read_time:.1f} 秒...")
                    time.sleep(read_time)
                    read_sessions += 1
                else:
                    time.sleep(random.uniform(1, 3))
                
                # 偶尔回滚模拟真实阅读
                if random.random() < 0.3:
                    back_scroll = random.randint(100, 400)
                    self.driver.execute_script(f"window.scrollBy(0, -{back_scroll})")
                    time.sleep(random.uniform(1, 2))
                
                # 随机暂停思考
                if random.random() < 0.2:
                    pause_time = random.uniform(2, 4)
                    logger.debug(f"⏸️ 思考暂停 {pause_time:.1f} 秒")
                    time.sleep(pause_time)
                    
            except Exception as e:
                logger.debug(f"阅读行为异常: {str(e)}")
                time.sleep(1)
        
        logger.debug(f"📊 深度阅读完成: {scroll_actions} 次滚动, {read_sessions} 次深度阅读")

    def get_connect_info_properly(self):
        """正确获取连接信息 - 确保通过Cloudflare验证"""
        logger.info("🔗 获取连接信息...")
        
        try:
            # 直接访问连接页面
            connect_url = self.site_config['connect_url']
            logger.info(f"📍 访问连接页面: {connect_url}")
            self.driver.get(connect_url)
            time.sleep(5)
            
            # 等待Cloudflare验证完成
            if not ImprovedCloudflareHandler.wait_for_cloudflare(self.driver, 30):
                logger.warning("⚠️ Cloudflare验证可能未完成，继续尝试...")
            
            # 检查当前页面是否是连接页面
            current_url = self.driver.current_url
            page_title = self.driver.title.lower() if self.driver.title else ""
            
            logger.info(f"📄 页面标题: {self.driver.title}")
            logger.info(f"🌐 当前URL: {current_url}")
            
            # 如果被重定向到其他页面，尝试重新访问
            if 'connect' not in current_url and 'just a moment' in page_title:
                logger.warning("⚠️ 被Cloudflare拦截，等待后重试...")
                time.sleep(10)
                self.driver.get(connect_url)
                ImprovedCloudflareHandler.wait_for_cloudflare(self.driver, 30)
            
            # 分析页面内容
            self.analyze_connect_page_content()
                
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def analyze_connect_page_content(self):
        """分析连接页面内容"""
        logger.info("🔍 分析连接页面内容...")
        
        try:
            # 获取完整的页面源码用于分析
            page_source = self.driver.page_source
            current_url = self.driver.current_url
            
            # 检查页面是否包含连接信息关键词
            connect_keywords = ['访问次数', '回复', '浏览', '已读', '访问天数', 'trust level', '统计', '进度']
            has_connect_info = any(keyword in page_source for keyword in connect_keywords)
            
            if not has_connect_info:
                logger.warning("⚠️ 页面不包含连接信息关键词")
                # 保存页面用于调试
                try:
                    with open(f"connect_debug_{self.site_name}.html", "w", encoding='utf-8') as f:
                        f.write(page_source)
                    logger.info(f"💾 已保存页面源码: connect_debug_{self.site_name}.html")
                except:
                    pass
                return
            
            # 查找所有可能的统计元素
            self.find_statistics_elements()
            
        except Exception as e:
            logger.error(f"分析页面内容失败: {str(e)}")

    def find_statistics_elements(self):
        """查找统计信息元素"""
        logger.info("🔍 查找统计信息元素...")
        
        # 尝试多种可能的统计信息容器
        stat_containers = [
            "table",
            ".user-stats",
            ".stats",
            ".progress-bar",
            ".trust-level",
            ".user-statistics",
            "[class*='stat']",
            "[class*='progress']"
        ]
        
        found_elements = []
        
        for selector in stat_containers:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    logger.info(f"✅ 找到 {len(elements)} 个 '{selector}' 元素")
                    for i, elem in enumerate(elements[:3]):  # 只检查前3个
                        elem_text = elem.text.strip()
                        if elem_text and len(elem_text) > 10:  # 只显示有内容的元素
                            logger.info(f"📋 {selector}[{i}]: {elem_text[:100]}...")
                            found_elements.append((selector, elem_text))
            except Exception as e:
                logger.debug(f"查找 '{selector}' 失败: {str(e)}")
        
        if found_elements:
            # 尝试提取表格数据
            self.extract_table_data()
        else:
            logger.warning("⚠️ 未找到明显的统计信息元素")

    def extract_table_data(self):
        """提取表格数据"""
        try:
            # 查找所有表格
            tables = self.driver.find_elements(By.TAG_NAME, "table")
            logger.info(f"📊 找到 {len(tables)} 个表格")
            
            for i, table in enumerate(tables):
                try:
                    table_text = table.text.strip()
                    if not table_text:
                        continue
                        
                    logger.info(f"📋 表格 {i+1} 内容:")
                    logger.info("-" * 50)
                    
                    # 提取表格行
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    table_data = []
                    
                    for row_idx, row in enumerate(rows):
                        try:
                            cells = row.find_elements(By.TAG_NAME, "td")
                            if not cells:
                                cells = row.find_elements(By.TAG_NAME, "th")
                            
                            if cells:
                                row_data = [cell.text.strip() for cell in cells]
                                table_data.append(row_data)
                                logger.info(f"行 {row_idx}: {row_data}")
                        except:
                            continue
                    
                    # 如果表格有数据，尝试格式化输出
                    if table_data and len(table_data) > 1:
                        self.format_table_output(table_data, i+1)
                        
                    logger.info("-" * 50)
                    
                except Exception as e:
                    logger.debug(f"解析表格 {i+1} 失败: {str(e)}")
                    
        except Exception as e:
            logger.error(f"提取表格数据失败: {str(e)}")

    def format_table_output(self, table_data, table_index):
        """格式化表格输出"""
        try:
            # 过滤空行
            table_data = [row for row in table_data if any(cell.strip() for cell in row)]
            
            if not table_data or len(table_data) < 2:
                return
                
            print(f"\n📊 {self.site_name.upper()} 连接信息 (表格 {table_index}):")
            print("=" * 70)
            
            # 尝试使用tabulate格式化输出
            try:
                from tabulate import tabulate
                headers = table_data[0] if any(cell for cell in table_data[0]) else []
                data = table_data[1:] if headers else table_data
                
                if headers and data:
                    print(tabulate(data, headers=headers, tablefmt="grid"))
                else:
                    # 如果没有明确的表头，直接输出所有行
                    for row in table_data:
                        print(" | ".join(f"{cell:<20}" for cell in row))
            except ImportError:
                # 如果没有tabulate，简单格式化
                for row in table_data:
                    print(" | ".join(f"{cell:<20}" for cell in row))
            
            print("=" * 70)
            logger.success(f"✅ 成功显示表格 {table_index} 的数据")
            
        except Exception as e:
            logger.debug(f"格式化表格输出失败: {str(e)}")

    def run_enhanced(self):
        """执行增强的自动化流程"""
        try:
            logger.info(f"🚀 开始处理: {self.site_name}")

            # 1. 登录（核心：私有主题验证）
            if not self.ensure_logged_in_fast():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False

            # 2. 增强浏览主题（确保被记录）
            browse_count = self.enhanced_browse_topics()
            if browse_count == 0:
                logger.warning(f"⚠️ {self.site_name} 浏览主题失败")

            # 3. 浏览后再次验证登录状态（使用私有主题）
            logger.info("🔍 浏览后验证登录状态...")
            if not self.verify_login_status(max_retries=1):
                logger.error("❌ 浏览后登录状态丢失")
                return False

            # 4. 正确获取连接信息
            self.get_connect_info_properly()

            # 5. 保存状态
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

# ======================== 增强主函数 ========================
def main_enhanced():
    """增强主函数"""
    logger.info("🚀 Linux.Do 增强自动化脚本启动")
    
    # 日志配置
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
            browser = ImprovedLinuxDoBrowser(site_config, credentials)
            success = browser.run_enhanced()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(10, 15)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个站点...")
            time.sleep(wait_time)

    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败: {', '.join(failed_sites) if failed_sites else '无'}")

    if success_sites:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main_enhanced()
