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

# 平台检测 - 固定为Windows
PLATFORM_IDENTIFIER = "Windows NT 10.0; Win64; x64"
USER_AGENT = f'Mozilla/5.0 ({PLATFORM_IDENTIFIER}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

# 扩展路径 - 检查是否存在，如果不存在则跳过
EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)

# 检查扩展目录是否存在
EXTENSION_ENABLED = os.path.exists(EXTENSION_PATH)

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
                '--allow-running-insecure-content'
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
            
            # 加载缓存cookies
            cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
            if cf_cookies:
                page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个缓存cookies")
            
            # 增强的反自动化检测
            page.run_js("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
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
            
            if self.enhanced_login_approach():
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                self.perform_browsing_actions_improved()
                self.get_connect_info_debug()
                self.save_session_data()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False

        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            self.cleanup()

    def enhanced_login_approach(self):
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")

            # 方法1: 尝试直接访问（使用缓存）
            if self.try_direct_access():
                return True

            # 方法2: 完整登录流程
            if self.enhanced_login_process():
                return True

            if attempt < RETRY_TIMES - 1:
                wait_time = 10 * (attempt + 1)
                logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)

        return False

    def try_direct_access(self):
        try:
            logger.info("🔍 尝试直接访问...")
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            if self.check_login_status():
                logger.info("✅ 缓存登录成功")
                return True
                
            logger.info("❌ 缓存登录失败")
            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    def enhanced_login_process(self):
        try:
            logger.info("🔐 开始完整登录流程")
            self.page.get(self.site_config['login_url'])
            time.sleep(5)  # 增加等待时间

            username = self.credentials['username']
            password = self.credentials['password']

            # 使用更健壮的元素定位
            username_field = self.page.ele("@id=login-account-name", timeout=15)
            password_field = self.page.ele("@id=login-account-password", timeout=15)
            login_button = self.page.ele("@id=login-button", timeout=15)

            if not all([username_field, password_field, login_button]):
                logger.error("❌ 登录表单元素未找到")
                # 尝试备用选择器
                return self.alternative_login_method()

            # 模拟人类输入
            self.human_like_input(username_field, username)
            time.sleep(random.uniform(1, 2))
            self.human_like_input(password_field, password)
            time.sleep(random.uniform(1, 2))

            login_button.click()
            time.sleep(8)  # 增加登录等待时间

            # 检查登录结果
            return self.check_login_status()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    def alternative_login_method(self):
        """备用登录方法"""
        try:
            logger.info("🔄 尝试备用登录方法")
            username = self.credentials['username']
            password = self.credentials['password']
            
            # 尝试通过name属性查找
            username_field = self.page.ele('@name=username', timeout=10)
            password_field = self.page.ele('@name=password', timeout=10)
            login_button = self.page.ele('@type=submit', timeout=10)
            
            if all([username_field, password_field, login_button]):
                self.human_like_input(username_field, username)
                time.sleep(1)
                self.human_like_input(password_field, password)
                time.sleep(1)
                login_button.click()
                time.sleep(8)
                return self.check_login_status()
                
            return False
        except Exception as e:
            logger.debug(f"备用登录方法失败: {str(e)}")
            return False

    def human_like_input(self, element, text):
        """模拟人类输入"""
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.15))

    def check_login_status(self):
        username = self.credentials['username']
        logger.info(f"🔍 检查登录状态，查找用户名: {username}")

        # 方法1: 检查用户菜单
        try:
            user_menu = self.page.ele("@id=current-user", timeout=8)
            if user_menu:
                logger.info("✅ 通过用户菜单验证登录成功")
                return True
        except:
            pass

        # 方法2: 访问个人资料页面验证
        try:
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page.get(profile_url)
            time.sleep(3)
            
            # 检查页面内容
            profile_content = self.page.html.lower()
            if username.lower() in profile_content:
                logger.info("✅ 通过个人资料页面验证登录成功")
                # 返回最新主题页面
                self.page.get(self.site_config['latest_topics_url'])
                return True
        except Exception as e:
            logger.debug(f"个人资料页面验证失败: {str(e)}")

        logger.error(f"❌ 登录状态检查失败")
        return False

    def perform_browsing_actions_improved(self):
        """改进的浏览操作，确保被网站记录"""
        try:
            logger.info("🌐 开始浏览操作...")
            
            # 确保在最新主题页面
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(3)
            
            # 获取主题列表
            topic_list = self.get_topic_list_improved()
            if not topic_list:
                logger.warning("❌ 未找到主题链接")
                return
            
            self.topic_count = len(topic_list)
            logger.info(f"📚 发现 {self.topic_count} 个主题帖")
            
            # 选择要浏览的主题
            browse_count = min(MAX_TOPICS_TO_BROWSE, len(topic_list))
            selected_topics = random.sample(topic_list, browse_count)
            
            logger.info(f"🎯 准备浏览 {browse_count} 个主题")
            
            for i, topic in enumerate(selected_topics, 1):
                logger.info(f"📖 浏览进度: {i}/{browse_count}")
                if self.browse_topic_safe(topic):
                    self.successful_browsed += 1
                
                # 主题间随机延迟
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
            # 方法1: 使用已验证的选择器
            list_area = self.page.ele("@id=list-area", timeout=10)
            if list_area:
                topics = list_area.eles(".:title")
                if topics:
                    logger.info(f"✅ 使用主要选择器找到 {len(topics)} 个主题")
                    return topics
            
            # 方法2: 直接查找所有主题链接
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

    def browse_topic_safe(self, topic):
        """安全浏览主题，避免元素失效问题"""
        try:
            topic_href = topic.attr("href")
            if not topic_href:
                return False
                
            # 构建完整URL
            if topic_href.startswith('/'):
                full_url = urljoin(self.site_config['base_url'], topic_href)
            else:
                full_url = topic_href
                
            logger.info(f"🔗 访问: {full_url}")
            
            # 使用新标签页浏览，避免页面刷新导致的元素失效
            new_tab = self.page.new_tab()
            new_tab.get(full_url)
            time.sleep(3)  # 确保页面加载完成
            
            # 执行深度浏览
            self.deep_simulate_reading(new_tab)
            
            # 随机点赞（极低概率，避免滥用）
            if random.random() < 0.002:  # 0.2%概率
                self.safe_like_action(new_tab)
            
            # 关闭标签页
            new_tab.close()
            logger.info(f"✅ 成功浏览主题")
            return True
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")
            # 如果新标签页出现问题，尝试关闭它
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
            # 随机滚动
            scroll_pixels = random.randint(400, 700)
            page.scroll.down(scroll_pixels)
            
            # 随机阅读时间
            read_time = random.uniform(2, 4)
            time.sleep(read_time)
            
            # 随机互动
            if random.random() < 0.15:
                self.random_interaction(page)
            
            # 检查是否到达底部
            at_bottom = page.run_js(
                "return window.innerHeight + window.scrollY >= document.body.scrollHeight - 100"
            )
            
            if at_bottom and random.random() < 0.7:
                logger.info("📄 到达页面底部，停止滚动")
                break
                
            # 随机提前退出
            if random.random() < 0.08:
                logger.info("🎲 随机提前退出浏览")
                break

    def random_interaction(self, page):
        """随机互动增加真实性"""
        try:
            # 随机鼠标移动
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

    def get_connect_info_debug(self):
        """修复的连接信息获取 - 调试版本"""
        logger.info("🔗 获取连接信息 - 调试模式")
        new_page = self.page.new_tab()
        try:
            new_page.get(self.site_config['connect_url'])
            time.sleep(10)  # 确保页面完全加载
            
            # 保存页面HTML用于调试
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = f"connect_debug_{self.site_config['name']}_{timestamp}.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(new_page.html)
            logger.info(f"💾 已保存完整HTML: {html_path}")
            
            # 打印页面标题和关键信息
            page_title = new_page.title
            logger.info(f"📄 页面标题: {page_title}")
            
            # 检查页面内容 - 修复：使用run_js获取页面文本
            try:
                page_text = new_page.run_js("return document.body.innerText")
                if "访问次数" in page_text or "浏览的话题" in page_text:
                    logger.info("✅ 页面包含连接信息关键词")
                else:
                    logger.warning("❌ 页面不包含连接信息关键词")
            except Exception as e:
                logger.warning(f"获取页面文本失败: {str(e)}")
            
            # 查找所有表格
            tables = new_page.eles("tag:table")
            logger.info(f"📊 找到 {len(tables)} 个表格")
            
            for i, table in enumerate(tables):
                logger.info(f"🔍 分析表格 {i+1}:")
                
                # 获取表格HTML结构
                table_html = table.html
                # 只打印前500个字符避免日志过长
                logger.info(f"  表格HTML (前500字符): {table_html[:500]}...")
                
                # 尝试提取表格行
                rows = table.eles("tag:tr")
                logger.info(f"  表格包含 {len(rows)} 行")
                
                for j, row in enumerate(rows):
                    cells = row.eles("tag:td")
                    th_cells = row.eles("tag:th")
                    
                    if cells or th_cells:
                        row_info = f"  第{j+1}行: "
                        if th_cells:
                            row_info += f"表头[{len(th_cells)}] "
                        if cells:
                            row_info += f"数据[{len(cells)}]"
                        
                        # 打印前几个单元格内容
                        content_preview = []
                        for cell in (th_cells + cells)[:3]:
                            try:
                                text = cell.text.strip()
                                if text:
                                    content_preview.append(text[:20])  # 只取前20个字符
                            except:
                                content_preview.append("无法获取文本")
                        
                        if content_preview:
                            row_info += f" 内容: {', '.join(content_preview)}"
                        
                        logger.info(row_info)
            
            # 尝试使用简单方法提取数据
            info = self.extract_connect_data_simple(new_page)
            if info:
                self.display_connect_info(info, "简单提取")
                return
            
            # 尝试使用高级方法提取数据
            info = self.extract_connect_data_advanced(new_page)
            if info:
                self.display_connect_info(info, "高级提取")
                return
                
            logger.error("💥 所有方法都无法提取连接信息")
                
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
            traceback.print_exc()
        finally:
            new_page.close()

    def extract_connect_data_simple(self, page):
        """简单提取连接数据"""
        try:
            # 查找所有表格
            tables = page.eles("tag:table")
            
            for table in tables:
                rows = table.eles("tag:tr")
                info = []
                
                for row in rows:
                    # 跳过表头行（只包含th）
                    th_cells = row.eles("tag:th")
                    if th_cells and len(th_cells) >= 3:
                        continue
                        
                    cells = row.eles("tag:td")
                    if len(cells) >= 3:
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        
                        # 只添加有意义的行
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
            # 获取页面所有文本
            all_text = page.run_js("return document.body.innerText")
            
            # 查找包含连接信息的部分
            lines = all_text.split('\n')
            info = []
            
            # 查找包含关键信息的行
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                    
                # 检查是否包含连接信息的关键词
                keywords = ['访问次数', '回复的话题', '浏览的话题', '已读帖子', '点赞', '获赞']
                if any(keyword in line for keyword in keywords):
                    # 尝试从上下文中提取信息
                    context_lines = lines[max(0, i-2):min(len(lines), i+3)]
                    logger.debug(f"找到关键词行: {line}")
                    logger.debug(f"上下文: {context_lines}")
            
            # 另一种方法：查找所有可能的数据行
            all_elements = page.eles("tag:tr, tag:div, tag:li")
            for elem in all_elements:
                try:
                    text = elem.text.strip()
                    if any(keyword in text for keyword in keywords):
                        # 尝试提取项目、当前值和要求
                        parts = [part.strip() for part in text.split('\n') if part.strip()]
                        if len(parts) >= 3:
                            # 简单的启发式规则：第一个部分可能是项目名
                            project = parts[0]
                            # 尝试找到包含百分比或数字的部分
                            current = next((p for p in parts if any(c in p for c in ['%', '/', '≥'])), '')
                            requirement = next((p for p in parts if '要求' in p or '需要' in p), '')
                            
                            if project and (current or requirement):
                                info.append([project, current, requirement])
                except:
                    continue
            
            # 去重
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

    def save_session_data(self):
        try:
            # 保存cookies
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_config['name'], 'cf_cookies')
                logger.info(f"💾 保存 {len(cookies)} 个cookies")

            # 保存会话数据
            session_data = {
                'topic_count': self.topic_count,
                'successful_browsed': self.successful_browsed,
                'last_updated': datetime.now().isoformat(),
                'user_agent': USER_AGENT
            }
            CacheManager.save_site_cache(session_data, self.site_config['name'], 'browser_state')
            
            logger.success(f"✅ 会话数据已保存 (发现主题: {self.topic_count}, 成功浏览: {self.successful_browsed})")

        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

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

    logger.info("🚀 LinuxDo自动化脚本启动 - 连接信息修复版")
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

            # 站点间延迟
            if site_config != target_sites[-1]:
                delay = random.uniform(15, 30)
                logger.info(f"⏳ 等待 {delay:.1f} 秒后处理下一个站点...")
                time.sleep(delay)

        # 输出最终结果
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
