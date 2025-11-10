// 拦截 Cloudflare 验证相关请求
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
);
