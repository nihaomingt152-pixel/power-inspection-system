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
let currentDispatchRecordId = null;  // 当前正在派发的检测记录 ID
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

    log('main', '全部初始化完成');
});

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

    if (files.length === 1) {
        const fd = new FormData(); fd.append('file', files[0]);
        fd.append('call_ai', callAi); fd.append('call_fallback', callFallback);
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
        for (const f of files) {
            const fd = new FormData(); fd.append('file', f);
            fd.append('call_ai', callAi); fd.append('call_fallback', callFallback);
            try {
                const d = await apiPost('/api/upload/image', fd);
                if (d.success) {
                    addBatchCard(d.data, f.name);
                    if (d.data.speech_alert) speakIfNeeded(d.data);
                }
                done++;
            } catch (e) { errLog('main', `批量上传失败: ${f.name}`, e); }
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
    const imgSrc = data.annotated_image_path
        ? '/api/preview/' + data.annotated_image_path.replace(/\\/g, '/').split('/static/uploads/').pop() : '';
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
    const imgSrc = data.annotated_image_path
        ? '/api/preview/' + data.annotated_image_path.replace(/\\/g, '/').split('/static/uploads/').pop() : '';
    const dc = document.getElementById('detail-content');
    if (!dc) return;
    dc.innerHTML = `
        <div class="col-md-6"><canvas id="glow-canvas" style="width:100%;max-height:450px;background:#1a1a2e;"></canvas></div>
        <div class="col-md-6">
            <h5>${fname}</h5>
            <span class="severity-tag severity-${sev} mb-2">${sev}</span>
            ${data.has_abnormal ? '<span class="badge bg-warning ms-1">⚠ 异物: ' + (data.abnormal_desc || '') + '</span>' : ''}
            <p class="mt-2"><strong>描述:</strong> ${ai.description || '无'}</p>
            <p><strong>成因:</strong> ${ai.cause || '无'}</p>
            <p><strong>建议:</strong> ${ai.suggestion || '无'}</p>
            <small class="text-muted">YOLO:${data.timing?.yolo_ms}ms + AI:${data.timing?.ai_ms || '-'}ms = ${data.timing?.total_ms}ms</small>
            ${data.detections?.length ? '<h6 class="mt-2">检测:</h6><ul>' + data.detections.map(d => `<li>${d.class_name} (${(d.confidence * 100).toFixed(1)}%)</li>`).join('') + '</ul>' : ''}
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

        // 填充图片
        const imgEl = document.getElementById('dispatch-img');
        if (imgEl) {
            const imgPath = rec.annotated_image_path
                ? '/api/preview/' + rec.annotated_image_path.replace(/\\/g, '/').split('/static/uploads/').pop()
                : '';
            imgEl.src = imgPath || '';
        }

        // 填充 AI 摘要
        const summaryEl = document.getElementById('dispatch-ai-summary');
        if (summaryEl) {
            const dets = rec.yolo_detections || [];
            const detStr = dets.map(d => `${d.class_name}(${(d.confidence * 100).toFixed(0)}%)`).join(', ') || '无';
            summaryEl.innerHTML = `
                <p><strong>类别:</strong> ${detStr}</p>
                <p><strong>严重程度:</strong> <span class="severity-tag severity-${ai.severity || '未知'}">${ai.severity || '未知'}</span></p>
                <p><strong>描述:</strong> ${ai.description || '无'}</p>
                <p><strong>成因:</strong> ${ai.cause || '无'}</p>
                <p><strong>建议:</strong> ${ai.suggestion || '无'}</p>`;
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
    if (gpsLat !== null) fd.append('gps_lat', gpsLat);
    if (gpsLng !== null) fd.append('gps_lng', gpsLng);

    try {
        const d = await apiPost('/api/orders', fd);
        if (d.success) {
            ModalManager.close('dispatch-modal');
            notify(`工单 #${d.data.id} 已派发！`, 'success');
            log('main', `工单派发成功: #${d.data.id}`);
            currentDispatchRecordId = null;
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
            summaryEl.innerHTML = `总帧数: <strong>${d.data.total_frames}</strong> | 缺陷帧: <strong>${d.data.defect_count}</strong> | 时长: ${durStr} | 等级: ${sevTag}`;
        }

        // 下载链接
        const btnDl = document.getElementById('btn-download-video2');
        if (btnDl) btnDl.href = d.data.output_video_path || '#';

        // 跳转视频
        const btnJump = document.getElementById('btn-jump-video');
        if (btnJump) btnJump.onclick = () => window.open(`/video/play/${d.data.task_id}?time=0`, '_blank');

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
            aiEl.innerHTML = '<p class="text-muted">无 AI 分析（此帧非缺陷帧）</p>';
        }

        // GPS
        const gpsEl = document.getElementById('frameGpsInfo');
        if (gpsEl && currentVideoRecord) {
            const lat = currentVideoRecord.gps_lat;
            const lng = currentVideoRecord.gps_lng;
            const src = currentVideoRecord.gps_source || 'none';
            gpsEl.innerHTML = `GPS: ${lat ? lat + ', ' + lng : '无'} ${src === 'exif' ? '📍 EXIF' : src === 'manual' ? '✏️ 手动' : ''}`;
        }

        // 生成工单按钮（仅缺陷帧 + Admin）
        const btnOrder = document.getElementById('btn-frame-create-order');
        if (btnOrder) {
            if (frame.has_defect && currentUser && currentUser.role === 'inspector') {
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

function createOrderFromFrame() {
    if (!currentVideoRecord || currentFrameIndex === undefined) return;
    // 打开派发模态框（使用视频 GPS 信息作为默认值）
    // 这里简化处理：跳转到派发模态框并预填信息
    const frame = (currentVideoRecord.frames_data || []).find(f => f.frame_index === currentFrameIndex);
    const ai = frame?.ai_analysis || {};

    // 设置派发表单
    const titleEl = document.getElementById('dispatch-title');
    if (titleEl) titleEl.value = `视频${currentVideoRecord.original_filename || ''} 帧#${currentFrameIndex} - ${ai.severity || '一般'}`;

    const descEl = document.getElementById('dispatch-desc');
    if (descEl) descEl.value = ai.description || '';

    const sevEl = document.getElementById('dispatch-severity');
    if (sevEl) sevEl.value = ai.severity || '一般';

    const latEl = document.getElementById('dispatch-gps-lat');
    const lngEl = document.getElementById('dispatch-gps-lng');
    if (latEl) latEl.value = currentVideoRecord.gps_lat || '';
    if (lngEl) lngEl.value = currentVideoRecord.gps_lng || '';

    // 关闭帧详情弹窗，打开派发弹窗
    ModalManager.close('frameDetailModal');
    currentDispatchRecordId = null;  // 视频帧派发不关联 detection_record

    // 加载检修人列表并显示派发弹窗
    apiGet('/api/repairmen').then(d => {
        const sel = document.getElementById('dispatch-assignee');
        if (sel && d.data) sel.innerHTML = d.data.map(u => `<option value="${u.id}">${u.full_name || u.username}</option>`).join('');
    });
    ModalManager.open('dispatch-modal');
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
        } else {
            alert(d.detail || '删除失败');
        }
    } catch (e) { errLog('main', '删除记录失败', e); alert('错误: ' + e.message); }
}

