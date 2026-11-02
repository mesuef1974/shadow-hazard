(() => {
  const interval = 60000;
  const status = document.getElementById('usage-auto-refresh-status');
  if (!status) return;
  let busy = false;
  let timer = null;
  let activeController = null;
  let resumePending = false;
  // Bounded, page-local observations allow a visibility check after returning.
  let requests = 0;
  const observations = [];
  function observe(event) {
    observations.push({event, at: Date.now(), hidden: document.hidden, requests,
      scrollY: window.scrollY, session: document.querySelector('#session-filter')?.value,
      period: document.querySelector('#period')?.value});
    if (observations.length > 20) observations.shift();
    status.dataset.refreshObservations = JSON.stringify(observations);
  }
  observe('initialized');
  function schedule() {
    clearTimeout(timer);
    timer = null;
    if (!document.hidden) timer = setTimeout(refresh, interval);
  }
  const key = node => node.dataset.refreshKey || node.id || node.querySelector(':scope > summary')?.textContent.trim();
  async function refresh() {
    if (busy || document.hidden) return;
    const current = document.querySelector('.usage-dashboard');
    if (!current || (current.contains(document.activeElement) && document.activeElement.matches('input,select,textarea'))) {schedule(); return;}
    busy = true;
    const controller = new AbortController();
    activeController = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      requests++;
      observe('request-started');
      const response = await fetch(window.location.href, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error('unavailable');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = parsed.querySelector('.usage-dashboard');
      if (!next) throw new Error('missing dashboard');
      if (document.hidden || controller.signal.aborted) return;
      // Preserve the user's current state after the request, not when it started.
      if (current.contains(document.activeElement) && document.activeElement.matches('input,select,textarea')) return;
      const folds = new Map([...current.querySelectorAll('details')].map(node => [key(node), node.open]));
      const session = current.querySelector('#context-session')?.value;
      const point = current.querySelector('#context-point')?.value;
      const chartScroll = current.querySelector('.hour-chart')?.scrollLeft;
      const scroll = {left: window.scrollX, top: window.scrollY};
      next.querySelectorAll('script[src]').forEach(node => node.remove());
      current.replaceWith(next);
      next.querySelectorAll('details').forEach(node => {if (folds.has(key(node))) node.open = folds.get(key(node));});
      document.dispatchEvent(new Event('usage:refreshed'));
      const select = next.querySelector('#context-session');
      if (select && [...select.options].some(option => option.value === session)) {
        select.value = session;
        select.dispatchEvent(new Event('change'));
      }
      const slider = next.querySelector('#context-point');
      if (slider && point !== undefined) {
        slider.value = Math.min(Number(point), Number(slider.max));
        slider.dispatchEvent(new Event('input'));
      }
      const chart = next.querySelector('.hour-chart');
      if (chart && chartScroll !== undefined) chart.scrollLeft = chartScroll;
      window.scrollTo(scroll);
      status.textContent = 'تم تحديث العرض تلقائيًا في ' + new Intl.DateTimeFormat('ar-QA', {hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date()) + '؛ موعد مزامنة البيانات الفعلي موضح أدناه.';
    } catch (error) {
      if (!document.hidden && controller.signal.reason !== 'hidden') status.textContent = 'تعذر تحديث العرض تلقائيًا؛ البيانات المعروضة محفوظة. ستُعاد المحاولة خلال دقيقة.';
    } finally {
      observe('request-finished');
      clearTimeout(timeout);
      busy = false;
      activeController = null;
      if (resumePending && !document.hidden) {
        resumePending = false;
        refresh();
      } else schedule();
    }
  }
  schedule();
  document.addEventListener('visibilitychange', () => {
    observe(document.hidden ? 'hidden' : 'visible');
    clearTimeout(timer);
    timer = null;
    if (document.hidden) {
      resumePending = false;
      activeController?.abort('hidden');
    } else if (busy) {
      resumePending = true;
    } else refresh();
  });
})();
