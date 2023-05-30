import pytest

from envparse import env


@pytest.mark.activation_key_conversion
def test_activation_key_conversion(convert2rhel, credentials):
    with convert2rhel(
        "-y --no-rpm-va --serverurl {} -k {} -o {} --debug".format(
            credentials.get("RHSM_SERVER_URL"),
            credentials.get("RHSM_KEY"),
            credentials.get("RHSM_ORG"),
        )
    ) as c2r:
        c2r.expect("Conversion successful!")
    assert c2r.exitstatus == 0
