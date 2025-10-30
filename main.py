"""
cron: 0 * * * *
new Env("Linux.Do 签到")
"""

import os
import random
import time
import functools
import sys
import json
import requests
import re
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# ======================== 配置常量 ========================
# 站点认证信息配置 - 从环境变量获取
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
        'latest_topics_url': 'https://linux.do/latest',
        'home_url': 'https://linux.do/',
        'connect_url': 'https://connect.linux.do/'
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest', 
        'home_url': 'https://idcflare.com/',
        'connect_url': None
    }
]

# 全局配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
PAGE_TIMEOUT = 180000
RETRY_TIMES = 2

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类，负责缓存文件的读写和管理"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(current_dir, "cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
            os.chmod(cache_dir, 0o755)
        except Exception as e:
            logger.warning(f"创建缓存目录失败: {e}")
            cache_dir = current_dir
        return cache_dir
    
    @staticmethod
    def get_cache_file_path(file_name):
        """获取缓存文件的完整路径"""
        cache_dir = CacheManager.get_cache_directory()
        return os.path.join(cache_dir, file_name)
    
    @staticmethod
    def get_file_age_hours(file_path):
        """获取文件年龄（小时）"""
        if not os.path.exists(file_path):
            return None
        try:
            file_mtime = os.path.getmtime(file_path)
            current_time = time.time()
            age_hours = (current_time - file_mtime) / 3600
            return age_hours
        except Exception:
            return None

    @staticmethod
    def load_cache(file_name):
        """从文件加载缓存数据"""
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                
                age_hours = CacheManager.get_file_age_hours(file_path)
                if age_hours is not None:
                    age_status = "全新" if age_hours < 0.1 else "较新" if age_hours < 6 else "较旧"
                    logger.info(f"📦 加载缓存 {file_name} (年龄: {age_hours:.3f}小时, {age_status})")
                else:
                    logger.info(f"📦 加载缓存 {file_name} (年龄: 未知)")
                
                return data.get('data', data)
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
                try:
                    os.remove(file_path)
                    logger.info(f"🗑️ 已删除损坏的缓存文件: {file_name}")
                except:
                    pass
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存数据到缓存文件"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '2.0',
                'file_created': time.time(),
            }
            
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            os.chmod(file_path, 0o644)
            
            new_age = CacheManager.get_file_age_hours(file_path)
            file_size = os.path.getsize(file_path)
            if new_age is not None:
                logger.info(f"💾 缓存已保存到 {file_name} (新年龄: {new_age:.3f}小时, 大小: {file_size} 字节)")
            else:
                logger.info(f"💾 缓存已保存到 {file_name} (大小: {file_size} 字节)")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_cookies(site_name):
        """加载cookies缓存"""
        return CacheManager.load_cache(f"{site_name}_cookies.json")

    @staticmethod
    def save_cookies(cookies, site_name):
        """保存cookies到缓存"""
        return CacheManager.save_cache(cookies, f"{site_name}_cookies.json")

    @staticmethod
    def load_session(site_name):
        """加载会话缓存"""
        return CacheManager.load_cache(f"{site_name}_session.json") or {}

    @staticmethod
    def save_session(session_data, site_name):
        """保存会话数据到缓存"""
        return CacheManager.save_cache(session_data, f"{site_name}_session.json")

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
                    if expires == -1 or expires > time.time():
                        return True
            return False
        except Exception:
            return False

    @staticmethod
    def handle_cloudflare(page, home_url, max_attempts=8, timeout=180):
        """处理Cloudflare验证"""
        start_time = time.time()
        logger.info("🛡️ 开始处理 Cloudflare验证")
        
        # 检查缓存的Cloudflare cookies
        cached_cookies = CacheManager.load_cookies("cloudflare")
        cached_cf_valid = CloudflareHandler.is_cf_cookie_valid(cached_cookies)
        
        if cached_cf_valid:
            logger.success("✅ 检测到有效的缓存Cloudflare cookie")
            try:
                if cached_cookies:
                    page.set.cookies(cached_cookies)
                    page.get(home_url)
                    time.sleep(5)
                    
                    page_title = page.title
                    if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                        logger.success("✅ 使用缓存成功绕过Cloudflare验证")
                        return True
            except Exception as e:
                logger.warning(f"使用缓存绕过失败: {str(e)}")
        
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
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    time.sleep(1)
            return None
        return wrapper
    return decorator

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials) -> None:
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        
        # 浏览器配置
        platformIdentifier = "Windows NT 10.0; Win64; x64"

        co = (
            ChromiumOptions()
            .headless(True)
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
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # 加载会话数据
        self.session_data = CacheManager.load_session(self.site_name)
        self.cache_saved = False
        
        # 注入反检测脚本
        self.inject_enhanced_script()

    def inject_enhanced_script(self, page=None):
        """注入增强的反检测脚本"""
        if page is None:
            page = self.page
            
        enhanced_script = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { 
            get: () => [1, 2, 3, 4, 5],
            configurable: true
        });
        Object.defineProperty(navigator, 'languages', { 
            get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
        });
        window.chrome = { 
            runtime: {},
            loadTimes: function() {},
            csi: function() {}, 
            app: {isInstalled: false}
        };
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
            cookies = self.browser.cookies()
            if cookies:
                logger.info(f"✅ 获取到 {len(cookies)} 个cookies")
                return cookies
            
            cookies = self.page.cookies()
            if cookies:
                logger.info(f"✅ 通过page.cookies()获取到 {len(cookies)} 个cookies")
                return cookies
            
            logger.warning("❌ 无法获取cookies")
            return None
            
        except Exception as e:
            logger.error(f"获取cookies时出错: {str(e)}")
            return None

    def save_all_caches(self, force_save=False):
        """统一保存所有缓存"""
        if self.cache_saved and not force_save:
            return True
            
        try:
            time.sleep(3)
            
            # 保存cookies
            try:
                cookies = self.get_all_cookies()
                if cookies:
                    logger.info(f"🔍 成功获取到 {len(cookies)} 个cookies")
                    success = CacheManager.save_cookies(cookies, self.site_name)
                    if success:
                        logger.info("✅ Cookies缓存已保存")
                    else:
                        logger.warning("⚠️ Cookies缓存保存失败")
                else:
                    logger.warning("⚠️ 无法获取cookies")
                    
            except Exception as e:
                logger.error(f"获取cookies失败: {str(e)}")
            
            # 更新并保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '4.0',
                'total_saved': self.session_data.get('total_saved', 0) + 1,
                'last_url': self.page.url
            })
            success = CacheManager.save_session(self.session_data, self.site_name)
            if success:
                logger.info("✅ 会话缓存已保存")
            else:
                logger.warning("⚠️ 会话缓存保存失败")
            
            self.cache_saved = True
            return True
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")
            return False

    def clear_caches(self):
        """清除所有缓存文件"""
        try:
            cache_dir = CacheManager.get_cache_directory()
            cache_files = [
                f"{self.site_name}_cookies.json", 
                f"{self.site_name}_session.json"
            ]
            for file_name in cache_files:
                file_path = os.path.join(cache_dir, file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info("✅ 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    def strict_check_login_status(self):
        """严格检查登录状态 - 必须验证用户名"""
        try:
            logger.info("🔍 开始严格登录状态检查...")
            
            # 方法1: 检查用户头像或用户菜单
            user_selectors = [
                '#current-user',
                '.current-user',
                'img.avatar',
                '.header-dropdown-toggle',
                '[data-user-menu]',
                '.user-menu'
            ]
            
            for selector in user_selectors:
                try:
                    user_element = self.page.ele(selector, timeout=5)
                    if user_element:
                        logger.success(f"✅ 找到用户元素: {selector}")
                        # 尝试点击用户菜单获取更多信息
                        if self._click_and_verify_user_menu():
                            return True
                except Exception:
                    continue
            
            # 方法2: 在页面内容中搜索用户名
            page_content = self.page.html.lower()
            if self.username and self.username.lower() in page_content:
                logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                return True
            
            # 方法3: 检查是否有登录按钮（反证未登录）
            login_selectors = [
                '.login-button',
                'button:has-text("登录")',
                'button:has-text("Log In")',
                '#login-button'
            ]
            
            for selector in login_selectors:
                try:
                    login_btn = self.page.ele(selector, timeout=3)
                    if login_btn and login_btn.displayed:
                        logger.warning(f"❌ 检测到登录按钮，确认未登录: {selector}")
                        return False
                except Exception:
                    continue
            
            # 方法4: 检查URL是否包含登录相关路径
            current_url = self.page.url.lower()
            if 'login' in current_url or 'signin' in current_url:
                logger.warning("❌ 当前在登录页面，确认未登录")
                return False
            
            # 如果所有检查都不确定，尝试访问用户相关页面
            logger.warning("⚠️ 登录状态不确定，需要进一步验证")
            return self._verify_by_user_page()
            
        except Exception as e:
            logger.error(f"严格登录检查时出错: {str(e)}")
            return False

    def _click_and_verify_user_menu(self):
        """点击用户菜单并验证用户名"""
        try:
            # 尝试点击用户头像或菜单
            click_selectors = ['img.avatar', '.current-user', '.header-dropdown-toggle']
            
            for selector in click_selectors:
                try:
                    user_elem = self.page.ele(selector, timeout=3)
                    if user_elem:
                        user_elem.click()
                        time.sleep(2)
                        
                        # 检查下拉菜单内容
                        menu_content = self.page.html.lower()
                        if self.username and self.username.lower() in menu_content:
                            logger.success(f"✅ 在用户菜单中找到用户名: {self.username}")
                            # 点击其他地方关闭菜单
                            self.page.ele('body').click()
                            time.sleep(1)
                            return True
                            
                        # 关闭菜单
                        self.page.ele('body').click()
                        time.sleep(1)
                except Exception:
                    continue
                    
            return False
        except Exception as e:
            logger.warning(f"点击用户菜单失败: {str(e)}")
            return False

    def _verify_by_user_page(self):
        """通过访问用户页面验证登录状态"""
        try:
            # 尝试访问用户个人页面
            if self.site_name == 'linux_do':
                user_url = f"https://linux.do/u/{self.username}"
            else:
                user_url = f"{self.site_config['base_url']}/u/{self.username}"
                
            self.page.get(user_url)
            time.sleep(3)
            
            page_content = self.page.html.lower()
            if self.username and self.username.lower() in page_content:
                logger.success(f"✅ 在用户页面验证成功: {self.username}")
                return True
            else:
                logger.warning("❌ 用户页面验证失败")
                return False
                
        except Exception as e:
            logger.warning(f"用户页面验证失败: {str(e)}")
            return False

    def login(self):
        """登录网站"""
        logger.info(f"开始登录 {self.site_config['name']}")
        
        # 先尝试访问主页处理Cloudflare
        self.page.get(self.site_config['home_url'])
        time.sleep(5)
        
        # 处理Cloudflare验证
        CloudflareHandler.handle_cloudflare(self.page, self.site_config['home_url'])
        
        # 严格检查是否已经登录
        if self.strict_check_login_status():
            logger.success("✅ 严格验证确认已登录，跳过登录步骤")
            return True
        
        # 需要重新登录
        logger.info("🔐 需要重新登录...")
        self.page.get(self.site_config['login_url'])
        time.sleep(5)
        
        try:
            # 查找登录表单元素
            username_input = None
            password_input = None
            login_button = None
            
            # 尝试多种选择器
            username_selectors = [
                '@id=login-account-name',
                '@name=username',
                '@name=login',
                'input[type="text"]',
                'input[placeholder*="用户名"]',
                'input[placeholder*="email"]'
            ]
            
            password_selectors = [
                '@id=login-account-password', 
                '@name=password',
                'input[type="password"]',
                'input[placeholder*="密码"]'
            ]
            
            login_button_selectors = [
                '@id=login-button',
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")',
                'input[type="submit"]'
            ]
            
            # 查找用户名输入框
            for selector in username_selectors:
                try:
                    username_input = self.page.ele(selector, timeout=3)
                    if username_input:
                        logger.info(f"✅ 找到用户名输入框: {selector}")
                        break
                except:
                    continue
            
            # 查找密码输入框
            for selector in password_selectors:
                try:
                    password_input = self.page.ele(selector, timeout=3)
                    if password_input:
                        logger.info(f"✅ 找到密码输入框: {selector}")
                        break
                except:
                    continue
            
            # 查找登录按钮
            for selector in login_button_selectors:
                try:
                    login_button = self.page.ele(selector, timeout=3)
                    if login_button:
                        logger.info(f"✅ 找到登录按钮: {selector}")
                        break
                except:
                    continue
            
            if not username_input or not password_input or not login_button:
                logger.error("❌ 找不到完整的登录表单元素")
                # 截图调试
                try:
                    self.page.get_screenshot(f"login_form_{self.site_name}.png")
                    logger.info(f"📸 已保存登录页面截图: login_form_{self.site_name}.png")
                except:
                    pass
                return False
            
            # 输入用户名和密码
            username_input.input(self.username)
            time.sleep(1)
            password_input.input(self.password)
            time.sleep(1)
            
            # 点击登录
            login_button.click()
            logger.info("🔄 提交登录表单...")
            
            # 等待登录完成
            time.sleep(10)
            
            # 严格验证登录是否成功
            if self.strict_check_login_status():
                logger.success("✅ 登录成功")
                self.save_all_caches()
                return True
            else:
                logger.error("❌ 登录失败 - 严格验证未通过")
                return False
                
        except Exception as e:
            logger.error(f"登录过程中出错: {str(e)}")
            return False

    def click_topic(self):
        """点击浏览主题 - 增强版本"""
        try:
            logger.info("📚 访问最新主题页面...")
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
            
            # 多种选择器获取主题列表
            topic_selectors = [
                '.title a',           # 标准选择器
                'a.title',            # 另一种格式
                '.topic-list-item a', # 主题列表项
                '[data-topic-id] a',  # 带topic id的
                '.main-link a',       # 主链接
                '.raw-topic-link'     # 原始主题链接
            ]
            
            topic_list = []
            for selector in topic_selectors:
                try:
                    topics = self.page.eles(f"css:{selector}")
                    if topics and len(topics) > 0:
                        logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(topics)} 个主题")
                        topic_list.extend(topics)
                        break
                except Exception as e:
                    continue
            
            # 如果上面的选择器都没找到，尝试更通用的方法
            if not topic_list:
                logger.info("🔄 尝试通用链接查找...")
                try:
                    # 查找所有包含主题的链接
                    all_links = self.page.eles('tag:a')
                    for link in all_links:
                        href = link.attr('href')
                        if href and '/t/' in href and not href.endswith('/t/'):
                            topic_list.append(link)
                    
                    logger.info(f"✅ 通过通用查找找到 {len(topic_list)} 个主题链接")
                except Exception as e:
                    logger.error(f"通用查找失败: {str(e)}")
            
            if not topic_list:
                logger.error("❌ 无法找到任何主题链接")
                # 保存页面源码用于调试
                try:
                    with open(f"page_source_{self.site_name}.html", "w", encoding='utf-8') as f:
                        f.write(self.page.html)
                    logger.info(f"📄 已保存页面源码: page_source_{self.site_name}.html")
                except:
                    pass
                return False
            
            # 去重并限制数量
            unique_topics = []
            seen_urls = set()
            
            for topic in topic_list:
                try:
                    href = topic.attr('href')
                    if href and href not in seen_urls:
                        seen_urls.add(href)
                        unique_topics.append(topic)
                except:
                    continue
            
            # 随机选择主题进行浏览
            browse_count = min(8, len(unique_topics))
            selected_topics = random.sample(unique_topics, browse_count)
            
            logger.info(f"🎯 准备浏览 {browse_count} 个主题")
            
            successful_browses = 0
            for i, topic in enumerate(selected_topics):
                try:
                    topic_title = topic.text.strip()[:50]  # 限制标题长度
                    topic_url = topic.attr('href')
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览第 {i+1}/{browse_count} 个主题: {topic_title}")
                    
                    if self.click_one_topic(topic_url):
                        successful_browses += 1
                    
                    # 随机等待一段时间再浏览下一个
                    wait_time = random.uniform(4, 8)
                    logger.info(f"⏳ 等待 {wait_time:.1f} 秒...")
                    time.sleep(wait_time)
                    
                except Exception as e:
                    logger.error(f"浏览主题时出错: {str(e)}")
                    continue
            
            logger.info(f"📊 浏览完成: 成功 {successful_browses}/{browse_count} 个主题")
            return successful_browses > 0
                    
        except Exception as e:
            logger.error(f"获取主题列表时出错: {str(e)}")
            return False

    @retry_decorator()
    def click_one_topic(self, topic_url):
        """浏览单个主题"""
        new_page = self.browser.new_tab()
        try:
            logger.info(f"🔗 打开主题: {topic_url}")
            new_page.get(topic_url)
            time.sleep(3)
            
            # 获取页面标题
            page_title = new_page.title
            logger.info(f"✅ 已打开主题页面: {page_title}")
            
            # 浏览帖子内容（模拟人类阅读行为）
            browse_success = self.browse_post(new_page)
            
            # 随机决定是否点赞（25%概率）
            if random.random() < 0.0025:  
                self.click_like(new_page)
            
            new_page.close()
            return browse_success
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            try:
                new_page.close()
            except:
                pass
            return False

    def browse_post(self, page):
        """浏览帖子内容，模拟人类阅读行为"""
        logger.info("👀 开始浏览帖子内容...")
        
        total_wait_time = 0
        max_wait_time = random.uniform(25, 40)  # 总浏览时间25-40秒
        
        # 开始自动滚动，模拟阅读
        scroll_count = 0
        while total_wait_time < max_wait_time and scroll_count < 12:
            # 随机滚动距离
            scroll_distance = random.randint(200, 600)
            logger.info(f"📜 向下滚动 {scroll_distance} 像素 (第{scroll_count + 1}次)")
            
            try:
                page.run_js(f"window.scrollBy(0, {scroll_distance})")
            except Exception as e:
                logger.warning(f"滚动失败: {str(e)}")
            
            # 随机等待时间，模拟阅读
            wait_time = random.uniform(2, 5)
            total_wait_time += wait_time
            
            logger.info(f"⏳ 等待 {wait_time:.1f} 秒 (累计: {total_wait_time:.1f}秒)")
            time.sleep(wait_time)
            
            scroll_count += 1
            
            # 10%概率提前结束浏览
            if random.random() < 0.1:
                logger.info("🎲 随机决定停止浏览")
                break
        
        logger.info(f"✅ 帖子浏览完成，总时长: {total_wait_time:.1f}秒")
        return True

    def click_like(self, page):
        """点赞帖子"""
        try:
            # 多种点赞按钮选择器
            like_selectors = [
                '.like-button',
                '.btn-like',
                '[title*="赞"]',
                '[title*="like"]',
                '.post-like-btn',
                '.d-likes'
            ]
            
            for selector in like_selectors:
                try:
                    like_buttons = page.eles(f"css:{selector}")
                    for button in like_buttons:
                        if button and button.displayed:
                            button.click()
                            logger.info("👍 点赞成功")
                            time.sleep(random.uniform(1, 2))
                            return True
                except:
                    continue
                    
            logger.info("ℹ️ 未找到可点击的点赞按钮")
            return False
            
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")
            return False

    def print_connect_info(self):
        """打印连接信息（仅限linux.do）"""
        if self.site_name != 'linux_do' or not self.site_config.get('connect_url'):
            logger.info("⏭️ 跳过连接信息获取（不适用于此站点）")
            return
            
        logger.info("🔗 获取连接信息...")
        try:
            page = self.browser.new_tab()
            page.set.timeout(15)  # 设置较短超时时间
            
            page.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 多种表格选择器
            table_selectors = [
                "tag:table",
                ".table",
                ".connect-table",
                "[data-table]"
            ]
            
            table = None
            for selector in table_selectors:
                try:
                    table = page.ele(selector, timeout=5)
                    if table:
                        logger.info(f"✅ 找到表格: {selector}")
                        break
                except:
                    continue
            
            if table:
                rows = table.eles("tag:tr")
                info = []

                for i, row in enumerate(rows):
                    if i == 0:  # 跳过表头
                        continue
                    cells = row.eles("tag:td")
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        info.append([project, current, requirement])

                if info:
                    print("--------------Connect Info-----------------")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                    logger.success("✅ 连接信息获取成功")
                else:
                    logger.warning("⚠️ 表格为空或格式不符")
            else:
                logger.warning("⚠️ 未找到连接信息表格")
                
            page.close()
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
            try:
                page.close()
            except:
                pass

    def run(self):
        """运行主流程"""
        logger.info(f"🚀 开始处理站点: {self.site_config['name']}")
        
        try:
            # 登录
            if not self.login():
                logger.error(f"❌ {self.site_config['name']} 登录失败，跳过该站点")
                return False

            # 浏览主题
            if BROWSE_ENABLED:
                logger.info("🌐 开始浏览主题")
                browse_success = self.click_topic()
                if browse_success:
                    logger.success("✅ 浏览任务完成")
                else:
                    logger.warning("⚠️ 浏览任务部分失败")
            else:
                logger.info("⏭️ 浏览功能已禁用，跳过")

            # 打印连接信息（仅linux.do）
            self.print_connect_info()

            # 保存缓存
            self.save_all_caches(force_save=True)
            
            logger.success(f"✅ {self.site_config['name']} 处理完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_config['name']} 处理失败: {str(e)}")
            return False
        finally:
            # 关闭浏览器
            try:
                self.page.close()
                self.browser.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    """主函数"""
    logger.info("🎯 Linux.Do 多站点自动化脚本启动")
    
    # 设置环境变量
    os.environ.pop("DISPLAY", None)
    os.environ.pop("DYLD_LIBRARY_PATH", None)
    
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
    
    if failed_sites:
        sys.exit(1)
    else:
        logger.success("🎉 所有站点处理完成")

if __name__ == "__main__":
    main()
