# -*- coding: utf-8 -*-
#
# Copyright(C) 2021 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

__metaclass__ = type

import json
import os
import re
import sys

from datetime import datetime

from convert2rhel import pkghandler, utils
from convert2rhel.logger import root_logger
from convert2rhel.systeminfo import system_info
from convert2rhel.toolopts import tool_opts
from convert2rhel.utils import files


# Path to the migration results of the old breadcrumbs.
MIGRATION_RESULTS_FILE = "/etc/migration-results"

# Path to the RHSM facts folder.
RHSM_CUSTOM_FACTS_FOLDER = "/etc/rhsm/facts"
# Path to the RHSM custom facts file generated by Convert2RHEL.
RHSM_CUSTOM_FACTS_FILE = os.path.join(RHSM_CUSTOM_FACTS_FOLDER, "convert2rhel.facts")
# Unique identifier under the RHSM custom facts.
RHSM_CUSTOM_FACTS_NAMESPACE = "conversions"

logger = root_logger.getChild(__name__)


class Breadcrumbs:
    """The so-called breadcrumbs data is a collection of basic information about the convert2rhel execution.

    This data is to be stored in a specific file in a machine-readable format which can be collected by various
    tools like sosreport or Red Hat Insights for further analysis.
    """

    def __init__(self):
        # Record what type of convert2rhel run we are performing.  Valid options right now are convert or analyze.
        self.activity = "null"
        # Version of the JSON schema of the breadcrumbs file. To be changed when the JSON schema changes.
        self.version = "1"
        # The convert2rhel command as executed by the user including all the options.
        self.executed = "null"
        # NEVRA = Name, Epoch, Version, Release, Architecture
        self.nevra = "null"
        # The convert2rhel package signature as stored in the RPM DB.
        self.signature = "null"
        # A boolean indicating whether the conversion stopped before successfully converting the system or not.
        self.success = "null"
        self.activity_started = "null"
        self.activity_ended = "null"
        self.source_os = "null"
        self.target_os = "null"
        # Convert2RHEL-related environment variables used while executing convert2rhel (CONVERT2RHEL_*).
        self.env = {}
        # Run ID is to be populated by Leapp only. The value should be null in the json generated by convert2rhel.
        self.run_id = "null"
        # The convert2rhel package object from the yum/dnf python API for further information extraction.
        self._pkg_object = None
        # The conversion was run with EUS/ELS
        self.non_default_channel = "null"

    def collect_early_data(self):
        """Set data which is accessible before the conversion"""
        self._set_activity()
        self._set_pkg_object()
        self._set_executed()
        self._set_nevra()
        self._set_signature()
        self._set_source_os()
        self._set_started()
        self._set_env()
        self._set_non_default_channel()

    def finish_collection(self, success=False):
        """Set the final data for breadcrumbs after the conversion ends.

        :param success: Flag to determinate the if the conversion process was
            successfull.
        :type success: bool
        """
        self.success = success

        if success and self.activity == "conversion":
            self._set_target_os()

        self._set_ended()

        self._save_migration_results()
        self._save_rhsm_facts()

    def _set_activity(self):
        """Set the activity that convert2rhel is going to perform"""
        self.activity = tool_opts.activity

    def _set_pkg_object(self):
        """Set pkg_object which is used to get information about installed Convert2RHEL"""
        # the index position is there because get_installed_pkg_objects return list, which is filtered and
        # should contain just one item
        self._pkg_object = pkghandler.get_installed_pkg_objects(name="convert2rhel")[0]

    def _set_executed(self):
        """Set how was Convert2RHEL executed"""
        self.executed = " ".join(utils.hide_secrets(args=sys.argv))

    def _set_nevra(self):
        """Set NEVRA of installed Convert2RHEL"""
        self.nevra = pkghandler.get_pkg_nevra(self._pkg_object, include_zero_epoch=True)

    def _set_signature(self):
        """Set signature of installed Convert2RHEL"""
        package = pkghandler.get_installed_pkg_information(str(self._pkg_object))[0]
        self.signature = package.signature

    def _set_started(self):
        """Set start time of activity"""
        self.activity_started = self._get_formatted_time()

    def _set_ended(self):
        """Set end time of activity"""
        self.activity_ended = self._get_formatted_time()

    def _get_formatted_time(self):
        """Set timestamp in format YYYYMMDDHHMMZ"""
        return datetime.utcnow().isoformat() + "Z"

    def _set_env(self):
        """Catch and set CONVERT2RHEL_ environment variables"""
        env_list = os.environ
        env_c2r = {}

        # filter just environment variables for C2R
        for env in env_list:
            if re.match(r"^CONVERT2RHEL_", env):
                env_c2r[env] = env_list[env]

        self.env = env_c2r

    def _set_non_default_channel(self):
        """Set whether the conversion was run with EUS/ELS"""
        if tool_opts.eus:
            self.non_default_channel = "EUS"
        elif tool_opts.els:
            self.non_default_channel = "ELS"

    def _set_source_os(self):
        """Set the source os release information."""
        self.source_os = system_info.get_system_release_info()
        if self.source_os["id"] is None:
            # rhsm facts can only be strings or booleans
            self.source_os["id"] = "null"

    def _set_target_os(self):
        """Set the target os release information."""
        # Reading the system-release file again to get the target os information.
        system_release_content = system_info.get_system_release_file_content()
        self.target_os = system_info.get_system_release_info(system_release_content)
        if self.target_os["id"] is None:
            # rhsm facts can only be strings or booleans
            self.target_os["id"] = "null"

    @property
    def data(self):
        """Property that return the current state of the breadcrumbs

        :return: A dictionary containing the collected data through the conversion.
        :rtype: dict[str, Any]
        """
        return {
            "version": self.version,
            "activity": self.activity,
            "packages": [{"nevra": self.nevra, "signature": self.signature}],
            "executed": self.executed,
            "success": self.success,
            "activity_started": self.activity_started,
            "activity_ended": self.activity_ended,
            "source_os": self.source_os,
            "target_os": self.target_os,
            "env": self.env,
            "run_id": self.run_id,
        }

    def _save_migration_results(self):
        """Write the results of the breadcrumbs to the migration-results file."""
        logger.info("Writing breadcrumbs to '%s'.", MIGRATION_RESULTS_FILE)
        _write_obj_to_array_json(path=MIGRATION_RESULTS_FILE, new_object=self.data, key="activities")

    def _save_rhsm_facts(self):
        """Write the results of the breadcrumbs to the rhsm custom facts file."""
        if not os.path.exists(RHSM_CUSTOM_FACTS_FOLDER):
            logger.debug("No RHSM facts folder found at '{}'. Creating a new one...".format(RHSM_CUSTOM_FACTS_FOLDER))
            # Using mkdir_p here as the `/etc/rhsm` might not exist at all.
            # Usually this can happen if we fail in the first run and we want to
            # save the custom facts gathered so far, or, if the `--no-rhsm` option
            # is provided.
            # This is safe as the RHSM_CUSTOM_FACTS_FOLDER, /etc/rhsm/facts, and its parents
            # are only writable by root
            files.mkdir_p(RHSM_CUSTOM_FACTS_FOLDER)

        data = utils.flatten(dictionary=self.data, parent_key=RHSM_CUSTOM_FACTS_NAMESPACE)
        logger.info("Writing RHSM custom facts to '%s'.", RHSM_CUSTOM_FACTS_FILE)
        # We don't need to use `_write_obj_to_array_json` function here, because
        # we only care about dumping the facts without having multiple copies of
        # it.
        utils.write_json_object_to_file(path=RHSM_CUSTOM_FACTS_FILE, data=data)

    def print_data_collection(self):
        """Print information about data collection and ask for acknowledgement."""
        logger.info(
            "The convert2rhel utility generates a %s file that contains the below data about the system conversion."
            " The subscription-manager then uploads the data to the server the system is registered to.\n"
            "- The Convert2RHEL command as executed\n"
            "- The Convert2RHEL RPM version and GPG signature\n"
            "- Success or failure status of the conversion\n"
            "- Conversion start and end timestamps\n"
            "- Source OS vendor and version\n"
            "- Target RHEL version\n"
            "- Convert2RHEL related environment variables\n\n",
            RHSM_CUSTOM_FACTS_FILE,
        )
        utils.ask_to_continue()


