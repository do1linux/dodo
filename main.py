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
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
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
                return data.get('data', data)
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
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
                'cache_version': '1.0'
            }
            
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            logger.info(f"💾 缓存已保存: {file_name}")
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
    def handle_cloudflare(page, timeout=120):
        """处理Cloudflare验证"""
        logger.info("🛡️ 检查Cloudflare验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                page_title = page.title
                current_url = page.url
                
                # 检查是否已经通过验证
                if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ Cloudflare验证已通过")
                    return True
                
                # 等待验证
                wait_time = random.uniform(5, 10)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒)")
                time.sleep(wait_time)
                
            except Exception as e:
                logger.warning(f"Cloudflare检查异常: {str(e)}")
                time.sleep(5)
        
        logger.warning("⚠️ Cloudflare处理超时，继续后续流程")
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
        
        # 浏览器配置
        platformIdentifier = "Windows NT 10.0; Win64; x64"

        co = (
            ChromiumOptions()
            .headless(HEADLESS)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-dev-shm-usage")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # 加载会话数据
        self.session_data = CacheManager.load_session(self.site_name)

    def strict_check_login_status(self):
        """严格检查登录状态 - 必须在latest页面验证"""
        logger.info("🔍 在latest页面严格验证登录状态...")
        
        # 首先跳转到latest页面
        self.page.get(self.site_config['latest_topics_url'])
        time.sleep(5)
        
        # 处理可能的Cloudflare
        CloudflareHandler.handle_cloudflare(self.page)
        
        try:
            # 方法1: 检查用户元素
            user_selectors = [
                '#current-user',
                '.current-user', 
                'img.avatar',
                '.header-dropdown-toggle'
            ]
            
            for selector in user_selectors:
                try:
                    user_element = self.page.ele(selector, timeout=5)
                    if user_element:
                        logger.success(f"✅ 找到用户元素: {selector}")
                        return True
                except:
                    continue
            
            # 方法2: 检查页面内容中的用户名
            page_content = self.page.html
            if self.username and self.username.lower() in page_content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                return True
            
            # 方法3: 检查登录按钮（反证未登录）
            login_selectors = ['.login-button', 'button:has-text("登录")', '#login-button']
            for selector in login_selectors:
                try:
                    login_btn = self.page.ele(selector, timeout=3)
                    if login_btn:
                        logger.warning(f"❌ 检测到登录按钮: {selector}")
                        return False
                except:
                    continue
            
            logger.warning("⚠️ 无法确定登录状态")
            return False
            
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

    def getTurnstileToken(self):
        """获取Turnstile token"""
        self.page.run_js("try { turnstile.reset() } catch(e) { }")

        turnstileResponse = None

        for i in range(0, 5):
            try:
                turnstileResponse = self.page.run_js(
                    "try { return turnstile.getResponse() } catch(e) { return null }"
                )
                if turnstileResponse:
                    return turnstileResponse

                challengeSolution = self.page.ele("@name=cf-turnstile-response")
                if challengeSolution:
                    challengeWrapper = challengeSolution.parent()
                    challengeIframe = challengeWrapper.shadow_root.ele("tag:iframe")
                    challengeIframeBody = challengeIframe.ele("tag:body").shadow_root
                    challengeButton = challengeIframeBody.ele("tag:input")
                    challengeButton.click()
            except Exception as e:
                logger.warning(f"处理 Turnstile 时出错: {str(e)}")
            time.sleep(1)
        return None

    def login(self):
        """登录网站"""
        logger.info("开始登录")
        
        # 先尝试使用缓存cookies
        cached_cookies = CacheManager.load_cookies(self.site_name)
        if cached_cookies:
            logger.info("🔄 尝试使用缓存cookies")
            try:
                self.page.set.cookies(cached_cookies)
                self.page.get(self.site_config['home_url'])
                time.sleep(5)
                
                if self.strict_check_login_status():
                    logger.success("✅ 使用缓存cookies登录成功")
                    return True
            except Exception as e:
                logger.warning(f"缓存登录失败: {str(e)}")
        
        # 需要重新登录
        logger.info("🔐 开始重新登录流程")
        self.page.get(self.site_config['login_url'])
        time.sleep(5)
        
        # 处理Cloudflare
        CloudflareHandler.handle_cloudflare(self.page)
        
        try:
            # 处理Turnstile验证
            turnstile_token = self.getTurnstileToken()
            if turnstile_token:
                logger.info(f"Turnstile token: {turnstile_token}")
            
            # 截图用于调试
            self.page.get_screenshot(f"login_{self.site_name}.png")
            
            # 输入用户名和密码
            self.page.ele("@id=login-account-name").input(self.username)
            self.page.ele("@id=login-account-password").input(self.password)
            self.page.ele("@id=login-button").click()
            time.sleep(10)
            
            # 严格验证登录状态
            if self.strict_check_login_status():
                logger.info("登录成功")
                
                # 保存cookies和会话
                cookies = self.page.cookies()
                if cookies:
                    CacheManager.save_cookies(cookies, self.site_name)
                
                session_data = {
                    'last_login': datetime.now().isoformat(),
                    'username': self.username,
                    'site': self.site_name
                }
                CacheManager.save_session(session_data, self.site_name)
                
                return True
            else:
                logger.error("登录失败")
                return False
                
        except Exception as e:
            logger.error(f"登录过程中出错: {str(e)}")
            return False

    def click_topic(self):
        """点击浏览主题"""
        # 确保在latest页面
        if not self.page.url.endswith('/latest'):
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
        
        # 再次验证登录状态
        if not self.strict_check_login_status():
            logger.error("❌ 在浏览主题前登录状态验证失败")
            return False
        
        try:
            topic_list = self.page.ele("@id=list-area").eles(".:title")
            logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择10个")
            
            selected_topics = random.sample(topic_list, min(10, len(topic_list)))
            success_count = 0
            
            for i, topic in enumerate(selected_topics):
                try:
                    topic_url = topic.attr("href")
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览第 {i+1} 个主题: {topic_url}")
                    
                    if self.click_one_topic(topic_url):
                        success_count += 1
                    
                    # 随机等待
                    wait_time = random.uniform(3, 8)
                    time.sleep(wait_time)
                    
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
                    continue
            
            logger.info(f"📊 浏览完成: 成功 {success_count}/{len(selected_topics)} 个主题")
            return success_count > 0
            
        except Exception as e:
            logger.error(f"获取主题列表失败: {str(e)}")
            return False

    @retry_decorator()
    def click_one_topic(self, topic_url):
        """浏览单个主题"""
        new_page = self.browser.new_tab()
        try:
            new_page.get(topic_url)
            time.sleep(3)
            
            # 随机决定是否点赞
            if random.random() < 0.003:  
                self.click_like(new_page)
            
            # 浏览帖子内容
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

    def browse_post(self, page):
        """浏览帖子内容"""
        prev_url = None
        
        # 开始自动滚动，最多滚动10次
        for i in range(10):
            # 随机滚动一段距离
            scroll_distance = random.randint(550, 650)
            logger.info(f"向下滚动 {scroll_distance} 像素... (第{i+1}次)")
            page.run_js(f"window.scrollBy(0, {scroll_distance})")

            if random.random() < 0.03:
                logger.success("随机退出浏览")
                break

            # 检查是否到达页面底部
            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                logger.success("已到达页面底部，退出浏览")
                break

            # 动态随机等待
            wait_time = random.uniform(2, 4)
            logger.info(f"等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)

    def click_like(self, page):
        """点赞帖子"""
        try:
            like_button = page.ele(".discourse-reactions-reaction-button")
            if like_button:
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                logger.info("点赞成功")
                time.sleep(random.uniform(1, 2))
            else:
                logger.info("帖子可能已经点过赞了")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    def print_connect_info(self):
        """打印连接信息（仅限linux.do）"""
        if self.site_name != 'linux_do':
            return
            
        logger.info("获取连接信息")
        try:
            page = self.browser.new_tab()
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

            print("--------------Connect Info-----------------")
            print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
            page.close()
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def run(self):
        """运行主流程"""
        logger.info(f"🚀 开始处理站点: {self.site_config['name']}")
        
        try:
            # 登录
            if not self.login():
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False

            # 浏览主题
            if BROWSE_ENABLED:
                logger.info("🌐 开始浏览主题")
                self.click_topic()
                logger.info("完成浏览任务")

            # 打印连接信息
            self.print_connect_info()
            
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
