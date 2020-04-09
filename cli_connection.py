import asyncio
import logging
import sys

import click

from aapns.connection import Connection, Request, create_ssl_context

USAGE = (
    "Usage: python cli_connection.py [opts] device_token cmd message-1 message-2 ..."
)


async def send_several(ssl_context, base_url, requests):
    c = await Connection.create(base_url, ssl=ssl_context)

    async def post(r):
        try:
            logging.info("%s", r)
            logging.info("%s", await c.post(r))
        except Exception as rv:
            logging.info("Failed with %r", rv)

    try:
        tasks = []
        for r in requests:
            logging.info("Sleeping a bit")
            await asyncio.sleep(1)
            tasks.append(asyncio.create_task(post(r)))
        await asyncio.gather(*tasks)
    finally:
        await c.close()


@click.command()
@click.option("--verbose", is_flag=True, default=False)
@click.option("--local", is_flag=True, default=False)
@click.option("--sandbox", is_flag=True, default=False)
@click.option("--prod", is_flag=True, default=False)
@click.option("--alt-port", is_flag=True, default=False)
@click.option("--localized", is_flag=True, default=False)
@click.option("--client-cert-path")
@click.argument("device_token")
@click.argument("messages", nargs=-1)
def main(
    verbose,
    local,
    sandbox,
    prod,
    alt_port,
    localized,
    client_cert_path,
    device_token,
    messages,
):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    if sum((local, sandbox, prod)) != 1:
        raise Exception("Specify one of --local/--sandbox/--prod")

    ssl_context = create_ssl_context()
    if local:
        host = "localhost"
        ssl_context.load_verify_locations(cafile=".test-server-certificate.pem")
    elif sandbox:
        host = "api.development.push.apple.com"
    elif prod:
        host = "api.push.apple.com"

    ssl_context.load_cert_chain(certfile=client_cert_path, keyfile=client_cert_path)

    port = 2197 if alt_port else 443
    base_url = f"https://{host}:{port}"
    key = "loc-key" if localized else "body"
    requests = [
        Request.new(
            f"/3/device/{device_token}",
            {"Apns-Priority": "5", "Apns-Push-Type": "alert"},
            {"aps": {"alert": {key: text}}},
            timeout=10,
        )
        for text in messages
    ]
    asyncio.run(send_several(ssl_context, base_url, requests))


if __name__ == "__main__":
    main()
