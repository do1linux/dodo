import os
import random
import time
import functools
import sys
import json
from datetime import datetime, timedelta
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

# Cookie有效期设置（天）
COOKIE_VALIDITY_DAYS = 7

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类"""
    
    @staticmethod
    def get_cache_directory():
        """"获取缓存目录"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(current_dir, "cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            cache_dir = current_dir
        return cache_dir
    
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
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
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

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类"""
    
    @staticmethod
    def is_cf_cookie_valid(cookies):
        """检查Cloudflare cookie是否有效"""
        try:
            if not cookies:
                return False
                
            for cookie in cookies:
                if cookie.get('name') == 'cf_clearance':
                    expires = cookie.get('expires', 0)
                    # 检查cookie是否过期
                    if expires == -1 or expires > time.time():
                        return True
            return False
        except Exception:
            return False

    @staticmethod
    def handle_cloudflare(page, max_attempts=8, timeout=180):
        """处理Cloudflare验证"""
        start_time = time.time()
        logger.info("🛡️ 开始处理 Cloudflare验证")
        
        # 完整验证流程
        logger.info("🔄 开始完整Cloudflare验证流程")
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = page.title
                
                # 检查页面是否已经正常加载
                if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                
                # 等待验证
                wait_time = random.uniform(8, 15)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时")
                    break
                    
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)
        
        # 最终检查
        try:
            page_title = page.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                logger.success("✅ 最终验证: Cloudflare验证通过")
                return True
            else:
                logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
                return True
        except Exception:
            logger.warning("⚠️ 无法获取页面标题，继续后续流程")
            return True

# ======================== 重试装饰器 ========================
def retry_decorator(retries=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    time.sleep(2)
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
        self.login_attempts = 0
        self.max_login_attempts = 2
        
        # 浏览器配置
        platformIdentifier = "Windows NT 10.0; Win64; x64"

        co = (
            ChromiumOptions()
            .headless(HEADLESS)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-features=VizDisplayCompositor")
            .set_argument("--disable-background-timer-throttling")
            .set_argument("--disable-backgrounding-occluded-windows")
            .set_argument("--disable-renderer-backgrounding")
            .set_argument("--disable-dev-shm-usage")
.set_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
)
co.set_user_agent(
f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0.0 Safari/537.36"
)
    self.browser = Chromium(co)
    self.page = self.browser.new_tab()
    
    # 立即注入增强的反检测脚本
    self.inject_enhanced_script()

def inject_enhanced_script(self, page=None):
    """注入增强的反检测脚本"""
    if page is None:
        page = self.page
        
    enhanced_script = """
    // 增强的反检测脚本
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    
    // 模拟完整的浏览器环境
    Object.defineProperty(navigator, 'plugins', { 
        get: () => [1, 2, 3, 4, 5],
        configurable: true
    });
    
    Object.defineProperty(navigator, 'languages', { 
        get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
    });
    
    // 屏蔽自动化特征
    window.chrome = { 
        runtime: {},
        loadTimes: function() {},
        csi: function() {}, 
        app: {isInstalled: false}
    };
    
    // 页面可见性API
    Object.defineProperty(document, 'hidden', { get: () => false });
    Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
    
    console.log('🔧 增强的JS环境模拟已加载');
    """
    try:
        page.run_js(enhanced_script)
        logger.info("✅ 增强的反检测脚本已注入")
        return True
    except Exception as e:
        logger.warning(f"注入脚本失败: {str(e)}")
        return False

def get_all_cookies(self):
    """获取所有cookies"""
    try:
        # 使用page.cookies()
        cookies = self.page.cookies()
        if cookies:
            logger.info(f"✅ 获取到 {len(cookies)} 个cookies")
            return cookies
        
        logger.warning("❌ 无法获取cookies")
        return None
        
    except Exception as e:
        logger.error(f"获取cookies时出错: {str(e)}")
        return None

def save_cookies_to_cache(self):
    """保存cookies到缓存"""
    try:
        # 等待一段时间确保cookies设置完成
        time.sleep(3)
        
        # 保存cookies
        cookies = self.get_all_cookies()
        if cookies:
            logger.info(f"🔍 成功获取到 {len(cookies)} 个cookies")
            success = CacheManager.save_cookies(cookies, self.site_name)
            if success:
                logger.info("✅ Cookies缓存已保存")
            else:
                logger.warning("⚠️ Cookies缓存保存失败")
        else:
            logger.warning("⚠️ 无法获取cookies，检查浏览器状态")
                
        return True
    except Exception as e:
        logger.error(f"保存缓存失败: {str(e)}")
        return False

def clear_caches(self):
    """清除所有缓存文件"""
    try:
        cache_dir = CacheManager.get_cache_directory()
        cache_files = [f"{self.site_name}_cookies.json"]
        for file_name in cache_files:
            file_path = os.path.join(cache_dir, file_name)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"🗑️ 已清除缓存: {file_name}")
        
        logger.info("✅ 所有缓存已清除")
        
    except Exception as e:
        logger.error(f"清除缓存失败: {str(e)}")

def enhanced_strict_check_login_status(self):
    """增强的严格登录状态验证 - 多种方式验证用户名"""
    logger.info("🔍 增强严格验证登录状态...")
    
    try:
        # 首先确保在latest页面
        if not self.page.url.endswith('/latest'):
            self.page.get(self.site_config['latest_url'])
            time.sleep(5)
        
        # 处理可能的Cloudflare
        CloudflareHandler.handle_cloudflare(self.page)
        
        # 方法1: 检查当前页面的用户名
        page_content = self.page.html
        if self.username and self.username.lower() in page_content.lower():
            logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
            return True
        
        # 方法2: 尝试访问用户个人资料页面
        logger.info("🔄 尝试访问用户个人资料页面验证...")
        try:
            profile_url = f"{self.site_config['base_url']}/u/{self.username}"
            self.page.get(profile_url)
            time.sleep(3)
            
            profile_content = self.page.html
            if self.username and self.username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面找到用户名: {self.username}")
                # 返回latest页面
                self.page.get(self.site_config['latest_url'])
                time.sleep(3)
                return True
            else:
                logger.warning("❌ 个人资料页面验证失败")
                # 返回latest页面
                self.page.get(self.site_config['latest_url'])
                time.sleep(3)
        except Exception as e:
            logger.warning(f"访问个人资料页面失败: {str(e)}")
            # 返回latest页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(3)
        
        # 方法3: 检查用户头像和菜单
        avatar_selectors = [
            'img.avatar',
            '.user-avatar',
            '.current-user img',
            '[class*="avatar"]',
            'img[src*="avatar"]'
        ]
        
        for selector in avatar_selectors:
            try:
                avatar_element = self.page.ele(selector, timeout=3)
                if avatar_element and avatar_element.is_displayed:
                    logger.success(f"✅ 找到用户头像元素: {selector}")
                    # 如果有头像，尝试点击查看用户名
                    try:
                        avatar_element.click()
                        time.sleep(2)
                        menu_content = self.page.html
                        if self.username and self.username.lower() in menu_content.lower():
                            logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                            # 点击其他地方关闭菜单
                            self.page.ele('body').click()
                            return True
                        self.page.ele('body').click()
                    except:
                        pass
            except:
                continue
        
        # 方法4: 检查用户菜单直接查找用户名
        user_menu_selectors = [
            '#current-user',
            '.current-user',
            '.header-dropdown-toggle',
            '[data-user-menu]',
            '.user-menu'
        ]
        
        for selector in user_menu_selectors:
            try:
                user_element = self.page.ele(selector, timeout=3)
                if user_element and user_element.is_displayed:
                    user_element.click()
                    time.sleep(2)
                    
                    menu_content = self.page.html
                    if self.username and self.username.lower() in menu_content.lower():
                        logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                        self.page.ele('body').click()
                        return True
                    self.page.ele('body').click()
            except:
                continue
        
        # 方法5: 检查登录按钮（反证未登录）
        login_selectors = [
            '.login-button', 
            'button:has-text("登录")', 
            '#login-button',
            'a[href*="/login"]',
            '.btn-login'
        ]
        
        for selector in login_selectors:
            try:
                login_btn = self.page.ele(selector, timeout=3)
                if login_btn and login_btn.is_displayed:
                    logger.error(f"❌ 检测到登录按钮: {selector}")
                    return False
            except:
                continue
        
        logger.error(f"❌ 所有验证方法都失败，未找到用户名: {self.username}")
        return False
        
    except Exception as e:
        logger.error(f"登录状态检查失败: {str(e)}")
        return False

def attempt_login(self):
    """尝试登录 - 改进的登录方法"""
    logger.info("🔐 尝试登录...")
    
    # 导航到登录页面
    self.page.get(self.site_config['login_url'])
    time.sleep(3)
    
    # 重新注入脚本以确保在登录页面生效
    self.inject_enhanced_script()
    
    # 处理Cloudflare验证
    cf_success = CloudflareHandler.handle_cloudflare(self.page)
    if not cf_success:
        logger.warning("⚠️ Cloudflare验证可能未完全通过，但继续登录流程")
    
    # 填写登录信息
    try:
        # 等待登录表单加载
        time.sleep(2)
        
        # 尝试多种可能的表单选择器
        username_selectors = [
            "@id=login-account-name", "@id=username", "@id=login", "@id=email",
"input[name='username']", "input[name='login']", "input[name='email']",
"input[type='text']", "input[placeholder*='用户名']", "input[placeholder*='邮箱']"
]
                password_selectors = [
            "@id=login-account-password", "@id=password", "@id=passwd", 
            "input[name='password']", "input[name='passwd']",
            "input[type='password']", "input[placeholder*='密码']"
        ]
        
        login_button_selectors = [
            "@id=login-button", "button[type='submit']", "input[type='submit']",
            "button:has-text('登录')", "button:has-text('Log In')", "button:has-text('Sign In')",
            ".btn-login", ".btn-primary"
        ]
        
        username_field = None
        password_field = None
        login_button = None
        
        # 查找用户名字段
        for selector in username_selectors:
            try:
                username_field = self.page.ele(selector, timeout=2)
                if username_field:
                    logger.info(f"✅ 找到用户名字段: {selector}")
                    break
            except:
                continue
        
        # 查找密码字段
        for selector in password_selectors:
            try:
                password_field = self.page.ele(selector, timeout=2)
                if password_field:
                    logger.info(f"✅ 找到密码字段: {selector}")
                    break
            except:
                continue
        
        # 查找登录按钮
        for selector in login_button_selectors:
            try:
                login_button = self.page.ele(selector, timeout=2)
                if login_button:
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    break
            except:
                continue
        
        if username_field and password_field and login_button:
            username_field.input(self.username)
            password_field.input(self.password)
            
            # 点击登录按钮
            login_button.click()
            time.sleep(15)  # 增加等待时间确保登录完成
            
            # 检查是否有错误消息
            error_selectors = ['.alert-error', '.error', '.flash-error', '.alert.alert-error']
            for selector in error_selectors:
                try:
                    error_element = self.page.ele(selector, timeout=3)
                    if error_element:
                        error_text = error_element.text
                        logger.error(f"❌ 登录错误: {error_text}")
                        return False
                except:
                    continue
            
            # 增强的严格检查登录是否成功
            login_success = self.enhanced_strict_check_login_status()
            if login_success:
                logger.success("✅ 登录成功")
                # 保存缓存
                self.save_cookies_to_cache()
                return True
            else:
                logger.error("❌ 登录失败")
                # 登录失败时清除可能损坏的缓存
                self.clear_caches()
                return False
        else:
            logger.error("❌ 找不到登录表单元素")
            return False
            
    except Exception as e:
        logger.error(f"❌ 登录过程出错: {str(e)}")
        return False

def ensure_logged_in(self):
    """确保用户已登录 - 改进策略"""
    logger.info("🎯 开始登录流程")
    
    # 首先尝试使用缓存cookies
    cached_cookies = CacheManager.load_cookies(self.site_name)
    if cached_cookies:
        logger.info("🔄 尝试使用缓存cookies登录")
        try:
            # 设置cookies
            self.page.set_cookies(cached_cookies)
            
            # 跳转到latest页面验证登录状态
            self.page.get(self.site_config['latest_url'])
            time.sleep(5)
            
            # 处理可能的Cloudflare
            CloudflareHandler.handle_cloudflare(self.page)
            
            # 增强的严格验证登录状态
            if self.enhanced_strict_check_login_status():
                logger.success("✅ 使用缓存cookies登录成功")
                return True
            else:
                logger.warning("❌ 缓存cookies已失效")
        except Exception as e:
            logger.warning(f"缓存登录失败: {str(e)}")

    # 如果缓存失败，尝试直接登录
    return self.attempt_login()

@retry_decorator()
def click_one_topic(self, topic_url):
    """浏览单个主题"""
    new_page = self.browser.new_tab()
    try:
        new_page.get(topic_url)
        time.sleep(3)
        
        # 注入脚本到新页面
        self.inject_enhanced_script(new_page)
        
        # 随机决定是否点赞 (0.5%概率)
        if random.random() < 0.005:  
            self.click_like(new_page)
        
        # 浏览帖子内容 - 不打印滚动日志
        self.browse_post(new_page)
        new_page.close()
        return True
        
    except Exception as e:
        logger.error(f"浏览主题失败: {str(e)}")
        try:
            new_page.close()
        except:
            pass
        return False

def click_like(self, page):
    """点赞帖子"""
    try:
        like_button = page.ele(".discourse-reactions-reaction-button", timeout=5)
        if like_button and like_button.is_displayed:
            logger.info("找到未点赞的帖子，准备点赞")
            like_button.click()
            logger.info("点赞成功")
            time.sleep(random.uniform(1, 2))
        else:
            logger.info("帖子可能已经点过赞了")
    except Exception as e:
        logger.error(f"点赞失败: {str(e)}")

def browse_post(self, page):
    """浏览帖子内容 - 简化版本，不打印滚动日志"""
    prev_url = None
    
    # 开始自动滚动，最多滚动8次
    for i in range(8):
        # 随机滚动一段距离
        scroll_distance = random.randint(400, 800)
        page.run_js(f"window.scrollBy(0, {scroll_distance})")

        if random.random() < 0.03:
            break

        # 检查是否到达页面底部
        at_bottom = page.run_js(
            "window.scrollY + window.innerHeight >= document.body.scrollHeight"
        )
        current_url = page.url
        if current_url != prev_url:
            prev_url = current_url
        elif at_bottom and prev_url == current_url:
            break

        # 动态随机等待
        wait_time = random.uniform(2, 4)
        time.sleep(wait_time)

def click_topic(self):
    """点击浏览主题"""
    if not BROWSE_ENABLED:
        logger.info("⏭️ 浏览功能已禁用，跳过")
        return True
        
    logger.info("🌐 开始浏览主题")
    
    # 确保在latest页面
    if not self.page.url.endswith('/latest'):
        self.page.get(self.site_config['latest_url'])
        time.sleep(5)
    
    try:
        # 获取主题列表
        topic_list = self.page.eles(".:title")
        if not topic_list:
            logger.error("❌ 没有找到主题列表")
            return False
        
        # 随机选择5-8个主题
        browse_count = min(random.randint(5, 8), len(topic_list))
        selected_topics = random.sample(topic_list, browse_count)
        success_count = 0
        
        logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择 {browse_count} 个")
        
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
                
                # 随机等待
                if i < browse_count - 1:
                    wait_time = random.uniform(5, 12)
                    time.sleep(wait_time)
                
            except Exception as e:
                logger.error(f"浏览主题失败: {str(e)}")
                continue
        
        logger.info(f"📊 浏览完成: 成功 {success_count}/{browse_count} 个主题")
        return success_count > 0
        
    except Exception as e:
        logger.error(f"获取主题列表失败: {str(e)}")
        return False

def enhanced_get_connect_info(self, page, max_retries=3):
    """增强的连接信息获取"""
    for attempt in range(max_retries):
        try:
            logger.info(f"🔄 尝试获取连接信息 ({attempt + 1}/{max_retries})")
            
            # 刷新页面确保最新状态
            if attempt > 0:
                page.refresh()
                page.wait.doc_loaded()
                CloudflareHandler.handle_cloudflare(page)
            
            # 先确保页面稳定
            page.wait.doc_loaded()
            
            
            # 处理可能的Cloudflare验证
            CloudflareHandler.handle_cloudflare(page)
            
            # 等待更长时间确保页面完全加载
time.sleep(5)
            # 多种方式查找连接信息表格
            table_selectors = [
                "table",
                ".table",
                ".connect-table",
                ".connection-info",
                "[class*='table']",
                "[class*='connect']"
            ]
            
            for selector in table_selectors:
                try:
                    table = page.ele(selector, timeout=10)
                    if table:
                        logger.info(f"✅ 找到表格元素: {selector}")
                        
                        # 提取表格数据
                        rows = table.eles("tag:tr")
                        info = []
                        
                        for row in rows:
                            cells = row.eles("tag:td")
                            if len(cells) >= 3:
                                project = cells[0].text.strip()
                                current = cells[1].text.strip()
                                requirement = cells[2].text.strip()
                                info.append([project, current, requirement])
                        
                        if info:
                            return info
                except:
                    continue
            
            # 如果找不到标准表格，尝试其他方式获取信息
            logger.info("🔄 尝试其他方式获取连接信息...")
            
            
            # 方法1: 查找包含连接信息的任何元素
            connect_selectors = [
                ".connect-info",
                ".connection-stats",
                ".user-stats",
                "[class*='connect']",
                "[class*='connection']"
            ]
            
            for selector in connect_selectors:
                try:
                    element = page.ele(selector, timeout=5)
                    if element:
                        text_content = element.text
                        if text_content and len(text_content.strip()) > 10:
                            logger.info(f"✅ 找到连接信息元素: {selector}")
                            return [["连接信息", text_content[:100] + "...", "详见页面"]]
                except:
                    continue
            
            # 方法2: 查找任何包含数字和统计信息的元素
            stats_elements = page.eles('[class*="stat"]') + page.eles('[class*="count"]')
            if stats_elements:
                stats_info = []
                for elem in stats_elements[:5]:  # 取前5个统计元素
                    try:
                        text = elem.text.strip()
                        if text and any(char.isdigit() for char in text):
                            stats_info.append(["统计信息", text, "-"])
                    except:
                        continue
                
                if stats_info:
                    return stats_info
            
            logger.warning(f"⚠️ 第 {attempt + 1} 次尝试未找到连接信息")
            time.sleep(3)  # 等待后重试
            
        except Exception as e:
            logger.warning(f"⚠️ 第 {attempt + 1} 次尝试失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(3)
    
    return None

def print_connect_info(self):
    logger.info("获取连接信息")
    page = self.browser.new_tab()
    try:
        page.get("https://connect.linux.do/")
        time.sleep(5)
        
        rows = page.ele("tag:table").eles("tag:tr")
        info = []

        for row in rows:
            cells = row.eles("tag:td")
            if len(cells) >= 3:
                project = cells[0].text.strip()
                current = cells[1].text.strip()
                requirement = cells[2].text.strip()
                info.append([project, current, requirement])

        print("--------------Connect Info------------------")
        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
    except Exception as e:
        logger.error(f"获取连接信息失败: {str(e)}")
    finally:
        page.close()
        ======================== 主函数 ========================
def main():
"""主函数"""
logger.info("🎯 Linux.Do 多站点自动化脚本启动")
# 设置环境变量
os.environ.pop("DISPLAY", None)

success_sites = []
failed_sites = []

# 遍历所有站点
for site_config in SITES:
    site_name = site_config['name']
    credentials = SITE_CREDENTIALS.get(site_name, {})
    
    # 检查凭证是否存在
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
    
    # 站点间随机等待
    if site_config != SITES[-1]:
        wait_time = random.uniform(10, 30)
        logger.info(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个站点...")
        time.sleep(wait_time)

# 输出总结
logger.info("📊 执行总结:")
logger.info(f"✅ 成功站点: {', '.join(success_sites) if success_sites else '无'}")
logger.info(f"❌ 失败站点: {', '.join(failed_sites) if failed_sites else '无'}")

# 如果有成功站点，不算完全失败
if success_sites:
    logger.success("🎉 部分任务完成")
    sys.exit(0)
else:
    logger.error("💥 所有任务失败")
    sys.exit(1)
    if name == "main":
main()
    
