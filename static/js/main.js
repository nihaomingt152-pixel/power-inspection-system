/**
 * main.js - 电力巡检系统 v3.0 主交互逻辑（修复版）。
 * 关键修复: try/catch 包裹整个初始化流程，确保 Tab/上传事件不因 auth 失败而丢失。
 */
console.log('[main.js] 已加载');

// ===== 全局状态 =====
let currentUser = null;
let voiceEnabled = true;
let currentResultData = null;
let cameraStream = null, cameraTimer = null;
let videoPollTimer = null, currentVideoTaskId = null;
let historyPage = 1;

// Phase 7.2: 历史记录自动刷新（15秒，静默更新）
let historyRefreshTimer = null;
let lastHistorySignature = null;   // 最近一次列表数据签名（用于判断是否有新增记录）
const HISTORY_REFRESH_INTERVAL = 15000;
let selectedHistoryIds = new Set();      // 勾选的历史记录（key: record_type:id）
let currentHistoryPageRecords = [];      // 当前页记录，用于全选/取消全选
let currentDispatchRecordId = null;  // 当前正在派发的检测记录 ID
let currentDispatchImagePath = null; // 当前派发工单的缺陷图路径（视频帧派发时传递给后端）
let currentDispatchAiSummary = null; // 当前派发工单的 AI 摘要（图片=ai_analysis，视频=video_summary）
let currentGpsRecordId = null;       // 当前正在设置 GPS 的检测记录 ID
let currentVideoRecord = null;       // 当前查看的视频检测记录
let currentFrameIndex = 0;           // 当前查看的帧索引

// ===== 初始化（加固版）=====
document.addEventListener('DOMContentLoaded', async () => {
    log('main', 'DOMContentLoaded 触发，开始初始化...');

    // Step 0: 先绑定纯 UI 事件（不依赖后端）
    try {
        initTabs();
        log('main', 'Tab 切换已绑定');
    } catch (e) { errLog('main', 'Tab 初始化失败', e); }

    try {
        initDropzone();
        log('main', '拖拽上传已绑定');
    } catch (e) { errLog('main', 'Dropzone 初始化失败', e); }

    try {
        initVideoFileSelect();
        log('main', '视频选择反馈已绑定');
    } catch (e) { errLog('main', '视频选择反馈绑定失败', e); }

    // Step 1: 认证检查
    try {
        log('main', '检查登录状态...');
        currentUser = await getCurrentUser();
        if (!currentUser) {
            warn('main', '未登录，跳转登录页');
            window.location.href = '/login';
            return;
        }
        log('main', `当前用户: ${currentUser.username} (${currentUser.role})`);
    } catch (e) {
        errLog('main', '认证检查异常（可能未登录）', e);
        // 不强制跳转，让用户仍然可以浏览基本 UI
        notify('认证服务暂不可用，部分功能受限', 'warning');
    }

    // Step 2: 更新导航栏
    try {
        updateNavUser();
        renderNavMenu(currentUser, 'index');
        log('main', '导航栏已更新');
    } catch (e) { errLog('main', '导航栏更新失败', e); }

    // Step 3: 后台任务
    try { checkStatus(); } catch (e) { errLog('main', '状态检查失败', e); }
    try { loadHistory(); } catch (e) { errLog('main', '历史加载失败', e); }
    try { pollGpuStatus(); } catch (e) { errLog('main', 'GPU 轮询失败', e); }

    // 定时任务
    setInterval(() => { try { pollGpuStatus(); } catch (e) {} }, 5000);

    // Phase 7.2: 历史记录自动刷新（后台时停止，切回前台立即刷新一次）
    startHistoryAutoRefresh();
    document.addEventListener('visibilitychange', handleHistoryVisibilityChange);

    log('main', '全部初始化完成');
});

// ===== Phase 7.2: 历史记录自动刷新 =====

function startHistoryAutoRefresh() {
    if (historyRefreshTimer) return;
    historyRefreshTimer = setInterval(() => {
        // 冲突处理：任何弹窗打开时暂停刷新（防止干扰用户正在编辑的表单）
        if (document.querySelector('.modal.show')) return;
        try { loadHistory(historyPage, true); } catch (e) { errLog('main', '历史自动刷新失败', e); }
    }, HISTORY_REFRESH_INTERVAL);
}

function stopHistoryAutoRefresh() {
    if (historyRefreshTimer) {
        clearInterval(historyRefreshTimer);
        historyRefreshTimer = null;
    }
}

// 页面切到后台停止刷新，切回前台立即刷新一次并恢复定时器（节省资源）
function handleHistoryVisibilityChange() {
    if (document.hidden) {
        stopHistoryAutoRefresh();
    } else {
        startHistoryAutoRefresh();
        try { loadHistory(historyPage, true); } catch (e) { errLog('main', '恢复刷新失败', e); }
    }
}

// 手动刷新按钮：显示旋转动画 + 立即刷新（带 spinner）
function manualRefreshHistory(btn) {
    showRefreshSpinner(btn);
    loadHistory(historyPage);
}

// ===== 导航栏 =====
function updateNavUser() {
    const el = document.getElementById('nav-user');
    if (!el) return;
    el.textContent = currentUser
        ? `👤 ${currentUser.full_name || currentUser.username} (${currentUser.role})`
        : '未登录';
}

// ===== Tab 切换 =====
function initTabs() {
    const tabBtns = document.querySelectorAll('#main-tabs .nav-link');
    log('main', `找到 ${tabBtns.length} 个 Tab 按钮`);
    if (tabBtns.length === 0) {
        warn('main', '未找到 Tab 按钮，检查 #main-tabs .nav-link 选择器');
        return;
    }
    tabBtns.forEach(btn => {
        btn.addEventListener('click', function(e) {
            log('main', `Tab 点击: ${this.dataset.tab}`);
            // 移除所有 active
            document.querySelectorAll('#main-tabs .nav-link').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            // 隐藏所有面板
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            // 显示目标面板
            const target = document.getElementById(this.dataset.tab);
            if (target) {
                target.classList.add('active');
                log('main', `切换到面板: ${this.dataset.tab}`);
            } else {
                errLog('main', `未找到面板: ${this.dataset.tab}`);
            }
        });
    });
}

// ===== 拖拽上传 =====
function initDropzone() {
    const z = document.getElementById('upload-dropzone');
    if (!z) { warn('main', '未找到 upload-dropzone'); return; }
    const inp = document.getElementById('file-images');
    if (!inp) { warn('main', '未找到 file-images'); return; }

    log('main', '绑定拖拽上传事件');
    z.addEventListener('dragover', e => { e.preventDefault(); z.style.borderColor = '#1a73e8'; });
    z.addEventListener('dragleave', () => { z.style.borderColor = '#e0e0e0'; });
    z.addEventListener('drop', e => {
        e.preventDefault(); z.style.borderColor = '#e0e0e0';
        if (e.dataTransfer.files.length) {
            inp.files = e.dataTransfer.files;
            z.querySelector('.upload-placeholder').innerHTML =
                `<span class="upload-icon">✅</span><p>已选 ${inp.files.length} 个文件</p>`;
            log('main', `拖入 ${inp.files.length} 个文件`);
        }
    });
    inp.addEventListener('change', () => {
        if (inp.files.length) {
            z.querySelector('.upload-placeholder').innerHTML =
                `<span class="upload-icon">✅</span><p>已选 ${inp.files.length} 个文件</p>`;
        }
    });
    log('main', '拖拽上传事件绑定完成');
}

// ===== 视频文件选择反馈（Phase 7.2 UI 优化）=====
function initVideoFileSelect() {
    const inp = document.getElementById('file-video');
    const display = document.getElementById('video-file-name');
    if (!inp || !display) { warn('main', '未找到视频选择元素'); return; }

    // 更新文件名显示
    function updateDisplay() {
        const file = inp.files[0];
        if (file) {
            const sizeMB = (file.size / 1024 / 1024).toFixed(2);
            display.textContent = `✅ 已选择: ${file.name} (${sizeMB} MB)`;
            display.classList.remove('text-muted');
            display.classList.add('text-success');
        } else {
            display.textContent = '未选择文件';
            display.classList.remove('text-success');
            display.classList.add('text-muted');
        }
    }

    inp.addEventListener('change', updateDisplay);

    // 上传区域点击也触发文件选择
    const area = document.getElementById('video-upload-area');
    if (area) {
        area.addEventListener('click', () => { if (!inp.files[0]) inp.click(); });
    }
    log('main', '视频文件选择反馈已绑定');
}

// ===== GPU 轮询 =====
async function pollGpuStatus() {
    try {
        const d = await apiGet('/api/system/status');
        if (d.success && d.data.gpu && d.data.gpu.available) {
            const g = d.data.gpu;
            const badge = document.getElementById('gpu-badge');
            if (!badge) return;
            badge.textContent = `GPU: ${g.utilization_pct}% | ${g.memory_used_gb}/${g.memory_total_gb}GB | ${g.temperature_c}°C`;
            badge.className = g.temperature_c > 80 ? 'badge bg-danger'
                : g.temperature_c > 65 ? 'badge bg-warning' : 'badge bg-success';
        }
    } catch (e) { /* 静默 */ }
}

// ===== 语音 =====
function toggleVoice() {
    voiceEnabled = !voiceEnabled;
    const btn = document.getElementById('btn-voice');
    if (btn) btn.textContent = voiceEnabled ? '🔊' : '🔇';
    log('main', `语音播报: ${voiceEnabled ? '开' : '关'}`);
}
function speakIfNeeded(resultData) {
    if (!voiceEnabled) return;
    try {
        const ai = resultData.ai_analysis || {};
        if (ai.severity === '严重' || ai.severity === '紧急') {
            const u = new SpeechSynthesisUtterance(
                `警告！检测到${ai.severity}缺陷：${(ai.description || '').substring(0, 80)}`);
            u.lang = 'zh-CN'; u.rate = 1.0;
            window.speechSynthesis.speak(u);
            log('main', '语音播报: 严重/紧急缺陷');
        }
        if (resultData.has_abnormal) {
            const u2 = new SpeechSynthesisUtterance(
                `注意，系统发现潜在异物：${(resultData.abnormal_desc || '').substring(0, 100)}`);
            u2.lang = 'zh-CN'; window.speechSynthesis.speak(u2);
        }
    } catch (e) { errLog('main', '语音播报失败', e); }
}

