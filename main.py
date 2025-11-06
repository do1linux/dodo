import os
import sys
import time
import random
import json
import traceback
from datetime import datetime
from urllib.parse import urljoin
from DrissionPage import ChromiumPage, SessionPage, ChromiumOptions
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

IS_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_topics_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do/',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json",
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_topics_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com/',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json",
    }
]

PAGE_TIMEOUT = 120
RETRY_TIMES = 3
MAX_TOPICS_TO_BROWSE = 10

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'

class CacheManager:
    @staticmethod
    def load_cache(file_name):
        try:
            if os.path.exists(file_name):
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 加载缓存: {file_name}")
                return data
            return None
        except Exception as e:
            logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
            return None

    @staticmethod
    def save_cache(data, file_name):
        try:
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_site_cache(site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.save_cache(data, file_name)

    @staticmethod
    def has_cache(site_name):
        cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
        return cf_cookies is not None

class HumanBehaviorSimulator:
    @staticmethod
    def random_delay(min_seconds=1.0, max_seconds=3.0):
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    @staticmethod
    def simulate_scroll_behavior(page, scroll_distance=None):
        if scroll_distance is None:
            scroll_distance = random.randint(550, 650)
        page.scroll.down(scroll_distance)
        time.sleep(random.uniform(2, 4))

class BrowserManager:
    @staticmethod
    def init_browser(site_name):
        try:
            co = ChromiumOptions()
            
            browser_args = [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--headless=new',
                '--disable-gpu',
                '--remote-debugging-port=0',
            ]
            
            for arg in browser_args:
                co.set_argument(arg)
            
            co.set_user_agent(USER_AGENT)
            co.auto_port()
            
            logger.info("🚀 正在启动浏览器...")
            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=PAGE_TIMEOUT)
            
            cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
            if cf_cookies:
                page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个缓存cookies")
            
            page.run_js("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
            """)
            
            logger.info("✅ 浏览器已成功启动")
            return page
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            try:
                logger.info("🔄 尝试备用浏览器启动方案...")
                page = ChromiumPage()
                page.set.timeouts(base=PAGE_TIMEOUT)
                cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
                if cf_cookies:
                    page.set.cookies(cf_cookies)
                logger.info("✅ 备用浏览器启动成功")
                return page
            except Exception as e2:
                logger.error(f"❌ 备用浏览器启动也失败: {str(e2)}")
                raise

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.page = None
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        self.topic_count = 0

    def run_for_site(self):
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False

        try:
            has_cache = CacheManager.has_cache(self.site_config['name'])
            if has_cache:
                logger.info(f"📦 检测到缓存，尝试使用缓存登录")
            else:
                logger.info(f"🆕 未检测到缓存，需要重新登录")

            self.page = BrowserManager.init_browser(self.site_config['name'])

            login_success = self.smart_login_approach()

            if login_success:
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                self.perform_browsing_actions()
                self.print_connect_info()
                self.save_session_data()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False

        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            traceback.print_exc()
            return False
        finally:
            self.cleanup()

    def smart_login_approach(self):
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")

            try:
                if self.try_direct_access():
                    return True

                if self.full_login_process():
                    return True

            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")

            if attempt < RETRY_TIMES - 1:
                self.clear_cache()
                time.sleep(10 * (attempt + 1))

        return False

    def try_direct_access(self):
        try:
            logger.info("🔍 尝试直接访问...")
            self.page.get(self.site_config['latest_topics_url'])
            time.sleep(5)

            if self.check_login_status():
                logger.success("✅ 缓存登录成功")
                return True

            logger.info("❌ 缓存登录失败，需要重新登录")
            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    def full_login_process(self):
        try:
            logger.info("🔐 开始完整登录流程")
            self.page.get(self.site_config['login_url'])
            time.sleep(5)

            username = self.credentials['username']
            password = self.credentials['password']

            # 使用您原来的登录方式
            self.page.ele("@id=login-account-name").input(username)
            self.page.ele("@id=login-account-password").input(password)
            self.page.ele("@id=login-button").click()
            time.sleep(10)

            return self.check_login_status()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    def check_login_status(self):
        try:
            username = self.credentials['username']
            logger.info(f"🔍 检查登录状态，查找用户名: {username}")

            # 方法1: 检查用户菜单
            user_ele = self.page.ele("@id=current-user")
            if user_ele:
                logger.success("✅ 找到用户菜单，登录成功")
                return True

            # 方法2: 检查页面内容
            content = self.page.html
            if username.lower() in content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {username}")
                return True

            # 方法3: 访问个人资料页面
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page.get(profile_url)
            time.sleep(3)
            profile_content = self.page.html
            if username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                self.page.back()
                return True

            logger.error(f"❌ 无法在页面中找到用户名: {username}")
            return False

        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    def perform_browsing_actions(self):
        """使用您原来的浏览逻辑"""
        try:
            logger.info("🌐 开始浏览操作...")
            
            # 使用您原来的选择器
            topic_list = self.get_topic_list()
            if not topic_list:
                logger.warning("❌ 未找到主题链接")
                return
            
            self.topic_count = len(topic_list)
            logger.info(f"📚 发现 {self.topic_count} 个主题帖，随机选择{MAX_TOPICS_TO_BROWSE}个")
            
            selected_topics = random.sample(topic_list, min(MAX_TOPICS_TO_BROWSE, len(topic_list)))
            
            for topic in selected_topics:
                topic_href = topic.attr("href")
                if topic_href:
                    self.click_one_topic(topic_href)
                    HumanBehaviorSimulator.random_delay(2, 5)
            
            logger.success("✅ 浏览操作完成")
            
        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    def get_topic_list(self):
        """使用您原来的选择器获取主题列表"""
        try:
            # 主要选择器：您原来的选择器
            list_area = self.page.ele("@id=list-area")
            if list_area:
                topic_list = list_area.eles(".:title")
                if topic_list:
                    logger.info(f"✅ 使用原选择器找到 {len(topic_list)} 个主题")
                    return topic_list
            
            # 备用选择器：如果主要选择器失败
            backup_selectors = [
                "#list-area .title",
                ".topic-list-item a.title",
                "a.title[href*='/t/']"
            ]
            
            for selector in backup_selectors:
                try:
                    elements = self.page.eles(selector)
                    if elements:
                        logger.info(f"✅ 使用备用选择器 '{selector}' 找到 {len(elements)} 个主题")
                        return elements
                except Exception as e:
                    logger.debug(f"备用选择器 '{selector}' 失败: {str(e)}")
                    continue
            
            logger.warning("❌ 所有选择器都未能找到主题")
            return []
            
        except Exception as e:
            logger.error(f"获取主题列表失败: {str(e)}")
            return []

    def click_one_topic(self, topic_url):
        """浏览单个主题 - 简化标签页管理"""
        try:
            # 保存当前页面的URL
            original_url = self.page.url
            
            # 在新标签页打开主题 - 直接使用 new_tab 方法
            new_page = self.page.new_tab()
            
            full_url = urljoin(self.site_config['base_url'], topic_url)
            logger.info(f"📖 打开主题: {full_url}")
            
            # 在新标签页中导航
            new_page.get(full_url)
            time.sleep(3)
            
            # 随机点赞（0.3%概率）
            if random.random() < 0.003:
                self.click_like(new_page)
            
            # 浏览帖子内容
            self.browse_post(new_page)
            
            # 关闭新标签页
            new_page.close()
            
            logger.info(f"✅ 完成浏览主题: {topic_url}")
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")

    def browse_post(self, page):
        """浏览帖子内容 - 使用您原来的滚动逻辑"""
        prev_url = None
        
        # 开始自动滚动，最多滚动10次
        for i in range(10):
            # 随机滚动一段距离
            scroll_distance = random.randint(550, 650)
            logger.debug(f"第{i+1}次滚动，向下滚动 {scroll_distance} 像素...")
            
            # 使用 DrissionPage 的滚动方法
            page.scroll.down(scroll_distance)
            
            logger.debug(f"已加载页面: {page.url}")

            if random.random() < 0.03:
                logger.info("随机退出浏览")
                break

            # 检查是否到达页面底部
            at_bottom = page.run_js(
                "return window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                logger.info("已到达页面底部，退出浏览")
                break

            # 动态随机等待
            wait_time = random.uniform(2, 4)
            logger.debug(f"等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)

    def click_like(self, page):
        """点赞操作 - 使用您原来的逻辑"""
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
    """获取连接信息 - 基于提供的HTML结构"""
    try:
        logger.info("获取连接信息")
        
        # 在新标签页打开连接信息
        new_page = self.page.new_tab()
        
        new_page.get(self.site_config['connect_url'])
        time.sleep(5)  # 增加等待时间确保页面加载
        
        # 根据提供的HTML结构，查找包含信任级别信息的div
        # 查找包含 "roychuan - 信任级别" 文本的元素
        trust_level_element = new_page.ele('xpath://*[contains(text(), "信任级别")]')
        
        if trust_level_element:
            logger.info("✅ 找到信任级别信息")
            
            # 获取父级div容器
            parent_div = trust_level_element.parent()
            
            # 在父级div中查找表格
            table = parent_div.ele('tag:table')
            
            if table:
                # 获取所有行
                rows = table.eles('tag:tr')
                info = []
                
                for row in rows:
                    # 获取所有单元格（包括th和td）
                    cells = row.eles('tag:td, tag:th')
                    
                    if len(cells) >= 3:
                        # 提取项目、当前状态和要求
                        project = cells[0].text.strip()
                        current = cells[1].text.strip()
                        requirement = cells[2].text.strip()
                        
                        # 跳过表头行（如果第一列是"项目"）
                        if project == "项目" and current == "当前" and requirement == "要求":
                            continue
                            
                        # 确保不是空行
                        if project and (current or requirement):
                            info.append([project, current, requirement])
                
                if info:
                    print("=" * 50)
                    print("📊 Connect 连接信息")
                    print("=" * 50)
                    print(tabulate(info, headers=["项目", "当前状态", "要求"], tablefmt="grid"))
                    print("=" * 50)
                    
                    # 统计达标情况
                    passed_count = 0
                    total_count = len(info)
                    
                    for item in info:
                        project, current, requirement = item
                        # 检查是否达标（绿色显示的项目）
                        if "text-green-500" in str(cells[1].html) or "✅" in current:
                            passed_count += 1
                    
                    logger.success(f"✅ 连接信息获取成功 - 达标项目: {passed_count}/{total_count}")
                else:
                    logger.warning("⚠️ 表格中没有找到有效数据")
            else:
                logger.warning("⚠️ 在信任级别区域未找到表格")
                
                # 备用方案：尝试直接查找所有包含统计信息的元素
                logger.info("🔄 尝试备用方案查找统计信息")
                
                # 查找所有包含统计数据的段落或div
                stats_elements = parent_div.eles('tag:p, tag:div')
                backup_info = []
                
                for element in stats_elements:
                    text = element.text.strip()
                    if text and ("访问次数" in text or "回复的话题" in text or "浏览的话题" in text or 
                               "已读帖子" in text or "被举报的帖子" in text or "点赞" in text or 
                               "获赞" in text or "被禁言" in text or "被封禁" in text):
                        backup_info.append(text)
                
                if backup_info:
                    print("=" * 50)
                    print("📊 Connect 连接信息（备用数据）")
                    print("=" * 50)
                    for item in backup_info:
                        print(f"• {item}")
                    print("=" * 50)
                    logger.success("✅ 通过备用方案获取连接信息成功")
        else:
            logger.warning("⚠️ 未找到信任级别信息")
            
            # 如果找不到信任级别信息，尝试其他可能的位置
            logger.info("🔄 尝试在其他位置查找连接信息")
            
            # 查找所有表格
            all_tables = new_page.eles('tag:table')
            found_table = False
            
            for table in all_tables:
                # 检查表格是否包含信任级别相关的关键词
                table_text = table.text
                if any(keyword in table_text for keyword in ["访问次数", "回复的话题", "浏览的话题", "已读帖子"]):
                    rows = table.eles('tag:tr')
                    info = []
                    
                    for row in rows:
                        cells = row.eles('tag:td, tag:th')
                        if len(cells) >= 3:
                            project = cells[0].text.strip()
                            current = cells[1].text.strip()
                            requirement = cells[2].text.strip()
                            
                            if project and project != "项目" and (current or requirement):
                                info.append([project, current, requirement])
                    
                    if info:
                        print("=" * 50)
                        print("📊 Connect 连接信息（通过关键词查找）")
                        print("=" * 50)
                        print(tabulate(info, headers=["项目", "当前状态", "要求"], tablefmt="grid"))
                        print("=" * 50)
                        logger.success("✅ 通过关键词查找获取连接信息成功")
                        found_table = True
                        break
            
            if not found_table:
                logger.error("❌ 无法在任何位置找到连接信息")
                
        # 关闭新标签页
        new_page.close()
            
    except Exception as e:
        logger.error(f"获取连接信息失败: {str(e)}")
        logger.debug(f"详细错误信息: {traceback.format_exc()}")
        try:
            new_page.close()
        except:
            pass

    def save_session_data(self):
        """保存会话数据"""
        try:
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_config['name'], 'cf_cookies')
                logger.info(f"💾 保存 {len(cookies)} 个cookies")

            session_data = {
                'topic_count': self.topic_count,
                'last_updated': datetime.now().isoformat(),
                'site': self.site_config['name']
            }
            CacheManager.save_site_cache(session_data, self.site_config['name'], 'browser_state')
            
            logger.success(f"✅ 会话数据已保存 (主题数量: {self.topic_count})")

        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    def clear_cache(self):
        cache_files = [
            f"cf_cookies_{self.site_config['name']}.json",
            f"browser_state_{self.site_config['name']}.json"
        ]
        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"🗑️ 已清除: {file}")

    def cleanup(self):
        try:
            if self.page:
                self.page.quit()
        except Exception:
            pass

def main():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    logger.info("🚀 LinuxDo自动化脚本启动 (简化标签页管理版本)")

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
                time.sleep(random.uniform(10, 20))

        logger.info("📊 执行结果:")
        table_data = [[r['site'], "✅ 成功" if r['success'] else "❌ 失败"] for r in results]
        print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))

        success_count = sum(1 for r in results if r['success'])
        logger.success(f"🎉 完成: {success_count}/{len(results)} 个站点成功")

    except Exception as e:
        logger.critical(f"💥 主流程异常: {str(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    main()

