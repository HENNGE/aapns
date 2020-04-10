from enum import Enum


class Priority(Enum):
    immediately = 10
    normal = 5


PRODUCTION_HOST = "api.push.apple.com"
SANDBOX_HOST = "api.development.apple.com"
DEFAULT_PORT = 443
ALT_PORT = 2197