// ===== Canvas 流光框 =====
function drawGlowBoxes(canvasId, originalSrc, detections) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !detections) return;
    const ctx = canvas.getContext('2d');
    const img = new Image();
    img.onload = () => {
        canvas.width = img.width; canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
        detections.forEach(d => {
            const b = d.bbox, conf = d.confidence;
            const r = conf > 0.8 ? 0 : conf > 0.5 ? 255 : 255;
            const g = conf > 0.8 ? 255 : conf > 0.5 ? 165 : 50;
            const bCol = conf > 0.8 ? 50 : 0;
            ctx.save(); ctx.shadowColor = `rgba(${r},${g},${bCol},0.8)`; ctx.shadowBlur = 10;
            ctx.strokeStyle = `rgb(${r},${g},${bCol})`; ctx.lineWidth = 3;
            ctx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
            ctx.restore();
            ctx.fillStyle = `rgb(${r},${g},${bCol})`; ctx.font = 'bold 14px sans-serif';
            ctx.shadowColor = 'cyan'; ctx.shadowBlur = 4;
            ctx.fillText(`${d.class_name} ${(conf * 100).toFixed(1)}%`, b.x1, b.y1 - 6);
            ctx.shadowBlur = 0;
        });
    };
    img.src = originalSrc;
}

// ===== 图片上传 =====
// ===== 移动端图片压缩（策略1：减轻公网传输负担）=====
// 仅移动端启用：canvas 绘制缩放（长边 1024px）+ JPEG 质量 0.7。
// 压缩失败或非图片时回退原文件，保证功能不中断。
async function compressImage(file) {
    if (!isMobile() || !file.type.startsWith('image/')) return file;
    return new Promise((resolve) => {
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => {
            URL.revokeObjectURL(url);
            const MAX = 1024;
            let { width, height } = img;
            if (width <= MAX && height <= MAX) { resolve(file); return; }
            const scale = Math.min(1, MAX / Math.max(width, height));
            width = Math.round(width * scale);
            height = Math.round(height * scale);
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            canvas.getContext('2d').drawImage(img, 0, 0, width, height);
            canvas.toBlob((blob) => {
                if (!blob) { resolve(file); return; }
                resolve(new File([blob], file.name.replace(/\.\w+$/, '.jpg'), { type: 'image/jpeg' }));
            }, 'image/jpeg', 0.7);
        };
        img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
        img.src = url;
    });
}

async function uploadImages() {
    log('main', 'uploadImages 被调用');
    const inp = document.getElementById('file-images');
    const files = inp.files;
    if (!files || !files.length) { alert('请先选择图片文件'); return; }
    log('main', `准备上传 ${files.length} 个文件`);

    const callAi = document.getElementById('chk-ai-image')?.checked ?? true;
    const callFallback = document.getElementById('chk-fallback')?.checked ?? true;
    const progress = document.getElementById('image-progress');
    if (progress) progress.classList.remove('d-none');
    const grid = document.getElementById('batch-grid');
    if (grid) grid.innerHTML = '';

    // 移动端：压缩图片（公网链路传输大图极慢）+ 请求精简返回
    const prepared = [];
    for (const f of files) prepared.push(await compressImage(f));
    const isMob = isMobile();

    if (files.length === 1) {
        const fd = new FormData(); fd.append('file', prepared[0]);
        fd.append('call_ai', callAi); fd.append('call_fallback', callFallback);
        if (isMob) fd.append('mobile', 'true');
        try {
            const d = await apiPost('/api/upload/image', fd);
            if (progress) progress.classList.add('d-none');
            if (d.success) {
                currentResultData = d.data;
                showDetail(d.data, files[0].name);
                const detailCard = document.getElementById('detail-card');
                if (detailCard) { detailCard.classList.remove('d-none'); detailCard.scrollIntoView({ behavior: 'smooth' }); }
                if (d.data.has_abnormal) showFallbackAlert(d.data.abnormal_desc);
                if (d.data.speech_alert) speakIfNeeded(d.data);
                if (d.data.alert?.triggered) showToast(d.data.alert);
                log('main', `单图检测完成: ${d.data.detections?.length || 0} 个目标`);
            } else {
                alert('检测失败: ' + (d.detail || '未知错误'));
            }
        } catch (e) {
            if (progress) progress.classList.add('d-none');
            errLog('main', '上传失败', e);
            alert('上传失败: ' + e.message);
        }
    } else {
        let done = 0;
        for (let i = 0; i < prepared.length; i++) {
            const f = prepared[i];
            const fd = new FormData(); fd.append('file', f);
            fd.append('call_ai', callAi); fd.append('call_fallback', callFallback);
            if (isMob) fd.append('mobile', 'true');
            try {
                const d = await apiPost('/api/upload/image', fd);
                if (d.success) {
                    addBatchCard(d.data, files[i].name);
                    if (d.data.speech_alert) speakIfNeeded(d.data);
                }
                done++;
            } catch (e) { errLog('main', `批量上传失败: ${files[i].name}`, e); }
        }
        if (progress) progress.classList.add('d-none');
        log('main', `批量检测完成: ${done}/${files.length}`);
        loadHistory();
    }
}

function addBatchCard(data, fname) {
    const grid = document.getElementById('batch-grid');
    if (!grid) return;
    const ai = data.ai_analysis || {};
    const sev = ai.severity || '未知';
    const imgSrc = buildPreviewUrl(data.annotated_image_path);
    const card = document.createElement('div');
    card.className = 'batch-card';
    card.onclick = () => { currentResultData = data; showDetail(data, fname); document.getElementById('detail-card')?.classList.remove('d-none'); };
    card.innerHTML = `<img class="batch-card-img" src="${imgSrc}" onerror="this.style.display='none'">
        <div class="batch-card-info"><h6>${fname.substring(0, 30)}</h6>
        <small>${data.detections ? data.detections.length : 0} 目标 | <span class="severity-tag severity-${sev}">${sev}</span> | ${data.timing ? data.timing.total_ms + 'ms' : ''}</small>
        ${data.has_abnormal ? '<br><span class="badge bg-warning">⚠ 异物</span>' : ''}</div>`;
    grid.appendChild(card);
}

function showDetail(data, fname) {
    const ai = data.ai_analysis || {};
    const sev = ai.severity || '未知';
    const fb = data.fallback_result || null;
    // 判断各引擎是否有实际结果（AI 关闭时 ai_analysis 为空，兜底关闭时 fallback_result 为 null）
    const hasAi = !!(data.ai_analysis && Object.keys(data.ai_analysis).length > 0);
    const hasFb = !!(fb && (fb.description || fb.is_abnormal));
    const imgSrc = buildPreviewUrl(data.annotated_image_path);
    const dc = document.getElementById('detail-content');
    if (!dc) return;

    // AI 分析报告区块（仅当 call_ai 开启且有结果时展示）
    let aiHtml = '';
    if (hasAi) {
        aiHtml = `<div class="result-block ai-report">
            <h6>🧠 AI 分析报告</h6>
            <p><strong>描述:</strong> ${ai.description || '无'}</p>
            <p><strong>严重程度:</strong> <span class="severity-tag severity-${ai.severity || '一般'}">${ai.severity || '一般'}</span></p>
            <p><strong>成因:</strong> ${ai.cause || '无'}</p>
            <p><strong>建议:</strong> ${ai.suggestion || '无'}</p>
        </div>`;
    }

    // 兜底异物检测区块（独立于 AI 分析，仅当 call_fallback 开启且有结果时展示）
    let fbHtml = '';
    if (hasFb) {
        const confMap = { high: '高', medium: '中', low: '低' };
        const confText = confMap[fb.confidence] || fb.confidence || '未知';
        fbHtml = `<div class="result-block fallback-report">
            <h6>🔍 兜底异物检测</h6>
            <p><strong>描述:</strong> ${fb.description || '无'}</p>
            <p><strong>是否异常:</strong> ${fb.is_abnormal ? '<span class="text-danger fw-bold">是</span>' : '否'}</p>
            <p><strong>置信度:</strong> ${confText}</p>
        </div>`;
    }

    dc.innerHTML = `
        <div class="col-md-6"><canvas id="glow-canvas" style="width:100%;max-height:450px;background:#1a1a2e;"></canvas></div>
        <div class="col-md-6">
            <h5>${fname}</h5>
            ${hasAi ? `<span class="severity-tag severity-${sev} mb-2">${sev}</span>` : ''}
            ${data.has_abnormal ? '<span class="badge bg-warning ms-1">⚠ 潜在异物</span>' : ''}
            ${aiHtml}
            ${fbHtml}
            <h6 class="mt-2">🤖 YOLO 检测:</h6>
            ${data.detections?.length ? '<ul>' + data.detections.map(d => `<li>${d.class_name} (${(d.confidence * 100).toFixed(1)}%)</li>`).join('') + '</ul>' : '<p class="text-muted">未检测到目标</p>'}
            <small class="text-muted">YOLO:${data.timing?.yolo_ms}ms + AI:${data.timing?.ai_ms || '-'}ms = ${data.timing?.total_ms}ms</small>
        </div>`;
    if (data.annotated_image_path) drawGlowBoxes('glow-canvas', imgSrc, data.detections);
    const ob = document.getElementById('btn-create-order');
    if (ob) {
        if (currentUser && currentUser.role === 'inspector') ob.classList.remove('d-none');
        else ob.classList.add('d-none');
    }
}

function showFallbackAlert(desc) {
    const el = document.getElementById('fallback-alert');
    if (!el) return;
    el.innerHTML = `⚠️ <strong>系统发现潜在异物：</strong>${desc}`;
    el.classList.remove('d-none');
    setTimeout(() => el.classList.add('d-none'), 10000);
}

// ===== 工单 =====
async function createWorkOrder() {
    log('main', 'createWorkOrder 被调用');
    if (!currentResultData) { alert('请先执行检测'); return; }
    const sel = document.getElementById('order-severity');
    const ai = currentResultData.ai_analysis || {};
    if (sel) sel.value = ai.severity || '一般';
    const ot = document.getElementById('order-title');
    if (ot) ot.value = `缺陷工单 - ${ai.severity || '一般'}`;
    const od = document.getElementById('order-desc');
    if (od) od.value = ai.description || '';
    try {
        const d = await apiGet('/api/repairmen');
        const s = document.getElementById('order-assignee');
        if (s && d.data) s.innerHTML = d.data.map(u => `<option value="${u.id}">${u.full_name || u.username}</option>`).join('');
    } catch (e) { errLog('main', '加载检修人失败', e); }
    ModalManager.open('order-modal');
}

async function submitWorkOrder() {
    const title = document.getElementById('order-title')?.value?.trim();
    const desc = document.getElementById('order-desc')?.value?.trim();
    const sev = document.getElementById('order-severity')?.value;
    const aid = document.getElementById('order-assignee')?.value;
    if (!title || !aid) { alert('请填写标题并选择检修人'); return; }
    const fd = new FormData();
    fd.append('title', title); fd.append('description', desc || '');
    fd.append('severity', sev); fd.append('assigned_to', aid);
    if (currentResultData?.annotated_image_path) fd.append('annotated_image_path', currentResultData.annotated_image_path);
    const exif = currentResultData?.exif || {};
    if (exif.latitude) { fd.append('gps_lat', exif.latitude); fd.append('gps_lng', exif.longitude); }
    try {
        const d = await apiPost('/api/orders', fd);
        if (d.success) {
            ModalManager.close('order-modal');
            alert(`工单 #${d.data.id} 已创建！`);
            log('main', `工单创建成功: #${d.data.id}`);
        } else alert(d.detail || '失败');
    } catch (e) { errLog('main', '提交工单失败', e); alert('错误: ' + e.message); }
}

