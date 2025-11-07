import os
import sys
import time
import random
import json
import traceback
import functools
from datetime import datetime
from urllib.parse import urljoin
from DrissionPage import ChromiumPage, ChromiumOptions
from loguru import logger
from tabulate import tabulate

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
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
    }
]

PAGE_TIMEOUT = 120
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 10

# 固定使用 Windows User-Agent
PLATFORM_IDENTIFIER = "Windows NT 10.0; Win64; x64"
USER_AGENT = f'Mozilla/5.0 ({PLATFORM_IDENTIFIER}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

# 扩展路径
EXTENSION_PATH = os.path.abspath("turnstilePatch")
EXTENSION_ENABLED = os.path.exists(EXTENSION_PATH)

# Cloudflare Turnstile 处理脚本
TURNSTILE_SCRIPT = """
// 改进的 Turnstile token 获取函数
function getTurnstileToken() {
    return new Promise((resolve, reject) => {
        console.log('开始获取 Turnstile token...');
        
        // 方法1: 检查全局 turnstile 对象
        if (window.turnstile) {
            console.log('检测到 window.turnstile 对象');
            try {
                const response = window.turnstile.getResponse();
                if (response && response.length > 0) {
                    console.log('通过 turnstile.getResponse() 获取到 token');
                    resolve(response);
                    return;
                }
            } catch (e) {
                console.log('turnstile.getResponse() 出错:', e);
            }
        }
        
        // 方法2: 检查隐藏的 input 字段
        const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
        if (hiddenInput && hiddenInput.value) {
            console.log('通过隐藏字段获取到 token');
            resolve(hiddenInput.value);
            return;
        }
        
        // 方法3: 轮询等待 token 出现
        let attempts = 0;
        const maxAttempts = 15;
        
        function pollForToken() {
            attempts++;
            console.log(`轮询等待 token (${attempts}/${maxAttempts})`);
            
            if (window.turnstile) {
                try {
                    const response = window.turnstile.getResponse();
                    if (response && response.length > 0) {
                        resolve(response);
                        return;
                    }
                } catch (e) {}
            }
            
            const input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input && input.value) {
                resolve(input.value);
                return;
            }
            
            if (attempts >= maxAttempts) {
                reject(new Error(`轮询 ${maxAttempts} 次后仍未获取到 token`));
                return;
            }
            
            setTimeout(pollForToken, 2000);
        }
        
        pollForToken();
    });
}

// 设置 Turnstile token 到表单
function setTurnstileToken(token) {
    console.log('设置 Turnstile token 到表单');
    
    let existingInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (existingInput) {
        existingInput.value = token;
        console.log('已设置到现有字段');
    } else {
        const newInput = document.createElement('input');
        newInput.type = 'hidden';
        newInput.name = 'cf-turnstile-response';
        newInput.value = token;
        
        const form = document.querySelector('form');
        if (form) {
            form.appendChild(newInput);
            console.log('已创建新字段并添加到表单');
        } else {
            console.log('未找到表单，无法设置 token');
            return false;
        }
    }
    return true;
}

// 主函数
async function handleTurnstile() {
    try {
        console.log('开始处理 Turnstile 验证...');
        const token = await getTurnstileToken();
        console.log('成功获取 Turnstile token:', token.substring(0, 20) + '...');
        
        const success = setTurnstileToken(token);
        if (success) {
            console.log('Turnstile token 已设置到表单');
            return { success: true, token: token };
        } else {
            return { success: false, error: '无法设置 token 到表单' };
        }
    } catch (error) {
        console.error('处理 Turnstile 验证失败:', error);
        return { success: false, error: error.message };
    }
}

return handleTurnstile();
"""

# 重试装饰器
def retry_decorator(max_retries=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"重试 {func.__name__} ({attempt + 1}/{max_retries}): {str(e)}")
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator

class CacheManager:
    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    return json.load(f)
            return None
        except Exception:
            return None

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        try:
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @staticmethod
    def load_cf_cookies(site_name):
        """加载 Cloudflare cookies 缓存"""
        return CacheManager.load_site_cache(site_name, 'cf_cookies')

    @staticmethod
    def save_cf_cookies(data, site_name):
        """保存 Cloudflare cookies 缓存"""
        return CacheManager.save_site_cache(data, site_name, 'cf_cookies')

