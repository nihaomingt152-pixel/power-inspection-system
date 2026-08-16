/**
 * common.js - 全局公共函数。
 * 所有页面均引用此文件，提供统一的 fetch 封装、日志和 UI 工具。
 */

console.log('[common.js] 已加载');

// ===== 诊断：检测点击拦截（捕获阶段，在任何 preventDefault/stopPropagation 之前触发）=====
(function() {
    let clickCount = 0;
    document.addEventListener('mousedown', function(e) {
        clickCount++;
        const target = e.target;
        const tag = target.tagName ? target.tagName.toLowerCase() : '?';
        const id = target.id ? '#' + target.id : '';
        const cls = target.className && typeof target.className === 'string'
            ? '.' + target.className.split(' ').slice(0, 3).join('.') : '';
        const rect = target.getBoundingClientRect ? target.getBoundingClientRect() : null;
        const pos = rect ? `(${Math.round(rect.left)},${Math.round(rect.top)} ${Math.round(rect.width)}x${Math.round(rect.height)})` : '';
        console.log(`[CLICK-DIAG] #${clickCount} 目标=<${tag}${id}${cls}> 位置=${pos} 坐标=(${e.clientX},${e.clientY})`);
        // 检查目标及其祖先是否有 pointer-events:none
        let el = target;
        while (el && el !== document.documentElement) {
            const style = window.getComputedStyle(el);
            if (style.pointerEvents === 'none') {
                console.warn(`[CLICK-DIAG] ⚠️ 发现 pointer-events:none 在元素上:`, el);
            }
            el = el.parentElement;
        }
    }, true); // true = 捕获阶段，最先收到事件
    console.log('[common.js] 点击诊断已激活（捕获阶段 mousedown 监听器）');
})();

// ===== 日志 =====
function log(module, msg, data) {
    const ts = new Date().toLocaleTimeString();
    if (data !== undefined) {
        console.log(`[${ts}][${module}] ${msg}`, data);
    } else {
        console.log(`[${ts}][${module}] ${msg}`);
    }
}
function warn(module, msg, data) {
    console.warn(`[${module}] ${msg}`, data || '');
}
function errLog(module, msg, data) {
    console.error(`[${module}] ${msg}`, data || '');
}

// ===== 设备判断（移动端优化用）=====
// 屏幕宽度 < 768 视为手机（与 Bootstrap 的 md 断点一致）。
// 移动端启用图片压缩、精简返回、降低轮询频率等优化，电脑端行为完全不变。
function isMobile() {
    return window.innerWidth < 768;
}

// ===== API 请求封装 =====
async function apiGet(url) {
    log('api', `GET ${url}`);
    const res = await fetch(url);
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.substring(0, 200)}`);
    }
    return res.json();
}

async function apiPost(url, body) {
    log('api', `POST ${url}`);
    const res = await fetch(url, { method: 'POST', body });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.substring(0, 200)}`);
    }
    return res.json();
}

async function apiPostJson(url, data) {
    log('api', `POST-JSON ${url}`);
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const err = await res.json();
            if (err.detail) detail = err.detail;
        } catch (e) {}
        throw new Error(detail);
    }
    return res.json();
}

