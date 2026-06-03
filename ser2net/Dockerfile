FROM debian:bookworm-slim

# Install ser2net and utilities. Debian packages of ser2net are typically
# built with YAML support which allows using the `trace-read` option.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends ser2net setserial grep netcat-openbsd iproute2 \
	&& rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
