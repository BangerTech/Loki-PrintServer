FROM python:3.12-slim-bookworm

LABEL maintainer="Loki-PrintServer"
LABEL description="Loki USB over IP Server - Share USB devices over the network"
LABEL version="1.5.0"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    usbip \
    kmod \
    udev \
    libusb-1.0-0 \
    libusb-dev \
    avahi-daemon \
    avahi-utils \
    curl \
    iproute2 \
    procps \
    socat \
    cups \
    cups-client \
    cups-bsd \
    printer-driver-all \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/
COPY scripts/ ./scripts/
COPY web/ ./web/
COPY config/ ./config/
COPY VERSION ./VERSION

RUN chmod +x scripts/*.sh

EXPOSE 7575
EXPOSE 7576

CMD ["bash", "scripts/start.sh"]
