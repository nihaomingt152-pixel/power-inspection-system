/**
 * orders.js - 工单管理（加固版）。
 */
console.log('[orders.js] 已加载');

let currentUser = null, orderPage = 1, detailModal = null;
let currentDetailId = null;        // 当前查看的工单 ID
let currentRejectOrderId = null;   // 当前要驳回的工单 ID
let currentDeleteOrderId = null;   // 当前要删除的工单 ID
let selectedOrderIds = new Set();       // 勾选的工单 ID
let currentOrderPageRecords = [];       // 当前页工单记录，用于全选/取消全选

// Phase 7.2: 工单自动刷新（10秒，静默更新）
let orderRefreshTimer = null;
let lastOrderSignature = null;     // 最近一次列表数据签名（用于判断是否有变化）
const ORDER_REFRESH_INTERVAL = 10000;

const REJECT_REASONS = ['现场情况不符', '备件不足', '需要停电作业', '天气原因无法作业', '其他'];

const STATUS_MAP = {
    pending: { text: '待派发', cls: 'badge bg-secondary' },
    processing: { text: '处理中', cls: 'badge bg-warning text-dark' },
    pending_review: { text: '待复检', cls: 'badge bg-primary' },
    rejected: { text: '已驳回', cls: 'badge bg-danger' },
    closed: { text: '已闭环', cls: 'badge bg-success' },
};

document.addEventListener('DOMContentLoaded', async () => {
    log('orders', '开始初始化...');

    // 认证
    try {
        currentUser = await getCurrentUser();
        if (!currentUser) {
            warn('orders', '未登录，跳转登录页');
            window.location.href = '/login';
            return;
        }
        const el = document.getElementById('nav-user');
        if (el) el.textContent = `👤 ${currentUser.full_name || currentUser.username} (${currentUser.role})`;
        renderNavMenu(currentUser, 'orders');
        log('orders', `用户: ${currentUser.username} (${currentUser.role})`);
    } catch (e) {
        errLog('orders', '认证失败', e);
        window.location.href = '/login';
        return;
    }

    // 弹窗
    try {
        const modalEl = document.getElementById('order-detail-modal');
        if (modalEl) detailModal = new bootstrap.Modal(modalEl);
    } catch (e) { errLog('orders', '弹窗初始化失败', e); }

    // 加载列表
    try { await loadOrders(); } catch (e) { errLog('orders', '列表加载失败', e); }

    // Phase 7.2: 启动工单自动刷新（页面可见时）
    startOrderAutoRefresh();
    document.addEventListener('visibilitychange', handleOrderVisibilityChange);

    // 用户打开/关闭弹窗时暂停/恢复自动刷新（防止干扰编辑操作）
    const modalEl = document.getElementById('order-detail-modal');
    if (modalEl) {
        modalEl.addEventListener('show.bs.modal', () => stopOrderAutoRefresh());
        modalEl.addEventListener('hidden.bs.modal', () => {
            hide('btn-export-report');   // 关闭详情后隐藏右下角导出按钮
            startOrderAutoRefresh();
        });
    }

    log('orders', '初始化完成');
});

// ===== Phase 7.2: 工单自动刷新 =====

function startOrderAutoRefresh() {
    if (orderRefreshTimer) return;
    orderRefreshTimer = setInterval(() => {
        // 冲突处理：任何弹窗打开时暂停刷新（防止覆盖用户正在编辑的表单）
        if (document.querySelector('.modal.show')) return;
        try { loadOrders(orderPage, true); } catch (e) { errLog('orders', '自动刷新失败', e); }
    }, ORDER_REFRESH_INTERVAL);
}

function stopOrderAutoRefresh() {
    if (orderRefreshTimer) {
        clearInterval(orderRefreshTimer);
        orderRefreshTimer = null;
    }
}

// 页面切到后台停止刷新，切回前台立即刷新一次并恢复定时器（节省资源）
function handleOrderVisibilityChange() {
    if (document.hidden) {
        stopOrderAutoRefresh();
    } else {
        startOrderAutoRefresh();
        try { loadOrders(orderPage, true); } catch (e) { errLog('orders', '恢复刷新失败', e); }
    }
}

