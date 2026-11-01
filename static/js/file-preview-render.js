/**
 * Readable preview renderers for KP (calendar) and TM (typical menu).
 * Expects escapeHtml(str) in the calling scope OR pass esc as option.
 */
(function (global) {
  'use strict';

  function defaultEsc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function metaBlock(data, esc) {
    const bits = [];
    if (data.title) bits.push(`<strong>${esc(data.title)}</strong>`);
    if (data.school) bits.push(`<span>${esc(data.school)}</span>`);
    if (data.year) bits.push(`<span>${esc(data.year)} год</span>`);
    if (data.age) bits.push(`<span>${esc(data.age)}</span>`);
    if (!bits.length && !data.legend) return '';
    return `<div class="pv-meta">${bits.join('')}${
      data.legend ? `<p class="pv-legend">${esc(data.legend)}</p>` : ''
    }</div>`;
  }

  function renderCalendar(data, esc) {
    esc = esc || defaultEsc;
    const months = data.months || [];
    if (!months.length) {
      return `<div class="pv-empty">В календаре нет данных для показа</div>`;
    }
    const monthsHtml = months.map((month) => {
      const days = month.days || [];
      const cells = days.map((d) => {
        const has = d.cycle != null && String(d.cycle).trim() !== '';
        return `<div class="pv-cal-day${has ? ' has-cycle' : ''}" title="${
          has ? `День месяца ${esc(d.day)} → день цикла ${esc(d.cycle)}` : `День ${esc(d.day)}`
        }">
          <span class="pv-cal-num">${esc(d.day)}</span>
          <span class="pv-cal-cycle">${has ? esc(d.cycle) : ''}</span>
        </div>`;
      }).join('');
      return `<section class="pv-cal-month">
        <h4>${esc(month.name || 'Месяц')}</h4>
        <div class="pv-cal-grid">${cells}</div>
      </section>`;
    }).join('');
    return `<div class="pv-special pv-calendar">${metaBlock(data, esc)}${monthsHtml}</div>`;
  }

  function renderMenu(data, esc) {
    esc = esc || defaultEsc;
    const days = data.days || [];
    if (!days.length) {
      return `<div class="pv-empty">В типовом меню нет данных для показа</div>`;
    }
    const daysHtml = days.map((day, index) => {
      const meals = (day.meals || []).map((meal) => {
        const dishes = (meal.dishes || []).map((dish) => {
          const exit = dish.exit ? `<span class="pv-dish-exit">${esc(dish.exit)} г</span>` : '';
          const kcal = dish.calories ? `<span class="pv-dish-kcal">${esc(dish.calories)} ккал</span>` : '';
          const section = dish.section ? `<span class="pv-dish-sec">${esc(dish.section)}</span>` : '';
          return `<li>${section}<span class="pv-dish-name">${esc(dish.name || '—')}</span>${exit}${kcal}</li>`;
        }).join('');
        return `<div class="pv-meal">
          <h5>${esc(meal.name || 'Приём пищи')}</h5>
          <ul>${dishes}</ul>
        </div>`;
      }).join('');
      const open = index === 0 ? ' open' : '';
      return `<details class="pv-menu-day"${open}>
        <summary>${esc(day.title || 'День меню')}</summary>
        <div class="pv-menu-day-body">${meals}</div>
      </details>`;
    }).join('');
    return `<div class="pv-special pv-menu">${metaBlock(data, esc)}${daysHtml}</div>`;
  }

  global.FilePreviewRender = {
    escapeHtml: defaultEsc,
    renderCalendar,
    renderMenu,
  };
})(window);
