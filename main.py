"""
cron: 0 * * * *
new Env("Linux.Do 多站点自动浏览")
"""
import os
import random
import time
import json
import functools
import sys
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from urllib.parse import urljoin

# ======================== 全局配置 ========================
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
SELECTOR = os.environ.get("SITE_SELECTOR", "all")
COOKIE_VALIDITY_DAYS = 7

# ======================== 站点配置 ========================
SITES = [
    {
        "name": "linux_do",
        "base_url": "https://linux.do",
        "login_url": "https://linux.do/login",
        "latest_topics_url": "https://linux.do/latest",
        "connect_url": "https://connect.linux.do",
        "username": os.environ.get("LINUXDO_USERNAME"),
        "password": os.environ.get("LINUXDO_PASSWORD")
    },
    {
        "name": "idcflare",
        "base_url": "https://idcflare.com", 
        "login_url": "https://idcflare.com/login",
        "latest_topics_url": "https://idcflare.com/latest",
        "connect_url": "https://connect.idcflare.com",
        "username": os.environ.get("IDCFLARE_USERNAME"),
        "password": os.environ.get("IDCFLARE_PASSWORD")
    }
]

# 站点选择过滤
if SELECTOR != "all":
    SITES = [s for s in SITES if s["name"] == SELECTOR]

# 检查账号密码配置
for site in SITES:
    if not (site["username"] and site["password"]):
        logger.error(f"❌ {site['name']} 账号或密码未配置")
        sys.exit(1)

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        cache_dir = Path("cache")
        cache_dir.mkdir(exist_ok=True)
        return str(cache_dir)
    
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

