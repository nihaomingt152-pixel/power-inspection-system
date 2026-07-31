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
    if (!d.success || !d.data?.length) {
        log('map', '无 GPS 数据');
        return;
    }

    const colors = { '一般': '#4caf50', '严重': '#ff9800', '紧急': '#f44336' };
    const bounds = [];

    d.data.forEach(m => {
        if (!m.lat || !m.lng) return;
        const color = colors[m.severity] || '#999';
        const popup = `
            <div style="min-width:200px;">
                <strong>${m.class_name}</strong>
                <span style="background:${color};color:#fff;padding:1px 6px;border-radius:8px;font-size:11px;">${m.severity}</span>
                <p style="margin:4px 0;font-size:12px;">${m.created_at ? new Date(m.created_at).toLocaleString() : '-'}</p>
                ${m.thumbnail ? `<img src="/api/preview/${m.thumbnail}" style="width:100%;max-height:120px;object-fit:contain;border-radius:4px;">` : ''}
                <br><a href="/orders" style="font-size:12px;">查看工单 &rarr;</a>
            </div>`;

        L.circleMarker([m.lat, m.lng], {
            radius: 8, fillColor: color, color: '#fff', weight: 2, fillOpacity: 0.85,
        }).addTo(map).bindPopup(popup);

        bounds.push([m.lat, m.lng]);
    });

    if (bounds.length) {
        map.fitBounds(bounds, { padding: [40, 40] });
    }
    log('map', `已加载 ${d.data.length} 个标记`);
}
