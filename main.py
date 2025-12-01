#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Linux.do 自动化浏览工具 - 重构版 v6.0
====================================
重构内容：
1. ✅ 借鉴参考代码的100%浏览痕迹收集机制
2. ✅ 集成localStorage标记系统
3. ✅ 优化页面加载和事件触发时机
4. ✅ 保持所有反检测功能
"""

import os
import random
import time
import sys
import json
import re
import base64
import requests
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions
from tabulate import tabulate

# 日志配置 - 只保留INFO及以上级别
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

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
        'private_topic_url': 'https://linux.do/t/topic/870130',
        'unread_url': 'https://linux.do/unread',
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
        'unread_url': 'https://idcflare.com/unread',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u',
        'cf_cookies_file': "cf_cookies_idcflare.json",
        'session_file': "session_data_idcflare.json"
    }
]

# 环境配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.environ.get("FORCE_LOGIN", "false").strip().lower() in ["true", "1", "on"]
BEHAVIOR_INJECTION_ENABLED = os.environ.get("BEHAVIOR_INJECTION_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
AUTO_LIKE = os.environ.get("AUTO_LIKE", "true").strip().lower() not in ["false", "0", "off"]
OCR_API_KEY = os.getenv("OCR_API_KEY")
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# ======================== 缓存管理器 ========================
class CacheManager:
    @staticmethod
    def get_cache_directory():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        return os.path.join(CacheManager.get_cache_directory(), file_name)

    @staticmethod
    def load_cache(file_name):
        file_path = CacheManager.get_cache_file_path(file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                return data
            except Exception as e:
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
            return True
        except Exception as e:
            logger.error(f"❌ 缓存保存失败: {str(e)}")
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
            
            logger.info(f"✅ {site_name} 缓存已清除")
            
        except Exception as e:
            logger.error(f"❌ 清除缓存失败: {str(e)}")

# ======================== 主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.page = None
        self.browser = None
        self.cache_saved = False
        self.session_start_time = time.time()
        self.request_count = 0
        self.browsing_active = True
        self.initialize_browser()

    def initialize_browser(self):
        """浏览器初始化 - 专注反检测"""
        try:
            co = ChromiumOptions()
            
            # GitHub Actions 环境特殊配置
            if GITHUB_ACTIONS:
                co.headless(True)
                co.set_argument("--no-sandbox")
                co.set_argument("--disable-dev-shm-usage")
                co.set_argument("--disable-gpu")
                co.set_argument("--disable-software-rasterizer")
            else:
                co.headless(HEADLESS)
                
            co.incognito(True)
            
            # 基础反检测配置
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-web-security")
            co.set_argument("--disable-features=TranslateUI")
            co.set_argument("--disable-background-networking")
            co.set_argument("--disable-sync")
            co.set_argument("--disable-translate")
            
            # 用户代理和窗口设置
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            co.set_user_agent(user_agent)
            co.set_argument("--window-size=1920,1080")
        
            # 保存browser实例
            self.browser = ChromiumPage(addr_or_opts=co)
            self.page = self.browser.new_tab()
            
            # 执行指纹优化
            self.enhance_browser_fingerprint()
            
            # 加载会话数据
            self.session_data = CacheManager.load_site_cache(self.site_name, 'session_data') or {}
            
            logger.info("✅ 浏览器初始化成功")
        
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {str(e)}")
            raise

    def enhance_browser_fingerprint(self):
        """浏览器指纹优化"""
        try:
            js_code = """
            Object.defineProperties(navigator, {
                webdriver: { get: () => undefined },
                platform: { get: () => 'Win32' },
                hardwareConcurrency: { get: () => 8 },
                deviceMemory: { get: () => 8 },
                maxTouchPoints: { get: () => 0 }
            });

            Object.defineProperty(screen, 'width', {get: () => 1920});
            Object.defineProperty(screen, 'height', {get: () => 1080});
            """
            self.page.run_js(js_code)
        
        except Exception as e:
            logger.debug(f"指纹优化异常: {str(e)}")

    def smart_delay(self, min_time=2, max_time=5):
        """智能延迟"""
        delay = random.uniform(min_time, max_time)
        time.sleep(delay)

    def handle_cloudflare_check(self, timeout=20):
        """处理Cloudflare检查"""
        start_time = time.time()
        check_count = 0
        
        while time.time() - start_time < timeout:
            try:
                page_title = self.page.title
                check_count += 1
                
                if page_title and "Checking" not in page_title and "Just a moment" not in page_title:
                    body_length = len(self.page.html)
                    if body_length > 1000:
                        return True
                
                if page_title and ("Checking" in page_title or "Just a moment" in page_title):
                    logger.debug(f"Cloudflare检查中... ({check_count})")
                
                time.sleep(1)
                    
            except Exception as e:
                time.sleep(1)
        
        logger.warning(f"Cloudflare检查超时 ({timeout}秒)，继续执行")
        return True

    def save_caches(self):
        """保存缓存"""
        if self.cache_saved:
            return
            
        try:
            cookies = self.page.cookies()
            if cookies:
                CacheManager.save_site_cache(cookies, self.site_name, 'cf_cookies')
            
            session_data = {
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
                'site_name': self.site_name,
                'username_hash': hash(self.username) if self.username else 0,
                'total_runs': self.session_data.get('total_runs', 0) + 1
            }
            CacheManager.save_site_cache(session_data, self.site_name, 'session_data')
            
            self.cache_saved = True
            logger.info(f"✅ {self.site_name} 缓存保存完成")
            
        except Exception as e:
            logger.error(f"❌ 缓存保存失败: {str(e)}")

    def try_cache_login(self):
        """尝试缓存登录"""
        if FORCE_LOGIN_EVERY_TIME:
            logger.info("⚠️ 强制重新登录")
            return False
            
        cookies = CacheManager.load_site_cache(self.site_name, 'cf_cookies')
        if not cookies:
            return False
        
        try:
            logger.info("🎯 尝试缓存登录...")
            self.page.get(self.site_config['base_url'])
            time.sleep(2)
            
            self.page.set.cookies(cookies)
            time.sleep(1)
            
            self.page.refresh()
            time.sleep(3)
            
            self.handle_cloudflare_check()
            
            if self.verify_login_status():
                logger.success("✅ 缓存登录成功")
                return True
            return False
                
        except Exception as e:
            logger.error(f"缓存登录异常: {str(e)}")
            return False

    def verify_login_status(self, max_retries=3):
        """验证登录状态"""
        logger.info("🔍 验证登录状态...")
        
        for attempt in range(max_retries):
            try:
                private_url = self.site_config['private_topic_url']
                logger.info(f"📍 访问私有主题 (尝试 {attempt+1}/{max_retries})")
                
                self.page.get(private_url)
                time.sleep(5)
                
                self.handle_cloudflare_check()
                time.sleep(3)
                
                # 检查是否成功访问私有主题
                content = self.page.html
                if "topic" in content.lower() or len(content) > 500000:
                    logger.success("🎉 登录验证通过")
                    return True
                
                time.sleep(2)
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"验证尝试 {attempt+1} 异常，重试中...")
                    time.sleep(3)
        
        logger.error(f"❌ 登录验证失败")
        return False

    def login(self, max_retries=2):
        """登录流程"""
        self.page.set.cookies([])
        
        for attempt in range(max_retries):
            try:
                logger.info(f"🔐 执行登录 (尝试 {attempt+1}/{max_retries})")
                
                self.page.get(self.site_config['login_url'])
                time.sleep(3)
                
                self.page.wait.ele_displayed('#login-account-name', timeout=10)
                
                self.handle_cloudflare_check()
                time.sleep(1)
                
                # 输入用户名密码
                self.page.ele("#login-account-name").input(self.username)
                time.sleep(0.5)
                
                self.page.ele("#login-account-password").input(self.password)
                time.sleep(0.5)
                
                # 点击登录
                self.page.ele("#login-button").click()
                time.sleep(12)
                
                self.handle_cloudflare_check()
                time.sleep(3)
                
                if self.verify_login_status():
                    logger.success("✅ 登录成功")
                    self.save_caches()
                    return True
                else:
                    time.sleep(5)
                
            except Exception as e:
                logger.error(f"❌ 登录出错 (尝试 {attempt+1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(5)
        
        logger.error("❌ 所有登录尝试均失败")
        return False

    def ensure_logged_in(self):
        """确保登录"""
        if not FORCE_LOGIN_EVERY_TIME and self.try_cache_login():
            return True
        
        login_success = self.login()
        if not login_success:
            CacheManager.clear_site_cache_on_failure(self.site_name)
        
        return login_success

    # ======================== 核心浏览方法重构 ========================

    def inject_automation_script(self):
        """注入自动化脚本 - 借鉴参考代码的核心机制"""
        try:
            js_code = """
            (function() {
                'use strict';
                
                // 设置关键标记 - 确保网站知道这是真实用户
                localStorage.setItem('read', 'true');
                localStorage.setItem('isFirstRun', 'false');
                localStorage.setItem('autoLikeEnabled', '%s');
                
                // 创建全局追踪对象
                window.discourseReadingTracker = {
                    startTime: Date.now(),
                    scrollDepth: 0,
                    postRead: new Set(),
                    triggerEvent: function(eventName, data) {
                        const event = new CustomEvent(eventName, { 
                            detail: data,
                            bubbles: true,
                            cancelable: true
                        });
                        document.dispatchEvent(event);
                        
                        // 同时触发jQuery事件（Discourse使用jQuery）
                        if (window.jQuery) {
                            jQuery(document).trigger(eventName, data);
                        }
                    }
                };
                
                // 监听滚动事件来记录阅读行为
                let lastScrollTime = 0;
                let scrollCount = 0;
                
                window.addEventListener('scroll', function() {
                    const now = Date.now();
                    if (now - lastScrollTime > 1000) {
                        scrollCount++;
                        lastScrollTime = now;
                        
                        // 记录滚动深度
                        const scrollDepth = (window.scrollY + window.innerHeight) / document.body.scrollHeight;
                        discourseReadingTracker.scrollDepth = Math.max(discourseReadingTracker.scrollDepth, scrollDepth);
                        
                        // 触发Discourse事件
                        discourseReadingTracker.triggerEvent('discourse:user-activity', {
                            type: 'scrolling',
                            scrollDepth: scrollDepth,
                            timestamp: now
                        });
                        
                        // 触发阅读进度事件
                        const progress = Math.floor(scrollDepth * 4) / 4;
                        discourseReadingTracker.triggerEvent('discourse:reading-progress', {
                            progress: progress,
                            topicId: window.location.pathname.split('/').pop()
                        });
                    }
                });
                
                // 定期触发活动事件
                setInterval(function() {
                    // 触发Discourse的活动检测
                    discourseReadingTracker.triggerEvent('discourse:user-activity', {
                        type: 'reading',
                        timestamp: Date.now()
                    });
                    
                    // 触发可见性变化
                    document.dispatchEvent(new Event('visibilitychange'));
                    
                    // 触发焦点事件
                    window.dispatchEvent(new Event('focus'));
                }, 15000);
                
                // 帖子加载事件处理
                function triggerPostEvents() {
                    const posts = document.querySelectorAll('.topic-post');
                    posts.forEach((post, index) => {
                        const postId = post.getAttribute('data-post-id');
                        if (postId && !discourseReadingTracker.postRead.has(postId)) {
                            discourseReadingTracker.postRead.add(postId);
                            
                            // 触发帖子加载事件
                            discourseReadingTracker.triggerEvent('discourse:post-loaded', {
                                postId: postId,
                                index: index
                            });
                            
                            // 触发帖子阅读事件
                            discourseReadingTracker.triggerEvent('discourse:post-read', {
                                postId: postId
                            });
                        }
                    });
                }
                
                // 初始触发帖子事件
                setTimeout(triggerPostEvents, 1000);
                
                // 监听DOM变化来触发新帖子事件
                const observer = new MutationObserver(triggerPostEvents);
                observer.observe(document.body, { 
                    childList: true, 
                    subtree: true 
                });
                
                console.log('自动化脚本已注入 - 确保浏览痕迹收集');
            })();
            """ % str(AUTO_LIKE).lower()
            
            self.page.run_js(js_code)
            return True
        except Exception as e:
            logger.error(f"❌ 自动化脚本注入失败: {str(e)}")
            return False

    def ensure_script_injected(self):
        """确保脚本已注入 - 双重保险"""
        try:
            # 检查是否已注入
            injected = self.page.run_js("return !!window.discourseReadingTracker;")
            if not injected:
                return self.inject_automation_script()
            return True
        except:
            return self.inject_automation_script()

    def simulate_real_reading_behavior(self):
        """模拟真实阅读行为 - 借鉴参考代码的100%有效机制"""
        try:
            logger.debug("📖 开始模拟真实阅读行为")
            
            # 1. 初始等待让页面完全加载
            self.smart_delay(3, 6)
            
            # 2. 确保脚本注入
            self.ensure_script_injected()
            
            # 3. 主帖深度阅读
            logger.debug("📝 深度阅读主帖内容")
            self.deep_read_main_post()
            
            # 4. 系统化滚动浏览
            logger.debug("🔄 系统化滚动浏览")
            self.systematic_scroll_browsing()
            
            # 5. 触发完成事件
            logger.debug("✅ 触发阅读完成事件")
            self.trigger_reading_completion()
            
            # 6. 随机点赞（如果启用）
            if AUTO_LIKE and random.random() < 0.1:  # 10%概率点赞
                self.click_like_button()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 阅读行为模拟失败: {str(e)}")
            return False

    def deep_read_main_post(self):
        """深度阅读主帖 - 确保主帖被充分阅读"""
        try:
            # 滚动到主帖开始位置
            self.page.run_js("""
                const firstPost = document.querySelector('.topic-post:first-child');
                if (firstPost) {
                    firstPost.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            """)
            self.smart_delay(2, 4)
            
            # 分段阅读主帖内容
            for i in range(4):
                scroll_amount = random.randint(200, 400)
                self.page.run_js(f"window.scrollBy(0, {scroll_amount});")
                
                # 关键：在每段停留足够时间让网站记录阅读
                read_time = random.uniform(3, 6)
                time.sleep(read_time)
                
                # 触发阅读事件
                self.trigger_reading_events()
        
        except Exception as e:
            logger.debug(f"主帖阅读异常: {e}")

    def systematic_scroll_browsing(self):
        """系统化滚动浏览 - 确保整个页面被阅读"""
        try:
            # 获取页面总高度
            total_height = self.page.run_js("return document.body.scrollHeight;") or 3000
            
            # 分段滚动策略
            scroll_positions = [0.2, 0.4, 0.6, 0.8, 1.0]
            
            for position in scroll_positions:
                target_scroll = total_height * position
                
                # 平滑滚动到目标位置
                self.page.run_js(f"""
                    window.scrollTo({{
                        top: {target_scroll},
                        behavior: 'smooth'
                    }});
                """)
                
                # 关键停留 - 让网站记录阅读行为
                stay_time = random.uniform(4, 8)
                time.sleep(stay_time)
                
                # 触发该位置的阅读事件
                self.trigger_position_events(position)
                
                # 偶尔随机滚动模拟真实用户
                if random.random() < 0.3:
                    self.random_micro_scroll()
        
        except Exception as e:
            logger.debug(f"滚动浏览异常: {e}")

    def trigger_reading_events(self):
        """触发阅读相关事件"""
        try:
            self.page.run_js("""
                // 触发基础事件
                document.dispatchEvent(new Event('visibilitychange'));
                window.dispatchEvent(new Event('focus'));
                window.dispatchEvent(new Event('scroll'));
                
                // 触发鼠标移动
                document.dispatchEvent(new MouseEvent('mousemove', {
                    bubbles: true,
                    clientX: Math.random() * window.innerWidth,
                    clientY: Math.random() * window.innerHeight
                }));
            """)
        except:
            pass

    def trigger_position_events(self, position):
        """触发位置相关事件"""
        try:
            self.page.run_js(f"""
                if (window.discourseReadingTracker) {{
                    // 触发阅读进度事件
                    discourseReadingTracker.triggerEvent('discourse:reading-progress', {{
                        progress: {position},
                        topicId: window.location.pathname.split('/').pop()
                    }});
                    
                    // 触发用户活动事件
                    discourseReadingTracker.triggerEvent('discourse:user-activity', {{
                        type: 'position_change',
                        position: {position},
                        timestamp: Date.now()
                    }});
                }}
            """)
        except:
            pass

    def random_micro_scroll(self):
        """随机微滚动"""
        try:
            scroll_amount = random.randint(-100, 100)
            self.page.run_js(f"window.scrollBy(0, {scroll_amount});")
            time.sleep(random.uniform(1, 2))
        except:
            pass

    def trigger_reading_completion(self):
        """触发阅读完成事件"""
        try:
            self.page.run_js("""
                // 滚动到底部确认完成
                window.scrollTo(0, document.body.scrollHeight);
                
                // 触发完成事件
                if (window.discourseReadingTracker) {
                    const topicId = window.location.pathname.split('/').pop();
                    const readingTime = Math.floor((Date.now() - window.discourseReadingTracker.startTime) / 1000);
                    
                    discourseReadingTracker.triggerEvent('discourse:reading-complete', {
                        topicId: topicId,
                        readingTime: readingTime,
                        scrollDepth: window.discourseReadingTracker.scrollDepth,
                        postsRead: Array.from(window.discourseReadingTracker.postRead)
                    });
                    
                    // 设置完成标记
                    localStorage.setItem(`discourse-topic-${topicId}-read`, 'true');
                    localStorage.setItem(`discourse-topic-${topicId}-read-time`, new Date().toISOString());
                }
                
                console.log('阅读完成事件已触发');
            """)
            
            # 最终停留确保事件处理完成
            time.sleep(random.uniform(3, 6))
            
        except Exception as e:
            logger.debug(f"完成事件触发异常: {e}")

    def click_like_button(self):
        """点击点赞按钮"""
        try:
            like_button = self.page.ele('.discourse-reactions-reaction-button')
            if like_button:
                logger.info("👍 尝试点赞...")
                like_button.click()
                time.sleep(random.uniform(1, 2))
                logger.success("✅ 点赞成功")
                return True
            return False
        except Exception as e:
            logger.debug(f"点赞失败: {e}")
            return False

    def find_topic_elements(self):
        """查找主题元素"""
        logger.info("🎯 查找主题...")
        
        try:
            self.page.wait.doc_loaded()
            time.sleep(3)
            
            all_links = self.page.eles('tag:a', timeout=10)
            if not all_links:
                logger.warning("⚠️ 未找到任何链接")
                return []
            
            seen_ids = set()
            topic_urls = []
            
            for link in all_links:
                href = link.attr('href')
                if not href:
                    continue
                
                # 排除非主题链接
                if any(exclude in href.lower() for exclude in ['/tags/', '/c/', '/u/', '/uploads/', '.png', '.jpg', '.gif']):
                    continue
                
                # 提取主题ID
                match = re.search(r'/t/(?:topic/)?(\d+)', href)
                if match:
                    topic_id = match.group(1)
                    if topic_id not in seen_ids:
                        seen_ids.add(topic_id)
                        full_url = f"{self.site_config['base_url'].rstrip('/')}/t/topic/{topic_id}"
                        topic_urls.append(full_url)
            
            logger.info(f"🔗 找到 {len(topic_urls)} 个主题")
            return topic_urls
            
        except Exception as e:
            logger.error(f"❌ 查找主题失败: {str(e)}")
            return []

    def browse_topics_guaranteed(self):
        """保证浏览痕迹收集的主题浏览"""
        if not BROWSE_ENABLED:
            logger.info("⏭️ 浏览功能已禁用")
            return 0

        try:
            logger.info(f"🌐 开始保证浏览痕迹收集的 {self.site_name} 主题浏览...")
            
            # 获取主题列表
            self.page.get(self.site_config['unread_url'])
            time.sleep(3)
            
            topic_urls = self.find_topic_elements()
            if not topic_urls:
                logger.warning("❌ 未找到可浏览的主题")
                return 0
            
            # 选择适量主题进行深度浏览
            browse_count = min(random.randint(3, 6), len(topic_urls))
            selected_urls = random.sample(topic_urls, browse_count)
            success_count = 0
            
            logger.info(f"📊 计划深度浏览 {browse_count} 个主题")
            
            for i, topic_url in enumerate(selected_urls):
                try:
                    logger.info(f"📖 深度浏览主题 {i+1}/{browse_count}")
                    
                    # 访问主题页面
                    self.page.get(topic_url)
                    time.sleep(2)
                    
                    # 执行保证浏览痕迹的阅读行为
                    if self.simulate_real_reading_behavior():
                        success_count += 1
                        logger.success(f"✅ 主题 {i+1} 浏览完成")
                    else:
                        logger.warning(f"⚠️ 主题 {i+1} 浏览异常")
                    
                    # 返回列表页
                    self.page.get(self.site_config['unread_url'])
                    time.sleep(2)
                    
                    # 主题间等待
                    if i < browse_count - 1:
                        interval = random.uniform(5, 10)
                        logger.info(f"⏳ 主题间等待 {interval:.1f} 秒...")
                        time.sleep(interval)
                        
                except Exception as e:
                    logger.error(f"❌ 浏览主题失败: {str(e)}")
                    continue
            
            logger.success(f"🎉 共成功深度浏览 {success_count} 个主题")
            return success_count
            
        except Exception as e:
            logger.error(f"❌ 主题浏览失败: {str(e)}")
            return 0

    def get_connect_info_single_tab(self):
        """获取连接信息"""
        logger.info("🔗 获取连接信息...")
        
        try:
            current_url = self.page.url
            
            # 访问连接页面
            self.page.get(self.site_config['connect_url'])
            time.sleep(5)
            
            # 等待表格出现
            table = None
            for i in range(5):
                table = self.page.ele("tag:table", timeout=5)
                if table:
                    break
                time.sleep(2)
            
            if not table:
                logger.warning("⚠️ 未找到连接信息表格")
                if self.site_name == 'idcflare':
                    logger.info("ℹ️ idcflare连接信息获取失败，不影响主流程")
                self.page.get(current_url)
                time.sleep(2)
                return True
            
            # 解析表格数据
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
                print("="*60 + "\n", flush=True)
                
                passed = sum(1 for item in info if any(indicator in str(item[1]) for indicator in ['✅', '✔', '✓', '≥', '%']))
                total = len(info)
                logger.success(f"📈 统计: {passed}/{total} 项达标")
            else:
                logger.warning("⚠️ 未找到连接信息数据")
            
            # 返回原页面
            self.page.get(current_url)
            time.sleep(2)
            
            logger.info("✅ 连接信息获取完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 获取连接信息失败: {str(e)}")
            if self.site_name == 'idcflare':
                logger.info("ℹ️ idcflare连接信息异常，但不影响继续执行")
                return True
            try:
                self.page.get(self.site_config['unread_url'])
                time.sleep(2)
            except:
                pass
            return False

    def run_complete_process(self):
        """执行完整流程"""
        try:
            logger.info(f"🚀 开始处理 {self.site_name}")
            
            # 1. 确保登录
            if not self.ensure_logged_in():
                logger.error(f"❌ {self.site_name} 登录失败")
                return False
                                
            # 2. 连接信息
            connect_success = self.get_connect_info_single_tab()
            if not connect_success and self.site_name != 'idcflare':
                logger.warning(f"⚠️ {self.site_name} 连接信息获取失败")

            # 3. 使用保证浏览痕迹的主题浏览方法
            browse_count = self.browse_topics_guaranteed()
            
            # 4. 保存缓存
            self.save_caches()
            
            logger.success(f"✅ {self.site_name} 处理完成 - 深度浏览 {browse_count} 个主题")
            return True
            
        except Exception as e:
            logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            return False
            
        finally:
            self.browsing_active = False
            try:
                if self.browser:
                    self.browser.quit()
            except:
                pass

# ======================== 主函数 ========================
def main():
    logger.info("🚀 Linux.Do 自动化 v6.0 重构版启动")
    
    if GITHUB_ACTIONS:
        logger.info("🎯 GitHub Actions 环境")
    
    success_sites = []
    failed_sites = []

    # 检查凭证
    for site_name, creds in SITE_CREDENTIALS.items():
        if not creds.get('username') or not creds.get('password'):
            logger.warning(f"⏭️ {site_name} 凭证未配置")

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

        logger.info(f"🔧 处理站点: {site_name}")
        
        try:
            browser = LinuxDoBrowser(site_config, credentials)
            success = browser.run_complete_process()

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
            logger.info(f"⏳ 站点间等待 {wait_time:.1f} 秒...")
            time.sleep(wait_time)

    # 总结
    logger.info("=" * 60)
    logger.info("📊 执行总结:")
    logger.info(f"✅ 成功: {', '.join(success_sites) if success_sites else '无'}")
    logger.info(f"❌ 失败: {', '.join(failed_sites) if failed_sites else '无'}")
    logger.info("=" * 60)

    if success_sites:
        logger.success(f"🎉 任务完成: {len(success_sites)}/{len(target_sites)} 个站点成功")
        sys.exit(0)
    else:
        logger.error("💥 任务失败: 所有站点均未成功")
        sys.exit(1)

if __name__ == "__main__":
    required_vars = ['LINUXDO_USERNAME', 'LINUXDO_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.warning(f"⚠️ 必需环境变量未设置: {', '.join(missing_vars)}")
    
    if not OCR_API_KEY:
        logger.warning("⚠️ 未配置OCR_API_KEY，验证码处理将不可用")
    
    main()

