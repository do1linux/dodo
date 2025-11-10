#!/usr/bin/env python3
"""
创建必要的目录结构
用于 GitHub Actions 工作流
"""

import os
import json

def setup_directories():
    """设置目录结构"""
    print("🔧 开始创建必要的目录结构...")
    
    # 创建 turnstilePatch 扩展目录
    turnstile_dir = "turnstilePatch"
    try:
        os.makedirs(turnstile_dir, exist_ok=True)
        print(f"✅ 创建目录: {turnstile_dir}")
    except Exception as e:
        print(f"❌ 创建目录失败 {turnstile_dir}: {e}")
        return False
    
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
    
    try:
        with open(os.path.join(turnstile_dir, "manifest.json"), "w") as f:
            json.dump(manifest_content, f, indent=2)
        print("✅ 创建 manifest.json")
    except Exception as e:
        print(f"❌ 创建 manifest.json 失败: {e}")
        return False
    
    # 创建 script.js
    script_content = """// Turnstile Patch - 辅助Cloudflare验证
console.log('Turnstile Patch loaded');

// 模拟用户行为，帮助通过Cloudflare验证
if (window._cf_chl_opt) {
    console.log('Cloudflare challenge detected, applying patches...');
}

// 添加一些常见的反检测措施
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// 覆盖chrome runtime
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};
"""
    
    try:
        with open(os.path.join(turnstile_dir, "script.js"), "w") as f:
            f.write(script_content)
        print("✅ 创建 script.js")
    except Exception as e:
        print(f"❌ 创建 script.js 失败: {e}")
        return False
    
    print("✅ Turnstile Patch 扩展创建完成")
    return True

if __name__ == "__main__":
    success = setup_directories()
    if success:
        print("🎉 所有目录和文件创建成功")
    else:
        print("💥 创建过程中出现错误")
        exit(1)
