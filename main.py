"""
================================================================================
Linux.Do & IDCFlare 多站点自动浏览脚本
cron: 0 * * * *
================================================================================
"""

import os
import sys
import json
import time
import random
import functools
from datetime import datetime, timedelta
from pathlib import Path

import requests
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# -------------------- 全局配置 --------------------
HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"false", "0", "off"}
BROWSE_ENABLED = os.getenv("BROWSE_ENABLED", "true").lower() not in {"false", "0", "off"}
SITE_SELECTOR = os.getenv("SITE_SELECTOR", "all")  # all / linux_do / idcflare
COOKIE_VALIDITY_DAYS = 7

# -------------------- 工具：缓存管理 --------------------
CACHE_DIR = Path(__file__).with_suffix("") / "cache"
CACHE_DIR.mkdir(exist_ok=True)


class CacheManager:
    @staticmethod
    def path(name: str) -> Path:
        return CACHE_DIR / f"{name}_cookies.json"

    @staticmethod
    def load(name: str):
        file = CacheManager.path(name)
        if not file.exists():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf8"))
            cache_time = datetime.fromisoformat(data["cache_time"])
            if datetime.now() - cache_time > timedelta(days=COOKIE_VALIDITY_DAYS):
                logger.warning("🕒 Cookies 已过期")
                return None
            logger.info(f"📦 加载 {name} 缓存")
            return data["cookies"]
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
            return None

    @staticmethod
    def save(name: str, cookies):
        try:
            data = {"cookies": cookies, "cache_time": datetime.now().isoformat()}
            CacheManager.path(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
            logger.info(f"💾 已保存 {name} cookies")
            return True
        except Exception as e:
            logger.error(f"缓存写入失败: {e}")
            return False


# -------------------- 工具：重试装饰器 --------------------
def retry(retries: int = 3, delay: int = 2):
    def deco(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"{func.__name__} 第 {i}/{retries} 次失败: {e}")
                    if i == retries:
                        raise
                    time.sleep(delay)
        return wrapper

    return deco


# -------------------- 站点配置 --------------------
SITES = [
    {
        "name": "linux_do",
        "base": "https://linux.do",
        "login": "https://linux.do/login",
        "latest": "https://linux.do/latest",
        "connect": "https://connect.linux.do",
        "user": os.getenv("LINUXDO_USERNAME"),
        "pass": os.getenv("LINUXDO_PASSWORD"),
    },
    {
        "name": "idcflare",
        "base": "https://idcflare.com",
        "login": "https://idcflare.com/login",
        "latest": "https://idcflare.com/latest",
        "connect": "https://connect.idcflare.com",
        "user": os.getenv("IDCFLARE_USERNAME"),
        "pass": os.getenv("IDCFLARE_PASSWORD"),
    },
]

# 过滤需要跑的站点
if SITE_SELECTOR != "all":
    SITES = [s for s in SITES if s["name"] == SITE_SELECTOR]
for s in SITES:
    if not s["user"] or not s["pass"]:
        logger.error(f"❌ {s['name']} 用户名或密码未配置")
        sys.exit(1)


# -------------------- 浏览器封装 --------------------
class AutoBrowser:
    def __init__(self, site: dict):
        self.site = site
        self.name = site["name"]
        self.user = site["user"]
        self.passwd = site["pass"]
        self.page = None
        self.browser = None

    # ---------- 浏览器启动 ----------
    def start_browser(self):
        co = (
            ChromiumOptions()
            .headless(HEADLESS)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-dev-shm-usage")
            .set_argument("--lang=zh-CN,zh;q=0.9")
        )
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        self._inject_anti_detect()

    def _inject_anti_detect(self):
        script = """
        Object.defineProperty(navigator, 'webdriver', {get: ()=> undefined});
        window.chrome = {runtime:{}, loadTimes(){}, csi(){}, app:{isInstalled:false}};
        Object.defineProperty(document, 'hidden', {get: ()=> false});
        Object.defineProperty(document, 'visibilityState', {get: ()=> 'visible'});
        """
        self.page.run_js(script)
        logger.info("✅ 反检测脚本已注入")

    # ---------- Cookie 复用 ----------
    def load_cookies_to_browser(self, cookies):
        self.page.get(self.site["base"])
        for c in cookies:
            self.page.set.cookie(c)
        logger.info("🍪 缓存 Cookie 已写入")

    # ---------- 登录 ----------
    @retry(retries=3, delay=3)
    def login(self):
        logger.info(f"🔐 开始登录 {self.name}")
        self.page.get(self.site["login"])
        time.sleep(3)

        # 处理 Turnstile
        self._handle_turnstile()

        # 慢速输入
        self._human_input(self.page.ele("@id=login-account-name"), self.user)
        time.sleep(random.uniform(0.8, 1.5))
        self._human_input(self.page.ele("@id=login-account-password"), self.passwd)
        time.sleep(random.uniform(0.8, 1.5))

        self.page.ele("@id=login-button").click()
        time.sleep(5)

        # 必须检测到用户名
        if not self._verify_login():
            self.page.get_screenshot(f"{self.name}_login_fail.png")
            raise Exception("未检测到用户名，登录失败")
        logger.success("✅ 登录成功")
        cookies = self.page.cookies()
        CacheManager.save(self.name, cookies)
        return True

    # ---------- Turnstile ----------
    def _handle_turnstile(self):
        logger.info("🛡️ 处理 Turnstile")
        for i in range(8):
            token = self.page.run_js("return (window.turnstile && turnstile.getResponse()) || null")
            if token:
                logger.success("🎫 取得 Turnstile token")
                return
            try:
                iframe = self.page("tag:iframe")
                if iframe:
                    iframe.ele("tag:input").click()
                    logger.info("🖱️ 模拟点击 Turnstile 框")
            except:
                pass
            time.sleep(random.uniform(1, 2))

    # ---------- 验证登录 ----------
    def _verify_login(self):
        html = self.page.html.lower()
        if self.user.lower() in html:
            logger.success("✅ 页面源码含用户名")
            return True
        # 头像检测
        avatar = self.page.ele("#current-user img.avatar")
        if avatar:
            logger.success("✅ 检测到用户头像")
            return True
        return False

    # ---------- 慢速输入 ----------
    @staticmethod
    def _human_input(ele, text: str):
        ele.clear()
        for ch in text:
            ele.input(ch)
            time.sleep(random.uniform(0.05, 0.15))

    # ---------- 浏览 ----------
    def browse(self):
        logger.info("🚀 开始浏览主题")
        self.page.get(self.site["latest"])
        time.sleep(3)

        topics = self.page.eles(".//a[@class='title raw-link raw-topic-link']", timeout=5)[:15]
        if not topics:
            logger.warning("未找到主题")
            return
        samples = random.sample(topics, min(10, len(topics)))
        for no, topic in enumerate(samples, 1):
            logger.info(f"🔍 第{no:02d}个主题")
            self._browse_one(topic.attr("href"))

    @retry(retries=2, delay=2)
    def _browse_one(self, url):
        tab = self.browser.new_tab()
        tab.get(url)
        time.sleep(random.uniform(2, 4))

        # 随机点赞 0.3%
        if random.random() < 0.003:
            try:
                like_btn = tab.ele(".discourse-reactions-reaction-button")
                if like_btn:
                    like_btn.click()
                    logger.success("👍 随机点赞")
                    time.sleep(random.uniform(1, 2))
            except:
                pass

        # 随机滚动
        for _ in range(random.randint(5, 10)):
            if random.random() < 0.03:
                logger.info("🛑 随机退出浏览")
                break
            dist = random.randint(550, 650)
            tab.run_js(f"window.scrollBy(0,{dist})")
            logger.info(f"⬇️ 滚动 {dist}px")
            time.sleep(random.uniform(2, 4))

        tab.close()

    # ---------- Connect 信息 ----------
    def print_connect(self):
        try:
            self.page.get(self.site["connect"])
            time.sleep(3)
            rows = []
            for tr in self.page.eles("tag:table tag:tr")[1:]:
                tds = tr.eles("tag:td")
                if len(tds) >= 3:
                    rows.append([td.text for td in tds[:3]])
            if rows:
                print("-------------- Connect Info -----------------")
                print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        except Exception as e:
            logger.warning(f"获取 Connect 信息失败: {e}")

    # ---------- 主入口 ----------
    def run(self):
        try:
            self.start_browser()
            cookies = CacheManager.load(self.name)
            if cookies:
                self.load_cookies_to_browser(cookies)
                self.page.get(self.site["latest"])
                time.sleep(3)
                if self._verify_login():
                    logger.info("🎉 缓存登录有效，跳过登录")
                else:
                    logger.info("🔄 缓存失效，重新登录")
                    self.login()
            else:
                self.login()

            if BROWSE_ENABLED:
                self.browse()
            self.print_connect()
            logger.success(f"{self.name} 全流程完成")
        except Exception as e:
            logger.error(f"{self.name} 运行失败: {e}")
            self.page.get_screenshot(f"{self.name}_error.png")
            raise
        finally:
            self.browser.quit()


# -------------------- 主程序 --------------------
def main():
    logger.add(
        "run.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        encoding="utf8",
    )
    logger.info("===== 多站点自动浏览开始 =====")
    for site in SITES:
        try:
            AutoBrowser(site).run()
        except Exception as e:
            logger.error(f"{site['name']} 站点异常: {e}")
            continue
    logger.info("===== 全部站点执行完毕 =====")


if __name__ == "__main__":
    main()