// ===== 派发工单（从历史记录一键派发）=====

async function dispatchOrder(recordId) {
    log('main', `dispatchOrder: 记录 #${recordId}`);
    currentDispatchRecordId = recordId;

    try {
        // 加载检测记录详情
        const d = await apiGet(`/api/history/${recordId}`);
        if (!d.success) { alert('加载记录失败'); return; }
        const rec = d.data;
        const ai = rec.ai_analysis || {};

        // 填充图片（修复2.2：加载失败时降级占位，避免裂图）
        currentDispatchImagePath = rec.annotated_image_path || null;
        const imgEl = document.getElementById('dispatch-img');
        if (imgEl) {
            const imgPath = buildPreviewUrl(rec.annotated_image_path);
            imgEl.src = imgPath || '';
            imgEl.onerror = () => { imgEl.removeAttribute('src'); imgEl.alt = '⚠ 图片加载失败'; };
        }

        // 填充 AI 摘要（图片检测记录的 AI 分析结果）
        currentDispatchAiSummary = rec.ai_analysis || null;
        const summaryEl = document.getElementById('dispatch-ai-summary');
        if (summaryEl) {
            const dets = rec.yolo_detections || [];
            const detStr = dets.map(d => `${d.class_name}(${(d.confidence * 100).toFixed(0)}%)`).join(', ') || '无';
            summaryEl.innerHTML = `
                <dl class="detail-kv">
                    <div><dt>类别</dt><dd>${detStr}</dd></div>
                    <div><dt>严重程度</dt><dd><span class="severity-tag severity-${ai.severity || '未知'}">${ai.severity || '未知'}</span></dd></div>
                    <div><dt>描述</dt><dd>${ai.description || '无'}</dd></div>
                    <div><dt>成因</dt><dd>${ai.cause || '无'}</dd></div>
                    <div><dt>建议</dt><dd>${ai.suggestion || '无'}</dd></div>
                </dl>`;
        }

        // 填充表单默认值
        const t = new Date(rec.created_at).toLocaleString();
        const className = ((rec.yolo_detections || [{}])[0].class_name || '缺陷');
        const sev = ai.severity || '一般';

        const titleEl = document.getElementById('dispatch-title');
        if (titleEl) titleEl.value = `${className} 检测于 ${t}`;

        const descEl = document.getElementById('dispatch-desc');
        if (descEl) descEl.value = ai.description || '';

        const sevEl = document.getElementById('dispatch-severity');
        if (sevEl && (sev === '严重' || sev === '紧急' || sev === '一般')) sevEl.value = sev;

        // GPS
        const latEl = document.getElementById('dispatch-gps-lat');
        const lngEl = document.getElementById('dispatch-gps-lng');
        if (latEl) latEl.value = rec.gps_lat || rec.gps_latitude || '';
        if (lngEl) lngEl.value = rec.gps_lng || rec.gps_longitude || '';

        // 检修人列表
        const r = await apiGet('/api/repairmen');
        const sel = document.getElementById('dispatch-assignee');
        if (sel && r.data) sel.innerHTML = r.data.map(u => `<option value="${u.id}">${u.full_name || u.username}</option>`).join('');

        // 显示模态框
        ModalManager.open('dispatch-modal');
        // 初始化 GPS 小地图（Phase 28）
        initDispatchMap();

    } catch (e) { errLog('main', '派发失败', e); alert('加载派发信息失败: ' + e.message); }
}

async function submitDispatch() {
    const title = document.getElementById('dispatch-title')?.value?.trim();
    const desc = document.getElementById('dispatch-desc')?.value?.trim();
    const sev = document.getElementById('dispatch-severity')?.value;
    const aid = document.getElementById('dispatch-assignee')?.value;
    const gpsLat = parseFloat(document.getElementById('dispatch-gps-lat')?.value) || null;
    const gpsLng = parseFloat(document.getElementById('dispatch-gps-lng')?.value) || null;

    if (!title || !aid) { alert('请填写标题并选择检修人'); return; }

    const fd = new FormData();
    fd.append('title', title);
    fd.append('description', desc || '');
    fd.append('severity', sev);
    fd.append('assigned_to', aid);
    if (currentDispatchRecordId) fd.append('detection_record_id', currentDispatchRecordId);
    if (currentDispatchImagePath) fd.append('annotated_image_path', currentDispatchImagePath);
    if (currentDispatchAiSummary) fd.append('ai_summary', JSON.stringify(currentDispatchAiSummary));
    if (gpsLat !== null) fd.append('gps_lat', gpsLat);
    if (gpsLng !== null) fd.append('gps_lng', gpsLng);

    try {
        const d = await apiPost('/api/orders', fd);
        if (d.success) {
            ModalManager.close('dispatch-modal');
            notify(`工单 #${d.data.id} 已派发！`, 'success');
            log('main', `工单派发成功: #${d.data.id}`);
            currentDispatchRecordId = null;
            currentDispatchImagePath = null;
            currentDispatchAiSummary = null;
            loadHistory();  // 刷新列表
        } else {
            alert(d.detail || '派发失败');
        }
    } catch (e) { errLog('main', '提交派发失败', e); alert('错误: ' + e.message); }
}

// ===== GPS 手动输入（Leaflet 地图点选）=====

let gpsMiniMap = null;      // 小型 Leaflet 地图实例
let gpsMarker = null;       // 地图上的标记点

function showGpsModal(recordId) {
    log('main', `showGpsModal: 记录 #${recordId}`);
    currentGpsRecordId = recordId;

    // 加载记录信息以获取现有坐标
    apiGet(`/api/history/${recordId}`).then(d => {
        if (!d.success) return;
        const rec = d.data;
        const existingLat = rec.gps_lat || rec.gps_latitude;
        const existingLng = rec.gps_lng || rec.gps_longitude;
        const hasCoords = existingLat != null && existingLng != null;

        // 默认位置：有坐标用坐标，无坐标用中国中心
        const centerLat = hasCoords ? existingLat : 30.0;
        const centerLng = hasCoords ? existingLng : 120.0;

        // 更新标题和来源提示
        const titleEl = document.getElementById('gpsMapModalTitle');
        if (titleEl) titleEl.textContent = `📍 编辑 GPS 位置 - 记录 #${recordId}`;

        const hintEl = document.getElementById('gps-source-hint');
        if (hintEl) {
            const src = rec.gps_source || 'none';
            if (src === 'exif') hintEl.textContent = '📍 当前坐标来源: EXIF 自动提取';
            else if (src === 'manual') hintEl.textContent = '✏️ 当前坐标来源: 手动输入';
            else hintEl.textContent = '📍 无现有坐标，请在地图上点选';
        }

        // 设置输入框
        const latEl = document.getElementById('gps-input-lat');
        const lngEl = document.getElementById('gps-input-lng');
        if (latEl) latEl.value = hasCoords ? existingLat : '';
        if (lngEl) lngEl.value = hasCoords ? existingLng : '';

        // 显示模态框（使用 ModalManager，防遮罩残留）
        const modalEl = document.getElementById('gpsMapModal');
        const bsModal = ModalManager.open('gpsMapModal');

        // 模态框显示后再初始化地图（DOM 已渲染）
        if (modalEl && bsModal) {
            modalEl.addEventListener('shown.bs.modal', function initMap() {
                modalEl.removeEventListener('shown.bs.modal', initMap);
                setTimeout(() => initGpsMiniMap(centerLat, centerLng, hasCoords ? [existingLat, existingLng] : null), 100);
            });
        }
    }).catch(e => { errLog('main', '加载记录失败', e); });
}

function initGpsMiniMap(centerLat, centerLng, markerPos) {
    // 销毁旧地图
    if (gpsMiniMap) { gpsMiniMap.remove(); gpsMiniMap = null; }
    if (gpsMarker) { gpsMarker = null; }

    const mapEl = document.getElementById('gps-mini-map');
    if (!mapEl) return;

    gpsMiniMap = L.map(mapEl, { attributionControl: false }).setView([centerLat, centerLng], markerPos ? 14 : 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap',
    }).addTo(gpsMiniMap);

    // 更新输入框的函数
    const updateInputs = (lat, lng) => {
        const latEl = document.getElementById('gps-input-lat');
        const lngEl = document.getElementById('gps-input-lng');
        if (latEl) latEl.value = lat.toFixed(6);
        if (lngEl) lngEl.value = lng.toFixed(6);
    };

    // 如果有现有坐标，放置可拖动标记
    if (markerPos) {
        gpsMarker = L.marker(markerPos, { draggable: true }).addTo(gpsMiniMap);
        gpsMarker.on('dragend', () => {
            const pos = gpsMarker.getLatLng();
            updateInputs(pos.lat, pos.lng);
        });
    }

    // 点击地图放置/移动标记
    gpsMiniMap.on('click', e => {
        const { lat, lng } = e.latlng;
        updateInputs(lat, lng);
        if (gpsMarker) {
            gpsMarker.setLatLng([lat, lng]);
        } else {
            gpsMarker = L.marker([lat, lng], { draggable: true }).addTo(gpsMiniMap);
            gpsMarker.on('dragend', () => {
                const pos = gpsMarker.getLatLng();
                updateInputs(pos.lat, pos.lng);
            });
        }
    });

    // 修复地图尺寸问题
    setTimeout(() => gpsMiniMap.invalidateSize(), 200);
}

async function saveGpsFromModal() {
    const latVal = parseFloat(document.getElementById('gps-input-lat')?.value);
    const lngVal = parseFloat(document.getElementById('gps-input-lng')?.value);

    if (isNaN(latVal) || isNaN(lngVal)) { alert('经纬度必须为有效数字'); return; }
    if (latVal < -90 || latVal > 90) { alert('纬度范围: -90 ~ 90'); return; }
    if (lngVal < -180 || lngVal > 180) { alert('经度范围: -180 ~ 180'); return; }
    if (!currentGpsRecordId) return;

    const fd = new FormData();
    fd.append('gps_lat', latVal);
    fd.append('gps_lng', lngVal);
    try {
        const d = await apiPost(`/api/history/${currentGpsRecordId}/gps`, fd);
        if (d.success) {
            // 清理地图
            if (gpsMiniMap) { gpsMiniMap.remove(); gpsMiniMap = null; gpsMarker = null; }
            ModalManager.close('gpsMapModal');
            notify('GPS 位置已保存 ✏️ 手动', 'info');
            currentGpsRecordId = null;
            loadHistory();
        } else {
            alert(d.detail || '保存失败');
        }
    } catch (e) { errLog('main', '保存 GPS 失败', e); alert('错误: ' + e.message); }
}

// ===== 视频检测详情（Phase 7）=====

