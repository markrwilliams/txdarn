from twisted.trial import unittest

from txdarn import compat as C


class TestCompat(unittest.SynchronousTestCase):

    def test_asJSON(self):
        self.assertEqual(C.asJSON({'a': [1]}),
                         b'{"a": [1]}')

    def test_intToBytes(self):
        self.assertEqual(C.intToBytes(1024), b'1024')
