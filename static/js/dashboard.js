/**
 * dashboard.js - 仪表盘 ECharts 图表（加固版）。
 */
console.log('[dashboard.js] 已加载');

let trendChart, pieChart, currentDays = 7;

document.addEventListener('DOMContentLoaded', async () => {
    log('dashboard', '开始初始化...');

    // 认证
    let currentUser = null;
    try {
        currentUser = await getCurrentUser();
        if (currentUser) {
            const el = document.getElementById('nav-user');
            if (el) el.textContent = `👤 ${currentUser.full_name || currentUser.username} (${currentUser.role})`;
            renderNavMenu(currentUser, 'dashboard');
            log('dashboard', `用户: ${currentUser.username}`);
        } else {
            warn('dashboard', '未登录');
        }
    } catch (e) { errLog('dashboard', '认证失败', e); }

    // 图表
    try {
        initCharts();
        await loadData();
        log('dashboard', '图表渲染完成');
    } catch (e) { errLog('dashboard', '图表初始化失败', e); }

    // 跨页面数据变更（批量删除等）后自动刷新，保持卡片与图表同步
    try {
        onDataChanged(() => loadData());
        log('dashboard', '数据变更监听已注册');
    } catch (e) { errLog('dashboard', '数据变更监听注册失败', e); }

    // 周期按钮
    try {
        const btns = document.getElementById('period-btns');
        if (btns) {
            btns.addEventListener('click', e => {
                const btn = e.target.closest('button');
                if (!btn?.dataset.days) return;
                btns.querySelectorAll('button').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentDays = parseInt(btn.dataset.days);
                log('dashboard', `切换周期: ${currentDays}天`);
                loadData();
            });
        }
    } catch (e) { errLog('dashboard', '按钮绑定失败', e); }

    log('dashboard', '初始化完成');
});

function initCharts() {
    const trendEl = document.getElementById('chart-trend');
    const pieEl = document.getElementById('chart-pie');
    if (!trendEl || !pieEl) { warn('dashboard', '未找到图表容器'); return; }
    trendChart = echarts.init(trendEl);
    pieChart = echarts.init(pieEl);
    window.addEventListener('resize', () => { trendChart?.resize(); pieChart?.resize(); });
}

async function loadData() {
    try {
        const d = await apiGet(`/api/dashboard/stats?days=${currentDays}`);
        if (!d.success) { warn('dashboard', 'API 返回失败'); return; }
        const data = d.data;

        // 工单卡片
        const os = data.order_stats || {};
        ['pending', 'processing', 'pending_review', 'closed'].forEach(k => {
            const el = document.getElementById('s-' + k);
            if (el) el.textContent = os[k] || 0;
        });

        // 趋势图
        const trend = data.trend || [];
        if (trendChart) {
            trendChart.setOption({
                color: ['#0071e3'],
                tooltip: {
                    trigger: 'axis',
                    backgroundColor: '#ffffff',
                    borderColor: '#e8e8ed',
                    borderWidth: 1,
                    textStyle: { color: '#1d1d1f', fontSize: 12 },
                    extraCssText: 'box-shadow: 0 10px 32px rgba(0,0,0,0.08); border-radius: 8px;',
                },
                xAxis: {
                    type: 'category',
                    data: trend.map(t => t.date),
                    axisLine: { lineStyle: { color: '#d2d2d7' } },
                    axisTick: { show: false },
                    axisLabel: { color: '#6e6e73', fontSize: 11 },
                },
                yAxis: {
                    type: 'value',
                    name: '缺陷数',
                    nameTextStyle: { color: '#86868b', fontSize: 11 },
                    axisLabel: { color: '#6e6e73', fontSize: 11 },
                    splitLine: { lineStyle: { color: '#ececf1' } },
                },
                series: [{
                    name: '缺陷', type: 'line', smooth: true,
                    data: trend.map(t => t.count),
                    symbol: 'circle',
                    symbolSize: 6,
                    areaStyle: { color: 'rgba(0,113,227,0.10)' },
                    lineStyle: { color: '#0071e3', width: 2.5 },
                    itemStyle: { color: '#0071e3' },
                }],
                grid: { left: 44, right: 20, top: 28, bottom: 34 },
            });
        }

        // 饼图
        const cat = data.category_distribution || {};
        const names = Object.keys(cat);
        if (pieChart && names.length) {
            pieChart.setOption({
                color: ['#0071e3', '#34c759', '#ff9f0a', '#ff3b30', '#86868b', '#af52de'],
                tooltip: {
                    trigger: 'item',
                    backgroundColor: '#ffffff',
                    borderColor: '#e8e8ed',
                    borderWidth: 1,
                    textStyle: { color: '#1d1d1f', fontSize: 12 },
                    extraCssText: 'box-shadow: 0 10px 32px rgba(0,0,0,0.08); border-radius: 8px;',
                },
                series: [{
                    type: 'pie', radius: ['45%', '75%'],
                    data: names.map(n => ({ name: n, value: cat[n] })),
                    label: { formatter: '{b}: {c}', color: '#3a3a3c', fontSize: 12 },
                    labelLine: { lineStyle: { color: '#d2d2d7' } },
                    itemStyle: { borderColor: '#ffffff', borderWidth: 2 },
                }],
            });
        }
        log('dashboard', `数据加载完成 (${currentDays}天)`);
    } catch (e) { errLog('dashboard', '数据加载失败', e); }
}
