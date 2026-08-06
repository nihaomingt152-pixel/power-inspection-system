/**
 * map.js - GIS 地图 Leaflet（加固版）。
 */
console.log('[map.js] 已加载');

let map;

document.addEventListener('DOMContentLoaded', async () => {
    log('map', '开始初始化...');

    // 认证
    try {
        const user = await getCurrentUser();
        if (user) {
            const el = document.getElementById('nav-user');
            if (el) el.textContent = `👤 ${user.full_name || user.username} (${user.role})`;
            renderNavMenu(user, 'map');
        }
    } catch (e) { errLog('map', '认证失败', e); }

    // 地图
    try {
        map = L.map('map').setView([35.86, 104.19], 5);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap', maxZoom: 18,
        }).addTo(map);
        log('map', '地图底图已加载');
    } catch (e) {
        errLog('map', '地图初始化失败', e);
        return;
    }

    // 加载标记
    try {
        await loadMarkers();
    } catch (e) { errLog('map', '标记加载失败', e); }

    log('map', '初始化完成');
});

async function loadMarkers() {
    const d = await apiGet('/api/map/records');
    const records = d.data?.records || [];
    const orders = d.data?.orders || [];
    if (!records.length && !orders.length) {
        log('map', '无 GPS 数据');
        return;
    }

    const colors = { '一般': '#4caf50', '严重': '#ff9800', '紧急': '#f44336' };
    const bounds = [];

    // 检测记录标记（彩色圆点）
    records.forEach(m => {
        if (!m.lat || !m.lng) return;
        const color = colors[m.severity] || '#999';
        const popup = `
            <div style="min-width:200px;">
                <strong>${m.class_name}</strong>
                <span style="background:${color};color:#fff;padding:1px 6px;border-radius:8px;font-size:11px;">${m.severity}</span>
                <p style="margin:4px 0;font-size:12px;">${m.created_at ? new Date(m.created_at).toLocaleString() : '-'}</p>
                ${m.annotated_image_path ? `<img src="${buildPreviewUrl(m.annotated_image_path)}" style="width:100%;max-height:120px;object-fit:contain;border-radius:4px;">` : ''}
                <br><a href="/orders" style="font-size:12px;">查看工单 &rarr;</a>
            </div>`;

        L.circleMarker([m.lat, m.lng], {
            radius: 8, fillColor: color, color: '#fff', weight: 2, fillOpacity: 0.85,
        }).addTo(map).bindPopup(popup);

        bounds.push([m.lat, m.lng]);
    });

    // 未闭环工单标记（Phase 29：📌 图钉 + 状态徽标，与检测记录圆点区分）
    orders.forEach(o => {
        if (!o.lat || !o.lng) return;
        const color = colors[o.severity] || '#999';
        const icon = L.divIcon({
            className: '',
            html: `<div style="width:26px;height:26px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 8px rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff;">📌</div>`,
            iconSize: [26, 26], iconAnchor: [13, 13],
        });
        const popup = `
            <div style="min-width:220px;">
                <strong>📋 工单 #${o.id} - ${o.title}</strong>
                <div style="margin-top:4px;">
                    <span style="background:${color};color:#fff;padding:1px 6px;border-radius:8px;font-size:11px;">${o.status_text}</span>
                    <span style="font-size:11px;color:#555;"> 严重度: ${o.severity}</span>
                </div>
                <p style="margin:4px 0;font-size:12px;">👷 检修人: ${o.assignee || '未指派'}</p>
                <p style="margin:4px 0;font-size:12px;">${o.created_at ? new Date(o.created_at).toLocaleString() : '-'}</p>
                ${o.annotated_image_path ? `<img src="${buildPreviewUrl(o.annotated_image_path)}" style="width:100%;max-height:100px;object-fit:contain;border-radius:4px;">` : ''}
                <br><a href="/orders" style="font-size:12px;">去工单管理 &rarr;</a>
            </div>`;
        L.marker([o.lat, o.lng], { icon }).addTo(map).bindPopup(popup);
        bounds.push([o.lat, o.lng]);
    });

    if (bounds.length) {
        map.fitBounds(bounds, { padding: [40, 40] });
    }
    log('map', `已加载 ${records.length} 个检测标记 + ${orders.length} 个未闭环工单标记`);
}
