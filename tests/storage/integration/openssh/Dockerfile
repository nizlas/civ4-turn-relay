# Disposable OpenSSH SFTP server for repository integration tests only.
# Generates an ephemeral host key and fixture user at container start.
FROM alpine:3.20

RUN apk add --no-cache openssh-server openssh-sftp-server \
    && mkdir -p /var/run/sshd /data/games /etc/ssh \
    && adduser -D -h /home/relaytest -s /bin/sh relaytest \
    && echo 'relaytest:relaytest-password-not-for-production' | chpasswd \
    && chown -R relaytest:relaytest /data \
    && ssh-keygen -A

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
