from typing import Type, Dict


class APNSError(Exception):
    pass


class Disconnected(APNSError):
    pass


class StreamResetError(APNSError):
    pass


class ResponseError(APNSError):
    codename = None

    def __init__(self, reason: str, apns_id: str):
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


def get(reason: str, apns_id: str) -> ResponseError:
    return CODES.get(reason, UnknownResponseError)(reason, apns_id)
