import json
import logging
import time
import cherrypy


class Controller:
    @cherrypy.expose
    def default(*vpath, **kw):
        logging.info(
            """
            method %r
            base %r
            request_line %r
            header %s
            """,
            cherrypy.request.method,
            cherrypy.request.base,
            cherrypy.request.request_line,
            "\n                   ".join(map(str, cherrypy.request.headers.items())),
        )
        if cherrypy.request.method != "POST":
            # assume it's a load-testing tool
            return json.dumps({"test": True})

        body = cherrypy.request.body.read()
        data = json.loads(body)
        logging.info(
            """
            body %r
            json %r
            """,
            body,
            data,
        )
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
