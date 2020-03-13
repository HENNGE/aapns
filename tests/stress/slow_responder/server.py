import json
import logging
import time
import cherrypy


class Controller:
    @cherrypy.expose
    def default(*vpath, **kw):
        assert cherrypy.request.method == "POST"
        data = json.load(cherrypy.request.body)
        # {"aps": {"alert": {"body": "123"}}}
        # cherrypy.request.path_info
        # "/3/device/hexhexhex"
        # vpath
        # ("device", "hexhexhex")
        time.sleep(2)
        return json.dumps(dict(foo="quux"))


logging.basicConfig(level=logging.INFO)
cherrypy.config.update({"server.socket_host": "0.0.0.0"})
cherrypy.config.update({"server.thread_pool": 2048})
cherrypy.quickstart(Controller)
