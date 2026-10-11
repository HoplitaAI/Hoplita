
import os
import platform
import subprocess

IS_WINDOWS = platform.system() == "Windows"


def _module_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _default_base():
    if IS_WINDOWS:
        appdata = os.getenv("APPDATA", "")
        if appdata:
            return os.path.join(appdata, "hoplita")
        return os.path.join(os.path.expanduser("~"), "hoplita")
    home = os.path.join(os.path.expanduser("~"), "hoplita")
    if not os.path.isdir(os.path.join(home, "lang")) and \
            os.path.isdir(os.path.join("/opt/hoplita", "lang")):
        return "/opt/hoplita"
    return home


def hoplita_base():
    env_home = os.getenv("HOPLITA_HOME")
    if env_home:
        return os.path.abspath(os.path.expanduser(env_home))

    here = _module_dir()
    if os.path.isdir(os.path.join(here, "lang")):
        return here
    parent = os.path.abspath(os.path.join(here, ".."))
    if os.path.isdir(os.path.join(parent, "lang")):
        return parent

    return _default_base()


def config_path():
    return os.path.join(hoplita_base(), "config.ini")


def lang_dir():
    return os.path.join(hoplita_base(), "lang")


def icon_path():
    return os.path.join(hoplita_base(), "hoplita.png")


def no_console():
    if platform.system() != "Windows":
        return {}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flag} if flag else {}
