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

from convert2rhel import actions, pkghandler, repo, utils
from convert2rhel.backup import backup_control, get_backedup_system_repos
from convert2rhel.backup.packages import RestorablePackage
from convert2rhel.logger import root_logger
from convert2rhel.repo import DEFAULT_YUM_REPOFILE_DIR
from convert2rhel.systeminfo import system_info


logger = root_logger.getChild(__name__)


class ListThirdPartyPackages(actions.Action):
    id = "LIST_THIRD_PARTY_PACKAGES"

    def run(self):
        """
        List packages not packaged by the original OS vendor or Red Hat and
        warn that these are not going to be converted.
        """
        super(ListThirdPartyPackages, self).run()

        logger.task("List third-party packages")
        third_party_pkgs = pkghandler.get_third_party_pkgs()
        if third_party_pkgs:
            # RHELC-884 disable the RHEL repos to avoid reaching them when checking original system.
            # There is needed for avoid reaching out RHEL repositories while requesting info about pkgs.
            repos_to_disable = repo.DisableReposDuringAnalysis().get_rhel_repos_to_disable()
            pkg_list = pkghandler.format_pkg_info(
                sorted(third_party_pkgs, key=self.extract_packages), disable_repos=repos_to_disable
            )
            warning_message = (
                "Only packages signed by {} are to be"
                " replaced. Red Hat support won't be provided"
                " for the following third party packages:\n".format(system_info.name)
            )

            logger.warning(warning_message)
            logger.info(pkg_list)
            self.add_message(
                level="WARNING",
                id="THIRD_PARTY_PACKAGE_DETECTED",
                title="Third party packages detected",
                description="Third party packages will not be replaced during the conversion.",
                diagnosis=warning_message + ", ".join(pkghandler.get_pkg_nevras(third_party_pkgs)),
            )
        else:
            logger.info("No third party packages installed.")

    def extract_packages(self, pkg):
        """Key function to extract the package name from third_party_pkgs"""
        return pkg.nevra.name


