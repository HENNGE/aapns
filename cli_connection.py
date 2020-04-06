import asyncio
import logging
import sys

from aapns.connection import Connection, Request, create_ssl_context

USAGE = (
    "Usage: python cli_connection.py [opts] device_token cmd message-1 message-2 ..."
)


async def send_several(ssl_context, base_url, requests):
    c = await Connection.create(base_url, ssl=ssl_context)
    try:
        tasks = []
        for r in requests:
            logging.info("Sleeping a bit")
            await asyncio.sleep(1)

            async def post(r):
                try:
                    logging.info("%s", r)
                    logging.info("%s", await c.post(r))
                except Exception as rv:
                    logging.info("Failed with %r", rv)

            # FIXME 2. Optional header fields
            # Apns-Id, Apns-Expiration, Apns-Topic, Apns-Collapse-Id
            tasks.append(asyncio.create_task(post(r)))
        await asyncio.gather(*tasks)
    finally:
        await c.close()



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ssl_context = create_ssl_context()

    argv = sys.argv[1:]
    if "--prod" in argv:
        argv.remove("--prod")
        host = "api.push.apple.com"
    elif "--sandbox" in argv:
        argv.remove("--sandbox")
        host = "api.development.push.apple.com"
    elif "--local" in argv:
        argv.remove("--local")
        host = "localhost"
        ssl_context.load_verify_locations(cafile="tests/stress/go1/cert.pem")
    else:
        raise Exception("Must pass flag: --prod/--sandbox/--local")

    if "--alt-port" in argv:
        argv.remove("--alt-port")
        port = 2197
    else:
        port = 443

    if "simple" in argv:
        argv.remove("simple")
        key = "body"
    elif "localized" in argv:
        argv.remove("localized")
        key = "loc-key"
    else:
        raise Exception("Must pass command: simple or localized")

    if "--client-cert-path" in argv:
        i = argv.index("--client-cert-path")
        cert = argv[i + 1]
        del argv[i : i + 2]
        ssl_context.load_cert_chain(certfile=cert, keyfile=cert)
    else:
        raise Exception("Must pass flag: --client-cert-path path-file.pem")

    if not argv:
        raise Exception("Must pass device_token")
    device_token = argv[0]
    del argv[0]

    if not argv:
        raise Exception(USAGE)

    base_url = f"https://{host}:{port}"
    requests = [
        Request.new(
            f"/3/device/{device_token}",
            {"Apns-Priority": "5", "Apns-Push-Type": "alert"},
            {"aps": {"alert": {key: text}}},
            timeout=10,
        )
        for text in argv
    ]

    asyncio.run(send_several(ssl_context, base_url, requests))
