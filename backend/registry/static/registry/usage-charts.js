(() => {
  const source = document.getElementById('usage-chart-data');
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const formatter = new Intl.NumberFormat('en-US');
  const status = document.getElementById('chart-selection');
  const selectable = (element, text) => {
    element.tabIndex = 0; element.setAttribute('role', 'button');
    element.setAttribute('aria-label', text);
    const select = () => { document.querySelectorAll('[aria-pressed="true"]').forEach(node=>node.setAttribute('aria-pressed','false'));status.textContent = text; element.setAttribute('aria-pressed', 'true'); };
    element.addEventListener('click', select);
    element.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
  };
  document.querySelectorAll('.hour-column').forEach((node, i) => {
    const row=data.hourly[i]; if(row)selectable(node,`${row.at} — ${formatter.format(row.tokens)} توكن مرصود`);
  });
  document.querySelectorAll('.model-bar').forEach((node, i) => {
    const row=data.models[i]; if(row)selectable(node,`${row.name}: ${formatter.format(row.tokens)} توكن، ${row.share}٪، ${row.intervals} فترة. إسناد تقديري.`);
  });
  const select = document.getElementById('context-session'), slider = document.getElementById('context-point');
  const svg=document.getElementById('context-chart'), detail=document.getElementById('context-detail');
  const groups = new Map();
  (data.context || []).forEach(row => { if(!groups.has(String(row.session_id)))groups.set(String(row.session_id),[]);groups.get(String(row.session_id)).push(row); });
  groups.forEach((rows,name)=>{const option=document.createElement('option');option.value=name;option.textContent=rows[0].session;select.append(option);});
  const ns='http://www.w3.org/2000/svg';
  const shape=(tag,attrs)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v)));svg.append(e);return e;};
  let rows=[],positions=[];
  const highlight=()=>{
    const index=Number(slider.value), row=rows[index];if(!row)return;
    svg.querySelector('[data-marker]')?.remove();
    if(row.percent!==null)shape('circle',{cx:positions[index].x,cy:positions[index].y,r:6,'data-marker':'1',class:'context-marker'});
    detail.textContent=`${row.session} · ${row.at} · ${formatter.format(row.tokens)} / ${row.limit?formatter.format(row.limit):'سعة غير معروفة'} توكن · ${row.percent===null?'نسبة غير متاحة':row.percent+'٪'} · العينة ${index+1} من ${rows.length}`;
  };
  const draw=()=>{
    svg.replaceChildren();rows=groups.get(select.value)||[];
    if(!rows.length){detail.textContent='لا توجد عينات في الفترة المختارة.';slider.disabled=true;return;}
    slider.disabled=false;slider.max=rows.length-1;slider.value=rows.length-1;
    [0,50,100].forEach(value=>{const y=205-value*1.8;shape('line',{x1:45,x2:880,y1:y,y2:y,class:'context-grid'});const label=shape('text',{x:4,y:y+4,class:'context-label'});label.textContent=value+'%';});
    const start=Date.parse(rows[0].timestamp),end=Date.parse(rows[rows.length-1].timestamp);
    positions=rows.map(row=>({x:45+(end===start?0:(Date.parse(row.timestamp)-start)/(end-start))*835,y:205-Math.max(0,Math.min(100,row.percent))*1.8}));
    let segment=[];const flush=()=>{if(segment.length)shape('polyline',{points:segment.join(' '),class:'context-line'});segment=[];};
    positions.forEach((p,i)=>{if(rows[i].percent===null){flush();return;}segment.push(`${p.x},${p.y}`);});flush();highlight();
  };
  svg.addEventListener('pointerdown',e=>{if(!positions.length)return;const point=svg.createSVGPoint();point.x=e.clientX;point.y=e.clientY;const local=point.matrixTransform(svg.getScreenCTM().inverse());let nearest=0;positions.forEach((p,i)=>{if(Math.abs(p.x-local.x)<Math.abs(positions[nearest].x-local.x))nearest=i;});slider.value=nearest;highlight();});
  select.addEventListener('change',draw);slider.addEventListener('input',highlight);draw();
})();

