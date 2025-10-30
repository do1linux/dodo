"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import os
import random
import time
import functools
import sys
import json
from datetime import datetime
from loguru import logger
from playwright.sync_api import sync_playwright
from tabulate import tabulate


def retry_decorator(retries=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:  # 最后一次尝试
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    time.sleep(1)
            return None

        return wrapper

    return decorator


os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD")

# 修复 BROWSE_ENABLED 环境变量处理
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower()
if BROWSE_ENABLED in ['false', '0', 'off', 'no']:
    BROWSE_ENABLED = False
else:
    BROWSE_ENABLED = True

if not USERNAME:
    USERNAME = os.environ.get('USERNAME')
if not PASSWORD:
    PASSWORD = os.environ.get('PASSWORD')

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"


# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类，负责缓存文件的读写和管理"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(current_dir, "cache")
        # 确保缓存目录存在
        try:
            os.makedirs(cache_dir, exist_ok=True)
            # 设置目录权限
            os.chmod(cache_dir, 0o755)
        except Exception as e:
            logger.warning(f"创建缓存目录失败: {e}")
            # 如果创建缓存目录失败，使用当前目录
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
                # 加载失败时删除损坏的缓存文件
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
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '1.0',
                'file_created': time.time(),
            }
            
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            # 设置文件权限
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
    def load_cookies():
        """加载cookies缓存"""
        return CacheManager.load_cache("linuxdo_cookies.json")

    @staticmethod
    def save_cookies(cookies):
        """保存cookies到缓存"""
        return CacheManager.save_cache(cookies, "linuxdo_cookies.json")

    @staticmethod
    def load_session():
        """加载会话缓存"""
        return CacheManager.load_cache("linuxdo_session.json") or {}

    @staticmethod
    def save_session(session_data):
        """保存会话数据到缓存"""
        return CacheManager.save_cache(session_data, "linuxdo_session.json")