async function openVideoDetail(recordId) {
    log('main', `openVideoDetail: 记录 #${recordId}`);
    try {
        const d = await apiGet(`/api/video/records/${recordId}`);
        if (!d.success) { alert('加载失败'); return; }
        currentVideoRecord = d.data;

        // 更新标题和摘要
        const titleEl = document.getElementById('videoDetailTitle');
        if (titleEl) titleEl.textContent = `🎬 视频检测结果 - ${d.data.original_filename || '未知'}`;

        const summaryEl = document.getElementById('videoDetailSummary');
        if (summaryEl) {
            const dur = d.data.duration_seconds || 0;
            const durStr = `${Math.floor(dur/60)}分${Math.round(dur%60)}秒`;
            const sevTag = `<span class="severity-tag severity-${d.data.severity || '一般'}">${d.data.severity || '一般'}</span>`;
            summaryEl.innerHTML = `
                <div class="video-meta-stats">
                    <div class="video-meta-stat"><span>总帧数</span><strong>${d.data.total_frames || 0}</strong></div>
                    <div class="video-meta-stat"><span>缺陷帧</span><strong>${d.data.defect_count || 0}</strong></div>
                    <div class="video-meta-stat"><span>时长</span><strong>${durStr}</strong></div>
                    <div class="video-meta-stat"><span>等级</span>${sevTag}</div>
                </div>`;
        }

        // 下载链接
        const btnDl = document.getElementById('btn-download-video2');
        if (btnDl) btnDl.href = d.data.output_video_path || '#';

        // 跳转视频
        const btnJump = document.getElementById('btn-jump-video');
        if (btnJump) btnJump.onclick = () => window.open(`/video/play/${d.data.task_id}?time=0`, '_blank');

        // 渲染整段视频 AI 总结（Phase 27：取代逐帧时间轴）
        renderVideoSummary(d.data.video_summary);

        // 渲染帧网格
        renderFrameGrid();

        // 显示模态框
        ModalManager.open('videoDetailModal');
    } catch (e) { errLog('main', '加载视频详情失败', e); alert('加载视频详情失败: ' + e.message); }
}

function renderFrameGrid() {
    if (!currentVideoRecord) return;
    const grid = document.getElementById('frameGrid');
    if (!grid) return;

    const showAll = document.getElementById('showAllFrames')?.checked || false;
    const framesData = currentVideoRecord.frames_data || [];

    // 图片路径查找表：优先 frame_images（新），降级 keyframe_images（旧数据兼容）
    const imageMap = {};
    const frameImages = currentVideoRecord.frame_images || [];
    frameImages.forEach(fi => { imageMap[fi.frame_index] = fi; });
    if (!Object.keys(imageMap).length) {
        (currentVideoRecord.keyframe_images || []).forEach(kf => { imageMap[kf.frame_index] = kf; });
    }

    // 筛选帧（默认间隔5帧，除非开启"显示全部"）
    const displayFrames = showAll ? framesData : framesData.filter(f => f.frame_index % 5 === 0);

    grid.innerHTML = displayFrames.map(f => {
        const isDefect = f.has_defect;
        const cardClass = isDefect ? 'defect' : 'normal';
        const badgeClass = isDefect ? 'defect' : 'normal';
        const badgeText = isDefect ? '⚠ 缺陷' : '✓ 正常';
        const sev = (f.ai_analysis && f.ai_analysis.severity) ? f.ai_analysis.severity : '';

        // Phase 7.1: 所有帧都有真实图片（带 YOLO 框），使用懒加载
        const fi = imageMap[f.frame_index];
        let imgHtml = '';
        if (fi && fi.image_path) {
            imgHtml = `<img data-src="/${fi.image_path}" loading="lazy" alt="帧${f.frame_index}"
                onerror="this.onerror=null; this.outerHTML='<div class=\\'frame-placeholder\\' style=\\'width:100%;height:120px;background:#3a3a4a;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:11px;\\'>⚠ 图片加载失败</div>'">`;
        } else {
            // 极端情况：确实无图片时显示占位
            imgHtml = `<div class="frame-placeholder" style="width:100%;height:120px;background:#3a3a4a;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#bbb;font-size:11px;">
                <div style="font-size:22px;margin-bottom:4px;">🎞️</div>
                <div>帧 #${f.frame_index}</div>
            </div>`;
        }

        return `<div class="frame-card ${cardClass}" onclick="openFrameDetail(${currentVideoRecord.id}, ${f.frame_index})">
            ${imgHtml}
            <div class="frame-info">
                <span class="frame-badge ${badgeClass}">${badgeText}</span>
                ${sev ? ` <span class="severity-tag severity-${sev}" style="font-size:10px;">${sev}</span>` : ''}
                <div style="font-size:10px;color:#999;">#${f.frame_index} | ${f.timestamp.toFixed(1)}s</div>
            </div>
        </div>`;
    }).join('');

    // 懒加载：Intersection Observer 或原生 loading="lazy"
    lazyLoadImages();
}

// 自定义懒加载（Intersection Observer，滚动到视口附近才设置 src）
function lazyLoadImages() {
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    if (img.dataset.src) {
                        img.src = img.dataset.src;
                        delete img.dataset.src;
                    }
                    observer.unobserve(img);
                }
            });
        }, { rootMargin: '100px' });
        document.querySelectorAll('#frameGrid img[data-src]').forEach(img => observer.observe(img));
    } else {
        // 降级：直接全部加载
        document.querySelectorAll('#frameGrid img[data-src]').forEach(img => {
            img.src = img.dataset.src;
            delete img.dataset.src;
        });
    }
}

async function openFrameDetail(recordId, frameIndex) {
    log('main', `openFrameDetail: 记录 #${recordId}, 帧 #${frameIndex}`);
    currentFrameIndex = frameIndex;

    try {
        const d = await apiGet(`/api/video/records/${recordId}/frame/${frameIndex}`);
        if (!d.success) { alert('加载帧失败'); return; }
        const frame = d.data;

        // 标题
        const titleEl = document.getElementById('frameDetailTitle');
        if (titleEl) titleEl.textContent = `📸 帧 #${frameIndex} | ⏱️ ${frame.timestamp.toFixed(1)}s`;

        // 帧信息
        const infoEl = document.getElementById('frameIndexInfo');
        if (infoEl) infoEl.textContent = `帧 ${frameIndex} / ${frame.total_frames || '?'}`;

        // 图片
        const canvas = document.getElementById('frameDetailCanvas');
        const img = document.getElementById('frameDetailImg');
        if (frame.image_url) {
            if (canvas) canvas.style.display = 'none';
            if (img) { img.style.display = 'block'; img.src = frame.image_url; }
        } else {
            // 无图片时用 Canvas 绘制占位 + 检测框（灰底，避免纯黑）
            if (img) img.style.display = 'none';
            if (canvas) {
                canvas.style.display = 'block';
                const ctx = canvas.getContext('2d');
                canvas.width = 640; canvas.height = 360;
                // 灰色背景（非纯黑）
                ctx.fillStyle = '#3a3a4a'; ctx.fillRect(0, 0, 640, 360);
                // 网格参考线
                ctx.strokeStyle = '#4a4a5a'; ctx.lineWidth = 1;
                for (let i = 0; i <= 640; i += 64) { ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, 360); ctx.stroke(); }
                for (let j = 0; j <= 360; j += 36) { ctx.beginPath(); ctx.moveTo(0, j); ctx.lineTo(640, j); ctx.stroke(); }

                if (frame.detections && frame.detections.length > 0) {
                    // 绘制检测框
                    ctx.strokeStyle = '#00ff66'; ctx.lineWidth = 2;
                    frame.detections.forEach(det => {
                        if (det.bbox && Array.isArray(det.bbox) && det.bbox.length === 4) {
                            const [x1, y1, x2, y2] = det.bbox;
                            ctx.strokeRect(x1, y1, x2-x1, y2-y1);
                            ctx.fillStyle = '#00ff66'; ctx.font = 'bold 13px sans-serif';
                            ctx.fillText(`${det.class_name} ${(det.confidence*100).toFixed(0)}%`, x1, y1 - 4);
                        }
                    });
                } else {
                    // 无检测结果，显示"无目标"
                    ctx.fillStyle = '#888'; ctx.font = '16px sans-serif'; ctx.textAlign = 'center';
                    ctx.fillText('无目标', 320, 180);
                    ctx.textAlign = 'start';
                }
            }
        }

        // YOLO 检测结果
        const detList = document.getElementById('frameDetectionList');
        if (detList) {
            if (frame.detections && frame.detections.length > 0) {
                detList.innerHTML = `<h6>🔍 YOLO 检测结果 (${frame.detections.length}个)</h6><ul>` +
                    frame.detections.map(d => `<li>${d.class_name} (${d.class_id}): 置信度 ${(d.confidence*100).toFixed(1)}%</li>`).join('') +
                    '</ul>';
            } else {
                detList.innerHTML = '<p class="text-muted">无检测结果</p>';
            }
        }

        // AI 分析
        const aiEl = document.getElementById('frameAiAnalysis');
        if (aiEl && frame.ai_analysis) {
            const ai = frame.ai_analysis;
            aiEl.innerHTML = `<h6>🤖 AI 分析报告</h6>
                <p><strong>严重等级:</strong> <span class="severity-tag severity-${ai.severity || '一般'}">${ai.severity || '一般'}</span></p>
                <p><strong>描述:</strong> ${ai.description || '无'}</p>
                <p><strong>成因:</strong> ${ai.cause || '无'}</p>
                <p><strong>建议:</strong> ${ai.suggestion || '无'}</p>`;
        } else if (aiEl) {
            aiEl.innerHTML = '<p class="text-muted">ℹ️ 本系统已改为整段视频 AI 总结模式，请在视频详情弹窗中查看整段视频的分析总结。</p>';
        }

        // GPS
        const gpsEl = document.getElementById('frameGpsInfo');
        if (gpsEl && currentVideoRecord) {
            const lat = currentVideoRecord.gps_lat;
            const lng = currentVideoRecord.gps_lng;
            const src = currentVideoRecord.gps_source || 'none';
            gpsEl.innerHTML = `GPS: ${lat ? lat + ', ' + lng : '无'} ${src === 'exif' ? '📍 EXIF' : src === 'manual' ? '✏️ 手动' : ''}`;
        }

        // Phase 29: 等级显示 + 生成工单按钮（等级非"正常"即可生成，含手动修改）
        const effectiveSev = frame.severity_override || frame.original_severity || '正常';
        updateSeverityDisplay(effectiveSev, frame.severity_override ? '手动修改' : 'YOLO判定');
        showSeverityModInfo(frame);
        const btnOrder = document.getElementById('btn-frame-create-order');
        if (btnOrder) {
            if (effectiveSev !== '正常' && currentUser && currentUser.role === 'inspector') {
                btnOrder.classList.remove('d-none');
            } else {
                btnOrder.classList.add('d-none');
            }
        }

        // 上一帧/下一帧按钮
        const btnPrev = document.getElementById('btn-prev-frame');
        const btnNext = document.getElementById('btn-next-frame');
        if (btnPrev) btnPrev.onclick = () => {
            const frames = currentVideoRecord?.frames_data || [];
            const idx = frames.findIndex(f => f.frame_index === currentFrameIndex);
            if (idx > 0) openFrameDetail(recordId, frames[idx - 1].frame_index);
        };
        if (btnNext) btnNext.onclick = () => {
            const frames = currentVideoRecord?.frames_data || [];
            const idx = frames.findIndex(f => f.frame_index === currentFrameIndex);
            if (idx < frames.length - 1) openFrameDetail(recordId, frames[idx + 1].frame_index);
        };

        // 显示模态框
        ModalManager.open('frameDetailModal');
    } catch (e) { errLog('main', '加载帧详情失败', e); }
}

