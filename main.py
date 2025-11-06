import os
import sys
import time
import random
import json
import traceback
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

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'

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

class BrowserManager:
    @staticmethod
    def init_browser(site_name):
        try:
            co = ChromiumOptions()
            browser_args = [
                '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--headless=new', '--disable-gpu'
            ]
            for arg in browser_args:
                co.set_argument(arg)
            
            co.set_user_agent(USER_AGENT)
            page = ChromiumPage(addr_or_opts=co)
            page.set.timeouts(base=PAGE_TIMEOUT)
            
            # 加载缓存cookies
            cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
            if cf_cookies:
                page.set.cookies(cf_cookies)
                logger.info(f"✅ 已加载 {len(cf_cookies)} 个缓存cookies")
            
            # 反自动化检测
            page.run_js("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
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

    def run_for_site(self):
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False

        try:
            self.page = BrowserManager.init_browser(self.site_config['name'])
            
            if self.smart_login_approach():
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
            return False
        finally:
            self.cleanup()

    def smart_login_approach(self):
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")

            # 方法1: 尝试直接访问（使用缓存）
            if self.try_direct_access():
                return True

            # 方法2: 完整登录流程
            if self.full_login_process():
                return True

            if attempt < RETRY_TIMES - 1:
                time.sleep(10 * (attempt + 1))

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

    def full_login_process(self):
        try:
            logger.info("🔐 开始完整登录流程")
            self.page.get(self.site_config['login_url'])
            time.sleep(3)

            username = self.credentials['username']
            password = self.credentials['password']

            # 输入凭据并登录
            self.page.ele("@id=login-account-name").input(username)
            self.page.ele("@id=login-account-password").input(password)
            self.page.ele("@id=login-button").click()
            time.sleep(5)

            return self.check_login_status()

        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    def check_login_status(self):
        username = self.credentials['username']
        logger.info(f"🔍 检查登录状态，查找用户名: {username}")

        # 使用已验证有效的方法：访问个人资料页面验证
        try:
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            self.page.get(profile_url)
            time.sleep(2)
            profile_content = self.page.html
            if username.lower() in profile_content.lower():
                logger.info("✅ 登录状态验证成功")
                self.page.back()
                return True
        except Exception as e:
            logger.error(f"个人资料页面验证失败: {str(e)}")

        logger.error(f"❌ 登录状态检查失败，无法找到用户名: {username}")
        return False

    def perform_browsing_actions(self):
        try:
            logger.info("🌐 开始浏览操作...")
            
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
                    self.browse_topic(topic_href)
                    time.sleep(random.uniform(2, 5))
            
            logger.success("✅ 浏览操作完成")
            
        except Exception as e:
            logger.error(f"浏览操作失败: {str(e)}")

    def get_topic_list(self):
        try:
            # 主要选择器
            list_area = self.page.ele("@id=list-area")
            if list_area:
                topic_list = list_area.eles(".:title")
                if topic_list:
                    logger.info("✅ 使用主要选择器找到主题")
                    return topic_list
            
            # 备用选择器
            backup_selectors = [
                "#list-area .title",
                ".topic-list-item a.title", 
                "a.title[href*='/t/']"
            ]
            
            for selector in backup_selectors:
                try:
                    elements = self.page.eles(selector)
                    if elements:
                        logger.info(f"✅ 使用备用选择器 '{selector}' 找到主题")
                        return elements
                except Exception:
                    continue
            
            logger.warning("❌ 所有选择器都未能找到主题")
            return []
            
        except Exception as e:
            logger.error(f"获取主题列表失败: {str(e)}")
            return []

    def browse_topic(self, topic_url):
        try:
            new_page = self.page.new_tab()
            full_url = urljoin(self.site_config['base_url'], topic_url)
            logger.info(f"📖 打开主题: {full_url}")
            
            new_page.get(full_url)
            time.sleep(2)
            
            # 随机点赞
            if random.random() < 0.003:
                self.click_like(new_page)
            
            # 浏览内容
            self.simulate_reading(new_page)
            
            new_page.close()
            logger.info(f"✅ 完成浏览主题")
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")

    def simulate_reading(self, page):
        for i in range(random.randint(3, 8)):
            scroll_distance = random.randint(300, 600)
            page.scroll.down(scroll_distance)
            
            if random.random() < 0.05:
                break
                
            time.sleep(random.uniform(1, 3))

    def click_like(self, page):
        try:
            like_button = page.ele(".discourse-reactions-reaction-button")
            if like_button:
                like_button.click()
                logger.info("👍 点赞成功")
                time.sleep(1)
        except Exception:
            pass

    def print_connect_info(self):
        """获取连接信息 - 使用已验证有效的方法"""
        logger.info("获取连接信息")
        new_page = self.page.new_tab()
        try:
            new_page.get(self.site_config['connect_url'])
            time.sleep(5)

            # 使用简单有效的方法：直接查找表格行
            rows = new_page.eles('table tr')
            info = []

            for row in rows:
                cells = row.eles('td')
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    
                    # 确保不是空行
                    if project and (current or requirement):
                        info.append([project, current, requirement])

            if info:
                print("=" * 50)
                print("📊 Connect 连接信息")
                print("=" * 50)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="grid"))
                print("=" * 50)
                logger.success("✅ 连接信息获取成功")
            else:
                logger.warning("⚠️ 未找到连接信息")

        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
        finally:
            new_page.close()

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
                'last_updated': datetime.now().isoformat(),
            }
            CacheManager.save_site_cache(session_data, self.site_config['name'], 'browser_state')
            
            logger.success(f"✅ 会话数据已保存 (主题数量: {self.topic_count})")

        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

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

    logger.info("🚀 LinuxDo自动化脚本启动")

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
