import logging
import time
import cherrypy

class Foo:
    @cherrypy.expose
    def index(self, **kw):
        logging.info("sleeping")
        time.sleep(5)
        logging.info("responding")
        return "foobar\n" * 10

logging.basicConfig(level=logging.INFO)
cherrypy.config.update({"server.socket_host": "0.0.0.0"})
cherrypy.config.update({"server.thread_pool": 2048})
cherrypy.quickstart(Foo())