// ===== Phase 29: 单帧预警等级手动修改 =====

const SEVERITY_BADGE_CLASS = { '正常': 'bg-secondary', '一般': 'bg-info', '严重': 'bg-warning', '紧急': 'bg-danger' };

/** 获取帧的有效等级：优先手动修改，否则原始等级，最后按 YOLO 推断 */
function getEffectiveSeverityOfFrame(frame) {
    if (frame?.severity_override) return frame.severity_override;
    if (frame?.original_severity) return frame.original_severity;
    return frame?.has_defect ? '严重' : '正常';
}

/** 更新当前等级徽章与来源标记（🤖 YOLO判定 / ✏️ 手动修改） */
function updateSeverityDisplay(severity, source) {
    const badge = document.getElementById('current-severity-badge');
    if (badge) {
        badge.className = 'badge ' + (SEVERITY_BADGE_CLASS[severity] || 'bg-secondary');
        badge.textContent = severity;
    }
    const tag = document.getElementById('severity-source-tag');
    if (tag) tag.textContent = source === '手动修改' ? '(✏️ 手动修改)' : '(🤖 YOLO判定)';
}

/** 显示手动修改记录（修改人 + 时间），未修改则隐藏 */
function showSeverityModInfo(frame) {
    const el = document.getElementById('severity-mod-info');
    if (!el) return;
    if (frame?.severity_modified_by_name) {
        el.style.display = 'block';
        const t = frame.severity_modified_at ? new Date(frame.severity_modified_at).toLocaleString() : '';
        el.innerHTML = `✏️ <strong>${frame.severity_modified_by_name}</strong> 于 ${t} 手动修改`;
    } else {
        el.style.display = 'none';
        el.innerHTML = '';
    }
}

/** 切换等级编辑区域显隐 */
function toggleSeverityEdit() {
    const area = document.getElementById('severity-edit-area');
    if (!area) return;
    const shown = area.style.display !== 'none';
    area.style.display = shown ? 'none' : 'flex';
    if (!shown) {
        // 预选当前有效等级，便于微调
        const sel = document.getElementById('severity-select');
        const badge = document.getElementById('current-severity-badge');
        if (sel && badge) sel.value = badge.textContent || '正常';
    }
}

function cancelSeverityEdit() {
    const area = document.getElementById('severity-edit-area');
    if (area) area.style.display = 'none';
}

/** 确认修改等级：调用后端接口并刷新界面 */
async function confirmSeverityModification() {
    if (!currentVideoRecord || currentFrameIndex === undefined) return;
    const newSeverity = document.getElementById('severity-select')?.value || '正常';
    try {
        const d = await apiPutJson(`/api/video/records/${currentVideoRecord.id}/frame/${currentFrameIndex}/severity`, { severity: newSeverity });
        if (d.success) {
            const data = d.data;
            updateSeverityDisplay(data.effective_severity, '手动修改');
            showSeverityModInfo(data);
            cancelSeverityEdit();
            updateFrameSeverityInMemory(data);
            // 等级非"正常"则生成工单按钮可用
            const btnOrder = document.getElementById('btn-frame-create-order');
            if (btnOrder) {
                if (data.effective_severity !== '正常' && currentUser?.role === 'inspector') btnOrder.classList.remove('d-none');
                else btnOrder.classList.add('d-none');
            }
            notify(`✅ 预警等级已修改为: ${data.effective_severity}`, 'success');
            log('main', `帧 #${currentFrameIndex} 等级已改为 ${data.effective_severity}`);
        } else {
            alert(d.detail || '修改失败');
        }
    } catch (e) { errLog('main', '修改等级失败', e); alert('错误: ' + e.message); }
}

/** 同步更新内存中 currentVideoRecord 的帧数据，保证后续生成工单使用最新等级 */
function updateFrameSeverityInMemory(data) {
    if (!currentVideoRecord?.frames_data) return;
    const fd = currentVideoRecord.frames_data.find(f => f.frame_index === data.frame_index);
    if (fd) {
        fd.severity_override = data.severity_override;
        fd.original_severity = data.original_severity;
        fd.severity_modified_at = data.severity_modified_at;
        fd.severity_modified_by = data.severity_modified_by;
        fd.severity_modified_by_name = data.severity_modified_by_name;
    }
}

function createOrderFromFrame() {
    if (!currentVideoRecord || currentFrameIndex === undefined) return;
    const frame = (currentVideoRecord.frames_data || []).find(f => f.frame_index === currentFrameIndex);
    // Phase 29: 使用有效等级（手动修改优先），支持对任意帧生成工单
    const severity = getEffectiveSeverityOfFrame(frame);

    // 设置派发表单
    const titleEl = document.getElementById('dispatch-title');
    if (titleEl) titleEl.value = `视频${currentVideoRecord.original_filename || ''} 帧#${currentFrameIndex} - ${severity}`;

    const descEl = document.getElementById('dispatch-desc');
    if (descEl) descEl.value = (frame?.ai_analysis?.description) || '';

    const sevEl = document.getElementById('dispatch-severity');
    if (sevEl && ['正常', '一般', '严重', '紧急'].includes(severity)) sevEl.value = severity;

    const latEl = document.getElementById('dispatch-gps-lat');
    const lngEl = document.getElementById('dispatch-gps-lng');
    if (latEl) latEl.value = currentVideoRecord.gps_lat || '';
    if (lngEl) lngEl.value = currentVideoRecord.gps_lng || '';

    // 填充当前帧图片（修复2.2：帧图路径需正确拼接为 /api/preview/...，失败降级）
    // 记录当前帧图片路径，派发工单时传给后端（否则 Worker 端工单详情无图）
    const fi = (currentVideoRecord.frame_images || []).find(f => f.frame_index === currentFrameIndex);
    currentDispatchImagePath = fi?.image_path || null;
    const imgEl = document.getElementById('dispatch-img');
    if (imgEl) {
        if (currentDispatchImagePath) {
            imgEl.src = buildPreviewUrl(currentDispatchImagePath);
            imgEl.onerror = () => { imgEl.removeAttribute('src'); imgEl.alt = '⚠ 图片加载失败'; };
        } else {
            imgEl.removeAttribute('src');
        }
    }

    // 填充 AI 分析摘要（整段视频总结 video_summary，工单创建时一并保存）
    currentDispatchAiSummary = currentVideoRecord.video_summary || null;
    const aiSummaryEl = document.getElementById('dispatch-ai-summary');
    if (aiSummaryEl) {
        const vs = currentDispatchAiSummary;
        if (vs?.overall_description) {
            const riskClass = { '低': 'success', '中': 'warning', '高': 'danger', '紧急': 'danger' }[vs.risk_level || '中'] || 'warning';
            aiSummaryEl.innerHTML = `
                <p><strong>风险等级:</strong> <span class="badge bg-${riskClass}">${vs.risk_level || '中'}</span></p>
                <p><strong>总体描述:</strong> ${vs.overall_description || '无'}</p>
                ${vs.suggestions ? `<p><strong>建议:</strong> ${vs.suggestions}</p>` : ''}
                ${vs.focus_points ? `<p><strong>重点关注:</strong> ${vs.focus_points}</p>` : ''}`;
        } else {
            aiSummaryEl.innerHTML = '<p class="text-muted">该视频未生成 AI 分析摘要</p>';
        }
    }

    // 视频帧派发不关联 detection_record
    currentDispatchRecordId = null;

    // 加载检修人列表
    apiGet('/api/repairmen').then(d => {
        const sel = document.getElementById('dispatch-assignee');
        if (sel && d.data) sel.innerHTML = d.data.map(u => `<option value="${u.id}">${u.full_name || u.username}</option>`).join('');
    });

    // 修复2.1：先关闭帧详情弹窗，等待其 backdrop 动画清理完成（约 350ms）
    // 再打开派发弹窗，避免嵌套 Modal 的 z-index/backdrop 冲突导致派发弹窗被遮挡
    ModalManager.close('frameDetailModal');
    setTimeout(() => {
        ModalManager.open('dispatch-modal');
        initDispatchMap();
    }, 350);
}

// ===== 派发工单 GPS 小地图（Phase 28）=====
let dispatchMap = null;      // 派发弹窗内的 Leaflet 地图实例
let dispatchMarker = null;   // 地图上的标记点

function initDispatchMap() {
    const mapEl = document.getElementById('dispatch-map');
    if (!mapEl) return;
    // 销毁旧实例，避免重复初始化
    if (dispatchMap) { dispatchMap.remove(); dispatchMap = null; }
    dispatchMarker = null;

    const latEl = document.getElementById('dispatch-gps-lat');
    const lngEl = document.getElementById('dispatch-gps-lng');
    const lat = parseFloat(latEl?.value) || 30.0;
    const lng = parseFloat(lngEl?.value) || 120.0;
    const hasCoords = !!parseFloat(latEl?.value) && !!parseFloat(lngEl?.value);

    dispatchMap = L.map(mapEl, { attributionControl: false }).setView([lat, lng], hasCoords ? 14 : 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap',
    }).addTo(dispatchMap);

    const updateInputs = (latv, lngv) => {
        if (latEl) latEl.value = latv.toFixed(6);
        if (lngEl) lngEl.value = lngv.toFixed(6);
    };

    // 已有坐标时放置可拖动标记
    if (hasCoords) {
        dispatchMarker = L.marker([lat, lng], { draggable: true }).addTo(dispatchMap);
        dispatchMarker.on('dragend', () => {
            const pos = dispatchMarker.getLatLng();
            updateInputs(pos.lat, pos.lng);
        });
    }

    // 点击地图放置/移动标记，并自动填充经纬度输入框
    dispatchMap.on('click', e => {
        const { lat: latv, lng: lngv } = e.latlng;
        updateInputs(latv, lngv);
        if (dispatchMarker) {
            dispatchMarker.setLatLng([latv, lngv]);
        } else {
            dispatchMarker = L.marker([latv, lngv], { draggable: true }).addTo(dispatchMap);
            dispatchMarker.on('dragend', () => {
                const pos = dispatchMarker.getLatLng();
                updateInputs(pos.lat, pos.lng);
            });
        }
    });

    // 修复地图尺寸（modal 显示后 Leaflet 需 invalidateSize 才正确渲染）
    setTimeout(() => dispatchMap.invalidateSize(), 200);
}

// ===== 历史记录多选删除（Admin）=====