// ===== 视频 =====
async function uploadVideo() {
    log('main', 'uploadVideo 被调用');
    const inp = document.getElementById('file-video');
    const f = inp?.files?.[0];
    if (!f) { alert('请选择视频文件'); return; }
    const fd = new FormData(); fd.append('file', f);
    fd.append('call_ai', document.getElementById('chk-ai-video')?.checked ?? true);
    fd.append('fallback_interval', document.getElementById('fallback-interval')?.value ?? 5);
    try {
        const d = await apiPost('/api/upload/video', fd);
        if (d.success) {
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
    videoPollTimer = setInterval(async () => {
        if (!currentVideoTaskId) { clearInterval(videoPollTimer); return; }
        try {
            const d = await apiGet(`/api/video/progress/${currentVideoTaskId}`);
            if (!d.success) return;
            const t = d.data;
            const fill = document.getElementById('video-progress-fill');
            if (fill) fill.style.width = (t.progress_pct || 0) + '%';
            const txt = document.getElementById('video-progress-text');
            if (txt) txt.textContent = `${t.progress_pct || 0}% (${t.processed_frames}/${t.total_frames})`;
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
                if (t.ai_reports?.length) renderTimeline(t.ai_reports);
                loadHistory();
                log('main', '视频分析完成');
            } else if (t.status === 'failed') { clearInterval(videoPollTimer); }
        } catch (e) { /* 轮询静默 */ }
    }, 1500);
}

function renderTimeline(reports) {
    const card = document.getElementById('video-timeline-card');
    const tl = document.getElementById('ai-timeline');
    if (!card || !tl) return;
    card.classList.remove('d-none');
    tl.innerHTML = reports.map(r => {
        const m = Math.floor(r.timestamp / 60), s = Math.floor(r.timestamp % 60);
        return `<div class="timeline-item severity-${r.severity}" onclick="seekVideo(${r.timestamp})">
            <div class="timeline-time">⏱ ${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')} — <span class="severity-tag severity-${r.severity}">${r.severity}</span></div>
            <div>${(r.description || '').substring(0, 200)}</div></div>`;
    }).join('');
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
async function loadHistory(pg = 1) {
    historyPage = pg;
    const src = document.getElementById('history-filter')?.value || '';
    const al = document.getElementById('alert-only')?.checked || false;
    let url = `/api/history?page=${pg}&page_size=15`;
    if (src) url += `&source_type=${src}`;
    if (al) url += `&alert_only=true`;
    try {
        const d = await apiGet(url);
        if (!d.success) return;
        const { records, total, page, total_pages } = d.data;
        const tb = document.getElementById('history-tbody');
        if (!tb) return;
        if (!records.length) { tb.innerHTML = '<tr><td colspan="10" class="text-center">暂无记录</td></tr>'; return; }
        tb.innerHTML = records.map(r => {
            const recType = r.record_type || 'image';
            const isVideo = recType === 'video';

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
            const img = rec.annotated_image_path ? '/api/preview/' + rec.annotated_image_path.replace(/\\/g, '/').split('/static/uploads/').pop() : '';
            const body = document.getElementById('detail-modal-body');
            if (body) {
                body.innerHTML = `<div class="row"><div class="col-md-6">${img ? `<img src="${img}" class="img-fluid rounded">` : ''}</div>
                    <div class="col-md-6"><p>ID:${rec.id} | ${rec.source_type} | ${rec.source_name || ''}</p>
                    <p>GPS:${rec.gps_lat || rec.gps_latitude || '无'},${rec.gps_lng || rec.gps_longitude || ''}
                        ${rec.gps_source === 'exif' ? ' <small class="text-success">📍 EXIF</small>' : ''}
                        ${rec.gps_source === 'manual' ? ' <small class="text-primary">✏️ 手动</small>' : ''}</p>
                    <p>目标:${rec.total_detections} | 缺陷:${rec.defect_count} | 异常:${rec.has_abnormal ? '是' : '否'}</p>
                    <p>${ai.description || '无AI分析'}</p></div></div>`;
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