def _write_obj_to_array_json(path, new_object, key):
    """Write new object to array defined by key in JSON file.
    If the file doesn't exist, create new one and create key for inserting.
    If the file is corrupted, append complete object (with key) as if it was new file and the
    original content of file stays there.
    """
    if not (os.path.exists(path)):
        with open(path, "a") as file:
            file_content = {key: []}
            json.dump(file_content, file, indent=4)

    # the file can be changed just by root
    os.chmod(path, 0o600)

    with open(path, "r+") as file:
        try:
            file_content = json.load(file)  # load data
            # valid json: update the JSON structure and rewrite the file
            file.seek(0)
        # The file contains something that isn't json.
        # Create activities and append to the file, JSON won't be valid, but the content of the file stays there
        # for administrators, etc.
        except ValueError:  # we cannot use json.decoder.JSONDecodeError due python 2.7 compatibility
            file_content = {key: []}

        try:
            file_content[key].append(new_object)  # append new_object to activities
        # valid json, but no 'activities' key there
        except KeyError:
            # create new 'activities' key which contains new_object
            file_content[key] = [new_object]
            # valid json: update the JSON structure and rewrite the file
            file.seek(0)

        # write the json to the file
        json.dump(file_content, file, indent=4)


# Code to be executed upon module import
breadcrumbs = Breadcrumbs()
