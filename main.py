#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化版 - 基于调试结果改进选择器和连接信息获取
"""

import os
import random
import time
import sys
import json
import re
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions
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
        'private_topic_url': 'https://linux.do/t/topic/2362',
        'latest_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do',
        'user_url': 'https://linux.do/u',
        'cf_cookies_file': "cf_cookies_linux_do.json",
        'session_file': "session_data_linux_do.json"
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'private_topic_url': 'https://idcflare.com/t/topic/24',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'session_file': "session_data_idcflare.json"
    }
]

# 环境变量配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]

# ======================== 缓存管理器 ========================
class CacheManager:
    @staticmethod
    def get_cache_directory():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        cache_dir = CacheManager.get_cache_directory()
        return os.path.join(cache_dir, file_name)

    @staticmethod
    def load_cache(file_name):
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"📦 成功加载缓存: {file_name}")
                return data
            except Exception as e:
                logger.warning(f"⚠️ 缓存加载失败 {file_name}: {str(e)}")
                try:
                    os.remove(file_path)
                except:
                    pass
        return None

    @staticmethod
    def save_cache(data, file_name):
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 缓存已保存: {file_name}")
            return True
        except Exception as e:
            logger.error(f"❌ 缓存保存失败 {file_name}: {str(e)}")
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
    def clear_site_cache_on_failure(site_name):
        """登录失败时清除该站点的缓存"""
        try:
            cache_types = ['cf_cookies', 'session_data']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{site_name}.json"
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 登录失败，已清除缓存: {file_name}")
            
            logger.info(f"✅ {site_name} 站点缓存已清除")
            
        except Exception as e:
            logger.error(f"❌ 清除站点缓存失败: {str(e)}")

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    @staticmethod
    def handle_cloudflare(page, max_attempts=3, timeout=60):
        """处理Cloudflare验证"""
        start_time = time.time()
        logger.info("🛡️ 开始处理Cloudflare验证")
        
        for attempt in range(max_attempts):
            try:
                page_title = page.title
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                
                wait_time = random.uniform(3, 6)
                logger.info(f"⏳ 等待Cloudflare验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时，继续执行")
                    break
                    
            except Exception as e:
                logger.debug(f"Cloudflare检查异常: {str(e)}")
                time.sleep(3)
        
        return True

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.page = None
        self.cache_saved = False
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器"""
        try:
            co = ChromiumOptions()
            if HEADLESS:
                co.headless(True)
            else:
                co.headless(False)
                
            co.incognito(True)
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--disable-blink-features=AutomationControlled")
            
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def save_caches(self):
        """保存缓存"""
        if self.cache_saved:
            return
            
        try:
            # 保存cookies
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info(f"✅ 已保存 {len(cookies)} 个Cookies")
            
            # 保存会话数据
            session_data = {
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '1.0',
                'site_name': self.site_name,
                'username_hash': hash(self.username) if self.username else 0,
                'total_runs': self.session_data.get('total_runs', 0) + 1
            }
            CacheManager.save_site_cache(session_data, self.site_name, 'session_data')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 缓存保存完成")
            
        except Exception as e:
            logger.error(f"❌ 保存缓存失败: {str(e)}")

    def try_cache_login(self):
        """尝试使用缓存登录"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录，跳过缓存")
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            logger.warning("⚠️ 无有效缓存Cookies")
            return False
        
        try:
            logger.info("🎯 尝试使用缓存登录...")
            
            self.page.get(self.site_config['base_url'])
            time.sleep(2)
            
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            self.page.refresh()
            time.sleep(2)
            
            CloudflareHandler.handle_cloudflare(self.page)
            
            if self.verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def verify_login_status(self):
        """验证登录状态 - 双重验证机制"""
        logger.info("🔍 执行登录状态验证...")
        
        try:
            # 第一重验证：访问私有主题
            private_url = self.site_config['private_topic_url']
            logger.info(f"📍 访问私有主题: {private_url}")
            self.page.get(private_url)
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(2)
            
            page_content = self.page.html
            page_title = self.page.title
            
            logger.info(f"📄 私有主题页面标题: {page_title}")
            
            # 检查是否有错误提示
            error_indicators = ["Page Not Found", "糟糕！该页面不存在或者是一个不公开页面。"]
            for indicator in error_indicators:
                if indicator.lower() in page_content.lower():
                    logger.error(f"❌ 私有主题访问失败: {indicator}")
                    return False
            
            logger.success("✅ 私有主题访问成功")
            
            # 第二重验证：验证用户名存在
            if self.username.lower() in page_content.lower():
                logger.success(f"✅ 在页面中找到用户名: {self.username}")
                logger.success("🎉 双重验证通过 - 登录状态有效")
                return True
            else:
                logger.error(f"❌ 在页面中未找到用户名: {self.username}")
                return False
            
        except Exception as e:
            logger.error(f"❌ 登录状态验证异常: {str(e)}")
            return False

    def login(self):
        """执行登录流程"""
        self.page.set.cookies([])
        logger.info("🔐 执行登录流程...")
        
        self.page.get(self.site_config['login_url'])
        time.sleep(2)
        
        CloudflareHandler.handle_cloudflare(self.page)
        time.sleep(2)
        
        try:
            # 等待表单元素出现
            time.sleep(2)
            
            # 查找并填写用户名
            username_field = self.page.ele("#login-account-name")
            if not username_field:
                logger.error("❌ 找不到用户名字段")
                return False
            
            logger.info("⌨️ 输入用户名...")
            username_field.input(self.username)
            time.sleep(random.uniform(0.5, 1))
            
            # 查找并填写密码
            password_field = self.page.ele("#login-account-password")
            if not password_field:
                logger.error("❌ 找不到密码字段")
                return False
            
            logger.info("⌨️ 输入密码...")
            password_field.input(self.password)
            time.sleep(random.uniform(0.5, 1))
            
            # 点击登录按钮
            login_button = self.page.ele("#login-button")
            if not login_button:
                logger.error("❌ 找不到登录按钮")
                return False
            
            logger.info("🔑 点击登录按钮...")
            login_button.click()
            time.sleep(8)
            
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(3)
            
            if self.verify_login_status():
                logger.success("✅ 登录成功")
                self.save_caches()
                return True
            else:
                logger.error("❌ 登录失败")
                return False
                
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def ensure_logged_in(self):
        """确保用户已登录"""
        # 尝试缓存登录
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login():
            return True
        
        # 执行手动登录
        login_success = self.login()
        if not login_success:
            # 登录失败时清除缓存
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    def find_topic_elements_optimized(self):
        """优化的主题元素查找方法 - 基于调试结果"""
        logger.info("🎯 使用优化的选择器查找主题...")
        
        # 基于调试结果，最有效的方法是href模式
        try:
            all_links = self.page.eles('tag:a')
            topic_links = []
            seen_urls = set()  # 用于去重
            
            for link in all_links:
                href = link.attr('href')
                if not href:
                    continue
                
                # 过滤主题链接
                if '/t/' in href and not any(exclude in href for exclude in ['/tags/', '/c/', '/u/']):
                    # 确保URL完整
                    if not href.startswith('http'):
                        href = self.site_config['base_url'] + href
                    
                    # 去重：提取基础主题URL（去掉页码）
                    base_url = re.sub(r'/t/topic/(\d+)(/\d+)?', r'/t/topic/\1', href)
                    
                    if base_url not in seen_urls:
                        seen_urls.add(base_url)
                        topic_links.append({
                            'element': link,
                            'url': base_url
                        })
            
            logger.info(f"🔗 找到 {len(topic_links)} 个去重后的主题")
            return topic_links
            
        except Exception as e:
            logger.error(f"❌ 查找主题失败: {str(e)}")
            return []

    def browse_topics_optimized(self):
        """优化的主题浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        # 浏览前验证登录状态
        if not self.verify_login_status():
            logger.error("❌ 浏览前登录状态验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始浏览 {self.site_name} 主题...")
            
            # 访问最新页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(2)
            
            # 使用优化的查找方法
            topic_data = self.find_topic_elements_optimized()
            if not topic_data:
                logger.error("❌ 无法找到任何主题元素")
                return 0
            
            logger.info(f"📚 发现 {len(topic_data)} 个主题帖")
            
            # 随机选择主题（3-5个）
            browse_count = min(random.randint(3, 5), len(topic_data))
            selected_topics = random.sample(topic_data, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划浏览 {browse_count} 个主题")
            
            for i, topic in enumerate(selected_topics):
                try:
                    topic_url = topic['url']
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    logger.debug(f"🔗 主题URL: {topic_url}")
                    
                    # 在当前标签页打开主题
                    self.page.get(topic_url)
                    time.sleep(3)
                    
                    CloudflareHandler.handle_cloudflare(self.page)
                    time.sleep(2)
                    
                    # 模拟阅读行为
                    self.simulate_reading_behavior()
                    
                    success_count += 1
                    logger.info(f"✅ 成功浏览主题 {i+1}")
                    
                    # 如果不是最后一个主题，返回列表页面
                    if i < browse_count - 1:
                        logger.info("🔄 返回主题列表页面...")
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(3)
                        CloudflareHandler.handle_cloudflare(self.page)
                        time.sleep(2)
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(8, 15)
                        logger.info(f"⏳ 主题间延迟 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"✅ 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def simulate_reading_behavior(self):
        """模拟阅读行为"""
        try:
            # 随机滚动次数
            scroll_count = random.randint(4, 7)
            logger.debug(f"📖 模拟阅读行为: {scroll_count} 次滚动")
            
            for i in range(scroll_count):
                # 随机滚动距离
                scroll_distance = random.randint(300, 600)
                self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
                
                # 随机阅读时间
                read_time = random.uniform(2, 5)
                time.sleep(read_time)
                
                # 随机触发一些交互
                if random.random() < 0.3:
                    self.page.run_js("""
                        document.dispatchEvent(new MouseEvent('mousemove', {
                            bubbles: true,
                            clientX: Math.random() * window.innerWidth,
                            clientY: Math.random() * window.innerHeight
                        }));
                    """)
            
            # 最终触发一些事件
            self.page.run_js("""
                window.dispatchEvent(new Event('scroll'));
                window.dispatchEvent(new Event('focus'));
            """)
            
            logger.debug("✅ 阅读行为模拟完成")
            
        except Exception as e:
            logger.debug(f"模拟阅读行为异常: {str(e)}")

    def print_connect_info_improved(self):
        """改进的连接信息获取"""
        logger.info("🔗 获取连接信息...")
        try:
            # 在新标签页打开连接页面
            connect_tab = self.page.new_tab()
            connect_tab.get(self.site_config['connect_url'])
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare(connect_tab)
            time.sleep(2)
            
            # 多种表格选择器
            table_selectors = [
                "tag:table",
                ".connect-table",
                ".stats-table",
                ".user-stats",
                "#connect-stats",
                ".panel"
            ]
            
            table = None
            for selector in table_selectors:
                try:
                    table = connect_tab.ele(selector)
                    if table:
                        logger.info(f"✅ 使用选择器 '{selector}' 找到表格")
                        break
                except:
                    continue
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格，尝试其他方法...")
                
                # 尝试查找任何包含连接信息的元素
                possible_containers = connect_tab.eles('.container, .content, .main, .wrapper')
                for container in possible_containers:
                    text = container.text
                    if any(keyword in text.lower() for keyword in ['访问次数', '回复', '浏览', '已读', '点赞']):
                        logger.info("✅ 找到包含连接信息的容器")
                        # 提取文本信息
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        info = []
                        for line in lines:
                            if any(keyword in line for keyword in ['访问次数', '回复', '浏览', '已读', '点赞']):
                                parts = re.split(r'[:：]', line, 1)
                                if len(parts) == 2:
                                    info.append([parts[0].strip(), parts[1].strip(), ''])
                        
                        if info:
                            self._display_connect_info(info)
                            connect_tab.close()
                            return
                
                logger.warning("⚠️ 无法找到连接信息")
                connect_tab.close()
                return
            
            # 提取表格数据
            rows = table.eles("tag:tr")
            info = []
            
            for row in rows:
                cells = row.eles("tag:td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    if project and current and requirement:
                        info.append([project, current, requirement])
                elif len(cells) == 2:
                    # 有些表格可能只有两列
                    project = cells[0].text.strip()
                    value = cells[1].text.strip()
                    if project and value:
                        info.append([project, value, ''])
            
            if info:
                self._display_connect_info(info)
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 关闭连接页面标签
            connect_tab.close()
            logger.info("✅ 连接信息获取完成")
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def _display_connect_info(self, info):
        """显示连接信息"""
        # 使用 tabulate 美化表格显示
        print("\n" + "="*60)
        print(f"📊 {self.site_name.upper()} 连接信息")
        print("="*60)
        
        # 确保有三列
        formatted_info = []
        for item in info:
            if len(item) == 2:
                formatted_info.append([item[0], item[1], ''])
            else:
                formatted_info.append(item)
        
        print(tabulate(formatted_info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        print("="*60 + "\n")
        
        # 统计达标情况
        passed = sum(1 for item in formatted_info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
        total = len(formatted_info)
        logger.success(f"📈 统计完成: {passed}/{total} 项达标")

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")
            
            # 1. 确保登录（双重验证）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 2. 优化的主题浏览
            browse_count = self.browse_topics_optimized()
            
            # 3. 改进的连接信息获取
            self.print_connect_info_improved()
            
            # 4. 保存缓存
            self.save_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 浏览 {browse_count} 个主题")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
            
        finally:
            try:
                if self.page:
                    self.page.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (优化版)")
    logger.info("=" * 80)
    
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    
    success_sites = []
    failed_sites = []

    # 检查凭证配置
    for site_name, creds in SITE_CREDENTIALS.items():
        if not creds.get('username') or not creds.get('password'):
            logger.warning(f"⏭️ {site_name} 的用户名或密码未配置")

    # 站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    target_sites = SITES if site_selector == "all" else [s for s in SITES if s['name'] == site_selector]

    if not target_sites:
        logger.error(f"❌ 未找到匹配的站点: {site_selector}")
        sys.exit(1)

    logger.info(f"🎯 目标站点: {', '.join([s['name'] for s in target_sites])}")

    for site_config in target_sites:
        site_name = site_config['name']
        credentials = SITE_CREDENTIALS.get(site_name, {})

        if not credentials.get('username') or not credentials.get('password'):
            logger.warning(f"⏭️ 跳过 {site_name} - 凭证未配置")
            failed_sites.append(site_name)
            continue

        logger.info("-" * 80)
        logger.info(f"🔧 初始化 {site_name}")
        
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

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(10, 20)
            logger.info(f"⏳ 站点间延迟 {wait_time:.1f} 秒...")
            time.sleep(wait_time)

    # 最终总结
    logger.info("=" * 80)
    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功站点: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败站点: {', '.join(failed_sites) if failed_sites else '无'}")
    logger.info("=" * 80)

    if success_sites:
        logger.success(f"🎉 任务完成: {len(success_sites)}/{len(target_sites)} 个站点成功")
        sys.exit(0)
    else:
        logger.error("💥 任务失败: 所有站点均未成功")
        sys.exit(1)

if __name__ == "__main__":
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD', 'IDCFLARE_USERNAME', 'IDCFLARE_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 以下环境变量未设置: {', '.join(missing_vars)}")
    
    main()
