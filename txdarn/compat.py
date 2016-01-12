import sys
import six
import json
from .encoding import ENCODING

if not (2 <= sys.version_info.major <= 3):
    raise RuntimeError("Unknown Python version!")


if six.PY3:
    def asJSON(*args, **kwargs):
        return json.dumps(*args, **kwargs).encode(ENCODING)

    def fromJSON(s, *args, **kwargs):
        return json.loads(s.decode(ENCODING), *args, **kwargs)
else:
    def asJSON(*args, **kwargs):
        return json.dumps(*args, **kwargs)

    def fromJSON(*args, **kwargs):
        return json.loads(*args, **kwargs)

if six.PY3:
    # shamelessly lifted from t.p.compat
    def intToBytes(integer):
        return str(integer).encode('ascii')

    def networkString(s):
        if not isinstance(s, str):
            raise TypeError("Can only convert text to bytes on Python 3")
        return s.encode('ascii')

    def stringFromNetwork(s):
        if not isinstance(s, bytes):
            raise TypeError("Can only convert bytes to text on Python 3")
        return s.decode('ascii')

else:
    def intToBytes(integer):
        return bytes(integer)

    def networkString(s):
        if not isinstance(s, str):
            raise TypeError("Can only pass-through bytes on Python 2")
        # Ensure we're limited to ASCII subset:
        s.decode('ascii')
        return s

    def stringFromNetwork(s):
        if not isinstance(s, str):
            raise TypeError("Can only pass-through bytes on Python 2")
        # Ensure we're limited to ASCII subset:
        s.decode('ascii')
        return s

if six.PY3:
    from collections.abc import Mapping
else:
    from collections import Mapping

if six.PY3:
    # Python 3 thinks header values are latin-1 encoded.  They're not
    # any more, and anyway we prefer to think of them in terms of
    # pure-ASCII bytes.  Wrap its list parser in our network string
    # massaging.
    from urllib.request import parse_http_list as _parse_http_list

    def parse_http_list(byteString):
        return [networkString(element) for element in
                _parse_http_list(stringFromNetwork(byteString))]
else:
    from urllib2 import parse_http_list
