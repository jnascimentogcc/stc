"""Secret hygiene (IDX-06, XCT-01): files that look like secrets are excluded *whole*, before any
other processing. Only the kind of finding and its line are ever reported, never the value."""

import fnmatch
from pathlib import Path, PurePosixPath

from detect_secrets.core.scan import scan_file
from detect_secrets.settings import transient_settings

# Pattern-based detectors only: the entropy detectors flag too much ordinary code.
_DETECTORS = (
    "AWSKeyDetector", "ArtifactoryDetector", "AzureStorageKeyDetector", "BasicAuthDetector",
    "CloudantDetector", "DiscordBotTokenDetector", "GitHubTokenDetector",
    "GitLabTokenDetector", "IbmCloudIamDetector", "IbmCosHmacDetector", "JwtTokenDetector",
    "KeywordDetector", "MailchimpDetector", "NpmDetector", "OpenAIDetector",
    "PrivateKeyDetector", "PypiTokenDetector", "SendGridDetector", "SlackDetector",
    "SoftlayerDetector", "SquareOAuthDetector", "StripeDetector",
    "TelegramBotTokenDetector", "TwilioKeyDetector",
)  # fmt: skip
_PLUGINS = [{"name": name} for name in _DETECTORS]

_SECRET_FILENAMES = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa", "id_rsa.*", "id_dsa", "id_ecdsa", "id_ed25519", "*.ppk",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd", "credentials", "credentials.*",
    "secrets.*", "*.secret", "*.secrets", "terraform.tfvars", "*.tfvars",
)  # fmt: skip
# Templates are documentation of the configuration, not secrets; their content is still scanned.
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def secret_filename_reason(path: PurePosixPath) -> str | None:
    name = path.name.lower()
    if name.endswith(_TEMPLATE_SUFFIXES):
        return None
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in _SECRET_FILENAMES):
        return "secret by file name"
    return None


def secret_content_reason(file: Path) -> str | None:
    """Describe the first finding as `<kind> at line <n>`; the secret value is never exposed."""
    with transient_settings({"plugins_used": _PLUGINS}):
        for found in scan_file(str(file)):
            return f"secret detected: {found.type} at line {found.line_number}"
    return None
