""" Reproducer for BPO-39951

We send some data over ssl and close the connection.
The server responds after our openssl considers the connection closed-ish and raises an error.
"""
import asyncio
import ssl

host = "nghttp2.org"
port = 443

ssl_context = ssl.create_default_context()
ssl_context.options |= ssl.OP_NO_TLSv1
ssl_context.options |= ssl.OP_NO_TLSv1_1
ssl_context.set_alpn_protocols(["h2"])

# Captured from an HTTP/2 client
DATA = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n\x00\x00*\x04\x00\x00\x00\x00\x00\x00\x01\x00\x00\x10\x00\x00\x02\x00\x00\x00\x00\x00\x04\x00\x00\xff\xff\x00\x05\x00\x00@\x00\x00\x08\x00\x00\x00\x00\x00\x03\x00\x10\x00\x00\x00\x06\x00\x00\xff\xff\x00\x00\x04\x08\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04\x01\x00\x00\x00\x00"


async def test():
    r, w = await asyncio.open_connection(host, port, ssl=ssl_context)

    info = w.get_extra_info("ssl_object")
    assert info, "HTTP/2 server is required"
    proto = info.selected_alpn_protocol()
    assert proto == "h2", "Failed to negotiate HTTP/2"

    w.write(DATA)
    w.close()
    await w.wait_closed()


asyncio.run(test())
