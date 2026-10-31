(() => {
  const source = document.getElementById('usage-chart-data');
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const formatter = new Intl.NumberFormat('en-US');
  document.querySelectorAll('.usage-dashboard .usage-metrics strong').forEach(element => {
    const match = element.textContent.trim().match(/^(\d+)(.*)$/);
    if (match) element.textContent = formatter.format(Number(match[1])) + match[2];
  });
  const status = document.getElementById('chart-selection');
  const pulseStatus = document.getElementById('pulse-selection');
  const selectable = (element, text) => {
    element.tabIndex = 0; element.setAttribute('role', 'button');
    element.setAttribute('aria-label', text); element.setAttribute('aria-pressed', 'false');
    const select = () => { document.querySelectorAll('[aria-pressed="true"]').forEach(node=>node.setAttribute('aria-pressed','false'));if(status)status.textContent = text;if(pulseStatus&&element.classList.contains('hour-column'))pulseStatus.textContent=text;element.setAttribute('aria-pressed', 'true'); };
    element.addEventListener('click', select);
    element.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
  };
  document.querySelectorAll('.hour-column').forEach((node, i) => {
    const row=data.hourly[i]; if(row){const text=`${row.at} بتوقيت قطر — ${formatter.format(row.tokens)} رمز مرصود${row.tokens===0?'، لا توجد زيادة مسجلة':''}`;selectable(node,text);}
  });
  document.querySelectorAll('.hour-y-axis span').forEach(node => { const value=Number(node.textContent);if(Number.isFinite(value))node.textContent=formatter.format(value); });
  document.querySelectorAll('.hour-chart').forEach(chart => {
    const bars=[...chart.querySelectorAll('.hour-column')];
    bars.forEach((bar,index) => {
      bar.tabIndex=index===bars.length-1?0:-1;
      bar.addEventListener('keydown',event=>{
        const nextIndex=event.key==='ArrowRight'?index+1:event.key==='ArrowLeft'?index-1:-1;
        if(nextIndex<0||nextIndex>=bars.length)return;
        event.preventDefault();bar.tabIndex=-1;bars[nextIndex].tabIndex=0;bars[nextIndex].focus();bars[nextIndex].click();
      });
    });
    const peakIndex = data.peak && data.peak.tokens > 0
      ? data.hourly.findIndex(row => row.at === data.peak.at && row.tokens === data.peak.tokens)
      : -1;
    if (peakIndex >= 0) bars[peakIndex]?.click();
    chart.scrollLeft = chart.scrollWidth;
  });
  document.querySelectorAll('.model-bar').forEach((node, i) => {
    const row=data.models[i]; if(row)selectable(node,`${row.name}: ${formatter.format(row.tokens)} رمز، ${row.share}٪، ${row.intervals} فترة. إسناد تقديري.`);
  });
  const select = document.getElementById('context-session');
  const slider = document.getElementById('context-point');
  const svg = document.getElementById('context-chart');
  const detail = document.getElementById('context-detail');
  const summary = document.getElementById('context-summary');
  const indexLabel = document.getElementById('context-index');
  const groups = new Map();
  (data.context || []).forEach(row => {
    const key = String(row.session_id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  groups.forEach((rows, key) => {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = rows[0].session;
    select.append(option);
  });

  const ns = 'http://www.w3.org/2000/svg';
  const plot = {left: 64, right: 850, top: 30, bottom: 208};
  const maxGapMs = 5 * 60 * 1000;
  const maxRenderedPoints = 850;
  const percentFormatter = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1});
  const timeFormatter = new Intl.DateTimeFormat('ar-QA', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23', timeZone: 'Asia/Qatar'
  });
  const shape = (tag, attrs, parent = svg) => {
    const element = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
    parent.append(element);
    return element;
  };
  let rows = [];
  let positions = [];
  let gapBefore = new Set();

  const summarize = () => {
    const known = rows.map((row, index) => ({row, index}))
      .filter(item => Number.isFinite(item.row.percent));
    if (!known.length) {
      summary.textContent = 'لا تتوفر قراءات ذات سعة معروفة لهذه الجلسة في الفترة المختارة.';
      return;
    }
    const low = known.reduce((best, item) => item.row.percent < best.row.percent ? item : best);
    const high = known.reduce((best, item) => item.row.percent > best.row.percent ? item : best);
    summary.textContent = `${known.length} قراءة صالحة · أدنى قراءة ${percentFormatter.format(low.row.percent)}٪ (${low.row.at}) · أعلى قراءة ${percentFormatter.format(high.row.percent)}٪ (${high.row.at}).`;
  };

  const highlight = () => {
    const index = Number(slider.value);
    const row = rows[index];
    if (!row) return;
    svg.querySelector('[data-marker]')?.remove();
    const point = positions[index];
    if (point) {
      shape('circle', {cx: point.x, cy: point.y, r: 7, 'data-marker': '1', class: 'context-marker'});
    }
    const value = Number.isFinite(row.percent) ? `${percentFormatter.format(row.percent)}٪` : 'النسبة غير متاحة';
    const gapNote = gapBefore.has(index) ? ' · بداية بعد فجوة رصد' : '';
    slider.setAttribute('aria-valuetext', `${row.at}، ${value}، العينة ${index + 1} من ${rows.length}`);
    indexLabel.textContent = `العينة ${index + 1} من ${rows.length}`;
    detail.textContent = `${row.session} · ${row.at} · ${formatter.format(row.tokens)} / ${row.limit ? formatter.format(row.limit) : 'سعة غير معروفة'} رمز · ${value}${gapNote}`;
  };

  const simplify = (indices, stride) => {
    if (stride <= 1 || indices.length <= 2) return indices;
    const output = [];
    for (let start = 0; start < indices.length; start += stride) {
      const bucket = indices.slice(start, start + stride);
      let low = bucket[0];
      let high = bucket[0];
      bucket.forEach(index => {
        if (rows[index].percent < rows[low].percent) low = index;
        if (rows[index].percent > rows[high].percent) high = index;
      });
      [low, high].sort((a, b) => a - b).forEach(index => {
        if (output[output.length - 1] !== index) output.push(index);
      });
    }
    return output;
  };

  const draw = () => {
    svg.replaceChildren();
    const title = shape('title', {id: 'context-chart-title'});
    title.textContent = 'تغير نسبة استخدام نافذة السياق عبر الزمن';
    rows = groups.get(select.value) || [];
    positions = [];
    gapBefore = new Set();
    if (!rows.length) {
      slider.disabled = true;
      select.disabled = groups.size === 0;
      indexLabel.textContent = '';
      summary.textContent = 'لا توجد عينات سياق في الفترة المختارة.';
      detail.textContent = 'اختر فترة أو جلسة تحتوي على قراءات.';
      const empty = shape('text', {x: 450, y: 140, 'text-anchor': 'middle', class: 'context-empty'});
      empty.textContent = 'لا توجد قراءات في هذه الفترة';
      return;
    }

    select.disabled = false;
    slider.disabled = false;
    slider.max = rows.length - 1;
    slider.value = rows.length - 1;
    const validTimes = rows.map(row => Date.parse(row.timestamp)).filter(Number.isFinite);
    const startTime = validTimes[0];
    const endTime = validTimes[validTimes.length - 1];
    const xFor = timestamp => plot.left + (endTime === startTime ? 0.5 : (timestamp - startTime) / (endTime - startTime)) * (plot.right - plot.left);
    const yFor = value => plot.bottom - (Math.max(0, Math.min(100, value)) / 100) * (plot.bottom - plot.top);
    positions = rows.map(row => {
      const timestamp = Date.parse(row.timestamp);
      if (!Number.isFinite(timestamp) || !Number.isFinite(row.percent)) return null;
      return {x: xFor(timestamp), y: yFor(row.percent), timestamp};
    });

    const segments = [];
    const gaps = [];
    let segment = [];
    let previousIndex = null;
    rows.forEach((row, index) => {
      const point = positions[index];
      if (!point) {
        if (segment.length) segments.push(segment);
        segment = [];
        previousIndex = null;
        return;
      }
      if (previousIndex !== null && point.timestamp - positions[previousIndex].timestamp > maxGapMs) {
        segments.push(segment);
        gaps.push([previousIndex, index]);
        gapBefore.add(index);
        segment = [];
      }
      segment.push(index);
      previousIndex = index;
    });
    if (segment.length) segments.push(segment);
    const validCount = segments.reduce((count, part) => count + part.length, 0);
    const stride = Math.max(1, Math.ceil(validCount / (maxRenderedPoints / 2)));

    [0, 50, 100].forEach(value => {
      const y = yFor(value);
      shape('line', {x1: plot.left, x2: plot.right, y1: y, y2: y, class: 'context-grid'});
      const label = shape('text', {x: plot.right + 42, y: y + 4, 'text-anchor': 'end', class: 'context-label'});
      label.textContent = `${value}٪`;
    });
    gaps.forEach(([leftIndex, rightIndex]) => {
      const x1 = positions[leftIndex].x;
      const x2 = positions[rightIndex].x;
      if (x2 - x1 < 28) return;
      [x1, x2].forEach(x => shape('path', {d: `M ${x - 4} ${plot.top - 7} l 4 4 l 4 -4 M ${x - 4} ${plot.bottom + 7} l 4 -4 l 4 4`, class: 'context-break-mark'}));
    });

    segments.forEach(part => {
      const sampled = simplify(part, stride);
      if (sampled.length < 2) return;
      const coordinates = sampled.map(index => `${positions[index].x},${positions[index].y}`).join(' ');
      shape('polyline', {points: coordinates, class: 'context-line'});
    });

    const ticks = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])];
    let previousTickX = -Infinity;
    ticks.forEach((index, tick) => {
      const timestamp = Date.parse(rows[index].timestamp);
      if (!Number.isFinite(timestamp)) return;
      const x = xFor(timestamp);
      shape('line', {x1: x, x2: x, y1: plot.top, y2: plot.bottom, class: 'context-grid context-grid-vertical'});
      if (Math.abs(x - previousTickX) < 72 && tick !== ticks.length - 1) return;
      const label = shape('text', {x, y: 250, 'text-anchor': tick === 0 ? 'start' : tick === ticks.length - 1 ? 'end' : 'middle', class: 'context-time-label'});
      label.textContent = timeFormatter.format(new Date(timestamp));
      previousTickX = x;
    });
    if (gaps.length) {
      const note = shape('text', {x: plot.right, y: 18, 'text-anchor': 'end', class: 'context-gap-count'});
      note.textContent = `${gaps.length} فجوة رصد أطول من 5 دقائق`;
    }
    summarize();
    highlight();
  };

  svg.addEventListener('pointerdown', event => {
    if (!positions.length) return;
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(matrix.inverse());
    let nearest = -1;
    positions.forEach((position, index) => {
      if (position && (nearest < 0 || Math.abs(position.x - local.x) < Math.abs(positions[nearest].x - local.x))) nearest = index;
    });
    if (nearest >= 0) {
      slider.value = nearest;
      highlight();
    }
  });
  select.addEventListener('change', draw);
  slider.addEventListener('input', highlight);
  draw();
})();

