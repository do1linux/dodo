#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
#说明：保持双重验证机制（私有主题访问+用户名确认）
#主题浏览: 单标签页,使用了Discourse专用选择器来获取主题列表
#连接信息: 新标签页,使用 tabulate 库美化表格显示
"""

import os
import random
import time
import sys
import json
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
DEBUG_SELECTORS = os.environ.get("DEBUG_SELECTORS", "true").strip().lower() in ["true", "1", "on"]

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

    def debug_all_selectors(self):
        """调试所有可能的选择器"""
        logger.info("🔍 开始调试所有选择器...")
        
        # 定义所有可能的选择器
        selectors = [
            # Discourse专用选择器
            "a.raw-topic-link",
            "a.title.raw-link", 
            "a[data-topic-id]",
            ".main-link a",
            ".link-top-line a",
            "tr.topic-list-item a",
            ".topic-list-body a",
            
            # 通用选择器
            "a[href*='/t/']",
            ".title",
            "a.title",
            "@id=list-area a",
            ".topic-list a",
            
            # 容器选择器
            "@id=list-area",
            ".topic-list",
            ".topic-list-body",
            "tbody"
        ]
        
        results = {}
        
        for selector in selectors:
            try:
                elements = self.page.eles(selector)
                count = len(elements) if elements else 0
                results[selector] = count
                
                if DEBUG_SELECTORS:
                    logger.info(f"🔎 选择器 '{selector}': 找到 {count} 个元素")
                    
                    # 对于找到的元素，显示详细信息
                    if count > 0 and count <= 5:  # 只显示前5个元素的详细信息
                        for i, elem in enumerate(elements[:5]):
                            href = elem.attr('href') if hasattr(elem, 'attr') else 'N/A'
                            text = elem.text[:50] + "..." if elem.text and len(elem.text) > 50 else elem.text
                            logger.info(f"    {i+1}. href: {href}, text: {text}")
                            
            except Exception as e:
                results[selector] = f"错误: {str(e)}"
                if DEBUG_SELECTORS:
                    logger.error(f"❌ 选择器 '{selector}' 执行错误: {str(e)}")
        
        # 输出总结
        logger.info("📊 选择器调试总结:")
        for selector, result in results.items():
            logger.info(f"  {selector}: {result}")
        
        return results

    def find_topic_elements_debug(self):
        """带调试信息的主题元素查找"""
        logger.info("🎯 开始查找主题元素 (调试模式)...")
        
        # 首先调试所有选择器
        selector_results = self.debug_all_selectors()
        
        # 尝试各种策略
        strategies = [
            self._find_by_discourse_specific,
            self._find_by_topic_rows,
            self._find_by_href_pattern,
            self._find_by_dom_structure
        ]
        
        for strategy in strategies:
            try:
                elements = strategy()
                if elements:
                    logger.info(f"✅ 策略 {strategy.__name__} 找到 {len(elements)} 个主题")
                    return elements
            except Exception as e:
                logger.debug(f"策略 {strategy.__name__} 失败: {str(e)}")
                continue
        
        logger.error("❌ 所有策略都找不到主题元素")
        return []

    def _find_by_discourse_specific(self):
        """Discourse专用选择器"""
        selectors = [
            "a.raw-topic-link",
            "a.title.raw-link",
            "a[data-topic-id]",
            ".main-link a[href*='/t/']",
            ".link-top-line a[href*='/t/']"
        ]
        
        for selector in selectors:
            try:
                elements = self.page.eles(selector)
                if elements:
                    valid_elements = [e for e in elements if e.attr('href') and '/t/' in e.attr('href')]
                    if valid_elements:
                        logger.info(f"🎯 Discourse选择器 '{selector}' 找到 {len(valid_elements)} 个有效主题")
                        return valid_elements
            except:
                continue
        return []

    def _find_by_topic_rows(self):
        """通过主题行查找"""
        try:
            # 查找主题行
            topic_rows = self.page.eles("tr.topic-list-item")
            if not topic_rows:
                return []
            
            topic_links = []
            for row in topic_rows:
                # 在每行中查找主题链接
                links = row.eles('tag:a')
                for link in links:
                    href = link.attr('href')
                    if href and '/t/' in href and not any(exclude in href for exclude in ['/tags/', '/c/', '/u/']):
                        topic_links.append(link)
            
            logger.info(f"📋 通过主题行找到 {len(topic_links)} 个主题")
            return topic_links
        except Exception as e:
            logger.debug(f"主题行查找失败: {str(e)}")
            return []

    def _find_by_href_pattern(self):
        """通过href模式查找"""
        try:
            all_links = self.page.eles('tag:a')
            topic_links = []
            
            for link in all_links:
                href = link.attr('href')
                if href and '/t/' in href and not any(exclude in href for exclude in ['/tags/', '/c/', '/u/']):
                    topic_links.append(link)
            
            logger.info(f"🔗 通过href模式找到 {len(topic_links)} 个主题链接")
            return topic_links
        except Exception as e:
            logger.debug(f"href模式查找失败: {str(e)}")
            return []

    def _find_by_dom_structure(self):
        """通过DOM结构查找"""
        try:
            # 查找可能的列表容器
            containers = [
                self.page.ele("@id=list-area"),
                self.page.ele(".topic-list"),
                self.page.ele(".topic-list-body"),
                self.page.ele("tbody")
            ]
            
            for container in containers:
                if container:
                    links = container.eles('tag:a')
                    topic_links = [link for link in links if link.attr('href') and '/t/' in link.attr('href')]
                    if topic_links:
                        logger.info(f"📦 在容器中找到 {len(topic_links)} 个主题")
                        return topic_links
            
            return []
        except Exception as e:
            logger.debug(f"DOM结构查找失败: {str(e)}")
            return []

    def browse_topics_with_debug(self):
        """带调试的主题浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        # 浏览前验证登录状态
        if not self.verify_login_status():
            logger.error("❌ 浏览前登录状态验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始浏览 {self.site_name} 主题 (调试模式)...")
            
            # 访问最新页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(5)
            
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(3)
            
            # 使用带调试的查找方法
            topic_elements = self.find_topic_elements_debug()
            if not topic_elements:
                logger.error("❌ 无法找到任何主题元素")
                return 0
            
            logger.info(f"📚 发现 {len(topic_elements)} 个主题帖")
            
            # 提取主题URL（避免元素失效问题）
            topic_urls = []
            for element in topic_elements:
                href = element.attr("href")
                if not href:
                    continue
                
                # 确保URL完整
                if not href.startswith('http'):
                    href = self.site_config['base_url'] + href
                
                topic_urls.append(href)
            
            # 显示前几个主题URL
            if DEBUG_SELECTORS and topic_urls:
                logger.info("🔗 前5个主题URL:")
                for i, url in enumerate(topic_urls[:5]):
                    logger.info(f"  {i+1}. {url}")
            
            # 随机选择主题（2-4个，避免太多）
            browse_count = min(random.randint(2, 4), len(topic_urls))
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划浏览 {browse_count} 个主题")
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    logger.info(f"🔗 主题URL: {topic_url}")
                    
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

    def print_connect_info_new_tab(self):
        """新标签页获取连接信息"""
        logger.info("🔗 新标签页获取连接信息...")
        try:
            # 在新标签页打开连接页面
            connect_tab = self.page.new_tab()
            connect_tab.get(self.site_config['connect_url'])
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare(connect_tab)
            time.sleep(2)
            
            # 查找表格
            table = connect_tab.ele("tag:table")
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
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
            
            if info:
                # 使用 tabulate 美化表格显示
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("="*60 + "\n")
                
                # 统计达标情况
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
                total = len(info)
                logger.success(f"📈 统计完成: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 关闭连接页面标签
            connect_tab.close()
            logger.info("✅ 连接信息获取完成")
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")
            
            # 1. 确保登录（双重验证）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 2. 带调试的主题浏览
            browse_count = self.browse_topics_with_debug()
            
            # 3. 新标签页获取连接信息
            self.print_connect_info_new_tab()
            
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
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (调试版)")
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
