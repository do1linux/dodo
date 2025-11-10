#!/usr/bin/env python3
"""
创建必要的目录结构
"""

import os

def setup_directories():
    """设置目录结构"""
    # 创建 turnstilePatch 扩展目录
    turnstile_dir = "turnstilePatch"
    os.makedirs(turnstile_dir, exist_ok=True)
    
    # 创建 manifest.json
    manifest_content = {
        "manifest_version": 3,
        "name": "Turnstile Patch",
        "version": "1.0",
        "content_scripts": [{
            "matches": ["<all_urls>"],
            "js": ["script.js"],
            "run_at": "document_start"
        }]
    }
    
    with open(os.path.join(turnstile_dir, "manifest.json"), "w") as f:
        import json
        json.dump(manifest_content, f, indent=2)
    
    # 创建 script.js
    script_content = """
// Turnstile Patch - 辅助Cloudflare验证
console.log('Turnstile Patch loaded');
"""
    
    with open(os.path.join(turnstile_dir, "script.js"), "w") as f:
        f.write(script_content)
    
    print("✅ Turnstile Patch 扩展创建完成")

if __name__ == "__main__":
    setup_directories()
