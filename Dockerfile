FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 stig \
    && git config --system safe.directory /workspace \
    && git config --system user.name Stig \
    && git config --system user.email stig@example.com

COPY packaging/container-requirements.txt /tmp/container-requirements.txt
RUN pip install -r /tmp/container-requirements.txt

COPY pyproject.toml README.md /tmp/stig/
COPY stig /tmp/stig/stig
RUN pip install --no-deps /tmp/stig \
    && rm -rf /tmp/stig /tmp/container-requirements.txt \
    && mkdir /workspace \
    && chown stig:stig /workspace

USER stig
WORKDIR /workspace
ENTRYPOINT ["stig"]
CMD ["--help"]
