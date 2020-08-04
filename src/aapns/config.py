from enum import Enum


class Priority(Enum):
    """
    Enum defining the priority of the notification
    """

    immediately = 10
    normal = 5


PRODUCTION_HOST = "api.push.apple.com"
SANDBOX_HOST = "api.development.push.apple.com"
DEFAULT_PORT = 443
ALT_PORT = 2197
MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP = 5120
MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER = 4096