async function apiPutJson(url, data) {
    log('api', `PUT-JSON ${url}`);
    const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.substring(0, 200)}`);
    }
    return res.json();
}

/**
 * 将存储的图片路径转换为 /api/preview/ 可访问的 URL（Phase 29 修复）。
 * 兼容三种格式：
 *   - 相对无前导斜杠：static/uploads/annotated/x.jpg
 *   - URL 风格：/static/uploads/annotated/x.jpg
 *   - Windows 绝对路径：E:\WorkSpace\...\static\uploads\annotated\x.jpg
 * 旧代码用 split('/static/uploads/') 对无前导斜杠的路径匹配失败，导致 URL 多出 static/uploads/ 前缀而 404。
 */
function buildPreviewUrl(path) {
    if (!path) return '';
    const p = String(path).replace(/\\/g, '/');
    const m = p.match(/(?:^|\/)static\/uploads\/(.+)$/);
    const rel = m ? m[1] : p.split('/').pop();
    return '/api/preview/' + rel;
}

// ===== DOM 工具 =====
function $(id) { return document.getElementById(id); }
function show(el) { if (typeof el === 'string') el = $(el); if (el) el.classList.remove('d-none'); }
function hide(el) { if (typeof el === 'string') el = $(el); if (el) el.classList.add('d-none'); }
function toggleDNone(el) { if (typeof el === 'string') el = $(el); if (el) el.classList.toggle('d-none'); }

// ===== 安全初始化包装器 =====
function safeInit(moduleName, initFn) {
    try {
        log(moduleName, '开始初始化...');
        initFn();
        log(moduleName, '初始化完成');
    } catch (e) {
        errLog(moduleName, '初始化失败', e);
        console.error(e.stack);
    }
}

// ===== 导航菜单动态渲染 =====
function renderNavMenu(user, currentPage) {
    /**
     * 根据用户角色动态渲染导航菜单。
     * @param {object|null} user - 当前用户对象（含 role 字段）
     * @param {string} currentPage - 当前页面标识: 'index'|'dashboard'|'map'|'orders'
     */
    const container = document.getElementById('nav-menu');
    if (!container) return;

    const isActive = (page) => currentPage === page ? ' active' : '';

    let links = '';
    if (user && user.role === 'repairman') {
        // Worker: 仅显示仪表盘、地图、工单管理
        links = `
            <li class="nav-item"><a class="nav-link${isActive('dashboard')}" href="/dashboard">📊 仪表盘</a></li>
            <li class="nav-item"><a class="nav-link${isActive('map')}" href="/map">🗺️ 地图总览</a></li>
            <li class="nav-item"><a class="nav-link${isActive('orders')}" href="/orders">📋 工单管理</a></li>`;
    } else {
        // Admin/Inspector 或未登录：显示全部
        links = `
            <li class="nav-item"><a class="nav-link${isActive('index')}" href="/">检测中心</a></li>
            <li class="nav-item"><a class="nav-link${isActive('dashboard')}" href="/dashboard">📊 仪表盘</a></li>
            <li class="nav-item"><a class="nav-link${isActive('map')}" href="/map">🗺️ 地图总览</a></li>
            <li class="nav-item"><a class="nav-link${isActive('orders')}" href="/orders">📋 工单管理</a></li>`;
    }
    container.innerHTML = links;
}

// ===== Modal 管理器（统一打开/关闭，防止遮罩残留）=====
const ModalManager = {
    _instances: {},

    open(modalId) {
        const el = document.getElementById(modalId);
        if (!el) { warn('modal', `元素不存在: ${modalId}`); return null; }
        // 清理旧实例
        if (this._instances[modalId]) {
            try { this._instances[modalId].dispose(); } catch(e) {}
            delete this._instances[modalId];
        }
        // 清理残留遮罩
        this._cleanBackdrops();
        const instance = new bootstrap.Modal(el);
        this._instances[modalId] = instance;
        instance.show();
        return instance;
    },

    close(modalId) {
        if (this._instances[modalId]) {
            try { this._instances[modalId].hide(); } catch(e) {}
            delete this._instances[modalId];
        }
        // 延迟清理（等待 hide 动画完成）
        setTimeout(() => this._cleanBackdrops(), 300);
    },

    _cleanBackdrops() {
        try {
            document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            document.body.classList.remove('modal-open');
            document.body.style.overflow = '';
            document.body.style.paddingRight = '';
        } catch(e) {}
    }
};

// 全局事件：任何 Modal 完全隐藏后自动清理残留遮罩
document.addEventListener('hidden.bs.modal', function() {
    ModalManager._cleanBackdrops();
});
// DOM 就绪时也清理一次（防止刷新前残留）
document.addEventListener('DOMContentLoaded', function() {
    ModalManager._cleanBackdrops();
});

// ===== 手动刷新按钮动画（Phase 7.2）=====
// 手动刷新时显示旋转图标 + "刷新中..."，2 秒后自动恢复原样
function showRefreshSpinner(btn) {
    if (!btn) return;
    const original = btn.innerHTML;
    const spinner = '<span class="refresh-spinner">🔄</span>';
    btn.disabled = true;
    btn.classList.add('btn-refreshing');
    btn.innerHTML = `${spinner} 刷新中...`;
    setTimeout(() => {
        btn.disabled = false;
        btn.classList.remove('btn-refreshing');
        btn.innerHTML = original;
    }, 2000);
}

// ===== 跨页面数据变更通知（批量删除后联动仪表盘刷新）=====
let _dataChannel = null;
const DATA_CHANGE_KEY = 'power_data_changed';

// 优先使用 BroadcastChannel（同源多标签页实时通知），低版本浏览器回退 localStorage 事件
function notifyDataChanged() {
    try {
        document.dispatchEvent(new CustomEvent('power:dataChanged'));
        if (window.BroadcastChannel) {
            if (!_dataChannel) _dataChannel = new BroadcastChannel('power-data-changed');
            _dataChannel.postMessage(Date.now());
        } else {
            localStorage.setItem(DATA_CHANGE_KEY, String(Date.now()));
        }
    } catch (e) {
        warn('common', '数据变更通知失败', e);
    }
}

function onDataChanged(cb) {
    if (typeof cb !== 'function') return;
    const handler = () => {
        try { cb(); } catch (e) { errLog('common', '数据变更回调失败', e); }
    };
    document.addEventListener('power:dataChanged', handler);
    if (window.BroadcastChannel) {
        if (!_dataChannel) _dataChannel = new BroadcastChannel('power-data-changed');
        _dataChannel.addEventListener('message', handler);
    } else {
        window.addEventListener('storage', e => {
            if (e.key === DATA_CHANGE_KEY) handler();
        });
    }
}

// ===== 通知 =====
function notify(msg, type) {
    type = type || 'info';
    log('notify', msg);
    try {
        const container = document.getElementById('alert-toast');
        if (!container) return;
        const div = document.createElement('div');
        const colors = { critical: '#e53935', warning: '#ff9800', info: '#1a73e8', success: '#4caf50' };
        div.style.cssText = `padding:12px 20px;border-radius:8px;color:#fff;font-weight:600;margin-bottom:6px;box-shadow:0 2px 8px rgba(0,0,0,0.2);background:${colors[type]||colors.info};`;
        div.textContent = msg;
        container.appendChild(div);
        setTimeout(() => div.remove(), 5000);
    } catch (e) {}
}
