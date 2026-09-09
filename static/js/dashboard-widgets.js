/**
 * Shared widget rendering for dashboard builder and public view.
 * Expects Chart.js on window.Chart when charts are rendered.
 */
(function (global) {
  'use strict';

  const charts = new Map();

  function esc(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function mean(arr) {
    return arr.reduce((a, b) => a + b, 0) / arr.length;
  }

  function median(arr) {
    const s = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function parseNumbers(str) {
    return String(str || '')
      .split(/[,;\s]+/)
      .map((x) => parseFloat(x))
      .filter((n) => Number.isFinite(n));
  }

  function destroyChart(key) {
    const existing = charts.get(key);
    if (existing) {
      existing.destroy();
      charts.delete(key);
    }
  }

  function resolveChartSpec(element) {
    const content = element.content || {};
    const chartType = element.chartType || 'bar';
    let type = 'bar';
    const options = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      layout: {
        padding: { top: 32, right: 16, bottom: 8, left: 8 },
      },
    };
    let datasets = (content.datasets || []).map((ds) => {
      const copy = { ...ds };
      if (Array.isArray(copy.data)) copy.data = [...copy.data];
      return copy;
    });
    let labels = [...(content.labels || [])];

    switch (chartType) {
      case 'bar-horizontal':
        type = 'bar';
        options.indexAxis = 'y';
        options.layout.padding = { top: 12, right: 36, bottom: 8, left: 8 };
        break;
      case 'bar-stacked':
        type = 'bar';
        options.scales = {
          x: { stacked: true },
          y: { stacked: true, grace: '12%' },
        };
        break;
      case 'line':
        type = 'line';
        datasets = datasets.map((ds) => ({
          ...ds,
          fill: false,
          tension: 0.35,
          pointRadius: 3,
          borderWidth: 2,
        }));
        options.scales = { y: { grace: '15%' } };
        break;
      case 'area':
        type = 'line';
        datasets = datasets.map((ds, i) => {
          const color = Array.isArray(ds.borderColor) ? ds.borderColor[0] : (ds.borderColor || ds.backgroundColor || '#60a5fa');
          const solid = Array.isArray(color) ? color[0] : color;
          return {
            ...ds,
            fill: true,
            tension: 0.35,
            backgroundColor: typeof solid === 'string' && solid.startsWith('#')
              ? `${solid}55`
              : 'rgba(96,165,250,0.35)',
            borderColor: solid,
            pointRadius: 2,
          };
        });
        options.scales = { y: { grace: '15%' } };
        break;
      case 'pie':
        type = 'pie';
        break;
      case 'doughnut':
        type = 'doughnut';
        break;
      case 'radar':
        type = 'radar';
        datasets = datasets.map((ds) => ({
          ...ds,
          fill: true,
          backgroundColor: Array.isArray(ds.backgroundColor)
            ? `${String(ds.backgroundColor[0])}44`
            : 'rgba(16,185,129,0.25)',
        }));
        break;
      case 'polarArea':
        type = 'polarArea';
        break;
      case 'scatter': {
        type = 'scatter';
        datasets = datasets.map((ds) => {
          const vals = ds.data || [];
          const points = vals.map((y, i) => (
            y && typeof y === 'object' && 'x' in y
              ? y
              : { x: i + 1, y: Number(y) || 0 }
          ));
          return {
            ...ds,
            data: points,
            showLine: false,
            pointRadius: 5,
            backgroundColor: Array.isArray(ds.backgroundColor) ? ds.backgroundColor[0] : (ds.backgroundColor || '#60a5fa'),
          };
        });
        labels = [];
        options.scales = {
          x: { type: 'linear', title: { display: true, text: 'X' } },
          y: { grace: '15%', title: { display: true, text: 'Y' } },
        };
        break;
      }
      case 'bubble': {
        type = 'bubble';
        datasets = datasets.map((ds) => {
          const vals = ds.data || [];
          const points = vals.map((y, i) => (
            y && typeof y === 'object' && 'x' in y
              ? y
              : { x: i + 1, y: Number(y) || 0, r: 8 + (Number(y) || 0) / 10 }
          ));
          return { ...ds, data: points };
        });
        labels = [];
        options.scales = { x: { type: 'linear' }, y: { grace: '15%' } };
        break;
      }
      case 'bar':
      default:
        type = 'bar';
        options.scales = { y: { beginAtZero: true, grace: '15%' } };
        break;
    }

    return { type, labels, datasets, options };
  }

  function buildDatalabelOptions(chartType, options) {
    if (!options.showDataLabels) return { display: false };
    const isHorizontal = chartType === 'bar-horizontal';
    const isRound = chartType === 'pie' || chartType === 'doughnut' || chartType === 'polarArea';
    const pos = options.dataLabelsPosition || 'top';
    let anchor = 'end';
    let align = 'top';
    if (isRound) {
      anchor = 'center';
      align = 'center';
    } else if (isHorizontal) {
      anchor = 'end';
      align = pos === 'center' ? 'center' : 'right';
    } else if (pos === 'center') {
      anchor = 'center';
      align = 'center';
    } else if (pos === 'bottom') {
      anchor = 'start';
      align = 'bottom';
    } else {
      anchor = 'end';
      align = 'top';
    }
    return {
      display: true,
      clip: false,
      clamp: false,
      color: options.dataLabelsColor || '#334155',
      font: { weight: '600', size: 11 },
      anchor,
      align,
      offset: isRound ? 0 : 6,
      formatter: (v) => {
        if (v == null) return '';
        if (typeof v === 'object' && 'y' in v) return v.y;
        return v;
      },
    };
  }

  function renderChart(canvas, element, key) {
    if (!global.Chart || !canvas) return;
    destroyChart(key);
    const userOpts = element.options || (element.settings && element.settings.options) || {};
    const spec = resolveChartSpec(element);
    const plugins = [];
    if (global.ChartDataLabels) {
      try {
        if (global.Chart.registry && !global.Chart.registry.plugins.get('datalabels')) {
          global.Chart.register(global.ChartDataLabels);
        }
      } catch (_) { /* already registered */ }
      plugins.push(global.ChartDataLabels);
    }

    const chart = new global.Chart(canvas.getContext('2d'), {
      type: spec.type,
      data: {
        labels: spec.labels,
        datasets: spec.datasets,
      },
      options: {
        ...spec.options,
        plugins: {
          ...(spec.options.plugins || {}),
          legend: { display: true, position: 'bottom', labels: { boxWidth: 12, padding: 10 } },
          datalabels: buildDatalabelOptions(element.chartType || 'bar', userOpts),
        },
      },
      plugins,
    });
    charts.set(key, chart);
    return chart;
  }

  function sparklineSvg(data, color, showArea) {
    const values = Array.isArray(data) ? data.map(Number).filter(Number.isFinite) : [];
    if (!values.length) return '<div class="dw-text">Нет данных</div>';
    const max = Math.max(...values);
    const min = Math.min(...values);
    const range = max === min ? 1 : max - min;
    const pts = values.map((v, i) => {
      const x = values.length === 1 ? 0 : (i / (values.length - 1)) * 100;
      const y = 48 - ((v - min) / range) * 42;
      return `${x},${y}`;
    });
    const poly = pts.join(' ');
    const area = showArea
      ? `<polygon points="${poly} 100,50 0,50" fill="${esc(color)}" opacity="0.12"></polygon>`
      : '';
    return `<svg class="dw-sparkline" viewBox="0 0 100 50" preserveAspectRatio="none">${area}<polyline fill="none" stroke="${esc(color)}" stroke-width="2" points="${poly}"></polyline></svg>`;
  }

  function renderContentHtml(element) {
    const c = element.content || {};
    const type = element.type;

    switch (type) {
      case 'chart':
        return `<div class="dw-chart"><canvas data-chart-id="${esc(element.id)}"></canvas></div>`;
      case 'text':
        return `<div class="dw-text" style="font-size:${esc((element.settings || {}).textSize || '14px')};color:${esc((element.settings || {}).textColor || '#475569')}">${esc(c.text || '')}</div>`;
      case 'list': {
        const items = c.items || [];
        const style = c.listStyle || 'disc';
        const ordered = /^(decimal|lower|upper)/.test(style);
        const tag = ordered ? 'ol' : 'ul';
        return `<${tag} style="list-style-type:${esc(style)};margin:0;padding-left:1.2rem">${items.map((it) => `<li>${esc(it.text || it)}</li>`).join('')}</${tag}>`;
      }
      case 'table': {
        const headers = c.headers || [];
        const rows = c.rows || [];
        const style = c.tableStyle || 'default';
        const editable = !!(element._editableTable);
        if (editable) {
          return `<div class="dw-table-wrap"><table class="dw-table dw-table--${esc(style)} dw-table--edit" data-editable-table="${esc(element.id)}">
            <thead><tr>${headers.map((h, ci) => `<th contenteditable="true" data-r="-1" data-c="${ci}">${esc(h)}</th>`).join('')}</tr></thead>
            <tbody>${rows.map((r, ri) => `<tr>${(r || []).map((cell, ci) => `<td contenteditable="true" data-r="${ri}" data-c="${ci}">${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody>
          </table></div>`;
        }
        return `<div class="dw-table-wrap"><table class="dw-table dw-table--${esc(style)}"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${(r || []).map((cell) => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
      }
      case 'gauge': {
        const value = Number(c.value || 0);
        const max = Number(c.max || 100) || 100;
        const pct = Math.max(0, Math.min(100, (value / max) * 100));
        return `<div><div class="dw-gauge-value" style="color:${esc(c.color || '#10b981')}">${esc(value)}</div><div class="dw-gauge-label">${esc(c.label || '')}</div><div class="dw-bar"><span style="width:${pct}%;background:${esc(c.color || '#10b981')}"></span></div></div>`;
      }
      case 'progress':
        return `<div>${(c.items || []).map((it) => `<div class="dw-progress-item"><div class="dw-progress-head"><span>${esc(it.label || '')}</span><span>${esc(it.value || 0)}%</span></div><div class="dw-bar"><span style="width:${Number(it.value) || 0}%;background:${esc(c.color || '#10b981')}"></span></div></div>`).join('')}</div>`;
      case 'sparkline':
        return sparklineSvg(c.data || [], c.color || '#10b981', !!c.showArea);
      case 'slicer':
        return `<div class="dw-slicer" data-slicer-id="${esc(element.id)}">${(c.values || []).map((v) => `<button type="button" data-value="${esc(v)}" class="${v === c.activeValue ? 'active' : ''}">${esc(v)}</button>`).join('')}</div>`;
      case 'matrix': {
        const data = c.data || [];
        return `<div class="dw-center"><span style="font-size:1.4rem;margin-right:0.35rem">[</span><table class="dw-matrix">${data.map((row) => `<tr>${(row || []).map((cell) => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</table><span style="font-size:1.4rem;margin-left:0.35rem">]</span></div>`;
      }
      case 'formula':
        return `<div class="dw-formula">${esc(c.expression || '')}</div><div class="dw-chips">${(c.variables || []).map((v) => `<span class="dw-chip">${esc(v)}</span>`).join('')}</div>`;
      case 'statistics': {
        const nums = parseNumbers(c.data);
        if (!nums.length) return '<div class="dw-text">Нет чисел</div>';
        const stats = [
          ['Среднее', mean(nums).toFixed(2)],
          ['Медиана', median(nums).toFixed(2)],
          ['Мин', Math.min(...nums)],
          ['Макс', Math.max(...nums)],
          ['Сумма', nums.reduce((a, b) => a + b, 0)],
          ['N', nums.length],
        ];
        return `<div class="dw-text" style="margin-bottom:0.5rem;font-family:monospace">${esc(c.data || '')}</div><div class="dw-stats">${stats.map(([l, v]) => `<div class="dw-stat"><div class="l">${l}</div><div class="v">${esc(v)}</div></div>`).join('')}</div>`;
      }
      case 'percentage': {
        const part = Number(c.part || 25);
        const whole = Number(c.whole || 100) || 1;
        const result = ((part / whole) * 100).toFixed(1);
        return `<div class="dw-center" style="flex-direction:column;gap:0.4rem"><div class="dw-text">${esc(part)} из ${esc(whole)}</div><div class="dw-gauge-value" style="color:${esc(c.color || '#10b981')}">${result}%</div></div>`;
      }
      case 'ratio': {
        const a = Number(c.a || 1);
        const b = Number(c.b || 1) || 1;
        const simplified = (a / b).toFixed(2);
        return `<div class="dw-center" style="flex-direction:column;gap:0.35rem"><div class="dw-gauge-value" style="font-size:1.4rem">${esc(a)} : ${esc(b)}</div><div class="dw-text">= ${simplified}</div></div>`;
      }
      case 'function':
        return `<div class="dw-formula">y = ${esc(c.expression || 'sin(x)')}</div><canvas class="dw-function-canvas" data-fn-id="${esc(element.id)}" width="320" height="120" style="width:100%;height:120px;margin-top:0.5rem;border:1px solid var(--border,#e2e8f0);border-radius:8px"></canvas>`;
      case 'comparison':
        return `<div>${(c.items || []).map((it) => {
          const max = Math.max(Number(it.value1) || 0, Number(it.value2) || 0, 1);
          return `<div class="dw-compare-row"><div class="dw-compare-label">${esc(it.label || '')}</div><div class="dw-compare-bars"><span style="width:${((Number(it.value1) || 0) / max) * 100}%;background:${esc(c.color1 || '#60a5fa')}"></span><span style="width:${((Number(it.value2) || 0) / max) * 100}%;background:${esc(c.color2 || '#f59e0b')}"></span></div></div>`;
        }).join('')}</div>`;
      case 'timeline':
        return `<div>${(c.items || []).map((it) => `<div class="dw-timeline-row"><div class="dw-timeline-label">${esc(it.label || '')}</div><div class="dw-timeline-line" style="opacity:${0.35 + Math.min(1, (Number(it.value) || 0) / 40)}"></div><div class="dw-timeline-label">${esc(it.value || 0)}</div></div>`).join('')}</div>`;
      case 'icon':
        return `<div class="dw-center"><i class="fas ${esc(c.name || 'fa-star')}" style="font-size:${Number(c.size) || 48}px;color:${esc(c.color || '#10b981')}"></i></div>`;
      case 'badge':
        return `<div class="dw-center"><span class="dw-badge ${esc(c.type || 'primary')}" style="font-size:${Number(c.size) || 14}px">${esc(c.text || 'Бейдж')}</span></div>`;
      default:
        return `<div class="dw-text">Тип «${esc(type)}» не поддерживается</div>`;
    }
  }

  function drawFunction(canvas, element) {
    if (!canvas) return;
    const c = element.content || {};
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = '#e2e8f0';
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2, h);
    ctx.stroke();

    const expr = String(c.expression || 'sin(x)').toLowerCase();
    const xMin = Number(c.xMin ?? -5);
    const xMax = Number(c.xMax ?? 5);
    const fn = (x) => {
      try {
        // eslint-disable-next-line no-new-func
        return Function('x', `with (Math) { return (${expr}); }`)(x);
      } catch (_) {
        return NaN;
      }
    };
    ctx.strokeStyle = c.color || '#10b981';
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i <= w; i++) {
      const x = xMin + (i / w) * (xMax - xMin);
      const y = fn(x);
      if (!Number.isFinite(y)) {
        started = false;
        continue;
      }
      const py = h / 2 - y * (h / 8);
      if (!started) {
        ctx.moveTo(i, py);
        started = true;
      } else {
        ctx.lineTo(i, py);
      }
    }
    ctx.stroke();
  }

  function readEditableTable(table) {
    if (!table) return { headers: [], rows: [] };
    const headers = Array.from(table.querySelectorAll('thead th')).map((th) => th.textContent.trim());
    const rows = Array.from(table.querySelectorAll('tbody tr')).map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => td.textContent.trim())
    );
    return { headers, rows };
  }

  function mountWidget(container, element, opts = {}) {
    const {
      interactiveSlicer = false,
      onSlicer,
      editableTable = false,
      onTableChange,
    } = opts;
    if (!container) return;

    const renderEl = { ...element, _editableTable: !!editableTable && element.type === 'table' };
    container.innerHTML = renderContentHtml(renderEl);

    if (element.type === 'chart') {
      const canvas = container.querySelector('canvas[data-chart-id]');
      renderChart(canvas, element, String(element.id));
    }
    if (element.type === 'function') {
      drawFunction(container.querySelector('canvas[data-fn-id]'), element);
    }
    if (element.type === 'slicer' && interactiveSlicer) {
      container.querySelectorAll('.dw-slicer button').forEach((btn) => {
        btn.addEventListener('click', () => {
          if (typeof onSlicer === 'function') onSlicer(element.id, btn.dataset.value);
        });
      });
    }
    if (editableTable && element.type === 'table') {
      const table = container.querySelector('[data-editable-table]');
      if (table) {
        const sync = () => {
          const parsed = readEditableTable(table);
          const next = {
            ...(element.content || {}),
            headers: parsed.headers,
            rows: parsed.rows,
            tableStyle: (element.content || {}).tableStyle || 'default',
          };
          element.content = next;
          if (typeof onTableChange === 'function') onTableChange(next);
        };
        table.addEventListener('input', sync);
        table.addEventListener('blur', sync, true);
        table.addEventListener('keydown', (e) => e.stopPropagation());
        table.addEventListener('mousedown', (e) => e.stopPropagation());
      }
    }
  }

  function applySlicerFilter(elements, chartsMap, value) {
    const filter = value === 'Все' ? null : String(value || '');
    elements.forEach((el) => {
      if (el.type !== 'chart') return;
      const chart = chartsMap.get(String(el.id)) || charts.get(String(el.id));
      if (!chart) return;
      const labels = (el.content && el.content.labels) || [];
      const datasets = (el.content && el.content.datasets) || [];
      if (!filter) {
        const spec = resolveChartSpec(el);
        chart.data.labels = spec.labels;
        chart.data.datasets = spec.datasets;
      } else {
        const idx = labels.findIndex((l) => String(l) === filter);
        if (idx < 0) return;
        chart.data.labels = [labels[idx]];
        chart.data.datasets = datasets.map((ds) => ({
          ...ds,
          data: [(ds.data || [])[idx]],
          backgroundColor: Array.isArray(ds.backgroundColor) ? [ds.backgroundColor[idx]] : ds.backgroundColor,
          borderColor: Array.isArray(ds.borderColor) ? [ds.borderColor[idx]] : ds.borderColor,
        }));
      }
      chart.update();
    });
  }

  global.DashboardWidgets = {
    esc,
    charts,
    destroyChart,
    renderContentHtml,
    mountWidget,
    renderChart,
    applySlicerFilter,
    parseNumbers,
    readEditableTable,
    resolveChartSpec,
  };
})(window);