function updateHistorySelectionUI(records) {
    const btn = document.getElementById('btn-batch-delete-history');
    const countEl = document.getElementById('history-selected-count');
    const selectAllEl = document.getElementById('history-select-all');
    const count = selectedHistoryIds.size;
    if (countEl) countEl.textContent = String(count);
    if (btn) {
        const canDelete = currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin');
        btn.classList.toggle('d-none', !(canDelete && count > 0));
    }
    const selectAllTh = document.getElementById('history-select-all-th');
    if (selectAllTh) {
        const canDelete = currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin');
        selectAllTh.classList.toggle('d-none', !canDelete);
    }
    if (selectAllEl && Array.isArray(records) && records.length) {
        const pageKeys = records.map(r => (r.record_type || 'image') + ':' + r.id);
        const selectedCount = pageKeys.filter(k => selectedHistoryIds.has(k)).length;
        selectAllEl.checked = selectedCount === pageKeys.length;
        selectAllEl.indeterminate = selectedCount > 0 && selectedCount < pageKeys.length;
    } else if (selectAllEl) {
        selectAllEl.checked = false;
        selectAllEl.indeterminate = false;
    }
}

function toggleSelectAllHistory(el) {
    if (!el) return;
    currentHistoryPageRecords.forEach(r => {
        const key = (r.record_type || 'image') + ':' + r.id;
        if (el.checked) selectedHistoryIds.add(key);
        else selectedHistoryIds.delete(key);
    });
    document.querySelectorAll('.history-row-check').forEach(cb => { cb.checked = el.checked; });
    updateHistorySelectionUI(currentHistoryPageRecords);
}

function toggleHistoryRow(cb) {
    if (!cb?.dataset?.key) return;
    if (cb.checked) selectedHistoryIds.add(cb.dataset.key);
    else selectedHistoryIds.delete(cb.dataset.key);
    updateHistorySelectionUI(currentHistoryPageRecords);
}

function showBatchDeleteHistoryModal() {
    if (!selectedHistoryIds.size) return;
    const countEl = document.getElementById('batch-del-history-count');
    const listEl = document.getElementById('batch-del-history-list');
    if (countEl) countEl.textContent = selectedHistoryIds.size + ' 条';
    if (listEl) listEl.textContent = '将删除 ID: ' + Array.from(selectedHistoryIds).sort().join('、');

    const btn = document.getElementById('btn-confirm-batch-delete-history');
    if (btn) {
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);
        newBtn.addEventListener('click', executeBatchDeleteHistory);
    }
    ModalManager.open('batchDeleteHistoryModal');
}

async function executeBatchDeleteHistory() {
    const items = Array.from(selectedHistoryIds).map(k => {
        const idx = k.indexOf(':');
        return { record_type: k.slice(0, idx), id: Number(k.slice(idx + 1)) };
    });
    if (!items.length) return;
    try {
        const d = await apiPostJson('/api/history/batch-delete', { items });
        selectedHistoryIds.clear();
        ModalManager.close('batchDeleteHistoryModal');
        await loadHistory(historyPage);
        notifyDataChanged();
        if (d.success) {
            const failed = d.data?.failed || [];
            if (failed.length) notify(`已删除 ${d.data.deleted} 条，失败 ${failed.length} 条`, 'warning');
            else notify(`已删除 ${d.data.deleted} 条检测记录`, 'warning');
        } else {
            alert(d.detail || '批量删除失败');
        }
    } catch (e) {
        errLog('main', '批量删除历史记录失败', e);
        alert('错误: ' + e.message);
    }
}

// ===== 删除视频记录（Admin）=====

function deleteVideoRecord(recordId, filename, frameCount) {
    const confirmMsg = `⚠️ 确认删除\n\n您确定要删除视频记录 [${filename}] 吗？\n此操作将删除所有相关的帧图片（共 ${frameCount} 张），不可恢复！`;
    if (!confirm(confirmMsg)) return;

    fetch(`/api/video/records/${recordId}`, { method: 'DELETE' })
        .then(res => res.json())
        .then(d => {
            if (d.success) {
                notify(`视频记录已删除（清理了 ${d.data.deleted_images} 张帧图片）`, 'warning');
                loadHistory();
                notifyDataChanged();
            } else {
                alert(d.detail || '删除失败');
            }
        })
        .catch(e => { errLog('main', '删除视频记录失败', e); alert('错误: ' + e.message); });
}

// ===== 删除图片检测记录（Admin）=====

let currentDeleteRecordId = null;

function showDeleteRecordModal(recordId, filename, createdAt) {
    currentDeleteRecordId = recordId;
    document.getElementById('del-rec-id').textContent = '#' + recordId;
    document.getElementById('del-rec-filename').textContent = filename || '未知';
    document.getElementById('del-rec-time').textContent = createdAt ? new Date(createdAt).toLocaleString() : '未知';

    // 重新绑定确认按钮（避免重复监听）
    const btn = document.getElementById('btn-confirm-delete-record');
    const newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn.addEventListener('click', executeDeleteRecord);

    ModalManager.open('deleteRecordModal');
}

async function executeDeleteRecord() {
    if (!currentDeleteRecordId) return;
    try {
        const res = await fetch(`/api/history/${currentDeleteRecordId}`, { method: 'DELETE' });
        const d = await res.json();
        if (d.success) {
            ModalManager.close('deleteRecordModal');
            notify('记录已删除', 'info');
            currentDeleteRecordId = null;
            loadHistory();
            notifyDataChanged();
        } else {
            alert(d.detail || '删除失败');
        }
    } catch (e) { errLog('main', '删除记录失败', e); alert('错误: ' + e.message); }
}

// ===== 视频 =====

/** 动态加载外部脚本（带超时，用于懒加载 ffmpeg.wasm） */
function loadScript(src, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        const t = setTimeout(() => { s.onload = s.onerror = null; reject(new Error('加载超时: ' + src)); }, timeoutMs);
        s.onload = () => { clearTimeout(t); resolve(); };
        s.onerror = () => { clearTimeout(t); reject(new Error('加载失败: ' + src)); };
        s.src = src;
        document.head.appendChild(s);
    });
}

/** 秒数格式化为 "x分y秒"，用于进度剩余时间展示 */
function formatDuration(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? `${m}分${s}秒` : `${s}秒`;
}

/**
 * 移动端视频压缩（优化1）
 * 使用 ffmpeg.wasm（CDN 懒加载，约 30MB）将大视频压缩到 720P，上传时间缩短约 70%。
 * 仅在移动端 + 文件 ≥ 50MB 时触发；压缩失败/超时自动回退原文件，不影响主流程。
 */
async function compressVideoForMobile(file) {
    if (!isMobile() && !/Android|iPhone|iPad/i.test(navigator.userAgent)) return file;
    if (file.size < 50 * 1024 * 1024) return file;  // 小文件直接上传
    try {
        // 懒加载 ffmpeg.wasm（首次下载，之后走浏览器缓存）
        if (!window.FFmpeg) {
            await loadScript('https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.11.6/dist/ffmpeg.min.js');
        }
        const { createFFmpeg, fetchFile } = FFmpeg;
        const ffmpeg = createFFmpeg({
            corePath: 'https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.11.0/dist/ffmpeg-core.js',
            log: false,
        });
        await ffmpeg.load();
        const inName = 'input.mp4', outName = 'output.mp4';
        ffmpeg.FS('writeFile', inName, await fetchFile(file));
        await ffmpeg.run('-i', inName,
            '-vf', 'scale=720:-2',   // 宽度 720，高度自适应保持宽高比
            '-c:v', 'libx264', '-b:v', '2M', '-preset', 'fast',
            '-c:a', 'aac', '-b:a', '128k', '-y', outName);
        const data = ffmpeg.FS('readFile', outName);
        ffmpeg.FS('unlink', inName);
        ffmpeg.FS('unlink', outName);
        const compressed = new File([data.buffer], file.name, { type: 'video/mp4' });
        // 压缩后反而更大则用原文件
        return compressed.size < file.size ? compressed : file;
    } catch (e) {
        console.warn('视频压缩失败，使用原始文件:', e);
        return file;
    }
}

/**
 * 缓存命中渲染（优化7）：后端返回历史分析结果，直接展示完成态，无需轮询。
 */
function renderVideoResultFromCache(data, file) {
    notify('✅ 该视频已分析过，直接显示缓存结果', 'success');
    currentVideoTaskId = data.task_id;
    show('video-progress-container');
    const fill = document.getElementById('video-progress-fill');
    if (fill) fill.style.width = '100%';
    const txt = document.getElementById('video-progress-text');
    if (txt) txt.textContent = '100% (缓存命中)';
    const st = document.getElementById('video-status-text');
    if (st) st.textContent = 'completed';
    const nameDisplay = document.getElementById('video-file-name');
    if (nameDisplay) { nameDisplay.textContent = '✅ 缓存命中，直接显示结果'; nameDisplay.classList.remove('text-warning'); nameDisplay.classList.add('text-success'); }
    // 原始视频预览（本地文件）
    const vo = document.getElementById('video-original');
    if (vo) vo.src = URL.createObjectURL(file);
    show('video-compare-card');
    // 标注视频 + 下载按钮
    if (data.output_video) {
        const o = document.getElementById('video-output');
        if (o) o.src = data.output_video.startsWith('/') ? data.output_video : '/' + data.output_video;
        const btn = document.getElementById('btn-download-video');
        if (btn) { btn.classList.remove('d-none'); btn.onclick = () => window.open('/api/export/video/' + data.output_video.split('/').pop(), '_blank'); }
    }
    renderVideoSummary(data.video_summary);
    loadHistory();
    log('main', '视频缓存命中，直接展示结果');
}

async function uploadVideo() {
    log('main', 'uploadVideo 被调用');
    const inp = document.getElementById('file-video');
    const f = inp?.files?.[0];
    if (!f) { alert('请选择视频文件'); return; }

    // 移动端大文件压缩（优化1）：压缩失败自动回退原文件
    let uploadFile = f;
    if (isMobile() && f.size >= 50 * 1024 * 1024) {
        const nameEl = document.getElementById('video-file-name');
        if (nameEl) { nameEl.textContent = '⏳ 视频压缩中（首次需下载约 30MB 压缩组件，请稍候）...'; nameEl.classList.remove('text-success'); nameEl.classList.add('text-warning'); }
        try {
            uploadFile = await compressVideoForMobile(f);
            if (uploadFile !== f) {
                const mb = (uploadFile.size / 1024 / 1024).toFixed(1);
                log('main', `视频压缩完成: ${mb}MB`);
                if (nameEl) { nameEl.textContent = `✅ 已压缩 (${mb}MB)`; nameEl.classList.remove('text-warning'); nameEl.classList.add('text-success'); }
            }
        } catch (e) { errLog('main', '视频压缩失败，使用原文件', e); }
    }

    const fd = new FormData(); fd.append('file', uploadFile);
    fd.append('call_ai', document.getElementById('chk-ai-video')?.checked ?? true);
    try {
        const d = await apiPost('/api/upload/video', fd);
        if (d.success) {
            // 缓存命中（优化7）：直接渲染完成态
            if (d.data.from_cache) { renderVideoResultFromCache(d.data, f); return; }
            currentVideoTaskId = d.data.task_id;
            show('video-progress-container');
            const vo = document.getElementById('video-original');
            if (vo) vo.src = URL.createObjectURL(f);
            show('video-compare-card');
            startVideoPolling();
            log('main', `视频分析已启动: ${currentVideoTaskId}`);
        }
    } catch (e) { errLog('main', '视频上传失败', e); alert('错误: ' + e.message); }
}

