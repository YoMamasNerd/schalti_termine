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
# Synchronisation von Beratungsterminen und Belegungszeiten über das zentrale FSM-Gateway.
FSM_SYNC_ENABLED = env_bool("FSM_SYNC_ENABLED", default=False)
FSM_GATEWAY_URL = os.environ.get("FSM_GATEWAY_URL", os.environ.get("FSM_BASE_URL", "http://127.0.0.1:8090/v1")).rstrip("/")
FSM_GATEWAY_API_KEY = os.environ.get("FSM_GATEWAY_API_KEY", "")
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
    # Allauth & VoidAuth SSO
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
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
    "allauth.account.middleware.AccountMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
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

# --- VoidAuth SSO (OpenID Connect) -----------------------------------------

VOIDAUTH_CLIENT_ID = os.environ.get("VOIDAUTH_CLIENT_ID", "").strip()
VOIDAUTH_CLIENT_SECRET = os.environ.get("VOIDAUTH_CLIENT_SECRET", "").strip()
VOIDAUTH_ISSUER_URL = os.environ.get("VOIDAUTH_ISSUER_URL", "").strip()
VOIDAUTH_ENABLED = bool(VOIDAUTH_CLIENT_ID and VOIDAUTH_CLIENT_SECRET and VOIDAUTH_ISSUER_URL)

SOCIALACCOUNT_ADAPTER = "termine.social_adapter.VoidAuthSocialAccountAdapter"
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = True

VOIDAUTH_BUERO_GROUPS = [
    g.strip().lower()
    for g in os.environ.get(
        "VOIDAUTH_BUERO_GROUPS", "buero,admin,office,leitung"
    ).split(",")
    if g.strip()
]
VOIDAUTH_FAHRLEHRER_GROUPS = [
    g.strip().lower()
    for g in os.environ.get(
        "VOIDAUTH_FAHRLEHRER_GROUPS", "fahrlehrer,instructor,lehrer"
    ).split(",")
    if g.strip()
]

if VOIDAUTH_ENABLED:
    SOCIALACCOUNT_PROVIDERS = {
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": "voidauth",
                    "name": "VoidAuth",
                    "client_id": VOIDAUTH_CLIENT_ID,
                    "secret": VOIDAUTH_CLIENT_SECRET,
                    "settings": {
                        "server_url": VOIDAUTH_ISSUER_URL,
                    },
                }
            ],
            "SCOPE": ["openid", "profile", "email", "groups"],
        }
    }
else:
    SOCIALACCOUNT_PROVIDERS = {}

# --- E-Mail ----------------------------------------------------------------
# SMTP- und Absender-Einstellungen werden zentral über die Benutzeroberfläche
# (FahrschulEinstellungen) verwaltet. Standard-Fallback für Tests/Konsole:
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "mail@fahrschule-schaltwerk.de"
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
