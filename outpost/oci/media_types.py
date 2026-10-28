"""The media types of OCI and Docker manifests, and the annotation that keeps an image's original name."""

MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
ACCEPT = ", ".join(MANIFEST_TYPES + INDEX_TYPES)  # what a pull asks the registry for
OCI_INDEX = INDEX_TYPES[0]
OCI_MANIFEST = MANIFEST_TYPES[0]
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER_GZIP = "application/vnd.oci.image.layer.v1.tar+gzip"
REF_ANNOTATION = "org.opencontainers.image.ref.name"
