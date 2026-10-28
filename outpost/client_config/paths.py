"""Where the client files live on a node, and the private networks every node reaches directly."""

PATH_CA = "/etc/outpost/ca.crt"
PATH_CA_DIR = "/etc/ssl/certs"  # OpenSSL's default CA directory, looked up by subject hash
PATH_APT_KEY = "/etc/outpost/gitea-apt.asc"
PATH_APT_LIST = "/etc/apt/sources.list.d/outpost.list"
PATH_RPM_KEY = "/etc/outpost/gitea-rpm.asc"
PATH_APK_KEYS = "/etc/apk/keys"  # apk only reads keys from here, under the name the signer used
PATH_APK_REPOS = "/etc/apk/repositories"
PATH_RPM_REPO = "/etc/yum.repos.d/outpost.repo"
PATH_PROXY_ENV = "/etc/outpost/proxy.env"
PATH_PROFILE = "/etc/profile.d/outpost.sh"
PATH_ENVIRONMENT = "/etc/environment"
PATH_COREFILE = "/etc/outpost/Corefile"
PATH_NODE_RESOLV_CONF = "/etc/outpost/resolv.conf"  # handed to k3s so pods use the node's DNS

# Reached directly, never through the egress proxy; the seal lets every server reach them.
PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")

# The Actions runner's HOME. Jobs read $HOME/.docker/config.json there, so the proxy settings go there too.
RUNNER_HOME = "/var/lib/outpost-runner"

# The repo files the official RHEL-family images ship. A single-layer config image cannot move them
# aside, so each is overwritten with a comment-only file; a name not listed here is still harmless on
# a sealed node (the seal rejects the mirror), it just fails later and louder.
STOCK_RPM_REPOS = (
    "rocky.repo",
    "rocky-extras.repo",
    "rocky-addons.repo",
    "rocky-devel.repo",
    "almalinux-baseos.repo",
    "almalinux-appstream.repo",
    "almalinux-crb.repo",
    "almalinux-extras.repo",
    "almalinux-highavailability.repo",
    "almalinux-nfv.repo",
    "almalinux-plus.repo",
    "almalinux-resilientstorage.repo",
    "almalinux-rt.repo",
    "almalinux-sap.repo",
    "almalinux-saphana.repo",
    "fedora.repo",
    "fedora-updates.repo",
    "fedora-updates-testing.repo",
    "fedora-cisco-openh264.repo",
    "centos.repo",
    "centos-addons.repo",
)
