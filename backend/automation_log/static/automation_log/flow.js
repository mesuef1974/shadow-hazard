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
  // الفرع ووقت الحدث لاتينيان داخل صفحة عربية: يُعزلان ويُكسر الفرع عند فواصله لا وسط كلمة.
  const latin = (text) => {
    const node = el('bdi', undefined, { dir: 'ltr' });
    String(text).split(/(?<=[/_-])/).forEach((part, index) => {
      if (index) node.append(el('wbr'));
      node.append(part);
    });
    return node;
  };
  const cleaned = (text) => (text || '').replace(/:\s*$/, '');
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
    const list = document.getElementById('flow-cards');
    if (!cards.length) { list.replaceChildren(el('li', 'لا بطاقات تطابق هذا البحث.', { class: 'flow-empty' })); return; }
    list.replaceChildren(...cards.map((card) => {
      const item = el('li');
      const button = el('button', undefined, { type: 'button', 'aria-pressed': String(form.elements.card.value === String(card.id)) });
      const meta = el('small');
      meta.append(latin(card.branch), `${card.pr ? ' · الطلب ' + card.pr : ''} · ${names[card.stage]}`);
      button.append(el('bdi', card.title || card.branch), meta);
      button.addEventListener('click', () => {
        form.elements.card.value = form.elements.card.value === String(card.id) ? '' : card.id;
        load();
      });
      item.append(button);
      return item;
    }));
  }

  function drawEvents(events) {
    const body = document.getElementById('flow-events');
    if (!events.length) {
      const row = el('tr', undefined, { role: 'row' });
      row.append(el('td', 'لا أحداث تطابق هذا البحث.', { class: 'flow-empty', colspan: '5', role: 'cell' }));
      body.replaceChildren(row);
      return;
    }
    const cell = (label, content, attrs) => {
      const node = el('td', undefined, { role: 'cell', 'data-label': label, ...attrs });
      node.append(content);
      return node;
    };
    body.replaceChildren(...events.map((event) => {
      const row = el('tr', undefined, { role: 'row' });
      const detail = cleaned(event.detail);
      const content = event.url.startsWith('https://github.com/')
        ? el('a', detail || event.sha, { href: event.url, rel: 'noopener', target: '_blank', dir: 'auto' })
        : el('bdi', detail);
      row.append(
        cell('الوقت', el('bdi', event.at, { dir: 'ltr' })),
        cell('المرحلة', event.stage_label),
        cell('النتيجة', event.result_label, { 'data-r': event.result }),
        cell('البطاقة', latin(event.branch)),
        cell('التفصيل', content),
      );
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
      status.textContent = data.synced ? 'آخر تسجيل في السجل: ' : 'السجل فارغ؛ ينتظر مزامنة العامل.';
      if (data.synced) status.append(el('bdi', data.synced, { dir: 'ltr' }));
      const shown = data.events.length;
      document.getElementById('flow-total').textContent = shown < data.total ? `(آخر ${shown} من ${data.total})` : `(${data.total})`;
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
