import json
import os.path

import jsonschema
import pytest

from conftest import _load_json_schema
from envparse import env


PRE_CONVERSION_REPORT = "/var/log/convert2rhel/convert2rhel-pre-conversion.json"
PRE_CONVERSION_REPORT_JSON_SCHEMA = _load_json_schema(path="../../../../../schemas/assessment-schema-1.0.json")


def _validate_report():
    """
    Helper function.
    Verify the report is created in /var/log/convert2rhel/convert2rhel-pre-conversion.json,
    and it corresponds to its respective schema.
    """
    assert os.path.exists(PRE_CONVERSION_REPORT)

    with open(PRE_CONVERSION_REPORT, "r") as data:
        data_json = json.load(data)
        # If some difference between generated json and its schema invoke exception
        try:
            jsonschema.validate(instance=data_json, schema=PRE_CONVERSION_REPORT_JSON_SCHEMA)
        except Exception:
            print(data_json)
            raise


@pytest.mark.test_failures_and_skips_in_report
def test_failures_and_skips_in_report(convert2rhel):
    """
    Verify that the assessment report contains the following headers and messages:
    Error header, skip header, success header.

    Verify the report is created in /var/log/convert2rhel/convert2rhel-pre-conversion.json,
    and it corresponds to its respective schema.
    """
    with convert2rhel(
        "--no-rpm-va --serverurl {} --username test --password test --pool a_pool --debug".format(
            env.str("RHSM_SERVER_URL"),
        )
    ) as c2r:
        # We need to get past the data collection acknowledgement.
        c2r.expect("Continue with the system conversion?")
        c2r.sendline("y")

        # Assert that we start rollback first
        assert c2r.expect("Rollback: RHSM-related actions") == 0

        # Then, verify that the analysis report is printed
        assert c2r.expect("Pre-conversion analysis report", timeout=600) == 0

        # Error header first
        assert c2r.expect("Must fix before conversion", timeout=600) == 0
        c2r.expect("SUBSCRIBE_SYSTEM.UNKNOWN_ERROR: Unable to register the system")

        # Skip header
        assert c2r.expect("Could not be checked due to other failures", timeout=600) == 0
        c2r.expect("ENSURE_KERNEL_MODULES_COMPATIBILITY.SKIP: Skipped")

        # Success header
        assert c2r.expect("No changes needed", timeout=600) == 0

    assert c2r.exitstatus == 0

    _validate_report()


@pytest.mark.test_successful_report
def test_successful_report(convert2rhel):
    """
    Verify that the assessment report contains the following header: Success header.
    And does not contain: Error header, skip header, success header.

    Verify the report is created in /var/log/convert2rhel/convert2rhel-pre-conversion.json,
    and it corresponds to its respective schema.
    """
    with convert2rhel(
        "--no-rpm-va --serverurl {} --username {} --password {} --pool {} --debug".format(
            env.str("RHSM_SERVER_URL"),
            env.str("RHSM_USERNAME"),
            env.str("RHSM_PASSWORD"),
            env.str("RHSM_POOL"),
        )
    ) as c2r:
        # We need to get past the data collection acknowledgement.
        c2r.expect("Continue with the system conversion?")
        c2r.sendline("y")

        # Assert that we start rollback first
        assert c2r.expect("Rollback: RHSM-related actions") == 0

        # Then, verify that the analysis report is printed
        assert c2r.expect("Pre-conversion analysis report", timeout=600) == 0

        # Verify that only success header printed out, assert if error or skip header appears
        c2r_report_header_index = c2r.expect(
            ["No changes needed", "Must fix before conversion", "Could not be checked due to other failures"],
            timeout=300,
        )
        if c2r_report_header_index == 0:
            pass
        elif c2r_report_header_index == 1:
            assert AssertionError("Error header in the analysis report.")
        elif c2r_report_header_index == 2:
            assert AssertionError("Skip header in the analysis report.")

    assert c2r.exitstatus == 0

    _validate_report()
