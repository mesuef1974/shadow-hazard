// Claude usage charts: plain SVG, one y-axis, thin rounded columns, hover/focus tooltips.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const fmt = new Intl.NumberFormat('en-US');
  const short = v => v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M' : v >= 1e3 ? Math.round(v / 1e3) + 'K' : String(v);
  const node = (tag, attrs = {}, parent) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.append(n); return n; };
  const niceMax = v => { if (v <= 0) return 1; const p = 10 ** Math.floor(Math.log10(v)); const m = v / p; return ([1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(n => m <= n)) * p; };
  // Column path rounded only at the free end (top), anchored square to the baseline.
  const column = (x, y, w, h, r) => { r = Math.min(r, w / 2, h); return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`; };

  function tooltip(box) { const t = document.createElement('div'); t.className = 'cu-tip'; t.hidden = true; box.append(t); return t; }
  function place(tip, box, svg, x, y, html) {
    tip.innerHTML = html; tip.hidden = false;
    const s = svg.getBoundingClientRect(), b = box.getBoundingClientRect(), k = s.width / svg.viewBox.baseVal.width;
    tip.style.left = Math.min(Math.max(s.left - b.left + x * k, 80), b.width - 80) + 'px';
    const top = s.top - b.top + y * k - 8;
    const below = top < tip.offsetHeight + 4;
    tip.style.transform = below ? 'translate(-50%, 16px)' : '';
    tip.style.top = (below ? Math.max(top, 0) : top) + 'px';
  }

  function stacked(box, rows, series, opts) {
    const W = 720, H = 240, L = 44, R = 8, T = 18, B = 26, iw = W - L - R, ih = H - T - B;
    const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opts.label, dir: 'ltr' }, box);
    const total = r => series.reduce((s, k) => s + r[k.key], 0);
    const max = niceMax(Math.max(...rows.map(total)) / 4) * 4;
    const grid = node('g', { class: 'grid' }, svg), axis = node('g', { class: 'axis' }, svg);
    for (let i = 0; i <= 4; i++) {
      const y = T + ih - ih * i / 4;
      node('line', { x1: L, x2: W - R, y1: y, y2: y }, grid);
      node('text', { x: L - 6, y: y + 4, 'text-anchor': 'end' }, axis).textContent = short(max * i / 4);
    }
    const step = iw / rows.length, bw = Math.max(2, Math.min(28, step * 0.62));
    const every = Math.ceil(rows.length / 8);
    const tip = tooltip(box);
    let peak = 0; rows.forEach((r, i) => { if (total(r) > total(rows[peak])) peak = i; });
    rows.forEach((r, i) => {
      const g = node('g', { class: 'col', tabindex: 0, 'aria-label': `${r.label}: ${fmt.format(total(r))}` }, svg);
      const cx = L + step * i + step / 2;
      node('rect', { class: 'hit', x: cx - step / 2, y: T, width: step, height: ih }, g);
      let base = T + ih;
      const segs = series.filter(s => r[s.key] > 0), x = cx - bw / 2;
      segs.forEach((s, j) => {
        const h = Math.max(1, ih * r[s.key] / max), y = base - h, hh = h - (j ? 2 : 0);
        if (hh > 0) node('path', { d: j === segs.length - 1 ? column(x, y, bw, hh, 4) : `M${x},${y + hh}V${y}H${x + bw}V${y + hh}Z`, fill: `var(${s.color})` }, g);
        base = y;
      });
      if ((i % every === 0 && rows.length - 1 - i >= every / 2) || i === rows.length - 1) node('text', { x: cx, y: H - 8, 'text-anchor': 'middle' }, axis).textContent = opts.tick(r.label);
      if (i === peak && total(r)) node('text', { class: 'direct', x: cx, y: base - 6, 'text-anchor': 'middle' }, svg).textContent = short(total(r));
      const show = () => place(tip, box, svg, cx, base, `<strong>${r.label}</strong><br>` + series.map(s => `<i style="background:var(${s.color})"></i>${s.name}: ${fmt.format(r[s.key])}`).join('<br>') + `<br>الطازج: ${fmt.format(total(r))}` + (r.cache_read !== undefined ? `<br>قراءة الذاكرة المؤقتة: ${fmt.format(r.cache_read)}` : '') + `<br>الردود: ${fmt.format(r.replies)}`);
      g.addEventListener('mouseenter', show); g.addEventListener('focus', show);
      g.addEventListener('mouseleave', () => tip.hidden = true); g.addEventListener('blur', () => tip.hidden = true);
    });
  }

  function bars(box, rows, opts) {
    const W = 720, rowH = 34, L = 170, R = 70, H = rows.length * rowH + 8;
    const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opts.label, dir: 'ltr' }, box);
    const max = Math.max(...rows.map(r => r.fresh), 1), tip = tooltip(box);
    rows.forEach((r, i) => {
      const y = 4 + i * rowH, w = Math.max(2, (W - L - R) * r.fresh / max);
      const g = node('g', { class: 'col', tabindex: 0, 'aria-label': `${r.label}: ${fmt.format(r.fresh)}` }, svg);
      node('rect', { class: 'hit', x: 0, y, width: W, height: rowH - 4 }, g);
      node('text', { x: L - 10, y: y + rowH / 2 + 2, 'text-anchor': 'end', class: 'direct' }, g).textContent = r.label;
      node('path', { d: `M${L},${y + 6}H${L + w - 4}Q${L + w},${y + 6} ${L + w},${y + 10}V${y + rowH - 14}Q${L + w},${y + rowH - 10} ${L + w - 4},${y + rowH - 10}H${L}Z`, fill: 'var(--cu-in)' }, g);
      node('text', { x: L + w + 8, y: y + rowH / 2 + 2, class: 'direct' }, g).textContent = short(r.fresh);
      const show = () => place(tip, box, svg, L + w / 2, y + 6, `<strong dir="ltr">${r.label}</strong><br>الطازج: ${fmt.format(r.fresh)}<br>الردود: ${fmt.format(r.replies)}`);
      g.addEventListener('mouseenter', show); g.addEventListener('focus', show);
      g.addEventListener('mouseleave', () => tip.hidden = true); g.addEventListener('blur', () => tip.hidden = true);
    });
  }

  const init = () => {
    const source = document.getElementById('claude-usage-charts'); if (!source) return;
    const data = JSON.parse(source.textContent);
    const series = [{ key: 'new_input', name: 'إدخال جديد', color: '--cu-in' }, { key: 'output', name: 'خرج', color: '--cu-out' }];
    const mount = (id, rows, draw) => { const box = document.getElementById(id); if (!box) return; const target = box.querySelector('.cu-plot');
      if (!rows.length || !rows.some(r => (r.fresh ?? r.new_input + r.output) > 0)) { target.innerHTML = '<p class="cu-empty">غير متاح: لا بيانات في هذه الفترة.</p>'; return; } draw(target, rows); };
    mount('cu-daily', data.daily, (b, rows) => stacked(b, rows, series, { label: 'الطازج يومًا بيوم لآخر 30 يومًا', tick: l => l.slice(5) }));
    mount('cu-hourly', data.hourly, (b, rows) => stacked(b, rows, series, { label: 'الجلسة ساعة بساعة', tick: l => l.slice(6) }));
    mount('cu-models', data.models, (b, rows) => bars(b, rows, { label: 'الطازج حسب النموذج لآخر 30 يومًا' }));
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
})();
