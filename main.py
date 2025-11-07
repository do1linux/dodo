import os
import sys
import time
import random
import json
import traceback
from datetime import datetime
from urllib.parse import urljoin
from loguru import logger
from tabulate import tabulate
from DrissionPage import ChromiumPage, ChromiumOptions

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

IS_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'
HEADLESS_MODE = True if IS_GITHUB_ACTIONS else False

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

# 固定使用单一 Windows UA
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

# 扩展路径
EXTENSION_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "turnstilePatch"))
EXTENSION_ENABLED = os.path.exists(EXTENSION_PATH)

# Turnstile 处理脚本
TURNSTILE_SCRIPT = """
async function handleTurnstile() {
    console.log('开始处理 Turnstile 验证...');
    
    // 等待 Turnstile 加载
    await new Promise(resolve => setTimeout(resolve, 5000));
    
    let token = null;
    
    // 方法1: 尝试通过 window.turnstile 获取
    if (window.turnstile) {
        try {
            const widgets = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
            if (widgets.length > 0) {
                // 尝试获取所有 widget ID 并获取 token
                for (let i = 0; i < widgets.length; i++) {
                    try {
                        const widgetId = widgets[i].id || widgets[i].getAttribute('data-widget-id');
                        if (widgetId) {
                            const response = window.turnstile.getResponse(widgetId);
                            if (response && response.length > 0) {
                                token = response;
                                break;
                            }
                        }
                    } catch (e) {}
                }
            }
        } catch (e) {}
    }
    
    // 方法2: 检查隐藏表单字段
    if (!token) {
        const input = document.querySelector('input[name="cf-turnstile-response"]');
        if (input && input.value) {
            token = input.value;
        }
    }
    
    // 方法3: 轮询等待
    if (!token) {
        let attempts = 0;
        const maxAttempts = 20;
        while (attempts < maxAttempts) {
            attempts++;
            await new Promise(resolve => setTimeout(resolve, 1000));
            
            if (window.turnstile) {
                try {
                    const widgets = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                    for (let i = 0; i < widgets.length; i++) {
                        try {
                            const widgetId = widgets[i].id || widgets[i].getAttribute('data-widget-id');
                            if (widgetId) {
                                const response = window.turnstile.getResponse(widgetId);
                                if (response && response.length > 0) {
                                    token = response;
                                    break;
                                }
                            }
                        } catch (e) {}
                    }
                } catch (e) {}
            }
            
            if (!token) {
                const input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input && input.value) {
                    token = input.value;
                }
            }
            
            if (token) break;
        }
    }
    
    if (token) {
        console.log('成功获取 Turnstile token:', token.substring(0, 20) + '...');
        
        // 设置到表单
        let existingInput = document.querySelector('input[name="cf-turnstile-response"]');
        if (existingInput) {
            existingInput.value = token;
        } else {
            const newInput = document.createElement('input');
            newInput.type = 'hidden';
            newInput.name = 'cf-turnstile-response';
            newInput.value = token;
            const form = document.querySelector('form');
            if (form) form.appendChild(newInput);
        }
        
        // 触发事件
        const event = new Event('change', { bubbles: true });
        existingInput?.dispatchEvent(event);
        
        return { success: true, token: token };
    } else {
        console.error('未能获取 Turnstile token');
        return { success: false, error: '无法获取 token' };
    }
}

return handleTurnstile();
"""

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

class BrowserManager:
    @staticmethod
    def init_browser(site_name):
        try:
            co = ChromiumOptions()
            
            # 浏览器参数
            browser_args = [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=VizDisplayCompositor',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-default-apps',
                '--disable-translate',
                '--disable-extensions',
                '--disable-sync',
                '--disable-web-security',
                '--disable-features=TranslateUI',
                '--window-size=1920,1080'
            ]

            for arg in browser_args:
                co.set_argument(arg)
            
            # 设置固定 UA
            co.set_user_agent(USER_AGENT)
            
            # 在 GitHub Actions 中启用无头模式
            if HEADLESS_MODE:
                co.headless()
            
            # 加载扩展（如果存在）
            if EXTENSION_ENABLED:
                logger.info(f"🔧 加载扩展: {EXTENSION_PATH}")
                try:
                    co.add_extension(EXTENSION_PATH)
                except Exception as e:
                    logger.warning(f"⚠️ 扩展加载失败，继续无扩展运行: {str(e)}")
            else:
                logger.warning("⚠️ 扩展目录不存在，跳过扩展加载")
            
            # 修复参数错误，使用正确的构造方式
            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=PAGE_TIMEOUT)
            
            # 仅加载 Cloudflare cookies
            cf_cookies = CacheManager.load_cf_cookies(site_name)
            if cf_cookies:
                page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个 Cloudflare 验证cookies")
            
            # 反检测脚本
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