// 手动刷新按钮：显示旋转动画 + 立即刷新（带 spinner）
function manualRefreshOrders(btn) {
    showRefreshSpinner(btn);
    loadOrders(orderPage);
}

// ===== Phase 7.2: 导出工单闭环报告 =====
async function exportOrderReport(orderId) {
    const id = orderId || currentDetailId;
    if (!id) return;
    try {
        const res = await fetch(`/api/orders/${id}/export-report`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.detail || '导出失败');
            return;
        }
        // 从 Content-Disposition 解析文件名（RFC 5987 编码），解析失败用默认名
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename\*?=(?:UTF-8''|"|)([^";]+)/i);
        const filename = match ? decodeURIComponent(match[1]) : `工单报告_#${id}.docx`;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        notify(`工单 #${id} 报告已导出`, 'success');
    } catch (e) {
        errLog('orders', '导出失败', e);
        alert('导出失败: ' + e.message);
    }
}

async function loadOrders(page = 1, auto = false) {
    orderPage = page;
    const status = document.getElementById('order-status-filter')?.value || '';
    // 移动端每页 10 条，减少公网传输量（策略3）
    let url = `/api/orders?page=${page}&page_size=${isMobile() ? 10 : 15}`;
    if (status) url += `&status=${status}`;

    const d = await apiGet(url);
    if (!d.success) return;
    const { records, total, page: cur, total_pages } = d.data;
    const tbody = document.getElementById('orders-tbody');
    if (!tbody) return;

    // 静默刷新优化：数据无变化时跳过渲染，避免页面闪烁/重置滚动位置
    const sig = records.map(r => r.id).join(',') + '|' + total;
    if (auto && sig === lastOrderSignature) return;
    lastOrderSignature = sig;

    currentOrderPageRecords = records;
    const canManage = currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin');
    const colSpan = canManage ? 9 : 8;
    if (!records.length) {
        tbody.innerHTML = `<tr><td colspan="${colSpan}" class="text-center">暂无工单</td></tr>`;
        updateOrderSelectionUI();
    } else {
        const selectAllTh = document.getElementById('orders-select-all-th');
        if (selectAllTh) selectAllTh.classList.toggle('d-none', !canManage);
        tbody.innerHTML = records.map(r => {
            const s = STATUS_MAP[r.status] || { text: r.status, cls: 'badge bg-light' };
            const rowSelectCell = canManage
                ? `<td class="text-center"><input type="checkbox" class="form-check-input order-row-check" data-id="${r.id}" ${selectedOrderIds.has(r.id) ? 'checked' : ''} onchange="toggleOrderRow(this)"></td>`
                : '';
            return `<tr>
                ${rowSelectCell}
                <td>${r.id}</td><td>${r.title}</td>
                <td><span class="severity-tag severity-${r.severity}">${r.severity}</span></td>
                <td><span class="${s.cls}">${s.text}</span></td>
                <td>${r.creator_name || '#' + r.created_by}</td>
                <td>${r.assignee_name || '#' + r.assigned_to}</td>
                <td>${r.created_at ? new Date(r.created_at).toLocaleString() : ''}</td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="showOrderDetail(${r.id})">详情</button>
                    ${(currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin')) ? `<button class="btn btn-sm btn-outline-danger" onclick="showDeleteOrderModal(${r.id}, '${r.title.replace(/'/g, "\\'")}')">🗑️</button>` : ''}
                </td>
            </tr>`;
        }).join('');
        updateOrderSelectionUI(records);
    }

    const pgDiv = document.getElementById('orders-pagination');
    if (pgDiv) {
        let h = '';
        for (let i = 1; i <= total_pages; i++) {
            h += `<button class="btn btn-sm ${i === cur ? 'btn-primary' : 'btn-outline-secondary'}" onclick="loadOrders(${i})">${i}</button>`;
        }
        pgDiv.innerHTML = h;
    }
    log('orders', `列表加载完成: ${records.length} 条`);
}

async function showOrderDetail(id) {
    try {
        currentDetailId = id;
        const d = await apiGet(`/api/orders/${id}`);
        if (!d.success) return;
        const r = d.data;
        const s = STATUS_MAP[r.status] || { text: r.status, cls: '' };

        // 左右并排对比图片
        let imgHtml = '';
        const origImg = buildPreviewUrl(r.annotated_image_path) || null;
        const repairImg = buildPreviewUrl(r.repair_image_path) || null;

        if (origImg || repairImg) {
            imgHtml = '<div class="row mb-3">';
            if (origImg) {
                imgHtml += `<div class="col-md-6 mb-2"><h6>🔍 原始缺陷图（YOLO标注）</h6>
                    <img src="${origImg}" class="compare-img" onclick="zoomImage('${origImg}')" title="点击放大"></div>`;
            }
            if (repairImg) {
                imgHtml += `<div class="col-md-6 mb-2"><h6>🔧 修复照片（Worker提交）</h6>
                    <img src="${repairImg}" class="compare-img" onclick="zoomImage('${repairImg}')" title="点击放大"></div>`;
            }
            imgHtml += '</div>';
        }

        // Worker 备注
        if (r.review_remark) {
            imgHtml += `<div class="alert alert-info"><strong>📝 Worker 备注:</strong> ${r.review_remark}</div>`;
        }

        // AI 分析摘要（派发时保存：图片记录=ai_analysis，视频帧=video_summary）
        let aiSummaryHtml = '';
        if (r.ai_summary) {
            const vs = r.ai_summary;
            const overall = vs.overall_description || vs.description || '';
            const risk = vs.risk_level || vs.severity || '';
            const riskClass = { '低': 'success', '中': 'warning', '高': 'danger', '紧急': 'danger', '严重': 'warning' }[risk] || 'secondary';
            if (overall || risk) {
                aiSummaryHtml = `<div class="alert alert-light border mt-2 mb-2">
                    <h6 class="mb-2">🤖 AI 分析摘要</h6>
                    ${risk ? `<p class="mb-1"><strong>风险等级:</strong> <span class="badge bg-${riskClass}">${risk}</span></p>` : ''}
                    ${overall ? `<p class="mb-1"><strong>${vs.overall_description ? '总体描述' : '描述'}:</strong> ${overall}</p>` : ''}
                    ${vs.suggestions ? `<p class="mb-1"><strong>建议:</strong> ${vs.suggestions}</p>` : ''}
                    ${vs.focus_points ? `<p class="mb-1"><strong>重点关注:</strong> ${vs.focus_points}</p>` : ''}
                    ${vs.cause ? `<p class="mb-1"><strong>成因:</strong> ${vs.cause}</p>` : ''}
                </div>`;
            }
        }

        const body = document.getElementById('order-detail-body');
        if (body) {
            body.innerHTML = `${imgHtml}
                <h5>${r.title} <span class="${s.cls}">${s.text}</span></h5>
                <p><strong>严重程度:</strong> <span class="severity-tag severity-${r.severity}">${r.severity}</span></p>
                <p><strong>描述:</strong> ${r.description || '无'}</p>
                ${aiSummaryHtml}
                <p><strong>创建者:</strong> ${r.creator_name || '#' + r.created_by}</p>
                <p><strong>检修人:</strong> ${r.assignee_name || '#' + r.assigned_to}</p>
                ${r.reject_reason ? `<p><strong>驳回理由:</strong> ${r.reject_reason}</p>` : ''}
                <p><strong>GPS:</strong> ${r.gps_lat ? r.gps_lat + ', ' + r.gps_lng : '无'}</p>
                <p><strong>创建时间:</strong> ${r.created_at ? new Date(r.created_at).toLocaleString() : ''}</p>
                <div id="order-log-timeline"><p class="text-muted">加载操作日志...</p></div>`;
        }

        // 操作按钮
        let actions = '';
        const role = currentUser?.role;
        if (role === 'repairman' && r.assigned_to === currentUser?.id) {
            if (r.status === 'processing') {
                actions += `<button class="btn btn-warning" onclick="submitReview(${r.id})">📤 提交复检</button> `;
                actions += `<button class="btn btn-outline-danger" onclick="showRejectWorkerModal(${r.id})">⛔ 驳回工单</button> `;
            }
            if (r.status === 'rejected') {
                actions += `<button class="btn btn-warning" onclick="submitReview(${r.id})">📤 提交复检</button> `;
            }
        }
        if ((role === 'inspector' || role === 'admin') && r.status === 'pending_review') {
            actions += `<button class="btn btn-success" onclick="approveOrder(${r.id})">✅ 确认闭环</button> `;
            actions += `<button class="btn btn-danger" onclick="rejectOrder(${r.id})">❌ 驳回</button> `;
        }
        // Admin 可重新派发已驳回工单
        if ((role === 'inspector' || role === 'admin') && r.status === 'rejected') {
            actions += `<button class="btn btn-primary" onclick="showReassignModal(${r.id})">📤 重新派发</button> `;
        }
        // Phase 7.2: 已闭环工单 → 详情弹窗内显示导出按钮 + 页面右下角悬浮按钮
        if (r.status === 'closed') {
            actions += `<button class="btn btn-success" onclick="exportOrderReport(${r.id})">📄 导出报告</button> `;
            const fab = document.getElementById('btn-export-report');
            if (fab) show(fab);
        } else {
            hide('btn-export-report');
        }
        const footer = document.getElementById('order-detail-actions');
        if (footer) footer.innerHTML = actions || '<small class="text-muted">当前状态无可用操作</small>';

        if (detailModal) detailModal.show();

        // 异步加载操作日志
        loadOrderLogs(id);
    } catch (e) { errLog('orders', '查看详情失败', e); }
}

// ===== Worker 提交复检（模态框版）=====

let currentSubmitReviewOrderId = null;

function submitReview(orderId) {
    currentSubmitReviewOrderId = orderId;
    document.getElementById('submit-review-order-id').textContent = '#' + orderId;
    document.getElementById('submit-review-remark').value = '';
    document.getElementById('submit-review-file').value = '';
    document.getElementById('submit-review-file-info').textContent = '';
    const modalEl = document.getElementById('submitReviewModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function confirmSubmitReview() {
    const remark = document.getElementById('submit-review-remark')?.value?.trim();
    const fileInput = document.getElementById('submit-review-file');
    const file = fileInput?.files?.[0];

    // 前端验证
    if (!remark) { alert('请填写处理说明/备注'); return; }
    if (!file) { alert('请选择修复照片'); return; }

    // 文件格式验证
    const allowedExts = ['.jpg', '.jpeg', '.png', '.webp'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowedExts.includes(ext)) {
        alert('仅支持 JPG / PNG / WebP 格式的图片');
        return;
    }
    // 文件大小验证（≤ 5MB）
    if (file.size > 5 * 1024 * 1024) {
        alert('文件大小不能超过 5MB');
        return;
    }

    const fd = new FormData();
    fd.append('repair_image', file);
    fd.append('review_remark', remark);

    try {
        const d = await apiPost(`/api/orders/${currentSubmitReviewOrderId}/submit-review`, fd);
        if (d.success) {
            const modalEl = document.getElementById('submitReviewModal');
            if (modalEl) bootstrap.Modal.getInstance(modalEl)?.hide();
            if (detailModal) detailModal.hide();
            await loadOrders();
            notify('已提交复检！', 'success');
            currentSubmitReviewOrderId = null;
        } else alert(d.detail || '失败');
    } catch (e) { errLog('orders', '提交复检失败', e); alert('错误: ' + e.message); }
}

// 文件选择后显示文件名
document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('submit-review-file');
    if (fileInput) {
        fileInput.addEventListener('change', () => {
            const info = document.getElementById('submit-review-file-info');
            const file = fileInput.files[0];
            if (file && info) {
                const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
                info.textContent = `已选择: ${file.name} (${sizeMB} MB)`;
                info.className = sizeMB > 5 ? 'mt-1 text-danger' : 'mt-1 text-success';
            }
        });
    }
});

// ===== Admin 重新派发工单 =====

let currentReassignOrderId = null;

async function showReassignModal(orderId) {
    currentReassignOrderId = orderId;
    try {
        const d = await apiGet(`/api/orders/${orderId}`);
        if (!d.success) return;
        const r = d.data;

        document.getElementById('reassign-order-id').textContent = '#' + orderId;
        document.getElementById('reassign-title').value = r.title || '';
        document.getElementById('reassign-desc').value = r.description || '';

        const sevEl = document.getElementById('reassign-severity');
        if (sevEl) sevEl.value = r.severity || '一般';

        document.getElementById('reassign-reject-reason').textContent = r.reject_reason || '无';

        // 加载检修人列表
        const rd = await apiGet('/api/repairmen');
        const sel = document.getElementById('reassign-assignee');
        if (sel && rd.data) {
            sel.innerHTML = rd.data.map(u =>
                `<option value="${u.id}" ${u.id === r.assigned_to ? 'selected' : ''}>${u.full_name || u.username}</option>`
            ).join('');
        }

        const modalEl = document.getElementById('reassignModal');
        if (modalEl) new bootstrap.Modal(modalEl).show();
    } catch (e) { errLog('orders', '加载重新派发失败', e); }
}

async function confirmReassign() {
    const title = document.getElementById('reassign-title')?.value?.trim();
    const desc = document.getElementById('reassign-desc')?.value?.trim();
    const sev = document.getElementById('reassign-severity')?.value;
    const aid = parseInt(document.getElementById('reassign-assignee')?.value);

    if (!title) { alert('请填写工单标题'); return; }
    if (!aid || isNaN(aid)) { alert('请选择指派人'); return; }

    try {
        const d = await apiPostJson(`/api/orders/${currentReassignOrderId}/reassign`, {
            title: title,
            description: desc || '',
            severity: sev || '一般',
            assigned_to: aid,
        });
        if (d.success) {
            const modalEl = document.getElementById('reassignModal');
            if (modalEl) bootstrap.Modal.getInstance(modalEl)?.hide();
            if (detailModal) detailModal.hide();
            await loadOrders();
            notify(`工单 #${currentReassignOrderId} 已重新派发`, 'success');
            currentReassignOrderId = null;
        } else alert(d.detail || '失败');
    } catch (e) { errLog('orders', '重新派发失败', e); alert('错误: ' + e.message); }
}

