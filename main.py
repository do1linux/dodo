#!/usr/bin/env python3
import os
import random
import time
import sys
import json
import requests
import hashlib
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.keys import Keys
from loguru import logger

# ======================== 配置常量 ========================
# 环境变量配置，带默认值
SITE_CREDENTIALS = {
    'linux_do': {
        'username': os.getenv('LINUXDO_USERNAME', ''),
        'password': os.getenv('LINUXDO_PASSWORD', '')
    },
    'idcflare': {
        'username': os.getenv('IDCFLARE_USERNAME', ''),
        'password': os.getenv('IDCFLARE_PASSWORD', '')
    }
}

SITES = [
    {
        'name': 'linux_do',
        'base_url': 'https://linux.do',
        'login_url': 'https://linux.do/login',
        'latest_url': 'https://linux.do/latest',
        'connect_url': 'https://connect.linux.do',
        'user_url': 'https://linux.do/u'
    },
    {
        'name': 'idcflare',
        'base_url': 'https://idcflare.com',
        'login_url': 'https://idcflare.com/login',
        'latest_url': 'https://idcflare.com/latest',
        'connect_url': 'https://connect.idcflare.com',
        'user_url': 'https://idcflare.com/u'
    }
]

# 功能开关配置
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]
FORCE_LOGIN_EVERY_TIME = os.getenv('FORCE_LOGIN_EVERY_TIME', 'false').strip().lower() in ['true', '1', 'yes']

# DoH服务器配置
DOH_SERVER = os.environ.get("DOH_SERVER", "https://ld.ddd.oaifree.com/query-dns")

# 扩展路径配置
TURNSTILE_PATCH_PATH = os.path.abspath("turnstilePatch")

# 缓存过期时间配置（小时）
COOKIES_EXPIRY_HOURS = int(os.getenv('COOKIES_EXPIRY_HOURS', '72'))  # 延长至3天
SESSION_EXPIRY_HOURS = int(os.getenv('SESSION_EXPIRY_HOURS', '24'))  # 会话缓存1天

# 缓存版本控制
CACHE_VERSION = os.getenv('CACHE_VERSION', 'v1')
FORCE_REFRESH_CACHE = os.getenv('FORCE_REFRESH_CACHE', 'false').strip().lower() in ['true', '1', 'yes']

