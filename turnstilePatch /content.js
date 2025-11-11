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
        if (arguments[0] === 'image/png') {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = this.width;
            canvas.height = this.height;
            ctx.drawImage(this, 0, 0);
            
            // 添加微小扰动
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const data = imageData.data;
            for (let i = 0; i < data.length; i += 4) {
                if (Math.random() < 0.01) { // 随机修改1%的像素
                    data[i] = data[i] + Math.floor(Math.random() * 2 - 1);
                    data[i+1] = data[i+1] + Math.floor(Math.random() * 2 - 1);
                    data[i+2] = data[i+2] + Math.floor(Math.random() * 2 - 1);
                }
            }
            ctx.putImageData(imageData, 0, 0);
            return canvas.toDataURL(arguments[0], arguments[1]);
        }
        return result;
    };
    
    // 模拟鼠标移动
    let lastTime = Date.now();
    let mouseX = 0;
    let mouseY = 0;
    
    document.addEventListener('mousemove', function(e) {
        const now = Date.now();
        const deltaTime = now - lastTime;
        
        // 计算速度
        const velocityX = Math.abs(e.clientX - mouseX) / deltaTime;
        const velocityY = Math.abs(e.clientY - mouseY) / deltaTime;
        
        // 限制不自然的快速移动
        if (velocityX > 0.5 || velocityY > 0.5) {
            e.stopPropagation();
        }
        
        mouseX = e.clientX;
        mouseY = e.clientY;
        lastTime = now;
    }, true);
    
    // 移除特定的检测脚本
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach((node) => {
                    if (node.tagName === 'SCRIPT' && node.src) {
                        if (node.src.includes('challenges.cloudflare.com') || 
                            node.src.includes('turnstile')) {
                            node.setAttribute('data-patched', 'true');
                        }
                    }
                });
            }
        });
    });
    
    observer.observe(document.documentElement, {
        childList: true,
        subtree: true
    });
    
    console.log('[TurnstilePatch] 注入成功 - 反检测已激活');
})();
