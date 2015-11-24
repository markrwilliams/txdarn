from functools import partial

from twisted.trial import unittest
from twisted.web.test import requesthelper
from .. import headers as H


class PolicyTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.request = requesthelper.DummyRequest([])


class CachePolicyTestCase(PolicyTestCase):

    def test_cacheableApply(self):
        '''
        cacheDirectives and expiresOffset can assign headers to enable caching
        '''
        expectedHeaders = {}

        cacheDirectives = (H.PUBLIC, H.MAX_AGE(1234))
        expectedHeaders[b'cache-control'] = b'public, max-age=1234'

        expiresOffset = 1234
        fakeNow = lambda: 1234
        expectedHeaders[b'expires'] = b'Thu, 01 Jan 1970 00:41:08 GMT'

        policy = H.CachePolicy(cacheDirectives=cacheDirectives,
                               expiresOffset=expiresOffset)
        policy.apply(self.request, now=fakeNow)

        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)

    def test_uncacheableApply(self):
        '''
        cacheDirectives and expiresOffset can assign headers to disable caching
        '''
        cacheDirectives = (H.NO_STORE,
                           H.NO_CACHE(),
                           H.MUST_REVALIDATE,
                           H.MAX_AGE(0))

        expectedHeaders = {b'cache-control':
                           b'no-store'
                           b', no-cache, must-revalidate, max-age=0'}

        policy = H.CachePolicy(cacheDirectives=cacheDirectives,
                               expiresOffset=None)

        fakeNow = lambda: 1234
        policy.apply(self.request, now=fakeNow)
        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)


class AccessControlPolicyTestCase(PolicyTestCase):

    def setUp(self):
        super(AccessControlPolicyTestCase, self).setUp()
        self.dummyPolicy = H.AccessControlPolicy(methods=(),
                                                 maxAge=None)

    def test_allowOrigin(self):
        '''
        allowOrigin allows any specific domain given or returns *
        '''
        cases = [(b'null', b'*'),
                 (None, b'*'),
                 (b'test', b'test')]
        for value, expected in cases:
            actual = H.allowOrigin(self.dummyPolicy,
                                   self.request,
                                   value)
            self.assertEqual(actual, expected)

    def test_allowCredentials(self):
        '''
        allowCredentials returns true only if given an origin
        '''

        preparedAllowCredentials = partial(H.allowCredentials,
                                           self.dummyPolicy,
                                           self.request)

        self.assertIs(preparedAllowCredentials(b'*'), None)
        self.assertIs(preparedAllowCredentials(None), None)
        self.assertEqual(preparedAllowCredentials(b'test'), b'true')

    def test_apply(self):
        expectedHeaders = {}

        methods = [b'GET', b'POST']
        expectedHeaders[b'access-control-allow-methods'] = b'GET, POST'

        maxAge = 1234
        expectedHeaders[b'access-control-max-age'] = b'1234'

        self.request.headers[b'origin'] = b'test'
        expectedHeaders[b'access-control-allowed-origin'] = b'test'
        expectedHeaders[b'access-control-allow-credentials'] = b'true'

        policy = H.AccessControlPolicy(methods=methods,
                                       maxAge=maxAge)

        policy.apply(self.request)
        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)
