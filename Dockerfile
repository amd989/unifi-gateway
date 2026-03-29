FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc libc6-dev && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY . .

ENV UNIFI_GW_CONFIG=/app/conf/unifi-gateway.conf
ENV UNIFI_GW_LOG_LEVEL=INFO

ENTRYPOINT ["python", "unifi_gateway.py"]
CMD ["run"]
