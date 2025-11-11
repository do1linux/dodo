// Service Worker脚本
chrome.webRequest.onBeforeRequest.addListener(
    function(details) {
        const url = details.url;
        
        // 记录请求日志
        console.log('[TurnstilePatch] 拦截请求:', url);
        
        // 拦截已知检测脚本
        if (url.includes('challenges.cloudflare.com') || url.includes('turnstile')) {
            console.log('[TurnstilePatch] 允许Turnstile请求');
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

// 注入脚本到所有页面
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === 'loading' && tab.url) {
        chrome.scripting.executeScript({
            target: { tabId: tabId },
            files: ['content.js'],
            injectImmediately: true
        }).catch(() => {});
    }
});

console.log('[TurnstilePatch] Service Worker 已加载');
