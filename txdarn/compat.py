import six
import json
from .encoding import ENCODING


if six.PY3:
    def asJSON(obj):
        return json.dumps(obj).encode(ENCODING)
else:
    def asJSON(obj):
        return json.dumps(obj)


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