async function approveOrder(id) {
    if (!confirm('确认此工单已修复完成，闭环处理？')) return;
    // 闭环备注（可选，会写入导出报告）
    const closeRemark = prompt('请输入闭环备注（可选）：', '');
    if (closeRemark === null) return;   // 用户取消
    const fd = new FormData();
    if (closeRemark.trim()) fd.append('close_remark', closeRemark.trim());
    try {
        const d = await apiPost(`/api/orders/${id}/approve`, fd);
        if (d.success) {
            if (detailModal) detailModal.hide();
            await loadOrders();
            notify('工单已闭环', 'success');
        } else alert(d.detail || '失败');
    } catch (e) { errLog('orders', '闭环失败', e); }
}

async function rejectOrder(id) {
    const reason = prompt('请输入驳回理由：', '需要重新检修');
    if (!reason) return;
    const fd = new FormData(); fd.append('reason', reason);
    try {
        const d = await apiPost(`/api/orders/${id}/reject`, fd);
        if (d.success) {
            if (detailModal) detailModal.hide();
            await loadOrders();
            notify('工单已驳回', 'warning');
        } else alert(d.detail || '失败');
    } catch (e) { errLog('orders', '驳回失败', e); }
}

// ===== Worker 驳回 =====

function showRejectWorkerModal(orderId) {
    currentRejectOrderId = orderId;
    document.getElementById('reject-order-id').textContent = '#' + orderId;
    document.getElementById('reject-reason-select').value = '';
    document.getElementById('reject-remark').value = '';
    document.getElementById('reject-remark-group').classList.add('d-none');
    const modalEl = document.getElementById('rejectWorkerModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

function onRejectReasonChange() {
    const sel = document.getElementById('reject-reason-select');
    const group = document.getElementById('reject-remark-group');
    if (sel.value === '其他') {
        group.classList.remove('d-none');
    } else {
        group.classList.add('d-none');
    }
}

async function confirmRejectWorker() {
    const reason = document.getElementById('reject-reason-select')?.value;
    const remark = document.getElementById('reject-remark')?.value?.trim();

    if (!reason) { alert('请选择驳回原因'); return; }
    if (reason === '其他' && !remark) { alert('选择「其他」时请填写补充说明'); return; }

    const fd = new FormData();
    fd.append('reason', reason);
    if (remark) fd.append('remark', remark);

    try {
        const d = await apiPost(`/api/orders/${currentRejectOrderId}/reject-worker`, fd);
        if (d.success) {
            const modalEl = document.getElementById('rejectWorkerModal');
            if (modalEl) bootstrap.Modal.getInstance(modalEl)?.hide();
            if (detailModal) detailModal.hide();
            await loadOrders();
            notify(`工单 #${currentRejectOrderId} 已驳回`, 'warning');
            currentRejectOrderId = null;
        } else alert(d.detail || '失败');
    } catch (e) { errLog('orders', 'Worker驳回失败', e); }
}

// ===== 工单多选删除（Admin）=====

function updateOrderSelectionUI(records) {
    const btn = document.getElementById('btn-batch-delete-orders');
    const countEl = document.getElementById('orders-selected-count');
    const selectAllEl = document.getElementById('orders-select-all');
    const count = selectedOrderIds.size;
    if (countEl) countEl.textContent = String(count);
    if (btn) {
        const canDelete = currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin');
        btn.classList.toggle('d-none', !(canDelete && count > 0));
    }
    const selectAllTh = document.getElementById('orders-select-all-th');
    if (selectAllTh) {
        const canDelete = currentUser && (currentUser.role === 'inspector' || currentUser.role === 'admin');
        selectAllTh.classList.toggle('d-none', !canDelete);
    }
    if (selectAllEl && Array.isArray(records) && records.length) {
        const selectedCount = records.filter(r => selectedOrderIds.has(r.id)).length;
        selectAllEl.checked = selectedCount === records.length;
        selectAllEl.indeterminate = selectedCount > 0 && selectedCount < records.length;
    } else if (selectAllEl) {
        selectAllEl.checked = false;
        selectAllEl.indeterminate = false;
    }
}

function toggleSelectAllOrders(el) {
    if (!el) return;
    currentOrderPageRecords.forEach(r => {
        if (el.checked) selectedOrderIds.add(r.id);
        else selectedOrderIds.delete(r.id);
    });
    document.querySelectorAll('.order-row-check').forEach(cb => { cb.checked = el.checked; });
    updateOrderSelectionUI(currentOrderPageRecords);
}

function toggleOrderRow(cb) {
    if (!cb?.dataset?.id) return;
    const id = Number(cb.dataset.id);
    if (cb.checked) selectedOrderIds.add(id);
    else selectedOrderIds.delete(id);
    updateOrderSelectionUI(currentOrderPageRecords);
}

function showBatchDeleteOrdersModal() {
    if (!selectedOrderIds.size) return;
    const countEl = document.getElementById('batch-del-orders-count');
    const listEl = document.getElementById('batch-del-orders-list');
    if (countEl) countEl.textContent = selectedOrderIds.size + ' 个';
    if (listEl) listEl.textContent = '将删除工单 ID: ' + Array.from(selectedOrderIds).sort((a, b) => a - b).join('、');

    const btn = document.getElementById('btn-confirm-batch-delete-orders');
    if (btn) {
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);
        newBtn.addEventListener('click', executeBatchDeleteOrders);
    }
    ModalManager.open('batchDeleteOrdersModal');
}

async function executeBatchDeleteOrders() {
    const ids = Array.from(selectedOrderIds);
    if (!ids.length) return;
    try {
        const d = await apiPostJson('/api/orders/batch-delete', { ids });
        selectedOrderIds.clear();
        ModalManager.close('batchDeleteOrdersModal');
        await loadOrders(orderPage);
        notifyDataChanged();
        if (d.success) {
            const failed = d.data?.failed || [];
            if (failed.length) notify(`已删除 ${d.data.deleted} 个工单，失败 ${failed.length} 个`, 'warning');
            else notify(`已删除 ${d.data.deleted} 个工单`, 'warning');
        } else {
            alert(d.detail || '批量删除失败');
        }
    } catch (e) {
        errLog('orders', '批量删除工单失败', e);
        alert('错误: ' + e.message);
    }
}

// ===== Admin 删除工单 =====

function showDeleteOrderModal(orderId, title) {
    currentDeleteOrderId = orderId;
    const body = document.getElementById('delete-order-body');
    if (body) {
        body.innerHTML = `<p>您确定要删除工单 <strong>#${orderId} - ${title}</strong> 吗？</p>
            <p class="text-danger">此操作将永久删除该工单，不可恢复！</p>`;
    }
    // 重新绑定删除按钮
    const btn = document.getElementById('btn-confirm-delete');
    if (btn) {
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);
        newBtn.addEventListener('click', confirmDeleteOrder);
    }
    const modalEl = document.getElementById('deleteOrderModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function confirmDeleteOrder() {
    if (!currentDeleteOrderId) return;
    try {
        const res = await fetch(`/api/orders/${currentDeleteOrderId}/delete`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            const modalEl = document.getElementById('deleteOrderModal');
            if (modalEl) bootstrap.Modal.getInstance(modalEl)?.hide();
            await loadOrders();
            notify(`工单 #${currentDeleteOrderId} 已删除`, 'warning');
            notifyDataChanged();
            currentDeleteOrderId = null;
        } else {
            alert(data.detail || '删除失败');
        }
    } catch (e) { errLog('orders', '删除失败', e); }
}

// ===== 操作日志时间线 =====

async function loadOrderLogs(orderId) {
    const container = document.getElementById('order-log-timeline');
    if (!container) return;
    try {
        const d = await apiGet(`/api/orders/${orderId}/logs`);
        if (!d.success || !d.data?.length) {
            container.innerHTML = '<p class="text-muted mt-3">暂无操作日志</p>';
            return;
        }

        const ACTION_LABELS = {
            created: '📝 创建工单',
            accepted: '✅ 确认接单',
            submitted: '📤 提交复检',
            approved: '✔️ 确认闭环',
            rejected: '❌ 驳回工单',
            deleted: '🗑️ 删除工单',
        };

        container.innerHTML = `<h6 class="mt-3">📋 操作日志</h6><div class="order-timeline">` +
            d.data.map(log => {
                const label = ACTION_LABELS[log.action] || log.action;
                const cls = log.action === 'rejected' ? 'action-rejected'
                    : log.action === 'approved' ? 'action-approved'
                    : log.action === 'deleted' ? 'action-deleted' : '';
                const time = log.created_at ? new Date(log.created_at).toLocaleString() : '';
                return `<div class="timeline-entry ${cls}">
                    <div class="timeline-action">${label}</div>
                    <div class="timeline-time">${time} — ${log.operator_name}</div>
                    ${log.content ? `<div class="timeline-content">${log.content}</div>` : ''}
                </div>`;
            }).join('') +
            `</div>`;
    } catch (e) { errLog('orders', '日志加载失败', e); }
}

// ===== 图片放大 =====

function zoomImage(src) {
    const img = document.getElementById('imageZoomImg');
    if (img) img.src = src;
    const modalEl = document.getElementById('imageZoomModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}
