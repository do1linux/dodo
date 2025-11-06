import os
import json

def create_turnstile_patch():
    """创建turnstilePatch扩展目录和文件"""
    turnstile_dir = "turnstilePatch"
    
    # 创建目录
    if not os.path.exists(turnstile_dir):
        os.makedirs(turnstile_dir)
        print(f"✅ 创建目录: {turnstile_dir}")
    
    # 创建manifest.json
    manifest_content = {
        "manifest_version": 3,
        "name": "Turnstile Patch",
        "version": "1.0",
        "description": "Patch for Cloudflare Turnstile challenges",
        "permissions": [],
        "content_scripts": [
            {
                "matches": ["<all_urls>"],
                "js": ["script.js"],
                "run_at": "document_start"
            }
        ]
    }
    
    with open(os.path.join(turnstile_dir, "manifest.json"), "w", encoding='utf-8') as f:
        json.dump(manifest_content, f, indent=2)
    print("✅ 创建 manifest.json")
    
    # 创建script.js
    script_content = """// Turnstile Patch - Cloudflare Challenge Bypass
(function() {
    'use strict';
    
    // Patch turnstile if it exists
    if (window.turnstile) {
        const originalRender = window.turnstile.render;
        const originalReset = window.turnstile.reset;
        
        window.turnstile.render = function(element, options) {
            console.log('Turnstile render intercepted');
            if (options && typeof options.callback === 'function') {
                // Simulate successful challenge
                setTimeout(() => {
                    options.callback('fake_turnstile_token_' + Date.now());
                }, 1000);
            }
            return 'fake_widget_id';
        };
        
        window.turnstile.reset = function(widgetId) {
            console.log('Turnstile reset intercepted');
            return true;
        };
        
        window.turnstile.getResponse = function(widgetId) {
            return 'fake_turnstile_token_' + Date.now();
        };
    }
    
    // Intercept Cloudflare challenges
    const originalFetch = window.fetch;
    window.fetch = function(...args) {
        const url = args[0];
        if (typeof url === 'string' && url.includes('challenges')) {
            console.log('Cloudflare challenge intercepted');
            return Promise.resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve({success: true}),
                text: () => Promise.resolve('{"success": true}')
            });
        }
        return originalFetch.apply(this, args);
    };
    
    // Remove Cloudflare protection attributes
    document.addEventListener('DOMContentLoaded', function() {
        const elements = document.querySelectorAll('*');
        elements.forEach(el => {
            if (el.hasAttribute('data-cf-chl-completed')) {
                el.setAttribute('data-cf-chl-completed', 'true');
            }
            if (el.hasAttribute('data-cf-modified')) {
                el.setAttribute('data-cf-modified', 'true');
            }
        });
    });
    
    console.log('Turnstile Patch loaded successfully');
})();"""
    
    with open(os.path.join(turnstile_dir, "script.js"), "w", encoding='utf-8') as f:
        f.write(script_content)
    print("✅ 创建 script.js")
    
    print("🎉 turnstilePatch扩展创建完成!")

if __name__ == "__main__":
    create_turnstile_patch()
