import datetime
import decimal
import ipaddress
import json
import types
from functools import partial
from typing import Any

from .exceptions import JSONHTTPError
from .request import Context


# JSON serialization tuning
def fix_json(obj: Any) -> Any:
    from .. import models

    if isinstance(obj, decimal.Decimal):
        value = format(obj, "f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        return "0" if value == "-0" else value
    # ToDo
    # should we install python-dateutils
    # Or just use this hack ?
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    if isinstance(obj, memoryview):
        return bytes(obj)
    if isinstance(obj, types.MethodType):
        return obj()
    if isinstance(obj, models.Manager):
        return obj.data
    if isinstance(obj, ipaddress.IPv4Address):
        return str(obj)
    # raise an error if its not dict
    if isinstance(obj, dict) is False:
        raise TypeError("unknown type: ", type(obj), obj)


fixed_dump = partial(json.dumps, indent=None, default=fix_json)

__all__ = ("Context", "JSONHTTPError", "fixed_dump")
