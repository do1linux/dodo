import os
import random
import time
import functools
import sys
import re
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from tabulate import tabulate


# -------------------------- 基础配置 --------------------------
HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"

USERNAME = os.environ.get("LINUXDO_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD") or os.environ.get("PASSWORD")
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]

# 日志配置
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level="INFO"
)


# -------------------------- 工具装饰器 --------------------------
def retry_decorator(retries=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries:
                        logger.error(f"【最终失败】函数 {func.__name__} 重试{retries}次后仍失败：{str(e)}")
                        raise
                    logger.warning(f"【重试】函数 {func.__name__} 第{attempt}/{retries}次失败：{str(e)}，{delay}秒后重试...")
                    time.sleep(delay)
        return wrapper
    return decorator


# -------------------------- 核心自动化类 --------------------------
class LinuxDoAutomation:
    def __init__(self):
        """初始化浏览器，优化超时和启动参数"""
        self.playwright = sync_playwright().start()
        # 修复点1：优化浏览器启动参数（适配CI环境）
        self.browser = self.playwright.chromium.launch(
            headless=True,
            timeout=60000,  # 延长浏览器启动超时（60秒）
            args=[
                "--no-sandbox",  # 禁用沙箱（CI环境必要）
                "--disable-dev-shm-usage",  # 解决临时目录不足问题
                "--disable-gpu",  # 禁用GPU加速（无头模式不需要）
                "--disable-extensions",  # 禁用扩展
                "--blink-settings=imagesEnabled=false"  # 禁用图片加载（加快页面加载）
            ]
        )
        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            ignore_https_errors=True  # 忽略HTTPS证书错误（部分环境可能需要）
        )
        self.page = self.context.new_page()
        # 修复点2：设置全局超时（60秒）
        self.page.set_default_timeout(60000)
        logger.info("浏览器初始化完成")

    def __del__(self):
        """资源清理"""
        if hasattr(self, "browser"):
            self.browser.close()
        if hasattr(self, "playwright"):
            self.playwright.stop()
        logger.info("浏览器资源已释放")

    @retry_decorator(retries=3, delay=3)  # 修复点3：增加重试机制
    def _safe_goto(self, url, wait_until="domcontentloaded"):
        """安全访问URL，优化等待策略"""
        try:
            # 修复点4：使用domcontentloaded替代networkidle（更快，避免等待不必要的网络请求）
            self.page.goto(url, wait_until=wait_until)
            logger.info(f"成功访问URL：{url}")
        except PlaywrightTimeoutError:
            # 超时后强制继续，避免卡死后退出
            logger.warning(f"访问{url}超时，但继续执行（可能页面已部分加载）")

    def login(self) -> bool:
        """登录逻辑优化"""
        try:
            logger.info("开始执行登录流程")
            self._safe_goto(LOGIN_URL)  # 使用安全访问方法

            # 等待登录表单加载（增加超时和重试）
            try:
                self.page.wait_for_selector("#login-account-name", state="visible", timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("登录表单加载慢，刷新页面重试")
                self.page.reload(wait_until="domcontentloaded")
                self.page.wait_for_selector("#login-account-name", state="visible", timeout=10000)

            # 填写表单
            self.page.fill("#login-account-name", USERNAME)
            self.page.fill("#login-account-password", PASSWORD)
            self.page.click("#login-button")

            # 等待登录结果（放宽条件）
            try:
                self.page.wait_for_url(HOME_URL, timeout=15000)
            except PlaywrightTimeoutError:
                logger.warning("登录后跳转超时，直接检查登录状态")

            if self.page.query_selector("#current-user"):
                logger.success(f"登录成功！用户名：{USERNAME}")
                return True
            else:
                logger.error("登录失败：未找到用户标识")
                return False

        except Exception as e:
            logger.error(f"登录过程异常：{str(e)}")
            return False

    @retry_decorator(retries=2, delay=2)
    def browse_single_topic(self, topic_element):
        """浏览单个主题"""
        topic_title = topic_element.text_content().strip()
        logger.info(f"开始浏览主题：{topic_title}")

        topic_element.click()
        try:
            self.page.wait_for_url(re.compile(r"https://linux.do/t/.*"), timeout=15000)
        except PlaywrightTimeoutError:
            logger.warning("主题页加载超时，继续执行")

        if random.random() < 0.3:
            self.like_topic()

        self.scroll_topic_page()
        self.page.go_back()
        try:
            self.page.wait_for_selector("#list-area .title", state="visible", timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("返回主题列表超时，继续下一个")
        time.sleep(random.uniform(1, 2))
        logger.success(f"主题浏览完成：{topic_title}")

    def scroll_topic_page(self):
        """滚动浏览优化"""
        logger.info("开始模拟滚动浏览")
        scroll_count = 0
        max_scroll = 10  # 减少滚动次数，加快流程
        while scroll_count < max_scroll:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            scroll_count += 1
            logger.debug(f"滚动次数：{scroll_count}/{max_scroll}")
            time.sleep(random.uniform(1.5, 3))  # 缩短等待时间
            if scroll_count >= 3 and random.random() < 0.2:  # 增加提前退出概率
                logger.info("随机提前结束滚动")
                break

    def like_topic(self):
        """点赞逻辑"""
        try:
            like_button = self.page.locator('.discourse-reactions-reaction-button[title="点赞此帖子"]').first
            if like_button.is_visible():
                like_button.click()
                time.sleep(random.uniform(1, 2))
                logger.success("点赞成功")
            else:
                logger.info("帖子已点赞或无点赞按钮")
        except Exception as e:
            logger.error(f"点赞异常：{str(e)}")

    def get_connect_info(self):
        """获取连接信息"""
        logger.info("开始获取Connect项目信息")
        try:
            connect_page = self.context.new_page()
            connect_page.set_default_timeout(60000)
            connect_page.goto("https://connect.linux.do/", wait_until="domcontentloaded")
            
            try:
                connect_page.wait_for_selector("table tr", state="visible", timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Connect页面表格加载超时，跳过")
                connect_page.close()
                return

            rows = connect_page.query_selector_all("table tr")
            connect_data = []
            for row in rows:
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    connect_data.append([
                        cells[0].text_content().strip(),
                        cells[1].text_content().strip(),
                        cells[2].text_content().strip()
                    ])

            print("\n" + "=" * 50)
            print("Linux.Do Connect 项目信息")
            print("=" * 50)
            print(tabulate(connect_data, headers=["项目名称", "当前进度", "要求"], tablefmt="grid"))
            print("=" * 50 + "\n")

            connect_page.close()
        except Exception as e:
            logger.error(f"获取Connect信息异常：{str(e)}")

    def run(self):
        """主运行流程"""
        try:
            if not self.login():
                logger.error("登录失败，程序终止")
                sys.exit(1)

            if BROWSE_ENABLED:
                logger.info("开始执行主题浏览任务")
                try:
                    self.page.wait_for_selector("#list-area .title", state="visible", timeout=15000)
                except PlaywrightTimeoutError:
                    logger.warning("主题列表加载超时，尝试刷新页面")
                    self.page.reload(wait_until="domcontentloaded")
                    self.page.wait_for_selector("#list-area .title", state="visible", timeout=15000)

                all_topics = self.page.query_selector_all("#list-area .title")
                if not all_topics:
                    logger.warning("未找到主题帖，跳过浏览")
                else:
                    topic_count = min(random.randint(2, 4), len(all_topics))  # 减少浏览数量
                    selected_topics = random.sample(all_topics, k=topic_count)
                    logger.info(f"随机选择{topic_count}个主题浏览")

                    for idx, topic in enumerate(selected_topics, 1):
                        logger.info(f"浏览第{idx}/{topic_count}个主题")
                        self.browse_single_topic(topic)

                logger.success("浏览任务完成")
            else:
                logger.info("浏览功能已禁用")

            self.get_connect_info()
            logger.success("所有任务执行完成！")

        except Exception as e:
            logger.error(f"程序异常：{str(e)}")
            sys.exit(1)


# -------------------------- 主执行入口 --------------------------
if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("错误：请配置LINUXDO_USERNAME和LINUXDO_PASSWORD环境变量")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Linux.Do 自动化程序启动")
    logger.info(f"配置：用户名={USERNAME}，浏览功能={'启用' if BROWSE_ENABLED else '禁用'}")
    logger.info("=" * 60)

    automation = LinuxDoAutomation()
    automation.run()
