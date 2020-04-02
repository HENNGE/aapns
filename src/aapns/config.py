from enum import Enum

import attr


@attr.s
class Server:
    host = attr.ib()
    port = attr.ib()


Production = Server("api.push.apple.com", 443)
ProductionAltPort = attr.evolve(Production, port=2197)
Development = Server("api.development.push.apple.com", 443)
DevelopmentAltPort = attr.evolve(Development, port=2197)


class Priority(Enum):
    immediately = 10
    normal = 5
