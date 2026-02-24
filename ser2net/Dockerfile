FROM alpine:latest
RUN apk add --no-cache ser2net
CMD ["/usr/sbin/ser2net", "-n", "-c", "/data/ser2net.yaml"]