class RemoveSpecialPackages(actions.Action):
    id = "REMOVE_SPECIAL_PACKAGES"
    dependencies = (
        # We use the backed up repos in remove_pkgs_unless_from_redhat()
        "BACKUP_REPOSITORY",
        "BACKUP_PACKAGE_FILES",
        "BACKUP_REDHAT_RELEASE",
    )

    def run(self):
        """Remove a set of special packages from the system.

        The packages marked for exclusion here are the excluded_pkgs and
        repofile_pkgs that comes from the system_info singleton. This class
        substitute the old RemoveExcludedPackages and RemoveRepofilePackages as
        both of them depends on the RestorablePackage class, in which case, the
        RestorablePackage was designed to handle a set of packages in the
        moment of instantiation of the class, but since both removal classes
        executed separately, we couldn't properly reinstall some packages that
        got excluded and backed up, as they had to be reinstalled in the system
        in the same RPM transaction call. To not redo how the RestorablePackage
        works, both RemoveExcludedPackages and RemoveRepofilePackages classes
        got merged together into this one, making possible to remove and back
        up all the packages in a single transaction.
        """
        super(RemoveSpecialPackages, self).run()

        all_pkgs = []
        pkgs_removed = []
        try:
            logger.task("Searching for the following excluded packages")
            excluded_pkgs = sorted(pkghandler.get_packages_to_remove(system_info.excluded_pkgs))

            logger.task("Searching for packages containing .repo files or affecting variables in the .repo files")
            repofile_pkgs = sorted(pkghandler.get_packages_to_remove(system_info.repofile_pkgs))

            logger.info("\n")

            all_pkgs = excluded_pkgs + repofile_pkgs
            if not all_pkgs:
                logger.info("No packages to backup and remove.")
                return

            # We're using the backed up yum repositories to prevent the following:
            # - the system was registered to RHSM prior to the conversion and the system didn't have the redhat.repo generated
            #   for the lack of the RHSM product certificate
            # - at this point convert2rhel has installed the RHSM product cert (e.g. /etc/pki/product-default/69.pem)
            # - this function might be performing the first yum call convert2rhel does after cleaning yum metadata
            # - the "subscription-manager" yum plugin spots that there's a new RHSM product cert and generates
            #   /etc/yum.repos.d/redhat.repo
            # - the suddenly enabled RHEL repos cause a package backup failure
            # Since the MD5 checksum of original path is used in backup path to avoid
            # conflicts in backup folder, preparing the path is needed.
            backedup_reposdir = get_backedup_system_repos()
            backup_control.push(RestorablePackage(pkgs=pkghandler.get_pkg_nevras(all_pkgs), reposdir=backedup_reposdir))

            logger.info("\nRemoving special packages from the system.")

            # RHELC-884 disable the RHEL repos to avoid reaching them when checking original system.
            # There is needed for avoid reaching out RHEL repositories while requesting info about pkgs.
            repos_to_disable = repo.DisableReposDuringAnalysis().get_rhel_repos_to_disable()
            pkgs_removed = _remove_packages_unless_from_redhat(pkgs_list=all_pkgs, disable_repos=repos_to_disable)

            # https://issues.redhat.com/browse/RHELC-1677
            # In some cases the {system}-release package takes ownership of the /etc/yum.repos.d/ directory,
            # when the package gets forcefully removed, the directory gets removed as well. Subscription-manager
            # doesn't expect this and without the directory the redhat.repo isn't re-created. This results in an inability
            # to access any repositories as the repository directory doesn't exist.
            _fix_repos_directory()
        except SystemExit as e:
            # TODO(r0x0d): Places where we raise SystemExit and need to be
            # changed to something more specific.
            #   - When we can't remove a package.
            self.set_result(
                level="ERROR",
                id="SPECIAL_PACKAGE_REMOVAL_FAILED",
                title="Failed to remove some packages necessary for the conversion.",
                description="The cause of this error is unknown, please look at the diagnosis for more information.",
                diagnosis=str(e),
            )
            return

        # shows which packages were not removed, if false, all packages were removed
        pkgs_not_removed = sorted(frozenset(pkghandler.get_pkg_nevras(all_pkgs)).difference(pkgs_removed))
        if pkgs_not_removed:
            message = "The following packages cannot be removed: {}".format(", ".join(pkgs_not_removed))
            logger.warning(message)
            self.add_message(
                level="WARNING",
                id="SPECIAL_PACKAGES_NOT_REMOVED",
                title="Some packages cannot be removed",
                diagnosis=message,
                description=(
                    "The packages in diagnosis match a pre-defined list of packages that are to be removed during the"
                    " conversion. This list includes packages that are known to cause a conversion failure."
                ),
                remediations=(
                    "Remove the packages manually before running convert2rhel again:\n" "yum remove -y {}".format(
                        " ".join(pkgs_not_removed)
                    )
                ),
            )

        if pkgs_removed:
            message = "The following packages will be removed during the conversion: {}".format(", ".join(pkgs_removed))
            logger.info(message)
            self.add_message(
                level="INFO",
                id="SPECIAL_PACKAGES_REMOVED",
                title="Packages to be removed",
                description=message,
                diagnosis=(
                    "We have identified installed packages that match a pre-defined list of packages that are"
                    " known to cause a conversion failure."
                ),
                remediations="Check that the system runs correctly without the packages after the conversion.",
            )


def _remove_packages_unless_from_redhat(pkgs_list, disable_repos=None):
    """Remove packages from the system that are not RHEL.

    :param pkgs_list list[str]: Packages that will be removed.
    :param disable_repos: List of repo IDs to be disabled when retrieving repository information from packages.
    :type disable_repos: List[str]
    :return list[str]: Packages removed from the system.
    """
    if not pkgs_list:
        logger.info("\nNothing to do.")
        return []

    # this call can return None, which is not ideal to use with sorted.
    logger.warning("Removing the following {} packages:\n".format(len(pkgs_list)))
    pkghandler.print_pkg_info(pkgs_list, disable_repos)

    pkgs_removed = utils.remove_pkgs(pkghandler.get_pkg_nevras(pkgs_list))
    logger.debug("Successfully removed {} packages".format(len(pkgs_list)))

    return pkgs_removed


def _fix_repos_directory():
    """Check if repository directory is present. If the directory is missing, create it."""
    repo_dir = DEFAULT_YUM_REPOFILE_DIR
    if not os.path.exists(repo_dir):
        os.mkdir(repo_dir)
        logger.debug("Recreated repository directory {} as it was removed with some special package.".format(repo_dir))
