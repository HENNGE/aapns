from typing import Any, Dict, Optional, Type


class APNSError(Exception):
    pass


class Blocked(APNSError):
    """This connection can't send more data at this point, can try later."""


class Closed(APNSError):
    """This connection is now closed, try another."""


class Timeout(APNSError):
    """The request deadline has passed."""


class FormatError(APNSError):
    """Response was weird."""


class ResponseTooLarge(APNSError):
    """Server response was larger than allowed."""


class StreamReset(APNSError):
    pass


class ResponseError(APNSError):
    codename: str

    def __init__(self, reason: str, apns_id: Optional[str]):
        self.reason = reason
        self.apns_id = apns_id
        super().__init__(reason)


class UnknownResponseError(ResponseError):
    codename = "!unknown"


CODES: Dict[str, Type[ResponseError]] = {}


def create(codename: str) -> Type[ResponseError]:
    cls: Type[ResponseError] = type(codename, (ResponseError,), {"codename": codename})
    CODES[codename] = cls
    return cls


BadCollapseId = create("BadCollapseId")
BadDeviceToken = create("BadDeviceToken")
BadExpirationDate = create("BadExpirationDate")
BadMessageId = create("BadMessageId")
BadPriority = create("BadPriority")
BadTopic = create("BadTopic")
DeviceTokenNotForTopic = create("DeviceTokenNotForTopic")
DuplicateHeaders = create("DuplicateHeaders")
IdleTimeout = create("IdleTimeout")
MissingDeviceToken = create("MissingDeviceToken")
MissingTopic = create("MissingTopic")
PayloadEmpty = create("PayloadEmpty")
BadCertificate = create("BadCertificate")
BadCertificateEnvironment = create("BadCertificateEnvironment")
ExpiredProviderToken = create("ExpiredProviderToken")
Forbidden = create("Forbidden")
InvalidProviderToken = create("InvalidProviderToken")
MissingProviderToken = create("MissingProviderToken")
BadPath = create("BadPath")
MethodNotAllowed = create("MethodNotAllowed")
Unregistered = create("Unregistered")
PayloadTooLarge = create("PayloadTooLarge")
TooManyProviderTokenUpdates = create("TooManyProviderTokenUpdates")
TooManyRequests = create("TooManyRequests")
InternalServerError = create("InternalServerError")
ServiceUnavailable = create("ServiceUnavailable")
Shutdown = create("Shutdown")


def get(reason: Any, apns_id: Optional[str]) -> ResponseError:
    return CODES.get(reason, UnknownResponseError)(reason, apns_id)
