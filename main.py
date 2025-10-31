# ==========================================
#  main.py  ——  Linux.Do / IdcFlare 双站点
#  1. 支持 Cookies 缓存（7 天有效期）
#  2. 支持 Cloudflare 5 s 盾
#  3. 支持「真实浏览埋点」：latest → 多主题 → 随机滚动 → 停留时长
#  4. 支持 Connect 页面信息打印
#  5. 支持 GitHub Actions / 本地 一键运行
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

# 站点清单
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

# 读取账号
def _get_credential(site: str):
    # 根据站点名称，从环境变量中获取用户名和密码
    username_env_var = f"{site.upper().replace('IDCFLARE', 'IDCFLARE')}_{'USERNAME'}"
    password_env_var = f"{site.upper().replace('IDCFLARE', 'IDCFLARE')}_{'PASSWORD'}"
    
    # 获取环境变量的值
    username = os.getenv(username_env_var)
    password = os.getenv(password_env_var)
    
    # 返回包含用户名和密码的字典
    return {
        "username": username,
        "password": password,
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
                json.dump(
                    {"cookies": cookies, "time": datetime.now().isoformat()},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info(f"💾 Cookies 已缓存  ->  {site}")
            return True
        except Exception as e:
            logger.error(f"缓存失败  ->  {site}  : {e}")
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
                logger.warning(f"🕒 Cookies 过期  ->  {site}")
                return None
            logger.info(f"📦 使用缓存 Cookies  ->  {site}")
            return data["cookies"]
        except Exception as e:
            logger.warning(f"加载缓存失败  ->  {site}  : {e}")
            return None

# -------------------- Cloudflare 处理 --------------------
class CloudflareHandler:
    @staticmethod
    def handle(page, timeout=30):
        logger.info("🛡️  检查 Cloudflare …")
        st = time.time()
        while time.time() - st < timeout:
            title = page.title.lower()
            if "please wait" in title or "checking" in title or "请稍候" in title:
                time.sleep(3)
                continue
            return True
        logger.warning("⚠️  Cloudflare 可能未通过，但仍继续")
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
        self._inject_anti_detect()

    # ----- 反检测 -----
    def _inject_anti_detect(self):
        js = """
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
        Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});
        window.chrome={runtime:{},loadTimes:function(){},csi:function(){},app:{isInstalled:false}};
        Object.defineProperty(document,'hidden',{get:()=>false});
        Object.defineProperty(document,'visibilityState',{get:()=>'visible'});
        """
        self.page.run_js(js)
        logger.info("✅ 反检测脚本已注入")

    # ----- 确保登录 -----
    def ensure_login(self):
        # 1. 先读缓存
        cookies = CacheManager.load_cookies(self.name)
        if cookies:
            self.page.set.cookies(cookies)
            self.page.get(self.site["latest_url"])
            CloudflareHandler.handle(self.page)
            if self._check_login_state():
                logger.success(f"✅ 缓存登录成功  ->  {self.name}")
                return True

        # 2. 缓存失效 -> 正常登录
        logger.info(f"🔄 缓存失效，重新登录  ->  {self.name}")
        return self._login_flow()

    def _login_flow(self):
        self.page.get(self.site["login_url"])
        CloudflareHandler.handle(self.page)
        try:
            self.page.ele("@id=login-account-name or @id=username or @placeholder*:用户名").input(self.cred["username"])
            self.page.ele("@id=login-account-password or @type=password").input(self.cred["password"])
            self.page.ele("@id=login-button or @type=submit or text:登录").click()
            time.sleep(15)
            if self._check_login_state():
                CacheManager.save_cookies(self.page.cookies(), self.name)
                return True
            logger.error(f"❌ 登录失败  ->  {self.name}")
            return False
        except Exception as e:
            logger.error(f"❌ 登录异常  ->  {self.name}  : {e}")
            return False

    # ----- 登录状态检查 -----
    def _check_login_state(self):
        try:
            self.page.get(self.site["latest_url"])
            html = self.page.html.lower()
            if self.cred["username"].lower() in html:
                return True
            # 检查头像
            if self.page.ele("img.avatar, .avatar, .header-dropdown-toggle", timeout=5):
                return True
            return False
        except:
            return False

    # ----- 浏览主题（核心埋点） -----
    def browse_topics(self):
        if not BROWSE_ENABLED:
            logger.info(f"⏭️  浏览已禁用  ->  {self.name}")
            return

        logger.info(f"🌐 开始浏览主题  ->  {self.name}")
        self.page.get(self.site["latest_url"])
        CloudflareHandler.handle(self.page)

        # 等待动态数据加载
        self.page.wait.doc_loaded()
        time.sleep(3)

        # 提取主题链接
        links = []
        try:
            # 优先取 <a class="title">
            eles = self.page.eles("a.title")
            if not eles:
                # 备选方案
                eles = self.page.eles(".main-link a, .topic-title")
            for a in eles:
                href = a.attr("href")
                if href and "/t/" in href:
                    links.append(href if href.startswith("http") else self.site["base_url"] + href)
        except Exception as e:
            logger.error(f"提取主题失败  ->  {self.name}  : {e}")
            return

        count = min(random.randint(5, 8), len(links))
        selected = random.sample(links, count)
        logger.info(f"📖 共 {len(links)} 主题，取 {count} 个浏览")

        succ = 0
        for i, url in enumerate(selected, 1):
            try:
                logger.info(f"📑 [{i}/{count}]  {url}")
                tab = self.browser.new_tab()
                tab.get(url)
                # 随机滚动
                for _ in range(random.randint(4, 8)):
                    tab.run_js(f"window.scrollBy(0, {random.randint(400, 800)})")
                    time.sleep(random.uniform(0.8, 2.0))
                # 低概率点赞
                if random.random() < 0.005:
                    try:
                        tab.ele(".discourse-reactions-reaction-button").click()
                        time.sleep(1)
                    except:
                        pass
                # 保证停留
                time.sleep(random.randint(12, 22))
                tab.close()
                succ += 1
            except Exception as e:
                logger.warning(f"浏览异常  ->  {url}  : {e}")
        logger.info(f"📊 浏览完成  ->  {self.name}  成功 {succ}/{count}")

    # ----- Connect 信息 -----
    def print_connect(self):
        logger.info(f"📊 获取 Connect 信息  ->  {self.name}")
        try:
            tab = self.browser.new_tab()
            tab.get(self.site["connect_url"])
            CloudflareHandler.handle(tab)
            tab.wait.doc_loaded()
            rows = []
            # 多种 table 可能
            for sel in ["table", ".table", ".connect-table"]:
                try:
                    tbl = tab.ele(sel, timeout=10)
                    if not tbl:
                        continue
                    for tr in tbl.eles("tag:tr"):
                        tds = [td.text.strip() for td in tr.eles("tag:td")]
                        if len(tds) >= 3:
                            rows.append(tds[:3])
                    break
                except:
                    continue
            if rows:
                print("-------------- Connect Info  ----------------")
                print(tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
            tab.close()
        except Exception as e:
            logger.error(f"Connect 失败  ->  {self.name}  : {e}")

    # ----- 总流程 -----
    def run(self):
        if not self.cred["username"] or not self.cred["password"]:
            logger.warning(f"⏭️  未配置账号  ->  {self.name}")
            return False
        if not self.ensure_login():
            return False
        self.browse_topics()
        self.print_connect()
        return True

    # ----- 清理 -----
    def quit(self):
        try:
            self.browser.quit()
        except:
            pass

# -------------------- 主入口 --------------------
def main():
    logger.info("🎯  Linux.Do / IdcFlare  多站点签到浏览脚本")
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
            logger.error(f"❌  站点异常  ->  {site['name']}  : {e}")
            fail.append(site["name"])
        finally:
            bro.quit()
        # 站点间休息
        if site != SITES[-1]:
            time.sleep(random.randint(10, 30))

    logger.info(f"📈  完成  ✅ 成功: {succ}   ❌ 失败: {fail}")
    sys.exit(0 if succ else 1)

if __name__ == "__main__":
    main()

