/* Release UI: render an explicit allowlist; never render transport output or secrets. */
(() => {
  'use strict';
  const labels = {draft:'مسودة',pinned:'مثبت',merging:'جارٍ الدمج',merge_failed:'فشل الدمج',merged:'مدمج',deploying:'جارٍ النشر',healthy:'فحوص سليمة',failed:'فشل الفحص',unknown:'نتيجة غير محسومة',running:'قيد التنفيذ',
    tests:'الاختبارات',migration:'خطة الهجرات',settings:'التهيئة',revise:'تعديل المسودة',pin:'تثبيت البصمة',evidence:'تسجيل دليل',review:'مراجعة مستقلة',merge:'الدمج المحلي',approve:'موافقة المشرف',withdraw:'سحب الموافقة',deploy:'النشر',rollback:'التراجع'};
  const label = value => labels[value] || 'غير معروفة';
  const list = value => Array.isArray(value) ? value : [];
  const last = rows => [...list(rows)].sort((a,b)=>Number(a.id)-Number(b.id)).at(-1);
  const hash = value => /^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(value || '') ? value : 'غير متاح';
  function gates(detail, userId, role) {
    const r=detail.release;
    const current=rows=>list(rows).filter(item=>item.fingerprint===r.fingerprint);
    const evidence=Object.fromEntries(current(detail.evidence).sort((a,b)=>a.id-b.id).map(item=>[item.kind,item.accepted===true]));
    const review=last(current(detail.reviews));
    const independent=review?.accepted===true && review.reviewer_id!==r.author_id;
    const merged=list(detail.merges).some(item=>item.state==='merged'&&item.result_commit===r.commit);
    const approved=current(detail.approvals).some(item=>!item.withdrawn&&item.environment===r.environment);
    const knownStates=['draft','pinned','merging','merge_failed','merged','deploying','healthy','failed','unknown'];
    const blocked=!knownStates.includes(r.state)||['unknown','merging','deploying'].includes(r.state) || list(detail.deployments).some(item=>item.state==='unknown') || list(detail.merges).some(item=>item.state==='unknown');
    return {blocked,evidence,independent,merged,approved,
      author:!blocked&&Number(userId)===r.author_id&&r.state==='draft',
      reviewer:!blocked&&Number(userId)!==r.author_id&&r.state==='pinned',
      supervisor:role==='supervisor'&&!blocked,
      merge:role==='supervisor'&&!blocked&&r.state==='pinned'&&independent&&['tests','migration','settings'].every(kind=>evidence[kind]),
      deploy:role==='supervisor'&&!blocked&&merged&&['merged','healthy','failed'].includes(r.state)};
  }
  function createPayload(values) {
    const payload={};
    for(const name of ['commit','environment','repository','source_ref','target_branch','target_commit']) payload[name]=values[name].trim();
    payload.manifest=Object.fromEntries(['artifact_sha256','migration_plan_sha256','settings_sha256'].map(name=>[name,values[name].trim()]));
    payload[values.source_type]=Number(values.source_id);
    return payload;
  }
  async function request(url, {body, key, csrf, fetcher=fetch, timeout=30000}={}) {
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),timeout);
    try {
      const headers={Accept:'application/json'};
      if(body) Object.assign(headers,{'Content-Type':'application/json','X-CSRFToken':csrf,'Idempotency-Key':key});
      const response=await fetcher(url,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers,signal:controller.signal,...(body?{body:JSON.stringify(body)}:{})});
      if(response.redirected) throw Object.assign(new Error('يلزم تسجيل الدخول مجددًا.'),{unknown:!!body});
      if(!response.ok) {
        const message={400:'رُفضت البيانات؛ تحقق من الحقول والارتباط بالمصدر.',403:'ليس لدورك صلاحية هذا الإجراء.',404:'لم يعد السجل متاحًا.',409:'رُفض الانتقال أو تغير الإصدار؛ حدّث السجل واقرأ حالته.'}[response.status] || 'تعذر الوصول إلى خدمة الإصدار.';
        throw Object.assign(new Error(message),{known:true,unknown:!!body&&response.status>=500});
      }
      return await response.json();
    } catch(error) {
      if(error.known) throw error;
      throw Object.assign(new Error(body?'نتيجة الإرسال غير محسومة؛ لا تعِد المحاولة قبل المصالحة اليدوية.':'تعذر تحميل السجل. تحقق من الاتصال ثم حدّث السجل.'),{unknown:!!body});
    } finally {clearTimeout(timer);}
  }
  // Pure helpers are also tested without a browser or production database.
  if(typeof module!=='undefined'&&module.exports) module.exports={gates,hash,label,createPayload,request};
  if(typeof document==='undefined') return;
  const app=document.getElementById('release-app');
  if(!app) return;
  const $=id=>document.getElementById(id);
  const status=$('release-status'), picker=$('release-picker');
  const root=app.dataset.apiRoot;
  if(!/^\/[a-zA-Z0-9/_-]+\/$/.test(root)) {status.textContent='تعذر تحديد عنوان خدمة الإصدار.';return;}
  let detail=null, busy=false, generation=0;
  const blockedScopes=new Map();
  const storagePrefix='shadow-release-unresolved:';
  function unresolved(scope) {
    if(blockedScopes.has(scope)) return blockedScopes.get(scope);
    try {return sessionStorage.getItem(storagePrefix+scope);} catch {return 'تعذر التحقق من سجل الإرسال المحلي';}
  }
  function remember(scope,key) {
    blockedScopes.set(scope,key);
    // Fail closed if an unresolved send cannot survive a reload.
    sessionStorage.setItem(storagePrefix+scope,key);
  }
  function resolved(scope) {
    try {sessionStorage.removeItem(storagePrefix+scope);blockedScopes.delete(scope);}
    catch { /* Keep the local lock until the stored operation can be reconciled. */ }
  }
  function notice(message,focus=false) {status.textContent=message;if(focus){status.tabIndex=-1;status.focus();}}
  function node(tag,text,className) {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
  function field(target,name,value,isHash=false) {target.append(node('dt',name),node('dd',isHash?hash(value):String(value??'غير مسجل'),isHash?'release-hash':''));}
  function record(target,title,values) {
    const article=node('div',undefined,'release-record');article.append(node('h3',title));
    const dl=node('dl');for(const [name,value,isHash] of values)field(dl,name,value,isHash);article.append(dl);target.append(article);
  }
  function empty(target,text) {target.replaceChildren(node('p',text));}
  function render() {
    const r=detail.release,g=gates(detail,app.dataset.userId,app.dataset.role);
    const localUnknown=unresolved(String(r.id));
    $('release-content').hidden=false;$('release-empty').hidden=true;
    $('release-name').textContent='الإصدار #'+r.id;
    $('release-state').textContent='الحالة: '+label(r.state)+'؛ المراجعة '+r.revision;
    const facts=$('release-facts');facts.replaceChildren();
    for(const [name,value,isHash] of [['المصدر',r.task_id?'مهمة #'+r.task_id:'طلب تنفيذ #'+r.job_id],['المؤلف (رقم الحساب)',r.author_id],['البيئة',r.environment],['معرّف النسخة',r.commit,true],['بصمة الإصدار',r.fingerprint,true]])field(facts,name,value,isHash);
    const technical=$('release-technical');technical.replaceChildren();
    for(const [name,value,isHash] of [['المستودع المسجل',r.repository],['فرع المصدر',r.source_ref],['فرع الدمج',r.target_branch],['نسخة فرع الدمج',r.target_commit,true],['بصمة الحزمة',r.manifest?.artifact_sha256,true],['بصمة خطة الهجرات',r.manifest?.migration_plan_sha256,true],['بصمة التهيئة',r.manifest?.settings_sha256,true]])field(technical,name,value,isHash);
    const attempts=list(detail.deployments), latest=last(attempts);
    const stages=[['المسودة والمصدر','سجل الإصدار #'+r.id,true],['تثبيت البصمة',r.state==='draft'?'لم تُثبّت بعد':'الحالة المسجلة: '+label(r.state),r.state!=='draft'],['الأدلة',Object.entries(g.evidence).filter(([,ok])=>ok).map(([kind])=>label(kind)).join('، ')||'لا أدلة مقبولة للبصمة الحالية',Object.values(g.evidence).some(Boolean)],['المراجعة المستقلة',g.independent?'قبول مستقل مسجل':'لا قبول مستقل مثبت',g.independent],['قرار الدمج',g.merged?'دمج موثق للنسخة الحالية':'لا دمج مكتمل مثبت',g.merged],['موافقة سفيان',g.approved?'موافقة غير مسحوبة مسجلة؛ يراجع الخادم أهليتها':'لا موافقة مطابقة غير مسحوبة',g.approved],['النسخة الاحتياطية',latest?'المرجع في آخر محاولة: #'+latest.backup_id:'لا محاولة ذات مرجع نسخة مسجلة',!!latest],['النشر والفحوص',latest?label(latest.state):'لا محاولة نشر مسجلة',latest?.state==='healthy']];
    const stageList=$('release-stages');stageList.replaceChildren();
    for(const [title,text,recorded] of stages){const li=node('li');li.dataset.recorded=String(recorded);li.append(node('strong',title),node('span',text));stageList.append(li);}
    renderEvidence();renderAttempts();renderEvents();
    $('release-unknown').hidden=!(g.blocked||localUnknown);
    $('release-unknown').textContent=g.blocked||localUnknown?'يوجد إجراء جارٍ أو غير محسوم. أُوقفت إجراءات هذا الإصدار. اطلب المصالحة اليدوية من المشرف؛ تحديث السجل لا يعيد إرسال الطلب. معرّف التتبع: '+(localUnknown||'راجع سجل المحاولات'):'';
    const controls=$('release-controls');
    if(controls) {
      controls.hidden=!!localUnknown||g.blocked||!(g.author||g.reviewer||g.supervisor);
      $('release-author-actions').hidden=!g.author;
      $('release-revise').hidden=true;
      $('release-review').hidden=!g.reviewer;
      if($('release-supervisor-actions')) {
        $('release-supervisor-actions').hidden=!g.supervisor;
        app.querySelector('[data-action="merge"]').hidden=!g.merge;
        $('release-add-evidence').hidden=!(g.supervisor&&r.state==='pinned');
        $('release-approve').hidden=!g.deploy;
        $('release-deploy').hidden=!g.deploy;
      }
    }
    const create=$('release-create');if(create)create.querySelector('button').disabled=!!unresolved('collection');
  }
  function renderEvidence() {
    const target=$('release-evidence');target.replaceChildren();
    for(const row of list(detail.evidence))record(target,label(row.kind),[['المرجع',row.reference],['القرار',row.accepted?'قبول':'رفض'],['مسجل الدليل (رقم الحساب)',row.actor_id],['البصمة',row.fingerprint,true]]);
    for(const row of list(detail.reviews))record(target,'مراجعة #'+row.id,[['المراجع (رقم الحساب)',row.reviewer_id],['القرار',row.accepted?'قبول':'رفض'],['الاستقلال عن المؤلف',row.reviewer_id!==detail.release.author_id?'مراجع مختلف':'المؤلف نفسه؛ لا قبول مستقل'],['البصمة',row.fingerprint,true]]);
    for(const row of list(detail.approvals))record(target,'موافقة #'+row.id,[['صاحب الموافقة (رقم الحساب)',row.actor_id],['الغرض',label(row.purpose)],['البيئة',row.environment],['الحالة',row.withdrawn?'مسحوبة':'مسجلة؛ الأهلية النهائية لدى الخادم'],['البصمة',row.fingerprint,true]]);
    if(!target.childNodes.length)empty(target,'لا أدلة أو مراجعات أو موافقات مسجلة.');
  }
  function renderAttempts() {
    const target=$('release-attempts');target.replaceChildren();
    for(const row of list(detail.merges))record(target,'محاولة دمج #'+row.id,[['الحالة',label(row.state)],['معرّف النسخة الناتجة',row.result_commit,true]]);
    const names={health:'سلامة الخدمة',pages:'الصفحات',logs:'السجلات',preflight:'الفحص قبل النشر',backup:'النسخة الاحتياطية',migrations:'الهجرات',settings:'التهيئة'};
    for(const row of list(detail.deployments)) {
      const checks=Object.entries(names).filter(([key])=>Object.hasOwn(row.checks||{},key)).map(([key,name])=>[name,row.checks[key]===true?'اجتاز':'لم يجتز']);
      record(target,(row.target_id?'محاولة تراجع #':'محاولة نشر #')+row.id,[['الحالة',label(row.state)],['النسخة الاحتياطية (مرجع فقط)',row.backup_id],['موافقة المشرف',row.approval_id],['هدف التراجع',row.target_id?'الإصدار #'+row.target_id:'لا يوجد'],['وقت التسجيل',row.created_at],...checks]);
    }
    if(!target.childNodes.length)empty(target,'لا محاولات مسجلة. لا يوجد دليل نشر فعلي أو استعادة بيانات.');
  }
  function renderEvents() {
    const target=$('release-events');target.replaceChildren();
    target.append(node('p','المؤلف يحرر المسودة ويثبتها. المراجع مختلف عن المؤلف. المشرف يسجل الأدلة وقرار الدمج والموافقة والنشر؛ الخادم يتحقق من كل إجراء.'));
    for(const row of list(detail.events))record(target,'حدث #'+row.id,[['الإجراء',label(row.action)],['المنفذ (رقم الحساب)',row.actor_id],['الوقت',row.created_at]]);
    if(!list(detail.events).length)target.append(node('p','لا أحداث انتقال مسجلة.'));
  }
  async function loadDetail(id,focus=false) {
    const token=++generation;detail=null;$('release-content').hidden=true;$('release-empty').hidden=false;$('release-empty').textContent='جارٍ تحميل الإصدار…';$('release-detail').setAttribute('aria-busy','true');
    try {
      const data=await request(root+encodeURIComponent(id)+'/');
      if(token!==generation)return;
      if(!data.release||Number(data.release.id)!==Number(id))throw new Error('استجابة الإصدار غير متوقعة.');
      detail=data;render();notice('تم تحميل سجل الإصدار #'+id+'.');
      if(focus)$('release-detail-title').focus();
    }catch(error){if(token===generation){notice(error.message,true);$('release-empty').textContent='تعذر تحميل الإصدار. حدّث السجل للمحاولة مجددًا؛ لم يُرسل أي إجراء.';}}
    finally{if(token===generation)$('release-detail').setAttribute('aria-busy','false');}
  }
  async function load(preferred=picker.value) {
    notice('جارٍ تحميل سجل الإصدارات…');
    try {
      const data=await request(root);if(!Array.isArray(data.releases))throw new Error('استجابة سجل الإصدارات غير متوقعة.');
      picker.replaceChildren(node('option','اختر إصدارًا'));picker.firstChild.value='';
      for(const r of data.releases){const option=node('option','#'+r.id+' · '+r.environment+' · '+label(r.state));option.value=r.id;picker.append(option);}
      if(data.releases.some(r=>String(r.id)===String(preferred))){picker.value=preferred;await loadDetail(preferred);}
      else {++generation;detail=null;$('release-content').hidden=true;$('release-empty').hidden=false;$('release-empty').textContent=data.releases.length?'اختر إصدارًا لقراءة أدلته.':'لا توجد إصدارات مسجلة بعد. يمكن للمخول إنشاء مسودة مرتبطة بمهمة.';notice('تم تحميل سجل الإصدارات.');}
      if(unresolved('collection')){$('release-unknown').hidden=false;$('release-unknown').textContent='إرسال مسودة سابق غير محسوم. لا تعِد الإنشاء قبل المصالحة اليدوية. معرّف التتبع: '+unresolved('collection');if($('release-create'))$('release-create').querySelector('button').disabled=true;}
    }catch(error){notice(error.message,true);}
  }
  async function send(scope,url,payload) {
    if(busy||unresolved(scope)){notice('الإرسال متوقف: توجد عملية جارية أو غير محسومة. راجع المشرف قبل المحاولة.',true);return;}
    const csrf=document.querySelector('meta[name="csrf-token"]')?.content||document.querySelector('[name="csrfmiddlewaretoken"]')?.value;
    if(!csrf||csrf==='NOTPROVIDED'){notice('تعذر التحقق من حماية الطلب. حدّث الصفحة وسجل الدخول مجددًا.',true);return;}
    let key;
    try{key=crypto.randomUUID();remember(scope,key);}catch{notice('تعذر حفظ معرّف العملية بأمان؛ لم يُرسل الطلب.',true);return;}
    busy=true;picker.disabled=true;$('release-reload').disabled=true;
    const buttons=[...app.querySelectorAll('[data-mutation] button,button[data-mutation]')];const disabled=buttons.map(n=>n.disabled);buttons.forEach(n=>n.disabled=true);
    notice('جارٍ إرسال الإجراء مرة واحدة…');
    let accepted=false, selected=scope;
    try {
      const result=await request(url,{body:scope==='collection'?payload:{...payload,key},key,csrf});
      if(result.state==='unknown'){notice('نتيجة العملية غير محسومة. لا تعِد الإرسال؛ اطلب المصالحة اليدوية.',true);}
      else {resolved(scope);accepted=true;if(scope==='collection'){selected=String(result.id||'');$('release-create').reset();}notice('قَبِل الخادم الإجراء. جارٍ قراءة السجل للتحقق من النتيجة.');}
    }catch(error){if(!error.unknown)resolved(scope);notice(error.message,true);}
    finally{busy=false;picker.disabled=false;$('release-reload').disabled=false;buttons.forEach((n,i)=>n.disabled=disabled[i]);if(detail)render();}
    if($('release-create'))$('release-create').querySelector('button').disabled=!!unresolved('collection');
    if(accepted)await load(selected);
    else if(unresolved(scope)){$('release-unknown').hidden=false;$('release-unknown').textContent='نتيجة الإرسال غير محسومة. لا تعِد المحاولة حتى المصالحة اليدوية. معرّف التتبع: '+key;}
  }
  const values=form=>Object.fromEntries(new FormData(form));
  picker.addEventListener('change',()=>{if(picker.value)loadDetail(picker.value,true);else{++generation;detail=null;$('release-content').hidden=true;$('release-empty').hidden=false;}});
  $('release-reload').addEventListener('click',()=>{if(!busy)load();});
  $('release-create')?.addEventListener('submit',event=>{event.preventDefault();send('collection',root,createPayload(values(event.currentTarget)));});
  function action(action,data={}){if(detail)send(String(detail.release.id),root+detail.release.id+'/actions/',{action,data});}
  app.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>action(button.dataset.action)));
  $('release-revise-toggle')?.addEventListener('click',()=>{
    const form=$('release-revise');form.hidden=!form.hidden;
    if(!form.hidden&&detail){const r=detail.release;for(const name of ['commit','environment'])form.elements[name].value=r[name];for(const name of ['artifact_sha256','migration_plan_sha256','settings_sha256'])form.elements[name].value=r.manifest[name];form.elements.commit.focus();}
  });
  $('release-revise')?.addEventListener('submit',event=>{event.preventDefault();const v=values(event.currentTarget);action('revise',{commit:v.commit.trim(),environment:v.environment.trim(),manifest:Object.fromEntries(['artifact_sha256','migration_plan_sha256','settings_sha256'].map(name=>[name,v[name].trim()]))});});
  for(const [id,name] of [['release-review','review'],['release-add-evidence','evidence']])$(id)?.addEventListener('submit',event=>{event.preventDefault();const data=values(event.currentTarget);data.accepted=data.accepted==='true';action(name,data);});
  $('release-approve')?.addEventListener('submit',event=>{event.preventDefault();if(detail)action('approve',{purpose:values(event.currentTarget).purpose,fingerprint:detail.release.fingerprint,environment:detail.release.environment});});
  $('release-deploy')?.addEventListener('submit',event=>{event.preventDefault();if(!detail)return;const v=values(event.currentTarget);send(String(detail.release.id),root+detail.release.id+'/deployments/',{backup_id:Number(v.backup_id),...(v.target_id?{target_id:Number(v.target_id)}:{})});});
  load();
})();
