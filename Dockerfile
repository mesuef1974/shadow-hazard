# النسخة السحابية للمنصة (ADR 0010). النسخة المحلية لا تستعمل هذا الملف.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements-cloud.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-cloud.txt

COPY backend backend
COPY src src
COPY docs docs

# الجمع يجري بإعدادات التطوير (لا قاعدة ولا أسرار)، ثم يُحذف المفتاح المؤقت الذي تولّده.
RUN python backend/manage.py collectstatic --noinput \
    && rm -f backend/.local/secret.key \
    && useradd --system --create-home app \
    && chown -R app backend/.local
USER app
# بعد الجمع: الصورة لا تعمل إلا بوضع الإنتاج، فغياب المتغيرات يوقف التشغيل بدل أن يفتحها بإعدادات التطوير.
ENV DJANGO_ENV=production

# migrate وimport_competition_data وinitialize_workspace متكررة بأمان. حساب المشرف يُنشأ مرة واحدة حين يضع المالك
# DJANGO_SUPERUSER_PASSWORD في متغيرات الخدمة، ثم يحذف المتغير.
CMD ["sh", "-c", "cd backend && python manage.py migrate --noinput && python manage.py import_competition_data && { [ -z \"$DJANGO_SUPERUSER_PASSWORD\" ] || python manage.py createsuperuser --noinput --username mesuef --email '' || echo 'supervisor account already exists'; } && python manage.py initialize_workspace && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --worker-class gthread --threads 4 --access-logfile -"]