# ======================== 增强缓存管理器 ========================
class CacheManager:
    """增强缓存管理类 - 支持多种状态缓存"""
    
    @staticmethod
    def get_cache_directory():
        """获取缓存目录（当前工作目录）"""
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_cache_file_path(file_name):
        """获取缓存文件的完整路径"""
        cache_dir = CacheManager.get_cache_directory()
        return os.path.join(cache_dir, file_name)

    @staticmethod
    def calculate_file_hash(file_path):
        """计算文件哈希值，用于验证缓存完整性"""
        try:
            with open(file_path, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()[:8]
            return file_hash
        except:
            return None

    @staticmethod
    def load_cache(file_name, validate_expiry=True):
        """从文件加载缓存数据 - 增强版"""
        file_path = CacheManager.get_cache_file_path(file_name)
        
        if not os.path.exists(file_path):
            logger.warning(f"⚠️ 缓存文件不存在: {file_name}")
            return None
        
        # 验证缓存有效期
        if validate_expiry:
            if not CacheManager.is_cache_valid(file_path):
                logger.warning(f"⚠️ 缓存文件过期，跳过加载: {file_name}")
                return None
        
        try:
            # 验证文件完整性
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"⚠️ 缓存文件为空: {file_name}")
                return None
            
            with open(file_path, "r", encoding='utf-8') as f:
                data = json.load(f)
            
            # 验证数据结构
            if not data:
                logger.warning(f"⚠️ 缓存数据为空: {file_name}")
                return None
            
            # 验证版本兼容性
            if isinstance(data, dict) and 'cache_version' in data:
                if data['cache_version'] != CACHE_VERSION:
                    logger.warning(f"⚠️ 缓存版本不匹配 (当前: {CACHE_VERSION}, 缓存: {data['cache_version']})")
                    return None
            
            # 计算并记录文件哈希
            file_hash = CacheManager.calculate_file_hash(file_path)
            logger.info(f"✅ 成功加载缓存: {file_name} (大小: {file_size} 字节, 哈希: {file_hash})")
            
            return data
        except json.JSONDecodeError as e:
            logger.error(f"❌ 缓存文件JSON解析失败 {file_name}: {str(e)}")
            # 删除损坏的缓存文件
            try:
                os.remove(file_path)
                logger.info(f"🗑️ 已删除损坏的缓存文件: {file_name}")
            except:
                pass
            return None
        except Exception as e:
            logger.error(f"❌ 加载缓存失败 {file_name}: {str(e)}")
            return None

    @staticmethod
    def save_cache(data, file_name, include_version=True):
        """保存数据到缓存文件 - 增强版"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            
            # 添加版本信息
            if include_version and isinstance(data, dict):
                data['cache_version'] = CACHE_VERSION
                data['saved_at'] = datetime.now().isoformat()
            
            # 写入临时文件，然后原子性重命名
            temp_path = f"{file_path}.tmp"
            with open(temp_path, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 验证写入是否成功
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, file_path)
                
                # 验证最终文件
                file_size = os.path.getsize(file_path)
                file_hash = CacheManager.calculate_file_hash(file_path)
                
                if file_size > 0:
                    logger.success(f"✅ 缓存已保存: {file_name} (大小: {file_size} 字节, 哈希: {file_hash})")
                    return True
                else:
                    logger.error(f"❌ 缓存文件大小验证失败: {file_name}")
                    return False
            else:
                logger.error(f"❌ 临时缓存文件创建失败: {file_name}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 保存缓存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def is_cache_valid(file_path, expiry_hours=COOKIES_EXPIRY_HOURS):
        """检查缓存是否有效 - 包含强制刷新逻辑"""
        try:
            # 强制刷新缓存
            if FORCE_REFRESH_CACHE:
                logger.info(f"🔄 强制刷新缓存已启用，跳过有效性检查")
                try:
                    os.remove(file_path)
                    logger.info(f"🗑️ 已删除旧缓存: {os.path.basename(file_path)}")
                except:
                    pass
                return False
            
            if not os.path.exists(file_path):
                logger.debug(f"缓存文件不存在: {os.path.basename(file_path)}")
                return False
            
            file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            time_diff = datetime.now() - file_modified_time
            is_valid = time_diff.total_seconds() < expiry_hours * 3600
            
            if is_valid:
                logger.debug(f"✅ 缓存有效: {os.path.basename(file_path)} (未超过{expiry_hours}小时)")
            else:
                hours_old = time_diff.total_seconds() / 3600
                logger.warning(f"⚠️ 缓存过期: {os.path.basename(file_path)} (已存在{hours_old:.1f}小时)")
            
            return is_valid
        except Exception as e:
            logger.error(f"❌ 缓存验证失败: {os.path.basename(file_path)} - {str(e)}")
            return False

    @staticmethod
    def delete_cache(file_name):
        """删除指定缓存文件"""
        try:
            file_path = CacheManager.get_cache_file_path(file_name)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"🗑️ 已删除缓存: {file_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ 删除缓存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def list_all_cache_files():
        """列出所有缓存文件及其状态"""
        cache_dir = CacheManager.get_cache_directory()
        cache_files = []
        
        # 定义缓存文件模式
        patterns = [
            "cf_cookies_*.json",
            "browser_state_*.json",
            "cloudflare_state_*.json",
            "session_fingerprint_*.json",
            "cache_metadata.json"
        ]
        
        import glob
        for pattern in patterns:
            search_pattern = os.path.join(cache_dir, pattern)
            files = glob.glob(search_pattern)
            cache_files.extend(files)
        
        # 显示详细信息
        if cache_files:
            logger.info("📋 当前缓存文件列表:")
            for file_path in sorted(cache_files):
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                hours_old = (datetime.now() - file_time).total_seconds() / 3600
                is_valid = hours_old < COOKIES_EXPIRY_HOURS
                
                status_icon = "✅" if is_valid else "⚠️"
                status_text = "有效" if is_valid else f"已过期({hours_old:.1f}小时)"
                
                logger.info(f"  {status_icon} {file_name} (大小: {file_size} 字节, {status_text})")
        else:
            logger.info("ℹ️ 未找到任何缓存文件")
        
        return cache_files


# ======================== Cloudflare处理器(增强版) ========================
class CloudflareHandler:
    @staticmethod
    def query_doh(domain, doh_server=DOH_SERVER):
        """通过DoH服务器查询DNS - 增强版"""
        try:
            query_url = f"{doh_server}?name={domain}&type=A"
            headers = {
                'Accept': 'application/dns-json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            logger.info(f"🔍 DoH查询: {domain}")
            response = requests.get(query_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if 'Answer' in data and data['Answer']:
                    ips = [answer['data'] for answer in data['Answer'] if answer['type'] == 1]
                    if ips:
                        logger.success(f"✅ DoH解析成功: {domain} -> {ips[0]}")
                        return ips
                    
            logger.warning(f"⚠️ DoH查询无结果: {domain}")
            return None
        except requests.exceptions.Timeout:
            logger.error(f"⏰ DoH查询超时: {domain}")
            return None
        except Exception as e:
            logger.error(f"❌ DoH查询失败 {domain}: {str(e)}")
            return None

    @staticmethod
    def save_verification_state(driver, site_name, success=True, metadata=None):
        """保存Cloudflare验证状态到缓存"""
        try:
            state_data = {
                'timestamp': datetime.now().isoformat(),
                'success': success,
                'url': driver.current_url if driver else '',
                'user_agent': driver.execute_script('return navigator.userAgent') if driver else '',
                'metadata': metadata or {}
            }
            
            cache_file = f"cloudflare_state_{site_name}.json"
            CacheManager.save_cache(state_data, cache_file)
            logger.info(f"💾 Cloudflare验证状态已保存: {cache_file}")
            return True
        except Exception as e:
            logger.error(f"❌ 保存Cloudflare验证状态失败: {str(e)}")
            return False

    @staticmethod
    def load_verification_state(site_name):
        """从缓存加载Cloudflare验证状态"""
        cache_file = f"cloudflare_state_{site_name}.json"
        return CacheManager.load_cache(cache_file, validate_expiry=True)

    @staticmethod
    def handle_cloudflare_with_doh(driver, site_name, doh_server=DOH_SERVER, max_attempts=15, timeout=240):
        """增强版Cloudflare验证处理"""
        start_time = time.time()
        logger.info(f"🛡️ 开始处理Cloudflare验证 (DoH: {doh_server})")
        
        # 预解析关键域名
        critical_domains = [
            'linux.do',
            'idcflare.com', 
            'challenges.cloudflare.com',
            'cloudflare.com',
            'ajax.cloudflare.com'
        ]
        
        resolved_ips = {}
        for domain in critical_domains:
            ips = CloudflareHandler.query_doh(domain, doh_server)
            if ips:
                resolved_ips[domain] = ips[0]
        
        # 将解析结果注入浏览器
        if driver and resolved_ips:
            inject_script = """
            window.resolvedDNS = {};
            console.log('💾 注入DNS解析结果:', window.resolvedDNS);
            """.format(json.dumps(resolved_ips))
            driver.execute_script(inject_script)
        
        last_url = driver.current_url
        attempt_details = []
        
        for attempt in range(max_attempts):
            try:
                current_url = driver.current_url
                page_title = driver.title.lower() if driver.title else ""
                page_source = driver.page_source.lower() if driver.page_source else ""
                
                # 检查URL是否发生变化
                if current_url != last_url:
                    logger.info(f"🔄 页面跳转: {last_url} -> {current_url}")
                    last_url = current_url
                
                # 检测Cloudflare页面 - 增强检测逻辑
                cf_indicators = [
                    "just a moment", "checking", "please wait", 
                    "ddos protection", "cloudflare", "verifying",
                    "attention required", "checking your browser"
                ]
                
                is_cf_page = any(indicator in page_title for indicator in cf_indicators) or \
                           any(indicator in page_source for indicator in cf_indicators)
                
                # 检测挑战页面
                is_challenge = "challenge" in current_url or \
                              "challenges" in current_url or \
                              "/cdn-cgi/challenge-platform" in current_url
                
                # 检测验证码
                has_turnstile = any(keyword in page_source for keyword in [
                    "turnstile", "cf-turnstile", "cf-challenge", 
                    "g-recaptcha", "h-captcha"
                ])
                
                if not is_cf_page and not is_challenge and not has_turnstile:
                    # 双重验证 - 等待后再次检查
                    time.sleep(3)
                    page_title = driver.title.lower() if driver.title else ""
                    is_cf_page = any(indicator in page_title for indicator in cf_indicators)
                    
                    if not is_cf_page:
                        elapsed = time.time() - start_time
                        logger.success(f"✅ Cloudflare验证通过 (耗时: {elapsed:.1f}秒)")
                        
                        # 保存验证状态到缓存
                        CloudflareHandler.save_verification_state(
                            driver, site_name, success=True,
                            metadata={
                                'attempts': attempt + 1,
                                'elapsed_time': elapsed,
                                'final_url': current_url,
                                'resolved_ips': resolved_ips
                            }
                        )
                        return True
                
                # 动态等待策略
                base_wait = min(5 + (attempt * 2), 12)  # 递增等待，最大12秒
                if has_turnstile:
                    base_wait = max(base_wait, 8)  # 遇到验证码至少等待8秒
                
                # 智能随机化
                wait_time = base_wait + random.uniform(-1, 2)
                elapsed = time.time() - start_time
                
                # 记录每次尝试的详细信息
                attempt_details.append({
                    'attempt': attempt + 1,
                    'url': current_url,
                    'wait_time': wait_time,
                    'has_turnstile': has_turnstile,
                    'elapsed': elapsed
                })
                
                logger.info(f"⏳ 等待验证 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                logger.debug(f"  📍 URL: {current_url}")
                logger.debug(f"  🔍 标题: {driver.title}")
                logger.debug(f"  🧩 验证码: {'有' if has_turnstile else '无'}")
                
                time.sleep(wait_time)
                
                # 超时保护
                if elapsed > timeout:
                    logger.warning(f"⚠️ Cloudflare处理超时 ({timeout}秒)")
                    break
                
                # 智能刷新策略
                if attempt % 4 == 3:  # 每4次尝试刷新一次
                    logger.info("🔄 执行智能刷新")
                    driver.refresh()
                    time.sleep(random.uniform(3, 5))
                
                # 模拟用户活动
                if attempt % 3 == 2:  # 每3次尝试模拟一次活动
                    try:
                        # 随机鼠标移动
                        driver.execute_script("""
                            const event = new MouseEvent('mousemove', {
                                clientX: Math.random() * window.innerWidth,
                                clientY: Math.random() * window.innerHeight
                            });
                            document.dispatchEvent(event);
                        """)
                        logger.debug("🖱️ 模拟鼠标移动")
                    except:
                        pass
                        
            except Exception as e:
                logger.error(f"❌ Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(5)
        
        # 所有尝试失败，保存失败状态
        logger.warning("⚠️ Cloudflare验证可能未完全通过")
        CloudflareHandler.save_verification_state(
            driver, site_name, success=False,
            metadata={
                'attempts': max_attempts,
                'timeout': True,
                'attempt_details': attempt_details
            }
        )
        return False


# ======================== 浏览器会话指纹管理 ========================
class SessionFingerprintManager:
    """管理浏览器会话指纹，用于识别session一致性"""
    
    @staticmethod
    def generate_fingerprint(driver, site_name):
        """生成当前会话的指纹"""
        try:
            fingerprint = {
                'timestamp': datetime.now().isoformat(),
                'url': driver.current_url,
                'user_agent': driver.execute_script('return navigator.userAgent'),
                'cookies_count': len(driver.get_cookies()),
                'local_storage_keys': driver.execute_script('return Object.keys(localStorage)'),
                'session_storage_keys': driver.execute_script('return Object.keys(sessionStorage)'),
                'window_size': driver.execute_script('return {width: window.innerWidth, height: window.innerHeight}')
            }
            
            # 计算指纹哈希
            fingerprint_str = json.dumps(fingerprint, sort_keys=True)
            fingerprint['hash'] = hashlib.md5(fingerprint_str.encode()).hexdigest()[:16]
            
            # 保存到缓存
            cache_file = f"session_fingerprint_{site_name}.json"
            CacheManager.save_cache(fingerprint, cache_file)
            
            logger.info(f"🔍 生成会话指纹: {fingerprint['hash']}")
            return fingerprint
        except Exception as e:
            logger.error(f"❌ 生成会话指纹失败: {str(e)}")
            return None
    
    @staticmethod
    def load_fingerprint(site_name):
        """加载历史会话指纹"""
        cache_file = f"session_fingerprint_{site_name}.json"
        return CacheManager.load_cache(cache_file, validate_expiry=True)
    
    @staticmethod
    def compare_fingerprint(current, historical):
        """比较当前和历史指纹的相似度"""
        try:
            if not current or not historical:
                return 0.0
            
            similarity = 0.0
            total_checks = 0
            
            # 比较User-Agent
            if current.get('user_agent') == historical.get('user_agent'):
                similarity += 1.0
            total_checks += 1
            
            # 比较Cookie数量（允许一定变化）
            current_cookies = current.get('cookies_count', 0)
            historical_cookies = historical.get('cookies_count', 0)
            if abs(current_cookies - historical_cookies) <= 3:
                similarity += 1.0
            elif current_cookies > historical_cookies:
                similarity += 0.5
            total_checks += 1
            
            # 比较LocalStorage键
            current_keys = set(current.get('local_storage_keys', []))
            historical_keys = set(historical.get('local_storage_keys', []))
            if len(historical_keys) > 0:
                key_similarity = len(current_keys & historical_keys) / len(historical_keys)
                similarity += key_similarity
                total_checks += 1
            
            return similarity / total_checks if total_checks > 0 else 0.0
        except Exception as e:
            logger.error(f"❌ 指纹比较失败: {str(e)}")
            return 0.0


# ======================== 增强版主浏览器类 ========================
class LinuxDoBrowser:
    def __init__(self, site_config, credentials):
        self.site_config = site_config
        self.site_name = site_config['name']
        self.username = credentials['username']
        self.password = credentials['password']
        self.driver = None
        self.wait = None
        self.logger = logger.bind(site=site_name)
        self.session_fingerprint = None
        
        # 初始化浏览器
        self.initialize_browser()

    def initialize_browser(self):
        """初始化浏览器 - 增强版"""
        chrome_options = Options()
        
        # Headless模式配置
        if HEADLESS:
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-setuid-sandbox')
        
        # 反检测核心配置
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--lang=zh-CN,zh;q=0.9,en-US,en;q=0.8')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--disable-features=IsolateOrigins,site-per-process')
        chrome_options.add_argument('--disable-infobars')
        chrome_options.add_argument('--disable-extensions-except')
        chrome_options.add_argument('--disable-plugins')
        chrome_options.add_argument('--disable-images')  # 禁用图片加速加载
        chrome_options.add_argument('--disable-javascript')  # 初始禁用JS，后续再启用
        chrome_options.add_argument('--disk-cache-size=104857600')  # 100MB磁盘缓存
        
        # 固定User-Agent
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # 排除自动化特征
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging", "load-extension"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # 增强的prefs配置
        chrome_options.add_experimental_option("prefs", {
            "profile.default_content_setting_values": {
                "images": 2,  # 禁用图片
                "cookies": 1,  # 启用cookies
                "notifications": 2,  # 禁用通知
                "popups": 2,  # 禁用弹出窗口
                "media_stream": 2,  # 禁用媒体流
                "media_stream_mic": 2,  # 禁用麦克风
                "media_stream_camera": 2,  # 禁用摄像头
                "protocol_handlers": 2,
                "ppapi_broker": 2,
                "automatic_downloads": 1
            },
            "profile.managed_default_content_settings": {
                "images": 2
            },
            "profile": {
                "default_content_setting_values": {
                    "images": 2
                }
            },
            "disk-cache-size": 104857600
        })
        
        # 加载turnstilePatch扩展
        if os.path.exists(TURNSTILE_PATCH_PATH):
            chrome_options.add_argument(f'--load-extension={TURNSTILE_PATCH_PATH}')
            self.logger.info(f"✅ 已加载turnstilePatch扩展: {TURNSTILE_PATCH_PATH}")
        else:
            self.logger.warning(f"⚠️ 未找到turnstilePatch扩展目录: {TURNSTILE_PATCH_PATH}")
        
        # 配置Chrome日志
        chrome_options.add_argument('--log-level=3')  # 只显示严重错误
        chrome_options.add_argument('--silent')
        
        try:
            self.logger.info("🔧 初始化Chrome驱动...")
            self.driver = webdriver.Chrome(options=chrome_options)
            
            # 隐藏webdriver属性 - 立即执行
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # 注入CDP命令增强反检测
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                // 增强反检测脚本
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                    configurable: false,
                    enumerable: true
                });
                
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
                    configurable: false,
                    enumerable: true
                });
                
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: () => [1, 2],
                    configurable: false,
                    enumerable: true
                });
                
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'Win32',
                    configurable: false,
                    enumerable: true
                });
                
                // 伪造chrome对象
                window.chrome = {
                    runtime: {
                        PlatformOs: { LINUX: 'linux', MAC: 'mac', WIN: 'win' },
                        PlatformArch: { ARM: 'arm', X86_32: 'x86_32', X86_64: 'x86_64' },
                        PlatformNaclArch: { ARM: 'arm', X86_32: 'x86_32', X86_64: 'x86_64' },
                        RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
                        LastError: {}
                    },
                    loadTimes: () => ({ securityState: 'secure' }),
                    csi: () => ({ onloadT: performance.timing.loadEventEnd || 0, startE: performance.timing.navigationStart || 0 }),
                    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                    webstore: {},
                    management: {}
                };
                
                // 模拟鼠标活动
                window.addEventListener('mousemove', (e) => {
                    window.lastMouseActivity = Date.now();
                });
                
                // 模拟键盘活动
                window.addEventListener('keydown', (e) => {
                    window.lastKeyActivity = Date.now();
                });
                
                // 隐藏扩展痕迹
                if (navigator.userAgent.includes('HeadlessChrome')) {
                    Object.defineProperty(navigator, 'userAgent', {
                        get: () => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        configurable: false
                    });
                }
                
                console.log('🔧 增强反检测脚本已注入');
                '''
            })
            
            # 启用JavaScript和图片（之前在prefs中禁用过）
            self.driver.execute_cdp_cmd('Emulation.setScriptExecutionDisabled', {'value': False})
            
            self.logger.success("✅ Chrome驱动初始化成功")
            
        except Exception as e:
            self.logger.error(f"❌ Chrome驱动初始化失败: {str(e)}")
            raise
            
        self.wait = WebDriverWait(self.driver, 30)  # 增加等待时间

    def robust_username_check(self, max_retries=3, require_logout_button=True):
        """增强的用户名验证 - 确保登录状态真实有效"""
        self.logger.info("🔍 增强验证登录状态...")
        
        for retry in range(max_retries):
            try:
                # 检查多个关键页面
                check_pages = [
                    (self.site_config['latest_url'], "最新话题页面"),
                    (f"{self.site_config['user_url']}/{self.username}", "用户主页"),
                    (self.site_config['base_url'], "首页")
                ]
                
                username_found = False
                logout_button_found = False
                
                for url, page_name in check_pages:
                    try:
                        self.logger.info(f"📍 检查 {page_name}: {url}")
                        self.driver.get(url)
                        time.sleep(random.uniform(4, 7))
                        
                        # 处理可能的Cloudflare验证
                        cf_passed = CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
                        if not cf_passed:
                            self.logger.warning(f"⚠️ {page_name} Cloudflare验证可能有问题")
                        
                        time.sleep(random.uniform(2, 3))
                        
                        # 获取页面内容
                        page_source = self.driver.page_source.lower()
                        current_url = self.driver.current_url
                        
                        # 多重检查1: 用户名在页面内容中
                        if self.username.lower() in page_source:
                            self.logger.success(f"✅ 页面内容中找到用户名: {self.username}")
                            username_found = True
                        else:
                            # 检查是否在meta标签或hidden字段中
                            if any(marker in page_source for marker in [f'"{self.username}"', f"'{self.username}'"]):
                                self.logger.success(f"✅ 在页面元素中找到用户名: {self.username}")
                                username_found = True
                        
                        # 多重检查2: 当前URL是否包含用户名
                        if self.username in current_url:
                            self.logger.success(f"✅ URL中包含用户名: {self.username}")
                            username_found = True
                        
                        # 多重检查3: 检查登录相关元素
                        if require_logout_button:
                            logout_indicators = ["logout", "sign out", "退出", "登出", "user-menu", "avatar", "profile"]
                            if any(indicator in page_source for indicator in logout_indicators):
                                self.logger.success("✅ 找到登出按钮/用户菜单，确认登录状态有效")
                                logout_button_found = True
                                username_found = True  # 找到登出按钮基本可以确认登录
                        
                        # 任何一项检查通过即可
                        if username_found or logout_button_found:
                            if username_found:
                                self.logger.success(f"✅ {page_name} 验证通过")
                            if logout_button_found:
                                self.logger.success("✅ 登出按钮验证通过")
                            return True
                        
                    except Exception as e:
                        self.logger.warning(f"检查 {page_name} 失败: {str(e)}")
                        continue
                
                # 所有页面检查失败
                self.logger.warning(f"❌ 未找到有效登录标志 (尝试 {retry + 1}/{max_retries})")
                
                # 重试前等待
                if retry < max_retries - 1:
                    wait_time = random.uniform(10, 15)
                    self.logger.info(f"🔄 等待 {wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                self.logger.error(f"登录状态检查异常: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(10)
        
        # 所有重试都失败
        self.logger.error(f"❌ 增强验证失败: 无法确认登录状态")
        return False

    def ensure_logged_in(self):
        """确保用户已登录 - 带多重恢复机制的增强版"""
        # 步骤1: 尝试使用会话指纹恢复
        historical_fingerprint = SessionFingerprintManager.load_fingerprint(self.site_name)
        if historical_fingerprint:
            self.logger.info(f"📊 找到历史会话指纹 (哈希: {historical_fingerprint.get('hash', 'N/A')})")
        
        # 步骤2: 尝试使用Cookies缓存登录（如果启用且未强制重新登录）
        if not FORCE_LOGIN_EVERY_TIME:
            self.logger.info("🎯 尝试使用Cookies缓存登录...")
            
            # 验证Cookies缓存是否存在且有效
            cache_file = f"cf_cookies_{self.site_name}.json"
            if CacheManager.is_cache_valid(cache_file, COOKIES_EXPIRY_HOURS):
                if self.load_cookies_from_cache():
                    # 生成当前会话指纹并比较
                    current_fingerprint = SessionFingerprintManager.generate_fingerprint(self.driver, self.site_name)
                    
                    if historical_fingerprint and current_fingerprint:
                        similarity = SessionFingerprintManager.compare_fingerprint(current_fingerprint, historical_fingerprint)
                        self.logger.info(f"📊 会话相似度: {similarity:.2%}")
                        
                        if similarity > 0.5:  # 相似度大于50%认为会话有效
                            # 使用增强验证检查登录状态
                            if self.robust_username_check():
                                self.logger.success("✅ 缓存登录成功 (会话指纹验证通过)")
                                # 更新会话指纹
                                SessionFingerprintManager.generate_fingerprint(self.driver, self.site_name)
                                return True
                            else:
                                self.logger.warning("⚠️ Cookies缓存无效，登录状态验证失败")
                        else:
                            self.logger.warning("⚠️ 会话相似度低，可能需要重新登录")
                    else:
                        # 无历史指纹，直接验证
                        if self.robust_username_check():
                            self.logger.success("✅ Cookies缓存登录成功")
                            # 生成并保存会话指纹
                            SessionFingerprintManager.generate_fingerprint(self.driver, self.site_name)
                            return True
                else:
                    self.logger.warning("⚠️ Cookies加载失败")
            else:
                self.logger.warning("⚠️ Cookies缓存不存在或已过期")
            
            # Cookies缓存失败，尝试加载Cloudflare状态
            self.logger.info("🔄 尝试加载Cloudflare验证状态...")
            cf_state = CloudflareHandler.load_verification_state(self.site_name)
            if cf_state and cf_state.get('success'):
                self.logger.info("✅ 找到成功的Cloudflare验证记录")
            else:
                self.logger.info("ℹ️ 无有效的Cloudflare验证缓存")
        
        # 步骤3: 如果缓存失败或强制登录，执行手动登录
        self.logger.info("🔐 执行完整手动登录流程...")
        login_success = self.attempt_login()
        
        # 登录成功后保存所有状态
        if login_success:
            self.logger.info("💾 登录成功，保存所有状态到缓存...")
            self.save_cookies_to_cache()
            SessionFingerprintManager.generate_fingerprint(self.driver, self.site_name)
            
            # 记录登录成功状态
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'login_success': True,
                'username': self.username,
                'site': self.site_name,
                'method': 'manual_login'
            }
            CacheManager.save_cache(cache_data, f"login_state_{self.site_name}.json")
        
        return login_success

    def save_cookies_to_cache(self, save_fingerprint=True):
        """增强的Cookies缓存保存"""
        try:
            # 获取当前所有cookies
            cookies = self.driver.get_cookies()
            
            # 增强cookie数据
            cookie_data = {
                'cookies': cookies,
                'timestamp': datetime.now().isoformat(),
                'username': self.username,
                'site': self.site_name,
                'total_cookies': len(cookies),
                'session_id': None,
                'user_id': None
            }
            
            # 提取关键cookie信息
            for cookie in cookies:
                if cookie.get('name') == '_forum_session':
                    cookie_data['session_id'] = cookie.get('value')
                elif cookie.get('name') == 'user_id':
                    cookie_data['user_id'] = cookie.get('value')
            
            # 保存到缓存文件
            cache_file = f"cf_cookies_{self.site_name}.json"
            success = CacheManager.save_cache(cookie_data, cache_file)
            
            # 同时保存指纹
            if save_fingerprint and success:
                SessionFingerprintManager.generate_fingerprint(self.driver, self.site_name)
            
            return success
        except Exception as e:
            self.logger.error(f"❌ Cookies缓存失败: {str(e)}")
            return False

    def load_cookies_from_cache(self, load_fingerprint=True):
        """增强的Cookies缓存加载"""
        cache_file = f"cf_cookies_{self.site_name}.json"
        
        # 验证缓存有效性
        if not CacheManager.is_cache_valid(cache_file, COOKIES_EXPIRY_HOURS):
            self.logger.warning("⚠️ Cookies缓存无效或不存在")
            return False
        
        try:
            # 加载缓存数据
            cookie_data = CacheManager.load_cache(cache_file, validate_expiry=True)
            if not cookie_data or 'cookies' not in cookie_data:
                self.logger.error("❌ Cookies缓存数据格式错误")
                return False
            
            # 验证cookie数量
            if len(cookie_data['cookies']) == 0:
                self.logger.warning("⚠️ Cookies缓存中无有效Cookie数据")
                return False
            
            # 加载Cookies到浏览器
            self.driver.get(self.site_config['base_url'])
            time.sleep(3)
            
            loaded_count = 0
            for cookie in cookie_data['cookies']:
                try:
                    # 清理并标准化cookie
                    clean_cookie = {
                        'name': cookie.get('name'),
                        'value': cookie.get('value'),
                        'domain': cookie.get('domain', f".{self.site_name.replace('_', '.')}"),
                        'path': cookie.get('path', '/'),
                        'secure': cookie.get('secure', True),
                        'httpOnly': cookie.get('httpOnly', False)
                    }
                    
                    # 移除过期时间，让浏览器自动管理
                    if 'expiry' in clean_cookie:
                        del clean_cookie['expiry']
                    if 'expires' in clean_cookie:
                        del clean_cookie['expires']
                    if 'sameSite' in clean_cookie:
                        del clean_cookie['sameSite']
                    
                    self.driver.add_cookie(clean_cookie)
                    loaded_count += 1
                except Exception as e:
                    self.logger.debug(f"单个Cookie加载失败: {str(e)}")
                    continue
            
            # 刷新页面使Cookies生效
            self.driver.refresh()
            time.sleep(2)
            
            self.logger.success(f"✅ Cookies已从缓存加载: {loaded_count}/{len(cookie_data['cookies'])} 个")
            
            # 加载历史指纹
            if load_fingerprint:
                historical_fingerprint = SessionFingerprintManager.load_fingerprint(self.site_name)
                if historical_fingerprint:
                    self.logger.info(f"📊 历史会话指纹: {historical_fingerprint.get('hash', 'N/A')}")
            
            return True
        except Exception as e:
            self.logger.error(f"❌ Cookies加载失败: {str(e)}")
            return False

    def attempt_login(self):
        """增强的登录流程"""
        self.logger.info("🔐 开始完整登录流程...")
        
        try:
            # 步骤1: 访问登录页面
            self.logger.info(f"📍 访问登录页面: {self.site_config['login_url']}")
            self.driver.get(self.site_config['login_url'])
            initial_url = self.driver.current_url
            time.sleep(random.uniform(5, 8))

            # 步骤2: 处理Cloudflare验证
            self.logger.info("🛡️ 处理登录页面的Cloudflare验证...")
            cf_passed = CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
            if not cf_passed:
                self.logger.warning("⚠️ Cloudflare验证可能有问题，继续尝试登录...")
            time.sleep(random.uniform(4, 6))

            # 步骤3: 验证页面状态
            current_url = self.driver.current_url
            page_title = self.driver.title
            self.logger.info(f"📄 当前页面状态: {page_title} | {current_url}")
            
            # 如果被重定向，记录跳转
            if current_url != initial_url:
                self.logger.info(f"🔄 已从登录页面重定向: {initial_url} -> {current_url}")
            
            # 如果需要返回登录页面
            if 'login' not in current_url and 'signin' not in current_url:
                self.logger.info("🔄 尝试返回登录页面...")
                self.driver.get(self.site_config['login_url'])
                time.sleep(random.uniform(5, 7))
                CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
                time.sleep(3)

            # 步骤4: 查找表单元素 - 多策略
            username_field = None
            password_field = None
            login_button = None
            
            # 策略1: CSS选择器
            username_selectors = [
                "#login-account-name", "#username", "input[name='username']", 
                "input[name='login']", "input[type='text']", "input[placeholder*='name']",
                "input[placeholder*='用户名']", "input[placeholder*='user']"
            ]
            password_selectors = [
                "#login-account-password", "#password", "input[name='password']", 
                "input[type='password']", "input[placeholder*='password']",
                "input[placeholder*='密码']", "input[placeholder*='pass']"
            ]
            login_button_selectors = [
                "#login-button", "button[type='submit']", "input[type='submit']",
                "button[name='login']", ".btn-login", ".btn-primary",
                ".login-button", "[aria-label*='登录']", "[aria-label*='login']"
            ]

            # 查找用户名字段
            for selector in username_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        username_field = element
                        self.logger.info(f"✅ 找到用户名字段: {selector}")
                        break
                except:
                    continue
            
            # 查找密码字段
            for selector in password_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        password_field = element
                        self.logger.info(f"✅ 找到密码字段: {selector}")
                        break
                except:
                    continue

            # 查找登录按钮
            for selector in login_button_selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        login_button = element
                        self.logger.info(f"✅ 找到登录按钮: {selector}")
                        break
                except:
                    continue

            # 策略2: 通过文本查找按钮
            if not login_button:
                try:
                    buttons = self.driver.find_elements(By.TAG_NAME, "button")
                    for btn in buttons:
                        btn_text = btn.text.lower().strip()
                        btn_aria = btn.get_attribute('aria-label', '').lower()
                        if any(text in btn_text for text in ['登录', 'log in', 'sign in', 'login']) or \
                           any(text in btn_aria for text in ['登录', 'login']):
                            if btn.is_displayed() and btn.is_enabled():
                                login_button = btn
                                self.logger.info("✅ 找到登录按钮 (通过文本)")
                                break
                except:
                    pass

            # 策略3: 查找表单后提交
            if username_field and password_field and not login_button:
                try:
                    # 查找表单
                    form = username_field.find_element(By.XPATH, "./ancestor::form")
                    if form:
                        # 尝试直接提交表单
                        login_button = form
                        self.logger.info("✅ 找到表单，将直接提交")
                except:
                    pass

            # 验证是否找到所有必要元素
            if not username_field:
                self.logger.error("❌ 找不到用户名字段")
                # 保存调试信息
                self.save_debug_info("login_debug", "找不到用户名字段")
                return False

            if not password_field:
                self.logger.error("❌ 找不到密码字段")
                self.save_debug_info("login_debug", "找不到密码字段")
                return False

            if not login_button:
                self.logger.warning("⚠️ 找不到登录按钮，将尝试回车提交")
                # 继续执行，使用回车键提交

            # 步骤5: 模拟真实输入
            self.logger.info("⌨️ 模拟用户名输入...")
            username_field.clear()
            time.sleep(random.uniform(0.8, 1.5))
            
            # 人类速度输入
            for char in self.username:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.08, 0.18))
            
            # 思考停顿
            think_pause = random.uniform(1.5, 2.5)
            self.logger.info(f"🤔 思考停顿 {think_pause:.1f} 秒...")
            time.sleep(think_pause)

            self.logger.info("⌨️ 模拟密码输入...")
            password_field.clear()
            time.sleep(random.uniform(0.8, 1.5))
            
            # 人类速度输入
            for char in self.password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.08, 0.18))

            # 最终思考时间
            final_think = random.uniform(2, 4)
            self.logger.info(f"🤔 最终思考 {final_think:.1f} 秒...")
            time.sleep(final_think)

            # 步骤6: 提交登录
            if login_button and hasattr(login_button, 'click'):
                self.logger.info("🖱️ 点击登录按钮...")
                # 滚动到可见区域
                self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", login_button)
                time.sleep(0.5)
                
                # 模拟鼠标悬停
                actions = ActionChains(self.driver)
                actions.move_to_element(login_button).perform()
                time.sleep(0.3)
                
                login_button.click()
            else:
                self.logger.info("⌨️ 使用回车键提交...")
                password_field.send_keys(Keys.RETURN)

            # 步骤7: 等待登录处理
            login_wait = random.uniform(8, 12)
            self.logger.info(f"⏳ 等待登录处理 {login_wait:.1f} 秒...")
            time.sleep(login_wait)

            # 步骤8: 处理登录后的Cloudflare验证
            self.logger.info("🛡️ 处理登录后的Cloudflare验证...")
            cf_passed = CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
            if not cf_passed:
                self.logger.warning("⚠️ 登录后Cloudflare验证可能有问题")
            time.sleep(random.uniform(5, 8))

            # 步骤9: 验证登录成功
            self.logger.info("🔍 验证登录结果...")
            login_success = self.robust_username_check()
            
            if login_success:
                self.logger.success("✅ 登录流程成功完成")
                # 登录成功后立即保存所有状态
                self.save_all_states()
                return True
            else:
                self.logger.error("❌ 登录验证失败")
                self.save_debug_info("login_error", "登录验证失败")
                return False

        except Exception as e:
            self.logger.error(f"❌ 登录过程出错: {str(e)}")
            self.save_debug_info("login_error", f"异常: {str(e)}")
            return False

    def save_debug_info(self, prefix, message):
        """保存调试信息"""
        try:
            debug_data = {
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'url': self.driver.current_url if self.driver else '',
                'title': self.driver.title if self.driver else '',
                'cookies_count': len(self.driver.get_cookies()) if self.driver else 0
            }
            
            # 保存HTML
            if self.driver and self.driver.page_source:
                html_file = f"{prefix}_{self.site_name}_{int(time.time())}.html"
                with open(html_file, "w", encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                self.logger.info(f"💾 调试HTML已保存: {html_file}")
            
            # 保存JSON信息
            json_file = f"{prefix}_info_{self.site_name}_{int(time.time())}.json"
            with open(json_file, "w", encoding='utf-8') as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2)
            
        except Exception as e:
            self.logger.error(f"❌ 保存调试信息失败: {str(e)}")

    def save_all_states(self):
        """保存所有状态 - 增强版"""
        self.logger.info("💾 保存所有状态到缓存...")
        
        # 1. 保存Cookies
        cookies_saved = self.save_cookies_to_cache(save_fingerprint=False)
        
        # 2. 保存会话指纹
        fingerprint_saved = SessionFingerprintManager.generate_fingerprint(self.driver, self.site_name) is not None
        
        # 3. 保存浏览器状态
        self.generate_browser_state(success=True, browse_count=0, save_all=True)
        
        # 4. 保存Cloudflare状态
        cf_saved = CloudflareHandler.save_verification_state(self.driver, self.site_name, success=True)
        
        self.logger.success(
            f"✅ 状态保存完成: "
            f"Cookies={'✅' if cookies_saved else '❌'}, "
            f"指纹={'✅' if fingerprint_saved else '❌'}, "
            f"CF状态={'✅' if cf_saved else '❌'}"
        )

    def generate_browser_state(self, success=True, browse_count=0, save_all=False):
        """生成浏览器状态文件 - 增强版"""
        try:
            # 基础状态数据
            state_data = {
                'site': self.site_name,
                'last_updated': datetime.now().isoformat(),
                'status': 'completed' if success else 'failed',
                'version': CACHE_VERSION,
                'browse_count': browse_count,
                'login_success': success,
                'execution_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'headless': HEADLESS,
                'force_login': FORCE_LOGIN_EVERY_TIME,
                'session_id': None,
                'total_activities': 0
            }
            
            # 如果driver可用，提取更多信息
            if self.driver:
                try:
                    current_url = self.driver.current_url
                    state_data.update({
                        'current_url': current_url,
                        'page_title': self.driver.title,
                        'cookies_count': len(self.driver.get_cookies()),
                        'window_size': self.driver.execute_script('return {width: window.innerWidth, height: window.innerHeight}'),
                        'user_agent': self.driver.execute_script('return navigator.userAgent')
                    })
                    
                    # 提取关键session信息
                    for cookie in self.driver.get_cookies():
                        if cookie.get('name') == '_forum_session':
                            state_data['session_id'] = cookie.get('value')
                            break
                except:
                    pass
            
            # 计算总活动数
            state_data['total_activities'] = browse_count
            
            # 保存到缓存
            cache_file = f"browser_state_{self.site_name}.json"
            CacheManager.save_cache(state_data, cache_file)
            
            self.logger.info(f"✅ 浏览器状态文件已生成: {cache_file}")
            
            # 如果要求保存所有状态
            if save_all and success:
                # 同步到持久化存储
                self.logger.debug("🔄 同步浏览器状态到持久化存储...")
            
        except Exception as e:
            self.logger.error(f"❌ 生成浏览器状态文件失败: {str(e)}")

    def click_like(self):
        """点赞功能 - 增强版"""
        try:
            # 等待页面完全加载
            time.sleep(2)
            
            # 多种选择器尝试
            like_selectors = [
                ".discourse-reactions-reaction-button", ".like-button", ".btn-like",
                "button[title*='Like']", "button[title*='点赞']", "button[aria-label*='like']"
            ]
            
            for selector in like_selectors:
                try:
                    # 查找所有匹配的按钮
                    like_buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for like_button in like_buttons[:3]:  # 最多尝试前3个
                        if not (like_button.is_displayed() and like_button.is_enabled()):
                            continue
                        
                        # 检查是否已点赞
                        button_class = like_button.get_attribute('class') or ''
                        button_text = like_button.text.lower()
                        
                        if 'has-like' in button_class or 'liked' in button_class or \
                           any(text in button_text for text in ['已点赞', 'liked', '已赞']):
                            self.logger.info("ℹ️ 帖子已经点过赞，跳过")
                            return False
                        
                        # 滚动到元素
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", 
                            like_button
                        )
                        time.sleep(1)
                        
                        # 模拟鼠标悬停
                        actions = ActionChains(self.driver)
                        actions.move_to_element(like_button).perform()
                        time.sleep(0.5)
                        
                        # 点击
                        like_button.click()
                        self.logger.success("✅ 点赞成功")
                        
                        # 随机等待
                        time.sleep(random.uniform(2, 4))
                        return True
                        
                except Exception as e:
                    self.logger.debug(f"选择器 {selector} 尝试失败: {str(e)}")
                    continue
            
            self.logger.info("ℹ️ 未找到可点赞的按钮")
            return False
            
        except Exception as e:
            self.logger.error(f"❌ 点赞失败: {str(e)}")
            return False

    def simulate_reading_behavior(self, stay_time=45):
        """高度模拟真实阅读行为"""
        self.logger.info(f"📖 模拟深度阅读行为 (停留 {stay_time:.1f} 秒)...")
        start_time = time.time()
        
        # 随机滚动策略
        scroll_strategy = random.choice(['deep', 'shallow', 'mixed'])
        scroll_count = 0
        last_action_time = time.time()
        
        while time.time() - start_time < stay_time:
            try:
                # 检查是否有足够时间执行下一个动作
                remaining = stay_time - (time.time() - start_time)
                if remaining < 3:
                    break
                
                # 随机执行不同动作
                action_roll = random.random()
                
                if action_roll < 0.4:  # 40%概率滚动
                    if scroll_strategy == 'deep':
                        scroll_amount = random.randint(300, 1200)
                        scroll_pause = random.uniform(4, 8)
                    elif scroll_strategy == 'shallow':
                        scroll_amount = random.randint(100, 400)
                        scroll_pause = random.uniform(2, 4)
                    else:  # mixed
                        scroll_amount = random.randint(200, 800)
                        scroll_pause = random.uniform(3, 6)
                    
                    self.driver.execute_script(f"window.scrollBy(0, {scroll_amount})")
                    scroll_count += 1
                    
                    # 模拟阅读时间
                    read_time = min(scroll_pause, stay_time - (time.time() - start_time))
                    if read_time > 0:
                        self.logger.debug(f"📚 滚动后阅读 {read_time:.1f} 秒...")
                        time.sleep(read_time)
                    
                elif action_roll < 0.6:  # 20%概率回滚
                    if scroll_count > 0:
                        back_scroll = random.randint(100, 400)
                        self.driver.execute_script(f"window.scrollBy(0, -{back_scroll})")
                        self.logger.debug(f"⬆️ 向上滚动 {back_scroll}px")
                        time.sleep(random.uniform(1, 3))
                
                elif action_roll < 0.75:  # 15%概率暂停思考
                    pause_time = random.uniform(2, 6)
                    pause_time = min(pause_time, stay_time - (time.time() - start_time))
                    if pause_time > 0:
                        self.logger.debug(f"⏸️ 深度思考暂停 {pause_time:.1f} 秒")
                        time.sleep(pause_time)
                
                elif action_roll < 0.85:  # 10%概率点赞
                    if random.random() < 0.3:  # 点赞的概率降低
                        liked = self.click_like()
                        if liked:
                            # 点赞后增加阅读时间
                            bonus_time = random.uniform(3, 5)
                            time.sleep(bonus_time)
                
                else:  # 10%概率检查时间或进行其他微操作
                    micro_action = random.choice(['check_time', 'mouse_move', 'tab_switch'])
                    if micro_action == 'check_time':
                        self.driver.execute_script('console.log("Checking time:", Date.now())')
                    elif micro_action == 'mouse_move':
                        x = random.randint(100, 1800)
                        y = random.randint(100, 900)
                        self.driver.execute_script(f'document.dispatchEvent(new MouseEvent("mousemove", {{clientX: {x}, clientY: {y}}}))')
                    
                    time.sleep(random.uniform(0.5, 1.5))
                
                # 确保至少有一定活动
                if time.time() - last_action_time > 8:
                    # 强制滚动
                    self.driver.execute_script(f"window.scrollBy(0, {random.randint(50, 200)})")
                    last_action_time = time.time()
                
            except Exception as e:
                self.logger.debug(f"阅读模拟异常: {str(e)}")
                time.sleep(1)
        
        # 模拟结束，滚动到页面顶部或随机位置
        final_pos = random.choice(['top', 'middle', 'bottom'])
        if final_pos == 'top':
            self.driver.execute_script("window.scrollTo(0, 0)")
        elif final_pos == 'middle':
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.3)")
        else:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        
        self.logger.debug(f"📊 阅读完成: {scroll_count} 次滚动, 策略: {scroll_strategy}")

    def click_topic(self):
        """浏览主题 - 增强版"""
        if not BROWSE_ENABLED:
            self.logger.info("⏭️ 浏览功能已禁用，跳过")
            return 0

        self.logger.info("🌐 开始增强版主题浏览...")
        
        try:
            # 步骤1: 访问最新页面
            self.logger.info(f"📍 访问最新页面: {self.site_config['latest_url']}")
            self.driver.get(self.site_config['latest_url'])
            time.sleep(random.uniform(5, 7))
            
            # 处理Cloudflare验证
            cf_passed = CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
            if not cf_passed:
                self.logger.warning("⚠️ 最新页面Cloudflare验证可能有问题")
            time.sleep(random.uniform(4, 6))

            # 步骤2: 查找主题元素 - 多策略
            topic_elements = []
            topic_selectors = [
                ".title", "a.title", ".topic-list-item a.title", 
                "tr.topic-list-item a", ".main-link a.title", "a.raw-link"
            ]
            
            for selector in topic_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        # 筛选有效的主题链接
                        topic_elements = []
                        for elem in elements:
                            href = elem.get_attribute('href')
                            if href and '/t/' in href and elem.is_displayed():
                                topic_elements.append(elem)
                        
                        if topic_elements:
                            self.logger.info(f"✅ 使用选择器 '{selector}' 找到 {len(topic_elements)} 个主题")
                            break
                except Exception as e:
                    self.logger.debug(f"选择器 {selector} 查找失败: {str(e)}")
                    continue

            if not topic_elements:
                self.logger.error("❌ 没有找到有效的主题列表")
                self.save_debug_info("no_topics", "未找到主题列表")
                return 0

            # 步骤3: 智能选择浏览数量
            available_topics = len(topic_elements)
            target_count = min(random.randint(12, 120), available_topics)
            
            # 避免重复浏览相同主题
            visited_hrefs = set()
            selected_topics = []
            
            # 随机选择不重复的主题
            max_attempts = min(target_count * 3, available_topics * 2)
            attempts = 0
            
            while len(selected_topics) < target_count and attempts < max_attempts:
                idx = random.randint(0, available_topics - 1)
                topic = topic_elements[idx]
                href = topic.get_attribute('href')
                
                if href and href not in visited_hrefs:
                    visited_hrefs.add(href)
                    selected_topics.append(topic)
                
                attempts += 1
            
            if not selected_topics:
                self.logger.warning("⚠️ 无法选择不重复的主题，使用随机选择...")
                selected_topics = random.sample(topic_elements, min(target_count, len(topic_elements)))

            self.logger.info(f"🎯 计划在 {available_topics} 个主题中浏览 {len(selected_topics)} 个")

            # 步骤4: 开始浏览
            success_count = 0
            for i, topic in enumerate(selected_topics):
                try:
                    # 动态重新获取主题元素，避免stale element
                    try:
                        current_url = self.driver.current_url
                        if self.site_config['latest_url'] not in current_url:
                            # 如果不在最新页面，返回
                            self.driver.get(self.site_config['latest_url'])
                            time.sleep(3)
                        
                        # 重新获取主题元素
                        current_topics = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                        if i < len(current_topics):
                            topic = current_topics[i]
                    except:
                        pass
                    
                    topic_url = topic.get_attribute("href")
                    if not topic_url:
                        continue
                    
                    if not topic_url.startswith('http'):
                        topic_url = self.site_config['base_url'] + topic_url
                    
                    # 显示主题信息（如果有）
                    topic_title = topic.text.strip() if topic.text else "未知标题"
                    self.logger.info(f"📖 浏览第 {i+1}/{len(selected_topics)} 个主题")
                    self.logger.debug(f"   标题: {topic_title[:50]}...")
                    self.logger.debug(f"   URL: {topic_url}")
                    
                    # 在同一标签页打开
                    self.driver.get(topic_url)
                    time.sleep(random.uniform(4, 6))
                    
                    # 处理内页的Cloudflare验证
                    CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
                    time.sleep(random.uniform(2, 4))
                    
                    # 模拟深度阅读
                    page_stay_time = random.uniform(35, 60)
                    self.simulate_reading_behavior(page_stay_time)
                    
                    # 返回列表页
                    self.driver.back()
                    time.sleep(random.uniform(4, 6))
                    
                    success_count += 1
                    
                    # 定期验证登录状态 (每5个主题检查一次)
                    if success_count % 5 == 0 and success_count > 0:
                        self.logger.info(f"===== 每5个主题后验证登录状态 ({success_count}/{len(selected_topics)}) =====")
                        if not self.robust_username_check():
                            self.logger.warning("⚠️ 浏览过程中登录状态丢失，尝试恢复...")
                            # 尝试重新登录
                            if self.ensure_logged_in():
                                self.logger.success("✅ 重新登录成功，继续浏览")
                                # 返回最新页面继续
                                self.driver.get(self.site_config['latest_url'])
                                time.sleep(4)
                                # 重新获取主题列表
                                current_topics = self.driver.find_elements(By.CSS_SELECTOR, ".title")
                                if not current_topics:
                                    break
                            else:
                                self.logger.error("❌ 重新登录失败，停止浏览")
                                break
                    
                    # 主题间随机间隔
                    if i < len(selected_topics) - 1:
                        interval = random.uniform(15, 25)
                        self.logger.info(f"⏳ 主题间间隔 {interval:.1f} 秒...")
                        time.sleep(interval)
                        
                except StaleElementReferenceException:
                    self.logger.warning("⚠️ 主题元素已过时，跳过当前")
                    continue
                except Exception as e:
                    self.logger.error(f"❌ 浏览主题失败: {str(e)}")
                    # 尝试恢复
                    try:
                        self.driver.get(self.site_config['latest_url'])
                        time.sleep(3)
                    except:
                        break
                    continue

            self.logger.success(f"✅ 浏览完成: 成功 {success_count}/{len(selected_topics)} 个主题")
            
            # 浏览后验证登录状态
            self.logger.info("===== 浏览完成后最终验证登录状态 =====")
            if not self.robust_username_check():
                self.logger.warning("⚠️ 浏览后登录状态验证失败，尝试恢复...")
                if self.ensure_logged_in():
                    self.logger.success("✅ 最终验证通过")
                else:
                    self.logger.error("❌ 最终验证失败")
            
            return success_count
            
        except Exception as e:
            self.logger.error(f"❌ 浏览主题失败: {str(e)}")
            return 0

    def get_user_stats(self):
        """获取用户信任级别统计信息 - 增强版"""
        self.logger.info("📊 获取用户信任级别统计信息...")
        
        max_retries = 3
        for retry in range(max_retries):
            try:
                connect_url = self.site_config['connect_url']
                self.logger.info(f"📍 访问: {connect_url}")
                self.driver.get(connect_url)
                time.sleep(random.uniform(7, 10))
                
                # 处理Cloudflare验证
                CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
                time.sleep(random.uniform(5, 7))
                
                # 获取并解析页面
                page_source = self.driver.page_source
                
                # 使用BeautifulSoup解析
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page_source, 'html.parser')
                
                # 查找统计表格
                stats_table = None
                tables = soup.find_all('table')
                
                for table in tables:
                    table_text = table.get_text()
                    if any(keyword in table_text for keyword in ['访问次数', '回复的话题', '浏览的话题', '已读帖子', '给予赞']):
                        stats_table = table
                        break
                
                if not stats_table:
                    self.logger.warning("⚠️ 未找到统计表格，尝试备用方法")
                    return self._parse_stats_advanced()
                
                # 提取数据
                stats_data = []
                rows = stats_table.find_all('tr')
                
                for row in rows[1:]:
                    cols = row.find_all(['td', 'th'])
                    if len(cols) >= 3:
                        item = cols[0].get_text(strip=True)
                        current = cols[1].get_text(strip=True)
                        requirement = cols[2].get_text(strip=True)
                        
                        # 检查状态颜色
                        col = cols[1]
                        color = 'unknown'
                        if col.get('class'):
                            col_class = ' '.join(col.get('class'))
                            if 'text-green' in col_class or 'green' in col_class:
                                color = 'green'
                            elif 'text-red' in col_class or 'red' in col_class:
                                color = 'red'
                        
                        stats_data.append([item, current, requirement, color])
                
                if stats_data:
                    return self._display_stats(stats_data)
                else:
                    self.logger.warning("⚠️ 未提取到统计信息")
                    if retry < max_retries - 1:
                        time.sleep(5)
                        continue
                
            except Exception as e:
                self.logger.error(f"获取统计信息失败 (尝试 {retry + 1}/{max_retries}): {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(8)
        
        return False

    def _parse_stats_advanced(self):
        """高级统计信息解析 - 备用方法"""
        try:
            self.logger.info("尝试高级备用解析方法...")
            
            # 查找所有包含统计信息的元素
            elements = self.driver.find_elements(By.CSS_SELECTOR, "tr, .stat-row, .requirement-row")
            stats_data = []
            
            for elem in elements:
                try:
                    text = elem.text.strip()
                    if not text:
                        continue
                    
                    # 解析类似 "访问次数\n当前: 15\n要求: 5" 的格式
                    lines = text.split('\n')
                    if len(lines) >= 3:
                        # 检查是否包含指标关键词
                        if any(keyword in text.lower() for keyword in [
                            '访问', '回复', '浏览', '已读', '给予', '收到', '话题', '帖子'
                        ]):
                            item = lines[0]
                            current = ""
                            requirement = ""
                            
                            # 解析当前值和要求值
                            for line in lines[1:]:
                                line = line.lower()
                                if any(kw in line for kw in ['当前', 'current', '已达成']):
                                    current = line.replace('当前:', '').replace('current:', '').strip()
                                elif any(kw in line for kw in ['要求', 'requirement', '需要']):
                                    requirement = line.replace('要求:', '').replace('requirement:', '').strip()
                            
                            if item and current and requirement:
                                # 检查颜色（通过父元素或自身class）
                                color = 'unknown'
                                try:
                                    parent = elem.find_element(By.XPATH, "..")
                                    if 'text-green' in (elem.get_attribute('class') or '') or \
                                       'text-green' in (parent.get_attribute('class') or ''):
                                        color = 'green'
                                    elif 'text-red' in (elem.get_attribute('class') or '') or \
                                         'text-red' in (parent.get_attribute('class') or ''):
                                        color = 'red'
                                except:
                                    pass
                                
                                stats_data.append([item, current, requirement, color])
                
                except Exception as e:
                    self.logger.debug(f"元素解析失败: {str(e)}")
                    continue
            
            if stats_data:
                return self._display_stats(stats_data)
            
            self.logger.warning("⚠️ 高级备用解析方法也未找到数据")
            return False
            
        except Exception as e:
            self.logger.error(f"❌ 高级备用解析失败: {str(e)}")
            return False

    def _display_stats(self, stats_data):
        """显示统计数据"""
        try:
            print("\n" + "="*80)
            print(f"📈 {self.site_name.upper()} 信任级别要求统计")
            print("="*80)
            
            # 导入tabulate用于表格显示
            try:
                from tabulate import tabulate
                print(tabulate(stats_data, headers=["项目", "当前", "要求", "状态"], tablefmt="grid"))
            except ImportError:
                # 回退显示方式
                print(f"{'项目':<25} {'当前':<30} {'要求':<20} {'状态':<10}")
                print("-" * 80)
                for item in stats_data:
                    status = "✅" if item[3] == 'green' else "❌" if item[3] == 'red' else "➖"
                    print(f"{item[0]:<25} {item[1]:<30} {item[2]:<20} {status}")
            
            print("="*80 + "\n")
            
            # 统计达标情况
            passed = sum(1 for item in stats_data if item[3] == 'green')
            total = len(stats_data)
            self.logger.success(f"📊 统计完成: {passed}/{total} 项达标 ({passed/total:.1%})")
            
            # 记录关键指标
            for item in stats_data:
                if any(keyword in item[0] for keyword in ['访问天数', '访问次数', '给予赞']):
                    status = "✅" if item[3] == 'green' else "❓"
                    self.logger.info(f"{status} 关键指标 - {item[0]}: {item[1]} / {item[2]}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 显示统计数据失败: {str(e)}")
            return False

    def print_connect_info(self):
        """打印连接信息 - 增强版"""
        self.logger.info("🔗 获取连接信息")
        max_retries = 2
        
        for retry in range(max_retries):
            try:
                self.logger.info(f"📍 访问: {self.site_config['connect_url']}")
                self.driver.get(self.site_config['connect_url'])
                time.sleep(random.uniform(7, 10))

                # 处理Cloudflare
                CloudflareHandler.handle_cloudflare_with_doh(self.driver, self.site_name)
                time.sleep(random.uniform(5, 7))

                # 解析页面
                page_source = self.driver.page_source
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page_source, 'html.parser')

                # 查找统计表格
                tables = soup.find_all('table')
                stats_table = None
                
                for table in tables:
                    if table.find(string=lambda text: text and '访问次数' in text):
                        stats_table = table
                        break
                
                if stats_table:
                    return self._parse_connect_table(stats_table)
                
                if retry < max_retries - 1:
                    self.logger.warning("⚠️ 未找到连接信息表格，重试中...")
                    time.sleep(6)
                    
            except Exception as e:
                self.logger.error(f"获取连接信息失败: {str(e)}")
                if retry < max_retries - 1:
                    time.sleep(5)
        
        return False

    def _parse_connect_table(self, stats_table):
        """解析连接信息表格"""
        try:
            stats_data = []
            rows = stats_table.find_all('tr')
            
            for row in rows[1:]:
                cols = row.find_all(['td', 'th'])
                if len(cols) >= 3:
                    item = cols[0].get_text(strip=True)
                    current = cols[1].get_text(strip=True)
                    requirement = cols[2].get_text(strip=True)
                    
                    # 检查状态
                    col_class = cols[1].get('class', [])
                    if isinstance(col_class, list):
                        col_class = ' '.join(col_class)
                    status = '✅' if 'text-green' in col_class or 'green' in col_class else '❌' if 'text-red' in col_class or 'red' in col_class else '➖'
                    
                    stats_data.append([item, current, requirement, status])

            if not stats_data:
                self.logger.warning("⚠️ 连接信息表格为空")
                return False

            print("\n" + "="*80)
            print(f"📊 {self.site_name.upper()} 连接信息")
            print("="*80)

            try:
                from tabulate import tabulate
                print(tabulate(stats_data, headers=["项目", "当前", "要求", "状态"], tablefmt="grid"))
            except ImportError:
                print(f"{'项目':<25} {'当前':<30} {'要求':<20} {'状态':<10}")
                print("-" * 80)
                for item in stats_data:
                    print(f"{item[0]:<25} {item[1]:<30} {item[2]:<20} {item[3]}")

            print("="*80 + "\n")

            # 统计
            passed = sum(1 for item in stats_data if item[3] == '✅')
            total = len(stats_data)
            self.logger.success(f"📊 连接信息统计: {passed}/{total} 项达标")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 解析连接信息表格失败: {str(e)}")
            return False

    def perform_additional_activities(self):
        """执行额外活跃行为"""
        self.logger.info("🎯 执行额外活跃行为提升信任等级...")
        
        activities_performed = 0
        activities_log = []
        
        try:
            # 活动1: 访问更多页面类型
            additional_pages = [
                ("/categories", "分类页面"),
                ("/top", "热门话题"),
                ("/about", "关于页面"),
                ("/faq", "FAQ页面"),
                ("/guidelines", "社区指南")
            ]
            
            # 随机选择2-3个页面访问
            selected_pages = random.sample(additional_pages, min(random.randint(2, 3), len(additional_pages)))
            
            for page, desc in selected_pages:
                try:
                    url = self.site_config['base_url'] + page
                    self.logger.info(f"📍 访问{desc}: {page}")
                    self.driver.get(url)
                    time.sleep(random.uniform(8, 15))
                    
                    # 模拟浏览
                    self.simulate_reading_behavior(random.uniform(12, 25))
                    activities_performed += 1
                    activities_log.append(desc)
                    
                    # 页面间间隔
                    if page != selected_pages[-1][0]:
                        interval = random.uniform(10, 18)
                        self.logger.info(f"⏳ 页面间等待 {interval:.1f} 秒...")
                        time.sleep(interval)
                        
                except Exception as e:
                    self.logger.warning(f"访问{desc}失败: {str(e)}")
                    continue
            
            # 活动2: 用户主页交互
            try:
                user_profile_url = f"{self.site_config['user_url']}/{self.username}/summary"
                self.logger.info(f"📍 访问个人主页: {user_profile_url}")
                self.driver.get(user_profile_url)
                time.sleep(random.uniform(6, 10))
                
                # 模拟在主页浏览
                self.simulate_reading_behavior(random.uniform(8, 15))
                activities_performed += 1
                activities_log.append("个人主页浏览")
                
            except Exception as e:
                self.logger.warning(f"个人主页交互失败: {str(e)}")
            
            # 活动3: 随机点击分类
            try:
                self.logger.info("📂 随机浏览分类...")
                self.driver.get(self.site_config['base_url'] + "/categories")
                time.sleep(4)
                
                # 查找分类
                category_links = self.driver.find_elements(By.CSS_SELECTOR, ".category a, .category-link, .category-title")
                if category_links:
                    # 随机选择1-2个分类
                    num_categories = random.randint(1, min(2, len(category_links)))
                    selected_cats = random.sample(category_links, num_categories)
                    
                    for cat in selected_cats:
                        try:
                            cat_url = cat.get_attribute('href')
                            if cat_url:
                                self.logger.info(f"📍 访问分类: {cat.text[:30]}")
                                self.driver.get(cat_url)
                                time.sleep(random.uniform(5, 10))
                                
                                # 浏览分类内容
                                self.simulate_reading_behavior(random.uniform(10, 18))
                                activities_performed += 1
                                activities_log.append(f"分类浏览: {cat.text[:20]}")
                                
                                # 返回分类列表
                                self.driver.back()
                                time.sleep(3)
                                
                        except Exception as e:
                            self.logger.debug(f"分类浏览失败: {str(e)}")
                            continue
            
            except Exception as e:
                self.logger.debug(f"分类浏览整体失败: {str(e)}")
            
            # 报告活动结果
            if activities_log:
                self.logger.success(f"✅ 完成 {activities_performed} 项额外活跃行为:")
                for log in activities_log:
                    self.logger.info(f"   - {log}")
            else:
                self.logger.warning("⚠️ 未完成任何额外活跃行为")
            
            return activities_performed
            
        except Exception as e:
            self.logger.error(f"❌ 执行额外活跃行为失败: {str(e)}")
            return activities_performed

    def run(self):
        """执行完整自动化流程 - 终极增强版"""
        start_time = time.time()
        self.logger.info(f"🚀 开始执行完整流程 (超时: 110分钟)")
        
        try:
            # 步骤1: 确保登录
            login_success = self.ensure_logged_in()
            if not login_success:
                self.logger.error(f"❌ {self.site_name} 登录流程失败")
                self.generate_browser_state(success=False, browse_count=0)
                return False
            
            # 步骤2: 获取初始统计
            self.logger.info("📊 获取初始统计数据...")
            self.get_user_stats()
            
            # 步骤3: 执行额外活跃行为
            self.logger.info("🎯 执行前测额外活跃行为...")
            extra_activities_before = self.perform_additional_activities()
            
            # 步骤4: 浏览主题
            browse_success_count = self.click_topic()
            if browse_success_count == 0:
                self.logger.error("❌ 主题浏览失败")
                # 尝试重新登录后继续
                self.logger.info("🔄 尝试重新登录后再次浏览...")
                if self.ensure_logged_in():
                    browse_success_count = self.click_topic()
            
            # 步骤5: 执行浏览后额外活跃行为
            self.logger.info("🎯 执行后测额外活跃行为...")
            extra_activities_after = self.perform_additional_activities()
            
            # 步骤6: 获取最终统计
            self.logger.info("📊 获取最终统计数据...")
            self.get_user_stats()
            
            # 步骤7: 打印连接信息
            self.print_connect_info()
            
            # 步骤8: 保存所有状态
            total_activities = browse_success_count + extra_activities_before + extra_activities_after
            self.generate_browser_state(success=True, browse_count=total_activities, save_all=True)
            
            elapsed = time.time() - start_time
            self.logger.success(
                f"✅ {self.site_name} 完整流程完成 - "
                f"总计 {total_activities} 项活动, "
                f"耗时 {elapsed/60:.1f} 分钟"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ {self.site_name} 执行异常: {str(e)}")
            self.save_debug_info("run_error", f"异常: {str(e)}")
            self.generate_browser_state(success=False, browse_count=0)
            return False
            
        finally:
            # 确保在GitHub Actions环境中总是尝试保存状态
            if os.getenv('GITHUB_ACTIONS') == 'true':
                self
