from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    # Keep the label "accounts" so the app label, migrations, table names, and
    # AUTH_USER_MODEL ("accounts.User") are unchanged by the move under apps/.
    label = "accounts"
    verbose_name = "Accounts & Authentication"
