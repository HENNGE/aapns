FROM ubuntu:17.04

RUN apt-get update -y && apt-get upgrade -y && apt-get install -y python3.6 python3.6-venv python3.6-dev libssl-dev libffi-dev build-essential && python3.6 -mvenv /virtualenv/ && /virtualenv/bin/pip install -U pip setuptools wheel
