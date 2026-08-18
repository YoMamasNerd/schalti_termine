"""Social account adapter for VoidAuth SSO integration in Schalti Termine."""

import logging
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class VoidAuthSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Adapter for VoidAuth SSO logins in Schalti Termine.
    - Allows automatic user creation for any user authenticated via VoidAuth.
    - Automatically links social login to existing Django user accounts matching email or username.
    - Supports explicit account linking when logged in.
    - Sets is_staff to True by default so staff can access internal views.
    """

    def is_open_for_signup(self, request, sociallogin):
        return True

    def is_auto_signup_allowed(self, request, sociallogin):
        return True

    def get_connect_redirect_url(self, request, socialaccount):
        from django.urls import reverse
        return reverse("termine:einstellungen")

    def pre_social_login(self, request, sociallogin):
        # If social account already exists in DB or user is connecting their account, do nothing here
        if sociallogin.is_existing or (request.user and request.user.is_authenticated):
            return

        extra = sociallogin.account.extra_data or {}

        # 1. Try matching by email
        email = extra.get("email")
        if not email and sociallogin.email_addresses:
            email = sociallogin.email_addresses[0].email
        if not email and hasattr(sociallogin.user, "email"):
            email = sociallogin.user.email

        matched_user = None
        if email:
            try:
                matched_user = User.objects.get(email__iexact=email)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                matched_user = None

        # 2. Try matching by username / preferred_username
        if not matched_user:
            username = extra.get("preferred_username") or extra.get("username")
            if username:
                try:
                    matched_user = User.objects.get(username__iexact=username)
                except (User.DoesNotExist, User.MultipleObjectsReturned):
                    matched_user = None

        # 3. Connect to existing user if found
        if matched_user:
            logger.info(
                "Auto-connecting VoidAuth login to existing user: %s (id=%s)",
                matched_user.username,
                matched_user.id,
            )
            sociallogin.connect(request, matched_user)

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data or {}

        if not user.email and extra.get("email"):
            user.email = extra.get("email")

        if not user.first_name and extra.get("name"):
            parts = extra.get("name", "").split(" ", 1)
            user.first_name = parts[0]
            if len(parts) > 1 and not user.last_name:
                user.last_name = parts[1]

        if not user.username:
            if extra.get("preferred_username"):
                user.username = extra.get("preferred_username")
            elif user.email:
                user.username = user.email.split("@")[0]

        # In Schalti Termine, all authenticated users are staff members
        user.is_staff = True

        return user
