# Copyright(C) 2023 Red Hat, Inc.
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


import os
import re

from convert2rhel import actions, backup, exceptions
from convert2rhel.backup.files import MissingFile, RestorableFile
from convert2rhel.logger import LOG_DIR, root_logger
from convert2rhel.pkghandler import VERSIONLOCK_FILE_PATH
from convert2rhel.redhatrelease import os_release_file, system_release_file
from convert2rhel.repo import DEFAULT_DNF_VARS_DIR, DEFAULT_YUM_REPOFILE_DIR, DEFAULT_YUM_VARS_DIR
from convert2rhel.systeminfo import system_info
from convert2rhel.toolopts import tool_opts
from convert2rhel.utils import warn_deprecated_env
from convert2rhel.utils.rpm import PRE_RPM_VA_LOG_FILENAME


# Regex explanation:
# Match missing or SM5DLUGTP (letters can be replaced by dots or ?) - output of rpm -Va:
#   (missing|([S\.\?][M\.\?][5\.\?][D\.\?][L\.\?][U\.\?][G\.\?][T\.\?][P\.\?]))
# Match whitespace: \s+
# Match type of file, can be replaced by any whitecharacter:
#   [cdlr\s+]
# Match unix path:
#   [\/\\](?:(?!\.\s+)\S)+(\.)?
RPM_VA_REGEX = re.compile(
    r"^(missing|([S\.\?][M\.\?][5\.\?][D\.\?][L\.\?][U\.\?][G\.\?][T\.\?][P\.\?]))\s+[cdlrg\s+]\s+[\/\\](?:(?!\.\s+)\S)+(\.)?$"
)


logger = root_logger.getChild(__name__)


class BackupRedhatRelease(actions.Action):
    id = "BACKUP_REDHAT_RELEASE"

    def run(self):
        """Backup redhat release file before starting conversion process"""
        logger.task("Backup Redhat Release Files")

        super(BackupRedhatRelease, self).run()

        try:
            # TODO(r0x0d): We need to keep calling those global objects from
            # redhatrelease.py because of the below code:
            # https://github.com/oamg/convert2rhel/blob/v1.2/convert2rhel/subscription.py#L189-L200
            backup.backup_control.push(system_release_file)
            backup.backup_control.push(os_release_file)
        except exceptions.CriticalError as e:
            self.set_result(
                level="ERROR",
                id=e.id,
                title=e.title,
                description=e.description,
                diagnosis=e.diagnosis,
                remediations=e.remediations,
                variables=e.variables,
            )


class BackupRepository(actions.Action):
    id = "BACKUP_REPOSITORY"

    def run(self):
        """Backup .repo files in /etc/yum.repos.d/ so the repositories can be restored on rollback."""
        logger.task("Backup Repository Files")

        super(BackupRepository, self).run()

        logger.info("Backing up .repo files from {}.".format(DEFAULT_YUM_REPOFILE_DIR))

        if not os.listdir(DEFAULT_YUM_REPOFILE_DIR):
            logger.info("Repository folder %s seems to be empty.", DEFAULT_YUM_REPOFILE_DIR)

        for repo in os.listdir(DEFAULT_YUM_REPOFILE_DIR):
            # backing up redhat.repo so repo files are properly backed up when doing satellite conversions

            if not repo.endswith(".repo"):
                logger.info("Skipping backup as {} is not a repository file.".format(repo))
                continue

            repo_path = os.path.join(DEFAULT_YUM_REPOFILE_DIR, repo)
            restorable_file = RestorableFile(repo_path)
            backup.backup_control.push(restorable_file)


class BackupYumVariables(actions.Action):
    id = "BACKUP_YUM_VARIABLES"

    def run(self):
        """Backup varsdir folder in /etc/{yum,dnf}/vars so the variables can be restored on rollback."""
        logger.task("Backup variables")

        super(BackupYumVariables, self).run()

        logger.info("Backing up variables files from {}.".format(DEFAULT_YUM_VARS_DIR))
        self._backup_variables(path=DEFAULT_YUM_VARS_DIR)

        if system_info.version.major >= 8:
            logger.info("Backing up variables files from {}.".format(DEFAULT_DNF_VARS_DIR))
            self._backup_variables(path=DEFAULT_DNF_VARS_DIR)

    def _backup_variables(self, path):
        """Helper internal function to backup the variables.

        :param path: The path for the original variable.
        :type path: str
        """
        variable_files_backed_up = False

        for variable in os.listdir(path):
            variable_path = os.path.join(path, variable)
            restorable_file = RestorableFile(variable_path)
            backup.backup_control.push(restorable_file)
            variable_files_backed_up = True

        if not variable_files_backed_up:
            logger.info("No variables files backed up.")


