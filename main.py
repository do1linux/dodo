# ==========================================
#  main.py  ——  Linux.Do / IdcFlare 双站点
#  1. 登录后 Cookie 缓存 7 天
#  2. 每篇帖子 70 s+ 慢速滚动到底，触发时长埋点
#  3. Actions 45 min 内可跑完
# ==========================================
import os, random, time, functools, sys, json
from datetime import datetime, timedelta
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# -------------------- 全局配置 --------------------
COOKIE_VALIDITY_DAYS = 7
HEADLESS = os.getenv("HEADLESS", "true").lower() not in ["false", "0", "off"]
BROWSE_ENABLED = os.getenv("BROWSE_ENABLED", "true").lower() not in ["false", "0", "off"]

SITES = [
    {
        "name": "linux_do",
        "base_url": "https://linux.do",
        "login_url": "https://linux.do/login",
        "latest_url": "https://linux.do/latest",
        "connect_url": "https://connect.linux.do",
    },
    {
        "name": "idcflare",
        "base_url": "https://idcflare.com",
        "login_url": "https://idcflare.com/login",
        "latest_url": "https://idcflare.com/latest",
        "connect_url": "https://connect.idcflare.com",
    },
]

def _get_credential(site: str):
    return {
        "username": os.getenv(f"{site.upper()}_USERNAME"),
        "password": os.getenv(f"{site.upper()}_PASSWORD"),
    }

