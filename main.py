#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinuxDo 多站点自动化脚本 - 最终修复版
版本：9.0 - 主题选择器修复版
"""

import os
import sys
import time
import random
import asyncio
import json
import traceback
import argparse
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright
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
HEADLESS_MODE = True

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

PAGE_TIMEOUT = 120000
RETRY_TIMES = 2
MAX_TOPICS_TO_BROWSE = 3

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

VIEWPORT_SIZES = [
    {'width': 1920, 'height': 1080},
    {'width': 1366, 'height': 768}
]

def parse_arguments():
    parser = argparse.ArgumentParser(description='LinuxDo 多站点自动化脚本')
    parser.add_argument('--site', type=str, help='指定运行的站点', 
                       choices=['linux_do', 'idcflare', 'all'], default='all')
    parser.add_argument('--verbose', action='store_true', help='详细输出模式')
    return parser.parse_args()

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

class CloudflareHandler:
    @staticmethod
    async def wait_for_cloudflare(page, timeout=30):
        """等待Cloudflare验证通过"""
        logger.info("⏳ 等待Cloudflare验证...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                title = await page.title()
                current_url = page.url
                
                if "请稍候" not in title and "Checking" not in title and "challenges" not in current_url:
                    logger.success("✅ Cloudflare验证已通过")
                    return True
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待Cloudflare时出错: {str(e)}")
                await asyncio.sleep(2)
        
        logger.warning("⚠️ Cloudflare等待超时，继续执行")
        return False

    @staticmethod
    async def save_cloudflare_cookies(context, site_name):
        try:
            cookies = await context.cookies()
            cf_cookies = [cookie for cookie in cookies if 'cf_' in cookie.get('name', '')]
            
            if cf_cookies:
                CacheManager.save_site_cache(cf_cookies, site_name, 'cf_cookies')
                logger.info(f"✅ 保存 {len(cf_cookies)} 个Cloudflare cookies")
                return True
        except Exception as e:
            logger.error(f"保存cookies失败: {str(e)}")
        return False

class BrowserManager:
    @staticmethod
    async def init_browser():
        playwright = await async_playwright().start()
        
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
        ]

        browser = await playwright.chromium.launch(
            headless=HEADLESS_MODE,
            args=browser_args
        )
        
        logger.info("🚀 浏览器已启动")
        return browser, playwright

    @staticmethod
    async def create_context(browser, site_name):
        storage_state = CacheManager.load_site_cache(site_name, 'browser_state')
        cf_cookies = CacheManager.load_site_cache(site_name, 'cf_cookies')
        
        user_agent = USER_AGENTS[hash(site_name) % len(USER_AGENTS)]
        viewport = VIEWPORT_SIZES[hash(site_name) % len(VIEWPORT_SIZES)]
        
        logger.info(f"🆔 {site_name} - UA: {user_agent[:50]}...")
        
        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            storage_state=storage_state,
            ignore_https_errors=True,
            java_script_enabled=True,
        )
        
        if cf_cookies:
            await context.add_cookies(cf_cookies)
            logger.info(f"✅ 已加载 {len(cf_cookies)} 个缓存cookies")
        
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        
        return context

class SiteAutomator:
    def __init__(self, site_config):
        self.site_config = site_config
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.is_logged_in = False
        self.credentials = SITE_CREDENTIALS.get(site_config['name'], {})
        
    async def run_for_site(self, browser, playwright):
        self.browser = browser
        self.playwright = playwright
        
        if not self.credentials.get('username'):
            logger.error(f"❌ {self.site_config['name']} 用户名未设置")
            return False
            
        try:
            self.context = await BrowserManager.create_context(browser, self.site_config['name'])
            self.page = await self.context.new_page()
            self.page.set_default_timeout(PAGE_TIMEOUT)
            
            login_success = await self.smart_login_approach()
            
            if login_success:
                logger.success(f"✅ {self.site_config['name']} 登录成功")
                await self.perform_browsing_actions()
                await self.print_connect_info()
                await self.save_session_data()
                return True
            else:
                logger.error(f"❌ {self.site_config['name']} 登录失败")
                return False
                
        except Exception as e:
            logger.error(f"💥 {self.site_config['name']} 执行异常: {str(e)}")
            return False
        finally:
            await self.cleanup()

    async def smart_login_approach(self):
        for attempt in range(RETRY_TIMES):
            logger.info(f"🔄 登录尝试 {attempt + 1}/{RETRY_TIMES}")
            
            try:
                if await self.try_direct_access():
                    return True
                
                if await self.full_login_process():
                    return True
                    
            except Exception as e:
                logger.error(f"登录尝试 {attempt + 1} 失败: {str(e)}")
            
            if attempt < RETRY_TIMES - 1:
                await self.clear_cache()
                await asyncio.sleep(10 * (attempt + 1))
        
        return False

    async def try_direct_access(self):
        try:
            logger.info("🔍 尝试直接访问...")
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(5)
            
            if await self.check_login_status():
                logger.success("✅ 缓存登录成功")
                return True
                
            return False
        except Exception as e:
            logger.debug(f"直接访问失败: {str(e)}")
            return False

    async def full_login_process(self):
        try:
            logger.info("🔐 开始完整登录流程")
            
            await self.page.goto(self.site_config['login_url'], timeout=90000)
            await asyncio.sleep(5)
            
            await CloudflareHandler.wait_for_cloudflare(self.page, timeout=30)
            
            if not await self.wait_for_login_form():
                logger.error("❌ 登录表单加载失败")
                return False
            
            username = self.credentials['username']
            password = self.credentials['password']
            
            await self.fill_login_form(username, password)
            
            if not await self.submit_login():
                return False
            
            return await self.verify_login_result()
            
        except Exception as e:
            logger.error(f"登录流程异常: {str(e)}")
            return False

    async def wait_for_login_form(self, max_wait=30):
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
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        logger.success(f"✅ 找到登录表单: {selector}")
                        return True
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.debug(f"等待登录表单时出错: {str(e)}")
                await asyncio.sleep(2)
        
        logger.error("❌ 登录表单等待超时")
        return False

    async def fill_login_form(self, username, password):
        try:
            username_selectors = ['#login-account-name', '#username', 'input[name="username"]']
            for selector in username_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    await element.click()
                    await asyncio.sleep(0.5)
                    await element.fill(username)
                    logger.info("✅ 已填写用户名")
                    break
            
            password_selectors = ['#login-account-password', '#password', 'input[name="password"]']
            for selector in password_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    await element.click()
                    await asyncio.sleep(0.5)
                    await element.fill(password)
                    logger.info("✅ 已填写密码")
                    break
            
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"填写登录表单失败: {str(e)}")

    async def submit_login(self):
        try:
            login_buttons = [
                '#login-button',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Log In")'
            ]
            
            for selector in login_buttons:
                button = await self.page.query_selector(selector)
                if button and await button.is_visible():
                    logger.info(f"✅ 找到登录按钮: {selector}")
                    await button.click()
                    logger.info("✅ 已点击登录按钮")
                    
                    await asyncio.sleep(8)
                    return True
            
            logger.error("❌ 未找到登录按钮")
            return False
            
        except Exception as e:
            logger.error(f"提交登录失败: {str(e)}")
            return False

    async def verify_login_result(self):
        logger.info("🔍 验证登录结果...")
        
        current_url = self.page.url
        if current_url != self.site_config['login_url']:
            logger.info("✅ 页面已跳转，可能登录成功")
            return await self.check_login_status()
        
        error_selectors = ['.alert-error', '.error', '.flash-error', '.alert-danger']
        for selector in error_selectors:
            error_element = await self.page.query_selector(selector)
            if error_element:
                error_text = await error_element.text_content()
                logger.error(f"❌ 登录错误: {error_text}")
                return False
        
        return await self.check_login_status()

    async def check_login_status(self):
        try:
            username = self.credentials['username']
            
            content = await self.page.content()
            if username.lower() in content.lower():
                logger.success(f"✅ 在页面内容中找到用户名: {username}")
                return True
            
            user_indicators = ['img.avatar', '.current-user', '[data-user-menu]', '.header-dropdown-toggle']
            for selector in user_indicators:
                element = await self.page.query_selector(selector)
                if element and await element.is_visible():
                    logger.success(f"✅ 找到用户元素: {selector}")
                    return True
            
            profile_url = f"{self.site_config['base_url']}/u/{username}"
            await self.page.goto(profile_url, timeout=30000)
            await asyncio.sleep(3)
            
            profile_content = await self.page.content()
            if username.lower() in profile_content.lower():
                logger.success(f"✅ 在个人资料页面验证用户名: {username}")
                await self.page.go_back(timeout=30000)
                return True
            
            logger.warning("❌ 无法验证登录状态")
            return False
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {str(e)}")
            return False

    async def perform_browsing_actions(self):
        if not await self.check_login_status():
            logger.error("❌ 未登录，跳过浏览")
            return
        
        try:
            logger.info("📚 开始浏览动作")
            
            await self.page.goto(self.site_config['latest_topics_url'], timeout=60000)
            await asyncio.sleep(3)
            
            # 修复主题选择器 - 使用更多可能的选择器
            topic_selectors = [
                'a.title',
                '.topic-list-item a',
                '.topic-title a',
                'a.raw-topic-link',
                'a[href*="/t/"]',
                '.title a',
                'tr.topic-list-item a',
                '.main-link a.title'
            ]
            
            topic_links = []
            for selector in topic_selectors:
                links = await self.page.query_selector_all(selector)
                if links:
                    topic_links.extend(links)
                    logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(links)} 个主题")
                    break
            
            if not topic_links:
                logger.warning("⚠️ 未找到主题链接，尝试备用选择器")
                # 备用选择器
                backup_selectors = ['a[href*="/t/"]', '.topic-list-body a']
                for selector in backup_selectors:
                    links = await self.page.query_selector_all(selector)
                    if links:
                        topic_links.extend(links)
                        logger.info(f"✅ 使用备用选择器 '{selector}' 找到 {len(links)} 个链接")
            
            logger.info(f"📖 总共找到 {len(topic_links)} 个主题链接")
            
            if not topic_links:
                logger.warning("⚠️ 未找到任何主题链接")
                return
            
            # 过滤出真正的主题链接
            valid_topic_links = []
            for link in topic_links:
                href = await link.get_attribute('href')
                if href and '/t/' in href and not href.endswith('/invite'):
                    valid_topic_links.append(link)
            
            logger.info(f"📖 过滤后得到 {len(valid_topic_links)} 个有效主题")
            
            if not valid_topic_links:
                logger.warning("⚠️ 没有有效的主题链接")
                return
            
            browse_count = min(MAX_TOPICS_TO_BROWSE, len(valid_topic_links))
            selected_topics = random.sample(valid_topic_links, browse_count)
            
            logger.info(f"🎯 浏览 {browse_count} 个主题")
            
            for i, topic in enumerate(selected_topics):
                try:
                    logger.info(f"🔍 浏览第 {i+1}/{browse_count} 个主题")
                    
                    href = await topic.get_attribute('href')
                    if href:
                        topic_url = urljoin(self.site_config['base_url'], href)
                        await self.browse_topic(topic_url)
                    
                    if i < browse_count - 1:
                        await asyncio.sleep(random.uniform(5, 10))
                        
                except Exception as e:
                    logger.error(f"浏览主题失败: {str(e)}")
            
            logger.success("✅ 浏览完成")
            
        except Exception as e:
            logger.error(f"浏览过程异常: {str(e)}")

    async def browse_topic(self, topic_url):
        try:
            new_page = await self.context.new_page()
            await new_page.goto(topic_url, timeout=60000)
            await asyncio.sleep(2)
            
            # 等待Cloudflare验证通过
            await CloudflareHandler.wait_for_cloudflare(new_page, timeout=15)
            
            page_title = await new_page.title()
            logger.info(f"📄 浏览: {page_title}")
            
            # 如果页面还是Cloudflare验证，跳过详细浏览
            if "请稍候" in page_title or "Checking" in page_title:
                logger.warning("⚠️ 主题页面仍在Cloudflare验证，跳过详细浏览")
                await new_page.close()
                return
            
            # 模拟阅读行为
            scroll_times = random.randint(2, 4)
            for i in range(scroll_times):
                scroll_amount = random.randint(400, 800)
                await new_page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                wait_time = random.uniform(2, 4)
                await asyncio.sleep(wait_time)
                
                # 随机点赞（概率较低）
                if random.random() < 0.1:
                    await self.try_like_post(new_page)
            
            await new_page.close()
            
        except Exception as e:
            logger.error(f"浏览主题失败: {str(e)}")

    async def try_like_post(self, page):
        try:
            like_buttons = await page.query_selector_all('.like-button, .btn-like, [data-like]')
            for button in like_buttons:
                if await button.is_visible():
                    await button.click()
                    logger.info("👍 点赞成功")
                    await asyncio.sleep(1)
                    return
        except Exception:
            pass

    async def print_connect_info(self):
        try:
            logger.info("🔗 获取连接信息")
            
            connect_page = await self.context.new_page()
            await connect_page.goto(self.site_config['connect_url'], timeout=60000)
            await asyncio.sleep(3)
            
            table = await connect_page.query_selector('table')
            if table:
                rows = await table.query_selector_all('tr')
                
                info = []
                for row in rows:
                    cells = await row.query_selector_all('td')
                    if len(cells) >= 3:
                        project = await cells[0].inner_text()
                        current = await cells[1].inner_text()
                        requirement = await cells[2].inner_text()
                        info.append([project.strip(), current.strip(), requirement.strip()])
                
                if info:
                    print("🔗 Connect 信息:")
                    print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="grid"))
                else:
                    logger.info("ℹ️ 未找到连接信息")
            else:
                logger.info("ℹ️ 未找到信息表格")
            
            await connect_page.close()
            
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")

    async def save_session_data(self):
        try:
            state = await self.context.storage_state()
            CacheManager.save_site_cache(state, self.site_config['name'], 'browser_state')
            
            await CloudflareHandler.save_cloudflare_cookies(self.context, self.site_config['name'])
            
            logger.info("💾 会话数据已保存")
            
        except Exception as e:
            logger.error(f"保存会话数据失败: {str(e)}")

    async def clear_cache(self):
        cache_files = [
            f"browser_state_{self.site_config['name']}.json",
            f"cf_cookies_{self.site_config['name']}.json"
        ]
        
        for file in cache_files:
            if os.path.exists(file):
                os.remove(file)
                logger.info(f"🗑️ 已清除: {file}")

    async def cleanup(self):
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass

async def main():
    args = parse_arguments()
    
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="DEBUG" if args.verbose else "INFO"
    )
    
    logger.info("🚀 LinuxDo自动化脚本启动 (最终修复版)")
    
    target_sites = SITES if args.site == 'all' else [s for s in SITES if s['name'] == args.site]
    
    browser, playwright = await BrowserManager.init_browser()
    
    try:
        results = []
        
        for site_config in target_sites:
            logger.info(f"🎯 处理站点: {site_config['name']}")
            
            automator = SiteAutomator(site_config)
            success = await automator.run_for_site(browser, playwright)
            
            results.append({
                'site': site_config['name'],
                'success': success
            })
            
            if site_config != target_sites[-1]:
                await asyncio.sleep(random.uniform(10, 20))
        
        logger.info("📊 执行结果:")
        table_data = [[r['site'], "✅" if r['success'] else "❌"] for r in results]
        print(tabulate(table_data, headers=['站点', '状态'], tablefmt='grid'))
        
        success_count = sum(1 for r in results if r['success'])
        logger.success(f"🎉 完成: {success_count}/{len(results)} 个站点成功")
        
    except Exception as e:
        logger.critical(f"💥 主流程异常: {str(e)}")
        traceback.print_exc()
    finally:
        await browser.close()
        await playwright.stop()
        logger.info("🔚 脚本结束")

if __name__ == "__main__":
    asyncio.run(main())
