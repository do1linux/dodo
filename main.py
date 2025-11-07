import os
import random
import time
import functools
import sys
import json
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

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

# 固定的 Windows User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理器，只处理 Cloudflare 验证相关的 cookies"""
    
    @staticmethod
    def get_cf_cookies_file(site_name):
        return f"cf_cookies_{site_name}.json"
    
    @staticmethod
    def load_cf_cookies(site_name):
        """加载 Cloudflare 验证相关的 cookies"""
        file_path = CacheManager.get_cf_cookies_file(site_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                logger.info(f"📦 加载了 {len(cookies)} 个 Cloudflare cookies for {site_name}")
                return cookies
            except Exception as e:
                logger.warning(f"加载 Cloudflare cookies 失败: {e}")
        return None
    
    @staticmethod
    def save_cf_cookies(site_name, cookies):
        """只保存 Cloudflare 验证相关的 cookies"""
        if not cookies:
            return False
            
        # 过滤只保留 Cloudflare 相关的 cookies
        cf_cookies = []
        cf_keywords = ['cf_', 'cloudflare', '__cf', '_cf', 'cf-bm', 'cf-cookie', 'cf_clearance']
        
        for cookie in cookies:
            cookie_name = cookie.get('name', '').lower()
            if any(keyword in cookie_name for keyword in cf_keywords):
                cf_cookies.append(cookie)
        
        if cf_cookies:
            file_path = CacheManager.get_cf_cookies_file(site_name)
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(cf_cookies, f, ensure_ascii=False, indent=2)
                logger.info(f"💾 保存了 {len(cf_cookies)} 个 Cloudflare cookies for {site_name}")
                return True
            except Exception as e:
                logger.error(f"保存 Cloudflare cookies 失败: {e}")
        
        return False

# ======================== 重试装饰器 ========================
def retry_decorator(retries=3, delay=2):
    """重试装饰器"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"❌ 函数 {func.__name__} 最终失败: {str(e)}")
                        raise
                    logger.warning(f"⚠️ 函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    time.sleep(delay)
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
        
        # 设置浏览器
        self.setup_browser()

    def setup_browser(self):
        """设置浏览器，只加载 Cloudflare cookies"""
        logger.info(f"🛠️ 为 {self.site_name} 设置浏览器")
        
        # 创建浏览器选项
        co = ChromiumOptions()
        co.headless(HEADLESS)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.incognito(True)
        
        # 设置 User-Agent
        co.set_user_agent(USER_AGENT)
        
        # 创建浏览器
        self.browser = Chromium(co)
        
        # 只加载 Cloudflare cookies（不加载登录状态）
        cf_cookies = CacheManager.load_cf_cookies(self.site_name)
        if cf_cookies:
            for cookie in cf_cookies:
                try:
                    self.browser.set_cookies(cookie)
                    logger.debug(f"设置 Cloudflare cookie: {cookie.get('name')}")
                except Exception as e:
                    logger.warning(f"设置 Cloudflare cookie 失败: {e}")
        
        # 创建新页面
        self.page = self.browser.new_tab()
        
        # 注入反检测脚本
        self.inject_stealth_script()
        
        logger.info(f"✅ 浏览器设置完成 for {self.site_name}")

    def inject_stealth_script(self):
        """注入反检测脚本"""
        stealth_script = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {isInstalled: false} };
        Object.defineProperty(document, 'hidden', { get: () => false });
        Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
        """
        self.page.run_js(stealth_script)

    def clear_all_cookies_except_cf(self):
        """清除所有cookies，除了Cloudflare相关的"""
        try:
            # 获取所有cookies
            all_cookies = self.browser.get_cookies()
            if not all_cookies:
                return True
                
            # 只保留Cloudflare相关的cookies
            cf_keywords = ['cf_', 'cloudflare', '__cf', '_cf', 'cf-bm', 'cf-cookie', 'cf_clearance']
            cookies_to_keep = []
            
            for cookie in all_cookies:
                cookie_name = cookie.get('name', '').lower()
                if any(keyword in cookie_name for keyword in cf_keywords):
                    cookies_to_keep.append(cookie)
            
            # 清除所有cookies
            self.browser.clear_cookies()
            
            # 重新设置Cloudflare cookies
            for cookie in cookies_to_keep:
                self.browser.set_cookies(cookie)
                
            logger.info(f"✅ 已清除非Cloudflare cookies，保留了 {len(cookies_to_keep)} 个Cloudflare cookies")
            return True
            
        except Exception as e:
            logger.error(f"❌ 清除cookies失败: {e}")
            return False

    def detect_login_elements_and_bot_protections(self):
        """检测登录页面元素和机器人验证"""
        logger.info("🔍 检测登录页面元素和验证...")
        
        elements_found = []
        bot_protections = []
        
        # 检测机器人验证
        turnstile_elements = self.page.eles('[data-sitekey], .cf-turnstile, iframe[src*="challenges.cloudflare.com"]')
        if turnstile_elements:
            bot_protections.append("Cloudflare Turnstile")
            logger.warning("🛡️ 检测到 Cloudflare Turnstile 验证")
        
        recaptcha_elements = self.page.eles('.g-recaptcha, iframe[src*="google.com/recaptcha"]')
        if recaptcha_elements:
            bot_protections.append("Google reCAPTCHA")
            logger.warning("🛡️ 检测到 Google reCAPTCHA 验证")
        
        # 检测登录表单元素
        username_fields = self.page.eles('@id=login-account-name, @name=username, input[type="text"]')
        if username_fields:
            elements_found.append("用户名输入框")
        
        password_fields = self.page.eles('@id=login-account-password, @name=password, input[type="password"]')
        if password_fields:
            elements_found.append("密码输入框")
        
        login_buttons = self.page.eles('@id=login-button, button[type="submit"]')
        if login_buttons:
            elements_found.append("登录按钮")
        
        # 输出检测结果
        if bot_protections:
            logger.warning(f"🤖 检测到机器人验证: {', '.join(bot_protections)}")
        else:
            logger.info("✅ 未检测到机器人验证")
        
        if elements_found:
            logger.info(f"✅ 检测到登录元素: {', '.join(elements_found)}")
        else:
            logger.error("❌ 未检测到登录表单元素")
        
        return len(username_fields) > 0 and len(password_fields) > 0

    def handle_turnstile_verification(self):
        """处理 Cloudflare Turnstile 验证"""
        logger.info("🛡️ 处理 Cloudflare Turnstile 验证...")
        
        max_attempts = 8
        for attempt in range(max_attempts):
            try:
                # 检查是否存在 Turnstile
                turnstile_elements = self.page.eles('[data-sitekey], .cf-turnstile, iframe[src*="challenges.cloudflare.com"]')
                if not turnstile_elements:
                    logger.info("✅ 未检测到 Turnstile 验证")
                    return True
                
                logger.info(f"🔄 检测到 Turnstile 验证，尝试处理 ({attempt + 1}/{max_attempts})")
                
                # 注入 JS 获取 token
                token = self.page.run_js("""
                    try {
                        if (typeof turnstile !== 'undefined') {
                            return turnstile.getResponse();
                        }
                        return null;
                    } catch(e) {
                        return null;
                    }
                """)
                
                if token:
                    logger.info(f"✅ 获取到 Turnstile token")
                    
                    # 设置到表单字段
                    cf_inputs = self.page.eles('@name=cf-turnstile-response')
                    if cf_inputs:
                        cf_inputs[0].input(token)
                        logger.info("✅ 已设置 cf-turnstile-response")
                        return True
                
                # 等待并重试
                time.sleep(3)
                
            except Exception as e:
                logger.warning(f"⚠️ 处理 Turnstile 时出错: {e}")
                time.sleep(3)
        
        logger.error("❌ 无法处理 Turnstile 验证")
        return False

    def strict_username_detection(self):
        """严格检测用户名，必须找到用户名才算登录成功"""
        logger.info("🔍 严格验证登录状态 - 查找用户名")
        
        # 方法1: 检查页面内容中的用户名
        page_content = self.page.html
        if self.username and self.username.lower() in page_content.lower():
            logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
            return True
        
        # 方法2: 检查用户菜单和头像
        user_selectors = [
            '#current-user',
            '.current-user', 
            '.user-menu',
            '[data-current-user]',
            '[class*="current-user"]'
        ]
        
        for selector in user_selectors:
            try:
                user_element = self.page.ele(selector, timeout=3)
                if user_element:
                    element_text = user_element.text.lower()
                    if self.username and self.username.lower() in element_text:
                        logger.success(f"✅ 在用户元素中找到用户名: {self.username} (选择器: {selector})")
                        return True
            except:
                continue
        
        # 方法3: 尝试访问用户个人资料页面
        try:
            profile_url = f"{self.site_config['base_url']}/u/{self.username}"
            current_tab = self.page.tab_id
            profile_tab = self.browser.new_tab()
            profile_tab.get(profile_url)
            time.sleep(3)
            
            profile_content = profile_tab.html
            if self.username and self.username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面找到用户名: {self.username}")
                profile_tab.close()
                # 切换回原来的标签页
                self.browser.to_tab(current_tab)
                return True
            else:
                profile_tab.close()
                self.browser.to_tab(current_tab)
        except Exception as e:
            logger.warning(f"访问个人资料页面失败: {e}")
        
        logger.error(f"❌ 未找到用户名: {self.username}，登录失败")
        return False

    def force_login(self):
        """强制登录 - 每次运行都重新登录"""
        logger.info("🔐 开始强制登录流程")
        
        # 清除所有非Cloudflare cookies
        self.clear_all_cookies_except_cf()
        
        # 访问登录页面
        self.page.get(self.site_config['login_url'])
        time.sleep(5)
        
        # 检测登录元素和机器人验证
        if not self.detect_login_elements_and_bot_protections():
            logger.error("❌ 登录页面元素检测失败")
            return False
        
        # 处理 Turnstile 验证
        if not self.handle_turnstile_verification():
            logger.warning("⚠️ Turnstile 验证处理可能失败，继续尝试登录")
        
        # 填写登录表单
        try:
            # 查找并填写用户名
            username_fields = self.page.eles('@id=login-account-name, @name=username, input[type="text"]')
            if not username_fields:
                logger.error("❌ 未找到用户名输入框")
                return False
            
            username_fields[0].input(self.username)
            logger.info("✅ 已输入用户名")
            time.sleep(1)
            
            # 查找并填写密码
            password_fields = self.page.eles('@id=login-account-password, @name=password, input[type="password"]')
            if not password_fields:
                logger.error("❌ 未找到密码输入框")
                return False
            
            password_fields[0].input(self.password)
            logger.info("✅ 已输入密码")
            time.sleep(1)
            
            # 点击登录按钮
            login_buttons = self.page.eles('@id=login-button, button[type="submit"]')
            if not login_buttons:
                logger.error("❌ 未找到登录按钮")
                return False
            
            login_buttons[0].click()
            logger.info("✅ 已点击登录按钮")
            
            # 等待登录完成
            time.sleep(10)
            
            # 严格验证登录成功 - 必须检测到用户名
            if self.strict_username_detection():
                logger.success("✅ 登录成功")
                
                # 只保存 Cloudflare cookies（不保存登录状态）
                all_cookies = self.browser.get_cookies()
                CacheManager.save_cf_cookies(self.site_name, all_cookies)
                
                return True
            else:
                logger.error("❌ 登录失败 - 未检测到用户名")
                return False
                
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {e}")
            return False

    @retry_decorator()
    def click_one_topic(self, topic_url):
        """在单个主题中浏览 - 模拟真实用户行为"""
        logger.info(f"🔗 打开主题: {topic_url}")
        
        # 在新标签页中打开主题
        new_page = self.browser.new_tab()
        try:
            new_page.get(topic_url)
            time.sleep(3)
            
            # 随机点赞 (1% 概率)
            if random.random() < 0.01:
                self.click_like(new_page)
            
            # 浏览帖子内容
            self.browse_post(new_page)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {e}")
            return False
        finally:
            new_page.close()

    def click_like(self, page):
        """点赞帖子"""
        try:
            like_buttons = page.eles('.discourse-reactions-reaction-button, .like-button, [class*="like"]')
            if like_buttons:
                like_buttons[0].click()
                logger.info("❤️ 点赞成功")
                time.sleep(random.uniform(1, 2))
            else:
                logger.info("ℹ️ 未找到点赞按钮或已点赞")
        except Exception as e:
            logger.warning(f"⚠️ 点赞失败: {e}")

    def browse_post(self, page):
        """浏览帖子内容 - 模拟真实用户滚动行为"""
        logger.info("👀 开始浏览帖子内容")
        
        prev_url = None
        scroll_attempts = 0
        max_scrolls = random.randint(8, 15)
        
        while scroll_attempts < max_scrolls:
            try:
                # 随机滚动距离
                scroll_distance = random.randint(550, 650)
                page.run_js(f"window.scrollBy(0, {scroll_distance})")
                scroll_attempts += 1
                
                # 检查是否到达底部
                at_bottom = page.run_js(
                    "return (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 10"
                )
                
                current_url = page.url
                if current_url != prev_url:
                    prev_url = current_url
                
                # 随机退出条件 (3% 概率)
                if random.random() < 0.03:
                    logger.info("🎲 随机退出浏览")
                    break
                
                if at_bottom:
                    logger.info("⬇️ 已到达页面底部，退出浏览")
                    break
                
                # 动态随机等待
                wait_time = random.uniform(2, 5)
                time.sleep(wait_time)
                
            except Exception as e:
                logger.error(f"❌ 浏览帖子时出错: {e}")
                break
        
        logger.info("✅ 帖子浏览完成")

    def click_topic(self):
        """点击浏览主题 - 定位主题列表 → 随机筛选主题 → 打开主题页 → 模拟滚动浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return True

        logger.info("🌐 开始浏览主题流程")
        
        # 确保在最新主题页面
        self.page.get(self.site_config['latest_url'])
        time.sleep(5)
        
        try:
            # 定位主题列表
            topic_list = self.page.ele("@id=list-area").eles(".:title")
            if not topic_list:
                logger.warning("⚠️ 未找到主题列表，尝试备用选择器")
                topic_list = self.page.eles('.title, .topic-title, a[href*="/t/"]')
            
            if not topic_list:
                logger.error("❌ 没有找到主题列表")
                return False
            
            # 随机筛选主题 (选择5-10个)
            browse_count = min(random.randint(5, 10), len(topic_list))
            selected_topics = random.sample(topic_list, browse_count)
            success_count = 0

            logger.info(f"📚 发现 {len(topic_list)} 个主题，随机选择 {browse_count} 个进行浏览")
            
            for i, topic in enumerate(selected_topics):
                try:
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                    
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题")
                    
                    if self.click_one_topic(topic_url):
                        success_count += 1
                    
                    # 随机等待 between topics
                    if i < browse_count - 1:
                        wait_time = random.uniform(5, 15)
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 处理主题失败: {e}")
                    continue
            
            logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count > 0
            
        except Exception as e:
            logger.error(f"❌ 获取主题列表失败: {e}")
            return False

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息")
        
        # 使用新标签页打开连接信息页面
        connect_page = self.browser.new_tab()
        try:
            connect_page.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 解析表格数据
            table = connect_page.ele('tag:table')
            if table:
                rows = table.eles('tag:tr')
                info = []
                
                for row in rows[1:]:  # 跳过表头
                    cells = row.eles('tag:td')
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        info.append([project, current, requirement])
                
                if info:
                    print(f"\n{'='*50}")
                    print(f"🔗 {self.site_name.upper()} 连接信息")
                    print(f"{'='*50}")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                    print(f"{'='*50}\n")
                else:
                    logger.warning("⚠️ 未找到连接信息表格数据")
            else:
                logger.warning("⚠️ 未找到连接信息表格")
                
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {e}")
        finally:
            connect_page.close()

    def run(self):
        """执行完整的自动化流程"""
        try:
            logger.info(f"🚀 开始执行 {self.site_name} 自动化")
            
            # 1. 强制登录（每次运行都重新登录）
            if not self.force_login():
                logger.error(f"❌ {self.site_name} 登录失败，终止执行")
                return False
            
            logger.info(f"✅ {self.site_name} 登录成功")
            
            # 2. 浏览主题（模拟真实用户行为，让网站收集浏览记录）
            if BROWSE_ENABLED:
                if not self.click_topic():
                    logger.warning(f"⚠️ {self.site_name} 浏览主题失败")
                else:
                    logger.info(f"✅ {self.site_name} 浏览主题完成")
            
            # 3. 打印连接信息
            self.print_connect_info()
            
            logger.info(f"✅ {self.site_name} 自动化执行完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 自动化执行失败: {e}")
            return False
        finally:
            # 关闭浏览器
            if self.browser:
                self.browser.quit()

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动")
    
    # 获取站点选择
    site_selector = os.environ.get('SITE_SELECTOR', 'all')
    
    # 修复站点选择逻辑
    if site_selector == 'all':
        sites_to_run = SITES  # 直接使用 SITES 配置
    else:
        # 查找匹配的站点配置
        sites_to_run = [site for site in SITES if site['name'] == site_selector]
        if not sites_to_run:
            logger.error(f"❌ 未知站点: {site_selector}")
            sites_to_run = []
    
    logger.info(f"🎯 选择的站点: {', '.join([site['name'] for site in sites_to_run])}")
    
    success_sites = []
    
    for site_config in sites_to_run:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})
        
        # 检查凭证
        if not credentials.get('username') or not credentials.get('password'):
            logger.error(f"❌ 跳过 {site_name} - 缺少环境变量")
            continue
        
        # 运行自动化
        browser = LinuxDoBrowser(site_config, credentials)
        if browser.run():
            success_sites.append(site_name)
        
        # 站点间等待
        if site_config != sites_to_run[-1]:
            wait_time = random.uniform(10, 30)
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个站点...")
            time.sleep(wait_time)
    
    # 输出总结
    logger.info(f"📊 自动化执行总结: 成功 {len(success_sites)}/{len(sites_to_run)} 个站点")
    if success_sites:
        logger.info(f"✅ 成功的站点: {', '.join(success_sites)}")
        logger.info("🎉 任务完成！")
        return 0
    else:
        logger.error("💥 所有站点执行失败")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
