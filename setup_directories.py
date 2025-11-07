import os
import json

def create_turnstile_patch():
    """创建 Turnstile Patch 扩展"""
    extension_dir = "turnstilePatch"
    os.makedirs(extension_dir, exist_ok=True)
    
    # 创建 manifest.json
    manifest = {
        "manifest_version": 3,
        "name": "Turnstile Patch",
        "version": "1.0",
        "content_scripts": [{
            "matches": ["https://linux.do/*", "https://idcflare.com/*"],
            "js": ["script.js"],
            "run_at": "document_end"
        }]
    }
    
    with open(os.path.join(extension_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    
    # 创建 script.js
    script_content = """
// Turnstile Patch - 自动处理 Cloudflare Turnstile 验证
(function() {
    'use strict';
    
    console.log('🔧 Turnstile Patch 已加载');
    
    function waitForTurnstile() {
        if (typeof turnstile !== 'undefined') {
            console.log('✅ 检测到 Turnstile，准备自动处理');
            handleTurnstile();
        } else {
            setTimeout(waitForTurnstile, 500);
        }
    }
    
    function handleTurnstile() {
        try {
            // 重置 Turnstile
            turnstile.reset();
            
            // 获取响应 token
            const response = turnstile.getResponse();
            if (response) {
                console.log('✅ 获取到 Turnstile token:', response.substring(0, 20) + '...');
                
                // 设置到表单字段
                const input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input) {
                    input.value = response;
                    console.log('✅ 已设置 cf-turnstile-response');
                }
                
                // 触发变化事件
                if (input) {
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }
        } catch (error) {
            console.warn('⚠️ Turnstile 处理出错:', error);
        }
    }
    
    // 页面加载完成后开始监听
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', waitForTurnstile);
    } else {
        waitForTurnstile();
    }
    
    // 监听动态加载的 Turnstile
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.type === 'childList') {
                const turnstileElements = document.querySelectorAll('[data-sitekey], .cf-turnstile');
                if (turnstileElements.length > 0 && typeof turnstile !== 'undefined') {
                    console.log('🔄 检测到动态加载的 Turnstile');
                    setTimeout(handleTurnstile, 1000);
                }
            }
        });
    });
    
    observer.observe(document.body, {
        childList: true,
        subtree: true
    });
})();
"""
    
    with open(os.path.join(extension_dir, "script.js"), "w") as f:
        f.write(script_content)
    
    print(f"✅ Turnstile Patch 扩展创建完成: {extension_dir}")

if __name__ == "__main__":
    create_turnstile_patch()