class BackupPackageFiles(actions.Action):
    id = "BACKUP_PACKAGE_FILES"
    # BACKUP_PACKAGE_FILES should be the last one
    # Something could be backed up by this function
    # and if the MD5 differs it might be backed up for second time
    # by the BackupPackageFiles
    dependencies = ("BACKUP_REPOSITORY", "BACKUP_REDHAT_RELEASE")

    def run(self):
        """Backup changed package files"""
        super(BackupPackageFiles, self).run()

        logger.task("Backup package files")

        package_files_changes = self._get_changed_package_files()

        # Paths and files already backed up
        backed_up_files = [system_release_file.filepath, os_release_file.filepath, VERSIONLOCK_FILE_PATH]
        backed_up_paths = ["/etc/yum.repos.d", "/etc/yum/vars", "/etc/dnf/vars"]

        for file in package_files_changes:
            # Ghost files can be skipped since those files are generated during the package run, usually temporary.
            # We don't need to backup those type of files as was discussed under RHELC-1427
            if file["file_type"] == "g":
                continue

            if file["status"] == "missing":
                missing_file = MissingFile(file["path"])
                backup.backup_control.push(missing_file)
            elif "5" in file["status"]:
                # Check if the file is not already backed up or the path is not backed up
                if os.path.dirname(file["path"]) not in backed_up_paths and file["path"] not in backed_up_files:
                    # If the MD5 checksum differs, the content of the file differs
                    restorable_file = RestorableFile(file["path"])
                    backup.backup_control.push(restorable_file)
                else:
                    logger.debug(
                        "File {filepath} already backed up - not backing up again".format(filepath=file["path"])
                    )

    def _get_changed_package_files(self):
        """Get the output from rpm -Va command from during resolving system info
        to get changes made to package files.


        :return dict: Return them as a list of dict, for example:
        [{"status":"S5T", "file_type":"c", "path":"/etc/yum.repos.d/CentOS-Linux-AppStream.repo"}]
        """
        data = []
        path = os.path.join(LOG_DIR, PRE_RPM_VA_LOG_FILENAME)
        output = ""
        try:
            with open(path, "r") as f:
                output = f.read()
        # Catch the IOError due Python 2 compatibility
        except IOError as err:
            warn_deprecated_env("CONVERT2RHEL_INCOMPLETE_ROLLBACK")
            if tool_opts.incomplete_rollback:
                logger.debug(
                    "You have set the incomplete rollback inhibitor override - skipping backing up of the package"
                    " files."
                )
                # Return empty list results in no backup of the files
                return data
            else:
                # The file should be there
                # If missing conversion is in unknown state
                logger.warning("Error({}): {}".format(err.errno, err.strerror))
                logger.critical("Missing file {rpm_va_output} in it's location".format(rpm_va_output=path))

        lines = output.strip().split("\n")
        for line in lines:
            parsed_line = self._parse_line(line.strip())
            # We first check that it has a path and status, otherwise nothing to backup
            if parsed_line["path"] and parsed_line["status"]:
                data.append(parsed_line)

        return data

    def _parse_line(self, line):
        """Return {"status":"S5T", "file_type":"c", "path":"/etc/yum.repos.d/CentOS-Linux-AppStream.repo"}"""
        match = re.match(RPM_VA_REGEX, line)

        if not match:  # line not matching the regex
            if line.strip() != "":
                # Line is not empty string
                logger.debug("Skipping invalid output {}".format(line))
            return {"status": None, "file_type": None, "path": None}

        line = line.split()

        # Replace the . (success) and ? (test could not be performed) symbols
        status = line[0].replace(".", "").replace("?", "")

        if len(line) == 2:
            # File type undefined
            file_type = None
            path = line[1]

        else:
            file_type = line[1]
            path = line[2]

        return {"status": status, "file_type": file_type, "path": path}
