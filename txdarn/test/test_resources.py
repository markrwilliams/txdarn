from functools import partial
import re

import eliot.testing

from twisted.internet import defer
from twisted.trial import unittest
from twisted.web import http
from twisted.web import template
from twisted.web.resource import Resource
from twisted.web.test import requesthelper

from zope.interface import implementer
from zope.interface.verify import verifyObject

from txdarn import encoding
from txdarn import resources as R


SOCKJS_URL = u'http://someplace'
SOCKJS_URL_BYTES = SOCKJS_URL.encode(encoding.ENCODING)

STATIC_IFRAME = (u'''\
<html>
<head>
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <script src="%s"></script>
  <script>
    document.domain = document.domain;
    SockJS.bootstrap_iframe();
  </script>
</head>
<body>
  <h2>Don't panic!</h2>
  <p>This is a SockJS hidden iframe. It's used for cross domain magic.</p>
</body>
</html>''' % (SOCKJS_URL)).encode(encoding.ENCODING)


class ImmutableDictTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.key = 'foo'
        self.value = 2
        self.dictionary = R.ImmutableDict({self.key: self.value})

    def test_init_mapping(self):
        self.assertEqual(self.dictionary,
                         R.ImmutableDict({self.key: self.value}))

    def test_init_iterable(self):
        self.assertEqual(self.dictionary,
                         R.ImmutableDict([(self.key, self.value)]))

    def test_init_kwarg(self):
        self.assertEqual(self.dictionary,
                         R.ImmutableDict(**{self.key: self.value}))

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
        repr(R.ImmutableDict(a=1))


class PolicyTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.request = requesthelper.DummyRequest([])


class CachePolicyTestCase(PolicyTestCase):

    def test_interface(self):
        '''
        CachePolicy instances provide IHeaderPolicy
        '''
        self.assertTrue(verifyObject(R.IHeaderPolicy,
                                     R.CachePolicy(b'', None)))

    def test_cacheableApply(self):
        '''
        cacheDirectives and expiresOffset can assign headers to enable caching
        '''
        expectedHeaders = {}

        cacheDirectives = (R.PUBLIC, R.MAX_AGE(1234))
        expectedHeaders[b'cache-control'] = b'public, max-age=1234'

        expiresOffset = 1234
        fakeNow = lambda: 1234
        expectedHeaders[b'expires'] = b'Thu, 01 Jan 1970 00:41:08 GMT'

        policy = R.CachePolicy(cacheDirectives=cacheDirectives,
                               expiresOffset=expiresOffset)
        policy.apply(self.request, now=fakeNow)

        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)

    def test_uncacheableApply(self):
        '''
        cacheDirectives and expiresOffset can assign headers to disable caching
        '''
        cacheDirectives = (R.NO_STORE,
                           R.NO_CACHE,
                           R.MUST_REVALIDATE,
                           R.MAX_AGE(0))

        expectedHeaders = {b'cache-control':
                           b'no-store'
                           b', no-cache, must-revalidate, max-age=0'}

        policy = R.CachePolicy(cacheDirectives=cacheDirectives,
                               expiresOffset=None)

        fakeNow = lambda: 1234
        policy.apply(self.request, now=fakeNow)
        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)


class GETPOSTResource(object):
    allowedMethods = (b'GET', b'POST')


class AccessControlPolicyTestCase(PolicyTestCase):

    def setUp(self):
        super(AccessControlPolicyTestCase, self).setUp()
        self.dummyPolicy = R.AccessControlPolicy(methods=(),
                                                 maxAge=None)

    def test_interface(self):
        '''
        AccessControlPolicy instances provide IHeaderPolicy
        '''
        self.assertTrue(verifyObject(R.IHeaderPolicy, self.dummyPolicy))

    def test_allowOrigin(self):
        '''
        allowOrigin allows any specific domain given or returns *
        '''
        cases = [(None, b'*'),
                 (b'null', b'null'),
                 (b'test', b'test')]
        for value, expected in cases:
            actual = R.allowOrigin(self.dummyPolicy,
                                   self.request,
                                   value)
            self.assertEqual(actual, expected)

    def test_allowCredentials(self):
        '''
        allowCredentials returns true only if given an origin
        '''

        preparedAllowCredentials = partial(R.allowCredentials,
                                           self.dummyPolicy,
                                           self.request)

        self.assertIs(preparedAllowCredentials(b'*'), None)
        self.assertIs(preparedAllowCredentials(None), None)
        self.assertEqual(preparedAllowCredentials(b'test'), b'true')

    def test_allowHeaders(self):
        '''allowHeaders just passes headers through.'''

        preparedAllowHeaders = partial(R.allowHeaders,
                                       self.dummyPolicy,
                                       self.request)
        self.assertEqual(preparedAllowHeaders([]), [])
        self.assertEqual(preparedAllowHeaders([b'a']), [b'a'])

    def test_apply(self):
        expectedHeaders = {}

        methods = [b'GET', b'POST']
        expectedHeaders[b'access-control-allow-methods'] = b'GET, POST'

        maxAge = 1234
        expectedHeaders[b'access-control-max-age'] = b'1234'

        self.request.headers[b'origin'] = b'test'
        expectedHeaders[b'access-control-allow-origin'] = b'test'
        expectedHeaders[b'access-control-allow-credentials'] = b'true'

        self.request.headers[b'access-control-request-headers'] = b'a, b, c'
        expectedHeaders[b'access-control-allow-headers'] = b'a, b, c'

        policy = R.AccessControlPolicy(methods=methods,
                                       maxAge=maxAge)

        policy.apply(self.request)
        self.assertEqual(self.request.outgoingHeaders, expectedHeaders)

    def test_forResource_inference(self):
        infers = R.AccessControlPolicy(methods=R.INFERRED,
                                       maxAge=1234)
        inferred = infers.forResource(GETPOSTResource())
        self.assertEqual(inferred.methods, (b'GET', b'POST'))

    def test_forResource_noAllowedMethods(self):
        infers = R.AccessControlPolicy(methods=R.INFERRED,
                                       maxAge=1234)
        with self.assertRaises(ValueError):
            infers.forResource(Resource())

    def test_forResource_noInference(self):
        doesNotInfer = R.AccessControlPolicy(methods=(b'PATCH'),
                                             maxAge=1234)
        theSameInstance = doesNotInfer.forResource(GETPOSTResource())

        self.assertEqual(theSameInstance, doesNotInfer)


