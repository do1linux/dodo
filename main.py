"""
GitHub Actions 用
Linux.Do 自动登录 + 模拟人类浏览行为
支持 cookie 缓存 + 自动重试登录 + 覆盖旧缓存
"""

import os
import random
import time
import sys
import json
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# 日志
logger.remove()
logger.add(sys.stdout, level="INFO")

# 环境变量
USERNAME = os.getenv("LINUXDO_USERNAME")
PASSWORD = os.getenv("LINUXDO_PASSWORD")
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
COOKIE_FILE = "cache/linux_do_cookies.json"

# 常量
HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
CONNECT_URL = "https://connect.linux.do/"

# 浏览器
def get_browser():
    co = ChromiumOptions()
    co.headless(HEADLESS)
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-gpu")
    co.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    return Chromium(co)

# 保存 cookie
def save_cookies(page):
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(page.cookies(), f)
    logger.info("✅ Cookie 已保存并覆盖旧缓存")

# 加载 cookie
def load_cookies(page):
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            page.set_cookies(json.load(f))
        logger.info("✅ Cookie 已加载")
        return True
    return False

# 检测是否登录（通过头像 alt 是否等于用户名）
def is_logged_in(page):
    page.get(HOME_URL)
    time.sleep(3)
    user_ele = page.ele("@id=current-user")
    if not user_ele:
        return False
    img = user_ele.ele("tag:img")
    if img and img.attr("alt") == USERNAME:
        logger.info(f"✅ 检测到已登录用户：{USERNAME}")
        return True
    return False

# 登录（支持重试）
def login_with_retry(page):
    for attempt in range(3):
        logger.info(f"🚀 第 {attempt + 1} 次尝试登录...")
        page.get(LOGIN_URL)
        time.sleep(3)
        page.ele("@id=login-account-name").input(USERNAME, clear=True)
        time.sleep(random.uniform(1, 2))
        page.ele("@id=login-account-password").input(PASSWORD, clear=True)
        time.sleep(random.uniform(1, 2))
        page.ele("@id=login-button").click()
        time.sleep(5)
        if is_logged_in(page):
            save_cookies(page)
            return True
        else:
            logger.warning(f"❌ 第 {attempt + 1} 次登录失败")
    return False

# 浏览帖子
def browse_topics(page):
    page.get(HOME_URL)
    time.sleep(3)
    topics = page.eles(".topic-list-item .main-link a")
    if not topics:
        logger.warning("❌ 没有找到任何帖子")
        return
    logger.info(f"📚 发现 {len(topics)} 个帖子，随机浏览 10 个")
    for link in random.sample(topics, min(10, len(topics))):
        url = link.attr("href")
        if not url.startswith("http"):
            url = "https://linux.do" + url
        logger.info(f"👀 正在浏览：{url}")
        page.get(url)
        time.sleep(random.uniform(3, 6))
        for _ in range(random.randint(3, 6)):
            page.run_js(f"window.scrollBy(0, {random.randint(400, 700)})")
            time.sleep(random.uniform(2, 4))
        if random.random() < 0.3:
            like_btn = page.ele(".discourse-reactions-reaction-button")
            if like_btn:
                like_btn.click()
                logger.info("👍 点赞成功")
                time.sleep(1)

# 打印连接信息
def print_connect_info(page):
    page.get(CONNECT_URL)
    time.sleep(3)
    table = page.ele("tag:table")
    if not table:
        logger.warning("❌ 没有找到连接信息表格")
        return
    rows = [[td.text.strip() for td in tr.eles("tag:td")] for tr in table.eles("tag:tr") if tr.eles("tag:td")]
    print("-------------- Connect Info --------------")
    print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))

# 主函数
def main():
    if not USERNAME or not PASSWORD:
        logger.error("❌ 请设置 LINUXDO_USERNAME 和 LINUXDO_PASSWORD")
        sys.exit(1)

    browser = get_browser()
    page = browser.new_tab()

    # 尝试用 cookie 登录
    if load_cookies(page) and is_logged_in(page):
        logger.info("✅ 使用缓存 Cookie 登录成功")
    else:
        logger.info("❌ 缓存无效，重新登录")
        if not login_with_retry(page):
            logger.error("❌ 多次登录失败，跳过任务")
            browser.quit()
            return

    # 浏览帖子
    browse_topics(page)

    # 再次保存 cookie（防止更新）
    save_cookies(page)

    # 打印连接信息
    print_connect_info(page)

    logger.info("✅ 所有任务完成，最新 Cookie 已保存")
    browser.quit()

if __name__ == "__main__":
    main()
