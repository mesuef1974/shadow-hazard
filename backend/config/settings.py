import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = BASE_DIR / ".local"
LOCAL_DIR.mkdir(exist_ok=True)
PRODUCTION = os.getenv("DJANGO_ENV") == "production"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")
    secret_file = LOCAL_DIR / "secret.key"
    try:
        with secret_file.open("x", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = secret_file.read_text(encoding="utf-8")
DEBUG = False
ALLOWED_HOSTS = [v.strip() for v in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if v.strip()]
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "registry", "django.contrib.staticfiles", "accounts"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "registry" / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"}, {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}, {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}, {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
url = os.getenv("DATABASE_URL")
if url:
    u = urlsplit(url)
    if u.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must reference PostgreSQL")
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": unquote(u.path.lstrip("/")), "USER": unquote(u.username or ""), "PASSWORD": unquote(u.password or ""), "HOST": u.hostname, "PORT": u.port or 5432, "CONN_MAX_AGE": 60, "OPTIONS": {"sslmode": parse_qs(u.query).get("sslmode", ["require" if PRODUCTION else "prefer"])[0]}}}
else:
    if PRODUCTION:
        raise ImproperlyConfigured("PostgreSQL DATABASE_URL is required in production")
    # Reserve writes at transaction entry so concurrent writers wait instead of
    # failing while upgrading a read transaction. Keep atomic sections short.
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": LOCAL_DIR / "registry.sqlite3", "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"}}}
LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Qatar"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / ".local" / "static"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
# Local applications share cookies across ports; keep this platform isolated.
SESSION_COOKIE_NAME = "shadow_platform_session"
CSRF_COOKIE_NAME = "shadow_platform_csrf"
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = PRODUCTION
CSRF_COOKIE_SECURE = PRODUCTION
SECURE_SSL_REDIRECT = PRODUCTION
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Read-only local skill roots; deployment may override these explicitly.
SKILL_INVENTORY_ROOTS = {
    'project_skills': BASE_DIR.parent / '.agents' / 'skills',
    'codex_skills': Path.home() / '.codex' / 'skills',
    'agent_skills': Path.home() / '.agents' / 'skills',
    'plugin_cache': Path.home() / '.codex' / 'plugins' / 'cache',
}

STATICFILES_DIRS = [('brand', BASE_DIR.parent / 'docs' / 'brand')]
LOGIN_REDIRECT_URL = '/'
INSTALLED_APPS += ['workspace.apps.WorkspaceConfig']
INSTALLED_APPS += ['token_usage.apps.TokenUsageConfig']
TOKEN_USAGE_SOURCE = Path(os.getenv('TOKEN_USAGE_SOURCE', str(Path.home() / '.codex' / 'usage-logs' / 'usage.sqlite3')))
STATICFILES_FINDERS = ['registry.static_finders.BrandAssetsFinder', 'django.contrib.staticfiles.finders.AppDirectoriesFinder']
INSTALLED_APPS += ['execution_context']
INSTALLED_APPS += ['operations.apps.OperationsConfig']
INSTALLED_APPS += ['releases']
INSTALLED_APPS += ['knowledge']
INSTALLED_APPS += ['simulation']
AUTO_RENEWAL_EXECUTION_ENABLED = True
INSTALLED_APPS += ['competition.apps.CompetitionConfig']
