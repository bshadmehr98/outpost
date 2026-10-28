import pytest

from outpost import distros


def test_os_family_from_os_release_facts():
    assert distros.os_family({"id": "ubuntu", "id_like": "debian"}) == "apt"
    assert distros.os_family({"id": "debian"}) == "apt"
    assert distros.os_family({"id": "alpine"}) == "apk"
    assert distros.os_family({"id": "rocky", "id_like": "rhel centos fedora"}) == "rpm"
    assert distros.os_family({"id": "fedora"}) == "rpm"
    assert distros.os_family({"id": "almalinux", "id_like": "rhel centos fedora"}) == "rpm"
    assert distros.os_family({"id": "centos", "id_like": "rhel fedora"}) == "rpm"
    assert distros.os_family({"codename": "noble"}) == "apt" and distros.os_family({}) == "apt"  # unknown: apt


def test_a_servers_own_distro():
    assert (
        distros.apk_branch_of({"version_id": "3.20.3"}) == "v3.20" and distros.apk_branch_of({"version_id": ""}) is None
    )
    assert distros.rpm_distro_of_host({"id": "rocky", "version_id": "9.4"}) == "rocky9"
    assert distros.rpm_distro_of_host({"id": "almalinux", "version_id": "9.4"}) == "alma9"
    assert distros.rpm_distro_of_host({"id": "fedora", "version_id": "40"}) == "fedora40"
    assert distros.rpm_distro_of_host({"id": "centos", "version_id": "9"}) == "stream9"
    assert distros.rpm_distro_of_host({"id": "ubuntu", "version_id": "24.04"}) is None
    assert distros.distro_of_host({"id": "debian", "codename": "bookworm"}) == "bookworm"
    assert distros.distro_of_host({"id": "alpine", "version_id": "3.20.1"}) == "v3.20"
    assert distros.distro_of_host({"id": "rocky", "id_like": "rhel", "version_id": "9.4"}) == "rocky9"
    assert distros.distro_of_host({"id": "debian"}) is None


def test_distro_names_per_kind():
    assert [distros.kind_for_distro(d) for d in ("bookworm", "v3.20", "rocky9", None)] == ["apt", "apk", "rpm", None]
    assert distros.is_apt_distro("noble") and not distros.is_apt_distro("rocky9")
    assert distros.is_apk_branch("v3.20") and not distros.is_apk_branch("edge") and not distros.is_apk_branch("3.20")
    assert distros.apk_branch_version("v3.19") == "3.19"
    assert distros.split_rpm_distro("fedora40") == ("fedora", "40")
    with pytest.raises(ValueError, match="rocky9, alma9"):
        distros.split_rpm_distro("rhel9")
