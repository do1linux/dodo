import os

def create_turnstile_patch():
    # 创建扩展目录
    patch_dir = "turnstilePatch"
    if not os.path.exists(patch_dir):
        os.makedirs(patch_dir)
        print(f"✅ 创建目录: {patch_dir}")

    # 创建 manifest.json
    manifest_content = '''{
  "manifest_version": 3,
  "name": "Turnstile Bypass Patch",
  "version": "1.0",
  "description": "Bypass Cloudflare Turnstile for automation",
  "permissions": ["scripting", "webRequest", "webRequestBlocking", "<all_urls>"],
  "background": {
    "service_worker": "script.js"
  },
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "run_at": "document_start",
      "js": ["content.js"]
    }
  ],
  "web_accessible_resources": [
    {
      "resources": ["*"],
      "matches": ["<all_urls>"]
    }
  ]
}'''
    with open(os.path.join(patch_dir, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(manifest_content)
    print("✅ 创建 manifest.json")

    # 创建 content.js
    content_content = '''// 消除自动化特征
delete navigator.webdriver;
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64' });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// 拦截 Turnstile 验证请求
document.addEventListener('DOMContentLoaded', () => {
  // 移除 Cloudflare 验证容器
  const cfTurnstile = document.querySelector('.cf-turnstile-container, #turnstile-wrapper');
  if (cfTurnstile) cfTurnstile.remove();

  // 模拟验证通过
  window.turnstile = {
    render: (el, config) => {
      setTimeout(() => {
        config.callback('fake-valid-token');
      }, 1000);
    }
  };

  // 触发页面继续加载
  const cfContinue = document.querySelector('.cf-browser-verification-continue');
  if (cfContinue) cfContinue.click();
});'''
    with open(os.path.join(patch_dir, "content.js"), "w", encoding="utf-8") as f:
        f.write(content_content)
    print("✅ 创建 content.js")

    # 创建 script.js
    script_content = '''// 拦截 Cloudflare 验证相关请求
chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    // 添加真实浏览器的请求头
    const headers = details.requestHeaders || [];
    headers.push(
      { name: 'Sec-Fetch-Dest', value: 'document' },
      { name: 'Sec-Fetch-Mode', value: 'navigate' },
      { name: 'Sec-Fetch-Site', value: 'same-origin' },
      { name: 'Sec-Fetch-User', value: '?1' },
      { name: 'Upgrade-Insecure-Requests', value: '1' }
    );
    return { requestHeaders: headers };
  },
  { urls: ['<all_urls>'] },
  ['blocking', 'requestHeaders', 'extraHeaders']
);

// 拦截验证响应，直接返回通过
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.url.includes('/cdn-cgi/challenge-platform/') || details.url.includes('turnstile/v2/')) {
      return {
        redirectUrl: 'data:text/plain;charset=utf-8,fake-valid-response'
      };
    }
  },
  { urls: ['<all_urls>'] },
  ['blocking']
);'''
    with open(os.path.join(patch_dir, "script.js"), "w", encoding="utf-8") as f:
        f.write(script_content)
    print("✅ 创建 script.js")

if __name__ == "__main__":
    print("🔧 开始创建必要的目录结构...")
    create_turnstile_patch()
    print("✅ Turnstile Patch 扩展创建完成")
    print("🎉 所有目录和文件创建成功")
