ARG DEBIAN=13
FROM debian:${DEBIAN}-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-yaml python3-cryptography caddy curl openssl ufw iproute2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
ENV PYTHONDONTWRITEBYTECODE=1 TEST_CADDY=/usr/bin/caddy
CMD ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
