import asyncio
import logging
from asyncio import get_event_loop
from typing import Dict, Optional

import attr
import click

from aapns import config, models
from aapns.api import create_client

SERVERS: Dict[bool, Dict[bool, config.Server]] = {
    True: {True: config.ProductionAltPort, False: config.Production},
    False: {True: config.DevelopmentAltPort, False: config.Development},
}

LOCAL_SERVERS: Dict[bool, config.Server] = {
    False: config.Server("localhost", 443),
    True: config.Server("localhost", 2197),
}


@attr.s
class Context:
    token = attr.ib()
    cert = attr.ib()
    server = attr.ib()
    apns_id = attr.ib()
    expiration = attr.ib()
    priority = attr.ib()
    topic = attr.ib()
    collapse_id = attr.ib()
    verbose = attr.ib()


async def do_send(context: Context, notification: models.Notification) -> Optional[str]:
    client = await create_client(
        context.cert,
        context.server,
        **{"cafile": "tests/functional/test-server-certificate.pem"}
        if context.server.host == "localhost"
        else {},
    )
    try:
        resp_id = await client.send_notification(
            context.token,
            notification,
            apns_id=context.apns_id,
            expiration=context.expiration,
            priority=context.priority,
            topic=context.topic,
            collapse_id=context.collapse_id,
        )
    finally:
        await client.close()
    return resp_id


def send(context: Context, notification: models.Notification):
    resp_id = asyncio.run(do_send(context, notification))
    click.echo(resp_id)


@click.group()
@click.argument("token")
@click.option("--client-cert-path", envvar="CLIENT_CERT_PATH")
@click.option("--prod", is_flag=True, default=False)
@click.option("--alt-port", is_flag=True, default=False)
@click.option("--expiration", default=None)
@click.option("--immediately", is_flag=True, default=False)
@click.option("--topic", default=None)
@click.option("--collapse-id", default=None)
@click.option("--apns-id", default=None)
@click.option("--verbose", is_flag=True, default=False)
@click.option("--local", is_flag=True, default=False)
@click.pass_context
def main(
    ctx,
    token,
    client_cert_path,
    prod,
    alt_port,
    expiration,
    immediately,
    topic,
    collapse_id,
    apns_id,
    verbose,
    local,
):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    ctx.obj = Context(
        token=token,
        cert=client_cert_path,
        server=LOCAL_SERVERS[alt_port] if local else SERVERS[prod][alt_port],
        expiration=expiration,
        priority=config.Priority.immediately if immediately else config.Priority.normal,
        topic=topic,
        collapse_id=collapse_id,
        apns_id=apns_id,
        verbose=verbose,
    )


@main.command("simple")
@click.argument("body")
@click.option("--title", default=None)
@click.pass_context
def simple(ctx, title, body):
    try:
        notification = models.Notification(alert=models.Alert(title=title, body=body))
        send(ctx.obj, notification)
    except Exception:
        logging.exception("Simple notification")


@main.command("localized")
@click.argument("body")
@click.option("--body-args", multiple=True)
@click.option("--title", default=None)
@click.option("--title-args", multiple=True)
@click.option("--badge", type=click.INT)
@click.pass_context
def localized(ctx, title, body, title_args, body_args, badge):
    try:
        notification = models.Notification(
            alert=models.Alert(
                body=models.Localized(body, list(body_args)),
                title=models.Localized(title, list(title_args)) if title else title,
            ),
            badge=badge,
        )
        send(ctx.obj, notification)
    except Exception:
        logging.exception("Localised notification")
