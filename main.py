#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
混合优化版本：结合两个版本的优点
- 版本1的连接信息获取逻辑
- 版本2的单标签页策略和会话保持
- 改进的表格选择器和错误处理
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
        'user_url': 'https://linux.do/u'
    },
    {
        'name': 'idcflare', 
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'private_topic_url': 'https://idcflare.com/t/topic/24',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u'
    }
]

# 环境配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

class HybridBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.page = None
        self.initialize_browser()

    def initialize_browser(self):
        """优化的浏览器初始化"""
        try:
            co = ChromiumOptions()
            
            if GITHUB_ACTIONS:
                logger.info("🎯 GitHub Actions 环境优化")
                co.headless(True)
                co.set_argument("--disable-dev-shm-usage")
                co.set_argument("--no-sandbox")
                co.set_argument("--disable-gpu")
            else:
                co.headless(False)
                
            co.incognito(True)
            co.set_argument("--disable-blink-features=AutomationControlled")
            
            # 用户代理设置
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            co.set_argument("--window-size=1920,1080")
            
            self.page = ChromiumPage(addr_or_opts=co)
            
            # 简化指纹优化
            self.page.run_js("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { 
                    get: () => [1, 2, 3, 4, 5] 
                });
            """)
            
            logger.info(f"✅ {self.site_name} 浏览器初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def smart_delay(self, min_time=2, max_time=5):
        """智能延迟"""
        delay = random.uniform(min_time, max_time)
        time.sleep(delay)

    def verify_login_status(self):
        """登录状态验证"""
        logger.info("🔍 验证登录状态...")
        
        try:
            self.page.get(self.site_config['private_topic_url'])
            self.smart_delay(2, 3)
            
            page_content = self.page.html
            page_title = self.page.title
            
            if "Page Not Found" in page_content or "页面不存在" in page_content:
                logger.error("❌ 私有主题访问失败")
                return False
            
            logger.success("✅ 私有主题访问成功")
            
            if self.username.lower() in page_content.lower():
                logger.success(f"✅ 找到用户名: {self.username}")
                return True
            else:
                logger.error(f"❌ 未找到用户名: {self.username}")
                return False
            
        except Exception as e:
            logger.error(f"❌ 登录验证异常: {str(e)}")
            return False

    def login(self):
        """登录流程"""
        logger.info("🔐 执行登录...")
        
        try:
            self.page.get(self.site_config['login_url'])
            self.smart_delay(2, 3)
            
            # 输入用户名
            username_field = self.page.ele("#login-account-name")
            if username_field:
                username_field.input(self.username)
                self.smart_delay(0.5, 1)
            
            # 输入密码
            password_field = self.page.ele("#login-account-password")
            if password_field:
                password_field.input(self.password)
                self.smart_delay(0.5, 1)
            
            # 点击登录
            login_button = self.page.ele("#login-button")
            if login_button:
                login_button.click()
                self.smart_delay(3, 5)
                
                return self.verify_login_status()
            
            return False
            
        except Exception as e:
            logger.error(f"❌ 登录过程出错: {str(e)}")
            return False

    def find_topic_elements(self):
        """查找主题链接"""
        logger.info("🎯 查找主题...")
        
        try:
            all_links = self.page.eles('tag:a')
            topic_links = []
            seen_urls = set()
            
            for link in all_links:
                href = link.attr('href')
                if not href:
                    continue
                
                if '/t/' in href and not any(exclude in href for exclude in ['/tags/', '/c/', '/u/']):
                    if not href.startswith('http'):
                        href = self.site_config['base_url'] + href
                    
                    base_url = re.sub(r'/t/topic/(\d+)(/\d+)?', r'/t/topic/\1', href)
                    
                    if base_url not in seen_urls:
                        seen_urls.add(base_url)
                        topic_links.append(base_url)
            
            logger.info(f"🔗 找到 {len(topic_links)} 个主题")
            return topic_links
            
        except Exception as e:
            logger.error(f"❌ 查找主题失败: {str(e)}")
            return []

    def browse_topics_optimized(self):
        """优化的主题浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0

        try:
            logger.info(f"🌐 开始浏览 {self.site_name} 主题...")
            
            self.page.get(self.site_config['latest_url'])
            self.smart_delay(3, 5)
            
            topic_urls = self.find_topic_elements()
            if not topic_urls:
                return 0
            
            browse_count = min(random.randint(2, 3), len(topic_urls))
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划浏览 {browse_count} 个主题")
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 浏览主题 {i+1}/{browse_count}")
                    
                    self.page.get(topic_url)
                    self.smart_delay(3, 5)
                    
                    # 模拟阅读行为
                    self.simulate_reading_behavior()
                    
                    success_count += 1
                    logger.info(f"✅ 成功浏览主题 {i+1}")
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        wait_time = random.uniform(15, 25)
                        logger.info(f"⏳ 等待 {wait_time:.1f} 秒...")
                        
                        self.page.get(self.site_config['latest_url'])
                        time.sleep(wait_time)
                            
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 主题浏览失败: {str(e)}")
            return 0

    def simulate_reading_behavior(self):
        """模拟阅读行为"""
        try:
            # 随机滚动次数
            scroll_count = random.randint(3, 6)
            
            for i in range(scroll_count):
                scroll_distance = random.randint(300, 800)
                self.page.run_js(f"window.scrollBy(0, {scroll_distance});")
                time.sleep(random.uniform(2, 4))
                
                # 偶尔触发交互
                if random.random() < 0.3:
                    self.page.run_js("""
                        document.dispatchEvent(new MouseEvent('mousemove', {
                            bubbles: true,
                            clientX: Math.random() * window.innerWidth,
                            clientY: Math.random() * window.innerHeight
                        }));
                    """)
            
        except Exception as e:
            logger.debug(f"阅读行为模拟异常: {str(e)}")

    def get_connect_info_improved(self):
        """改进的连接信息获取"""
        logger.info("🔗 获取连接信息...")
        
        try:
            current_url = self.page.url
            
            # 访问连接页面
            self.page.get(self.site_config['connect_url'])
            self.smart_delay(3, 5)
            
            # 多种表格选择器尝试
            table_selectors = [
                "tag:table",
                ".table",
                "table",
                "[class*='table']"
            ]
            
            table = None
            for selector in table_selectors:
                table = self.page.ele(selector)
                if table:
                    break
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                # 对于idcflare，失败不影响继续执行
                if self.site_name == 'idcflare':
                    logger.info("ℹ️ idcflare连接信息获取失败，但不影响继续执行")
                self.page.get(current_url)
                return True
            
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
                print("\n" + "="*60)
                print(f"📊 {self.site_name.upper()} 连接信息")
                print("="*60)
                print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
                print("="*60 + "\n")
                
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
                total = len(info)
                logger.success(f"📈 统计: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 返回原页面
            self.page.get(current_url)
            self.smart_delay(2, 3)
            
            logger.info("✅ 连接信息获取完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")
            # idcflare失败不影响
            if self.site_name == 'idcflare':
                logger.info("ℹ️ idcflare连接信息异常，但不影响继续执行")
                return True
            return False

    def run_optimized_process(self):
        """优化执行流程"""
        try:
            logger.info(f"🚀 开始处理 {self.site_name}")
            
            # 1. 登录验证
            if not self.verify_login_status():
                if not self.login():
                    logger.error(f"❌ {self.site_name} 登录失败")
                    return False
            
            # 2. 主题浏览
            browse_count = self.browse_topics_optimized()
            
            # 3. 连接信息（改进版本）
            self.get_connect_info_improved()
            
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

def main():
    logger.info("🚀 Linux.Do 混合优化版启动")
    
    success_sites = []
    failed_sites = []

    # 站点选择
    site_selector = os.environ.get("SITE_SELECTOR", "all")
    target_sites = SITES if site_selector == "all" else [s for s in SITES if s['name'] == site_selector]

    if not target_sites:
        logger.error(f"❌ 未找到站点: {site_selector}")
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
        logger.info(f"🔧 处理站点: {site_name}")
        
        try:
            browser = HybridBrowser(site_config, credentials)
            success = browser.run_optimized_process()

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
            logger.info(f"⏳ 站点间等待 {wait_time:.1f} 秒...")
            time.sleep(wait_time)

    # 总结
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
    main()
