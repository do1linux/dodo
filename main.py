#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 修复主题浏览问题版本
主要修复：主题选择器问题、浏览计数问题、连接信息显示问题
"""

import os
import random
import time
import sys
import json
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions, SessionPage

# ======================== 配置常量 ========================
# 站点认证信息配置 - 请确保环境变量已设置
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

# 站点配置列表 - 更新私有主题URL
SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'private_topic_url': 'https://linux.do/t/topic/2362',  # 已验证的私有主题
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
        'private_topic_url': 'https://idcflare.com/t/topic/24',  # 已验证的私有主题
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'session_file': "session_data_idcflare.json"
    }
]

# 配置项
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
COOKIES_EXPIRY_HOURS = int(os.environ.get("COOKIES_EXPIRY_HOURS", "24"))
MAX_CACHE_AGE_HOURS = int(os.environ.get("MAX_CACHE_AGE_HOURS", "168"))

# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类 - 管理Cookies和Session数据"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录"""
        return os.path.dirname(os.path.abspath(__file__))

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
                logger.info(f"📦 成功加载缓存: {file_name}")
                return data
            except Exception as e:
                logger.warning(f"⚠️ 缓存加载失败 {file_name}: {str(e)}")
                try:
                    os.remove(file_path)
                    logger.info(f"🗑️ 已删除损坏的缓存文件: {file_name}")
                except:
                    pass
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存数据到缓存文件"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            file_size = os.path.getsize(file_path)
            logger.info(f"💾 缓存已保存: {file_name} (大小: {file_size} 字节)")
            return True
        except Exception as e:
            logger.error(f"❌ 缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_site_cache(site_name, cache_type):
        """加载特定站点的缓存"""
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.load_cache(file_name)

    @staticmethod
    def save_site_cache(data, site_name, cache_type):
        """保存特定站点的缓存"""
        file_name = f"{cache_type}_{site_name}.json"
        return CacheManager.save_cache(data, file_name)

# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类"""
    
    @staticmethod
    def handle_cloudflare(page, max_attempts=5, timeout=120):
        """处理Cloudflare验证"""
        start_time = time.time()
        logger.info("🛡️ 开始处理Cloudflare验证")
        
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = page.title
                
                # 检查页面是否已经正常加载
                if page_title and page_title != "请稍候…" and "Checking" not in page_title and "Just a moment" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                
                # 等待验证
                wait_time = random.uniform(5, 10)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                    
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(5)
        
        # 最终检查
        try:
            page_title = page.title
            if page_title and page_title != "请稍候…" and "Checking" not in page_title:
                logger.success("✅ 最终验证: Cloudflare验证通过")
                return True
            else:
                logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
                return True
        except Exception:
            logger.warning("⚠️ 无法获取页面标题，继续后续流程")
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
        
        # 初始化浏览器
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器"""
        try:
            # 配置浏览器选项
            co = ChromiumOptions()
            
            if HEADLESS:
                co.headless(True)
            else:
                co.headless(False)
                
            # 反检测配置
            co.incognito(True)
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-features=VizDisplayCompositor")
            co.set_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
            
            # 用户代理
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            
            # 初始化页面
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def get_all_cookies(self):
        """获取所有cookies"""
        try:
            cookies = self.page.cookies()
            if cookies:
                logger.info(f"✅ 成功获取 {len(cookies)} 个cookies")
                return cookies
            else:
                logger.warning("⚠️ 未获取到cookies")
                return []
        except Exception as e:
            logger.error(f"❌ 获取cookies失败: {str(e)}")
            return []

    def save_all_caches(self, force_save=False):
        """统一保存所有缓存"""
        if self.cache_saved and not force_save:
            return
            
        try:
            # 保存cookies
            cookies = self.get_all_cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
                logger.info("✅ Cloudflare Cookies已缓存")
            
            # 保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'cache_version': '4.1',
                'total_saved': self.session_data.get('total_saved', 0) + 1,
                'last_url': self.page.url
            })
            CacheManager.save_site_cache(self.session_data, self.site_name, 'session_data')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 所有缓存已保存")
            
        except Exception as e:
            logger.error(f"❌ 保存缓存失败: {str(e)}")

    def clear_caches(self):
        """清除所有缓存文件"""
        try:
            cache_types = ['session_data', 'browser_state', 'cf_cookies']
            for cache_type in cache_types:
                file_name = f"{cache_type}_{self.site_name}.json"
                file_path = CacheManager.get_cache_file_path(file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info(f"✅ {self.site_name} 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"❌ 清除缓存失败: {str(e)}")

    def try_cache_login(self):
        """尝试使用缓存登录"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录，跳过缓存")
            return False
            
        # 加载缓存的cookies
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            logger.warning("⚠️ 无有效缓存Cookies")
            return False
        
        try:
            logger.info("🎯 尝试使用Cookies缓存登录...")
            
            # 访问首页
            self.page.get(self.site_config['base_url'])
            time.sleep(3)
            
            # 设置cookies
            self.page.set.cookies(cookies)
            time.sleep(2)
            
            # 刷新页面
            self.page.refresh()
            time.sleep(3)
            
            # 验证登录状态
            if self.strict_verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            else:
                logger.warning("⚠️ 缓存登录失败，需要重新登录")
                return False
                
        except Exception as e:
            logger.error(f"❌ 缓存登录异常: {str(e)}")
            return False

    def strict_verify_login_status(self, max_retries=2):
        """严格的登录状态验证"""
        logger.info("🔍 执行严格登录状态验证...")
        
        for retry in range(max_retries):
            try:
                # 访问私有主题
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题: {private_url}")
                self.page.get(private_url)
                time.sleep(5)
                
                # 处理可能的Cloudflare验证
                CloudflareHandler.handle_cloudflare(self.page)
                time.sleep(3)
                
                # 获取页面内容和标题
                page_content = self.page.html
                page_title = self.page.title
                current_url = self.page.url
                
                logger.info(f"📄 私有主题页面标题: {page_title}")
                logger.info(f"🌐 当前URL: {current_url}")
                
                # 检查是否在登录页面
                if 'login' in current_url or 'signin' in current_url:
                    logger.warning(f"❌ 被重定向到登录页面 (尝试 {retry + 1}/{max_retries})")
                    if retry < max_retries - 1:
                        time.sleep(3)
                        continue
                    return False
                
                # 检查是否有错误提示
                error_indicators = [
                    "Page Not Found",
                    "糟糕！该页面不存在或者是一个不公开页面。",
                    "Oops! This page doesn't exist or is not a public page.",
                    "page doesn't exist",
                    "not a public page"
                ]
                
                for indicator in error_indicators:
                    if indicator.lower() in page_content.lower():
                        logger.error(f"❌ 私有主题访问失败: {indicator}")
                        return False
                
                logger.success("✅ 私有主题访问成功 - 无错误提示")
                
                # 验证用户名存在
                if self.username.lower() in page_content.lower():
                    logger.success(f"✅ 在页面中找到用户名: {self.username}")
                    logger.success("🎉 双重验证通过 - 登录状态有效")
                    return True
                else:
                    logger.warning(f"❌ 在页面中未找到用户名: {self.username} (尝试 {retry + 1}/{max_retries})")
                    if retry < max_retries - 1:
                        wait_time = random.uniform(3, 6)
                        logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                
            except Exception as e:
                logger.error(f"❌ 登录状态验证异常: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(3)
        
        logger.error("❌ 所有登录状态验证尝试均失败")
        return False

    def login(self):
        """执行登录流程"""
        # 清除旧cookies
        self.page.set.cookies([])
        
        logger.info("🔐 执行登录流程...")
        self.page.get(self.site_config['login_url'])
        time.sleep(3)
        
        # 处理Cloudflare验证
        CloudflareHandler.handle_cloudflare(self.page)
        time.sleep(3)
        
        # 填写登录信息
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
            time.sleep(10)  # 等待登录完成
            
            # 处理登录后的Cloudflare验证
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(5)
            
            # 严格验证登录状态
            if self.strict_verify_login_status():
                logger.success("✅ 登录成功")
                self.save_all_caches()
                return True
            else:
                logger.error("❌ 登录失败")
                self.clear_caches()
                return False
                
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            self.clear_caches()
            return False

    def ensure_logged_in(self):
        """确保用户已登录"""
        # 尝试缓存登录
        if not FORCE_LOGIN_EVERY_TIME:
            if self.try_cache_login():
                return True
        
        # 执行手动登录
        return self.login()

    def enhanced_browse_post(self, stay_time=25):
        """增强的浏览行为"""
        try:
            logger.info(f"📖 模拟阅读行为，停留 {stay_time:.1f} 秒...")
            start_time = time.time()
            
            # 随机滚动次数
            scroll_count = random.randint(6, 10)
            scrolls_done = 0
            
            while time.time() - start_time < stay_time:
                try:
                    # 随机滚动距离
                    scroll_distance = random.randint(200, 600)
                    self.page.run_js(f"window.scrollBy(0, {scroll_distance})")
                    scrolls_done += 1
                    
                    # 随机阅读时间
                    if random.random() < 0.5:
                        read_time = random.uniform(2, 5)
                        time.sleep(read_time)
                    else:
                        time.sleep(random.uniform(1, 3))
                    
                    # 随机回滚
                    if random.random() < 0.2:
                        back_scroll = random.randint(100, 200)
                        self.page.run_js(f"window.scrollBy(0, -{back_scroll})")
                        time.sleep(random.uniform(1, 2))
                    
                except Exception as e:
                    logger.debug(f"阅读行为模拟异常: {str(e)}")
                    time.sleep(1)
            
            logger.debug(f"📊 阅读完成: {scrolls_done} 次滚动")
            
        except Exception as e:
            logger.error(f"❌ 增强浏览失败: {str(e)}")
            # 降级到基础浏览
            time.sleep(stay_time)

    def find_topic_elements(self):
        """查找主题元素 - 使用多种选择器"""
        topic_elements = []
        
        # 尝试多种选择器
        selectors = [
            "a.title",
            ".title a", 
            "tr.topic-list-item a",
            ".topic-list-body a",
            "[data-topic-id] a"
        ]
        
        for selector in selectors:
            try:
                elements = self.page.eles(selector)
                if elements:
                    # 过滤出有效的主题链接
                    valid_elements = []
                    for elem in elements:
                        href = elem.attr('href')
                        if href and '/t/' in href:
                            valid_elements.append(elem)
                    
                    if valid_elements:
                        topic_elements = valid_elements
                        logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(topic_elements)} 个主题")
                        break
            except Exception as e:
                logger.debug(f"选择器 {selector} 查找失败: {str(e)}")
                continue
        
        return topic_elements

    def browse_topics_enhanced(self):
        """增强的主题浏览功能"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0
        
        # 浏览前严格验证登录状态
        if not self.strict_verify_login_status():
            logger.error("❌ 浏览前登录状态验证失败")
            return 0
        
        try:
            logger.info(f"🌐 开始增强浏览 {self.site_name} 主题...")
            
            # 访问最新页面
            self.page.get(self.site_config['latest_url'])
            time.sleep(5)
            
            # 处理Cloudflare验证
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(3)
            
            # 查找主题元素
            topic_elements = self.find_topic_elements()
            if not topic_elements:
                logger.error("❌ 未找到主题列表")
                return 0
            
            # 随机选择3-5个主题（避免过多）
            browse_count = min(random.randint(3, 5), len(topic_elements))
            selected_indices = random.sample(range(len(topic_elements)), browse_count)
            success_count = 0
            
            logger.info(f"📊 发现 {len(topic_elements)} 个主题，计划浏览 {browse_count} 个")
            
            for i, idx in enumerate(selected_indices):
                try:
                    if idx >= len(topic_elements):
                        logger.warning("⚠️ 主题索引超出范围，跳过")
                        continue
                    
                    topic = topic_elements[idx]
                    topic_url = topic.attr("href")
                    if not topic_url:
                        continue
                    
                    # 确保URL完整
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    # 在新标签页打开主题
                    self.page.get(topic_url)
                    time.sleep(5)
                    
                    # 处理Cloudflare验证
                    CloudflareHandler.handle_cloudflare(self.page)
                    time.sleep(3)
                    
                    # 模拟阅读行为
                    stay_time = random.uniform(20, 35)
                    self.enhanced_browse_post(stay_time)
                    
                    success_count += 1
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(8, 15)
                        logger.info(f"⏳ 主题间延迟 {wait_time:.1f} 秒...")
                        time.sleep(wait_time)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"✅ 浏览完成: 成功 {success_count}/{browse_count} 个主题")
            
            # 浏览后再次验证登录状态
            if not self.strict_verify_login_status():
                logger.warning("⚠️ 浏览后登录状态丢失")
            
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def print_connect_info(self):
        """打印连接信息"""
        logger.info("🔗 获取连接信息...")
        try:
            self.page.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 处理Cloudflare验证
            CloudflareHandler.handle_cloudflare(self.page)
            time.sleep(3)
            
            # 提取表格数据 - 改进的选择器
            page_html = self.page.html
            
            # 查找表格数据
            if 'linux.do' in self.site_config['base_url']:
                # Linux.do 特定的表格解析
                if '访问次数' in page_html or '已读帖子' in page_html:
                    logger.success("✅ 找到连接信息表格")
                    # 这里可以添加更精确的表格解析逻辑
                else:
                    logger.warning("⚠️ 未找到标准的连接信息表格")
            else:
                # IDCFlare 或其他站点
                logger.info("ℹ️ 连接信息表格格式可能不同")
            
            # 简单显示页面标题和关键信息
            page_title = self.page.title
            logger.info(f"📄 连接页面标题: {page_title}")
            
            # 检查是否包含关键指标
            key_indicators = ['访问次数', '回复', '浏览', '已读', '点赞', '获赞']
            found_indicators = [indicator for indicator in key_indicators if indicator in page_html]
            
            if found_indicators:
                logger.info(f"📊 找到关键指标: {', '.join(found_indicators)}")
            else:
                logger.warning("⚠️ 未找到关键连接指标")
                
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")

    def run(self):
        """执行完整自动化流程"""
        try:
            logger.info(f"🚀 开始处理站点: {self.site_name}")
            
            # 1. 确保登录（严格验证）
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
            
            # 2. 浏览主题（使用增强版本）
            browse_count = self.browse_topics_enhanced()
            
            # 3. 打印连接信息
            self.print_connect_info()
            
            # 4. 保存最终状态
            self.save_all_caches()
            
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
    """主函数"""
    logger.info("🚀 Linux.Do 多站点自动化脚本启动 (修复主题浏览版)")
    logger.info("=" * 80)
    
    # 配置日志
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>", level="INFO")
    
    success_sites = []
    failed_sites = []

    # 检查凭证配置
    for site_name, creds in SITE_CREDENTIALS.items():
        if not creds.get('username') or not creds.get('password'):
            logger.warning(f"⏭️ {site_name} 的用户名或密码未配置，将跳过该站点")

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
    # 检查环境变量
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD', 'IDCFLARE_USERNAME', 'IDCFLARE_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 以下环境变量未设置: {', '.join(missing_vars)}")
        logger.warning("请确保在运行前设置所有必要的环境变量")
    
    main()
