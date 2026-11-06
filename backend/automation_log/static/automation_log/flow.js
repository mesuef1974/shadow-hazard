// صفحة مسار البطاقة: استكشاف بلا تعديل. النص يُدرج بـtextContent وحده.
(function () {
  const root = document.getElementById('flow');
  if (!root) return;
  const form = document.getElementById('flow-filters');
  const names = { ci: 'الفحوص', pr: 'طلب مفتوح', merged: 'مدموج', deployed: 'منشور', closed: 'مغلق' };
  let timer;

  const el = (tag, text, attrs) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };
  const query = () => {
    const params = new URLSearchParams();
    new FormData(form).forEach((value, key) => { if (value) params.set(key, value); });
    return params.toString();
  };

  function drawChart(daily) {
    const max = Math.max(1, ...daily.map((d) => d.pass + d.fail + d.info));
    document.getElementById('flow-chart').replaceChildren(...daily.map((d) => {
      const label = `${d.day}: نجح ${d.pass}، فشل ${d.fail}، معلومة ${d.info}`;
      const bar = el('button', undefined, { type: 'button', title: label, 'aria-label': label });
      ['pass', 'fail', 'info'].forEach((r) => {
        if (d[r]) bar.append(el('i', undefined, { 'data-r': r, style: `block-size:${(d[r] / max) * 100}%` }));
      });
      bar.addEventListener('click', () => { form.elements.from.value = d.day; form.elements.to.value = d.day; load(); });
      return bar;
    }));
  }

  function drawCards(cards) {
    document.getElementById('flow-cards').replaceChildren(...cards.map((card) => {
      const item = el('li');
      const button = el('button', card.title || card.branch, { type: 'button', 'aria-pressed': String(form.elements.card.value === String(card.id)) });
      button.append(el('small', `${card.branch}${card.pr ? ' · الطلب ' + card.pr : ''} · ${names[card.stage]}`));
      button.addEventListener('click', () => {
        form.elements.card.value = form.elements.card.value === String(card.id) ? '' : card.id;
        load();
      });
      item.append(button);
      return item;
    }));
  }

  function drawEvents(events) {
    document.getElementById('flow-events').replaceChildren(...events.map((event) => {
      const row = el('tr');
      row.append(el('td', event.at), el('td', event.stage_label), el('td', event.result_label, { 'data-r': event.result }), el('td', event.branch));
      const cell = el('td');
      if (event.url.startsWith('https://github.com/')) {
        cell.append(el('a', event.detail || event.sha, { href: event.url, rel: 'noopener', target: '_blank' }));
      } else {
        cell.textContent = event.detail;
      }
      row.append(cell);
      return row;
    }));
  }

  async function load() {
    const params = query();
    document.getElementById('flow-export').href = `${root.dataset.export}?${params}`;
    document.querySelectorAll('#flow-path button').forEach((b) => b.setAttribute('aria-pressed', String(form.elements.stage.value === b.dataset.stage)));
    const status = document.getElementById('flow-sync');
    try {
      const response = await fetch(`${root.dataset.url}?${params}`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      Object.entries(data.counts).forEach(([key, value]) => {
        root.querySelectorAll(`[data-count="${key}"]`).forEach((n) => { n.textContent = value; });
      });
      status.textContent = data.synced ? `آخر تسجيل في السجل: ${data.synced}` : 'السجل فارغ؛ ينتظر مزامنة العامل.';
      document.getElementById('flow-total').textContent = `(${data.total})`;
      drawChart(data.daily);
      drawCards(data.cards);
      drawEvents(data.events);
    } catch (error) {
      status.textContent = 'تعذر تحميل السجل.';
    }
  }

  document.querySelectorAll('#flow-path button').forEach((button) => button.addEventListener('click', () => {
    form.elements.stage.value = form.elements.stage.value === button.dataset.stage ? '' : button.dataset.stage;
    load();
  }));
  form.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
  form.addEventListener('reset', () => setTimeout(() => { form.elements.card.value = ''; load(); }));
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
}());