function startVideoPolling() {
    if (videoPollTimer) clearInterval(videoPollTimer);
    // 移动端 5s 一次，电脑端保持 1.5s（策略5）
    const interval = isMobile() ? 5000 : 1500;
    videoPollTimer = setInterval(async () => {
        if (!currentVideoTaskId) { clearInterval(videoPollTimer); return; }
        // 页面不可见时跳过请求（移动端切后台 / 切 Tab 时不再轮询）
        if (document.hidden) return;
        try {
            const d = await apiGet(`/api/video/progress/${currentVideoTaskId}`);
            if (!d.success) return;
            const t = d.data;
            const fill = document.getElementById('video-progress-fill');
            if (fill) fill.style.width = (t.progress_pct || 0) + '%';
            const txt = document.getElementById('video-progress-text');
            if (txt) {
                // 优化2：进度百分比 + 帧数 + 预估剩余时间
                let msg = `${t.progress_pct || 0}% (${t.processed_frames}/${t.total_frames} 帧)`;
                if (t.estimated_remaining > 0) msg += ` · 预计剩余 ${formatDuration(t.estimated_remaining)}`;
                if (t.elapsed_seconds > 0) msg += ` · 已用 ${formatDuration(t.elapsed_seconds)}`;
                txt.textContent = msg;
            }
            const st = document.getElementById('video-status-text');
            if (st) st.textContent = t.status;

            // 文件名旁显示分析状态（Phase 7.2）
            const nameDisplay = document.getElementById('video-file-name');
            if (nameDisplay) {
                if (t.status === 'processing') {
                    nameDisplay.textContent = `⏳ 分析中... ${t.processed_frames}/${t.total_frames} 帧`;
                    nameDisplay.classList.remove('text-success');
                    nameDisplay.classList.add('text-warning');
                } else if (t.status === 'completed') {
                    nameDisplay.textContent = `✅ 分析完成！`;
                    nameDisplay.classList.remove('text-warning');
                    nameDisplay.classList.add('text-success');
                } else if (t.status === 'failed') {
                    nameDisplay.textContent = `❌ 分析失败`;
                    nameDisplay.classList.remove('text-warning', 'text-success');
                    nameDisplay.classList.add('text-danger');
                }
            }

            if (t.status === 'completed') {
                clearInterval(videoPollTimer); videoPollTimer = null;
                if (t.output_video_path) {
                    const vo = document.getElementById('video-output');
                    if (vo) vo.src = t.output_video_path.startsWith('/') ? t.output_video_path : '/' + t.output_video_path;
                    const btn = document.getElementById('btn-download-video');
                    if (btn) { btn.classList.remove('d-none'); btn.onclick = () => window.open('/api/export/video/' + t.output_video_path.split('/').pop(), '_blank'); }
                }
                renderVideoSummary(t.video_summary);
                loadHistory();
                log('main', '视频分析完成');
            } else if (t.status === 'failed') { clearInterval(videoPollTimer); }
        } catch (e) { /* 轮询静默 */ }
    }, interval);
}

/**
 * 渲染整段视频 AI 分析总结（Phase 27，取代逐帧 AI 时间轴）。
 * 同步渲染到两个位置：主检测页的总结卡片 + 视频详情弹窗的总结面板。
 */
function renderVideoSummary(summary) {
    const targets = [
        { card: 'video-summary-card', content: 'video-summary-content' },
        { card: 'videoDetailSummaryPanel', content: 'videoDetailSummaryContent' },
    ];
    targets.forEach(({ card, content }) => {
        const cardEl = document.getElementById(card);
        const el = document.getElementById(content);
        if (!el) return;
        if (!summary || !summary.overall_description) {
            // Phase 28: 未启用 AI 分析时不显示总结区块
            if (cardEl) cardEl.classList.add('d-none');
            el.innerHTML = '';
            return;
        }
        const risk = summary.risk_level || '中';
        const riskClass = { '低': 'success', '中': 'warning', '高': 'danger', '紧急': 'danger' }[risk] || 'warning';
        const issues = (summary.main_issues || []).map(iss => {
            const frames = iss.frames || [];
            const frameText = frames.length > 8
                ? frames.slice(0, 8).join('、') + ` 等 ${frames.length} 帧`
                : frames.join('、') || '未知';
            return `<li><strong>${iss.issue || '未知'}</strong>（${iss.severity || '一般'}）— 帧号: ${frameText}</li>`;
        }).join('');
        el.innerHTML = `
            <dl class="detail-kv">
                <div><dt>总体描述</dt><dd>${summary.overall_description || '无'}</dd></div>
                ${issues ? `<div><dt>主要问题</dt><dd><ul class="mb-0">${issues}</ul></dd></div>` : ''}
                <div><dt>风险等级</dt><dd><span class="badge bg-${riskClass}">${risk}</span></dd></div>
                ${summary.suggestions ? `<div><dt>建议</dt><dd>${summary.suggestions}</dd></div>` : ''}
                ${summary.focus_points ? `<div><dt>重点关注</dt><dd>${summary.focus_points}</dd></div>` : ''}
                ${summary.extra_notes ? `<div><dt>备注</dt><dd>${summary.extra_notes}</dd></div>` : ''}
            </dl>`;
        if (cardEl) cardEl.classList.remove('d-none');
    });
}

function seekVideo(sec) {
    const v = document.getElementById('video-output');
    if (v?.src && !v.src.endsWith('#')) { v.currentTime = sec; v.play().catch(() => {}); }
}

// ===== 摄像头 =====
async function toggleCamera() {
    log('main', 'toggleCamera 被调用');
    const btn = document.getElementById('btn-camera-toggle');
    if (cameraStream) {
        stopCamera();
        if (btn) btn.textContent = '▶ 开启摄像头';
    } else {
        try {
            cameraStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
            const video = document.getElementById('camera-video');
            if (!video) throw new Error('未找到 camera-video 元素');
            video.srcObject = cameraStream; await video.play();
            show('camera-layout'); show('camera-log-card');
            if (btn) btn.textContent = '⏸ 暂停';
            let fc = 0, st = Date.now();
            cameraTimer = setInterval(async () => {
                if (!cameraStream) { clearInterval(cameraTimer); return; }
                const cvs = document.getElementById('camera-canvas');
                if (!cvs) return;
                cvs.width = video.videoWidth || 640; cvs.height = video.videoHeight || 480;
                cvs.getContext('2d').drawImage(video, 0, 0);
                try {
                    const d = await apiPostJson('/api/predict_frame', { image: cvs.toDataURL('image/jpeg', 0.75) });
                    if (d.success) {
                        drawGlowBoxesOnCanvas(cvs, d.data.detections);
                        addCameraLog(d.data.detections);
                    }
                } catch (e) { /* 帧丢弃 */ }
                fc++;
                const fpsEl = document.getElementById('camera-fps');
                if (fpsEl) fpsEl.textContent = `FPS: ${(fc / ((Date.now() - st) / 1000)).toFixed(1)}`;
            }, 300);
            log('main', '摄像头已开启');
        } catch (e) { errLog('main', '摄像头失败', e); alert('摄像头失败: ' + e.message); }
    }
}

function stopCamera() {
    if (cameraStream) { cameraStream.getTracks().forEach(t => t.stop()); cameraStream = null; }
    if (cameraTimer) { clearInterval(cameraTimer); cameraTimer = null; }
    const layout = document.getElementById('camera-layout');
    if (layout) layout.classList.add('d-none');
}

function drawGlowBoxesOnCanvas(canvas, detections) {
    const ctx = canvas.getContext('2d');
    detections?.forEach(d => {
        const b = d.bbox, conf = d.confidence;
        ctx.save();
        ctx.shadowColor = conf > 0.7 ? 'rgba(0,255,100,0.9)' : 'rgba(255,255,0,0.7)';
        ctx.shadowBlur = 8; ctx.strokeStyle = conf > 0.7 ? '#00ff66' : '#ffff00'; ctx.lineWidth = 2;
        ctx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
        ctx.restore();
        ctx.fillStyle = '#00ff66'; ctx.font = '12px sans-serif';
        ctx.fillText(`${d.class_name} ${(conf * 100).toFixed(0)}%`, b.x1, b.y1 - 4);
    });
}

function addCameraLog(dets) {
    if (!dets?.length) return;
    const log = document.getElementById('camera-log');
    if (!log) return;
    const now = new Date().toLocaleTimeString();
    dets.forEach(d => {
        const div = document.createElement('div');
        div.className = 'log-item';
        div.innerHTML = `<span>${d.class_name}</span><span>${(d.confidence * 100).toFixed(1)}%</span><span class="log-time">${now}</span>`;
        log.prepend(div);
    });
    while (log.children.length > 10) log.removeChild(log.lastChild);
}