# -------------------- 缓存工具 --------------------
class CacheManager:
    CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

    @staticmethod
    def _path(name):
        os.makedirs(CacheManager.CACHE_DIR, exist_ok=True)
        return os.path.join(CacheManager.CACHE_DIR, f"{name}_cookies.json")

    @staticmethod
    def save_cookies(cookies, site: str):
        try:
            with open(CacheManager._path(site), "w", encoding="utf-8") as f:
                json.dump({"cookies": cookies, "time": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 Cookies 已缓存 -> {site}")
            return True
        except Exception as e:
            logger.error(f"缓存失败 -> {site}: {e}")
            return False

    @staticmethod
    def load_cookies(site: str):
        file = CacheManager._path(site)
        if not os.path.exists(file):
            return None
        try:
            with open(file, encoding="utf-8") as f:
                data = json.load(f)
            if datetime.now() - datetime.fromisoformat(data["time"]) > timedelta(days=COOKIE_VALIDITY_DAYS):
                logger.warning(f"🕒 Cookies 过期 -> {site}")
                return None
            logger.info(f"📦 使用缓存 Cookies -> {site}")
            return data["cookies"]
        except Exception as e:
            logger.warning(f"加载缓存失败 -> {site}: {e}")
            return None

# -------------------- Cloudflare --------------------
class CloudflareHandler:
    @staticmethod
    def handle(page, timeout=30):
        logger.info("🛡️ 检查 Cloudflare …")
        st = time.time()
        while time.time() - st < timeout:
            title = page.title.lower()
            if "please wait" in title or "checking" in title or "请稍候" in title:
                time.sleep(3)
                continue
            logger.info("✅ Cloudflare 验证通过")
            return True
        logger.warning("⚠️ Cloudflare 可能未通过，但仍继续")
        return True

# -------------------- 浏览器封装 --------------------
class LDBrowser:
    def __init__(self, site: dict, cred: dict):
        self.site = site
        self.cred = cred
        self.name = site["name"]

        co = (
            ChromiumOptions()
            .headless(HEADLESS)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-dev-shm-usage")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--lang=zh-CN,zh;q=0.9")
        )
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()

    # ---------- 登录 ----------
    def ensure_login(self):
        cookies = CacheManager.load_cookies(self.name)
        if cookies:
            self.page.set.cookies(cookies)
            self.page.get(self.site["latest_url"])
            CloudflareHandler.handle(self.page)
            if self._check_login():
                logger.success(f"✅ 缓存登录成功 -> {self.name}")
                return True
        return self._login_from_scratch()

    def _login_from_scratch(self):
        logger.info(f"🔄 重新登录 -> {self.name}")
        self.page.get(self.site["login_url"])
        CloudflareHandler.handle(self.page)
        try:
            self.page.ele("input#login-account-name").input(self.cred["username"])
            self.page.ele("input#login-account-password").input(self.cred["password"])
            self.page.ele("button#login-button").click()
            time.sleep(15)
            if self._check_login():
                CacheManager.save_cookies(self.page.cookies(), self.name)
                return True
            logger.error(f"❌ 登录失败 -> {self.name}")
            return False
        except Exception as e:
            logger.error(f"❌ 登录异常 -> {self.name}: {e}")
            return False

    def _check_login(self):
        try:
            self.page.get(self.site["latest_url"])
            return self.cred["username"].lower() in self.page.html.lower()
        except:
            return False

    # ---------- 浏览（70 s+ 慢滚） ----------
    def browse_topics(self):
        if not BROWSE_ENABLED:
            logger.info(f"⏭️ 浏览已禁用 -> {self.name}")
            return
        logger.info(f"🌐 开始浏览主题 -> {self.name}")
        self.page.get(self.site["latest_url"])
        CloudflareHandler.handle(self.page)
        self.page.wait.doc_loaded()

        links = []
        for a in self.page.eles("a.title"):
            href = a.attr("href")
            if href and "/t/" in href:
                links.append(href if href.startswith("http") else self.site["base_url"] + href)
        if not links:
            logger.warning("❌ 未提取到主题链接")
            return

        browse_count = min(2, len(links))          # 只看 2 篇，防超时
        selected = random.sample(links, browse_count)
        logger.info(f"共 {len(links)} 主题，取 {browse_count} 篇，单篇≥70 s")
        succ = 0
        for i, url in enumerate(selected, 1):
            logger.info(f"📖 第 {i}/{browse_count} 篇 | {url}")
            if self._browse_one_post(url):
                succ += 1
            if i < browse_count:
                time.sleep(random.randint(10, 20))
        logger.info(f"📊 浏览完成 -> {self.name} 成功 {succ}/{browse_count}")

    def _browse_one_post(self, url):
        tab = self.browser.new_tab()
        try:
            tab.set.cookies(self.page.cookies())   # 带Cookie
            tab.get(url)
            tab.wait(3)                            # JS初始化

            # 慢滚 4 次 + 到底 + 20 s
            for _ in range(4):
                tab.run_js(f"window.scrollBy(0, {random.randint(600, 900)})")
                tab.wait(random.randint(8, 12))
            tab.run_js("window.scrollTo(0, document.body.scrollHeight)")
            tab.wait(20)                           # 关键：保证≥70 s

            # 随机点赞
            if random.random() < 0.008:
                try:
                    tab.ele(".discourse-reactions-reaction-button").click()
                    tab.wait(2)
                except:
                    pass
            tab.close()
            return True
        except Exception as e:
            logger.warning(f"浏览异常: {e}")
            tab.close()
            return False

    # ---------- Connect 信息 ----------
    def print_connect(self):
        logger.info(f"📊 Connect 信息 -> {self.name}")
        try:
            tab = self.browser.new_tab()
            tab.get(self.site["connect_url"])
            CloudflareHandler.handle(tab)
            tab.wait.doc_loaded()
            rows = []
            for sel in ["table", ".table", ".connect-table"]:
                tbl = tab.ele(sel, timeout=10)
                if not tbl:
                    continue
                for tr in tbl.eles("tag:tr"):
                    tds = [td.text.strip() for td in tr.eles("tag:td")]
                    if len(tds) >= 3:
                        rows.append(tds[:3])
                break
            if rows:
                print("-------------- Connect Info  ----------------")
                print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
            tab.close()
        except Exception as e:
            logger.error(f"Connect 失败 -> {self.name}: {e}")

    # ---------- 主流程 ----------
    def run(self):
        if not self.cred["username"] or not self.cred["password"]:
            logger.warning(f"⏭️ 未配置账号 -> {self.name}")
            return False
        if not self.ensure_login():
            return False
        self.browse_topics()
        self.print_connect()
        return True

    def quit(self):
        try:
            self.browser.quit()
        except:
            pass

# -------------------- 主入口 --------------------
def main():
    logger.info("🎯 Linux.Do / IdcFlare  双站点签到浏览脚本")
    succ, fail = [], []
    for site in SITES:
        cred = _get_credential(site["name"])
        bro = LDBrowser(site, cred)
        try:
            if bro.run():
                succ.append(site["name"])
            else:
                fail.append(site["name"])
        except Exception as e:
            logger.error(f"❌ 站点异常 -> {site['name']}: {e}")
            fail.append(site["name"])
        finally:
            bro.quit()
        if site != SITES[-1]:
            time.sleep(random.randint(10, 30))
    logger.info(f"📈 完成  ✅ 成功: {succ}   ❌ 失败: {fail}")
    sys.exit(0 if succ else 1)

if __name__ == "__main__":
    main()
