import os

import pytest

from conftest import SYSTEM_RELEASE_ENV
from envparse import env


@pytest.fixture
def kernel(shell):
    """
    Install specific kernel version and configure
    the system to boot to it. The kernel version is not the
    latest one available in repositories.
    """

    # Set default kernel
    if "centos-7" in SYSTEM_RELEASE_ENV:
        assert shell("yum install kernel-3.10.0-1160.el7.x86_64 -y").returncode == 0
        shell("grub2-set-default 'CentOS Linux (3.10.0-1160.el7.x86_64) 7 (Core)'")
    elif "oracle-7" in SYSTEM_RELEASE_ENV:
        assert shell("yum install kernel-3.10.0-1160.el7.x86_64 -y").returncode == 0
        shell("grub2-set-default 'Oracle Linux Server 7.9, with Linux 3.10.0-1160.el7.x86_64'")
    elif "centos-8.4" in SYSTEM_RELEASE_ENV:
        assert shell("yum install kernel-4.18.0-305.3.1.el8 -y").returncode == 0
        shell("grub2-set-default 'CentOS Linux (4.18.0-305.3.1.el8.x86_64) 8'")
    elif "centos-8.5" in SYSTEM_RELEASE_ENV:
        assert shell("yum install kernel-4.18.0-348.el8 -y").returncode == 0
        shell("grub2-set-default 'CentOS Stream (4.18.0-348.el8.x86_64) 8'")
    # Test is being run only for the latest released oracle-linux
    elif "oracle-8" in SYSTEM_RELEASE_ENV:
        assert shell("yum install kernel-4.18.0-80.el8.x86_64 -y").returncode == 0
        shell("grub2-set-default 'Oracle Linux Server (4.18.0-80.el8.x86_64) 8.0'")

    if os.environ["TMT_REBOOT_COUNT"] == "0":
        shell("tmt-reboot -t 600")

    yield

    # We need to get the name of the latest kernel
    # present in the repositories

    # Install 'yum-utils' required by the repoquery command
    shell("yum install yum-utils -y")

    # Get the name of the latest kernel
    latest_kernel = shell(
        "repoquery --quiet --qf '%{BUILDTIME}\t%{VERSION}-%{RELEASE}' kernel 2>/dev/null | tail -n 1 | awk '{printf $NF}'"
    ).output

    # Get the full name of the kernel
    full_name = shell(
        "grubby --info ALL | grep \"title=.*{}\" | tr -d '\"' | sed 's/title=//'".format(latest_kernel)
    ).output

    # Set the latest kernel as the one we want to reboot to
    shell("grub2-set-default '{}'".format(full_name.strip()))

    if os.environ["TMT_REBOOT_COUNT"] == "1":
        shell("tmt-reboot -t 600")


def test_non_latest_kernel_inhibitor(kernel, shell, convert2rhel):
    """
    System has non latest kernel installed, thus the conversion
    has to be inhibited.
    """

    with convert2rhel(
        "-y --no-rpm-va --serverurl {} --username {} --password {} --pool {} --debug".format(
            env.str("RHSM_SERVER_URL"),
            env.str("RHSM_USERNAME"),
            env.str("RHSM_PASSWORD"),
            env.str("RHSM_POOL"),
        )
    ) as c2r:
        if "centos-8" in SYSTEM_RELEASE_ENV:
            c2r.expect(
                "The version of the loaded kernel is different from the latest version in repositories defined in the"
            )
        else:
            c2r.expect(
                "The version of the loaded kernel is different from the latest version in the enabled system repositories."
            )
    assert c2r.exitstatus != 0
