const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../registry/static/registry/usage-refresh.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture(pending = false) {
  let now = 0, serial = 0, requests = 0, maxConcurrent = 0, concurrent = 0;
  const timers = new Map(), events = {}, urls = [];
  const status = {textContent:'initial',dataset:{}};
  function makeRoot(initial) {
    const fold = {dataset:{refreshKey:'session-2'},id:'',open:initial};
    const select = {value:initial?'2':'1',options:[{value:'1'},{value:'2'}],dispatchEvent:()=>{}};
    const slider = {value:initial?'7':'0',max:'20',dispatchEvent:()=>{}};
    const chart = {scrollLeft:initial?123:0};
    return {fold,select,slider,chart,contains:()=>false,
      querySelector:selector=>({'#context-session':select,'#context-point':slider,'.hour-chart':chart}[selector] || null),
      querySelectorAll:selector=>selector==='details'?[fold]:[],replaceWith:next=>{root=next;}};
  }
  let root = makeRoot(true);
  const document = {getElementById:()=>status,hidden:false,activeElement:{matches:()=>false},querySelector:selector=>selector==='#usage-auto-refresh-status'?status:root,
    addEventListener:(name,callback)=>{events[name]=callback;},dispatchEvent:()=>{}};
  const window = {location:{href:'http://localhost/usage/?session=2&period=day'},scrollX:0,scrollY:456,scrollTo:position=>{window.scrollY=position.top;}};
  const response = {ok:true,redirected:false,text:async()=>'<dashboard>'};
  const fetch = (url, options) => {
    requests++; concurrent++; maxConcurrent=Math.max(maxConcurrent,concurrent); urls.push(url);
    if (!pending) {concurrent--;return Promise.resolve(response);}
    return new Promise((resolve,reject)=>{
      options.signal.addEventListener('abort',()=>{concurrent--;reject(new Error('aborted'));},{once:true});
    });
  };
  vm.runInNewContext(code,{document,window,fetch,AbortController,Intl,Date,Event,
    DOMParser:class {parseFromString(){const next=makeRoot(false);return {querySelector:()=>next};}},
    setTimeout:(callback,delay)=>{const id=++serial;timers.set(id,{at:now+delay,callback});return id;},
    clearTimeout:id=>timers.delete(id)});
  return {document,status,urls,window,get requests(){return requests;},get maxConcurrent(){return maxConcurrent;},
    async visibility(hidden){document.hidden=hidden;events.visibilitychange();await settle();},
    async advance(duration){const target=now+duration;while(true){const due=[...timers.entries()].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);due[1].callback();await settle();}now=target;},
    timers,get root(){return root;}};
}
test('hidden tab makes no requests; return refreshes once and restarts the full minute',async()=>{
  const f=fixture();await f.advance(59000);await f.visibility(true);await f.advance(120000);
  assert.equal(f.requests,0);assert.equal(f.status.textContent,'initial');
  await f.visibility(false);assert.equal(f.requests,1);
  await f.advance(1000);assert.equal(f.requests,1);
  await f.advance(59000);assert.equal(f.requests,2);
  assert.equal(f.urls[0],'http://localhost/usage/?session=2&period=day');assert.equal(f.window.scrollY,456);
  assert.equal(f.root.fold.open,true);assert.equal(f.root.select.value,'2');
  assert.equal(f.root.slider.value,7);assert.equal(f.root.chart.scrollLeft,123);
  const observations=JSON.parse(f.status.dataset.refreshObservations);
  assert.equal(observations.find(e=>e.event==='hidden').requests,0);
  assert.equal(observations.find(e=>e.event==='visible').requests,0);
  assert.equal(observations.filter(e=>e.event==='request-started').length,2);
});
test('hide aborts inflight display request; rapid return resumes once without overlap',async()=>{
  const f=fixture(true);await f.advance(60000);assert.equal(f.requests,1);
  const hiding=f.visibility(true);const returning=f.visibility(false);await Promise.all([hiding,returning]);
  assert.equal(f.requests,2);assert.equal(f.maxConcurrent,1);
  assert.equal(f.status.textContent,'initial');await f.visibility(true);await f.advance(120000);assert.equal(f.requests,2);
});
test('background synchronization is never triggered by refresh: only filtered GET is fetched',async()=>{
  const f=fixture();await f.advance(180000);
  assert.equal(f.requests,3);assert.ok(f.urls.every(url=>url.endsWith('/usage/?session=2&period=day')));
});

test('visibility observations remain bounded and page-local',async()=>{
  const f=fixture();await f.advance(1200000);
  const observations=JSON.parse(f.status.dataset.refreshObservations);
  assert.equal(observations.length,20);
  assert.equal(observations.at(-1).requests,20);
});

test('a foreground timeout reports failure and schedules the next attempt',async()=>{
  const f=fixture(true);await f.advance(80000);
  assert.match(f.status.textContent,/تعذر تحديث العرض/);
  assert.equal(f.requests,1);await f.advance(60000);assert.equal(f.requests,2);
});
