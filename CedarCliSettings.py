import os
import platform
import subprocess
from rich.console import Console

console = Console()

class CedarCliSettings(object):
    do_fail_on_error = True
    shell_path = '/bin/bash'
    _sed_replace_in_place = None
    _use_osa = None

    @staticmethod
    def _test_sed_in_place_command():
        """
        Tests which sed in-place command to use depending on the operating system.
        Tries with an empty string for macOS/BSD-style sed and without it for GNU sed.
        """
        test_file = f"{os.getenv('CEDAR_HOME')}/cedar-cli/assets/utilities/sed-test.txt"
        with open(test_file, 'w') as f:
            f.write("test")
        try:
            # Try the BSD/macOS version first
            subprocess.check_call(["sed", "-i", "", "s/test/X/", test_file], stderr=subprocess.DEVNULL)
            return "sed -i ''"
        except subprocess.CalledProcessError:
            # Fallback to GNU/Linux version
            return "sed -i"
        except FileNotFoundError:
            console.print("[bold red]Error:[/bold red] sed command not found.")
            exit(1)
        finally:
            os.remove(test_file)

    @classmethod
    def get_sed_replace_in_place(cls):
        if cls._sed_replace_in_place is None:
            cls._sed_replace_in_place = cls._test_sed_in_place_command()
        return cls._sed_replace_in_place

    @classmethod
    def get_use_osa(cls):
        if cls._use_osa is None:
            cls._use_osa = platform.system() == 'Darwin'  # True if macOS, False otherwise
        return cls._use_osa
