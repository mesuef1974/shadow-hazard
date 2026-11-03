'use strict';
(() => {
const $ = id => document.getElementById(id), form = $('experiment');
const presets = JSON.parse($('scenario-data').textContent);
const canvas = $('scene'), ctx = canvas.getContext('2d');
const busImage = new Image();
busImage.onload=()=>draw();
busImage.src=canvas.dataset.busImage;
const carImage = new Image();
carImage.onload=()=>draw();
carImage.src=canvas.dataset.carImage;
let result = null, frameIndex = 0, playing = false, timer = null;
let revision = 0, batchRunning = false, batchStop = false, finishBatchScene = null, currentRunId = null;
const descriptions = {
 success:'نقارن المشهد نفسه: بعد التنبيه ينتظر السائق المهلة المحددة ثم يفرمل. النجاح يتطلب تنبيهًا قبل ظهور الطالب وتجنب تداخل يحدث دون استجابة.',
 crossing:'يتحرك الطالب بمحاذاة الحافلة ثم يعبر الممر، بينما تقترب السيارة. نفحص تقارب وقت الوصول.',
 late:'ينتظر الطالب 7 ثوانٍ قبل التحرك؛ تمر السيارة أولًا في الإعداد الأصلي.',
 early:'تتحرك السيارة ببطء ليعبر الطالب قبل وصولها في الإعداد الأصلي.',
 standing:'يبقى الطالب ثابتًا قرب الحافلة طوال التجربة.',
 parked:'تبقى السيارة ثابتة بعيدًا عن الممر بينما يتحرك الطالب.',
 stop:'يتحرك الطالب ثم يتوقف عند الثانية الثانية قبل دخول الممر.',
 reverse:'يتحرك الطالب ثم يرجع عند الثانية الثانية إلى نقطة البداية.',
 brake:'تتناقص سرعة السيارة بمقدار 2 م/ث كل ثانية حتى تتوقف.',
 accelerate:'تزداد سرعة السيارة بمقدار 0.5 م/ث كل ثانية.',
 away:'يكمل الطالب مستقيمًا على الرصيف ولا ينعطف إلى الممر.',
 noise:'الحركة الأساسية نفسها، مع خطأ اصطناعي في قياس الموقع.',
 delay:'الحركة الأساسية نفسها، لكن الرصد يصل متأخرًا؛ تظهر حالة غير موثوق.',
 loss:'تنقطع كاميرا الطالب من الثانية 1 إلى 8 بينما تستمر حركته الفعلية.',
 dropout:'تُفقد بعض المشاهدات عشوائيًا؛ لا يتوقف الجسم عند فقد الرصد.',
 confidence:'الحركة الأساسية نفسها، لكن ثقة الرصد أقل من حد الاعتماد.',
 calibration:'الحركة الأساسية نفسها، ويُرفض الرصد لأن معايرته غير صالحة.',
 clock:'الحركة الأساسية نفسها، مع تقديم ساعة مصدر الطالب بمقدار 0.4 ثانية.',
 missing:'مصدر الطالب غير متاح منذ البداية؛ الطالب موجود في المشهد لكن لا يُرصد.',
 fast:'تتحرك السيارة بسرعة أعلى؛ نفحص هل تمر قبل وصول الطالب.',
 slow:'يتحرك الطالب ببطء؛ نفحص اختلاف توقيت عبوره عن مرور السيارة.'
};
function describe(config, savedId=null){
 $('driver-settings').hidden=false;
 if(config.key==='custom'){
  const choice=(name)=>form.elements[name].querySelector(`option[value="${config[name]}"]`)?.textContent||'';
  $('scene-title').textContent='تجربة من اختياراتك';
  $('scene-description').textContent=`الطالب: ${choice('pedestrian_behavior')} · السائق: ${choice('driver_behavior')} · الإشارات: ${choice('signals')} · الانطلاق: ${choice('crossing_side')} · الرصد: ${choice('sensor_fault')}. زمن تغير السلوك ${config.behavior_time} ث، والتوقف داخل الممر ${config.pause_duration} ث.`;
  $('scene-config').textContent=`${savedId?'سجل #'+savedId:'معاينة غير محفوظة'} · السيارة ${config.car_speed} م/ث · الطالب ${config.pedestrian_speed} م/ث · التأخير ${config.pedestrian_delay} ث · أفق التوقع ${config.horizon} ث · مدة المتابعة ${config.duration ?? 20} ث`;
  return;
 }
 const original=presets.find(s=>s.key===config.key);
 const modified=original&&['car_speed','pedestrian_speed','pedestrian_delay','noise','latency','dropout','confidence','horizon','margin','reaction_time','braking_deceleration'].some(k=>Number(config[k])!==original[k]);
 $('scene-title').textContent=(config.title || original?.title || 'مقارنة مخصصة')+(modified?' — إعدادات معدلة':'');
 $('scene-description').textContent=(modified?'وصف الإعداد الأصلي؛ القيم المعدلة قد تغيّر النتيجة: ':'')+(descriptions[config.key] || 'مقارنة سرعات وأوقات انطلاق محددة.');
 $('scene-config').textContent=`${savedId ? 'سجل #'+savedId : 'معاينة غير محفوظة'} · السيارة ${config.car_speed} م/ث · الطالب ${config.pedestrian_speed} م/ث · تأخير الطالب ${config.pedestrian_delay} ث`;
}
const states = {UNKNOWN:'غير متاح', DEGRADED:'غير موثوق', HAZARD_SUSPECTED:'انتبه للمشاة', CLEAR_WITHIN_SCOPE:'لا تعارض متوقع ضمن الإعدادات'};
let zoom=1, METRE=16, panX=0, panY=0, drag=null;
const xy = p => [200+(140+p[0]*16-200)*zoom+panX,500+(960-(p[1]+38)*16-500)*zoom+panY];
function geometry(){return selectedScene()?.geometry||{road_edges:[0,7],lane_divider:3.5,crosswalk_half_width:.7,stop_line_y:-.7,sizes:{car:[1.8,4],pedestrian:[.6,.6]}};}
function footprint(actor){return geometry().sizes[actor].map(n=>n*METRE);}
function line(a,b,color,width=2){ctx.beginPath();ctx.moveTo(...xy(a));ctx.lineTo(...xy(b));ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();}
function box(x,y,w,h,color){const p=xy([x,y+h]);ctx.fillStyle=color;ctx.fillRect(p[0],p[1],w*METRE,h*METRE);}
let sceneLabels=[];
function label(text,p){sceneLabels.push({text,point:xy(p)});}
function paintLabels(){
 const placed=[];ctx.save();ctx.font='600 16px "IBM Plex Sans Arabic", sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
 for(const {text,point:[px,py]} of sceneLabels){
  if(px<0||px>canvas.width||py<0||py>canvas.height)continue;
  const w=ctx.measureText(text).width+14,h=26,x=Math.max(2,Math.min(canvas.width-w-2,px-w/2));
  let y=Math.max(2,Math.min(canvas.height-h-2,py-h/2));
  for(let attempt=0;attempt<8&&placed.some(r=>x<r.x+r.w&&x+w>r.x&&y<r.y+r.h&&y+h>r.y);attempt++)y=Math.max(2,Math.min(canvas.height-h-2,y+h+3));
  roundedRect(x,y,w,h,4,'#f4f7fb');ctx.strokeStyle='#8293a5';ctx.lineWidth=1;ctx.stroke();
  ctx.fillStyle='#142d4e';ctx.fillText(text,x+w/2,y+h/2);placed.push({x,y,w,h});
 }
 ctx.restore();
}
function selectedScene(){return result?.comparison && $('comparison-view').value==='control'?result.comparison:result;}
function roundedRect(x,y,w,h,r,color){ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fillStyle=color;ctx.fill();}
function drawCar(position,frame){
 const [x,y]=xy(position),[w,h]=footprint('car');ctx.save();ctx.translate(x,y);
 if(carImage.complete&&carImage.naturalWidth){
  const scale=Math.min(w/carImage.naturalWidth,h/carImage.naturalHeight);
  const iw=carImage.naturalWidth*scale,ih=carImage.naturalHeight*scale;
  ctx.drawImage(carImage,-iw/2,-ih/2,iw,ih);
 }else roundedRect(-w/2,-h/2,w,h,4,'#235b87');
 // The outline is the same finite body used by collision calculations.
 ctx.strokeStyle='#84c0ff';ctx.lineWidth=.7;ctx.strokeRect(-w/2,-h/2,w,h);
 if(frame.hazards_active){
  const lit=frame.hazards_lit||matchMedia('(prefers-reduced-motion: reduce)').matches;
  ctx.shadowColor='#ffbd19';ctx.shadowBlur=lit?5:0;
  for(const side of [-1,1])for(const end of [-1,1]){
   roundedRect(side*(w/2-2)-1.5,end*(h/2-3)-1.5,3,3,1,lit?'#ffbd19':'#73592c');
  }
 }
 ctx.restore();
}
function drawStudent(position,scene){
 const current=xy(position);let heading=-Math.PI/2;
 for(let i=frameIndex-1;i>=0;i--){const previous=xy(scene.frames[i].truth.pedestrian);if(Math.hypot(current[0]-previous[0],current[1]-previous[1])>.1){heading=Math.atan2(current[1]-previous[1],current[0]-previous[0]);break;}}
 const [pw,ph]=footprint('pedestrian');
 ctx.save();ctx.strokeStyle='#007f82';ctx.lineWidth=.7;ctx.strokeRect(current[0]-pw/2,current[1]-ph/2,pw,ph);
 ctx.translate(...current);ctx.rotate(heading+Math.PI/2);ctx.scale(Math.min(pw,ph)/36,Math.min(pw,ph)/36);
 // Shoulders, head and a rear backpack distinguish the pupil from a location dot.
 roundedRect(-9,5,7,9,3,'#142d4e');roundedRect(2,5,7,9,3,'#142d4e');
 roundedRect(-13,-4,7,12,3,'#f0bd91');roundedRect(6,-4,7,12,3,'#f0bd91');
 roundedRect(-10,-6,20,17,6,'#007f82');
 roundedRect(-7,-4,3,13,1,'#f4b544');roundedRect(4,-4,3,13,1,'#f4b544');
 roundedRect(-8,3,16,12,4,'#f4b544');roundedRect(-5,8,10,5,2,'#bc7924');
 ctx.beginPath();ctx.ellipse(0,-8,7,8,0,0,Math.PI*2);ctx.fillStyle='#f0bd91';ctx.fill();
 ctx.beginPath();ctx.ellipse(0,-6,7,6,0,0,Math.PI*2);ctx.fillStyle='#302c29';ctx.fill();
 ctx.restore();
}
function drawSignals(state){
 const edge=geometry().crosswalk_half_width;
 for(const position of [[-1.3,edge+1.5],[8.3,edge+1.5]]){
  const [x,y]=xy(position);roundedRect(x-6,y-15,12,28,3,'#142d4e');
  for(const [dy,color,on] of [[-7,'#ef5549',state==='RED'],[6,'#39d79a',state==='GREEN']]){
   ctx.beginPath();ctx.arc(x,y+dy,4,0,Math.PI*2);ctx.fillStyle=on?color:'#526477';ctx.fill();
  }
  if(state==='UNAVAILABLE')label('؟',[position[0],position[1]+2.5],'#142d4e');
 }
}
function routeCrossing(routes){
 for(let i=1;i<routes.car.length;i++)for(let j=1;j<routes.pedestrian.length;j++){
  const a=routes.car[i-1],b=routes.car[i],c=routes.pedestrian[j-1],d=routes.pedestrian[j];
  const u=[b[0]-a[0],b[1]-a[1]],v=[d[0]-c[0],d[1]-c[1]],w=[c[0]-a[0],c[1]-a[1]];
  const cross=(x,y)=>x[0]*y[1]-x[1]*y[0],den=cross(u,v);if(Math.abs(den)<1e-9)continue;
  const t=cross(w,v)/den,s=cross(w,u)/den;if(t>=0&&t<=1&&s>=0&&s<=1)return[a[0]+t*u[0],a[1]+t*u[1]];
 }
 return null;
}
function drawConflict(scene,f){
 const crossing=routeCrossing(scene.routes), predicted=f.prediction.state==='HAZARD_SUSPECTED';
 const incident=$('incident');incident.className=f.collision?'collision':predicted?'predicted':'neutral';
 incident.textContent=f.collision?`حدث تداخل فعلي — فشل تجنب الاصطدام · ${f.time.toFixed(1)} ث`:predicted?`تعارض متوقع خلال ${f.prediction.ttc} ثانية`:crossing?'تقاطع مكاني للمسارين؛ وجوده وحده لا يعني خطرًا متزامنًا.':'لا تقاطع مكاني بين المسارين المحددين.';
 if(crossing){
  const [x,y]=xy(crossing),[cw,ch]=footprint('car'),[pw,ph]=footprint('pedestrian');
  const hw=(cw+pw)/2,hh=(ch+ph)/2;ctx.save();ctx.strokeStyle=predicted?'#c46b00':'#a7b7c7';ctx.lineWidth=predicted?4:2;ctx.setLineDash(predicted?[]:[4,5]);
  if(predicted){ctx.fillStyle='rgba(244,181,68,.4)';ctx.fillRect(x-hw,y-hh,hw*2,hh*2);}
  ctx.strokeRect(x-hw,y-hh,hw*2,hh*2);ctx.restore();
 }
 if(f.collision){
  const c=xy(f.truth.car),p=xy(f.truth.pedestrian);ctx.save();ctx.strokeStyle='#c52222';ctx.fillStyle='rgba(197,34,34,.25)';ctx.lineWidth=5;
  const [cw,ch]=footprint('car'),[pw,ph]=footprint('pedestrian');
  const x=Math.min(c[0]-cw/2,p[0]-pw/2),y=Math.min(c[1]-ch/2,p[1]-ph/2),w=Math.max(c[0]+cw/2,p[0]+pw/2)-x,h=Math.max(c[1]+ch/2,p[1]+ph/2)-y;
  ctx.fillRect(x,y,w,h);ctx.strokeRect(x,y,w,h);ctx.restore();
 }
}
function draw(){
 const scene=selectedScene();
 sceneLabels=[];
 ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#e7eef3';ctx.fillRect(0,0,canvas.width,canvas.height);
 const g=geometry(),edge=g.crosswalk_half_width;
 box(0,-38,7,60,'#414b57');box(-3,-38,3,60,'#c8d3db');box(7,-38,3,60,'#c8d3db');
 // Kerbs leave flush openings at both ends of the crossing.
 for(const x of [0,7]){line([x,-38],[x,-edge],'#fff',2);line([x,edge],[x,22],'#fff',2);}
 ctx.setLineDash([METRE, METRE]);line([g.lane_divider,-38],[g.lane_divider,-edge],'#fff');line([g.lane_divider,edge],[g.lane_divider,22],'#fff');ctx.setLineDash([]);
 for(let x=.25;x<7;x+=1)box(x,-edge,.5,edge*2,'#fff');
 box(0,g.stop_line_y-.2,g.lane_divider,.2,'#fff');
 box(-2.6,-edge,2.6,edge*2,'#e2e6e8');box(7,-edge,2.6,edge*2,'#e2e6e8');
 const bus=scene?.bus||[4,-12.5,7,-2.5],busTop=xy([bus[0],bus[3]]);
 const busWidth=(bus[2]-bus[0])*METRE,busHeight=(bus[3]-bus[1])*METRE;
 if(busImage.complete&&busImage.naturalWidth){
  const busScale=Math.min(busWidth/555,busHeight/1720);
  ctx.drawImage(busImage,164,29,555,1720,busTop[0]+(busWidth-555*busScale)/2,busTop[1]+(busHeight-1720*busScale)/2,555*busScale,1720*busScale);
 }else box(bus[0],bus[1],bus[2]-bus[0],bus[3]-bus[1],'#f4b544');
 ctx.save();ctx.strokeStyle='#b28b39';ctx.lineWidth=.7;ctx.strokeRect(busTop[0],busTop[1],busWidth,busHeight);ctx.restore();
 label('الحافلة',[(bus[0]+bus[2])/2,bus[1]-1.2],'#fff');
 label('ممر المشاة',[3.5,edge+1.2],'#fff');label('خط التوقف',[-3.8,g.stop_line_y]);
 // A shared scale bar remains valid when the canvas is resized by CSS.
 line([-5,-30],[-5,-25],'#142d4e');line([-5.3,-30],[-4.7,-30],'#142d4e');line([-5.3,-25],[-4.7,-25],'#142d4e');label('5 م',[-6,-27.5]);
 if(!scene){$('incident').textContent='';$('incident').className='neutral';label('جارٍ إعداد المشهد المختار',[4,-20],'#fff');paintLabels();return;}
 const f=scene.frames[frameIndex];
 const warning=f.driver_warning??(f.prediction.state==='HAZARD_SUSPECTED');
 box(-5,7,3,2,warning?'#f4b544':'#142d4e');
 label(warning?'انتبه':'اللوحة',[-3.5,7.7],warning?'#142d4e':'#fff');
 if(f.pedestrian_signal&&f.pedestrian_signal!=='OFF')drawSignals(f.pedestrian_signal);
 Object.entries(scene.routes).forEach(([a,r])=>{ctx.setLineDash([5,6]);for(let i=1;i<r.length;i++)line(r[i-1],r[i],a==='car'?'#84c0ff':'#007f82',3);ctx.setLineDash([]);});
 const c=f.truth.car,p=f.truth.pedestrian;
 line([c[0],c[1]+1],p,f.hidden?'#d44128':'#007f82');

 label('السيارة',[c[0],c[1]-3],'#fff');

 if(!['UNKNOWN','DEGRADED'].includes(f.prediction.state))Object.entries(f.tracks).forEach(([actor,t])=>{const route=t.route;const progressPoint=(distance)=>{for(let i=1;i<route.length;i++){const a=route[i-1],b=route[i],len=Math.hypot(b[0]-a[0],b[1]-a[1]);if(distance<=len)return[a[0]+(b[0]-a[0])*Math.max(0,distance)/len,a[1]+(b[1]-a[1])*Math.max(0,distance)/len];distance-=len;}return route.at(-1);};ctx.setLineDash([3,5]);for(let d=0;d<scene.scenario.horizon;d+=.25)line(progressPoint(t.progress+t.speed*(f.time-t.time+d)),progressPoint(t.progress+t.speed*(f.time-t.time+Math.min(d+.25,scene.scenario.horizon))),actor==='car'?'#f4b544':'#b04991',3);ctx.setLineDash([]);});
 drawConflict(scene,f);drawCar(c,f);drawStudent(p,scene);label('الطالب',[p[0]+2,p[1]+.7]);paintLabels();
 $('time').textContent=f.time.toFixed(1);$('timeline').value=frameIndex;
 if(!playing)$('play').textContent=f.collision?'متابعة العرض':'تشغيل الحركة';
 $('warning').textContent=states[f.prediction.state]+' — '+f.prediction.reason;
 $('warning').className=f.prediction.state==='HAZARD_SUSPECTED'?'hazard':['UNKNOWN','DEGRADED'].includes(f.prediction.state)?'degraded':'';
 if(f.crossing_request&&f.driver_warning){$('warning').textContent+=' · طلب عبور قائم — انتبه للمشاة';$('warning').className='hazard';}
 if(f.collision){$('warning').textContent=`تداخل فعلي عند ${f.time.toFixed(1)} ث — راجع موضع السيارة والطالب`; $('warning').className='collision';}
 const readings=[...(result?.comparison?[`المقارنة: ${$('comparison-view').value==='control'?'دون استجابة':({finished:'وصلت السيارة إلى نهاية المسار',waiting:'انتظار التنبيه',reaction:'مهلة استجابة السائق',braking:'السائق يفرمل',stopped:'السيارة متوقفة',resuming:'السيارة تستأنف الحركة',continuing:'السيارة تكمل مسارها'}[f.driver_state])}`]:[]),...(f.hazards_active?['أضواء التحذير الرباعية تعمل']:[]),`الطالب ${f.hidden?'محجوب عن السائق':'ظاهر للسائق'}`,`زمن التعارض المتوقع: ${f.prediction.ttc??'—'} ث`];
 Object.entries(f.tracks).forEach(([a,t])=>readings.push(`${a==='car'?'السيارة':'الطالب'}: ${Math.abs(t.speed).toFixed(2)} م/ث${t.speed<-.005?' · عكس اتجاه المسار':''} · عمر الرصد ${t.age.toFixed(1)} ث`));
 if(f.pedestrian_signal){readings.push('إشارتا المشاة: '+({OFF:'غير مستخدمة',RED:'أحمر — انتظر قبل الممر',GREEN:'أخضر — إذن بدء العبور ضمن المحاكاة',UNAVAILABLE:'غير متاح — لا إذن عبور جديد'}[f.pedestrian_signal]));if(f.pedestrian_in_crossing)readings.push('الطالب داخل الممر؛ الأحمر يخص المنتظر ولا يأمر العابر بالتوقف أو الرجوع.');}
 if(scene.scenario.key==='custom')readings.push('السائق: '+({finished:'وصل إلى نهاية المسار',slowing:'يبطئ دون توقف',reduced_speed:'يسير بنصف السرعة',waiting:'وفق السلوك المختار',reaction:'مهلة الاستجابة',braking:'يفرمل',stopped:'متوقف',resuming:'يستأنف الحركة',continuing:'يكمل المسار'}[f.driver_state]));
 $('readings').replaceChildren(...readings.map(text=>{const p=document.createElement('p');p.textContent=text;return p;}));
 let preview=scene.frames.slice(0,frameIndex+1).reverse().find(f=>Object.keys(f.previews).length);
 $('cameras').replaceChildren();if(preview)Object.entries(preview.previews).forEach(([a,png])=>{const img=document.createElement('img');img.src='data:image/png;base64,'+png;img.alt=a==='car'?'صورة مصدر السيارة':'صورة مصدر الطالب';$('cameras').append(img);});
}
function pause(){playing=false;clearInterval(timer);$('play').textContent=selectedScene()?.frames[frameIndex]?.collision?'متابعة العرض':'تشغيل الحركة';}
function show(data, syncControls=false){pause();currentRunId=data.id;result=data.result;$('comparison-controls').hidden=!result.comparison;$('comparison-view').value='response';frameIndex=0;describe(result.scenario,data.id);$('timeline').max=result.frames.length-1;$('timeline').disabled=false;$('play').disabled=false;
 if(syncControls){
  form.reset();
  form.elements.pedestrian_behavior.value='cross';form.elements.signals.value='off';
  form.elements.driver_behavior.value=result.scenario.fault==='driver_response'?'respond':'ignore';
  if(['stop','reverse','away'].includes(result.scenario.fault))form.elements.pedestrian_behavior.value=result.scenario.fault;
  Object.entries(result.scenario).forEach(([key,value])=>{if(form.elements[key])form.elements[key].value=value;});
  if(result.scenario.key!=='custom'){
   const s=result.scenario;
   form.elements.signals.value='off';form.elements.crossing_side.value='right';
   form.elements.pedestrian_behavior.value=['stop','reverse','away'].includes(s.fault)?s.fault:s.pedestrian_speed===0?'standing':'cross';
   form.elements.driver_behavior.value=s.fault==='driver_response'?'respond':['brake','accelerate'].includes(s.fault)?s.fault:s.car_speed===0?'parked':'ignore';
   form.elements.sensor_fault.value=['loss','missing','calibration','clock'].includes(s.fault)?s.fault:'';
   form.elements.behavior_time.value=2;
  }
  describe(result.scenario,data.id);
 }
 renderMetrics();draw();}
function renderMetrics(){
 const scene=selectedScene();if(!scene)return;
 const m=scene.metrics, dl=document.createElement('dl');
 $('metrics-title').textContent=result.comparison?($('comparison-view').value==='control'?'نتيجة المشهد دون استجابة':'نتيجة المشهد مع الاستجابة'):'نتيجة التجربة';
 [['رقم السجل',currentRunId??'معاينة غير محفوظة'],['أول تنبيه (ث)',m.first_alert],['أول ظهور للسائق (ث)',m.first_visible],['أسبقية التنبيه (ث)',m.lead_before_visible],['أول تداخل فعلي (ث)',m.first_collision],['تنبيه أثناء الحجب',m.alert_while_hidden?'نعم':'لا'],['عينات إنذار كاذب قابلة للتقييم',m.fp],['عينات مستبعدة بسبب استجابة السائق',m.intervened],['عينات خطر دون تنبيه',m.fn],['عينات غير موثوقة',m.unavailable],['أول تشغيل للرباعية (ث)',m.hazards_start],['أول بداية فرملة (ث)',m.brake_start],['أول توقف للسيارة (ث)',m.car_stopped],['أول استئناف للحركة (ث)',m.resume_start]].forEach(([name,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value??'لم يحدث';dl.append(dt,dd);});
 $('metrics').replaceChildren(dl);$('comparison-metrics').replaceChildren();
 if(scene.scenario.key==='custom'){
  for(const [name,value] of [['أول عينة حركة بعد الاستئناف (ث)',m.first_resumed_motion_sample],['كشف الحركة المباشر (ث)',m.motion_detected_after_resume],['سحب الأخضر بعد الاستئناف (ث)',m.green_revoked_after_resume],['تأخر كشف الحركة منذ بدء الاستئناف (ث)',m.motion_detection_delay],['تأخر سحب الأخضر منذ بدء الاستئناف (ث)',m.signal_withdrawal_delay],['تأخر الكشف عن أول عينة حركة (ث)',m.sample_motion_detection_delay]]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value??'غير منطبق / لم يرصد';dl.append(dt,dd);}
  for(const [name,value] of [['أول توقع تعارض (ث)',m.first_prediction_alert??'لم يحدث'],['أول أخضر (ث)',m.first_green??'لم يحدث'],['عينات أخضر والسيارة تتحرك قبل إخلاء الممر',m.green_while_car_moving_samples??0],['اكتمل العبور خلال التجربة',m.crossing_complete?'نعم':'لا']]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;dl.append(dt,dd);}
 }
 $('events').replaceChildren(...(scene.events||[]).map(e=>{const p=document.createElement('p');p.textContent=`${e.time.toFixed(1)} ث — ${e.event}${e.signal?' · '+({GREEN:'أخضر',RED:'أحمر',UNAVAILABLE:'غير متاح',OFF:'بدون إشارة'}[e.signal]):''}`;return p;}));
 if(!scene.events?.length)$('events').textContent='لا أحداث مسجلة لهذا المشهد.';
 if(result.comparison){const p=document.createElement('p');p.textContent=`خلاصة المقارنة: ${result.metrics.success?'تحقق النجاح':'لم يتحقق النجاح'} · التداخل مع الاستجابة: ${result.metrics.first_collision??'لم يحدث'} · دون استجابة: ${result.comparison.metrics.first_collision??'لم يحدث'} ث`;$('comparison-metrics').append(p);}
}
async function request(url,body){const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':form.elements.csrfmiddlewaretoken.value},body:JSON.stringify(body)}:{});if(!response.ok){let message='تعذر تنفيذ الطلب؛ تحقق من تسجيل الدخول.';try{message=(await response.json()).error||message;}catch{}throw Error(message);}return response.json();}
function row(data){const r=data.result,tr=document.createElement('tr');[r.scenario.title,r.scenario.mode==='vision'?'تتبع لوني':'قياسات اصطناعية',r.metrics.lead_before_visible??'—',r.metrics.fp,r.metrics.fn,r.metrics.unavailable].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});const td=document.createElement('td'),b=document.createElement('button');b.dataset.load=data.id;b.textContent='عرض #'+data.id;td.append(b);tr.append(td);$('history').prepend(tr);}
function busy(value){form.querySelectorAll('button').forEach(b=>{if(b.id!=='suite-stop')b.disabled=value||batchRunning;});}
function payload(){const data={};new FormData(form).forEach((v,k)=>{if(k!=='csrfmiddlewaretoken')data[k]=['key','mode','pedestrian_behavior','driver_behavior','signals','sensor_fault','crossing_side'].includes(k)?v:Number(v);});return data;}
function invalidate(){
 revision++;pause();result=null;$('comparison-controls').hidden=true;frameIndex=0;$('play').disabled=true;$('timeline').disabled=true;$('timeline').value=0;$('time').textContent='0.0';
 $('comparison-metrics').replaceChildren();$('metrics').textContent='لم تُحسب نتيجة الاختيار الحالي بعد.';$('readings').replaceChildren();$('cameras').replaceChildren();
 $('events').textContent='جارٍ حساب الأحداث…';
 $('warning').textContent='جارٍ إعداد السيناريو المختار…';$('warning').className='';describe(payload());draw();return revision;
}
function start(){
 if(!result)return;if(frameIndex>=result.frames.length-1)frameIndex=0;playing=true;$('play').textContent='إيقاف الحركة';
 const scene=selectedScene(),firstCollision=scene.frames.findIndex(f=>f.collision);
 timer=setInterval(()=>{if(frameIndex>=scene.frames.length-1){pause();return;}frameIndex++;draw();if(frameIndex===firstCollision)pause();},100);
}
async function preview(animate=false){
 const token=invalidate();if(!form.reportValidity()){busy(false);$('feedback').textContent='أكمل القيم ضمن الحدود الموضحة.';return;}
 const data=payload();busy(true);$('feedback').textContent='جارٍ معاينة الاختيار الحالي…';
 try{const response=await request('/simulation/preview/',data);if(token!==revision)return;show(response);$('feedback').textContent='معاينة السيناريو المختار؛ الحفظ عند الضغط على تشغيل وحفظ التجربة.';if(animate&&!matchMedia('(prefers-reduced-motion: reduce)').matches)start();}
 catch(e){if(token===revision){$('feedback').textContent=e.message;$('warning').textContent='تعذر إعداد السيناريو المختار';}}
 finally{if(token===revision)busy(false);}
}
form.addEventListener('submit',async event=>{
 event.preventDefault();const data=payload(),token=invalidate();busy(true);$('feedback').textContent='جارٍ تشغيل التجربة وحفظها…';
 try{const response=await request('/simulation/run/',data);row(response);if(token!==revision)return;show(response);$('feedback').textContent='حُفظت التجربة المطابقة للإعدادات الحالية.';}
 catch(e){if(token===revision)$('feedback').textContent=e.message;}finally{if(token===revision)busy(false);}
});
function playBatchScene(){
 return new Promise(resolve=>{
  const scene=selectedScene(),firstCollision=scene.frames.findIndex(f=>f.collision);
  let hold=0,collisionShown=false;
  playing=true;$('play').disabled=true;$('timeline').disabled=true;$('comparison-view').disabled=true;
  finishBatchScene=()=>{pause();finishBatchScene=null;resolve();};
  timer=setInterval(()=>{
   if(hold>0){hold--;return;}
   if(frameIndex>=scene.frames.length-1){finishBatchScene();return;}
   frameIndex++;draw();
   if(frameIndex===firstCollision&&!collisionShown){collisionShown=true;hold=30;}
   if(frameIndex===scene.frames.length-1)hold=20;
  },100);
 });
}
if($('suite'))$('suite').addEventListener('click',async()=>{
 if(batchRunning||!form.reportValidity())return;
 pause();revision++;const mode=form.elements.mode.value,seed=Number(form.elements.seed.value);
 const jobs=presets.map(s=>{const data={...s,mode,seed};delete data.title;delete data.fault;return data;});
 for(const pedestrian of form.elements.pedestrian_behavior.options)for(const driver of form.elements.driver_behavior.options)
  jobs.push({key:'custom',mode,seed,horizon:2,signals:'auto',pedestrian_behavior:pedestrian.value,driver_behavior:driver.value});
 batchRunning=true;batchStop=false;busy(true);
 const inputs=[...form.querySelectorAll('input,select')],disabled=inputs.map(el=>el.disabled);
 inputs.forEach(el=>el.disabled=true);
 $('suite-view').hidden=false;$('suite-progress').max=jobs.length;$('suite-progress').value=0;
 $('suite-stop').disabled=false;let done=0;
 $('suite-view').scrollIntoView({block:'start',behavior:'instant'});
 try{for(const data of jobs){
  if(batchStop)break;
  $('suite-status').textContent=`جارٍ إعداد السيناريو ${done+1} من ${jobs.length}…`;
  $('suite-next').disabled=true;
  const response=await request('/simulation/run/',data);row(response);done++;
  if(batchStop)break;
  show(response,true);
  $('suite-status').textContent=`السيناريو ${done} من ${jobs.length}: ${$('scene-title').textContent}`;
  $('feedback').textContent='عرض متتابع جارٍ؛ تتحرك الأجسام في المشهد وتظهر نتائج السيناريو نفسه.';
  $('suite-progress').value=done;$('suite-next').disabled=false;
  await playBatchScene();
 }
 $('feedback').textContent=`${batchStop?'أُوقف العرض':'اكتمل عرض السيناريوهات'}؛ حُفظت ${done} من ${jobs.length} تجربة في السجل.`;
 $('suite-status').textContent=$('feedback').textContent;
 }catch(e){$('feedback').textContent=`توقف العرض بعد حفظ ${done} من ${jobs.length}: ${e.message}. النتائج المحفوظة باقية.`;$('suite-status').textContent=$('feedback').textContent;}
 finally{pause();batchRunning=false;inputs.forEach((el,i)=>el.disabled=disabled[i]);busy(false);$('suite-stop').disabled=true;$('suite-next').disabled=true;$('play').disabled=!result;$('timeline').disabled=!result;$('comparison-view').disabled=false;}
});
if($('suite-stop'))$('suite-stop').addEventListener('click',()=>{batchStop=true;$('suite-stop').disabled=true;if(finishBatchScene)finishBatchScene();});
if($('suite-next'))$('suite-next').addEventListener('click',()=>{if(finishBatchScene)finishBatchScene();});
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-load]');if(!b||batchRunning)return;const token=invalidate();busy(true);
 try{const response=await request('/simulation/runs/'+b.dataset.load+'/');if(token!==revision)return;show(response,true);$('feedback').textContent='عرض سجل محفوظ؛ الإعدادات تخص هذا السجل.';}
 catch(err){if(token===revision)$('feedback').textContent=err.message;}finally{if(token===revision)busy(false);}
});
form.addEventListener('change',event=>{
 $('scenario').value='custom';
 if($('run'))preview(true);else{invalidate();$('feedback').textContent='الإعدادات تغيرت؛ تشغيلها متاح للمشرف.';}
});
$('comparison-view').addEventListener('change',()=>{pause();renderMetrics();draw();});
$('timeline').addEventListener('input',()=>{frameIndex=Number($('timeline').value);pause();draw();});
$('play').addEventListener('click',()=>{if(playing)pause();else start();});
function setZoom(value,anchor=[200,500]){
 const next=Math.max(.5,Math.min(4,value)),ratio=next/zoom;
 panX=anchor[0]-200-(anchor[0]-200-panX)*ratio;
 panY=anchor[1]-500-(anchor[1]-500-panY)*ratio;
 zoom=next;METRE=16*zoom;
 $('scene-zoom').value=Math.round(zoom*100);$('zoom-value').textContent=`${Math.round(zoom*100)}%`;
 $('zoom-in').disabled=zoom>=4;$('zoom-out').disabled=zoom<=.5;draw();
}
function canvasPoint(event){
 const r=canvas.getBoundingClientRect(),scale=Math.min(r.width/canvas.width,r.height/canvas.height);
 return [(event.clientX-r.left-(r.width-canvas.width*scale)/2)/scale,
         (event.clientY-r.top-(r.height-canvas.height*scale)/2)/scale];
}
$('zoom-in').addEventListener('click',()=>setZoom(zoom+.25));
$('zoom-out').addEventListener('click',()=>setZoom(zoom-.25));
$('scene-zoom').addEventListener('input',event=>setZoom(Number(event.target.value)/100));
$('zoom-reset').addEventListener('click',()=>{panX=panY=0;setZoom(1);panX=panY=0;draw();});
canvas.addEventListener('pointerdown',event=>{
 if(!event.isPrimary||event.button!==0)return;
 drag={id:event.pointerId,point:canvasPoint(event)};canvas.setPointerCapture(event.pointerId);canvas.classList.add('dragging');
});
canvas.addEventListener('pointermove',event=>{
 if(!drag||drag.id!==event.pointerId)return;
 const point=canvasPoint(event);panX+=point[0]-drag.point[0];panY+=point[1]-drag.point[1];drag.point=point;draw();
});
function endDrag(){drag=null;canvas.classList.remove('dragging');}
canvas.addEventListener('pointerup',endDrag);canvas.addEventListener('pointercancel',endDrag);canvas.addEventListener('lostpointercapture',endDrag);
canvas.addEventListener('wheel',event=>{
 if(!event.ctrlKey||!event.deltaY)return;event.preventDefault();setZoom(zoom+(event.deltaY<0?.25:-.25),canvasPoint(event));
},{passive:false});
canvas.addEventListener('keydown',event=>{
 const moves={ArrowLeft:[30,0],ArrowRight:[-30,0],ArrowUp:[0,30],ArrowDown:[0,-30]};
 if(moves[event.key]){event.preventDefault();panX+=moves[event.key][0];panY+=moves[event.key][1];draw();}
 else if(['+','=','-','Home'].includes(event.key)){event.preventDefault();if(event.key==='Home')$('zoom-reset').click();else setZoom(zoom+(event.key==='-'?-.25:.25));}
});
if($('run'))preview();else{form.querySelectorAll('input,select').forEach(el=>el.disabled=true);draw();}
})();
