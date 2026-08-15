"""Django-Einstellungen für Schalti Termine."""

from pathlib import Path

from dotenv import load_dotenv
import os
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Basis -----------------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsicher-nur-fuer-entwicklung")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Der Healthcheck des Containers ruft die App über 127.0.0.1 auf. Stünde dort
# nur die öffentliche Domain, bekäme er 400 (DisallowedHost) und würde einen
# gesunden Container für krank erklären. Für Besucher ändert das nichts: Die
# Links dieser App entstehen aus SITE_BASE_URL, nicht aus dem Host-Kopf – das
# ist genau der Grund, warum es SITE_BASE_URL überhaupt gibt.
for _lokal in ("127.0.0.1", "localhost"):
    if _lokal not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_lokal)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Öffentliche Basis-URL. Wird für Links in E-Mails und für den ICS-Feed gebraucht,
# weil diese außerhalb eines Requests erzeugt werden (Hintergrundjobs).
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:8000").rstrip("/")
SITE_NAME = os.environ.get("SITE_NAME", "Fahrschule – Beratungstermine")

# Seiten, die die Terminauswahl unter /einbetten/ in einen Rahmen setzen
# dürfen, z. B. https://fahrschule-schaltwerk.de. Leer = niemand; die
# Auswahl bleibt dann nur direkt aufrufbar. Bewusst eine ausdrückliche
# Liste: Wer beliebige Einbettung zulässt, lädt zum Klickfang ein.
EMBED_ORIGINS = env_list("EMBED_ORIGINS")

# --- Fahrschulmanager (FSM) Integration ------------------------------------
# Ermöglicht die optionale Synchronisation von Beratungsterminen und Belegungszeiten.
FSM_SYNC_ENABLED = env_bool("FSM_SYNC_ENABLED", default=False)
FSM_EMAIL = os.environ.get("FSM_EMAIL", "")
FSM_PASSWORD = os.environ.get("FSM_PASSWORD", "")
FSM_BASE_URL = os.environ.get("FSM_BASE_URL", "https://api.fahrschulmanager.de/v1").rstrip("/")
FSM_AUTH_TOKEN = os.environ.get("FSM_AUTH_TOKEN", "")
FSM_API_KEY = os.environ.get(
    "FSM_API_KEY",
    "04TapXakdwXWUDVJyNEE8.W3t83Y3FhNryQABM0cMUq10JBH6Wv7X2k1iassfBsXOJpgyUHlYm2nUfCk6vdgVl10NadmfI8KnSmqefUlOJjv.8gCXHwujMBoT0TY2gGQ",
)
FSM_LEISTUNGSART_ID = os.environ.get("FSM_LEISTUNGSART_ID", "4330ec51-91b9-45f1-a3fb-88179db000ce")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_q",
    "termine",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "termine.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Datenbank -------------------------------------------------------------
# Ohne POSTGRES_DB läuft die App auf SQLite. Das ist für die Entwicklung und für
# kleine Einzelplatz-Installationen völlig ausreichend.

if os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "termine"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "data" / "db.sqlite3"),
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 10},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Sprache und Zeit ------------------------------------------------------

LANGUAGE_CODE = "de"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True

# --- Statische Dateien -----------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Im Testlauf und bei der Entwicklung wird ohne Manifest ausgeliefert – sonst
# müsste vor jedem Testlauf erst `collectstatic` laufen. Im Betrieb sorgt die
# Manifest-Storage für Cache-Busting und vorkomprimierte Dateien.
IM_TESTLAUF = "test" in sys.argv

if DEBUG or IM_TESTLAUF:
    STATICFILES_BACKEND = "django.contrib.staticfiles.storage.StaticFilesStorage"
else:
    STATICFILES_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_BACKEND},
}

# --- Login -----------------------------------------------------------------

LOGIN_URL = "/intern/anmelden/"
LOGIN_REDIRECT_URL = "/intern/"
LOGOUT_REDIRECT_URL = "/intern/anmelden/"

# --- E-Mail ----------------------------------------------------------------

if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", default=False)
else:
    # Ohne SMTP-Konfiguration landen Mails auf der Konsole statt im Nirwana.
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "termine@example.org")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- Buchungsregeln --------------------------------------------------------

# Wie lange ein Slot nach dem Absenden des Formulars für den Kunden reserviert
# bleibt, bis er den Bestätigungslink angeklickt haben muss.
RESERVATION_MINUTES = int(os.environ.get("RESERVATION_MINUTES", "30"))

# Standard-Vorlauf für den Slot-Generator, wenn beim Fahrlehrer nichts anderes
# hinterlegt ist.
DEFAULT_HORIZON_WEEKS = int(os.environ.get("DEFAULT_HORIZON_WEEKS", "4"))

# Wie viele Stunden vor dem Termin die Erinnerungsmail rausgeht.
REMINDER_HOURS_BEFORE = int(os.environ.get("REMINDER_HOURS_BEFORE", "24"))

# Buchungen werden nach Ablauf dieser Frist anonymisiert (DSGVO-Löschkonzept).
DATA_RETENTION_DAYS = int(os.environ.get("DATA_RETENTION_DAYS", "180"))

# --- Hintergrundjobs (django-q2) -------------------------------------------

Q_CLUSTER = {
    "name": "schalti_termine",
    "workers": 2,
    "timeout": 300,
    "retry": 600,
    "queue_limit": 50,
    "bulk": 10,
    "orm": "default",
    "catch_up": False,
    "sync": IM_TESTLAUF,
}

# --- Sicherheit ------------------------------------------------------------

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=False)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Der Healthcheck spricht den Container direkt über HTTP an, ohne den
    # Reverse Proxy und damit ohne X-Forwarded-Proto. Ohne diese Ausnahme
    # bekäme er eine Umleitung auf https://127.0.0.1/ – und dort hört niemand.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    # HSTS erst einschalten, wenn die Seite dauerhaft per HTTPS läuft – ein zu
    # früh gesetzter Wert sperrt Besucher für die eingestellte Dauer aus.
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", default=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {
        "handlers": ["console"],
        "level": "WARNING" if IM_TESTLAUF else os.environ.get("LOG_LEVEL", "INFO"),
    },
}

if IM_TESTLAUF:
    # Sicheres Hashing kostet pro Testbenutzer spürbar Zeit und bringt im
    # Testlauf nichts. Gilt ausschließlich hier, nie im Betrieb.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
