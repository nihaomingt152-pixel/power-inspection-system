/**
 * 认证模块 v2（修复版）：登录/注册/登出/用户状态。
 * 修复: 确认密码验证、密码长度校验、友好错误提示。
 */

function showRegister() {
    document.getElementById('login-card').classList.add('d-none');
    document.getElementById('register-card').classList.remove('d-none');
    // 清空注册表单
    ['reg-username', 'reg-password', 'reg-password-confirm', 'reg-fullname'].forEach(id => {
        document.getElementById(id).value = '';
    });
    hideErr('reg-error');
}
function showLogin() {
    document.getElementById('register-card').classList.add('d-none');
    document.getElementById('login-card').classList.remove('d-none');
    hideErr('login-error');
}

async function doLogin() {
    const u = document.getElementById('login-username').value.trim();
    const p = document.getElementById('login-password').value;
    if (!u || !p) return showErr('login-error', '请输入用户名和密码');

    const fd = new FormData();
    fd.append('username', u);
    fd.append('password', p);

    try {
        const res = await fetch('/api/auth/login', { method: 'POST', body: fd });
        const d = await res.json();
        if (d.success) {
            // 角色路由：Admin 进入检测中心，Worker 进入仪表盘
            const role = d.data?.role;
            if (role === 'repairman') {
                window.location.href = '/dashboard';
            } else {
                window.location.href = '/';
            }
        } else {
            showErr('login-error', d.detail || '用户名或密码错误，请重试');
        }
    } catch (e) {
        showErr('login-error', '网络连接失败，请检查服务器是否启动');
    }
}

async function doRegister() {
    const u = document.getElementById('reg-username').value.trim();
    const p = document.getElementById('reg-password').value;
    const pc = document.getElementById('reg-password-confirm').value;
    const n = document.getElementById('reg-fullname').value.trim();
    const r = document.getElementById('reg-role').value;

    // 前端验证
    if (!u) return showErr('reg-error', '请输入用户名');
    if (u.length < 2) return showErr('reg-error', '用户名至少需要 2 个字符');
    if (!p) return showErr('reg-error', '请输入密码');
    if (p.length < 4) return showErr('reg-error', '密码至少需要 4 个字符');
    if (p.length > 72) return showErr('reg-error', '密码不能超过 72 个字符');
    if (p !== pc) return showErr('reg-error', '两次输入的密码不一致，请重新输入');
    if (!r) return showErr('reg-error', '请选择角色');

    const fd = new FormData();
    fd.append('username', u);
    fd.append('password', p);
    fd.append('role', r);
    fd.append('full_name', n || u);

    try {
        const res = await fetch('/api/auth/register', { method: 'POST', body: fd });
        const d = await res.json();
        if (d.success) {
            alert('注册成功！即将跳转到登录页。');
            // 自动填入用户名方便登录
            document.getElementById('login-username').value = u;
            showLogin();
        } else {
            showErr('reg-error', d.detail || '注册失败，请稍后重试');
        }
    } catch (e) {
        showErr('reg-error', '网络连接失败，请检查服务器是否启动');
    }
}

function showErr(id, msg) {
    const el = document.getElementById(id);
    el.textContent = '❌ ' + msg;
    el.classList.remove('d-none');
}
function hideErr(id) {
    const el = document.getElementById(id);
    el.classList.add('d-none');
}

async function logout() {
    await fetch('/api/auth/logout');
    window.location.href = '/login';
}

async function getCurrentUser() {
    try {
        const res = await fetch('/api/auth/me');
        const d = await res.json();
        return d.success ? d.data : null;
    } catch {
        return null;
    }
}
