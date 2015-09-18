from twisted.web import resource, server, template
from twisted.trial import unittest
from twisted.web.test import requesthelper
from txdarn import static as S, encoding


SOCKJS_URL = u'http://someplace'
SOCKJS_URL_BYTES = SOCKJS_URL.encode(encoding.ENCODING)

STATIC_IFRAME = (u'''\
<html>
<head>
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <script>
    document.domain = document.domain;
    _sockjs_onload = function(){SockJS.bootstrap_iframe();};
  </script>
  <script src="%s"></script>
</head>
<body>
  <h2>Don't panic!</h2>
  <p>This is a SockJS hidden iframe. It's used for cross domain magic.</p>
</body>
</html>''' % (SOCKJS_URL)).encode(encoding.ENCODING)


class SimpleResource(resource.Resource):
    isLeaf = True


class SlashIgnoringResourceTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.resource = S.SlashIgnoringResource()
        self.path = b'simple'
        self.childResource = SimpleResource()
        self.resource.putChild(b'simple', self.childResource)

    def test_getChild_withoutSlash(self):
        request = requesthelper.DummyRequest([])
        request.prepath = []
        self.assertIdentical(self.resource.getChild(self.path, request),
                             self.childResource)

    def test_getChild_withSlash(self):
        request = requesthelper.DummyRequest([])
        request.prepath = [self.path]
        self.assertIdentical(self.resource.getChild(b'', request),
                             self.childResource)


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


class IFrameResourceTestCase(unittest.TestCase):

    def test_render(self):
        resource = S.IFrameResource(SOCKJS_URL_BYTES)
        request = requesthelper.DummyRequest(['ignored'])
        request.method = b'GET'

        requestDeferred = request.notifyFinish()

        def assertPageContent(_):
            content = b''.join(request.written)
            iframeWithDOCTYPE = b'\n'.join([b'<!DOCTYPE html>', STATIC_IFRAME])
            self.assertEqual(content, iframeWithDOCTYPE)

        requestDeferred.addCallback(assertPageContent)

        self.assertEqual(resource.render(request),
                         server.NOT_DONE_YET)

        return requestDeferred
