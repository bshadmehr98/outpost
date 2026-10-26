# A throwaway "VPS": Ubuntu 24.04 with systemd and sshd. `outpost lab up` builds it and starts a few.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      systemd systemd-sysv openssh-server sudo curl ca-certificates iptables iproute2 git gnupg util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
    && systemctl enable ssh \
    && systemctl mask systemd-resolved systemd-networkd systemd-timesyncd getty@tty1 || true
COPY authorized_keys /root/.ssh/authorized_keys
RUN chmod 600 /root/.ssh/authorized_keys
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
