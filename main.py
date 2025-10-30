import os
import random
import time
import functools
import sys
import requests
import re
from loguru import logger
from playwright.sync_api import sync_playwright
from tabulate import tabulate


def retry_decorator(retries=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    time.sleep(1)
            return None
        return wrapper
    return decorator


os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME") or os.environ.get('USERNAME')
PASSWORD = os.environ.get("LINUXDO_PASSWORD") or os.environ.get('PASSWORD')
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ['false', '0', 'off']

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"


class LinuxDoBrowser:
    def __init__(self) -> None:
        self.pw = sync_playwright().start()
        # 还原之前可能使用的chromium（更兼容统计逻辑），保留headless
        self.browser = self.pw.chromium.launch(headless=True, timeout=30000)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(HOME_URL, wait_until="networkidle")  # 等待网络空闲，确保页面加载完成

    def login(self):
        logger.info("开始登录")
        self.page.goto(LOGIN_URL, wait_until="networkidle")
        # 等待输入框加载完成，替代固定sleep
        self.page.wait_for_selector("#login-account-name", state="visible", timeout=10000)
        self.page.fill("#login-account-name", USERNAME)
        self.page.fill("#login-account-password", PASSWORD)
        self.page.click("#login-button")
        # 等待登录后页面跳转完成
        self.page.wait_for_url(HOME_URL, timeout=15000)
        user_ele = self.page.query_selector("#current-user")
        if not user_ele:
            logger.error("登录失败")
            return False
        logger.info("登录成功")
        return True

    def click_topic(self):
        # 等待主题列表加载完成
        self.page.wait_for_selector("#list-area .title", state="visible", timeout=10000)
        topic_list = self.page.query_selector_all("#list-area .title")
        logger.info(f"发现 {len(topic_list)} 个主题帖")
        # 随机选择3-5个主题（模拟真实用户不会全点），避免触发反爬
        selected_topics = random.sample(topic_list, k=min(5, len(topic_list)))
        for topic in selected_topics:
            self.click_one_topic(topic)  # 直接传入元素，而非链接

    @retry_decorator()
    def click_one_topic(self, topic_ele):
        # 同一页面跳转，不打开新页面（关键：保持会话连贯）
        topic_ele.click()
        self.page.wait_for_url(re.compile(r"https://linux.do/t/.*"), timeout=15000)  # 等待主题页加载
        logger.info(f"进入主题页：{self.page.url}")
        
        if random.random() < 0.3:
            self.click_like(self.page)
        self.browse_post(self.page)  # 浏览当前主题页
        
        # 返回主题列表，继续下一个（模拟真实浏览流程）
        self.page.go_back()
        self.page.wait_for_selector("#list-area .title", state="visible", timeout=10000)
        time.sleep(random.uniform(1, 2))

    def browse_post(self, page):
        # 模拟真实用户滚动：多次滚动+等待内容加载
        scroll_count = 0
        max_scroll = 15  # 增加最大滚动次数
        while scroll_count < max_scroll:
            # 滚动到页面底部（而非固定距离，确保加载所有内容）
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            logger.info(f"滚动至页面底部，当前滚动次数：{scroll_count + 1}")
            
            # 等待异步内容加载（关键：让网站记录浏览深度）
            page.wait_for_load_state("networkidle", timeout=5000)
            time.sleep(random.uniform(2.5, 4.5))  # 延长停留时间，确保统计生效
            
            # 检查是否真的到底（避免无限滚动）
            at_bottom = page.evaluate("""
                window.scrollY + window.innerHeight >= document.body.scrollHeight - 100
            """)
            if at_bottom:
                logger.info("已到达页面底部，结束当前主题浏览")
                break
            
            scroll_count += 1
            # 随机退出概率降低，确保足够浏览时长
            if random.random() < 0.01:
                logger.info("随机退出当前主题浏览")
                break

    def run(self):
        if not self.login():
            logger.error("登录失败，程序终止")
            sys.exit(1)

        if BROWSE_ENABLED:
            self.click_topic()
            logger.info("完成浏览任务")

        self.print_connect_info()

    def click_like(self, page):
        try:
            like_button = page.locator('.discourse-reactions-reaction-button[title="点赞此帖子"]').first
            if like_button.is_visible():
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                time.sleep(random.uniform(1, 2))
                logger.info("点赞成功")
            else:
                logger.info("帖子已点赞或无点赞按钮")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    def print_connect_info(self):
        logger.info("获取连接信息")
        page = self.context.new_page()
        page.goto("https://connect.linux.do/", wait_until="networkidle")
        page.wait_for_selector("table tr", state="visible", timeout=10000)
        rows = page.query_selector_all("table tr")
        info = []
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) >= 3:
                project = cells[0].text_content().strip()
                current = cells[1].text_content().strip()
                requirement = cells[2].text_content().strip()
                info.append([project, current, requirement])
        print("--------------Connect Info-----------------")
        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        page.close()

    def send_notifications(self, browse_enabled):
        status_msg = "✅每日登录成功"
        if browse_enabled:
            status_msg += " + 浏览任务完成"


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Please set USERNAME and PASSWORD")
        exit(1)
    l = LinuxDoBrowser()
    l.run()
