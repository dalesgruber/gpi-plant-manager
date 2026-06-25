import importlib
import sys
import warnings

from authlib.deprecate import AuthlibDeprecationWarning
from starlette.exceptions import StarletteDeprecationWarning


def test_testclient_import_has_no_starlette_deprecation_warning():
    sys.modules.pop("fastapi.testclient", None)
    sys.modules.pop("starlette.testclient", None)

    with warnings.catch_warnings():
        warnings.simplefilter("error", StarletteDeprecationWarning)
        importlib.import_module("fastapi.testclient")


def test_auth_import_has_no_authlib_jose_deprecation_warning():
    sys.modules.pop("zira_dashboard.auth", None)

    with warnings.catch_warnings():
        warnings.simplefilter("error", AuthlibDeprecationWarning)
        importlib.import_module("zira_dashboard.auth")