class EnhancedBrowserManager:
    @staticmethod
    def init_browser(site_name):
        try:
            co = ChromiumOptions()
            
            # 优化的浏览器参数
            browser_args = [
                '--no-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--headless=new', 
                '--disable-gpu',
                '--disable-extensions',
                '--disable-plugins',
                '--disable-web-security',
                '--allow-running-insecure-content',
                '--disable-features=VizDisplayCompositor',
                '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows'
            ]
            
            for arg in browser_args:
                co.set_argument(arg)
            
            # 只有在扩展存在时才加载
            if EXTENSION_ENABLED:
                logger.info(f"🔧 加载扩展: {EXTENSION_PATH}")
                try:
                    co.add_extension(EXTENSION_PATH)
                except Exception as e:
                    logger.warning(f"⚠️ 扩展加载失败，继续无扩展运行: {str(e)}")
            else:
                logger.warning("⚠️ 扩展目录不存在，跳过扩展加载")
            
            co.set_user_agent(USER_AGENT)
            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=PAGE_TIMEOUT)
            
            # 只加载 Cloudflare cookies 缓存
            cf_cookies = CacheManager.load_cf_cookies(site_name)
            if cf_cookies:
                page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个 Cloudflare 缓存cookies")
            
            # 反自动化检测
            page.run_js("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
            delete navigator.__proto__.webdriver;
            """)
            
            return page
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

class EnhancedSiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.page = None
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.topic_count = 0
        self.successful_browsed = 0

    def run_for_site(self):
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False

        try:
            self.page = EnhancedBrowserManager.init_browser(self.site_config['name'])
            
            # 强制每次都必须登录
            if self.force_login_required():
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                self.perform_browsing_actions_improved()
                self.get_connect_info_fixed()
                self.save_verification_data_only()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False

        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            self.cleanup()

    def force_login_required(self):
        """强制要求每次都必须登录"""
        logger.info("🔐 强制登录流程 - 每次都必须重新登录")
        
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")
            
            if self.enhanced_login_process_with_turnstile():
                return True

            if attempt < RETRY_TIMES - 1:
                wait_time = 10 * (attempt + 1)
                logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)

        return False

    def enhanced_login_process_with_turnstile(self):
        """增强的登录流程，专门处理 Turnstile 验证"""
        try:
            logger.info("🔐 开始完整登录流程（含 Turnstile 处理）")
            
            # 清除可能的旧会话
            self.page.get("about:blank")
            time.sleep(2)
            
            self.page.get(self.site_config['login_url'])
            time.sleep(8)

            # 分析登录页面状态
            self.analyze_login_page()

            # 检查是否有 Cloudflare Turnstile 验证
            turnstile_detected = self.detect_turnstile_challenge()
            if turnstile_detected:
                logger.info("🛡️ 检测到 Cloudflare Turnstile 验证")
                if self.enhanced_turnstile_handler():
                    logger.info("✅ Turnstile 验证处理成功")
                else:
                    logger.error("❌ Turnstile 验证处理失败")
                    return False

            if not self.wait_for_login_form():
                logger.error("❌ 登录表单未加载完成")
                return False

            username = self.credentials['username']
            password = self.credentials['password']

            # 使用更健壮的元素定位
            username_field = self.page.ele("@id=login-account-name", timeout=20)
            password_field = self.page.ele("@id=login-account-password", timeout=20)
            login_button = self.page.ele("@id=login-button", timeout=20)

            if not all([username_field, password_field, login_button]):
                logger.error("❌ 登录表单元素未找到")
                return self.alternative_login_method()

            # 模拟人类输入
            self.human_like_input(username_field, username)
            time.sleep(random.uniform(1, 3))
            self.human_like_input(password_field, password)
            time.sleep(random.uniform(1, 2))

            # 再次检查是否有 Turnstile 验证
            if self.detect_turnstile_challenge():
                logger.info("🛡️ 输入后检测到 Turnstile 验证")
                if self.enhanced_turnstile_handler():
                    logger.info("✅ 输入后 Turnstile 验证处理成功")

            # 点击登录按钮
            login_button.click()
            time.sleep(10)

            # 严格检查登录结果
            return self.strict_check_login_status()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            traceback.print_exc()
            return False

    def analyze_login_page(self):
        """分析登录页面状态"""
        try:
            logger.info("🔍 分析登录页面状态...")
            
            # 检查机器人验证元素
            bot_detection_selectors = [
                '.cf-turnstile',
                '.g-recaptcha',
                '[data-sitekey]',
                '.h-captcha',
                '.challenge-form',
                '#cf-challenge',
                '.verification-form',
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]'
            ]
            
            found_bot_elements = []
            for selector in bot_detection_selectors:
                elements = self.page.eles(selector)
                if elements:
                    found_bot_elements.append(selector)
                    logger.warning(f"🚨 发现机器人验证元素: {selector}")
            
            if found_bot_elements:
                logger.warning(f"🚨 页面包含以下机器人验证: {', '.join(found_bot_elements)}")
            else:
                logger.info("✅ 未发现明显的机器人验证元素")
            
            # 检查登录相关元素
            login_selectors = [
                '#login-account-name',
                '#username', 
                'input[name="username"]',
                'input[type="text"]',
                '#login-account-password',
                '#password',
                'input[name="password"]',
                'input[type="password"]',
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]'
            ]
            
            found_login_elements = []
            for selector in login_selectors:
                elements = self.page.eles(selector)
                if elements:
                    found_login_elements.append(selector)
                    logger.info(f"✅ 发现登录元素: {selector}")
            
            logger.info(f"📊 登录页面分析完成，发现 {len(found_login_elements)} 个登录相关元素")
            
        except Exception as e:
            logger.error(f"登录页面分析失败: {str(e)}")

    def detect_turnstile_challenge(self):
        """检测是否存在 Cloudflare Turnstile 验证"""
        try:
            turnstile_selectors = [
                'iframe[src*="challenges.cloudflare.com"]',
                'div[class*="turnstile"]',
                'input[name="cf-turnstile-response"]',
                '.cf-turnstile',
                '[data-sitekey]'
            ]
            
            for selector in turnstile_selectors:
                elements = self.page.eles(selector)
                if elements:
                    logger.info(f"🛡️ 检测到 Turnstile 元素: {selector}")
                    return True
            
            page_text = self.page.html.lower()
            turnstile_keywords = ['cloudflare', 'turnstile', 'challenge', 'verifying', 'captcha']
            if any(keyword in page_text for keyword in turnstile_keywords):
                logger.info("🛡️ 检测到 Turnstile 相关关键词")
                return True
                
            return False
        except Exception as e:
            logger.debug(f"检测 Turnstile 验证失败: {str(e)}")
            return False

    def enhanced_turnstile_handler(self):
        """增强的 Turnstile 验证处理器"""
        try:
            logger.info("🔄 开始处理 Turnstile 验证...")
            
            time.sleep(8)
            
            logger.info("🔄 尝试 Turnstile 处理脚本...")
            result = self.page.run_js(TURNSTILE_SCRIPT)
            
            if result and result.get('success'):
                token = result.get('token')
                logger.info(f"✅ 成功获取 Turnstile token: {token[:20]}...")
                return True
            else:
                error_msg = result.get('error', '未知错误') if result else '无结果'
                logger.warning(f"⚠️ 脚本失败: {error_msg}")
                return self.manual_turnstile_wait()
                
        except Exception as e:
            logger.error(f"❌ 处理 Turnstile 验证时发生异常: {str(e)}")
            return False

    def manual_turnstile_wait(self):
        """手动等待 Turnstile 验证完成"""
        try:
            logger.info("⏳ 手动等待 Turnstile 验证完成...")
            
            for i in range(15):
                time.sleep(2)
                
                if not self.detect_turnstile_challenge():
                    logger.info("✅ Turnstile 验证似乎已完成")
                    return True
                    
                try:
                    token_input = self.page.ele('@name=cf-turnstile-response')
                    if token_input and token_input.value:
                        logger.info("✅ 检测到自动填充的 Turnstile token")
                        return True
                except:
                    pass
                    
                logger.info(f"⏳ 等待 Turnstile 完成... ({i+1}/15)")
            
            logger.error("❌ 手动等待 Turnstile 超时")
            return False
            
        except Exception as e:
            logger.error(f"❌ 手动等待失败: {str(e)}")
            return False

    def wait_for_login_form(self, max_wait=30):
        """等待登录表单加载"""
        logger.info("⏳ 等待登录表单...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                username_selectors = [
                    '#login-account-name',
                    '#username',
                    'input[name="username"]',
                    'input[type="text"]'
                ]
                
                for selector in username_selectors:
                    element = self.page.ele(selector)
                    if element and element.is_displayed():
                        logger.success(f"✅ 找到登录表单: {selector}")
                        return True
                
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                time.sleep(2)
        
        logger.error("❌ 登录表单等待超时")
        return False

    def alternative_login_method(self):
        """备用登录方法"""
        try:
            logger.info("🔄 尝试备用登录方法")
            username = self.credentials['username']
            password = self.credentials['password']
            
            username_field = self.page.ele('@name=username', timeout=15)
            password_field = self.page.ele('@name=password', timeout=15)
            login_button = self.page.ele('@type=submit', timeout=15)
            
            if all([username_field, password_field, login_button]):
                self.human_like_input(username_field, username)
                time.sleep(1)
                self.human_like_input(password_field, password)
                time.sleep(1)
                login_button.click()
                time.sleep(10)
                return self.strict_check_login_status()
                
            return False
        except Exception as e:
            logger.debug(f"备用登录方法失败: {str(e)}")
            return False

    def human_like_input(self, element, text):
        """模拟人类输入"""
        try:
            element.clear()
            time.sleep(0.5)
            for char in text:
                element.input(char)
                time.sleep(random.uniform(0.05, 0.2))
        except Exception as e:
            logger.warning(f"输入时发生错误: {str(e)}")
            element.input(text)

    def strict_check_login_status(self):
        """严格检查登录状态 - 必须检测到用户名"""
        username = self.credentials['username']
        logger.info(f"🔍 严格检查登录状态，查找用户名: {username}")

        time.sleep(3)

        # 方法1: 检查用户菜单
        try:
            user_menu = self.page.ele("@id=current-user", timeout=10)
            if user_menu:
                user_text = user_menu.text
                if username.lower() in user_text.lower():
                    logger.success("✅ 通过用户菜单验证登录成功")
                    return True
        except:
            pass

        # 方法2: 在页面内容中搜索用户名
        content = self.page.html
        if username.lower() in content.lower():
            logger.success(f"✅ 在页面内容中找到用户名: {username}")
            return True

        # 方法3: 访问个人资料页面验证
        try:
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page.get(profile_url)
            time.sleep(3)
            
            profile_content = self.page.html
            if username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                self.page.get(self.site_config['latest_topics_url'])
                return True
        except Exception as e:
            logger.debug(f"个人资料页面验证失败: {str(e)}")

        # 方法4: 检查当前URL是否还在登录页面
        current_url = self.page.url.lower()
        if 'login' in current_url:
            logger.error("❌ 仍然在登录页面，登录失败")
            return False

        logger.error(f"❌ 未在页面中找到用户名: {username}")
        return False

    def perform_browsing_actions_improved(self):
        """改进的浏览操作，确保被网站记录"""
        try:
            logger.info("🌐 开始浏览操作...")
            
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            topic_list = self.get_topic_list_improved()
            if not topic_list:
                logger.warning("❌ 未找到主题链接")
                return
            
            self.topic_count = len(topic_list)
            logger.info(f"📚 发现 {self.topic_count} 个主题帖")
            
            browse_count = min(MAX_TOPICS_TO_BROWSE, len(topic_list))
            selected_topics = random.sample(topic_list, browse_count)
            
            logger.info(f"🎯 准备浏览 {browse_count} 个主题")
            
            for i, topic in enumerate(selected_topics, 1):
                logger.info(f"📖 浏览进度: {i}/{browse_count}")
                if self.click_one_topic(topic):
                    self.successful_browsed += 1
                
                if i < browse_count:
                    delay = random.uniform(3, 8)
                    logger.info(f"⏳ 等待 {delay:.1f} 秒后浏览下一个主题...")
                    time.sleep(delay)
            
            logger.success(f"✅ 完成浏览 {self.successful_browsed}/{browse_count} 个主题")
            
        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    def get_topic_list_improved(self):
        """改进的主题列表获取"""
        try:
            # 使用 @id=list-area 选择器
            list_area = self.page.ele("@id=list-area", timeout=10)
            if list_area:
                topics = list_area.eles(".:title")
                if topics:
                    logger.info(f"✅ 使用 @id=list-area 找到 {len(topics)} 个主题")
                    return topics
            
            # 备用方法: 直接查找所有主题链接
            all_links = self.page.eles('tag:a')
            topic_links = []
            for link in all_links:
                href = link.attr("href", "")
                if href and '/t/' in href and len(link.text.strip()) > 5:
                    topic_links.append(link)
            
            if topic_links:
                logger.info(f"✅ 使用链接过滤找到 {len(topic_links)} 个主题")
                return topic_links
                
            logger.warning("❌ 未找到主题链接")
            return []
            
        except Exception as e:
            logger.error(f"获取主题列表失败: {str(e)}")
            return []

    def click_one_topic(self, topic):
        """点击并浏览单个主题 - 修复了 to_tab 问题"""
        try:
            topic_href = topic.attr("href")
            if not topic_href:
                return False
                
            if topic_href.startswith('/'):
                full_url = urljoin(self.site_config['base_url'], topic_href)
            else:
                full_url = topic_href
                
            logger.info(f"🔗 访问: {full_url}")
            
            # 使用 new_tab 而不是 to_tab
            new_tab = self.page.new_tab()
            new_tab.get(full_url)
            time.sleep(3)
            
            # 执行浏览
            self.browse_post(new_tab)
            
            # 关闭标签页
            new_tab.close()
            logger.info(f"✅ 成功浏览主题")
            return True
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            try:
                if 'new_tab' in locals():
                    new_tab.close()
            except:
                pass
            return False

    def browse_post(self, page):
        """浏览帖子内容 - 模拟真实用户行为"""
        scroll_actions = random.randint(8, 15)
        
        for i in range(scroll_actions):
            # 随机滚动距离 550-650 像素
            scroll_pixels = random.randint(550, 650)
            page.scroll.down(scroll_pixels)
            
            read_time = random.uniform(2, 4)
            time.sleep(read_time)
            
            if random.random() < 0.15:
                self.random_interaction(page)
            
            # 检查是否到达底部
            at_bottom = page.run_js(
                "return window.innerHeight + window.scrollY >= document.body.scrollHeight - 100"
            )
            
            if at_bottom and random.random() < 0.7:
                logger.info("📄 到达页面底部，停止滚动")
                break
                
            if random.random() < 0.08:
                logger.info("🎲 随机提前退出浏览")
                break

    def random_interaction(self, page):
        """随机互动"""
        try:
            x = random.randint(50, 800)
            y = random.randint(50, 600)
            page.run_js(f"""
            var elem = document.elementFromPoint({x}, {y});
            if (elem) {{
                var event = new MouseEvent('mousemove', {{
                    clientX: {x},
                    clientY: {y},
                    bubbles: true
                }});
                elem.dispatchEvent(event);
            }}
            """)
        except:
            pass

    def get_connect_info_fixed(self):
        """修复的连接信息获取"""
        logger.info("🔗 获取连接信息")
        
        if not self.strict_check_login_status():
            logger.warning("⚠️ 连接信息页面访问前需要重新登录")
            if not self.enhanced_login_process_with_turnstile():
                logger.error("❌ 重新登录失败，无法获取连接信息")
                return
        
        try:
            logger.info(f"🔗 访问连接信息页面: {self.site_config['connect_url']}")
            self.page.get(self.site_config['connect_url'])
            time.sleep(8)
            
            current_url = self.page.url
            page_title = self.page.title
            
            logger.info(f"🌐 当前URL: {current_url}")
            logger.info(f"📄 页面标题: {page_title}")
            
            if 'login' in current_url or '登录' in page_title:
                logger.warning("⚠️ 被重定向到登录页面，需要重新登录")
                if not self.enhanced_login_process_with_turnstile():
                    logger.error("❌ 重新登录失败，无法获取连接信息")
                    return
                
                self.page.get(self.site_config['connect_url'])
                time.sleep(8)
            
            # 保存页面HTML用于调试
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = f"connect_fixed_{self.site_config['name']}_{timestamp}.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(self.page.html)
            logger.info(f"💾 已保存HTML: {html_path}")
            
            # 提取连接信息
            info = self.extract_connect_data_simple(self.page)
            if info:
                self.display_connect_info(info, "简单提取")
                return
            
            info = self.extract_connect_data_advanced(self.page)
            if info:
                self.display_connect_info(info, "高级提取")
                return
            
            logger.error("💥 无法获取连接信息")
                
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    def extract_connect_data_simple(self, page):
        """简单提取连接数据"""
        try:
            tables = page.eles("tag:table")
            
            for table in tables:
                rows = table.eles("tag:tr")
                info = []
                
                for row in rows:
                    th_cells = row.eles("tag:th")
                    if th_cells and len(th_cells) >= 3:
                        continue
                        
                    cells = row.eles("tag:td")
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        
                        if project and (current or requirement):
                            info.append([project, current, requirement])
                
                if info:
                    return info
                    
            return []
        except Exception as e:
            logger.debug(f"简单提取失败: {str(e)}")
            return []

    def extract_connect_data_advanced(self, page):
        """高级提取连接数据"""
        try:
            all_text = page.run_js("return document.body.innerText")
            
            keywords = ['访问次数', '回复的话题', '浏览的话题', '已读帖子', '点赞', '获赞']
            found_keywords = [kw for kw in keywords if kw in all_text]
            
            if found_keywords:
                logger.info(f"✅ 找到连接信息关键词: {found_keywords}")
            else:
                logger.warning("❌ 未找到连接信息关键词")
                return []
            
            info = []
            all_elements = page.eles("tag:tr, tag:div, tag:li, tag:p")
            
            for elem in all_elements:
                try:
                    text = elem.text.strip()
                    if any(keyword in text for keyword in keywords):
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        
                        if len(lines) >= 2:
                            project = lines[0]
                            current = ""
                            requirement = ""
                            
                            for line in lines[1:]:
                                if any(indicator in line for indicator in ['%', '/', '≥', '>', '<']):
                                    current = line
                                elif '要求' in line or '需要' in line or '至少' in line:
                                    requirement = line
                            
                            if project and (current or requirement):
                                info.append([project, current, requirement])
                except:
                    continue
            
            unique_info = []
            seen = set()
            for item in info:
                key = tuple(item)
                if key not in seen:
                    seen.add(key)
                    unique_info.append(item)
            
            return unique_info
            
        except Exception as e:
            logger.debug(f"高级提取失败: {str(e)}")
            return []

    def display_connect_info(self, info, method):
        """显示连接信息"""
        print("=" * 60)
        print(f"📊 {self.site_config['name']} Connect 连接信息 ({method})")
        print("=" * 60)
        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="grid"))
        print("=" * 60)
        logger.success(f"✅ 连接信息获取成功 ({method}) - 找到 {len(info)} 个项目")

    def save_verification_data_only(self):
        """只保存验证数据，不保存登录状态"""
        try:
            cookies = self.page.cookies()
            if cookies:
                cf_cookies = []
                for cookie in cookies:
                    if any(keyword in cookie.get('name', '').lower() for keyword in 
                          ['cf_', 'cloudflare', '__cf', '_cf']):
                        cf_cookies.append(cookie)
                
                if cf_cookies:
                    CacheManager.save_cf_cookies(cf_cookies, self.site_config['name'])
                    logger.info(f"💾 保存 {len(cf_cookies)} 个 Cloudflare 验证cookies")
            
            logger.success(f"✅ 验证数据已保存 (发现主题: {self.topic_count}, 成功浏览: {self.successful_browsed})")

        except Exception as e:
            logger.error(f"保存验证数据失败: {str(e)}")

    def cleanup(self):
        try:
            if self.page:
                self.page.quit()
        except Exception as e:
            logger.debug(f"清理资源: {str(e)}")

def main():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    logger.info("🚀 LinuxDo自动化脚本启动 - Turnstile验证增强版")
    logger.info(f"🔧 平台: {PLATFORM_IDENTIFIER}")
    logger.info(f"🔧 User-Agent: {USER_AGENT}")
    logger.info(f"🔧 扩展状态: {'已启用' if EXTENSION_ENABLED else '未启用'}")

    target_sites = SITES
    results = []

    try:
        for site_config in target_sites:
            logger.info(f"🎯 处理站点: {site_config['name']}")

            automator = EnhancedSiteAutomator(site_config)
            success = automator.run_for_site()

            results.append({
                'site': site_config['name'],
                'success': success
            })

            if site_config != target_sites[-1]:
                delay = random.uniform(15, 30)
                logger.info(f"⏳ 等待 {delay:.1f} 秒后处理下一个站点...")
                time.sleep(delay)

        logger.info("📊 执行结果汇总:")
        table_data = [[r['site'], "✅ 成功" if r['success'] else "❌ 失败"] for r in results]
        print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))

        success_count = sum(1 for r in results if r['success'])
        logger.success(f"🎉 完成: {success_count}/{len(results)} 个站点成功")

    except Exception as e:
        logger.critical(f"💥 主流程异常: {str(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
