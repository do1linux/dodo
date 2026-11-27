# -*- coding: utf-8 -*-
"""cf_bridge.py — 线上全自动：agnes-3.0-flash vision 解 hCaptcha 拖拽拼图 → 拿 cf_clearance → 写缓存文件
让 main.py 缓存登录 linux.do 过盾 → 读 /unread 帖 → 积分真涨。

缓存链路（GitHub Actions）：
  run 开始：actions/cache 恢复上次 cf_cookies_linux_do.json
  cf_bridge：解出**新** cf_clearance → **覆盖**写 cf_cookies_linux_do.json（CacheManager 格式）
  main.py：CacheManager.load_site_cache('linux_do','cf_cookies') 读文件 → 注入 linux.do → 过盾登录
  run 结束：actions/cache 存回最新文件 → 下次恢复
LINUXDO_CF_COOKIES 环境变量仅作首跑 seed（cache 未建时），之后靠文件覆盖刷新。
"""
import os, sys, time, json, base64, random, re
import requests

AGNES_BASE = "https://apihub.agnes-ai.com/v1"
AGNES_VISION_MODEL = "agnes-3.0-flash"    # 多模态（text+image），该 key 免费配额可用
AGNES_KEY = os.environ.get("AGNES_API_KEY", "")
BASE = "https://linux.do"
USERNAME = os.environ.get("LINUXDO_USERNAME", "")
PASSWORD = os.environ.get("LINUXDO_PASSWORD", "")
# 缓存文件（仓根，与 main.py CacheManager.get_cache_directory()=os.path.dirname(__file__) 一致）
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cf_cookies_linux_do.json")

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def parse_cookie_string(s):
    """LINUXDO_CF_COOKIES 是 cookie 字符串（a=1; b=2），非 JSON → 解析为 list[dict]。"""
    out = []
    if not s:
        return out
    for part in s.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out.append({"name": k.strip(), "value": v.strip(), "domain": "linux.do",
                        "path": "/", "secure": True, "httpOnly": False, "sameSite": "Lax"})
    return out

