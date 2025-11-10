// 消除自动化特征
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
});
