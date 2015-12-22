import six

from twisted.trial import unittest

from txdarn import compat as C


def skipIfVersion(test):
    def _decorator(method):
        if test:
            method.skip = 'wrong version of python'
        return method
    return _decorator


class TestCompat(unittest.SynchronousTestCase):

    def test_asJSON(self):
        self.assertEqual(C.asJSON({'a': [1]}),
                         b'{"a": [1]}')

    def test_intToBytes(self):
        self.assertEqual(C.intToBytes(1024), b'1024')

    def test_networkString(self):
        self.assertEqual(C.networkString('native string to bytes'),
                         b'native string to bytes')

    @skipIfVersion(not six.PY3)
    def test_networkStringFails_py3(self):
        with self.assertRaises(TypeError):
            C.networkString(b"i fail because i'm bytes")

    @skipIfVersion(six.PY3)
    def test_networkStringFails_py2(self):
        with self.assertRaises(TypeError):
            C.networkString(u"i fail because i'm unicode")

        with self.assertRaises(UnicodeDecodeError):
            C.networkString(b"\xff fails because it's not ascii")

    def test_stringFromNetwork(self):
        self.assertEqual(C.stringFromNetwork(b'bytes to native string'),
                         'bytes to native string')

    @skipIfVersion(not six.PY3)
    def test_stringFromNetworkFails_py3(self):
        with self.assertRaises(TypeError):
            C.stringFromNetwork("i fail because i'm a native string")

    @skipIfVersion(six.PY3)
    def test_stringFromNetworkFails_py2(self):
        with self.assertRaises(TypeError):
            C.stringFromNetwork(u"i fail because i'm unicode")

        with self.assertRaises(UnicodeDecodeError):
            C.stringFromNetwork("\xff fails because it's not ascii")