// ===== 历史 =====
async function loadHistory(pg = 1, auto = false) {
    historyPage = pg;
    const src = document.getElementById('history-filter')?.value || '';
    const al = document.getElementById('alert-only')?.checked || false;
    // 移动端每页 10 条，减少公网传输量（策略3）
    let url = `/api/history?page=${pg}&page_size=${isMobile() ? 10 : 15}`;
    if (src) url += `&source_type=${src}`;
    if (al) url += `&alert_only=true`;
    try {
        const d = await apiGet(url);
        if (!d.success) return;
        const { records, total, page, total_pages } = d.data;
        const tb = document.getElementById('history-tbody');
        if (!tb) return;

        // 静默刷新优化：数据无变化时跳过渲染，避免页面闪烁/重置滚动位置
        const sig = records.map(r => r.id + (r.record_type || '')).join(',') + '|' + total;
        if (auto && sig === lastHistorySignature) return;
        lastHistorySignature = sig;

        currentHistoryPageRecords = records;
        const canManage = currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin');
        const colSpan = canManage ? 11 : 10;
        const selectAllTh = document.getElementById('history-select-all-th');
        if (selectAllTh) selectAllTh.classList.toggle('d-none', !canManage);
        if (!records.length) {
            tb.innerHTML = `<tr><td colspan="${colSpan}" class="text-center">暂无记录</td></tr>`;
            updateHistorySelectionUI();
            return;
        }
        tb.innerHTML = records.map(r => {
            const recType = r.record_type || 'image';
            const isVideo = recType === 'video';
            const recKey = recType + ':' + r.id;
            const rowSelectCell = canManage
                ? `<td class="text-center"><input type="checkbox" class="form-check-input history-row-check" data-key="${recKey}" ${selectedHistoryIds.has(recKey) ? 'checked' : ''} onchange="toggleHistoryRow(this)"></td>`
                : '';

            // 类型标识
            const typeLabel = isVideo ? '🎬 视频' : '📷 图片';

            // GPS 来源标签（仅图片记录）
            let gpsLabel = '';
            if (!isVideo) {
                const gpsSrc = r.gps_source || 'none';
                if (gpsSrc === 'exif') gpsLabel = ' <small class="text-success">📍 EXIF</small>';
                else if (gpsSrc === 'manual') gpsLabel = ' <small class="text-primary">✏️ 手动</small>';
            }

            // 文件名（视频用 original_filename）
            const fname = isVideo ? (r.original_filename || '').substring(0, 18) : (r.source_name || '').substring(0, 18);

            // 操作按钮
            let actions = '';
            if (isVideo) {
                actions = `<button class="btn btn-sm btn-outline-primary" onclick="openVideoDetail(${r.id})">详情</button>
                    <a class="btn btn-sm btn-outline-success" href="${r.output_video_path || '#'}" download>📥 下载</a>
                    <button class="btn btn-sm btn-outline-info" onclick="window.open('/video/play/${r.task_id}','_blank')">▶ 播放</button>`;
                // Admin 可见：删除视频按钮
                if (currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin')) {
                    actions += ` <button class="btn btn-sm btn-outline-danger" onclick="deleteVideoRecord(${r.id}, '${(r.original_filename || '视频').replace(/'/g, "\\'")}', ${r.total_frames || 0})">🗑️</button>`;
                }
            } else {
                actions = `<button class="btn btn-sm btn-outline-primary" onclick="viewDetail(${r.id})">详情</button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="window.open('/api/export/${r.id}','_blank')">导出</button>`;
                // Admin 可见：派发按钮
                if (currentUser && currentUser.role === 'inspector') {
                    actions += ` <button class="btn btn-sm btn-outline-info" onclick="dispatchOrder(${r.id})">📤 派发</button>`;
                }
                // GPS 为空时显示"添加位置"按钮
                const hasGps = (r.gps_lat || r.gps_latitude) && (r.gps_lng || r.gps_longitude);
                if (!hasGps && currentUser && currentUser.role === 'inspector') {
                    actions += ` <button class="btn btn-sm btn-outline-warning" onclick="showGpsModal(${r.id})">📍 添加位置</button>`;
                }
                // Admin 可见：删除记录按钮（最右侧）
                if (currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin')) {
                    actions += ` <button class="btn btn-sm btn-outline-danger" onclick="showDeleteRecordModal(${r.id}, '${(r.source_name || '记录').replace(/'/g, "\\'")}', '${r.created_at || ''}')">🗑️</button>`;
                }
            }

            return `<tr>
                ${rowSelectCell}
                <td>${r.id}</td><td>${typeLabel}</td>
                <td>${fname}</td>
                <td>${isVideo ? (r.total_frames || 0) : (r.total_detections || 0)}</td>
                <td>${isVideo ? (r.defect_count || 0) : (r.defect_count || 0)}</td>
                <td>${r.alert_triggered || r.has_alert ? `<span class="badge bg-warning">${r.alert_level || r.severity || '预警'}</span>` : '<span class="badge bg-success">正常</span>'}</td>
                <td>${r.has_abnormal ? '<span class="badge bg-warning">⚠</span>' : '-'}</td>
                <td>${isVideo ? (r.duration_seconds ? Math.floor(r.duration_seconds/60)+'m'+Math.round(r.duration_seconds%60)+'s' : '-') : (r.total_time_ms ? r.total_time_ms + 'ms' : '-')}</td>
                <td>${r.created_at ? new Date(r.created_at).toLocaleString() : ''}${gpsLabel}</td>
                <td>${actions}</td>
            </tr>`;
        }).join('');
        updateHistorySelectionUI(records);
        const pgDiv = document.getElementById('history-pagination');
        if (pgDiv) {
            let h = '';
            for (let i = 1; i <= total_pages; i++) h += `<button class="btn btn-sm ${i === page ? 'btn-primary' : 'btn-outline-secondary'}" onclick="loadHistory(${i})">${i}</button>`;
            pgDiv.innerHTML = h;
        }
    } catch (e) { errLog('main', '历史加载失败', e); }
}

async function viewDetail(id) {
    try {
        const d = await apiGet(`/api/history/${id}`);
        if (d.success) {
            const rec = d.data, ai = rec.ai_analysis || {};
            // 兼容旧记录：早期只保存了 abnormal_desc，没有完整 fallback_result
            let fb = rec.fallback_result || null;
            if (!fb && rec.has_abnormal && rec.abnormal_desc) {
                fb = { description: rec.abnormal_desc, is_abnormal: true, confidence: 'low' };
            }
            const img = buildPreviewUrl(rec.annotated_image_path);
            const body = document.getElementById('detail-modal-body');
            if (body) {
                let aiHtml = '';
                const hasAi = !!(rec.ai_analysis && Object.keys(rec.ai_analysis).length > 0);
                if (hasAi) {
                    aiHtml = `<section class="result-block ai-report detail-section mt-3">
                        <h6>🧠 AI 分析报告 <span class="severity-tag severity-${ai.severity || '一般'}">${ai.severity || '一般'}</span></h6>
                        <dl class="detail-kv">
                            <div><dt>描述</dt><dd>${ai.description || '无'}</dd></div>
                            <div><dt>成因</dt><dd>${ai.cause || '无'}</dd></div>
                            <div><dt>建议</dt><dd>${ai.suggestion || '无'}</dd></div>
                        </dl>
                    </section>`;
                }
                const dets = rec.yolo_detections || [];
                let yoloHtml = '';
                if (dets.length) {
                    yoloHtml = `<section class="result-block detail-section mt-3">
                        <h6>🤖 YOLO 检测结果 <span class="detail-count">${dets.length} 个目标</span></h6>
                        <ul class="detail-detection-list">${dets.map(d => {
                            const conf = typeof d.confidence === 'number' ? (d.confidence * 100).toFixed(1) + '%' : (d.confidence || '');
                            const box = d.bbox ? `[(${Math.round(d.bbox.x1)},${Math.round(d.bbox.y1)}) → (${Math.round(d.bbox.x2)},${Math.round(d.bbox.y2)})]` : '';
                            return `<li><span class="detect-name">${d.class_name}</span><span class="detect-conf">${conf}</span>${box ? `<small class="detect-box">${box}</small>` : ''}</li>`;
                        }).join('')}</ul>
                    </section>`;
                } else {
                    yoloHtml = `<section class="result-block detail-section mt-3">
                        <h6>🤖 YOLO 检测结果</h6>
                        <p class="text-muted mb-0">未检测到目标</p>
                    </section>`;
                }
                let fbHtml = '';
                if (fb && (fb.description || fb.is_abnormal)) {
                    const confMap = { high: '高', medium: '中', low: '低' };
                    const confText = confMap[fb.confidence] || fb.confidence || '未知';
                    fbHtml = `<section class="result-block fallback-report detail-section mt-3">
                        <h6>🔍 兜底异物检测 <span class="detail-status ${fb.is_abnormal ? 'is-abnormal' : 'is-normal'}">${fb.is_abnormal ? '异常' : '正常'}</span></h6>
                        <dl class="detail-kv">
                            <div><dt>描述</dt><dd>${fb.description || '无'}</dd></div>
                            <div><dt>置信度</dt><dd>${confText}</dd></div>
                        </dl>
                    </section>`;
                }
                const lat = rec.gps_lat || rec.gps_latitude;
                const lng = rec.gps_lng || rec.gps_longitude;
                const gpsText = (lat && lng) ? `${lat}, ${lng}` : '无';
                const gpsSource = rec.gps_source === 'exif'
                    ? '<span class="gps-source">📍 EXIF</span>'
                    : rec.gps_source === 'manual'
                        ? '<span class="gps-source">✏️ 手动</span>'
                        : '';
                const sourceLabel = rec.source_type === 'video' ? '视频' : '图片';
                const imageHtml = img
                    ? `<div class="detail-image-shell"><img src="${img}" class="detail-image" alt="检测图"></div>`
                    : '<div class="detail-image-shell"><div class="detail-image-empty">暂无检测图片</div></div>';
                const summaryHtml = `<div class="detail-summary">
                    <div class="detail-summary-head">
                        <span class="detail-record-id">记录 #${rec.id}</span>
                        <span class="detail-source-type">${sourceLabel}</span>
                        ${hasAi ? `<span class="severity-tag severity-${ai.severity || '一般'}">${ai.severity || '一般'}</span>` : ''}
                    </div>
                    <div class="detail-facts">
                        <div class="detail-fact"><span class="detail-fact-label">文件名</span><span class="detail-fact-value">${rec.source_name || '-'}</span></div>
                        <div class="detail-fact"><span class="detail-fact-label">GPS 位置</span><span class="detail-fact-value">${gpsText}${gpsSource}</span></div>
                        <div class="detail-fact"><span class="detail-fact-label">目标</span><span class="detail-fact-value">${rec.total_detections || 0}</span></div>
                        <div class="detail-fact"><span class="detail-fact-label">缺陷</span><span class="detail-fact-value">${rec.defect_count || 0}</span></div>
                        <div class="detail-fact"><span class="detail-fact-label">异常</span><span class="detail-fact-value">${rec.has_abnormal ? '是' : '否'}</span></div>
                        <div class="detail-fact"><span class="detail-fact-label">耗时</span><span class="detail-fact-value">${rec.total_time_ms ? rec.total_time_ms + ' ms' : '-'}</span></div>
                    </div>
                </div>`;
                body.innerHTML = `
                    <div class="detail-modal-layout">
                        ${summaryHtml}
                        <div class="row g-4 align-items-start">
                            <div class="col-lg-5">${imageHtml}</div>
                            <div class="col-lg-7">
                                ${yoloHtml}
                                ${aiHtml}
                                ${fbHtml}
                            </div>
                        </div>
                    </div>`;
            }
            ModalManager.open('detail-modal');
        }
    } catch (e) { errLog('main', '查看详情失败', e); }
}

function showToast(ad) {
    const c = document.getElementById('alert-toast');
    if (!c) return;
    const div = document.createElement('div');
    div.className = `toast-item ${ad.level === 'critical' ? 'critical' : 'warning'}`;
    div.innerHTML = `${ad.level === 'critical' ? '🚨' : '⚠️'} ${ad.message}`;
    c.appendChild(div);
    setTimeout(() => div.remove(), 5000);
}

async function checkStatus() {
    try {
        const d = await apiGet('/api/status');
        if (d.success) {
            const badge = document.getElementById('gpu-badge');
            if (badge) badge.textContent = d.data.model_loaded ? '模型已加载' : '模型未加载';
        }
    } catch (e) { /* 静默 */ }
}

window.addEventListener('beforeunload', stopCamera);
