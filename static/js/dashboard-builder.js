/**
 * Professional dashboard builder (GridStack + shared widgets).
 */
(function () {
  'use strict';

  const W = window.DashboardWidgets;
  if (!W) {
    console.error('DashboardWidgets is required');
    return;
  }

  const state = {
    id: null,
    slug: null,
    isPublished: false,
    elements: [],
    selectedId: null,
    nextId: 1,
    history: [],
    historyIndex: -1,
    grid: null,
    dirty: false,
    suppressHistory: false,
  };

  const DEFAULT_SIZE = {
    chart: { w: 4, h: 4 },
    text: { w: 3, h: 3 },
    list: { w: 3, h: 3 },
    table: { w: 5, h: 4 },
    gauge: { w: 3, h: 3 },
    progress: { w: 3, h: 3 },
    sparkline: { w: 3, h: 3 },
    slicer: { w: 3, h: 2 },
    matrix: { w: 3, h: 3 },
    formula: { w: 3, h: 3 },
    statistics: { w: 3, h: 4 },
    percentage: { w: 3, h: 3 },
    ratio: { w: 3, h: 3 },
    function: { w: 4, h: 4 },
    comparison: { w: 4, h: 3 },
    timeline: { w: 4, h: 3 },
    icon: { w: 2, h: 2 },
    badge: { w: 2, h: 2 },
  };

  function toast(message, type) {
    const el = document.getElementById('dbToast');
    if (!el) return;
    el.textContent = message;
    el.className = `db-toast show ${type || ''}`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 2600);
  }

  function iconFor(type, chartType) {
    if (type === 'chart') {
      const map = {
        bar: 'fa-chart-column',
        'bar-horizontal': 'fa-chart-bar',
        'bar-stacked': 'fa-chart-column',
        line: 'fa-chart-line',
        area: 'fa-chart-area',
        pie: 'fa-chart-pie',
        doughnut: 'fa-circle-notch',
        radar: 'fa-bullseye',
        polarArea: 'fa-chart-pie',
        scatter: 'fa-braille',
        bubble: 'fa-circle',
      };
      return map[chartType] || 'fa-chart-column';
    }
    const map = {
      text: 'fa-align-left',
      list: 'fa-list',
      table: 'fa-table',
      gauge: 'fa-tachometer-alt',
      progress: 'fa-tasks',
      sparkline: 'fa-wave-square',
      slicer: 'fa-filter',
      matrix: 'fa-th',
      formula: 'fa-square-root-alt',
      statistics: 'fa-calculator',
      percentage: 'fa-percent',
      ratio: 'fa-balance-scale',
      function: 'fa-project-diagram',
      comparison: 'fa-columns',
      timeline: 'fa-stream',
      icon: 'fa-icons',
      badge: 'fa-certificate',
    };
    return map[type] || 'fa-cube';
  }

  function defaultTitle(type, chartType) {
    if (type === 'chart') {
      return ({
        bar: 'Столбчатая',
        'bar-horizontal': 'Линейчатая',
        'bar-stacked': 'С накоплением',
        line: 'График',
        area: 'С областями',
        pie: 'Круговая',
        doughnut: 'Кольцевая',
        radar: 'Лепестковая',
        polarArea: 'Полярная',
        scatter: 'Точечная',
        bubble: 'Пузырьковая',
      }[chartType] || 'Диаграмма') + ' диаграмма';
    }
    return ({
      text: 'Текстовый блок',
      list: 'Список',
      table: 'Таблица',
      gauge: 'Индикатор',
      progress: 'Прогресс',
      sparkline: 'Спарклайн',
      slicer: 'Фильтр (срез)',
      matrix: 'Матрица',
      formula: 'Формула',
      statistics: 'Статистика',
      percentage: 'Проценты',
      ratio: 'Пропорция',
      function: 'Функция',
      comparison: 'Сравнение',
      timeline: 'Таймлайн',
      icon: 'Иконка',
      badge: 'Бейдж',
    })[type] || 'Виджет';
  }

  function defaultContent(type, chartType) {
    switch (type) {
      case 'chart':
        if (chartType === 'bar-stacked') {
          return {
            labels: ['Янв', 'Фев', 'Мар', 'Апр'],
            datasets: [
              {
                label: 'План',
                data: [12, 19, 14, 22],
                backgroundColor: '#60a5fa',
                borderColor: '#3b82f6',
                borderWidth: 1,
              },
              {
                label: 'Факт',
                data: [8, 11, 9, 15],
                backgroundColor: '#34d399',
                borderColor: '#10b981',
                borderWidth: 1,
              },
            ],
          };
        }
        return {
          labels: ['Янв', 'Фев', 'Мар', 'Апр'],
          datasets: [{
            label: 'Значения',
            data: [12, 19, 14, 22],
            backgroundColor: ['#60a5fa', '#34d399', '#fbbf24', '#f87171'],
            borderColor: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'],
            borderWidth: 2,
          }],
        };
      case 'text':
        return { text: 'Опишите ключевой вывод или комментарий к данным.' };
      case 'list':
        return { listStyle: 'disc', items: [{ text: 'Пункт 1' }, { text: 'Пункт 2' }, { text: 'Пункт 3' }] };
      case 'table':
        return {
          headers: ['Показатель', 'План', 'Факт'],
          rows: [['Калории', '2000', '1980'], ['Белки', '70', '68'], ['Жиры', '60', '55']],
          tableStyle: 'striped',
        };
      case 'gauge':
        return { value: 76, max: 100, label: 'Выполнение', color: '#10b981' };
      case 'progress':
        return { color: '#10b981', items: [{ label: 'Меню загружено', value: 80 }, { label: 'Проверено', value: 55 }] };
      case 'sparkline':
        return { data: [5, 8, 6, 12, 9, 14, 11], labels: ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'], color: '#10b981', showArea: true };
      case 'slicer':
        return { name: 'Период', values: ['Все', 'Янв', 'Фев', 'Мар', 'Апр'], activeValue: 'Все', connections: [] };
      case 'matrix':
        return { rows: 2, cols: 2, data: [[1, 2], [3, 4]] };
      case 'formula':
        return { expression: 'E = mc²', variables: ['m = масса', 'c = скорость света'] };
      case 'statistics':
        return { data: '12, 45, 67, 23, 89, 34, 56' };
      case 'percentage':
        return { part: 25, whole: 100, color: '#10b981' };
      case 'ratio':
        return { a: 3, b: 2, color: '#10b981' };
      case 'function':
        return { expression: 'sin(x)', xMin: -5, xMax: 5, color: '#10b981' };
      case 'comparison':
        return {
          color1: '#60a5fa',
          color2: '#f59e0b',
          items: [
            { label: 'Школа A', value1: 85, value2: 70 },
            { label: 'Школа B', value1: 60, value2: 75 },
          ],
        };
      case 'timeline':
        return { items: [{ label: 'Янв', value: 10 }, { label: 'Фев', value: 18 }, { label: 'Мар', value: 14 }], color: '#10b981' };
      case 'icon':
        return { name: 'fa-utensils', size: 42, color: '#10b981' };
      case 'badge':
        return { text: 'Норма', type: 'success', size: 14 };
      default:
        return {};
    }
  }

  function defaultSettings() {
    return { textSize: '14px', textColor: '#475569', backgroundColor: '#ffffff', borderColor: '#e2e8f0' };
  }

  function defaultOptions() {
    return { showDataLabels: false, dataLabelsColor: '#334155', dataLabelsPosition: 'top' };
  }

  function findById(id) {
    const nid = Number(id);
    return state.elements.find((el) => Number(el.id) === nid);
  }

  function sameId(a, b) {
    return Number(a) === Number(b);
  }

  function markDirty() {
    state.dirty = true;
  }

  function updateStatusBadge() {
    const badge = document.getElementById('dbPublishStatus');
    if (!badge) return;
    badge.textContent = state.isPublished ? 'Опубликован' : 'Черновик';
    badge.className = `db-status${state.isPublished ? ' published' : ''}`;
  }

  function syncGridFromNodes() {
    if (!state.grid) return;
    state.grid.engine.nodes.forEach((node) => {
      const id = Number(node.el.dataset.id);
      const el = findById(id);
      if (!el) return;
      el.position = { x: node.x, y: node.y };
      el.size = { w: node.w, h: node.h };
    });
  }

  function pushHistory() {
    if (state.suppressHistory) return;
    syncGridFromNodes();
    const snapshot = JSON.stringify(state.elements);
    if (state.historyIndex >= 0 && state.history[state.historyIndex] === snapshot) return;
    state.history = state.history.slice(0, state.historyIndex + 1);
    state.history.push(snapshot);
    if (state.history.length > 40) state.history.shift();
    state.historyIndex = state.history.length - 1;
    document.getElementById('btnUndo').disabled = state.historyIndex <= 0;
    document.getElementById('btnRedo').disabled = state.historyIndex >= state.history.length - 1;
  }

  function loadHistory(index) {
    const raw = state.history[index];
    if (!raw) return;
    state.historyIndex = index;
    state.elements = JSON.parse(raw);
    state.selectedId = null;
    rebuildGrid();
    renderProps(null);
    document.getElementById('btnUndo').disabled = state.historyIndex <= 0;
    document.getElementById('btnRedo').disabled = state.historyIndex >= state.history.length - 1;
  }

  function remountContent(el) {
    const body = document.querySelector(`.db-widget[data-id="${el.id}"] .db-widget-body`);
    if (!body) return;
    try {
      W.mountWidget(body, el, {
        interactiveSlicer: true,
        editableTable: sameId(state.selectedId, el.id) && el.type === 'table',
        onTableChange: (content) => {
          el.content = { ...el.content, ...content };
          markDirty();
          // Keep props grid in sync if open
          const grid = document.getElementById('pTableGrid');
          if (grid && sameId(state.selectedId, el.id)) {
            // props will refresh on next apply/select; live-sync cells if present
          }
        },
        onSlicer: (id, value) => {
          const slicer = findById(id);
          if (!slicer) return;
          slicer.content.activeValue = value;
          remountContent(slicer);
          W.applySlicerFilter(state.elements, W.charts, value);
          markDirty();
        },
      });
    } catch (err) {
      console.error('Widget render failed', el, err);
      body.innerHTML = `<div class="dw-text">Ошибка отрисовки виджета</div>`;
    }
  }

  function createWidgetEl(el) {
    const wrap = document.createElement('div');
    wrap.className = 'grid-stack-item';
    wrap.dataset.id = String(el.id);
    wrap.setAttribute('gs-id', String(el.id));
    wrap.setAttribute('gs-x', el.position.x);
    wrap.setAttribute('gs-y', el.position.y);
    wrap.setAttribute('gs-w', el.size.w);
    wrap.setAttribute('gs-h', el.size.h);
    wrap.setAttribute('gs-min-w', 2);
    wrap.setAttribute('gs-min-h', 2);

    wrap.innerHTML = `
      <div class="grid-stack-item-content db-widget ${sameId(state.selectedId, el.id) ? 'selected' : ''}" data-id="${el.id}">
        <div class="db-widget-head">
          <i class="fas fa-grip-vertical" style="color:#94a3b8"></i>
          <i class="fas ${iconFor(el.type, el.chartType)}" style="color:var(--primary,#10b981)"></i>
          <span class="title">${W.esc(el.title)}</span>
          <button type="button" title="Удалить" data-action="delete" data-id="${el.id}"><i class="fas fa-trash"></i></button>
        </div>
        <div class="db-widget-body" data-select-id="${el.id}"></div>
      </div>`;

    return wrap;
  }

  function rebuildGrid() {
    if (!state.grid) return;
    state.suppressHistory = true;
    // true = remove DOM nodes (false left empty shells on the canvas)
    state.grid.removeAll(true);
    state.elements.forEach((el) => {
      const node = createWidgetEl(el);
      state.grid.addWidget(node);
      remountContent(el);
    });
    state.suppressHistory = false;
  }

  function selectElement(id) {
    const nid = Number(id);
    if (!Number.isFinite(nid)) return;
    const prevId = state.selectedId;
    const el = findById(nid);
    state.selectedId = el ? nid : null;
    document.querySelectorAll('.db-widget').forEach((node) => {
      node.classList.toggle('selected', sameId(node.dataset.id, nid));
    });
    // Toggle editable mode on tables when selection changes
    if (prevId != null && !sameId(prevId, nid)) {
      const prev = findById(prevId);
      if (prev && prev.type === 'table') remountContent(prev);
    }
    if (el) {
      ensurePropsPanelOpen();
      renderProps(el);
    } else {
      renderProps(null);
    }
  }

  function removeElement(id) {
    const nid = Number(id);
    W.destroyChart(String(nid));

    // Remove from GridStack + DOM immediately
    const item = document.querySelector(`.grid-stack-item[data-id="${nid}"]`);
    if (item && state.grid) {
      try {
        state.grid.removeWidget(item, true, true);
      } catch (_) {
        item.remove();
      }
    } else if (item) {
      item.remove();
    }

    state.elements = state.elements.filter((el) => !sameId(el.id, nid));
    if (sameId(state.selectedId, nid)) {
      state.selectedId = null;
      renderProps(null);
    }
    pushHistory();
    markDirty();
  }

  function addElement(type, chartType) {
    const size = DEFAULT_SIZE[type] || { w: 4, h: 3 };
    const el = {
      id: state.nextId++,
      type,
      chartType: chartType || null,
      title: defaultTitle(type, chartType),
      content: defaultContent(type, chartType),
      settings: defaultSettings(),
      options: defaultOptions(),
      position: { x: 0, y: 0 },
      size: { ...size },
    };
    state.elements.push(el);
    state.suppressHistory = true;
    const node = createWidgetEl(el);
    state.grid.addWidget(node);
    remountContent(el);
    state.suppressHistory = false;
    selectElement(el.id);
    pushHistory();
    markDirty();
  }

  function ensurePropsPanelOpen() {
    const builder = document.querySelector('.db-builder');
    if (builder && builder.classList.contains('props-collapsed')) {
      builder.classList.remove('props-collapsed');
      const btn = document.getElementById('btnToggleProps');
      if (btn) {
        btn.setAttribute('aria-pressed', 'true');
        btn.title = 'Скрыть свойства';
        btn.innerHTML = '<i class="fas fa-sliders-h"></i> Свойства';
      }
    }
  }

  function togglePropsPanel() {
    const builder = document.querySelector('.db-builder');
    if (!builder) return;
    const collapsed = builder.classList.toggle('props-collapsed');
    const btn = document.getElementById('btnToggleProps');
    if (btn) {
      btn.setAttribute('aria-pressed', collapsed ? 'false' : 'true');
      btn.title = collapsed ? 'Показать свойства' : 'Скрыть свойства';
      btn.innerHTML = collapsed
        ? '<i class="fas fa-sliders-h"></i> Свойства'
        : '<i class="fas fa-sliders-h"></i> Свойства';
    }
  }

  function field(label, controlHtml, hint) {
    return `<div class="db-field"><label>${label}</label>${controlHtml}${hint ? `<div class="db-hint">${hint}</div>` : ''}</div>`;
  }

  function renderProps(el) {
    const box = document.getElementById('dbProps');
    if (!el) {
      box.innerHTML = '<div class="empty">Выберите виджет на холсте или добавьте новый из панели слева.</div>';
      return;
    }

    let extra = '';
    const c = el.content || {};

    if (el.type === 'chart') {
      const ds = (c.datasets || [])[0] || {};
      const colors = [].concat(ds.backgroundColor || ['#60a5fa', '#34d399', '#fbbf24', '#f87171']);
      const colorInputs = [0, 1, 2, 3, 4, 5].map((i) => {
        const val = colors[i] || colors[i % Math.max(colors.length, 1)] || '#60a5fa';
        const hex = typeof val === 'string' && val.startsWith('#') ? val : '#60a5fa';
        return `<input type="color" class="pChartColor" data-idx="${i}" value="${W.esc(hex)}" title="Цвет ${i + 1}">`;
      }).join('');
      const chartTypes = [
        ['bar', 'Столбчатая'],
        ['bar-horizontal', 'Линейчатая'],
        ['bar-stacked', 'С накоплением'],
        ['line', 'График'],
        ['area', 'С областями'],
        ['pie', 'Круговая'],
        ['doughnut', 'Кольцевая'],
        ['radar', 'Лепестковая'],
        ['polarArea', 'Кольцевая полярная'],
        ['scatter', 'Точечная'],
        ['bubble', 'Пузырьковая'],
      ];
      extra = `
        ${field('Тип диаграммы', `<select id="pChartType">${chartTypes.map(([v, l]) => `<option value="${v}" ${el.chartType === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`)}
        ${field('Подписи оси / категории', `<input id="pLabels" value="${W.esc((c.labels || []).join(', '))}">`, 'Например: Янв, Фев, Мар')}
        ${field('Значения', `<input id="pValues" value="${W.esc((ds.data || []).map((x) => (x && typeof x === 'object' ? x.y : x)).join(', '))}">`, 'Числа через запятую: 12, 19, 14')}
        ${field('Название серии', `<input id="pSeries" value="${W.esc(ds.label || '')}">`)}
        ${field('Цвета', `<div class="db-color-row">${colorInputs}</div>`)}
        <div class="db-field-row">
          ${field('Подписи данных', `<select id="pShowLabels"><option value="0" ${!(el.options || {}).showDataLabels ? 'selected' : ''}>Скрыть</option><option value="1" ${(el.options || {}).showDataLabels ? 'selected' : ''}>Показать</option></select>`)}
          ${field('Цвет подписей', `<input id="pLabelColor" type="color" value="${W.esc((el.options || {}).dataLabelsColor || '#334155')}">`)}
        </div>
        ${field('Позиция подписей', `<select id="pLabelPos">
          <option value="top" ${(el.options || {}).dataLabelsPosition !== 'center' && (el.options || {}).dataLabelsPosition !== 'bottom' ? 'selected' : ''}>Сверху / снаружи</option>
          <option value="center" ${(el.options || {}).dataLabelsPosition === 'center' ? 'selected' : ''}>По центру</option>
          <option value="bottom" ${(el.options || {}).dataLabelsPosition === 'bottom' ? 'selected' : ''}>Снизу</option>
        </select>`)}`;
    } else if (el.type === 'text') {
      extra = field('Текст', `<textarea id="pText">${W.esc(c.text || '')}</textarea>`);
    } else if (el.type === 'list') {
      extra = field('Пункты (по одному в строке)', `<textarea id="pList">${W.esc((c.items || []).map((i) => i.text || i).join('\n'))}</textarea>`);
    } else if (el.type === 'table') {
      const headers = c.headers || ['Колонка 1'];
      const rows = c.rows || [['']];
      const style = c.tableStyle || 'default';
      extra = `
        ${field('Стиль таблицы', `<select id="pTableStyle">
          <option value="default" ${style === 'default' ? 'selected' : ''}>Обычный</option>
          <option value="striped" ${style === 'striped' ? 'selected' : ''}>Чередование строк</option>
          <option value="bordered" ${style === 'bordered' ? 'selected' : ''}>С границами</option>
          <option value="compact" ${style === 'compact' ? 'selected' : ''}>Компактный</option>
          <option value="modern" ${style === 'modern' ? 'selected' : ''}>Современный</option>
          <option value="excel" ${style === 'excel' ? 'selected' : ''}>Как Excel</option>
        </select>`)}
        <div class="db-field">
          <label>Редактор таблицы</label>
          <div class="db-table-toolbar">
            <button type="button" class="db-btn db-btn-secondary" data-table-act="add-row">+ Строка</button>
            <button type="button" class="db-btn db-btn-secondary" data-table-act="add-col">+ Колонка</button>
            <button type="button" class="db-btn db-btn-ghost" data-table-act="del-row">− Строка</button>
            <button type="button" class="db-btn db-btn-ghost" data-table-act="del-col">− Колонка</button>
          </div>
          <div class="db-table-editor-wrap">
            <table class="db-table-editor" id="pTableGrid">
              <thead><tr>${headers.map((h, ci) => `<th contenteditable="true" data-r="-1" data-c="${ci}">${W.esc(h)}</th>`).join('')}</tr></thead>
              <tbody>${rows.map((r, ri) => `<tr>${headers.map((_, ci) => `<td contenteditable="true" data-r="${ri}" data-c="${ci}">${W.esc((r || [])[ci] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody>
            </table>
          </div>
          <div class="db-hint">Кликните по ячейке, чтобы изменить. Можно редактировать и прямо на холсте.</div>
        </div>`;
    } else if (el.type === 'gauge') {
      extra = `
        <div class="db-field-row">
          ${field('Значение', `<input id="pValue" type="number" value="${W.esc(c.value ?? 0)}">`)}
          ${field('Максимум', `<input id="pMax" type="number" value="${W.esc(c.max ?? 100)}">`)}
        </div>
        ${field('Подпись', `<input id="pLabel" value="${W.esc(c.label || '')}">`)}
        ${field('Цвет', `<input id="pColor" type="color" value="${W.esc(c.color || '#10b981')}">`)}`;
    } else if (el.type === 'progress' || el.type === 'timeline' || el.type === 'comparison') {
      extra = field(
        'Данные JSON',
        `<textarea id="pJson">${W.esc(JSON.stringify(c, null, 2))}</textarea>`,
        'Редактируйте структуру виджета'
      );
    } else if (el.type === 'sparkline') {
      extra = `
        ${field('Значения', `<input id="pSpark" value="${W.esc((c.data || []).join(', '))}">`)}
        ${field('Цвет', `<input id="pColor" type="color" value="${W.esc(c.color || '#10b981')}">`)}`;
    } else if (el.type === 'slicer') {
      extra = field('Значения фильтра (через запятую)', `<input id="pSlicer" value="${W.esc((c.values || []).join(', '))}">`);
    } else if (el.type === 'statistics') {
      extra = field('Числа', `<input id="pStats" value="${W.esc(c.data || '')}">`);
    } else if (el.type === 'percentage') {
      extra = `<div class="db-field-row">${field('Часть', `<input id="pPart" type="number" value="${W.esc(c.part ?? 0)}">`)}${field('Целое', `<input id="pWhole" type="number" value="${W.esc(c.whole ?? 100)}">`)}</div>`;
    } else if (el.type === 'ratio') {
      extra = `<div class="db-field-row">${field('A', `<input id="pA" type="number" value="${W.esc(c.a ?? 1)}">`)}${field('B', `<input id="pB" type="number" value="${W.esc(c.b ?? 1)}">`)}</div>`;
    } else if (el.type === 'formula') {
      extra = `
        ${field('Выражение', `<input id="pExpr" value="${W.esc(c.expression || '')}">`)}
        ${field('Переменные (по строке)', `<textarea id="pVars">${W.esc((c.variables || []).join('\n'))}</textarea>`)}`;
    } else if (el.type === 'function') {
      extra = `
        ${field('f(x)', `<input id="pExpr" value="${W.esc(c.expression || 'sin(x)')}">`, 'Можно использовать Math: sin, cos, abs…')}
        <div class="db-field-row">${field('x min', `<input id="pXMin" type="number" value="${W.esc(c.xMin ?? -5)}">`)}${field('x max', `<input id="pXMax" type="number" value="${W.esc(c.xMax ?? 5)}">`)}</div>`;
    } else if (el.type === 'matrix') {
      extra = field('Матрица JSON', `<textarea id="pJson">${W.esc(JSON.stringify(c.data || [], null, 2))}</textarea>`);
    } else if (el.type === 'icon') {
      extra = `
        ${field('Иконка Font Awesome', `<input id="pIcon" value="${W.esc(c.name || 'fa-star')}">`, 'Например fa-utensils')}
        <div class="db-field-row">${field('Размер', `<input id="pSize" type="number" value="${W.esc(c.size || 42)}">`)}${field('Цвет', `<input id="pColor" type="color" value="${W.esc(c.color || '#10b981')}">`)}</div>`;
    } else if (el.type === 'badge') {
      extra = `
        ${field('Текст', `<input id="pBadgeText" value="${W.esc(c.text || '')}">`)}
        ${field('Тип', `<select id="pBadgeType"><option value="primary">primary</option><option value="success">success</option><option value="warning">warning</option><option value="danger">danger</option><option value="info">info</option></select>`)}`;
    } else {
      extra = field('JSON', `<textarea id="pJson">${W.esc(JSON.stringify(c, null, 2))}</textarea>`);
    }

    box.innerHTML = `
      ${field('Заголовок', `<input id="pTitle" value="${W.esc(el.title || '')}">`)}
      <div class="db-field-row">
        ${field('Ширина', `<input id="pW" type="number" min="2" max="12" value="${el.size.w}">`)}
        ${field('Высота', `<input id="pH" type="number" min="2" max="20" value="${el.size.h}">`)}
      </div>
      ${extra}
      <button type="button" class="db-btn db-btn-primary" id="btnApplyProps" style="width:100%;justify-content:center">Применить</button>`;

    if (el.type === 'badge') {
      const sel = box.querySelector('#pBadgeType');
      if (sel) sel.value = c.type || 'primary';
    }

    box.querySelector('#btnApplyProps').addEventListener('click', () => applyProps(el.id));
    if (el.type === 'table') bindTableEditor(el.id);
    // Re-render canvas table in editable mode when selected
    if (el.type === 'table') remountContent(el);
  }

  function readPropsTable() {
    const grid = document.getElementById('pTableGrid');
    if (!grid) return null;
    return W.readEditableTable(grid);
  }

  function rebuildPropsTableGrid(headers, rows) {
    const grid = document.getElementById('pTableGrid');
    if (!grid) return;
    grid.innerHTML = `
      <thead><tr>${headers.map((h, ci) => `<th contenteditable="true" data-r="-1" data-c="${ci}">${W.esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map((r, ri) => `<tr>${headers.map((_, ci) => `<td contenteditable="true" data-r="${ri}" data-c="${ci}">${W.esc((r || [])[ci] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody>`;
  }

  function bindTableEditor(elementId) {
    const box = document.getElementById('dbProps');
    const syncFromGrid = () => {
      const el = findById(elementId);
      if (!el) return;
      const parsed = readPropsTable();
      if (!parsed) return;
      const style = document.getElementById('pTableStyle')?.value || 'default';
      el.content = { ...el.content, headers: parsed.headers, rows: parsed.rows, tableStyle: style };
      remountContent(el);
      markDirty();
    };

    box.querySelectorAll('[data-table-act]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const el = findById(elementId);
        if (!el) return;
        const parsed = readPropsTable() || { headers: [...(el.content.headers || [])], rows: [...(el.content.rows || [])] };
        let { headers, rows } = parsed;
        headers = [...headers];
        rows = rows.map((r) => [...(r || [])]);
        const act = btn.dataset.tableAct;
        if (act === 'add-col') {
          headers.push(`Колонка ${headers.length + 1}`);
          rows = rows.map((r) => { r.push(''); return r; });
        } else if (act === 'del-col') {
          if (headers.length <= 1) return toast('Нужна хотя бы одна колонка', 'error');
          headers.pop();
          rows = rows.map((r) => { r.pop(); return r; });
        } else if (act === 'add-row') {
          rows.push(headers.map(() => ''));
        } else if (act === 'del-row') {
          if (rows.length <= 1) return toast('Нужна хотя бы одна строка', 'error');
          rows.pop();
        }
        rebuildPropsTableGrid(headers, rows);
        el.content = {
          ...el.content,
          headers,
          rows,
          tableStyle: document.getElementById('pTableStyle')?.value || el.content.tableStyle || 'default',
        };
        remountContent(el);
        markDirty();
      });
    });

    document.getElementById('pTableStyle')?.addEventListener('change', syncFromGrid);
    document.getElementById('pTableGrid')?.addEventListener('input', syncFromGrid);
  }

  function parseCsvTable(text) {
    const lines = String(text || '').split('\n').map((l) => l.trim()).filter(Boolean);
    if (!lines.length) return { headers: [], rows: [] };
    const split = (line) => line.split(',').map((x) => x.trim());
    return { headers: split(lines[0]), rows: lines.slice(1).map(split) };
  }

  function applyProps(id) {
    const el = findById(id);
    if (!el) return;
    el.title = document.getElementById('pTitle').value.trim() || el.title;
    const w = Math.max(2, Math.min(12, Number(document.getElementById('pW').value) || el.size.w));
    const h = Math.max(2, Math.min(20, Number(document.getElementById('pH').value) || el.size.h));
    el.size = { w, h };

    const c = el.content || {};
    if (el.type === 'chart') {
      const labels = document.getElementById('pLabels').value.split(',').map((x) => x.trim()).filter(Boolean);
      const values = document.getElementById('pValues').value.split(',').map((x) => parseFloat(x.trim())).filter(Number.isFinite);
      const series = document.getElementById('pSeries').value.trim() || 'Значения';
      const chartType = document.getElementById('pChartType')?.value || el.chartType || 'bar';
      const picked = Array.from(document.querySelectorAll('.pChartColor')).map((inp) => inp.value);
      const fallback = ['#60a5fa', '#34d399', '#fbbf24', '#f87171', '#a78bfa', '#f472b6'];
      const n = Math.max(labels.length, values.length, 1);
      const backgroundColor = Array.from({ length: n }, (_, i) => picked[i] || fallback[i % fallback.length]);
      const borderColor = backgroundColor.map((hex) => hex);
      el.chartType = chartType;
      if (chartType === 'bar-stacked' && (c.datasets || []).length > 1) {
        const firstBg = backgroundColor[0] || '#60a5fa';
        el.content = {
          labels,
          datasets: [
            {
              label: series,
              data: values,
              backgroundColor: firstBg,
              borderColor: firstBg,
              borderWidth: 1,
            },
            ...c.datasets.slice(1).map((ds, i) => ({
              ...ds,
              backgroundColor: ds.backgroundColor || fallback[(i + 1) % fallback.length],
              borderWidth: 1,
            })),
          ],
        };
      } else {
        el.content = {
          labels,
          datasets: [{
            label: series,
            data: values,
            backgroundColor,
            borderColor,
            borderWidth: 2,
          }],
        };
      }
      el.options = {
        ...(el.options || {}),
        showDataLabels: document.getElementById('pShowLabels').value === '1',
        dataLabelsColor: document.getElementById('pLabelColor').value,
        dataLabelsPosition: document.getElementById('pLabelPos')?.value || 'top',
      };
      const titleNode = document.querySelector(`.db-widget[data-id="${el.id}"] .title`);
      const iconNode = document.querySelector(`.db-widget[data-id="${el.id}"] .db-widget-head i.fas:not(.fa-grip-vertical)`);
      if (iconNode) iconNode.className = `fas ${iconFor('chart', chartType)}`;
      if (titleNode && !document.getElementById('pTitle').value.trim()) {
        el.title = defaultTitle('chart', chartType);
        titleNode.textContent = el.title;
      }
    } else if (el.type === 'text') {
      el.content = { text: document.getElementById('pText').value };
    } else if (el.type === 'list') {
      el.content = {
        listStyle: 'disc',
        items: document.getElementById('pList').value.split('\n').map((t) => t.trim()).filter(Boolean).map((text) => ({ text })),
      };
    } else if (el.type === 'table') {
      const parsed = readPropsTable();
      if (parsed) {
        el.content = {
          headers: parsed.headers,
          rows: parsed.rows,
          tableStyle: document.getElementById('pTableStyle')?.value || 'default',
        };
      }
    } else if (el.type === 'gauge') {
      el.content = {
        ...c,
        value: Number(document.getElementById('pValue').value),
        max: Number(document.getElementById('pMax').value),
        label: document.getElementById('pLabel').value,
        color: document.getElementById('pColor').value,
      };
    } else if (el.type === 'sparkline') {
      el.content = {
        ...c,
        data: document.getElementById('pSpark').value.split(',').map((x) => parseFloat(x.trim())).filter(Number.isFinite),
        color: document.getElementById('pColor').value,
      };
    } else if (el.type === 'slicer') {
      const values = document.getElementById('pSlicer').value.split(',').map((x) => x.trim()).filter(Boolean);
      el.content = { ...c, values, activeValue: values[0] || 'Все' };
    } else if (el.type === 'statistics') {
      el.content = { ...c, data: document.getElementById('pStats').value };
    } else if (el.type === 'percentage') {
      el.content = { ...c, part: Number(document.getElementById('pPart').value), whole: Number(document.getElementById('pWhole').value) };
    } else if (el.type === 'ratio') {
      el.content = { ...c, a: Number(document.getElementById('pA').value), b: Number(document.getElementById('pB').value) };
    } else if (el.type === 'formula') {
      el.content = {
        expression: document.getElementById('pExpr').value,
        variables: document.getElementById('pVars').value.split('\n').map((x) => x.trim()).filter(Boolean),
      };
    } else if (el.type === 'function') {
      el.content = {
        ...c,
        expression: document.getElementById('pExpr').value,
        xMin: Number(document.getElementById('pXMin').value),
        xMax: Number(document.getElementById('pXMax').value),
      };
    } else if (el.type === 'matrix') {
      try {
        el.content = { ...c, data: JSON.parse(document.getElementById('pJson').value) };
      } catch (_) {
        toast('Некорректный JSON матрицы', 'error');
        return;
      }
    } else if (el.type === 'icon') {
      el.content = { name: document.getElementById('pIcon').value, size: Number(document.getElementById('pSize').value), color: document.getElementById('pColor').value };
    } else if (el.type === 'badge') {
      el.content = { text: document.getElementById('pBadgeText').value, type: document.getElementById('pBadgeType').value, size: 14 };
    } else if (document.getElementById('pJson')) {
      try {
        el.content = JSON.parse(document.getElementById('pJson').value);
      } catch (_) {
        toast('Некорректный JSON', 'error');
        return;
      }
    }

    syncGridFromNodes();
    const node = state.grid.engine.nodes.find((n) => Number(n.el.dataset.id) === el.id);
    if (node) state.grid.update(node.el, { w: el.size.w, h: el.size.h });
    const titleEl = document.querySelector(`.db-widget[data-id="${el.id}"] .title`);
    if (titleEl) titleEl.textContent = el.title;
    remountContent(el);
    pushHistory();
    markDirty();
    toast('Виджет обновлён', 'success');
  }

  async function saveDashboard(publish) {
    const title = document.getElementById('dashboardTitle').value.trim();
    if (!title) {
      toast('Введите название дашборда', 'error');
      return;
    }
    syncGridFromNodes();
    const payload = {
      id: state.id,
      title,
      description: document.getElementById('dashboardDescription').value.trim(),
      is_published: !!publish,
      elements: state.elements.map((el) => ({
        id: el.id,
        type: el.type,
        chartType: el.chartType,
        title: el.title,
        content: el.content,
        settings: el.settings,
        options: el.options,
        position: el.position,
        size: el.size,
      })),
      layout: { columns: 12 },
    };

    try {
      const res = await fetch('/dashboard-admin/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !(data.success || data.status === 'success')) {
        throw new Error(data.detail || 'Ошибка сохранения');
      }
      state.id = data.id;
      state.slug = data.slug;
      state.isPublished = !!data.is_published;
      state.dirty = false;
      updateStatusBadge();
      toast(publish ? 'Дашборд опубликован' : 'Черновик сохранён', 'success');
      if (publish && data.slug) {
        const url = `${window.location.origin}/dashboard/${data.slug}`;
        const input = document.getElementById('publicUrl');
        if (input) input.value = url;
        const modal = document.getElementById('publishModal');
        if (modal) modal.style.display = 'flex';
      }
      if (!state.id && data.id) {
        history.replaceState({}, '', `/dashboard-admin/edit/${data.id}`);
      } else if (data.id && !window.location.pathname.includes(`/edit/${data.id}`)) {
        history.replaceState({}, '', `/dashboard-admin/edit/${data.id}`);
      }
    } catch (err) {
      console.error(err);
      toast(err.message || 'Ошибка сохранения', 'error');
    }
  }

  function clearAll() {
    if (!state.elements.length) return;
    if (!confirm('Удалить все виджеты с холста?')) return;
    state.elements.forEach((el) => W.destroyChart(String(el.id)));
    state.elements = [];
    state.selectedId = null;
    if (state.grid) state.grid.removeAll(true);
    renderProps(null);
    pushHistory();
    markDirty();
  }

  function bindToolbar() {
    document.getElementById('btnSaveDraft').addEventListener('click', () => saveDashboard(false));
    document.getElementById('btnPublish').addEventListener('click', () => saveDashboard(true));
    document.getElementById('btnClear').addEventListener('click', clearAll);
    document.getElementById('btnToggleProps')?.addEventListener('click', togglePropsPanel);
    document.getElementById('btnCloseProps')?.addEventListener('click', () => {
      const builder = document.querySelector('.db-builder');
      if (builder && !builder.classList.contains('props-collapsed')) togglePropsPanel();
    });
    document.getElementById('btnToggleToolbox')?.addEventListener('click', () => {
      document.querySelector('.db-builder')?.classList.toggle('toolbox-collapsed');
    });
    document.getElementById('btnUndo').addEventListener('click', () => {
      if (state.historyIndex > 0) loadHistory(state.historyIndex - 1);
    });
    document.getElementById('btnRedo').addEventListener('click', () => {
      if (state.historyIndex < state.history.length - 1) loadHistory(state.historyIndex + 1);
    });
    document.getElementById('btnPreview')?.addEventListener('click', () => {
      if (state.slug) window.open(`/dashboard/${state.slug}`, '_blank');
      else if (state.id) window.open(`/dashboard/${state.id}`, '_blank');
      else toast('Сначала сохраните дашборд', 'error');
    });
    document.querySelectorAll('[data-close-modal]').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.getElementById('publishModal').style.display = 'none';
      });
    });
    document.getElementById('btnCopyUrl')?.addEventListener('click', async () => {
      const input = document.getElementById('publicUrl');
      try {
        await navigator.clipboard.writeText(input.value);
        toast('Ссылка скопирована', 'success');
      } catch (_) {
        input.select();
        document.execCommand('copy');
        toast('Ссылка скопирована', 'success');
      }
    });
  }

  function bindToolbox() {
    document.querySelectorAll('.db-tool').forEach((btn) => {
      btn.addEventListener('click', () => addElement(btn.dataset.type, btn.dataset.chartType || null));
      btn.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', JSON.stringify({
          type: btn.dataset.type,
          chartType: btn.dataset.chartType || null,
        }));
      });
    });
  }

  function initGrid() {
    state.grid = GridStack.init({
      column: 12,
      cellHeight: 56,
      margin: 8,
      float: true,
      animate: true,
      draggable: { handle: '.db-widget-head' },
      resizable: { handles: 'e, se, s, sw, w' },
    }, '#dashboardGrid');

    state.grid.on('change', () => {
      if (state.suppressHistory) return;
      syncGridFromNodes();
      markDirty();
      pushHistory();
    });

    // Event delegation — works even after GridStack reparents nodes / Chart.js canvas
    const gridEl = document.getElementById('dashboardGrid');
    gridEl.addEventListener('click', (e) => {
      const delBtn = e.target.closest('[data-action="delete"]');
      if (delBtn) {
        e.preventDefault();
        e.stopPropagation();
        removeElement(delBtn.dataset.id || delBtn.closest('[data-id]')?.dataset.id);
        return;
      }
      const widget = e.target.closest('.db-widget, .grid-stack-item');
      if (!widget) return;
      const id = widget.dataset.id || widget.closest('.grid-stack-item')?.dataset.id;
      if (id != null) selectElement(id);
    });

    const canvas = document.getElementById('dbCanvasWrap');
    canvas.addEventListener('dragover', (e) => e.preventDefault());
    canvas.addEventListener('drop', (e) => {
      e.preventDefault();
      try {
        const data = JSON.parse(e.dataTransfer.getData('text/plain'));
        if (data && data.type) addElement(data.type, data.chartType);
      } catch (_) { /* ignore */ }
    });
  }

  function bootstrap(initial) {
    if (initial) {
      state.id = initial.id || null;
      state.slug = initial.slug || null;
      state.isPublished = !!initial.is_published;
      state.elements = (initial.elements || []).map((el) => {
        let id = Number(el.id);
        if (!Number.isFinite(id)) id = state.nextId++;
        state.nextId = Math.max(state.nextId, id + 1);
        return {
          id,
          type: el.type,
          chartType: el.chartType || null,
          title: el.title || '',
          content: el.content || {},
          settings: el.settings || defaultSettings(),
          options: el.options || defaultOptions(),
          position: el.position || { x: 0, y: 0 },
          size: el.size || { w: 4, h: 3 },
        };
      });
      document.getElementById('dashboardTitle').value = initial.title || '';
      document.getElementById('dashboardDescription').value = initial.description || '';
    }
    updateStatusBadge();
    initGrid();
    rebuildGrid();
    bindToolbar();
    bindToolbox();
    pushHistory();
    renderProps(null);
    window.addEventListener('beforeunload', (e) => {
      if (!state.dirty) return;
      e.preventDefault();
      e.returnValue = '';
    });
  }

  window.DashboardBuilder = { bootstrap };
})();
