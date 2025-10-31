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

# ======================== 验证检测器 ========================
class SecurityDetector:
    """安全验证检测器"""
    
    @staticmethod
    def detect_security_challenges(page):
        """检测登录页面上的安全验证类型"""
        logger.info("🛡️ 开始检测登录页面的安全验证...")
        
        challenges = {
            'cloudflare_turnstile': False,
            'google_recaptcha': False,
            'hcaptcha': False,
            'cloudflare_protection': False,
            'traditional_captcha': False,
            'other_security': False
        }
        
        try:
            # 获取页面HTML内容
            page_html = page.html
            page_url = page.url
            page_title = page.title
            
            logger.info(f"📄 页面标题: {page_title}")
            logger.info(f"🌐 页面URL: {page_url}")
            
            # 检测Cloudflare Turnstile
            turnstile_indicators = [
                'challenges.cloudflare.com/cdn-cgi/challenge-platform',
                'turnstile',
                'cf-turnstile',
                'data-sitekey',
                'data-action'
            ]
            
            for indicator in turnstile_indicators:
                if indicator in page_html.lower():
                    challenges['cloudflare_turnstile'] = True
                    logger.warning(f"🔍 检测到Cloudflare Turnstile: {indicator}")
                    break
            
            # 检测Google reCAPTCHA
            recaptcha_indicators = [
                'google.com/recaptcha',
                'g-recaptcha',
                'recaptcha/api',
                'data-sitekey'
            ]
            
            for indicator in recaptcha_indicators:
                if indicator in page_html.lower():
                    challenges['google_recaptcha'] = True
                    logger.warning(f"🔍 检测到Google reCAPTCHA: {indicator}")
                    break
            
            # 检测hCaptcha
            hcaptcha_indicators = [
                'hcaptcha.com',
                'h-captcha',
                'hcaptcha/api'
            ]
            
            for indicator in hcaptcha_indicators:
                if indicator in page_html.lower():
                    challenges['hcaptcha'] = True
                    logger.warning(f"🔍 检测到hCaptcha: {indicator}")
                    break
            
            # 检测传统Cloudflare保护
            cloudflare_indicators = [
                'checking your browser',
                'ddos protection',
                'cloudflare',
                'ray id',
                'please wait'
            ]
            
            for indicator in cloudflare_indicators:
                if indicator in page_html.lower() or indicator in page_title.lower():
                    challenges['cloudflare_protection'] = True
                    logger.warning(f"🔍 检测到Cloudflare保护: {indicator}")
                    break
            
            # 检测传统验证码
            captcha_indicators = [
                'captcha',
                '验证码',
                'captcha-image',
                'input[name="captcha"]'
            ]
            
            for indicator in captcha_indicators:
                if indicator in page_html.lower():
                    challenges['traditional_captcha'] = True
                    logger.warning(f"🔍 检测到传统验证码: {indicator}")
                    break
            
            # 检测其他安全措施
            other_security_indicators = [
                'security check',
                'bot protection',
                'anti-bot',
                'rate limiting'
            ]
            
            for indicator in other_security_indicators:
                if indicator in page_html.lower():
                    challenges['other_security'] = True
                    logger.warning(f"🔍 检测到其他安全措施: {indicator}")
                    break
            
            # 打印检测总结
            SecurityDetector.print_detection_summary(challenges)
            
            return challenges
            
        except Exception as e:
            logger.error(f"安全验证检测失败: {str(e)}")
            return challenges
    
    @staticmethod
    def print_detection_summary(challenges):
        """打印检测结果总结"""
        logger.info("📊 安全验证检测总结:")
        
        detected_challenges = [name for name, detected in challenges.items() if detected]
        
        if detected_challenges:
            logger.warning("⚠️ 检测到的安全验证:")
            for challenge in detected_challenges:
                logger.warning(f"   - {challenge.replace('_', ' ').title()}")
            
            if any([challenges['cloudflare_turnstile'], challenges['google_recaptcha'], challenges['hcaptcha']]):
                logger.error("🚨 检测到高级验证码，在无头模式下可能无法自动解决")
            else:
                logger.info("✅ 未检测到高级验证码，可以尝试自动登录")
        else:
            logger.success("✅ 未检测到明显的安全验证")
    
    @staticmethod
    def can_auto_login(challenges):
        """判断是否可以自动登录"""
        # 如果检测到高级验证码，在无头模式下很难自动解决
        advanced_captchas = [
            challenges['cloudflare_turnstile'],
            challenges['google_recaptcha'], 
            challenges['hcaptcha']
        ]
        
        if any(advanced_captchas) and HEADLESS:
            logger.error("❌ 检测到高级验证码且在无头模式下，无法自动登录")
            return False
        
        return True

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

