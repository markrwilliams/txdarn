from functools import partial

from twisted.trial import unittest
from twisted.web.resource import Resource
from twisted.web.test import requesthelper

from zope.interface import implementer
from zope.interface.verify import verifyObject

from .. import headers as H


class ImmutableDictTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.key = 'foo'
        self.value = 2
        self.dictionary = H.ImmutableDict({self.key: self.value})

    def test_init_mapping(self):
        self.assertEqual(self.dictionary,
                         H.ImmutableDict({self.key: self.value}))

    def test_init_iterable(self):
        self.assertEqual(self.dictionary,
                         H.ImmutableDict([(self.key, self.value)]))

    def test_init_kwarg(self):
        self.assertEqual(self.dictionary,
                         H.ImmutableDict(**{self.key: self.value}))

    def test_getitem(self):
        self.assertIs(self.dictionary[self.key], self.value)

        with self.assertRaises(KeyError):
            self.dictionary['c']

    def test_iter(self):
        self.assertEqual([self.key],
                         list(iter(self.dictionary)))

    def test_len(self):
        self.assertEqual(1, len(self.dictionary))

    def test_repr(self):
        repr(H.ImmutableDict(a=1))


class PolicyTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.request = requesthelper.DummyRequest([])


class CachePolicyTestCase(PolicyTestCase):

    def test_interface(self):
        '''
        CachePolicy instances provide IHeaderPolicy
        '''
        self.assertTrue(verifyObject(H.IHeaderPolicy,
                                     H.CachePolicy(b'', None)))

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
                           H.NO_CACHE,
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


class GETPOSTResource(object):
    allowedMethods = ('GET', 'POST')


class AccessControlPolicyTestCase(PolicyTestCase):

    def setUp(self):
        super(AccessControlPolicyTestCase, self).setUp()
        self.dummyPolicy = H.AccessControlPolicy(methods=(),
                                                 maxAge=None)

    def test_interface(self):
        '''
        AccessControlPolicy instances provide IHeaderPolicy
        '''
        self.assertTrue(verifyObject(H.IHeaderPolicy, self.dummyPolicy))

    def test_allowOrigin(self):
        '''
        allowOrigin allows any specific domain given or returns *
        '''
        cases = [(None, b'*'),
                 (b'null', b'null'),
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
        expectedHeaders[b'access-control-allow-origin'] = b'test'
        expectedHeaders[b'access-control-allow-credentials'] = b'true'

        policy = H.AccessControlPolicy(methods=methods,
                                       maxAge=maxAge)

        policy.apply(self.request)
        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)

    def test_forResource_inference(self):
        infers = H.AccessControlPolicy(methods=H.INFERRED,
                                       maxAge=1234)
        inferred = infers.forResource(GETPOSTResource())
        self.assertEqual(inferred.methods, (b'GET', b'POST'))

    def test_forResource_noAllowedMethods(self):
        infers = H.AccessControlPolicy(methods=H.INFERRED,
                                       maxAge=1234)
        with self.assertRaises(ValueError):
            infers.forResource(Resource())

    def test_forResource_noInference(self):
        doesNotInfer = H.AccessControlPolicy(methods=(b'PATCH'),
                                             maxAge=1234)
        theSameInstance = doesNotInfer.forResource(GETPOSTResource())

        self.assertEqual(theSameInstance, doesNotInfer)


class RecordsPolicy(object):
    sawResource = False
    SAW_RESOURCE_HEADER = b'X-Saw-Resource'


@implementer(H.IHeaderPolicy)
class FakePolicy(object):

    def __init__(self, recorder):
        self._recorder = recorder

    def forResource(self, resource):
        self._recorder.sawResource = True
        return self

    def apply(self, request):
        if self._recorder.sawResource:
            request.setHeader(self._recorder.SAW_RESOURCE_HEADER, b'1')
        return request


class HeaderPolicyApplyingResourceTestCase(PolicyTestCase):

    def setUp(self):
        super(HeaderPolicyApplyingResourceTestCase, self).setUp()
        self.recorder = RecordsPolicy()

        class TestResource(H.HeaderPolicyApplyingResource):
            allowedMethods = ('GET',)
            policies = H.ImmutableDict({b'GET': (FakePolicy(self.recorder),)})

        self.TestResource = TestResource

    def test_applyPolicies(self):
        resource = self.TestResource()
        resource.applyPolicies(self.request)

        headerSet = self.request.outgoingHeaders.get(
            self.recorder.SAW_RESOURCE_HEADER.lower())

        self.assertTrue(headerSet)

    def test_applyPolicies_overridenDefaults(self):
        resource = self.TestResource(policies={b'GET': ()})
        resource.applyPolicies(self.request)

        headerSet = self.request.outgoingHeaders.get(
            self.recorder.SAW_RESOURCE_HEADER.lower())

        self.assertFalse(headerSet)

    def test_init_failsWithInvalidPolicy(self):
        with self.assertRaises(ValueError):
            H.HeaderPolicyApplyingResource(policies='blah')

    def test_init_failsWithoutAllowedMethods(self):
        with self.assertRaises(ValueError):
            H.HeaderPolicyApplyingResource(policies={b'GET': ()})

    def test_init_failsWithIncompletePolicy(self):
        with self.assertRaises(ValueError):
            self.TestResource(policies={b'POST': ()})