# ======================== 重试装饰器 ========================
def retry_decorator(retries=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                        raise
                    logger.warning(f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

# ======================== 直接操作浏览器类 ========================
class DirectBrowser:
    """直接操作浏览器，避免复杂的元素操作"""
    
    def __init__(self, site_config):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = site_config['username']
        self.password = site_config['password']
        
        # 初始化浏览器
        self._setup_browser()
        
    def _setup_browser(self):
        """配置浏览器设置"""
        co = (
            ChromiumOptions()
            .headless(HEADLESS)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-dev-shm-usage")
            .set_argument("--disable-gpu")
            .set_argument("--remote-debugging-port=9222")
        )
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # 注入反检测脚本
        self._inject_anti_detection()

    def _inject_anti_detection(self):
        """注入反检测脚本"""
        script = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """
        try:
            self.page.run_js(script)
            logger.info("✅ 反检测脚本已注入")
        except Exception as e:
            logger.warning(f"注入脚本失败: {str(e)}")

    def get_all_cookies(self):
        """获取所有cookies"""
        try:
            cookies = self.page.cookies()
            if cookies:
                logger.info(f"✅ 获取到 {len(cookies)} 个cookies")
                return cookies
            return None
        except Exception as e:
            logger.error(f"获取cookies时出错: {str(e)}")
            return None

    def save_cookies_to_cache(self):
        """保存cookies到缓存"""
        try:
            cookies = self.get_all_cookies()
            if cookies:
                success = CacheManager.save_cookies(cookies, self.site_name)
                if success:
                    logger.info("✅ Cookies缓存已保存")
                else:
                    logger.warning("⚠️ Cookies缓存保存失败")
            else:
                logger.warning("⚠️ 无法获取cookies")
            return True
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")
            return False

    def wait_for_cloudflare(self, timeout=30):
        """等待Cloudflare验证通过"""
        logger.info("🛡️ 等待Cloudflare验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                title = self.page.title
                if title and "Checking" not in title and "请稍候" not in title:
                    logger.success("✅ Cloudflare验证通过")
                    return True
                time.sleep(2)
            except Exception as e:
                logger.warning(f"检查页面标题时出错: {e}")
                time.sleep(2)
        
        logger.warning("⚠️ Cloudflare等待超时，继续执行")
        return True

    def check_login_status(self):
        """检查登录状态"""
        logger.info("🔍 检查登录状态...")
        
        try:
            # 检查页面中是否包含用户名
            page_html = self.page.html.lower()
            if self.username.lower() in page_html:
                logger.success(f"✅ 登录成功 - 找到用户名: {self.username}")
                return True
            
            # 检查是否有用户相关的元素
            user_selectors = [
                '.current-user',
                '.user-menu',
                '.header-user',
                '[data-current-user]'
            ]
            
            for selector in user_selectors:
                try:
                    if self.page(selector, timeout=2):
                        logger.success(f"✅ 找到用户元素: {selector}")
                        return True
                except:
                    continue
            
            # 检查是否有登录按钮（反证未登录）
            login_selectors = ['.login-button', '#login-button', 'a[href*="/login"]']
            for selector in login_selectors:
                try:
                    if self.page(selector, timeout=2):
                        logger.error(f"❌ 检测到登录按钮: {selector}")
                        return False
                except:
                    continue
            
            logger.warning("⚠️ 无法确定登录状态")
            return False
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return False

    @retry_decorator(retries=2, delay=3)
    def attempt_login_with_cookies(self):
        """尝试使用缓存的cookies登录"""
        logger.info(f"🔐 尝试使用缓存cookies登录 {self.site_name}")
        
        cached_cookies = CacheManager.load_cookies(self.site_name)
        if not cached_cookies:
            logger.warning("❌ 没有可用的缓存cookies")
            return False
        
        try:
            self.page.get(self.site_config['base_url'])
            time.sleep(3)
            
            # 设置cookies
            for cookie in cached_cookies:
                self.page.set.cookie(cookie)
            
            # 验证登录状态
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
            
            if self.check_login_status():
                logger.success("🎉 缓存cookies登录成功")
                return True
            else:
                logger.warning("🔄 缓存cookies失效，需要重新登录")
                return False
                
        except Exception as e:
            logger.error(f"缓存登录失败: {str(e)}")
            return False

    def perform_full_login(self):
        """执行完整登录流程 - 使用JavaScript直接操作"""
        logger.info("🔐 开始完整登录流程...")
        
        try:
            # 导航到登录页面
            self.page.get(self.site_config['login_url'])
            time.sleep(5)
            
            # 等待Cloudflare
            self.wait_for_cloudflare()
            
            # 重新注入脚本
            self._inject_anti_detection()
            
            # 使用JavaScript直接查找并填写表单
            if not self._fill_form_with_js():
                return False
            
            # 提交登录
            if not self._submit_login_with_js():
                return False
            
            # 等待登录完成
            time.sleep(5)
            
            # 验证登录成功
            if self.check_login_status():
                logger.success("✅ 登录成功")
                
                # 保存cookies
                self.save_cookies_to_cache()
                return True
            else:
                logger.error("❌ 登录验证失败")
                # 截图调试
                self.page.get_screenshot(f"{self.site_name}_login_failed.png")
                return False
                
        except Exception as e:
            logger.error(f"登录流程异常: {e}")
            self.page.get_screenshot(f"{self.site_name}_login_error.png")
            return False

    def _fill_form_with_js(self):
        """使用JavaScript直接填写表单"""
        logger.info("🔄 使用JavaScript填写登录表单...")
        
        try:
            # 首先尝试找到所有可能的输入框
            username_found = False
            password_found = False
            
            # 用户名输入框选择器
            username_selectors = [
                '#user', '#username', 'input[name="username"]', 'input[name="user"]',
                'input[type="text"]', 'input[placeholder*="user"]', 'input[placeholder*="name"]'
            ]
            
            # 密码输入框选择器  
            password_selectors = [
                '#password', 'input[type="password"]', 'input[name="password"]',
                'input[placeholder*="password"]', 'input[placeholder*="密码"]'
            ]
            
            # 使用JavaScript直接设置值
            js_script = """
            // 查找用户名输入框
            var usernameSelectors = %s;
            var usernameField = null;
            for (var i = 0; i < usernameSelectors.length; i++) {
                var field = document.querySelector(usernameSelectors[i]);
                if (field && (field.type === 'text' || field.type === 'email' || !field.type)) {
                    usernameField = field;
                    break;
                }
            }
            
            // 查找密码输入框
            var passwordSelectors = %s;
            var passwordField = null;
            for (var i = 0; i < passwordSelectors.length; i++) {
                var field = document.querySelector(passwordSelectors[i]);
                if (field && field.type === 'password') {
                    passwordField = field;
                    break;
                }
            }
            
            // 设置值
            if (usernameField) {
                usernameField.value = '%s';
                usernameField.dispatchEvent(new Event('input', {bubbles: true}));
                usernameField.dispatchEvent(new Event('change', {bubbles: true}));
            }
            
            if (passwordField) {
                passwordField.value = '%s';
                passwordField.dispatchEvent(new Event('input', {bubbles: true}));
                passwordField.dispatchEvent(new Event('change', {bubbles: true}));
            }
            
            // 返回结果
            return {
                usernameFound: !!usernameField,
                passwordFound: !!passwordField,
                usernameSelector: usernameField ? usernameSelectors.find(s => document.querySelector(s) === usernameField) : null,
                passwordSelector: passwordField ? passwordSelectors.find(s => document.querySelector(s) === passwordField) : null
            };
            """ % (username_selectors, password_selectors, self.username, self.password)
            
            result = self.page.run_js(js_script)
            
            if result:
                if result.get('usernameFound'):
                    logger.info(f"✅ 找到用户名输入框: {result.get('usernameSelector')}")
                    username_found = True
                else:
                    logger.error("❌ 未找到用户名输入框")
                
                if result.get('passwordFound'):
                    logger.info(f"✅ 找到密码输入框: {result.get('passwordSelector')}")
                    password_found = True
                else:
                    logger.error("❌ 未找到密码输入框")
                
                return username_found and password_found
            else:
                logger.error("❌ JavaScript执行失败")
                return False
                
        except Exception as e:
            logger.error(f"JavaScript填写表单失败: {e}")
            return False

    def _submit_login_with_js(self):
        """使用JavaScript提交登录表单"""
        logger.info("🔄 使用JavaScript提交登录...")
        
        try:
            # 查找并点击登录按钮
            login_selectors = [
                '#login-button', '.login-button', 'button[type="submit"]', 
                'input[type="submit"]', 'button:contains("登录")', 
                'button:contains("Sign in")', 'button:contains("Log in")'
            ]
            
            js_script = """
            var loginSelectors = %s;
            var loginButton = null;
            
            for (var i = 0; i < loginSelectors.length; i++) {
                if (loginSelectors[i].includes('contains')) {
                    // 处理文本包含选择器
                    var text = loginSelectors[i].split('"')[1];
                    var buttons = document.querySelectorAll('button');
                    for (var j = 0; j < buttons.length; j++) {
                        if (buttons[j].textContent.includes(text)) {
                            loginButton = buttons[j];
                            break;
                        }
                    }
                } else {
                    loginButton = document.querySelector(loginSelectors[i]);
                }
                if (loginButton) break;
            }
            
            if (loginButton) {
                loginButton.click();
                return {success: true, selector: loginSelectors[i]};
            } else {
                // 如果找不到按钮，尝试提交表单
                var forms = document.querySelectorAll('form');
                for (var k = 0; k < forms.length; k++) {
                    if (forms[k].querySelector('input[type="password"]')) {
                        forms[k].submit();
                        return {success: true, method: 'form_submit'};
                    }
                }
                return {success: false};
            }
            """ % login_selectors
            
            result = self.page.run_js(js_script)
            
            if result and result.get('success'):
                logger.info(f"✅ 登录提交成功 - {result.get('selector', result.get('method', '未知'))}")
                return True
            else:
                logger.error("❌ 找不到登录按钮或表单")
                return False
                
        except Exception as e:
            logger.error(f"JavaScript提交登录失败: {e}")
            return False

    def browse_topics(self):
        """浏览主题"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return
        
        logger.info("🌐 开始浏览主题")
        
        try:
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            # 使用JavaScript获取主题链接
            js_script = """
            var links = Array.from(document.querySelectorAll('.title.raw-link.raw-topic-link'));
            return links.slice(0, 10).map(link => link.href);
            """
            
            theme_urls = self.page.run_js(js_script)
            
            if not theme_urls or len(theme_urls) == 0:
                logger.warning("📭 未找到主题链接")
                return
            
            logger.info(f"🔗 找到 {len(theme_urls)} 个主题链接")
            
            # 随机选择几个主题浏览
            selected_urls = random.sample(theme_urls, min(3, len(theme_urls)))
            
            for i, url in enumerate(selected_urls, 1):
                try:
                    logger.info(f"📖 浏览第{i}/{len(selected_urls)}个主题")
                    self._browse_single_theme(url)
                    
                    if i < len(selected_urls):
                        time.sleep(random.uniform(2, 5))
                except Exception as e:
                    logger.warning(f"浏览主题 {i} 失败: {e}")
                    continue
            
            logger.success("✅ 主题浏览完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {e}")

    def _browse_single_theme(self, url):
        """浏览单个主题"""
        tab = self.browser.new_tab()
        try:
            tab.get(url)
            time.sleep(random.uniform(3, 6))
            
            # 模拟阅读行为
            read_time = random.randint(5, 10)
            start_time = time.time()
            
            while time.time() - start_time < read_time:
                # 随机滚动
                scroll_distance = random.randint(200, 500)
                tab.run_js(f"window.scrollBy(0, {scroll_distance})")
                time.sleep(random.uniform(1, 2))
            
        finally:
            tab.close()

    def get_connect_info(self):
        """获取连接信息"""
        try:
            logger.info(f"📊 获取 {self.site_name} 的连接信息")
            self.page.get(self.site_config['connect_url'])
            time.sleep(3)
            
            # 使用JavaScript获取表格数据
            js_script = """
            var rows = [];
            var tables = document.querySelectorAll('table');
            
            tables.forEach(table => {
                var tableRows = table.querySelectorAll('tr');
                for (var i = 1; i < tableRows.length; i++) {
                    var cells = tableRows[i].querySelectorAll('td');
                    if (cells.length >= 3) {
                        var rowData = Array.from(cells).slice(0, 3).map(cell => cell.textContent.trim());
                        rows.push(rowData);
                    }
                }
            });
            
            return rows;
            """
            
            rows = self.page.run_js(js_script)
            
            if rows and len(rows) > 0:
                logger.info("📋 连接信息表格:")
                print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("-" * 50)
            else:
                logger.info("📭 未找到连接信息表格")
                
        except Exception as e:
            logger.warning(f"⚠️ 获取连接信息失败: {e}")

    def run(self):
        """主运行流程"""
        logger.info(f"🎬 开始处理 {self.site_name}")
        
        try:
            # 1. 尝试使用缓存cookies登录
            if CacheManager.cookies_exist(self.site_name):
                if self.attempt_login_with_cookies():
                    logger.info("✅ 缓存登录成功")
                else:
                    # 缓存失效，执行完整登录
                    logger.info("🔄 缓存登录失败，执行完整登录")
                    if not self.perform_full_login():
                        raise Exception("完整登录失败")
            else:
                # 无缓存，执行完整登录
                logger.info("🔄 无缓存，执行完整登录")
                if not self.perform_full_login():
                    raise Exception("完整登录失败")
            
            # 2. 浏览主题
            self.browse_topics()
            
            # 3. 获取连接信息
            self.get_connect_info()
            
            logger.success(f"✅ {self.site_name} 处理完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 处理失败: {e}")
            # 截图保存错误信息
            try:
                self.page.get_screenshot(f"{self.site_name}_error.png")
                logger.info(f"📸 错误截图已保存: {self.site_name}_error.png")
            except:
                pass
            return False
        
        finally:
            # 关闭浏览器
            try:
                if self.browser:
                    self.browser.quit()
                    logger.info(f"🔚 关闭 {self.site_name} 浏览器")
            except Exception as e:
                logger.warning(f"⚠️ 关闭浏览器时出错: {e}")

# ======================== 主入口 ========================
def main():
    """主函数"""
    # 配置日志
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )
    logger.add(
        "run.log",
        rotation="10 MB",
        retention="7 days",
        encoding="utf8",
        level="INFO"
    )
    
    logger.info("=" * 60)
    logger.info("🚀 Linux.Do 多站点自动浏览脚本启动")
    logger.info("=" * 60)
    
    # 显示配置信息
    logger.info(f"📋 配置信息:")
    logger.info(f"   - 无头模式: {'是' if HEADLESS else '否'}")
    logger.info(f"   - 浏览功能: {'启用' if BROWSE_ENABLED else '禁用'}")
    logger.info(f"   - 站点选择: {SELECTOR}")
    logger.info(f"   - 处理站点: {[s['name'] for s in SITES]}")
    
    # 依次处理每个站点
    success_count = 0
    for site in SITES:
        try:
            browser = DirectBrowser(site)
            if browser.run():
                success_count += 1
        except Exception as e:
            logger.error(f"❌ 站点 {site['name']} 执行失败: {e}")
            continue
    
    # 总结报告
    logger.info("=" * 60)
    logger.info(f"📊 执行总结: {success_count}/{len(SITES)} 个站点成功")
    logger.info("=" * 60)
    
    if success_count == len(SITES):
        logger.success("🎉 所有站点处理完成！")
    else:
        logger.warning(f"⚠️ 有 {len(SITES) - success_count} 个站点处理失败")

if __name__ == "__main__":
    main()
