'use strict';
const stages={
submit:['تسجيل المهمة','يسجل سفيان نص المهمة، ويختار الميثاق المسؤول والصلاحية. اختيار الجلسة صريح؛ نوع المهمة لا يبدّل وجهتها خفية.','مشرف فعّال، وسياسة مفعّلة، ومهمة صالحة.','معرف طلب ومفتاح تقديم يمنع تكرار نفس الطلب.','workspace.dispatcher.enqueue · ModelJob'],
context:['سياق بحجم المهمة','تبقى القواعد العامة في AGENTS.md، والتوثيق في ذاكرة القاعدة، وحدود الدور في الميثاق. تُختار وحدات الذاكرة بحسب المهمة، مع إزالة الأسطر المتطابقة وتحميل التفاصيل والمهارات عند الحاجة. لا يُنسخ التاريخ كاملًا.','التحقق من نوع السياق وبصمته وإيصال قراءة المضيف؛ التفاصيل الناقصة تُقرأ قبل العمل المعني.','بصمة الذاكرة وإيصالها، سياق المهمة، والمهارات والسياسات المختارة.','instruction_text.merge_instructions · memory.instructions · TaskContext'],
route:['تجهيز المسار والسياسة','يصنف العامل المهمة ويختار مسار الحساب المحلي أو القراءة المعزولة أو الجلسة الفعلية. يستخرج النموذج والتفكير من السياسة المحفوظة.','عدم وجود عمل نشط أو ملتبس في مسار الجلسة نفسها.','السياسة المختارة وسبب التوجيه ولقطة الميثاق.','workspace.job_lanes · SessionRoutingPolicy'],
local:['الحساب المحلي','عمليات حسابية محددة تنفذ بالكود المحلي. لا يستدعي هذا الفرع نموذجًا؛ الادعاء بصفر توكنز يخص الحساب المحلي وحده.','نجاح الحساب وتسجيل دليل التنفيذ.','النتيجة وسجل استخدام محلي وعدّادات نموذج صفرية.','Decimal · record_local_execution'],
readonly:['القراءة المعزولة','عامل خلفي واحد ينفذ على نسخة مشروع داخل حاوية، مع إثبات منع الكتابة والشبكة غير المصرح بها. يعمل منفصلًا عن حلقة إرسال الجلسات؛ وقد يستهلك النموذج توكنز.','نجاح اختبار العزل، صلاحية الميثاق والسياسة، واكتمال الدور ضمن المهلة.','معرف الدور والنموذج والعدادات وبصمات النسخة ودليل العزل.','ReadonlyRunner · readonly_service · ReadOnlyServer'],
outbox:['صندوق الإرسال','تسجل رسالة دائمة مرتبطة بطلب التشغيل وميثاق الجلسة. تمثل قائمة إرسال يمكن تدقيقها، وليست دليل وصول إلى المحادثة.','وجود جلسة مستهدفة فعالة وسياسة نقل صالحة.','معرف رسالة وارتباط ومفتاح منع التكرار وسجل حالات.','SessionMessage · SessionDispatch · SessionDispatchEvent'],
guard:['بوابة الهوية والخمول','يثبت العامل هوية تطبيق المالك وقناته، ويتحقق من خمول الجلسة قبل المطالبة. يثبت الحجز للطلب المحدد ثم يعيد الفحص والتفويض مباشرة قبل الإرسال.','جلسة خاملة، حجز مطابق، ومشرف وسياسة وسياق لم تتغير.','رمز حجز، لقطة السلطة، وأحداث التأجيل أو تجديد القناة.','owner_channel · owner_delivery · session_outbox claim'],
send:['إرسال رسمي باتصال المشغّل','يمر الإرسال عبر delivery إلى owner_delivery وحده. يحفظ نية الإرسال ثم يستدعي أداة المراسلة الرسمية مع الوجهة والنموذج والتفكير. لا ينتقل إلى ناقل بديل عند الفشل، ولا ينشئ محادثة جديدة.','إيصال مطابق للوجهة. قبول النقل وحده ليس دليل تنفيذ؛ عند الالتباس لا إعادة تلقائية.','owner_call_intent ثم owner_call_accepted ثم sent.','delivery.deliver_one → owner_delivery → send_message_to_thread'],
proof:['رصد الدور والتحقق','يربط الرصد رسالة المهمة بالدور الفعلي. تُراجع هوية الجلسة والدور، والنموذج والتفكير، ونتيجة الدور، واتساق العدادات ومصدرها.','دليل دور مطابق. اختلاف السياسة أو فساد الدليل ينقلان الطلب للمراجعة.','معرفا thread وturn، المصدر، النموذج والتفكير الفعليان، والاستهلاك.','observe_arrivals · reconcile · SessionDispatchTurn'],
close:['الإغلاق والتوثيق','تحفظ نتيجة التنفيذ في قاعدة المشروع ويعاد التحقق من بصمتها. لا يغلق الطلب بنجاح لمجرد وصول الرسالة أو انتهاء الدور.','إغلاق من دليل الدور، مع إيصال توثيق متحقق.','نتيجة وإيصال وبصمة وتوقيت، مع مصدر استهلاك لا يضاف مرتين.','complete-from-rollout · ProjectDocumentation · owner_closure'],
busy:['التأجيل ثم الإرسال بعد الخمول','إذا كانت الجلسة مشغولة قبل الحجز، تبقى المهمة في الطابور ويؤجل فحصها 30 ثانية؛ تستمر الجلسات المستقلة. يعاد فحص الخمول قبل الحجز وبعده. إذا انشغلت بين الفحصين يُحرر الحجز بلا إرسال وتعود المهمة للطابور.','ثبوت الخمول والهوية والتفويض في فحص جديد. لا إيقاف لجلسة تعمل ولا إرسال مكرر.','deferred_busy؛ ثم حجز جديد ونية إرسال واحدة عند الجاهزية. عند إعادة تشغيل العامل يمكن استعادة حجز لم يبدأ إرساله فقط بعد إثبات الخمول وغياب نية إرسال أو دليل دور.','owner_delivery · recover_unstarted_claims · deferred_busy'],
uncertain:['عزل النتيجة الملتبسة','قد ينقطع الاتصال بعد احتمال قبول الرسالة. لا يمكن عندها الجزم بعدم وصولها؛ يحفظ النظام الالتباس ويمنع إعادة الإرسال التلقائي.','مراجعة السجل الفعلي وحسم الدليل قبل أي محاولة أخرى.','حالة uncertain أو needs_review، وسبب التعثر والأدلة المتاحة.','SessionDispatchEvent · AutomationIssue'],
escalate:['التصعيد أو مراجعة السياسة','طلب تعثر صريح داخل دور مثبت قد يفتح محاولة أعلى ضمن حدود السياسة والمحاولات. اختلاف المرصود عن المطلوب لا يعرض كنجاح مطابق.','دليل تعثر، وسياسة وحدود تسمح بالتصعيد؛ وإلا مراجعة بشرية.','الدور السابق، سبب التصعيد، رقم المحاولة والسياسة والاستهلاك.','session_outbox stuck · SessionRoutingPolicy · ReviewAssignment']
};
Object.assign(stages,{"carrier": ["حامل صلاحية الاتصال", "مشغّل الجلسات هو هوية الاتصال الحالية التي يستخدمها العامل لاستدعاء أدوات التطبيق الرسمية. هذا لا يشغّل نموذج الجلسة في كل فحص.", "هوية تطبيق موثوقة، ومشرف مفوض، وربط موثق داخل المشروع.", "جورنال النقل والموافقات وبصمات الاتصال قبل العملية وبعدها.", "AuthorityTransferJournal · owner_tools · authority_transfer_runtime"], "automonitor": ["الفحص البرمجي", "بعد صيانة السعة والتجديد يفحص الكود حالات النقل المتعثر والتجديد المتوقف والتسليم الملتبس الحالي. يتابع الحادث نفسه عبر تغير مراحله.", "سياسة متابعة مفعّلة ومطابقة للمالك والميثاق والقناة.", "حادث ذو هوية وجيل ومراجع؛ لا يفتح تغير المرحلة حادثًا مكررًا.", "operator_monitor.maintenance · OperatorMonitorPolicy · OperatorIncident"], "quiet": ["الحالة السليمة: دون طلب نموذج", "إذا لم يوجد عائق قابل للعمل يواصل العامل صيانته البرمجية دون إنشاء مهمة تحليل أو إرسال نبض دوري إلى النموذج.", "غياب الحوادث القابلة للتحليل. إذا زال العائق قبل إرسال مهمة المتابعة تُلغى المهمة غير المرسلة فقط.", "حالة العامل وحوادث المتابعة؛ لا يُنشأ استهلاك نموذج من الفحص المحلي نفسه.", "operator_monitor.inspect_policy · single_worker"], "wake": ["إيقاظ النموذج عند الحاجة فقط", "تجمع العوائق في دفعة مختصرة عبر صندوق الإرسال نفسه، على Luna بتفكير منخفض ومحاولة واحدة دون تصعيد آلي. النموذج يقرأ المراجع اللازمة ويحلل ويوثق توصيته.", "منع التكرار وفاصل إرسال، وانتظار الجلسة الخاملة؛ لا دفعة لاحقة قبل تحليل موثق للدفعة السابقة. الإشعار الذاتي يحتاج دليل دعم رسمي.", "رسالة واحدة ودور مثبت وإيصال operator-analysis:JOB_ID. الاستهلاك من المصدر القائم دون جمعه مرتين.", "SessionMessage · SessionDispatch · self_notification_allowed"]});
// المسار المدمج (ADR 0003، مراجعة 4 نوفمبر 2026). الحقل السادس حالة التنفيذ.
Object.assign(stages,{
'm-order':['أمر المهمة من الشات','الشات يتخذ القرار ويكتب أمر مهمة من عشرة أسطر: الهدف، الملفات، القبول، خارج النطاق، المرجع، الإنهاء. لا يعدّل الكود ولا يقرؤه كاملًا.','أمر مكتمل الحقول، ولا مهمة أخرى مفتوحة على الملفات نفسها.','ملف أمر المهمة بحالته في docs/tasks/.','docs/coordination-protocol.md · docs/tasks/NNNN-<slug>.md','منفّذ: عملية بملف'],
'm-session':['بدء الجلسة','عند بدء جلسة Claude Code يطبع خطّاف SessionStart فقرة «حالة الكود» من ملف التسليم والمهام المفتوحة فقط، فلا يُحمَّل تاريخ المحادثات. وينبّه إن لم تُفعَّل حرّاس Git. الخطّاف محصور بهذا المشروع ويصمت في غيره.','خرج لا يزيد على 15 سطرًا. أي خطأ يُطبع سطرًا ولا يمنع بدء الجلسة.','سياق الجلسة يبدأ من الملف نفسه الذي يكتب فيه كل سطح آخرًا.','.claude/settings.json → scripts/session_start.py','منفّذ: مهمة 0002'],
'm-run':['تنفيذ مهمة واحدة','جلسة واحدة = مهمة واحدة. ينفّذ Claude Code أو Codex بأصغر تعديل صحيح ويختبر بأضيق اختبار مفيد. سطح واحد فقط يكتب على src/ وbackend/simulation/ في أي وقت.','قبول أمر المهمة متحقق باختبار مُشغَّل، لا باستنتاج.','ثلاثة أسطر تحت «حالة الكود»: ما تغيّر، والاختبارات، والتالي.','minimal-code-edit · focused-testing','منفّذ: قاعدة عمل'],
'm-shz':['الطبقة 0: خط إنتاج حتمي','أداة shz بخمسة أفعال: sim للسيناريوهات S1–S7 ببذرة، وeval لبروتوكول 731 بخط الأساس كتأكيدات، وcalib وcapture وreport. نفس الإعداد ونفس البذرة يعطيان الملف نفسه، بصفر توكنز.','تشغيلان بالبذرة نفسها يعطيان ملفات متطابقة بايتًا.','runs/<timestamp>/ بملفات CSV والإعداد والبذرة.','src/shadow_hazard/cli/','مخطط: sim وeval في المهمة 0003، وcalib وcapture في ديسمبر'],
'm-selfcheck':['الطبقة 1: الجهاز يراقب نفسه','selfcheck عند التشغيل يفحص الكاميرتين والإطارات والعلامة وزمن الدورة ورد المتحكم. أي فشل يُظهر «غير متاح»؛ والمعايرة تُقفل ببصمة فيرفض capture إعدادًا غير مطابق.','كل فحص ناجح قبل أي تشغيل على الطاولة.','بصمة المعايرة وسجل الفحص.','shz selfcheck','مخطط: ديسمبر'],
'm-commit':['حارس التجميد عند الالتزام','خطّاف pre-commit يقرأ الملفات المجهّزة، ويرفض أي تغيير تحت backend/ (عدا simulation/) أو docs/brand/ أو docs/blueprint/ (عدا وثيقة التجربة)، ويطبع اسم القرار. نقل ملف من منطقة مجمّدة يُعامل حذفًا منها.','لا ملف مجمّد في الالتزام، أو تجاوز صريح بسبب.','رسالة رفض بالملفات والقرار؛ لا شيء يدخل Git عند الرفض.','.githooks/pre-commit → scripts/guard_frozen.py','منفّذ: مهمة 0002'],
'm-override':['تجاوز التجميد بقرار','يمر الالتزام فقط إذا وُضع المتغير SHZ_FROZEN_OVERRIDE بسبب لا يقل عن 10 أحرف. خطّاف commit-msg يكتب السبب سطرًا ختاميًا في رسالة الالتزام.','قرار موثق من المشرف، مثل سطر الاستثناء في ADR 0002.','Frozen-Override: <السبب> في تاريخ Git.','.githooks/commit-msg → guard_frozen.py --commit-msg','منفّذ: مهمة 0002'],
'm-push':['فحص ما قبل الدفع','خطّاف pre-push يشغّل اختبارات الحرّاس واختبارات المحاكاة المركّزة، ويتحقق أن لا ملف من .local متعقَّب، ويفحص تاريخ الالتزامات منذ قاعدة التجميد: أي التزام يمس منطقة مجمّدة بلا سطر Frozen-Override يُفشل الدفع، فيلتقط التراجع والنقل الانتقائي والتخطي. عند الفشل يطبع آخر 30 سطرًا فقط.','كل الفحوص ناجحة؛ وإلا خروج غير صفري.','سطر نجاح أو فشل لكل فحص.','.githooks/pre-push → scripts/prepush_check.py','منفّذ: مهمة 0002'],
'm-ci':['CI عند طلب الدمج','وظيفة «Pre-push check» في ci.yml تشغّل الأمر نفسه على GitHub عند كل دفع وطلب دمج، بتاريخ كامل حتى يجري فحص التاريخ؛ وغياب قاعدة التجميد فيه فشل صريح لا تنبيه. في المهمة 0004 تُضاف إليها shz sim وshz eval بخط الأساس، ويُرفع runs/ أرتفاكتًا.','نجاح الوظيفة؛ وتأكيدات eval تُفشلها عند أي تراجع عن خط الأساس.','نتيجة الوظيفة على طلب الدمج، ولاحقًا runs/ وخلاصة الفشل.','.github/workflows/ci.yml · scripts/prepush_check.py','جزئي: الفحص منفّذ، وsim وeval مع المهمة 0004'],
'm-failure':['خلاصة الفشل وحلقة الإصلاح','عند فشل eval فقط يُولَّد failure-digest.md بحد 40 سطرًا: السيناريو، والمقياس، والفرق عن خط الأساس، والملفات المتغيرة. هذا كل ما يقرؤه وكيل الإصلاح، ثم تعود المهمة إلى التنفيذ.','فشل تأكيد في eval.','runs/<timestamp>/failure-digest.md في أرتفاكت CI.','scripts/failure_digest.py','مخطط: المهمة 0004'],
'm-merge':['المراجعة والدمج','المشرف يراجع النتيجة ويقرر الدمج. نجاح الفحوص لا يدمج ولا ينشر شيئًا وحده.','قرار المشرف بعد المراجعة.','طلب الدمج ومراجعته.','CONTRIBUTING.md','منفّذ: قرار بشري'],
'm-digest':['العودة إلى الشات','الشات يقرأ ملف التسليم لا المحادثات. وكل خميس يولّد سكربت الخلاصة الأسبوعية: التشغيلات، واتجاه المقاييس، والمهام المفتوحة، وسطر الكلفة الذي يدخله المشرف من /cost. هذا هو المكان الوحيد لسطر الكلفة.','ملف التسليم والخلاصة تحت 60 سطرًا لكل منهما.','docs/handoff-*.md، ولاحقًا docs/digests/weekly-<date>.md.','scripts/weekly_digest.py','جزئي: ملف التسليم منفّذ، والخلاصة مع المهمة 0004']
});
const nodes=[...document.querySelectorAll('[data-node]')];
const scopeOf=el=>el.closest('[data-map-scope]')||document;
const fields=['title','body','gate','evidence','source','status'];
function selectNode(key,updateHash=true,reveal=false){
 const s=stages[key],node=nodes.find(n=>n.dataset.node===key);if(!s||!node)return;
 const scope=scopeOf(node);
 if(reveal&&scope.tagName==='DETAILS'){scope.open=true;node.scrollIntoView({block:'center'});}
 scope.querySelectorAll('[data-node]').forEach(n=>n.setAttribute('aria-pressed',String(n===node)));
 const inspector=scope.querySelector('.inspector');
 fields.forEach((name,i)=>{const el=inspector&&inspector.querySelector('[data-field="'+name+'"]');if(el)el.textContent=s[i]||'';});
 if(['carrier','automonitor','quiet','wake'].includes(key))fields.slice(0,5).forEach((name,i)=>{const el=document.getElementById('operator-detail-'+name);if(el)el.textContent=s[i];});
 if(updateHash){const u=new URL(location.href);u.hash=key;history.replaceState(null,'',u);}
}
function setMode(scope,mode){
 scope.querySelectorAll('[data-mode]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.mode===mode)));
 scope.querySelectorAll('[data-node]').forEach(n=>n.classList.toggle('dim',mode!=='all'&&n.dataset.path!=='all'&&n.dataset.path!==mode));
 scope.querySelectorAll('[data-route]').forEach(p=>p.classList.toggle('dim',mode!=='all'&&p.dataset.route!==mode));
}
nodes.forEach(n=>n.addEventListener('click',()=>selectNode(n.dataset.node)));
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{
 const scope=scopeOf(b),mode=b.dataset.mode;setMode(scope,mode);
 const list=[...scope.querySelectorAll('.diagram [data-node]')];
 const pick=list.find(n=>n.dataset.path===mode)||list.find(n=>!n.classList.contains('dim'));
 if(pick)selectNode(pick.dataset.node);
}));
const printButton=document.getElementById('print');
if(printButton)printButton.addEventListener('click',()=>window.print());
window.addEventListener('beforeprint',()=>document.querySelectorAll('details[data-map-scope]').forEach(d=>{d.open=true;}));
const initialKey=stages[location.hash.slice(1)]?location.hash.slice(1):null;
document.querySelectorAll('[data-map-scope]').forEach(scope=>{
 const pressed=scope.querySelector('[data-mode][aria-pressed="true"]');
 const own=initialKey&&scope.querySelector('[data-node="'+initialKey+'"]');
 let mode=pressed?pressed.dataset.mode:'all';
 if(own&&mode!=='all'&&own.dataset.path!=='all'&&own.dataset.path!==mode)mode=own.dataset.path;
 setMode(scope,mode);
 const first=scope.querySelector('.diagram [data-node]');
 if(own)selectNode(initialKey,false,true);else if(first)selectNode(first.dataset.node,false);
});
window.addEventListener('hashchange',()=>{const key=location.hash.slice(1);if(stages[key])selectNode(key,false,true);});
// النبض الحي داخل المنصة فقط: يقرأ live.json كل 5 ثوانٍ بالكود وحده (صفر توكنز).
// خارج المنصة (فتح الملف مباشرة) يبقى الرسم وصفًا ثابتًا.
(function(){
 const host=document.querySelector('.automation-model'),map=document.getElementById('map');
 if(!host||!map||!window.fetch)return;
 const POLL=5000,RECENT=120;
 const css=`.automation-model .live-bar{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:12px 16px;margin:6px 0;border:1px solid var(--platform-border);border-radius:8px;background:var(--platform-surface);font-size:14px;overflow-wrap:anywhere}
.automation-model .live-bar .live-zero{margin-inline-start:auto;font-size:12px;color:var(--platform-muted)}
.automation-model .live-dot{width:11px;height:11px;border-radius:50%;background:var(--shz,#8a94a3);flex:none}
.automation-model [data-live=ok]{--shz:#2e9d6b}.automation-model [data-live=bad]{--shz:#c0392b}.automation-model [data-live=warn]{--shz:#d9a441}.automation-model [data-live=run]{--shz:#2f80ed}.automation-model [data-live=idle]{--shz:#8a94a3}
.automation-model .live-bar[data-live=ok] .live-dot,.automation-model .live-pulse .live-dot,.automation-model .node.live-pulse{animation:shz-pulse 1.6s ease-out infinite}
.automation-model .live-feed{list-style:none;margin:0 0 8px;padding:0;display:flex;flex-wrap:wrap;gap:4px 18px;font-size:12px;color:var(--platform-muted)}.automation-model .live-feed li{min-width:0;overflow-wrap:anywhere}
.automation-model .node[data-live]:not([data-live=idle]){box-shadow:0 0 0 2px var(--shz)}
.automation-model .node .live-dot{position:absolute;top:7px;right:8px;width:9px;height:9px}
.automation-model .live-platform{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(190px,100%),1fr));gap:12px;margin:6px 0 20px}
.automation-model .live-card{position:relative;min-width:0;overflow-wrap:anywhere;border:1px solid var(--platform-border);border-inline-start:4px solid var(--shz,#8a94a3);border-radius:8px;padding:12px 14px 12px 30px;background:var(--platform-surface);font-size:13px}
.automation-model .live-card strong{display:block;font-size:14px}.automation-model .live-card span{color:var(--platform-muted)}
.automation-model .live-card .live-dot{position:absolute;top:16px;left:12px}
@keyframes shz-pulse{0%{box-shadow:0 0 0 0 var(--shz)}70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}
@media(max-width:850px){.automation-model .diagram [data-node=m-override],.automation-model .diagram [data-node=m-failure]{order:0}}
@media(prefers-reduced-motion:reduce){.automation-model .live-pulse,.automation-model .live-dot,.automation-model .node.live-pulse{animation:none!important}}`;
 const style=document.createElement('style');style.textContent=css;document.head.append(style);
 const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
 const bar=el('div','live-bar');bar.setAttribute('role','status');bar.dataset.live='idle';
 const barText=el('span','live-text','جارٍ الاتصال بالمنصة…');
 bar.append(el('span','live-dot'),el('strong','','الحالة الحية'),barText,el('span','live-zero','بالكود وحده · صفر توكنز · كل 5 ثوانٍ'));
 const feed=el('ol','live-feed');feed.setAttribute('aria-label','آخر أحداث الأتمتة');
 map.querySelector('.tabs').before(bar,feed);
 const inspector=map.querySelector('.inspector dl'),liveDt=el('dt','','الآن (حي)'),liveDd=el('dd','','—');
 inspector.prepend(liveDt,liveDd);
 const frozen=document.getElementById('platform-frozen'),panel=el('div','live-platform');
 if(frozen)frozen.querySelector('.service-grid').before(panel);
 const KIND={session:'بدء جلسة',commit:'التزام',push:'دفع',override:'تجاوز'};
 const RESULT={pass:'مرّ',reject:'رُفض',override:'تجاوز بسبب',fail:'فشل'};
 const BEAT={local:'العامل المحلي',session_bridge:'جسر التسليم',readonly:'القراءة المعزولة'};
 const clip=(t,n)=>t&&t.length>n?t.slice(0,n)+'…':(t||'');
 const seconds=iso=>iso?Math.max(0,(Date.now()-Date.parse(iso))/1000):Infinity;
 const ago=iso=>{const s=seconds(iso);if(!isFinite(s))return 'لا يوجد بعد';if(s<60)return 'قبل '+Math.round(s)+' ث';if(s<3600)return 'قبل '+Math.round(s/60)+' د';if(s<86400)return 'قبل '+Math.round(s/3600)+' س';return 'قبل '+Math.round(s/86400)+' ي';};
 const liveText={};
 function mark(key,tone,text,at){
  const n=document.querySelector('[data-node="'+key+'"]');if(!n)return;
  n.dataset.live=tone;n.classList.toggle('live-pulse',seconds(at)<RECENT&&tone!=='idle');
  let dot=n.querySelector(':scope > .live-dot');if(!dot){dot=el('span','live-dot');n.append(dot);}
  n.title=text;liveText[key]=text;
 }
 function showSelected(){const n=map.querySelector('.node[aria-pressed="true"]');liveDd.textContent=n&&liveText[n.dataset.node]||'—';}
 map.addEventListener('click',e=>{if(e.target.closest('.node'))setTimeout(showSelected);});
 function event(ev,key,label,map2){
  if(!ev)return mark(key,'idle',label+': لم يُسجَّل حدث بعد');
  const tone=map2[ev.result]||'idle';mark(key,tone,label+' '+(RESULT[ev.result]||ev.result)+' '+ago(ev.at)+(ev.detail?' · '+ev.detail:''),ev.at);
 }
 function render(d){
  const ev=(d.events&&d.events.latest)||{},git=d.git||{},docs=d.docs||{},gh=d.github||{},pf=d.platform||{};
  mark('m-order',docs.available?'ok':'idle',docs.available?'مهام مفتوحة: '+docs.open_tasks:'غير متاح',docs.handoff_at);
  event(ev.session,'m-session','بدء الجلسة',{pass:'ok',fail:'bad'});
  if(git.available){const h=git.head||{};mark('m-run',git.uncommitted?'run':'ok','الفرع '+git.branch+' · آخر التزام '+ago(h.at)+' · '+(h.subject||'')+(git.uncommitted?' · تغييرات غير ملتزمة: '+git.uncommitted:''),h.at);
   mark('m-merge','ok','آخر تغيير في main '+ago(git.main&&git.main.at)+(git.main?' · '+git.main.subject:''),git.main&&git.main.at);}
  else{mark('m-run','idle','Git غير متاح');mark('m-merge','idle','Git غير متاح');}
  if(git.available&&!git.hooks_active)mark('m-commit','warn','الحرّاس غير مفعّلة في هذه النسخة');else event(ev.commit,'m-commit','الالتزام',{pass:'ok',reject:'bad',override:'warn'});
  event(ev.override,'m-override','التجاوز',{override:'warn'});
  event(ev.push,'m-push','فحص الدفع',{pass:'ok',fail:'bad'});
  if(gh.available&&gh.runs&&gh.runs.length){const runs=gh.runs,mine=runs.find(r=>r.headBranch===git.branch)||runs[0];
   const tone=mine.status!=='completed'?'run':mine.conclusion==='success'?'ok':'bad';
   mark('m-ci',tone,'التكامل المستمر على '+mine.headBranch+': '+(mine.status!=='completed'?'يعمل الآن':mine.conclusion==='success'?'نجح':'فشل')+' '+ago(mine.updatedAt||mine.createdAt),mine.status!=='completed'?new Date().toISOString():mine.updatedAt);
   const bad=runs.find(r=>r.conclusion==='failure');mark('m-failure',bad?'bad':'idle',bad?'آخر فشل: '+bad.headBranch+' '+ago(bad.updatedAt):'مخطط · لا فشل حديث',bad&&bad.updatedAt);}
  else{mark('m-ci','idle',gh.reason||'GitHub غير متاح');mark('m-failure','idle','مخطط');}
  mark('m-digest',docs.handoff_at?'ok':'idle','ملف التسليم حُدّث '+ago(docs.handoff_at),docs.handoff_at);
  mark('m-shz','idle','مخطط: لا نبض قبل المهمة 0003');mark('m-selfcheck','idle','مخطط: ديسمبر');
  feed.replaceChildren(...((d.events&&d.events.recent)||[]).slice(0,5).map(r=>el('li','',ago(r.at)+' · '+(KIND[r.kind]||r.kind)+' · '+(RESULT[r.result]||r.result)+(r.detail?' · '+clip(r.detail,60):''))));
  if(pf.available){const cards=[];const local=(pf.heartbeats||[]).find(b=>b.key==='local');
   for(const b of pf.heartbeats||[])cards.push([BEAT[b.key]||b.key,b.online?'ok':'bad',(b.online?'ينبض ':'متوقف · آخر نبض ')+ago(b.at)+(b.error?' · '+clip(b.error,70):''),b.online]);
   const j=pf.jobs||{},o=pf.outbox||{};
   cards.push(['المهام والصندوق',(j.running||o.queued)?'run':(j.uncertain||o.uncertain)?'warn':'ok','تعمل '+(j.running||0)+' · في الانتظار '+(j.queued||0)+' · للمراجعة '+(j.needs_review||0)+' · ملتبسة '+((j.uncertain||0)+(o.uncertain||0)),!!(j.running||o.queued)]);
   if(pf.sync)cards.push(['مزامنة التوكنز',pf.sync.success?'ok':'bad',(pf.sync.success?'نجحت ':'فشلت ')+ago(pf.sync.at),seconds(pf.sync.at)<RECENT]);
   if(pf.backup)cards.push(['النسخ الاحتياطي',pf.backup.state==='success'?'ok':pf.backup.state==='running'?'run':'bad',ago(pf.backup.at),pf.backup.state==='running']);
   cards.push(['قضايا مفتوحة',pf.open_issues?'warn':'ok',String(pf.open_issues)+' قضية',false]);
   panel.replaceChildren(...cards.map(([t,tone,text,pulse])=>{const c=el('div','live-card'+(pulse?' live-pulse':''));c.dataset.live=tone;c.append(el('span','live-dot'),el('strong','',t),el('span','',text));return c;}));
   const on=!!(local&&local.online);mark('automonitor',on?'ok':'bad',on?'العامل ينبض':'العامل متوقف',local&&local.at);mark('carrier',on?'ok':'bad',on?'المشغّل متصل':'المشغّل غير متصل',local&&local.at);
   mark('outbox',o.queued?'run':'ok','في الصندوق '+(o.queued||0),o.queued?new Date().toISOString():null);mark('send',j.running?'run':'ok','تعمل الآن '+(j.running||0),j.running?new Date().toISOString():null);}
  else panel.replaceChildren(el('p','','حالة المنصة غير متاحة: '+(pf.reason||'')));
  showSelected();
 }
 let last=null,delay=POLL,timer=null;
 async function poll(){
  if(document.hidden){timer=setTimeout(poll,POLL);return;}
  try{
   const r=await fetch(new URL('live.json',location.href.split('#')[0]),{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
   if(r.redirected||r.status===403||r.status===302)throw new Error('انتهت جلسة الدخول؛ سجّل الدخول من جديد');
   if(!r.ok)throw new Error('الخادم ردّ برمز '+r.status);
   render(await r.json());last=Date.now();delay=POLL;bar.dataset.live='ok';
  }catch(error){delay=Math.min(delay*2,30000);bar.dataset.live='bad';barText.textContent='متوقف: '+(error.message.indexOf('fetch')>=0||error.name==='TypeError'?'الخادم لا يستجيب':error.message)+' · إعادة المحاولة خلال '+Math.round(delay/1000)+' ث';}
  timer=setTimeout(poll,delay);
 }
 setInterval(()=>{if(last&&bar.dataset.live==='ok')barText.textContent='يعمل · آخر تحديث قبل '+Math.round((Date.now()-last)/1000)+' ث';},1000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden){clearTimeout(timer);poll();}});
 poll();
})();
