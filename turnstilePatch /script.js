// Turnstile Patch - Cloudflare Challenge Bypass
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
})();
