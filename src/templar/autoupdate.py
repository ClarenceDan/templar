import os
import sys
import threading
import time
import git
import subprocess
from packaging import version

# Import local modules
from . import logger

# Import the local version
from .__init__ import __version__

TARGET_BRANCH = "main"

class AutoUpdate(threading.Thread):
    """
    Automatic update utility for templar neurons.
    """

    def __init__(self, interval_hours=4, process_name=None):
        super().__init__()
        self.interval_hours = interval_hours
        self.process_name = process_name
        self.daemon = True  # Ensure thread exits when main program exits
        try:
            self.repo = git.Repo(search_parent_directories=True)
        except Exception as e:
            logger.exception("Failed to initialize the repository", exc_info=e)

    def get_remote_version(self):
        """
        Fetch the remote version string from the src/templar/__init__.py file in the repository.
        """
        try:
            # Perform a git fetch to ensure we have the latest remote information
            self.repo.remotes.origin.fetch(kill_after_timeout=5)

            # Get version number from remote __init__.py
            init_blob = (
                self.repo.remote().refs[TARGET_BRANCH].commit.tree
                / "src"
                / "templar"
                / "__init__.py"
            )
            lines = init_blob.data_stream.read().decode("utf-8").split("\n")

            for line in lines:
                if line.startswith("__version__"):
                    version_info = line.split("=")[1].strip().strip(' "')
                    return version_info
        except Exception as e:
            logger.exception("Failed to get remote version for version check", exc_info=e)
            return None

    def check_version_updated(self):
        """
        Compares local and remote versions and returns True if the remote version is higher.
        """
        remote_version = self.get_remote_version()
        if not remote_version:
            logger.error("Failed to get remote version, skipping version check")
            return False

        # Reload the version from __init__.py to get the latest version after updates
        try:
            from importlib import reload
            from . import __init__
            reload(__init__)
            local_version = __init__.__version__
        except Exception as e:
            logger.error(f"Failed to reload local version: {e}")
            # Fallback to imported version
            local_version = __version__

        local_version_obj = version.parse(local_version)
        remote_version_obj = version.parse(remote_version)
        logger.info(
            f"Version check - remote_version: {remote_version}, local_version: {local_version}"
        )

        if remote_version_obj > local_version_obj:
            logger.info(
                f"Remote version ({remote_version}) is higher "
                f"than local version ({local_version}), automatically updating..."
            )
            return True
        return False

    def attempt_update(self):
        """
        Attempt to update the repository by pulling the latest changes from the remote repository.
        """
        try:
            origin = self.repo.remotes.origin

            if self.repo.is_dirty(untracked_files=False):
                logger.error(
                    "Current changeset is dirty. Please commit changes, discard changes, or update manually."
                )
                return False
            try:
                logger.debug("Attempting to pull the latest changes...")
                origin.pull(TARGET_BRANCH, kill_after_timeout=10, rebase=True)
                logger.debug("Successfully pulled the latest changes")
                return True
            except git.GitCommandError as e:
                logger.exception(
                    "Automatic update failed due to conflicts. Attempting to handle merge conflicts.",
                    exc_info=e,
                )
                return self.handle_merge_conflicts()
        except Exception as e:
            logger.exception(
                "Automatic update failed. Manually pull the latest changes and update.",
                exc_info=e,
            )

        return False

    def handle_merge_conflicts(self):
        """
        Attempt to automatically resolve any merge conflicts that may have arisen.
        """
        try:
            self.repo.git.reset("--merge")
            origin = self.repo.remotes.origin
            current_branch = self.repo.active_branch
            origin.pull(current_branch.name)

            for item in self.repo.index.diff(None):
                file_path = item.a_path
                logger.info(f"Resolving conflict in file: {file_path}")
                self.repo.git.checkout("--theirs", file_path)
            self.repo.index.commit("Resolved merge conflicts automatically")
            logger.info(
                "Merge conflicts resolved, repository updated to remote state."
            )
            logger.info("✅ Successfully updated")
            return True
        except git.GitCommandError as e:
            logger.exception(
                "Failed to resolve merge conflicts, automatic update cannot proceed. Please manually pull and update.",
                exc_info=e,
            )
            return False

    def attempt_package_update(self):
        """
        Synchronize dependencies using 'uv sync --extra all'.
        """
        logger.info("Attempting to update packages using 'uv sync --extra all'...")

        try:
            uv_executable = "uv"
            # Optionally, specify the path to 'uv' if it's not in PATH

            subprocess.check_call(
                [uv_executable, "sync", "--extra", "all"],
                timeout=300,
            )
            logger.info("Successfully updated packages using 'uv sync --extra all'.")
        except Exception as e:
            logger.exception("Failed to synchronize dependencies with uv", exc_info=e)

    def try_update(self):
        """
        Automatic update entrypoint method.
        """
        # if self.repo.head.is_detached or self.repo.active_branch.name != TARGET_BRANCH:
        #     logger.info("Not on the target branch, skipping auto-update")
        #     return

        if not self.check_version_updated():
            return

        if not self.attempt_update():
            return

        # Synchronize dependencies
        self.attempt_package_update()

        # Restart application
        self.restart_app()

    def restart_app(self):
        """Restarts the current application appropriately based on the runtime environment."""
        logger.info("Restarting application...")
        # Check for PM2 environment
        if "PM2_HOME" in os.environ:
            if not self.process_name:
                logger.error("PM2 environment detected but process_name not provided")
                sys.exit(1)
            # PM2 will restart the process if we exit
            logger.info(f"Detected PM2 environment. Restarting process: {self.process_name}")
            subprocess.check_call(["pm2", "restart", self.process_name])
            time.sleep(5)  # Give PM2 time to restart the process
            sys.exit(1)
        # TODO: Not tested
        # elif os.getenv("RUNNING_IN_DOCKER") == "true" or os.path.exists('/.dockerenv'):
        #     # In Docker, it's better to exit and let the orchestrator handle restarts
        #     logger.info("Detected Docker environment. Exiting for Docker to restart the container.")
        #     sys.exit(0)
        else:
            # Regular restart
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def run(self):
        """Thread run method to periodically check for updates."""
        while True:
            try:
                logger.info("Running autoupdate")
                self.try_update()
            except Exception as e:
                logger.exception("Exception during autoupdate check", exc_info=e)
            time.sleep(self.interval_hours * 3600)  # Sleep for specified hours