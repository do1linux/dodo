"""
cron: 0 * * * *
new Env("Linux.Do 多站点自动浏览")
"""
import os, random, time, json, functools, sys
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate

# ---------------- 基本配置 ----------------
HEADLESS   = os.getenv("HEADLESS", "true").lower() not in {"false","0","off"}
BROWSE_EN   = os.getenv("BROWSE_ENABLED", "true").lower() not in {"false","0","off"}
SELECTOR   = os.getenv("SITE_SELECTOR", "all")
CACHE_DIR  = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# ---------------- 缓存 --------------------
def cache_file(name): return CACHE_DIR / f"{name}_cookies.json"
def load_cookies(name):
    f = cache_file(name)
    if not f.exists(): return None
    try:
        data = json.loads(f.read_text(encoding="utf8"))
        if datetime.now() - datetime.fromisoformat(data["cache_time"]) > timedelta(days=7):
            logger.warning("🕒 Cookies 过期"); return None
        logger.info(f"📦 加载 {name} 缓存"); return data["cookies"]
    except: return None
def save_cookies(name, cookies):
    try:
        cache_file(name).write_text(json.dumps({"cookies":cookies,"cache_time":datetime.now().isoformat()},ensure_ascii=False,indent=2),encoding="utf8")
        logger.info(f"💾 保存 {name} cookies"); return True
    except: return False

# ---------------- 重试 --------------------
def retry(retries=3, delay=2):
    def deco(f):
        @functools.wraps(f)
        def wrap(*a,**k):
            for i in range(1,retries+1):
                try: return f(*a,**k)
                except as e:
                    if i==retries: raise
                    logger.warning(f"{f.__name__} 第{i}/{retries}次失败: {e}")
                    time.sleep(delay)
        return wrap
    return deco

# ---------------- 站点 --------------------
SITES = [
    {"name":"linux_do","base":"https://linux.do","login":"https://linux.do/login","latest":"https://linux.do/latest","connect":"https://connect.linux.do","user":os.getenv("LINUXDO_USERNAME"),"pass":os.getenv("LINUXDO_PASSWORD")},
    {"name":"idcflare","base":"https://idcflare.com","login":"https://idcflare.com/login","latest":"https://idcflare.com/latest","connect":"https://connect.idcflare.com","user":os.getenv("IDCFLARE_USERNAME"),"pass":os.getenv("IDCFLARE_PASSWORD")}
]
if SELECTOR!="all": SITES = [s for s in SITES if s["name"]==SELECTOR]
for s in SITES:
    if not (s["user"] and s["pass"]): logger.error(f"❌ {s['name']} 账号/密码未配"); sys.exit(1)

# ---------------- 浏览器 ------------------
class AutoBrowser:
    def __init__(self,site): self.site=site; self.name=site["name"]; self.user=site["user"]; self.pw=site["pass"]
    def start(self):
        co = (ChromiumOptions().headless(HEADLESS).incognito(True)
              .set_argument("--no-sandbox").set_argument("--disable-blink-features=AutomationControlled"))
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
        self.b = Chromium(co); self.p = self.b.new_tab()
        self.p.run_js("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    def login(self):
        logger.info(f"🔐 登录 {self.name}")
        self.p.get(self.site["login"]); time.sleep(3)
        # =====  等验证消失 / 刷新  =====
        logger.info("⏳ 先等8秒，验证框常自动消失")
        time.sleep(8)
        if self.p("tag:iframe"):
            logger.info("🔄 验证仍在，刷新一次再试")
            self.p.refresh(); time.sleep(5)
        # 点不到就算
        try:
            if self.p("tag:iframe"): self.p("tag:iframe").ele("tag:input").click(); time.sleep(2)
        except: pass
        # 填账号
        self._type(self.p.ele("@id=login-account-name"), self.user)
        time.sleep(0.8)
        self._type(self.p.ele("@id=login-account-password"), self.pw)
        time.sleep(0.8)
        self.p.ele("@id=login-button").click(); time.sleep(5)
        if self.user.lower() not in self.p.html.lower():
            self.p.get_screenshot(f"{self.name}_fail.png")
            raise Exception("未检测到用户名，登录失败")
        logger.success("✅ 登录成功")
        save_cookies(self.name, self.p.cookies())
    def _type(self,ele,txt):
        ele.clear()
        for ch in txt:
            ele.input(ch)
            time.sleep(random.uniform(0.05,0.15))
    def browse(self):
        self.p.get(self.site["latest"]); time.sleep(3)
        links = self.p.eles(".//a[@class='title raw-link raw-topic-link']")[:15]
        if not links: logger.warning("无主题"); return
        for a in random.sample(links, min(10,len(links))):
            self._browse_one(a.attr("href"))
    @retry(2,2)
    def _browse_one(self,url):
        t=self.b.new_tab(); t.get(url); time.sleep(random.uniform(2,4))
        if random.random()<0.03:
            try: t.ele(".discourse-reactions-reaction-button").click(); logger.success("👍 点赞")
            except: pass
        for _ in range(random.randint(5,10)):
            if random.random()<0.03: logger.info("🛑 随机退出"); break
            t.run_js(f"window.scrollBy(0,{random.randint(550,650)})")
            time.sleep(random.uniform(2,4))
        t.close()
    def connect(self):
        try:
            self.p.get(self.site["connect"]); time.sleep(3)
            rows=[]
            for tr in self.p.eles("tag:table tag:tr")[1:]:
                td=tr.eles("tag:td")[:3]; rows.append([x.text for x in td])
            if rows:
                print("-------------- Connect Info -----------------")
                print(tabulate(rows,["项目","当前","要求"],"pretty"))
        except: pass
    def run(self):
        try:
            self.start()
            cks=load_cookies(self.name)
            if cks:
                self.p.get(self.site["base"])
                for ck in cks: self.p.set.cookie(ck)
                self.p.get(self.site["latest"]); time.sleep(3)
                if self.user.lower() in self.p.html.lower(): logger.info("🎉 缓存有效，跳过登录")
                else: logger.info("🔄 缓存失效，重新登录"); self.login()
            else: self.login()
            if BROWSE_EN: self.browse()
            self.connect()
            logger.success(f"{self.name} 完成")
        except Exception as e:
            logger.error(f"{self.name} 失败: {e}")
            self.p.get_screenshot(f"{self.name}_error.png")
        finally: self.b.quit()

# ---------------- 主入口 ------------------
def main():
    logger.add("run.log",rotation="10MB",retention="7 days",encoding="utf8")
    logger.info("===== 多站点自动浏览开始 =====")
    for s in SITES: AutoBrowser(s).run()
    logger.info("===== 全部结束 =====")

if __name__ == "__main__": main()
