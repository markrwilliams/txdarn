import eliot.testing
from twisted.internet import defer
from twisted.web import template
from twisted.trial import unittest
from twisted.web.test import requesthelper

from txdarn import encoding
from txdarn.resources import support as S


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


class GreetingTestCase(unittest.SynchronousTestCase):

    def test_get(self):
        request = requesthelper.DummyRequest(['ignored'])
        request.method = b'GET'
        expected = b'Welcome to SockJS!\n'
        self.assertEqual(S.Greeting().render(request),
                         expected)


class IFrameElementTestCase(unittest.TestCase):

    def test_render(self):
        '''Rendering IFrameElement produces the HTML SockJS requires.'''
        renderDeferred = template.flattenString(
            None, S.IFrameElement(SOCKJS_URL_BYTES))

        renderDeferred.addCallback(self.assertEqual, STATIC_IFRAME)
        return renderDeferred


class IFrameResourceTestCase(unittest.SynchronousTestCase):

    def test_render(self):
        '''A request for the iframe resource produces the requisite HTML'''
        iframe = S.IFrameResource(SOCKJS_URL_BYTES)
        request = requesthelper.DummyRequest(['ignored'])
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
            S.IFrameResource(SOCKJS_URL_BYTES, _render=badRender)

    @eliot.testing.capture_logging(test_templateError,
                                   exceptionType=RenderingError)
    def assertFailureLogged(self, logger, exceptionType):
        messages = logger.flush_tracebacks(exceptionType)
        self.assertEqual(len(messages), 1)