# ======================== 智能登录策略 ========================
class SmartLoginStrategy:
    """智能登录策略"""
    
    @staticmethod
    def evaluate_login_options(challenges, has_valid_cookies):
        """评估登录选项"""
        logger.info("🤔 评估登录策略...")
        
        # 策略1: 如果有有效cookie，优先使用
        if has_valid_cookies:
            logger.success("🎯 策略1: 使用缓存cookie登录")
            return "use_cookie"
        
        # 策略2: 检查是否可以自动登录
        if SecurityDetector.can_auto_login(challenges):
            logger.info("🎯 策略2: 尝试自动登录")
            return "auto_login"
        
        # 策略3: 备用方案
        logger.warning("🎯 策略3: 备用方案 - 等待cookie缓存")
        return "fallback"

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
            .set_argument("--disable-dev-shm-usage")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()

    def strict_check_login_status(self):
        """严格检查登录状态 - 在latest页面验证用户元素"""
        logger.info("🔍 在latest页面严格验证登录状态...")
        
        # 确保在latest页面
        if not self.page.url.endswith('/latest'):
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
        
        # 处理可能的Cloudflare
        CloudflareHandler.handle_cloudflare(self.page)
        
        try:
            # 方法1: 检查用户头像元素
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
                        return True
                except:
                    continue
            
            # 方法2: 检查用户下拉菜单
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
                        logger.success(f"✅ 找到用户菜单元素: {selector}")
                        return True
                except:
                    continue
            
            # 方法3: 检查页面内容中的用户名
            if self.username:
                page_content = self.page.html
                if self.username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面内容中找到用户名: {self.username}")
                    return True
            
            # 方法4: 检查登录按钮（反证未登录）
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
                        logger.warning(f"❌ 检测到登录按钮: {selector}")
                        return False
                except:
                    continue
            
            logger.warning("⚠️ 无法确定登录状态，假设未登录")
            return False
            
        except Exception as e:
            logger.error(f"登录状态检查失败: {str(e)}")
            return False

    def try_cookie_login(self):
        """尝试使用缓存的cookies登录"""
        logger.info("🔄 尝试使用缓存cookies登录")
        
        cached_cookies = CacheManager.load_cookies(self.site_name)
        if not cached_cookies:
            logger.info("❌ 没有找到有效的缓存cookies")
            return False
        
        try:
            # 设置cookies
            self.page.set.cookies(cached_cookies)
            
            # 跳转到latest页面验证登录状态
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
            
            # 处理可能的Cloudflare
            CloudflareHandler.handle_cloudflare(self.page)
            
            # 严格验证登录状态
            if self.strict_check_login_status():
                logger.success("✅ 使用缓存cookies登录成功")
                return True
            else:
                logger.warning("❌ 缓存cookies已失效")
                return False
                
        except Exception as e:
            logger.error(f"缓存登录失败: {str(e)}")
            return False

    def analyze_login_page(self):
        """分析登录页面，检测安全验证"""
        logger.info("🔍 分析登录页面...")
        
        # 导航到登录页面
        self.page.get(self.site_config['login_url'])
        time.sleep(5)
        
        # 处理Cloudflare
        CloudflareHandler.handle_cloudflare(self.page)
        
        # 检测安全验证
        challenges = SecurityDetector.detect_security_challenges(self.page)
        
        # 截图保存当前页面状态
        self.page.get_screenshot(f"login_analysis_{self.site_name}.png")
        
        return challenges

    def attempt_simple_login(self):
        """尝试简单登录（不处理复杂验证码）"""
        logger.info("🔐 尝试简单登录...")
        
        try:
            # 输入用户名和密码
            username_input = self.page.ele("@id=login-account-name", timeout=10)
            password_input = self.page.ele("@id=login-account-password", timeout=10)
            login_button = self.page.ele("@id=login-button", timeout=10)
            
            if not all([username_input, password_input, login_button]):
                logger.error("❌ 找不到登录表单元素")
                return False
            
            # 清空并输入凭据
            username_input.input('')
            username_input.input(self.username)
            
            password_input.input('')
            password_input.input(self.password)
            
            # 点击登录按钮
            login_button.click()
            time.sleep(10)
            
            # 处理可能的Cloudflare
            CloudflareHandler.handle_cloudflare(self.page)
            
            # 验证登录是否成功
            if self.strict_check_login_status():
                logger.success("✅ 简单登录成功")
                
                # 保存cookies
                cookies = self.page.cookies()
                if cookies:
                    CacheManager.save_cookies(cookies, self.site_name)
                    logger.info("💾 保存新的cookies")
                
                return True
            else:
                logger.error("❌ 简单登录失败")
                return False
            
        except Exception as e:
            logger.error(f"简单登录过程出错: {str(e)}")
            return False

    def ensure_logged_in(self):
        """确保用户已登录 - 智能策略"""
        logger.info("🎯 智能登录策略启动")
        
        # 检查cookies文件是否存在
        if CacheManager.cookies_exist(self.site_name):
            logger.info("📦 检测到cookies文件，尝试使用")
            if self.try_cookie_login():
                return True
            else:
                logger.warning("❌ cookies文件无效，继续其他登录方式")
        else:
            logger.info("❌ 未找到cookies文件，需要完整登录")
        
        # 策略2: 分析登录页面
        logger.info("🔄 分析登录页面")
        challenges = self.analyze_login_page()
        
        # 评估登录选项
        strategy = SmartLoginStrategy.evaluate_login_options(
            challenges, 
            has_valid_cookies=False
        )
        
        if strategy == "auto_login":
            # 尝试简单登录
            if self.attempt_simple_login():
                return True
        elif strategy == "fallback":
            # 备用方案：等待并重试
            logger.info("⏳ 备用方案：等待后重试...")
            time.sleep(10)
            if self.attempt_simple_login():
                return True
        
        logger.error("❌ 所有登录策略均失败")
        return False

    @retry_decorator()
    def click_one_topic(self, topic_url):
        """浏览单个主题"""
        new_page = self.browser.new_tab()
        try:
            new_page.get(topic_url)
            time.sleep(3)
            
            # 随机决定是否点赞 (0.3%概率)
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

    def click_topic(self):
        """点击浏览主题"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用，跳过")
            return True
            
        logger.info("🌐 开始浏览主题")
        
        # 确保在latest页面
        if not self.page.url.endswith('/latest'):
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)
        
        try:
            # 获取主题列表
            topic_list = self.page.eles(".:title")
            if not topic_list:
                logger.error("❌ 没有找到主题列表")
                return False
            
            logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择10个")
            
            # 随机选择主题
            selected_topics = random.sample(topic_list, min(10, len(topic_list)))
            success_count = 0
            
            for i, topic in enumerate(selected_topics):
                try:
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                        
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
            # 第一步：确保登录状态
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_config['name']} 登录失败，跳过后续操作")
                
                # 即使登录失败，也尝试获取连接信息
                logger.info("🔄 尝试获取连接信息...")
                self.print_connect_info()
                return False

            # 第二步：浏览主题（仅在登录成功后）
            self.click_topic()

            # 第三步：打印连接信息
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
    
    # 如果有成功站点或者只是登录失败但获取了信息，不算完全失败
    if success_sites or (failed_sites and "获取连接信息失败" not in str(failed_sites)):
        logger.success("🎉 部分任务完成")
        sys.exit(0)
    else:
        logger.error("💥 所有任务失败")
        sys.exit(1)

if __name__ == "__main__":
    main()
