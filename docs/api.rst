API Reference
=============

``aapns.api``
-------------

.. automodule:: aapns.api
    :members:


``aapns.models``
----------------

.. automodule:: aapns.models
    :members:


``aapns.config``
----------------

.. automodule:: aapns.config
    :members:


``aapns.errors``
----------------

.. py:module:: aapns.errors


.. py:exception:: APNSError

    Base class for all errors raised by aapns itself.

.. py:exception:: Blocked

    This connection cannot temporarily send more requests.

.. py:exception:: Closed

    This connection was closed, the condition is permanent.

.. py:exception:: Timeout

    This request has timed out or would time out.

.. py:exception:: FormatError

    The error response could not be parsed.

.. py:exception:: ResponseTooLarge

    Server sent abnormally large respinse.

.. py:exception:: StreamReset

    Error raised if the HTTP2 stream used to send a notification was reset by APNS.

.. py:exception:: ResponseError

    Base class for response errors when sending notifications.

    See below for concrete instance.

    .. py:attribute:: reason

        The error code from APNS for this exception.

    .. py:attribute:: apns_id

        The APNS ID this error corresponds to.


.. py:exception:: BadCollapseId
.. py:exception:: BadDeviceToken
.. py:exception:: BadExpirationDate
.. py:exception:: BadMessageId
.. py:exception:: BadPriority
.. py:exception:: BadTopic
.. py:exception:: DeviceTokenNotForTopic
.. py:exception:: DuplicateHeaders
.. py:exception:: IdleTimeout
.. py:exception:: MissingDeviceToken
.. py:exception:: MissingTopic
.. py:exception:: PayloadEmpty
.. py:exception:: BadCertificate
.. py:exception:: BadCertificateEnvironment
.. py:exception:: ExpiredProviderToken
.. py:exception:: Forbidden
.. py:exception:: InvalidProviderToken
.. py:exception:: MissingProviderToken
.. py:exception:: BadPath
.. py:exception:: MethodNotAllowed
.. py:exception:: Unregistered
.. py:exception:: PayloadTooLarge
.. py:exception:: TooManyProviderTokenUpdates
.. py:exception:: TooManyRequests
.. py:exception:: InternalServerError
.. py:exception:: ServiceUnavailable
.. py:exception:: Shutdown
.. py:exception:: UnkownResponseError