class SiteAutomator:
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
            self.page = BrowserManager.init_browser(self.site_config['name'])
            
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
        """强制要求每次都必须登录，不使用任何登录状态缓存"""
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
            
            # 访问登录页面
            self.page.get(self.site_config['login_url'])
            time.sleep(8)

            # 检测并打印页面元素
            self.analyze_login_page()
            
            # 检查 Turnstile
            if self.detect_turnstile_challenge():
                logger.info("🛡️ 检测到 Cloudflare Turnstile 验证")
                if self.enhanced_turnstile_handler():
                    logger.info("✅ Turnstile 验证处理成功")
                else:
                    logger.error("❌ Turnstile 验证处理失败")
                    return False

            username = self.credentials['username']
            password = self.credentials['password']

            # 查找登录表单元素
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

            # 再次检查 Turnstile（可能在输入后出现）
            if self.detect_turnstile_challenge():
                logger.info("🛡️ 输入后检测到 Turnstile 验证")
                if self.enhanced_turnstile_handler():
                    logger.info("✅ 输入后 Turnstile 验证处理成功")

            # 点击登录按钮
            login_button.click()
            time.sleep(10)

            # 检查登录结果
            return self.check_login_status()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            traceback.print_exc()
            return False

    def analyze_login_page(self):
        """分析登录页面状态，打印检测到的元素"""
        try:
            logger.info("🔍 检测页面元素...")
            
            # 检测机器人验证
            bot_selectors = [
                'iframe[src*="cloudflare"]',
                'iframe[src*="challenges"]',
                'iframe[src*="turnstile"]',
                '.cf-challenge',
                '#cf-challenge',
                '.turnstile-wrapper',
                '[data-sitekey]',
                '.g-recaptcha',
                '.h-captcha',
                '.cf-turnstile'
            ]
            
            detected_bots = []
            for selector in bot_selectors:
                elements = self.page.eles(selector)
                if elements:
                    detected_bots.append(selector)
                    logger.warning(f"🤖 检测到机器人验证: {selector}")
            
            # 检测登录元素
            login_selectors = [
                'input[type="text"]',
                'input[type="password"]',
                'input[name="username"]',
                '#username',
                '#password',
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")',
                '#login-account-name',
                '#login-account-password',
                '#login-button'
            ]
            
            detected_login = []
            for selector in login_selectors:
                elements = self.page.eles(selector)
                if elements:
                    for element in elements:
                        if element.displayed:
                            detected_login.append(selector)
                            logger.info(f"🔑 检测到登录元素: {selector}")
            
            # 打印结果
            if detected_bots:
                logger.warning(f"🚨 检测到的机器人验证: {list(set(detected_bots))}")
            if detected_login:
                logger.info(f"✅ 检测到的登录元素: {list(set(detected_login))}")
                
        except Exception as e:
            logger.debug(f"页面分析失败: {str(e)}")

    def detect_turnstile_challenge(self):
        """检测是否存在 Cloudflare Turnstile 验证"""
        try:
            # 检查 Turnstile 相关元素
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
                    logger.info(f"✅ 检测到 Turnstile 元素: {selector}")
                    return True
            
            # 检查页面内容关键词
            page_text = self.page.html.lower()
            keywords = ['cloudflare', 'turnstile', 'challenge', 'verifying', 'captcha']
            if any(keyword in page_text for keyword in keywords):
                logger.info("✅ 检测到 Turnstile 相关关键词")
                return True
                
            return False
        except Exception as e:
            logger.debug(f"检测 Turnstile 验证失败: {str(e)}")
            return False

    def enhanced_turnstile_handler(self):
        """增强的 Turnstile 验证处理器"""
        try:
            logger.info("🔄 开始处理 Turnstile 验证...")
            
            # 等待加载完成
            time.sleep(8)
            
            # 执行处理脚本
            result = self.page.run_js(TURNSTILE_SCRIPT)
            
            if result and result.get('success'):
                token = result.get('token')
                logger.info(f"✅ 成功获取 Turnstile token: {token[:20]}...")
                return True
            else:
                error_msg = result.get('error', '未知错误') if result else '无结果'
                logger.error(f"❌ Turnstile 处理失败: {error_msg}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 处理 Turnstile 验证时发生异常: {str(e)}")
            return False

    def alternative_login_method(self):
        """备用登录方法"""
        try:
            logger.info("🔄 尝试备用登录方法")
            username = self.credentials['username']
            password = self.credentials['password']
            
            # 尝试通过 name 属性查找
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
                return self.check_login_status()
                
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

    def check_login_status(self):
        username = self.credentials['username']
        logger.info(f"🔍 检查登录状态，查找用户名: {username}")

        time.sleep(3)

        # 方法1: 检查用户菜单
        try:
            user_menu = self.page.ele("@id=current-user", timeout=10)
            if user_menu:
                logger.info("✅ 通过用户菜单验证登录成功")
                return True
        except:
            pass

        # 方法2: 检查登出按钮
        try:
            logout_btn = self.page.ele('@text=退出', timeout=8)
            if logout_btn:
                logger.info("✅ 通过退出按钮验证登录成功")
                return True
        except:
            pass

        # 方法3: 访问个人资料页面验证
        try:
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page.get(profile_url)
            time.sleep(3)
            
            profile_content = self.page.html.lower()
            if username.lower() in profile_content:
                logger.info("✅ 通过个人资料页面验证登录成功")
                # 返回最新主题页面
                self.page.get(self.site_config['latest_topics_url'])
                return True
        except Exception as e:
            logger.debug(f"个人资料页面验证失败: {str(e)}")

        # 方法4: 检查URL是否还在登录页
        current_url = self.page.url.lower()
        if 'login' in current_url:
            logger.error("❌ 仍然在登录页面，登录可能失败")
            return False

        logger.error(f"❌ 登录状态检查失败")
        return False

    def perform_browsing_actions_improved(self):
        """改进的浏览操作，确保被网站记录"""
        try:
            logger.info("🌐 开始浏览操作...")
            
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            # 获取主题列表 - 使用你提到的已验证选择器
            topic_list = []
            try:
                list_area = self.page.ele("@id=list-area", timeout=10)
                if list_area:
                    topics = list_area.eles(".:title")
                    if topics:
                        logger.info(f"✅ 使用主要选择器找到 {len(topics)} 个主题")
                        topic_list = topics
            except:
                pass
            
            # 备选方法
            if not topic_list:
                all_links = self.page.eles('tag:a')
                for link in all_links:
                    href = link.attr("href", "")
                    if href and '/t/' in href and len(link.text.strip()) > 5:
                        topic_list.append(link)
            
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
                if self.browse_topic_safe(topic):
                    self.successful_browsed += 1
                
                if i < browse_count:
                    delay = random.uniform(3, 8)
                    logger.info(f"⏳ 等待 {delay:.1f} 秒后浏览下一个主题...")
                    time.sleep(delay)
            
            logger.success(f"✅ 完成浏览 {self.successful_browsed}/{browse_count} 个主题")
            
        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    def browse_topic_safe(self, topic):
        """安全浏览主题"""
        try:
            topic_href = topic.attr("href")
            if not topic_href:
                return False
                
            if topic_href.startswith('/'):
                full_url = urljoin(self.site_config['base_url'], topic_href)
            else:
                full_url = topic_href
                
            logger.info(f"🔗 访问: {full_url}")
            
            new_tab = self.page.new_tab()
            new_tab.get(full_url)
            time.sleep(3)
            
            self.deep_simulate_reading(new_tab)
            
            # 随机点赞（极低概率）
            if random.random() < 0.002:
                self.safe_like_action(new_tab)
            
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

    def deep_simulate_reading(self, page):
        """深度模拟阅读行为"""
        scroll_actions = random.randint(8, 15)
        
        for i in range(scroll_actions):
            scroll_pixels = random.randint(400, 700)
            page.scroll.down(scroll_pixels)
            
            read_time = random.uniform(2, 4)
            time.sleep(read_time)
            
            if random.random() < 0.15:
                self.random_interaction(page)
            
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
        """随机互动增加真实性"""
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

    def safe_like_action(self, page):
        """安全的点赞动作"""
        try:
            like_buttons = page.eles('.like-button, .discourse-reactions-reaction-button')
            for button in like_buttons:
                class_attr = button.attr('class', '')
                if class_attr and 'has-like' not in class_attr:
                    button.click()
                    logger.info("👍 执行点赞")
                    time.sleep(1)
                    break
        except:
            pass

    def get_connect_info_fixed(self):
        """修复的连接信息获取"""
        logger.info("🔗 获取连接信息...")
        
        try:
            # connect 页面不需要登录，直接访问
            self.page.get(self.site_config['connect_url'])
            time.sleep(8)
            
            # 保存页面HTML用于调试
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = f"connect_debug_{self.site_config['name']}_{timestamp}.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(self.page.html)
            logger.info(f"💾 已保存HTML: {html_path}")
            
            # 尝试提取表格数据
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
            traceback.print_exc()

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
            
            keywords = ['访问次数', '回复的话题', '浏览的话题', '已读帖子', '点赞', '获赞', '被举报', '被封禁']
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
                # 只保存 Cloudflare 相关的 cookies
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
    logger.info(f"🔧 平台: Windows NT 10.0; Win64; x64")
    logger.info(f"🔧 User-Agent: {USER_AGENT}")
    logger.info(f"🔧 扩展状态: {'已启用' if EXTENSION_ENABLED else '未启用'}")

    target_sites = SITES
    results = []

    try:
        for site_config in target_sites:
            logger.info(f"🎯 处理站点: {site_config['name']}")

            automator = SiteAutomator(site_config)
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
