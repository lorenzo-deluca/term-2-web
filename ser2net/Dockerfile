FROM alpine:3.21

RUN apk add --no-cache ser2net setserial grep netcat-openbsd

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]