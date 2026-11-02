(() => {
  'use strict';
  let selection = '', selectedAt = null;
  const ns = 'http://www.w3.org/2000/svg';
  const fmt = value => new Intl.NumberFormat('ar-QA', {maximumFractionDigits:1}).format(value);
  const time = value => new Intl.DateTimeFormat('ar-QA', {dateStyle:'short',timeStyle:'medium',timeZone:'Asia/Qatar'}).format(new Date(value));
  function init() {
    const root = document.querySelector('.runtime-context');
    if (!root) return;
    const dataNode = root.querySelector('#runtime-context-data');
    let series;
    try {series=JSON.parse(dataNode.textContent);} catch {return;}
    const panel=root.querySelector('#runtime-history'), select=root.querySelector('#runtime-session');
    const chart=root.querySelector('#runtime-chart'), slider=root.querySelector('#runtime-point');
    const caption=root.querySelector('#runtime-chart-caption'), event=root.querySelector('#runtime-model-event');
    if (!series.length) return;
    panel.hidden=false;
    select.replaceChildren();
    for (const row of series) {const option=document.createElement('option');option.value=String(row.id);option.textContent=row.name;select.append(option);}
    const newest=[...series].sort((a,b)=>(Date.parse(b.points.at(-1)?.at)||0)-(Date.parse(a.points.at(-1)?.at)||0))[0];
    select.value=series.some(s=>String(s.id)===selection)?selection:String(newest.id);
    function draw() {
      selection=select.value;
      const rows=series.find(s=>String(s.id)===selection)?.points||[];
      root.querySelector('#runtime-history-empty').hidden=rows.length>0;
      chart.hidden=slider.hidden=!rows.length;caption.textContent='';event.textContent='';
      chart.replaceChildren();if (!rows.length) return;
      slider.max=String(rows.length-1);
      const previous=rows.findIndex(r=>r.at===selectedAt);
      slider.value=String(previous>=0?previous:rows.length-1);
      const max=Math.max(100,...rows.map(r=>r.percent));
      const first=Date.parse(rows[0].at), span=Math.max(1,Date.parse(rows.at(-1).at)-first);
      const width=Math.max(300,chart.clientWidth), left=40, right=width-20;
      chart.setAttribute('viewBox','0 0 '+width+' 200');
      const x=r=>left+(Date.parse(r.at)-first)/span*(right-left), y=p=>175-p/max*155;
      function svg(tag,attrs,text) {const el=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));if(text!==undefined)el.textContent=text;chart.append(el);return el;}
      for(const value of [0,max]){svg('line',{x1:left,x2:right,y1:y(value),y2:y(value),class:'grid'});svg('text',{x:4,y:y(value)+4},fmt(value)+'٪');}
      for(const value of [70,85])svg('line',{x1:left,x2:right,y1:y(value),y2:y(value),class:'reference'});
      let path='';rows.forEach((r,i)=>{const prev=rows[i-1];const gap=!prev||r.generation!==prev.generation||r.process!==prev.process||Date.parse(r.at)-Date.parse(prev.at)>300000;path+=(gap?'M':'L')+x(r)+','+y(r.percent)+' ';});
      svg('path',{d:path,class:'series'});
      rows.forEach((r,i)=>{const prev=rows[i-1];if(prev?.model&&r.model&&prev.model!==r.model){const mark=svg('circle',{cx:x(r),cy:y(r.percent),r:3,class:'point'});const tip=document.createElementNS(ns,'title');tip.textContent='تغير النموذج المسجل: '+prev.model+' ← '+r.model;mark.append(tip);}});
      const shortTime=value=>new Intl.DateTimeFormat('ar-QA',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Qatar'}).format(new Date(value));
      svg('text',{x:left,y:198},shortTime(rows[0].at));svg('text',{x:right,y:198,'text-anchor':'end'},shortTime(rows.at(-1).at));
      const point=svg('circle',{r:5,class:'point'});const title=document.createElementNS(ns,'title');point.append(title);
      function inspect() {
        const index=Number(slider.value), r=rows[index], prev=rows[index-1];
        point.setAttribute('cx',x(r));point.setAttribute('cy',y(r.percent));
        const text=time(r.at)+' — '+fmt(r.percent)+'٪؛ '+fmt(r.used)+' من '+fmt(r.limit)+' رمز'+(r.model?'؛ '+r.model:'؛ النموذج غير مؤكد');
        caption.textContent=text;title.textContent=text;slider.setAttribute('aria-valuetext',text);
        selectedAt=index===rows.length-1?null:r.at;
        event.textContent=prev&&prev.model&&r.model&&prev.model!==r.model?'تغير النموذج المسجل بين هاتين العينتين: '+prev.model+' ← '+r.model:'';
      }
      slider.oninput=inspect;inspect();
      chart.onclick=e=>{const box=chart.getBoundingClientRect();const at=first+((e.clientX-box.left)/box.width*width-left)/(right-left)*span;let nearest=0;rows.forEach((r,i)=>{if(Math.abs(Date.parse(r.at)-at)<Math.abs(Date.parse(rows[nearest].at)-at))nearest=i;});slider.value=String(nearest);inspect();};
    }
    select.onchange=()=>{selectedAt=null;draw();};draw();
  }
  document.addEventListener('usage:refreshed',init);
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(init,100);});
  init();
})();
