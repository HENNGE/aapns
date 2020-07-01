import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Optional

import click
from aapns import config, models
from aapns.api import Server, Simulator, Target

ORIGINS = {
    "prod": config.PRODUCTION_HOST,
    "sandbox": config.SANDBOX_HOST,
    "local": "localhost",
}


@dataclass(frozen=True)
class Context:
    token: str
    target: Target
    apns_id: str
    expiration: Optional[int]
    priority: config.Priority
    topic: Optional[str]
    collapse_id: Optional[str]
    verbose: bool


async def do_send(context: Context, notification: models.Notification) -> Optional[str]:
    client = await context.target.create_client()
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
def main():
    pass


@main.command()
@click.argument("token")
@click.argument("body")
@click.option("--title", default=None)
@click.option("--client-cert-path", envvar="CLIENT_CERT_PATH")
@click.option(
    "--server", type=click.Choice(["prod", "sandbox", "local"]), default="sandbox"
)
@click.option("--alt-port", is_flag=True, default=False)
@click.option("--expiration", default=None, type=click.INT)
@click.option("--immediately", is_flag=True, default=False)
@click.option("--topic", default=None)
@click.option("--collapse-id", default=None)
@click.option("--apns-id", default=None)
@click.option("--verbose", is_flag=True, default=False)
def server(
    title,
    body,
    token,
    client_cert_path,
    server,
    alt_port,
    expiration,
    immediately,
    topic,
    collapse_id,
    apns_id,
    verbose,
):
    target = Server(
        client_cert_path,
        ORIGINS[server],
        config.ALT_PORT if alt_port else config.DEFAULT_PORT,
    )
    if server == "local":
        target = replace(target, ca_file="tests/functional/test-server-certificate.pem")
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    context = Context(
        token=token,
        target=target,
        expiration=expiration,
        priority=config.Priority.immediately if immediately else config.Priority.normal,
        topic=topic,
        collapse_id=collapse_id,
        apns_id=apns_id,
        verbose=verbose,
    )
    try:
        notification = models.Notification(alert=models.Alert(title=title, body=body))
        send(context, notification)
    except Exception:
        logging.exception("Simple notification")


@main.command()
@click.argument("device_id")
@click.argument("app_id")
@click.argument("body")
@click.option("--title", default=None)
@click.option("--expiration", default=None, type=click.INT)
@click.option("--immediately", is_flag=True, default=False)
@click.option("--topic", default=None)
@click.option("--collapse-id", default=None)
@click.option("--apns-id", default=None)
@click.option("--verbose", is_flag=True, default=False)
def simulator(
    device_id,
    app_id,
    title,
    body,
    expiration,
    immediately,
    topic,
    collapse_id,
    apns_id,
    verbose,
):
    target = Simulator(device_id, app_id,)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    context = Context(
        token="cli",
        target=target,
        expiration=expiration,
        priority=config.Priority.immediately if immediately else config.Priority.normal,
        topic=topic,
        collapse_id=collapse_id,
        apns_id=apns_id,
        verbose=verbose,
    )
    try:
        notification = models.Notification(alert=models.Alert(title=title, body=body))
        send(context, notification)
    except Exception:
        logging.exception("Simple notification")
