"""
================================================================================
Linux.Do & IDCFlare 多站点自动浏览脚本
cron: 0 * * * *
env: 仅需在 GitHub Secrets 配以下 4 个变量
     LINUXDO_USERNAME / LINUXDO_PASSWORD
     IDCFLARE_USERNAME / IDCFLARE_PASSWORD
================================================================================
"""
import os
import sys
import json
import time
import random
import functools
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# -------------------- 基础配置 --------------------
HEADLESS   = os.getenv("HEADLESS", "true").lower() not in {"false", "0", "off"}
BROWSE_EN  = os.getenv("BROWSE_ENABLED", "true").lower() not in {"false", "0", "off"}
SELECTOR   = os.getenv("SITE_SELECTOR", "all")          # all / linux_do / idcflare
CACHE_DIR  = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# -------------------- 缓存工具 --------------------
def cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}_cookies.json"

def load_cookies(name: str):
    f = cache_path(name)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf8"))
        if datetime.now() - datetime.fromisoformat(data["cache_time"]) > timedelta(days=7):
            logger.warning("🕒 Cookies 已过期")
            return None
        logger.info(f"📦 加载 {name} 缓存")
        return data["cookies"]
    except Exception as e:
        logger.warning(f"缓存读取失败: {e}")
        return None

def save_cookies(name: str, cookies) -> bool:
    try:
        data = {"cookies": cookies, "cache_time": datetime.now().isoformat()}
        cache_path(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
        logger.info(f"💾 保存 {name} cookies")
        return True
    except Exception as e:
        logger.error(f"缓存写入失败: {e}")
        return False

# -------------------- 重试装饰器 --------------------
def retry(retries: int = 3, delay: int = 2):
    def deco(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if i == retries:
                        logger.error(f"{func.__name__} 最终失败: {e}")
                        raise
                    logger.warning(f"{func.__name__} 第 {i}/{retries} 次失败: {e}")
                    time.sleep(delay)
        return wrapper
    return deco

# -------------------- 站点列表 --------------------
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
if SELECTOR != "all":
    SITES = [s for s in SITES if s["name"] == SELECTOR]
for s in SITES:
    if not (s["user"] and s["pass"]):
        logger.error(f"❌ {s['name']} 用户名或密码未配置")
        sys.exit(1)

# -------------------- 浏览器类 --------------------
class AutoBrowser:
    def __init__(self, site: dict):
        self.site  = site
        self.name  = site["name"]
        self.user  = site["user"]
        self.pw    = site["pass"]
        self.b     = None
        self.p     = None

    # ---------- 启动浏览器 ----------
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
        self.b = Chromium(co)
        self.p = self.b.new_tab()
        self.p.run_js("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

    # ---------- 登录 ----------
    @retry(retries=3, delay=3)
    def login(self):
        logger.info(f"🔐 登录 {self.name}")
        self.p.get(self.site["login"])
        time.sleep(3)

        # =====  等待验证消失 / 刷新  =====
        logger.info("⏳ 先等 8 秒，验证框常自动消失")
        time.sleep(8)
        if self.p("tag:iframe"):
            logger.info("🔄 验证仍在，刷新一次再试")
            self.p.refresh()
            time.sleep(5)
        # 实在还在 → 点一次
        try:
            if self.p("tag:iframe"):
                self.p("tag:iframe").ele("tag:input").click()
                logger.info("🖱️ 已点击验证框")
                time.sleep(2)
        except Exception:
            pass

        # 填账号
        self._human_input(self.p.ele("@id=login-account-name"), self.user)
        time.sleep(random.uniform(0.8, 1.5))
        self._human_input(self.p.ele("@id=login-account-password"), self.pw)
        time.sleep(random.uniform(0.8, 1.5))

        self.p.ele("@id=login-button").click()
        time.sleep(5)

        # 必须看到用户名
        if self.user.lower() not in self.p.html.lower():
            self.p.get_screenshot(f"{self.name}_login_fail.png")
            raise Exception("未检测到用户名，登录失败")
        logger.success("✅ 登录成功")
        save_cookies(self.name, self.p.cookies())

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
        self.p.get(self.site["latest"])
        time.sleep(3)
        links = self.p.eles(".//a[@class='title raw-link raw-topic-link']")[:15]
        if not links:
            logger.warning("未找到主题")
            return
        for a in random.sample(links, min(10, len(links))):
            self._browse_one(a.attr("href"))

    @retry(retries=2, delay=2)
    def _browse_one(self, url):
        t = self.b.new_tab()
        t.get(url)
        time.sleep(random.uniform(2, 4))
        # 3 % 点赞
        if random.random() < 0.03:
            try:
                t.ele(".discourse-reactions-reaction-button").click()
                logger.success("👍 随机点赞")
                time.sleep(random.uniform(1, 2))
            except Exception:
                pass
        # 随机滚动
        for _ in range(random.randint(5, 10)):
            if random.random() < 0.03:
                logger.info("🛑 随机退出浏览")
                break
            t.run_js(f"window.scrollBy(0,{random.randint(550,650)})")
            time.sleep(random.uniform(2, 4))
        t.close()

    # ---------- Connect 信息 ----------
    def print_connect(self):
        try:
            self.p.get(self.site["connect"])
            time.sleep(3)
            rows = []
            for tr in self.p.eles("tag:table tag:tr")[1:]:
                tds = tr.eles("tag:td")[:3]
                rows.append([td.text for td in tds])
            if rows:
                print("-------------- Connect Info -----------------")
                print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        except Exception as e:
            logger.warning(f"获取 Connect 信息失败: {e}")

    # ---------- 主入口 ----------
    def run(self):
        try:
            self.start_browser()
            cookies = load_cookies(self.name)
            if cookies:
                self.p.get(self.site["base"])
                for ck in cookies:
                    self.p.set.cookie(ck)
                self.p.get(self.site["latest"])
                time.sleep(3)
                if self.user.lower() in self.p.html.lower():
                    logger.info("🎉 缓存有效，跳过登录")
                else:
                    logger.info("🔄 缓存失效，重新登录")
                    self.login()
            else:
                self.login()

            if BROWSE_EN:
                self.browse()
            self.print_connect()
            logger.success(f"{self.name} 全流程完成")
        except Exception as e:
            logger.error(f"{self.name} 运行失败: {e}")
            self.p.get_screenshot(f"{self.name}_error.png")
            raise
        finally:
            self.b.quit()


# -------------------- 主程序 --------------------
def main():
    logger.add("run.log", rotation="10 MB", retention="7 days", encoding="utf8")
    logger.info("===== 多站点自动浏览开始 =====")
    for site in SITES:
        try:
            AutoBrowser(site).run()
        except Exception as e:
            logger.error(f"{site['name']} 站点异常: {e}")
            continue
    logger.info("===== 全部结束 =====")


if __name__ == "__main__":
    main()