class RecordsPolicy(object):
    sawResource = False
    SAW_RESOURCE_HEADER = b'X-Saw-Resource'


@implementer(R.IHeaderPolicy)
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

        class TestResource(R.HeaderPolicyApplyingResource):
            allowedMethods = (b'GET',)
            policies = R.ImmutableDict({b'GET': (FakePolicy(self.recorder),)})

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
            R.HeaderPolicyApplyingResource(policies='blah')

    def test_init_failsWithoutAllowedMethods(self):
        with self.assertRaises(ValueError):
            R.HeaderPolicyApplyingResource(policies={b'GET': ()})

    def test_init_failsWithIncompletePolicy(self):
        with self.assertRaises(ValueError):
            self.TestResource(policies={b'POST': ()})


class GreetingTestCase(unittest.SynchronousTestCase):

    def test_get(self):
        request = requesthelper.DummyRequest([b'ignored'])
        request.method = b'GET'
        expected = b'Welcome to SockJS!\n'
        self.assertEqual(R.Greeting().render(request),
                         expected)


class IFrameElementTestCase(unittest.TestCase):

    def test_render(self):
        '''Rendering IFrameElement produces the HTML SockJS requires.'''
        renderDeferred = template.flattenString(
            None, R.IFrameElement(SOCKJS_URL_BYTES))

        renderDeferred.addCallback(self.assertEqual, STATIC_IFRAME)
        return renderDeferred


class IFrameResourceTestCase(unittest.SynchronousTestCase):

    def test_render(self):
        '''A request for the iframe resource produces the requisite HTML'''
        iframe = R.IFrameResource(SOCKJS_URL_BYTES)
        request = requesthelper.DummyRequest([b'ignored'])
        request.method = b'GET'

        iframeWithDOCTYPE = b'\n'.join([b'<!DOCTYPE html>', STATIC_IFRAME])
        self.assertEqual(iframe.render(request), iframeWithDOCTYPE)

    RenderingError = ValueError

    def test_templateError(self):
        '''An exception when rendering the iframe template becomes an
           RuntimeError and is logged.'''
        def badRender(*args, **kwargs):
            return defer.fail(self.RenderingError("no good :("))

        with self.assertRaises(RuntimeError):
            R.IFrameResource(SOCKJS_URL_BYTES, _render=badRender)

    def test_get_cached(self):
        iframe = R.IFrameResource(SOCKJS_URL_BYTES)

        request = requesthelper.DummyRequest([b'ignored'])
        request.method = b'GET'

        def setETag(request):
            return http.CACHED

        request.setETag = setETag

        request.requestHeaders.addRawHeader(b'if-none-match',
                                            iframe.etag)
        self.assertFalse(iframe.render(request))

    @eliot.testing.capture_logging(test_templateError,
                                   exceptionType=RenderingError)
    def assertFailureLogged(self, logger, exceptionType):
        messages = logger.flush_tracebacks(exceptionType)
        self.assertEqual(len(messages), 1)


class OptionsSubResourceTestCaseMixin:

    def test_options(self):
        request = requesthelper.DummyRequest([b'ignored'])
        request.method = b'OPTIONS'

        optionsResource = self.resourceClass()

        written = optionsResource.render(request)
        outgoingHeaders = request.outgoingHeaders

        cache_control = outgoingHeaders[b'cache-control']
        self.assertIn(b'public', cache_control)
        self.assertTrue(re.search(b'max-age=[1-9]\d{6}', cache_control),
                        'SockJS demands a "large" max-age')

        self.assertIn(b'expires', outgoingHeaders)
        self.assertGreater(int(outgoingHeaders[b'access-control-max-age']),
                           1000000)
        # TODO - test methods
        self.assertFalse(written)


class InfoResourceTestCase(OptionsSubResourceTestCaseMixin,
                           unittest.TestCase):
    resourceClass = R.InfoResource

    def fakeRandRange(self, minimum, maximum):
        self.assertEqual((minimum, maximum), self.resourceClass.entropyRange)
        self._entropyCalled = True
        return 42

    def fakeRender(self, data):
        return data

    def setUp(self):
        self._entropyCalled = False
        self._data = None
        self.config = {'websocketsEnabled': False,
                       'cookiesNeeded': True}
        self.info = self.resourceClass(_render=self.fakeRender,
                                       _random=self.fakeRandRange,
                                       **self.config)

    def test_calculateEntropy(self):
        self.assertEqual(self.info.calculateEntropy(), 42)
        self.assertTrue(self._entropyCalled)

    def test_get(self):
        request = requesthelper.DummyRequest([b'ignored'])
        request.method = b'GET'

        result = self.info.render(request)

        self.assertEqual({'websocket': self.config['websocketsEnabled'],
                          'cookie_needed': self.config['cookiesNeeded'],
                          'origins': ['*:*'],
                          'entropy': 42},
                         result)
