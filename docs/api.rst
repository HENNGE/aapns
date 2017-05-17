API Reference
=============


``aapns.api``
-------------


.. py:module:: aapns.api

.. py:function:: connect(client_cert_path, server, *, ssl_context=None, logger=None, auto_reconnect=False, timeout=None)

    This is a coroutine.

    Connect to APNS. Use this coroutine to create :py:class:`APNS` instances.

    :param str client_cert_path: Path to the client certificate to authenticate with
    :param server: Server to connect to
    :type server: :py:class:`aapns.config.Server`
    :param ssl_context: Optional SSLContext instance to use
    :type ssl_context: :py:class:`ssl.SSLContext`
    :param logger: Optional structlog logger to use for logging
    :type logger: :py:class:`structlog.BoundLogger`
    :param bool auto_reconnect: Toggle automatic reconnecting.
    :param int timeout: Optional timeout for connections and requests. If set to ``None``, no timeout will be used.
    :return: A connected instance of :py:class:`APNS`
    :rtype: APNS

.. py:class:: APNS

    Main API of aapns. You should not create instances of this class yourself.
    Use :py:func:`connect` instead.

    .. py:method:: send_notification(token, notification, *, apns_id=None, expiration=None, priority=Priority.normal, topic=None, collapse_id=None):

        This is a coroutine.

        Send a notification to the device registered for the given token. Returns
        the notification ID.

        :param str token: Device token
        :param notification: The notification to send
        :type notification: :py:class:`aapns.models.Notification`
        :param str apns_id: Optional notification ID. If none is provided, APNS creates one
        :param int expiration: Optional unix timestamp when the notification should expire
        :param priority: Priority to use for the notification. See :py:class:`aapns.config.Priority`
        :type priority: :py:class:`aapns.config.Priority`
        :param str topic: Optional topic to send the notification to. If your certificate is for more than one topic, you must specify this parameter
        :param str collapse_id: Optional collapse id for this notification
        :return: ID of the notification
        :rtype: str
        :raises aapns.errors.ResponseError: If there was a problem with the notification
        :raises aapns.errors.StreamResetError: If the HTTP2 stream was reset by APNS


    .. py:method:: close

        This is a coroutine.

        Closes the connection if one is active.


``aapns.config``
----------------

.. py:module:: aapns.config


.. py:class:: Server(host: str, port: int)

    Class to configure the server to connect to, usually you do not need to
    manually create an instance but can use one of the pre-created instances in
    this module.

.. py:data:: Production

    Instance of :py:class:`Server` that points to the production APNS server.

.. py:data:: ProductionAltPort

    Same as :py:data:`Production` but uses the alternative port 2197.

.. py:data:: Development

    Instance of :py:class:`Server` that points to the testing APNS server.

.. py:data:: DevelopmentAltPort

    Same as :py:data:`Development` but uses the alternative port 2197.

.. py:class:: Priority

    An Enum to specify notification priority. Refer to Apples APNS documentation
    for what these values mean exactly.

    .. py:attribute:: immediately

        To send a notification immediately.

    .. py:attribute:: normal

        To send a notification with normal priority.


``aapns.errors``
----------------

.. py:module:: aapns.errors


.. py:exception:: APNSError

    Base class for all errors raised by aapns itself.

.. py:exception:: Disconnected

    Error raised by :py:meth:`aapns.api.APNS.send_notification` if the
    connection was lost and automatic reconnection is disabled or the reconnect
    failed.

.. py:exception:: StreamResetError

    Error raised if the HTTP2 stream used to send a notification was reset by APNS.

.. py:exception:: UnkownResponseError

    Error used when there was an unknown error with the notification.

.. py:exception:: ResponseError

    Base class for response errors when sending notifications.

    See below for concrete instances.

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


``aapns.models``
----------------

.. py:module:: aapns.models

.. py:class:: Notification(alert, badge=None, sound=None, content_available=False, category=None, thread_id=None, extra=None)

    Represents a notification to send. For details on the parameters, please
    refer to the Apple APNS documentation.

    :param alert: Alert to send
    :type alert: :py:class:`Alert`
    :param int badge: Optional badge number to set
    :param str sound: Optional path to sound file
    :param bool content_available: Optional flag to indicate there is content available
    :param str category: Optional category of this notification
    :param str thread_id: Optional thread ID of this notification
    :param extra: Optional dictionary holding app specific extra data to send with the notification
    :type extra: dict[str, str]

.. py:class:: Alert(body, title=None, action_loc_key=None, launch_image=None)

    Represents an alert, which can be used in :py:class:`Notification`.

    :param body: Body of the alert
    :type body: str or :py:class:`Localized`
    :param title: Optional title of the alert
    :type title: str or :py:class:`Localized`
    :param str action_loc_key: Optional localization key to use for the action button of the alert
    :param str launch_image: Optional path to the launch image to use for the alert


.. py:class:: Localized(key, args=None)

    Represents a localized string to be used for the body or title of an :py:class:`Alert`.

    :param str key: Localization key
    :param args: Optional list of localization arguments
    :type args: list[str]