def solve_drag_puzzle(bg_b64, piece_b64):
    """agnes vision 解拖拽缺口坐标（背景+缺块 2 图 → {x_pct,y_pct}）。"""
    if not AGNES_KEY:
        log("❌ AGNES_API_KEY 未设置"); return None
    resp = requests.post(f"{AGNES_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {AGNES_KEY}"},
        json={"model": AGNES_VISION_MODEL, "max_tokens": 120, "temperature": 0.0,
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": "Drag the puzzle piece to fill the gap. Reply ONLY JSON {\"x_pct\": 55.2, \"y_pct\": 48.1}."},
                  {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{bg_b64}"}},
                  {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{piece_b64}"}}]}]},
        timeout=60)
    log(f"agnes vision http={resp.status_code}")
    if resp.status_code != 200:
        log(f"❌ vision 失败: {resp.text[:200]}"); return None
    txt = resp.json()["choices"][0]["message"]["content"]
    t = txt.strip()
    if t.startswith("```"):
        t = t.split("```")[1].replace("json", "", 1).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"x_pct[\"':\s]+([\d.]+)[\s\S]*?y_pct[\"':\s]+([\d.]+)", txt)
        return {"x_pct": float(m.group(1)), "y_pct": float(m.group(2))} if m else None

def save_cf_cookies(page):
    """把当前 browser 的 cf_clearance + _forum_session 写缓存文件（覆盖，CacheManager 格式）。"""
    try:
        cookies = page.cookies()
        # 只保留 linux.do 相关
        ld = [c for c in cookies if "linux.do" in (c.get("domain", "") or "")]
        json.dump(ld, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        names = [c.get("name") for c in ld]
        log(f"💾 写 {CACHE_FILE}（{len(ld)} 个 cookie：{names}）")
        return True
    except Exception as e:
        log(f"缓存写入失败: {e}"); return False

def run_browser_flow(cached_cookies=None):
    """注入 cache cookie → 过 CF 盾 → hCaptcha 截图给 vision 拿坐标 → 自动拖 → 登录 → 写 cf_clearance 缓存。"""
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions()
    co.set_argument("--window-size=1920,1080")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36")
    co.headless(True)
    co.set_local_port(9333)
    page = ChromiumPage(co)
    try:
        # 注入 cache cookie（cf_clearance 可能有效 → 直接过盾）
        if cached_cookies:
            page.get(BASE); time.sleep(2)
            page.set.cookies(cached_cookies); time.sleep(1)
            page.refresh(); time.sleep(4)
        # 到登录页
        page.get(BASE + "/login"); time.sleep(3)
        # CF 盾：等 title 不再 "Just a moment"
        for _ in range(30):
            if "just a moment" not in (page.title or "").lower() and "checking" not in (page.title or "").lower():
                break
            time.sleep(3)
        # hCaptcha 真拖拽（P0-1 修复：iframe 内取背景图/缺块图 2 张不同图喂 vision → 真 mouse 拖拽）
        solved = False
        for attempt in range(3):
            try:
                # 切 hCaptcha iframe（src 含 hcaptcha）
                hc = page.get_frame(src="hcaptcha", timeout=0)
                if not hc:
                    log(f"  hCaptcha attempt {attempt+1}: 无 iframe（CF 盾可能未触发 hCaptcha）")
                    time.sleep(4)
                    continue
                # 背景图 + 缺块图（hCaptcha 拼图：img.bg + img.puzzle，两张不同图）
                bg = hc.ele("css:img.hcaptcha-canvas, img[alt*='background'], img[src*='challenge']", timeout=5)
                piece = hc.ele("css:img.hcaptcha-puzzle, img[alt*='puzzle'], img[src*='puzzle']", timeout=5)
                if not bg:
                    log(f"  hCaptcha attempt {attempt+1}: 背景图未找到")
                    time.sleep(4)
                    continue
                # 各取 src（iframe 内 img src）
                bg_src = bg.attr("src") or ""
                piece_src = piece.attr("src") if piece else ""
                # 下载 2 张图 base64（缺块没有就只用背景，vision 仍给缺口 x）
                import base64 as _b64
                def _img_b64(url):
                    if not url:
                        return ""
                    try:
                        if url.startswith("data:"):
                            return url.split(",", 1)[1]
                        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                        return _b64.b64encode(r.content).decode()
                    except Exception:
                        return ""
                bg_b64 = _img_b64(bg_src)
                piece_b64 = _img_b64(piece_src) or bg_b64   # 缺块图缺失则用背景兜底
                if not bg_b64:
                    log(f"  hCaptcha attempt {attempt+1}: 背景图 src 取不到（{bg_src[:60]}）")
                    time.sleep(4)
                    continue
                coords = solve_drag_puzzle(bg_b64, piece_b64)
                if not coords:
                    continue
                # 真 mouse 拖拽：定位拼图块 draggable + 拖到缺口 x
                # hCaptcha 拖拽控件：.h-captcha-draggable / .h-captcha-slide / 拼图块
                drag_handle = hc.ele("css:.h-captcha-draggable, .h-captcha-slide, img.hcaptcha-puzzle, [class*='drag']", timeout=0)
                if not drag_handle:
                    log(f"  hCaptcha attempt {attempt+1}: 拖拽控件未找到")
                    time.sleep(4)
                    continue
                # 缺口像素 x（背景图宽 px，hCaptcha 背景渲染宽约 300px）
                bg_w = int(bg.attr("width") or 300)
                target_x = int(coords.get("x_pct", 50) / 100 * bg_w)
                # DrissionPage 真拖拽（mouse down → move → up 事件链，模拟真人）
                try:
                    drag_handle.drag_to(target_x, 0, duration=0.8, times=3)   # 渐进拖（防瞬移被风控）
                except Exception:
                    # 兜底：mouse 事件链
                    x0, y0 = drag_handle.x, drag_handle.y
                    page.mouse.down(); page.mouse.move(x0 + target_x, y0, duration=800); page.mouse.up()
                log(f"  hCaptcha attempt {attempt+1}: 拖到 x={target_x}px (vision {coords.get('x_pct')}%)")
                time.sleep(4)
                # 验证拖拽是否过（hCaptcha 消失 / 出现 success）
                page.wait(3)
                if not page.get_frame(src="hcaptcha", timeout=0):
                    log("  hCaptcha 已消失（拖拽成功）")
                    solved = True
                    break
            except Exception as e:
                log(f"  hCaptcha 拖拽异常: {e}")
                time.sleep(4)
        # 登录（若 hCaptcha 过了 + 账密）
        try:
            if page.ele("#login-account-name", timeout=5):
                page.ele("#login-account-name").input(USERNAME); time.sleep(0.4)
                page.ele("#login-account-password").input(PASSWORD); time.sleep(0.4)
                page.ele("#login-button").click(); time.sleep(12)
        except Exception as e:
            log(f"  登录填表异常（可能无需账密）: {e}")
        time.sleep(5)
        # 验证登录成功标志 = 页面含 username
        ok = USERNAME and (USERNAME in (page.html or ""))
        log(f"login ok(页面含 {USERNAME})={ok}")
        # 写 cf_clearance 缓存（无论 ok，存当前 cookie 供 main.py 用）
        save_cf_cookies(page)
        return ok
    except Exception as e:
        log(f"flow err: {e}"); return False
    finally:
        try:
            page.quit()
        except Exception:
            pass

def main():
    seed = parse_cookie_string(os.environ.get("LINUXDO_CF_COOKIES", ""))
    # 优先读本地缓存文件（上次 run 存的新 cf_clearance），seed 环境变量兜底
    local_cache = []
    if os.path.exists(CACHE_FILE):
        try:
            local_cache = json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception:
            local_cache = []
    cached = local_cache if local_cache else seed
    log(f"start, cached={len(cached)} (local_file={len(local_cache)}, env_seed={len(seed)})")
    ok = run_browser_flow(cached)
    if ok:
        log("✅ linux.do 登录成功，cf_clearance 已写缓存（main.py 复用）")
    else:
        log("⚠️ linux.do 未登录（main.py 用现有缓存兜底）")
    # 不 sys.exit(1)——cf_bridge 失败不阻断 main.py（缓存兜底）
    sys.exit(0)

if __name__ == "__main__":
    main()
