#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 优化缓存和Cloudflare处理版本
优化：减少Cloudflare验证、改进缓存管理、单标签页浏览
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
        'session_file': "session_data_linux_do.json",
        'browser_state_file': "browser_state_linux_do.json"
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
        'session_file': "session_data_idcflare.json",
        'browser_state_file': "browser_state_idcflare.json"
    }
]

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
        """加载缓存，始终返回最新数据"""
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
                    logger.info(f"🗑️ 已删除损坏的缓存文件: {file_name}")
                except:
                    pass
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存缓存，始终覆盖旧文件"""
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
    def clear_old_caches():
        """清除所有旧缓存文件，确保新运行使用新缓存"""
        try:
            cache_files = [
                "cf_cookies_linux_do.json", "session_data_linux_do.json", "browser_state_linux_do.json",
                "cf_cookies_idcflare.json", "session_data_idcflare.json", "browser_state_idcflare.json"
            ]
            
            for cache_file in cache_files:
                file_path = CacheManager.get_cache_file_path(cache_file)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除旧缓存: {cache_file}")
            
            logger.info("✅ 所有旧缓存已清除")
        except Exception as e:
            logger.error(f"❌ 清除旧缓存失败: {str(e)}")

# ======================== Cloudflare优化处理器 ========================
class CloudflareHandler:
    @staticmethod
    def handle_cloudflare_light(page, max_attempts=3, timeout=60):
        """轻量级Cloudflare处理 - 减少验证等待"""
        start_time = time.time()
        logger.info("🛡️ 快速处理Cloudflare验证")
        
        # 首先检查是否已经通过验证
        try:
            page_title = page.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                logger.success("✅ 页面已正常加载，无需Cloudflare验证")
                return True
        except:
            pass
        
        for attempt in range(max_attempts):
            try:
                page_title = page.title
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    logger.success("✅ Cloudflare验证通过")
                    return True
                
                # 更短的等待时间
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

    @staticmethod
    def inject_cloudflare_bypass(page):
        """注入Cloudflare绕过脚本"""
        try:
            bypass_script = """
            // Cloudflare绕过脚本
            if (typeof window.console !== 'undefined') {
                console.constructor = window.console.constructor;
            }
            
            // 屏蔽自动化检测
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            
            // 模拟真实浏览器行为
            window.chrome = { runtime: {} };
            """
            page.run_js(bypass_script)
            logger.info("✅ Cloudflare绕过脚本已注入")
        except Exception as e:
            logger.debug(f"注入Cloudflare绕过脚本失败: {str(e)}")

# ======================== 主浏览器类 - 优化版本 ========================
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
        """初始化浏览器并加载缓存状态"""
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
            co.set_argument("--disable-features=VizDisplayCompositor")
            co.set_argument("--disable-background-timer-throttling")
            
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 加载所有可能的缓存
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            self.browser_state = CacheManager.load_site_cache(self.site_name, 'browser_state') or {}
            
            # 注入Cloudflare绕过
            CloudflareHandler.inject_cloudflare_bypass(self.page)
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def save_comprehensive_caches(self):
        """保存全面的缓存数据"""
        if self.cache_saved:
            return
            
        try:
            # 1. 保存cookies
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info(f"✅ 已保存 {len(cookies)} 个Cookies")
            
            # 2. 保存会话数据
            session_data = {
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '5.0',
                'site_name': self.site_name,
                'username': self.username[:3] + '***',  # 部分隐藏用户名
                'total_saved': self.session_data.get('total_saved', 0) + 1,
                'last_url': self.page.url,
                'user_agent': self.page.run_js("return navigator.userAgent;")
            }
            CacheManager.save_site_cache(session_data, self.site_name, 'session_data')
            
            # 3. 保存浏览器状态
            browser_state = {
                'timestamp': datetime.now().isoformat(),
                'url': self.page.url,
                'title': self.page.title,
                'cookies_count': len(cookies) if cookies else 0,
                'window_size': self.page.run_js("return {width: window.innerWidth, height: window.innerHeight};")
            }
            CacheManager.save_site_cache(browser_state, self.site_name, 'browser_state')
            
            # 4. 保存页面HTML快照（简化版）
            try:
                html_snapshot = {
                    'timestamp': datetime.now().isoformat(),
                    'title': self.page.title,
                    'url': self.page.url,
                    'content_length': len(self.page.html) if self.page.html else 0
                }
                CacheManager.save_site_cache(html_snapshot, self.site_name, 'html_snapshot')
            except:
                pass
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 所有缓存已保存完成")
            
        except Exception as e:
            logger.error(f"❌ 保存缓存失败: {str(e)}")

    def restore_browser_state(self):
        """恢复浏览器状态"""
        try:
            if self.browser_state and self.browser_state.get('url'):
                logger.info("🔄 恢复浏览器状态...")
                self.page.get(self.browser_state['url'])
                time.sleep(2)
                return True
        except Exception as e:
            logger.debug(f"恢复浏览器状态失败: {str(e)}")
        return False

    def try_cache_login_enhanced(self):
        """增强的缓存登录尝试"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录，跳过缓存")
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            logger.warning("⚠️ 无有效缓存Cookies")
            return False
        
        try:
            logger.info("🎯 尝试使用增强缓存登录...")
            
            # 先恢复浏览器状态
            if self.restore_browser_state():
                logger.info("✅ 浏览器状态恢复成功")
            else:
                # 如果状态恢复失败，访问首页
                self.page.get(self.site_config['base_url'])
                time.sleep(2)
            
            # 设置cookies
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            # 刷新页面
            self.page.refresh()
            time.sleep(3)
            
            # 轻量级Cloudflare处理
            CloudflareHandler.handle_cloudflare_light(self.page)
            
            if self.strict_verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def strict_verify_login_status(self, max_retries=2):
        """严格的登录状态验证"""
        logger.info("🔍 执行严格登录状态验证...")
        
        for retry in range(max_retries):
            try:
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题: {private_url}")
                self.page.get(private_url)
                time.sleep(3)  # 减少等待时间
                
                CloudflareHandler.handle_cloudflare_light(self.page)
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
                
                if self.username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面中找到用户名: {self.username}")
                    logger.success("🎉 双重验证通过 - 登录状态有效")
                    return True
                else:
                    logger.warning(f"❌ 在页面中未找到用户名: {self.username}")
                    continue
                
            except Exception as e:
                logger.error(f"❌ 登录状态验证异常: {str(e)}")
        
        return False

    def login_optimized(self):
        """优化的登录流程"""
        self.page.set.cookies([])  # 清除旧cookies
        logger.info("🔐 执行优化登录流程...")
        
        self.page.get(self.site_config['login_url'])
        time.sleep(2)
        
        CloudflareHandler.handle_cloudflare_light(self.page)
        time.sleep(2)
        
        try:
            username_field = self.page.ele("#login-account-name")
            password_field = self.page.ele("#login-account-password")
            login_button = self.page.ele("#login-button")
            
            if not all([username_field, password_field, login_button]):
                logger.error("❌ 登录表单元素未找到")
                return False
            
            logger.info("⌨️ 输入登录信息...")
            username_field.input(self.username)
            time.sleep(random.uniform(0.3, 0.7))
            
            password_field.input(self.password)
            time.sleep(random.uniform(0.3, 0.7))
            
            logger.info("🔑 点击登录按钮...")
            login_button.click()
            time.sleep(8)  # 减少等待时间
            
            CloudflareHandler.handle_cloudflare_light(self.page)
            time.sleep(3)
            
            if self.strict_verify_login_status():
                logger.success("✅ 登录成功")
                self.save_comprehensive_caches()
                return True
            else:
                logger.error("❌ 登录失败")
                return False
                
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def ensure_logged_in(self):
        """确保用户已登录"""
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login_enhanced():
            return True
        return self.login_optimized()

    def browse_topics_single_tab(self):
        """单标签页主题浏览 - 减少Cloudflare验证"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        if not self.strict_verify_login_status():
            logger.error("❌ 浏览前登录状态验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始单标签页浏览 {self.site_name} 主题...")
            
            # 访问最新页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare_light(self.page)
            time.sleep(2)
            
            # 查找主题元素
            topic_elements = self.find_topic_elements_advanced()
            if not topic_elements:
                logger.error("❌ 未找到主题列表")
                return 0
            
            # 选择主题浏览
            browse_count = min(random.randint(4, 8), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0
            
            logger.info(f"📊 发现 {len(topic_elements)} 个主题，计划浏览 {browse_count} 个")
            
            for i, idx in enumerate(selected_indices):
                try:
                    if idx >= len(topic_elements):
                        continue
                    
                    topic = topic_elements[idx]
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                    
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    # 在当前标签页打开主题（不再新建标签页）
                    self.page.get(topic_url)
                    time.sleep(2)
                    
                    CloudflareHandler.handle_cloudflare_light(self.page)
                    time.sleep(2)
                    
                    # 浏览帖子内容
                    self.browse_post_optimized()
                    
                    # 随机点赞（低概率）
                    if random.random() < 0.02:
                        self.click_like_optimized()
                    
                    success_count += 1
                    
                    # 返回主题列表页面
                    if i < browse_count - 1:
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(2)
                        CloudflareHandler.handle_cloudflare_light(self.page)
                        
                        # 重新获取主题元素（避免stale reference）
                        topic_elements = self.find_topic_elements_advanced()
                        if not topic_elements:
                            logger.error("❌ 重新获取主题列表失败")
                            break
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(4, 8)
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    # 尝试恢复状态
                    try:
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(2)
                        topic_elements = self.find_topic_elements_advanced()
                    except:
                        pass
                    continue
            
            logger.success(f"✅ 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def find_topic_elements_advanced(self):
        """高级主题元素查找"""
        topic_elements = []
        
        selectors = [
            "a.title", 
            ".topic-list-item a",
            ".topic-list-body a",
            "[data-topic-id] a",
            "tr.topic-list-item a"
        ]
        
        for selector in selectors:
            try:
                elements = self.page.eles(selector)
                if elements:
                    valid_elements = []
                    for elem in elements:
                        href = elem.attr('href')
                        if href and '/t/' in href and not href.endswith('/latest'):
                            valid_elements.append(elem)
                    
                    if valid_elements:
                        topic_elements = valid_elements
                        logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(topic_elements)} 个主题")
                        return topic_elements
            except Exception:
                continue
        
        # 备用方法：链接分析
        try:
            all_links = self.page.eles('tag:a')
            for link in all_links:
                href = link.attr('href')
                if href and '/t/' in href and not href.endswith('/latest'):
                    if not any(x in href for x in ['/c/', '/tag/', '/u/', '/latest']):
                        topic_elements.append(link)
            
            if topic_elements:
                logger.info(f"🔍 通过链接分析找到 {len(topic_elements)} 个主题")
                return topic_elements
        except Exception as e:
            logger.error(f"❌ 链接分析失败: {str(e)}")
        
        return topic_elements

    def browse_post_optimized(self):
        """优化的帖子浏览"""
        try:
            scroll_count = random.randint(4, 7)  # 减少滚动次数
            logger.info(f"📜 开始浏览帖子，计划滚动 {scroll_count} 次")
            
            for i in range(scroll_count):
                scroll_distance = random.randint(300, 500)  # 减少滚动距离
                self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
                
                # 随机等待时间
                wait_time = random.uniform(1.5, 3)
                time.sleep(wait_time)
                
                # 检查是否到达底部
                at_bottom = self.page.run_js(
                    "return window.innerHeight + window.pageYOffset >= document.body.offsetHeight - 10"
                )
                if at_bottom:
                    logger.info("📄 已到达页面底部")
                    break
            
            logger.info("✅ 帖子浏览完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览帖子失败: {str(e)}")

    def click_like_optimized(self):
        """优化的点赞功能"""
        try:
            like_buttons = self.page.eles(".discourse-reactions-reaction-button")
            for button in like_buttons:
                if button and button.states.is_enabled:
                    button_class = button.attr('class') or ''
                    if 'has-like' not in button_class:
                        logger.info("👍 尝试点赞")
                        button.click()
                        time.sleep(1)
                        logger.success("✅ 点赞成功")
                        return
            logger.info("ℹ️ 未找到可点赞的按钮")
        except Exception as e:
            logger.debug(f"点赞失败: {str(e)}")

    def print_connect_info_optimized(self):
        """优化的连接信息获取"""
        logger.info("🔗 获取连接信息...")
        try:
            # 保存当前URL
            current_url = self.page.url
            
            self.page.get(self.site_config['connect_url'])
            time.sleep(3)
            
            CloudflareHandler.handle_cloudflare_light(self.page)
            time.sleep(2)
            
            # 解析表格数据
            table = self.page.ele("tag:table")
            if table:
                self.parse_connect_table()
            else:
                logger.warning("⚠️ 未找到标准表格")
            
            # 返回原页面
            self.page.get(current_url)
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def parse_connect_table(self):
        """解析连接表格"""
        try:
            rows = self.page.eles("tag:tr")
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
                logger.warning("⚠️ 未找到表格数据")
                
        except Exception as e:
            logger.error(f"❌ 解析表格失败: {str(e)}")

    def run_optimized(self):
        """优化的完整流程"""
        try:
            logger.info(f"🚀 开始优化处理站点: {self.site_name}")
            
            # 确保登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 浏览主题
            browse_count = self.browse_topics_single_tab()
            
            # 显示连接信息
            self.print_connect_info_optimized()
            
            # 保存完整缓存
            self.save_comprehensive_caches()
            
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

# ======================== 主函数 - 优化版本 ========================
def main():
    # 清除旧缓存，确保新运行使用新缓存
    CacheManager.clear_old_caches()
    
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (优化缓存和Cloudflare版)")
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
            success = browser.run_optimized()

            if success:
                success_sites.append(site_name)
            else:
                failed_sites.append(site_name)
                
        except Exception as e:
            logger.error(f"❌ {site_name} 执行异常: {str(e)}")
            failed_sites.append(site_name)

        # 站点间等待
        if site_config != target_sites[-1]:
            wait_time = random.uniform(8, 15)
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