class LinuxDoBrowser:
    def __init__(self) -> None:
        # 使用Chromium而不是Firefox，在GitHub Actions中更稳定
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=VizDisplayCompositor',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
            ]
        )
        
        # 加载缓存的cookies
        cached_cookies = CacheManager.load_cookies()
        
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
        )
        
        # 如果有缓存的cookies，设置它们
        if cached_cookies:
            logger.info(f"✅ 加载了 {len(cached_cookies)} 个缓存cookies")
            self.context.add_cookies(cached_cookies)
        
        self.page = self.context.new_page()
        
        # 加载会话数据
        self.session_data = CacheManager.load_session()
        self.cache_saved = False
        
        # 注入反检测脚本
        self.inject_stealth_script()

    def inject_stealth_script(self):
        """注入反检测脚本"""
        stealth_script = """
        // 屏蔽自动化特征
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
        
        // 屏蔽Chrome特征
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {isInstalled: false} };
        
        // 页面可见性
        Object.defineProperty(document, 'hidden', { get: () => false });
        Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
        
        console.log('🔧 反检测脚本已加载');
        """
        
        self.page.add_init_script(stealth_script)

    def save_all_caches(self):
        """保存所有缓存"""
        try:
            # 保存cookies
            cookies = self.context.cookies()
            if cookies:
                CacheManager.save_cookies(cookies)
                logger.info(f"✅ 保存了 {len(cookies)} 个cookies")
            
            # 保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
            })
            CacheManager.save_session(self.session_data)
            logger.info("✅ 会话数据已保存")
            
            self.cache_saved = True
            return True
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")
            return False

    def clear_caches(self):
        """清除所有缓存"""
        try:
            cache_files = ["linuxdo_cookies.json", "linuxdo_session.json"]
            for file_name in cache_files:
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info("✅ 所有缓存已清除")
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    def check_login_status(self):
        """检查登录状态"""
        try:
            # 检查用户元素
            user_element = self.page.query_selector("#current-user")
            if user_element:
                logger.success("✅ 检测到用户元素，已登录")
                return True
            
            # 检查登录按钮
            login_button = self.page.query_selector(".login-button")
            if login_button:
                logger.warning("❌ 检测到登录按钮，未登录")
                return False
            
            # 检查页面内容中的用户名
            page_content = self.page.content()
            if USERNAME and USERNAME.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {USERNAME}")
                return True
            
            logger.warning("⚠️ 登录状态不确定")
            return False
            
        except Exception as e:
            logger.warning(f"检查登录状态时出错: {str(e)}")
            return False

    def login(self):
        """登录流程"""
        logger.info("开始登录")
        
        # 首先尝试使用缓存cookies访问
        cached_cookies = CacheManager.load_cookies()
        if cached_cookies:
            logger.info("✅ 使用缓存cookies访问")
            self.page.goto(HOME_URL)
            time.sleep(5)
            
            if self.check_login_status():
                logger.success("✅ 缓存登录成功")
                return True
        
        # 正常登录流程
        self.page.goto(LOGIN_URL)
        time.sleep(3)
        
        # 等待并填写登录表单
        self.page.fill("#login-account-name", USERNAME)
        time.sleep(1)
        self.page.fill("#login-account-password", PASSWORD)
        time.sleep(1)
        self.page.click("#login-button")
        time.sleep(10)
        
        # 检查登录是否成功
        if self.check_login_status():
            logger.success("✅ 登录成功")
            self.save_all_caches()
            return True
        else:
            logger.error("❌ 登录失败")
            self.clear_caches()
            return False

    def click_topic(self):
        """浏览主题帖"""
        # 确保在主页面
        self.page.goto(HOME_URL)
        time.sleep(3)
        
        # 获取主题列表
        topic_elements = self.page.query_selector_all("#list-area .title")
        if not topic_elements:
            logger.warning("未找到主题帖，尝试刷新页面")
            self.page.reload()
            time.sleep(3)
            topic_elements = self.page.query_selector_all("#list-area .title")
        
        topic_count = len(topic_elements)
        # 随机选择4-8个主题进行浏览
        browse_count = min(random.randint(4, 8), topic_count)
        logger.info(f"发现 {topic_count} 个主题帖，随机选择 {browse_count} 个进行浏览")
        
        selected_topics = random.sample(topic_elements, browse_count)
        for i, topic in enumerate(selected_topics):
            logger.info(f"📖 浏览进度: {i+1}/{browse_count}")
            
            # 获取主题链接
            topic_href = topic.get_attribute("href")
            if topic_href:
                self.click_one_topic(topic_href)
            
            # 主题间随机延迟
            if i < browse_count - 1:
                delay = random.uniform(5, 10)
                logger.info(f"⏳ 主题间延迟 {delay:.1f} 秒")
                time.sleep(delay)

    @retry_decorator()
    def click_one_topic(self, topic_url):
        """浏览单个主题"""
        # 在新标签页中打开主题
        new_page = self.context.new_page()
        try:
            full_url = f"https://linux.do{topic_url}" if topic_url.startswith('/') else topic_url
            new_page.goto(full_url)
            time.sleep(3)
            
            # 触发页面统计事件
            self.trigger_page_events(new_page)
            
            # 模拟阅读行为
            self.simulate_reading(new_page)
            
            # 随机点赞
            if random.random() < 0.3:
                self.click_like(new_page)
                
        finally:
            new_page.close()

    def simulate_reading(self, page):
        """模拟阅读行为"""
        try:
            # 获取页面内容信息
            content_info = page.evaluate("""
                () => {
                    const content = document.querySelector('.topic-post .cooked') || 
                                   document.querySelector('.post-content') ||
                                   document.querySelector('.post-body') ||
                                   document.body;
                    return {
                        length: content ? content.textContent.length : 500,
                        height: content ? content.scrollHeight : 2000,
                        wordCount: content ? content.textContent.split(/\\s+/).length : 100,
                    };
                }
            """)
            
            # 基于内容计算阅读时间
            base_time = max(20, min(120, content_info['length'] / 20))
            read_time = base_time * random.uniform(0.8, 1.3)
            
            logger.info(f"📖 预计阅读时间: {read_time:.1f}秒 (长度:{content_info['length']}字符)")
            
            # 分段滚动模拟
            scroll_segments = random.randint(4, 8)
            time_per_segment = read_time / scroll_segments
            
            for segment in range(scroll_segments):
                # 计算滚动位置
                scroll_ratio = (segment + 1) / scroll_segments
                scroll_pos = content_info['height'] * scroll_ratio
                
                # 平滑滚动
                page.evaluate(f"window.scrollTo({{top: {scroll_pos}, behavior: 'smooth'}})")
                
                # 模拟交互
                if random.random() < 0.4:
                    self.simulate_interaction(page)
                
                # 分段停留
                segment_wait = time_per_segment * random.uniform(0.7, 1.2)
                time.sleep(segment_wait)
            
            # 最终滚动到底部
            page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
            time.sleep(random.uniform(2, 4))
            
            logger.info("✅ 阅读完成")
            
        except Exception as e:
            logger.error(f"模拟阅读失败: {str(e)}")
            # 降级到基础浏览
            self.fallback_browsing(page)

    def fallback_browsing(self, page):
        """降级浏览行为"""
        logger.info("使用基础浏览模式")
        for i in range(random.randint(6, 12)):
            # 随机滚动
            scroll_distance = random.randint(300, 700)
            page.evaluate(f"window.scrollBy(0, {scroll_distance})")
            
            # 随机交互
            if random.random() < 0.3:
                self.simulate_interaction(page)
            
            # 随机等待
            wait_time = random.uniform(1, 3)
            time.sleep(wait_time)

    def simulate_interaction(self, page):
        """模拟用户交互"""
        try:
            # 随机鼠标移动
            page.mouse.move(
                random.randint(100, 500),
                random.randint(100, 500)
            )
            time.sleep(0.1)
            
            # 随机点击
            if random.random() < 0.2:
                page.mouse.click(
                    random.randint(100, 500),
                    random.randint(100, 500)
                )
                time.sleep(0.5)
                
        except Exception as e:
            logger.debug(f"模拟交互失败: {str(e)}")

    def trigger_page_events(self, page):
        """触发页面统计事件"""
        try:
            # 触发页面浏览事件
            page.evaluate("""
                () => {
                    window.dispatchEvent(new Event('pageview'));
                    window.dispatchEvent(new Event('load'));
                    if (typeof jQuery !== 'undefined') {
                        jQuery(window).trigger('load');
                        jQuery(document).trigger('ready');
                    }
                }
            """)
            time.sleep(1)
            logger.debug("📊 页面统计事件已触发")
        except Exception as e:
            logger.debug(f"触发页面事件失败: {str(e)}")

    def click_like(self, page):
        """点赞功能"""
        try:
            # 查找可点赞的按钮
            like_buttons = page.query_selector_all('.discourse-reactions-reaction-button')
            for button in like_buttons:
                try:
                    if button.is_enabled():
                        logger.info("找到未点赞按钮，准备点赞")
                        button.click()
                        time.sleep(random.uniform(1, 3))
                        logger.info("点赞成功")
                        return True
                except:
                    continue
            logger.info("未找到可点赞的按钮或已点过赞")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")
        return False

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("获取连接信息")
        page = self.context.new_page()
        try:
            page.goto("https://connect.linux.do/")
            time.sleep(5)
            
            rows = page.query_selector_all("table tr")
            info = []

            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    project = cells[0].text_content().strip()
                    current = cells[1].text_content().strip()
                    requirement = cells[2].text_content().strip()
                    info.append([project, current, requirement])

            print("--------------Connect Info-----------------")
            print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
        finally:
            page.close()

    def run(self):
        """主运行函数"""
        logger.info(f"BROWSE_ENABLED: {BROWSE_ENABLED}")
        
        if not self.login():
            logger.error("登录失败，程序终止")
            self.clear_caches()
            sys.exit(1)

        if BROWSE_ENABLED:
            logger.info("开始执行浏览任务")
            self.click_topic()
            logger.info("✅ 浏览任务完成")
            
            # 更新会话数据
            self.session_data['last_browse'] = datetime.now().isoformat()
            self.session_data['total_browsed'] = self.session_data.get('total_browsed', 0) + 1
            if not self.cache_saved:
                self.save_all_caches()
        else:
            logger.info("跳过浏览任务")

        self.print_connect_info()
        
        # 关闭浏览器
        self.context.close()
        self.browser.close()
        self.playwright.stop()


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Please set LINUXDO_USERNAME and LINUXDO_PASSWORD environment variables")
        exit(1)
    
    logger.info("🚀 LinuxDo 自动化脚本启动 (Playwright修复版)")
    
    try:
        browser = LinuxDoBrowser()
        browser.run()
        logger.info("🔚 脚本执行完成")
    except Exception as e:
        logger.error(f"脚本执行异常: {str(e)}")
        sys.exit(1)
