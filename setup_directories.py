import os
import json

def create_turnstile_patch():
    """创建TurnstilePatch扩展目录和文件"""
    print("🔧 创建turnstilePatch扩展...")
    
    # 创建目录
    if not os.path.exists("turnstilePatch"):
        os.makedirs("turnstilePatch")
        print("✅ 创建目录: turnstilePatch")
    
    # 创建manifest.json
    manifest = {
        "manifest_version": 3,
        "name": "TurnstilePatch",
        "version": "1.0",
        "description": "Patch for Cloudflare Turnstile",
        "permissions": [
            "webRequest",
            "webRequestBlocking",
            "storage",
            "tabs",
            "activeTab"
        ],
        "host_permissions": [
            "*://*/*"
        ],
        "background": {
            "service_worker": "script.js"
        },
        "content_scripts": [
            {
                "matches": ["*://*.linux.do/*", "*://*.idcflare.com/*"],
                "js": ["content.js"],
                "run_at": "document_start",
                "all_frames": True
            }
        ],
        "action": {},
        "icons": {
            "16": "icon.png",
            "48": "icon.png",
            "128": "icon.png"
        }
    }
    
    with open("turnstilePatch/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print("✅ 创建 manifest.json")
    
    # 创建content.js
    content_js = """
// 在页面加载前注入，隐藏自动化特征
(function() {
    'use strict';
    
    // 拦截和修改navigator对象
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
    
    // 模拟真实用户的插件
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    
    // 模拟语言
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en']
    });
    
    // 模拟mimeTypes
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => [1, 2]
    });
    
    // 添加chrome对象
    if (!window.chrome) {
        window.chrome = {
            runtime: {}
        };
    }
    
    // 移除连接信息
    if (navigator.connection) {
        delete navigator.connection;
    }
    
    // 拦截Turnstile检测
    if (window.turnstile) {
        const originalReady = window.turnstile.ready;
        if (originalReady) {
            window.turnstile.ready = function(callback) {
                setTimeout(() => {
                    callback();
                }, Math.random() * 1000 + 500);
            };
        }
    }
    
    // 随机化Canvas指纹
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function() {
        const result = originalToDataURL.apply(this, arguments);
        // 添加微小扰动
        if (arguments[0] === 'image/png') {
            return result + Math.random().toString(36).substring(2, 8);
        }
        return result;
    };
    
    console.log('[TurnstilePatch] 注入成功');
})();
"""
    
    with open("turnstilePatch/content.js", "w") as f:
        f.write(content_js.strip())
    print("✅ 创建 content.js")
    
    # 创建script.js
    script_js = """
// Service Worker脚本
chrome.webRequest.onBeforeRequest.addListener(
    function(details) {
        const url = details.url;
        
        // 拦截已知检测脚本
        if (url.includes('challenges.cloudflare.com') || url.includes('turnstile')) {
            return { cancel: false };
        }
        
        // 修改请求头
        const requestHeaders = details.requestHeaders || [];
        requestHeaders.push({
            name: 'X-Patched',
            value: 'true'
        });
        
        return { requestHeaders: requestHeaders };
    },
    { urls: ["*://*/*"] },
    ["blocking", "requestHeaders"]
);

// 监听消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "getTurnstileToken") {
        // 模拟返回token
        sendResponse({ token: "mock-token-" + Math.random().toString(36).substring(2, 15) });
    }
});

console.log('[TurnstilePatch] Service Worker 已加载');
"""
    
    with open("turnstilePatch/script.js", "w") as f:
        f.write(script_js.strip())
    print("✅ 创建 script.js")
    
    print("🎉 Turnstile Patch 扩展创建完成")
    print("📁 所有目录和文件创建成功")

if __name__ == "__main__":
    create_turnstile_patch()
