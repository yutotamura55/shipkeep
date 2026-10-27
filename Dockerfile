FROM python:3.14-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements-dev.txt

WORKDIR /work
