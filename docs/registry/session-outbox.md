# صندوق إرسال جلسات Codex

## العقد الحالي

يشغّل backend `run_model_dispatcher --watch` كعامل محلي طويل العمر. يوقظه إرسال المهمة عبر إشارة Windows بعد حفظ المعاملة، ثم يستخدم Codex App Server عبر stdio لتسليم مهمة `workspace-write` إلى `thread_id` الحالي. لا يستدعي `codex exec` ولا ينشئ محادثة. قبل المطالبة يستعلم عن `thread/read`؛ ثم يستعيد الجلسة القائمة بـ`thread/resume` ويبدأ دورًا واحدًا بـ`turn/start`. المهمة التي تواجه جلسة نشطة تبقى في الصف. عامل `read-only` لا يرسل إلى جلسات سطح المكتب.

للمطالبة اليدوية عند التحقيق فقط، يقرأ عامل Codex المخوّل سجلًا واحدًا بـ:

```powershell
python backend/manage.py session_outbox claim --consumer coordination
```

الناتج JSON يحتوي `dispatch_id`, `dispatch_key`, `claim_token`, `thread_id`, `charter`, `model`, `thinking`, `prompt`, `route_reason`, `attempt_no`, `max_attempts`. الجسر الآلي يرسل `prompt` إلى `thread_id` عبر `turn/start` بنفس النموذج والتفكير المحددين. لا تنشئ محادثة جديدة. بعد رجوع معرف الدور يسجل `sent`؛ وبعد اكتماله يثبت `acknowledged` ثم `complete-from-rollout` مع `turn_id`:

```powershell
python backend/manage.py session_outbox sent --dispatch ID --claim-token TOKEN
python backend/manage.py session_outbox acknowledged --dispatch ID --claim-token TOKEN
python backend/manage.py session_outbox complete-from-rollout --dispatch ID --claim-token TOKEN
```

الإكمال لا يقبل أرقامًا أو نموذجًا يمررهما العامل يدويًا. يقرأ backend ملف rollout للجلسة من `.codex/sessions` ويختار دورًا وحيدًا داخل نافذة claim/ack؛ يتحقق من `turn_context` و`token_usage_record` و`task_complete`، ويطابق `turn_token_usage` مع الفرق بين عدادي `total_token_usage` قبل الدور وبعده. يحفظ SHA-256 ومسار الملف وأرقام الأسطر والنموذج والتفكير والاستهلاك المثبت في `SessionDispatchTurn`، ويربط النتيجة النهائية الفعلية بـ`ModelJob`. يرفض الدور المتداخل، أو فقدان حدوده، أو إعادة ضبط العداد، أو اختلاف النموذج/التفكير. لا يضيف استهلاك الدور إلى عينات الجلسة التراكمية. عدم وضوح الإرسال يسجل `uncertain` دون إعادة تلقائية.

قيم `TokenSample` تراكمية على مستوى الجلسة ولا تثبت استهلاك دور بعينه؛ لذا لا يقبل القارئ فروقها كتوكِنات للدور. يعتمد سجل الدور على rollout مصدره ويمنع قيد قاعدة البيانات تكرار `(thread_id, turn_id)` حتى لو حاول جسران نسب الدور إلى مهمتين.

عند ظهور تعثر موثق في دور معروف، يمكن للجسر تسجيل `stuck` من حالة `acknowledged` مع `--detail '{"reason":"سبب التعثر"}'`؛ يتحقق backend من نفس دليل rollout. تطبق سياسة الفئة المحفوظة سقف المحاولات والتصعيد؛ للفئات المهيأة تكون محاولة تصعيد واحدة إلى Sol/high. يضم الظرف التالي سبب التعثر. عند بلوغ السقف توقف المهمة للمراجعة البشرية.

## التوجيه والحدود

أنواع code تذهب إلى backend، design إلى frontend، ومهام المراجعة التي يتضح قصدها من النص إلى review، والنصوص الأخرى إلى coordination. تحفظ سياسة كل نوع في `SessionRoutingPolicy`: Luna/low للنص والمراجعة الافتراضية، Sol/medium للبرمجة والتصميم، وسقف تصعيد Sol/high عند التعثر. يجب توفر ميثاق وجهة مفعّل ضمن المشروع. لا توجد خدمة خلفية تملك MCP `send_message_to_thread`؛ لذلك هذا العقد يتيح لجسر Codex حي تنفيذ التسليم والتصعيد، لكنه لا يشكل بذاته عامل إرسال دائمًا. لا يُفعّل Windows Scheduled Task القديم حتى يُعتمد جسر دائم ومالك تشغيله.

## الاستخدام ضمن سجل الخدمات

التطبيق الداخلي هو `workspace` ومخزن صندوق التسليم هو `execution_context.SessionDispatch`. يستدعي العامل المحلي Codex App Server المثبت مع تطبيق Codex؛ لم تُضف خدمة أو اعتمادًا خارجيًا جديدًا.


## تحديث القراءة فقط وعرض السياسة — 2026-10-31

تعرض صفحة sessions الآن SessionRoutingPolicy الفعلية من قاعدة البيانات، بما في ذلك التفعيل والنموذج والتفكير وحد المحاولات والتصعيد. إعداد الميثاق القديم معروض كتفعيل لوجهة الجلسة ولا يقدم نموذج المهمة كأنه السياسة الفعلية.

كان هذا القيد يخص أداة MCP `send_message_to_thread` قبل دمج App Server. الجسر الحالي يبقي إعدادات السماح والعزل الموروثة من الجلسة كما هي، ويرفض طلبات الموافقة التفاعلية لعدم وجود واجهة موافقة آمنة في العامل. يظل `session_outbox claim` محصورًا بمهام `workspace-write` ويرفض أي مهمة `read-only`؛ لا يرقّي الطلب إلى صلاحية تعديل تلقائيًا. تبقى طلبات القراءة على العامل المعزول وتظهر كذلك في الواجهة.

تسليم الجلسات من الباك إند متاح فقط عندما يعمل العامل المحلي على جهاز Codex. إذا نُشرت المنصة إلى Railway، فقاعدة Railway لا تستطيع إيقاظ عملية سطح المكتب مباشرة؛ يجب أن يبقى العامل المحلي متصلًا بقاعدة التشغيل. لا يستطيع هذا الحاجز منع إرسال يدوي مباشر خارج خدمة المشروع.

التحقق: نجحت 95 حالة اختبار، بما فيها منع claim لمهمة read-only بلا توليد claim_token، وعرض سياسة متغيرة/متوقفة من قاعدة البيانات. نجح check وفحص الهجرات؛ لا هجرة جديدة. لم تُجر معاينة بصرية مستقلة؛ تحقق HTML كان عبر Django client والقوالب الحالية.
