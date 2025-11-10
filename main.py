import os
import random
import time
import functools
import sys
import json
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

BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]

# ======================== 修复的缓存管理器 ========================
class CacheManager:
    """修复的缓存管理类"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        """获取缓存文件的完整路径"""
        return os.path.join(CacheManager.get_cache_directory(), file_name)

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
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_cookies(site_name):
        """加载cookies缓存并检查有效期"""
        # 修复：使用正确的文件名格式
        cache_data = CacheManager.load_cache(f"cf_cookies_{site_name}.json")
        if not cache_data:
            return None
            
        cache_time_str = cache_data.get('cache_time')
        if cache_time_str:
            try:
                cache_time = datetime.fromisoformat(cache_time_str)
                if datetime.now() - cache_time > timedelta(days=7):
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
        # 修复：使用正确的文件名格式
        return CacheManager.save_cache(cache_data, f"cf_cookies_{site_name}.json")

# ======================== 修复的主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        
        chrome_options = Options()
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--lang=zh-CN,zh;q=0.9,en;q=0.8')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception as e:
            logger.error(f"Chrome驱动初始化失败: {str(e)}")
            raise
            
        self.wait = WebDriverWait(self.driver, 20)

    def generate_browser_state(self, success=True, browse_count=0):
        """生成浏览器状态文件 - 新增方法"""
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
        """确保用户已登录 - 强制每次登录"""
        logger.info("🎯 开始登录流程")
        return self.attempt_login()  # 强制登录，不使用缓存

    def attempt_login(self):
        """尝试登录"""
        logger.info("🔐 尝试登录...")
        self.driver.get(self.site_config['login_url'])
        time.sleep(3)

        # 处理Cloudflare验证
        CloudflareHandler.handle_cloudflare(self.driver)

        # 填写登录信息
        try:
            time.sleep(2)
            
            username_selectors = ["#login-account-name", "#username", "input[name='username']"]
            password_selectors = ["#login-account-password", "#password", "input[name='password']"]
            login_button_selectors = ["#login-button", "button[type='submit']"]

            username_field = None
            password_field = None
            login_button = None

            for selector in username_selectors:
                try:
                    username_field = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if username_field:
                        break
                except:
                    continue

            for selector in password_selectors:
                try:
                    password_field = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if password_field:
                        break
                except:
                    continue

            for selector in login_button_selectors:
                try:
                    login_button = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if login_button:
                        break
                except:
                    continue

            if username_field and password_field and login_button:
                username_field.clear()
                for char in self.username:
                    username_field.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.3))
                
                password_field.clear()
                for char in self.password:
                    password_field.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.3))

                login_button.click()
                time.sleep(15)

                # 增强的严格检查登录是否成功
                login_success = self.enhanced_strict_check_login_status()
                if login_success:
                    logger.success("✅ 登录成功")
                    self.save_cookies_to_cache()
                    return True
                else:
                    logger.error("❌ 登录失败")
                    return False
            else:
                logger.error("❌ 找不到登录表单元素")
                return False
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def enhanced_strict_check_login_status(self):
        """增强的严格登录状态验证"""
        logger.info("🔍 增强严格验证登录状态...")
        try:
            if not self.driver.current_url.endswith('/latest'):
                self.driver.get(self.site_config['latest_url'])
                time.sleep(3)

            CloudflareHandler.handle_cloudflare(self.driver)
            page_content = self.driver.page_source
            
            if self.username and self.username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                return True

            logger.info("🔄 尝试访问用户个人资料页面验证...")
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

            logger.error(f"❌ 所有验证方法都失败，未找到用户名: {self.username}")
            return False
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

    def save_cookies_to_cache(self):
        """保存cookies到缓存 - 只保存Cloudflare cookies"""
        try:
            time.sleep(3)
            cookies = self.driver.get_cookies()
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
                logger.warning("⚠️ 无法获取cookies")
            return True
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")
            return False

    def click_topic(self):
        """点击浏览主题 - 返回成功数量"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        logger.info("🌐 开始浏览主题 - 模拟真实用户行为")
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

                if i < browse_count - 1:
                    wait_time = random.uniform(5, 12)
                    time.sleep(wait_time)

            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count  # 返回成功数量
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
            
            # 随机决定是否点赞 (0.5%概率)
            if random.random() < 0.005:
                self.click_like()

            # 真实用户浏览行为
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
        """浏览帖子内容 - 真实用户滚动行为模拟"""
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
        """修复的连接信息获取"""
        logger.info("🔗 获取连接信息")
        try:
            self.driver.get(self.site_config['connect_url'])
            time.sleep(5)
            CloudflareHandler.handle_cloudflare(self.driver)
            time.sleep(8)
            
            table_selectors = [
                "table",
                ".table", 
                "table.table",
                ".topic-list",
                ".container table",
                ".wrap table"
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
                logger.warning("⚠️ 无法找到连接信息表格")
                return

            rows = table_element.find_elements(By.TAG_NAME, "tr")
            info = []
            
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip() 
                    requirement = cells[2].text.strip()
                    if project and current:
                        info.append([project, current, requirement])

            if info:
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
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
            try:
                self.driver.quit()
            except:
                pass

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    @staticmethod
    def handle_cloudflare(driver, max_attempts=8, timeout=180):
        """处理Cloudflare验证"""
        start_time = time.time()
        logger.info("🛡️ 开始处理 Cloudflare验证")
        
        for attempt in range(max_attempts):
            try:
                page_title = driver.title
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                
                wait_time = random.uniform(8, 15)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时")
                    break
                    
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)
        
        try:
            page_title = driver.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                logger.success("✅ 最终验证: Cloudflare验证通过")
                return True
            else:
                logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
                return True
        except Exception:
            logger.warning("⚠️ 无法获取页面标题，继续后续流程")
            return True

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动 (Selenium版) - 真实用户行为模拟")
    os.environ.pop("DISPLAY", None)
    success_sites = []
    failed_sites = []

    for site_config in SITES:
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

        if site_config != SITES[-1]:
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
