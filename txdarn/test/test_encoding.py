from twisted.trial import unittest
from twisted.web.test import requesthelper
from txdarn import encoding as E


class ContentTypeDecoratorTestCase(unittest.SynchronousTestCase):

    def setUp(self):
        self.request = requesthelper.DummyRequest('ignored')

    def test_withInvalidContentType(self):
        '''
        Setting an invalid content type at decoration time raises an
        exception
        '''

        with self.assertRaises(E.MalformedContentType):

            @E.contentType(b"application/json; charset=UTF-8")
            def neverCalled(request):
                return b'ok'

        self.assertFalse(self.request.outgoingHeaders)

    def test_withNoParams(self):
        '''
        A valid content type without params sets the header
        '''

        @E.contentType(b'text/html')
        def handler(request):
            return b'ok'

        handler(self.request)
        self.assertEqual(self.request.outgoingHeaders,
                         {b'content-type': b'text/html; charset=UTF-8'})

    def test_withParams(self):
        '''
        A valid content type with params sets the header
        '''

        @E.contentType(b'text/html', params=[(b'q', b'1')])
        def handler(request):
            return b'ok'

        handler(self.request)
        self.assertEqual(self.request.outgoingHeaders,
                         {b'content-type':
                          b'text/html; q=1 charset=UTF-8'})

    def test_decoratedMethod(self):
        '''
        A decorated method should work
        '''

        class FakeResource(object):

            @E.contentType(b'text/html', params=[(b'q', b'1')])
            def handler(self, request):
                return b'ok'

        FakeResource().handler(self.request)
        self.assertEqual(self.request.outgoingHeaders,
                         {b'content-type':
                          b'text/html; q=1 charset=UTF-8'})

    def test_emptyResponse(self):
        '''
        An empty response gets no Content-Type
        '''

        @E.contentType(b'text/html')
        def getsNoHeader(request):
            pass

        getsNoHeader(self.request)
        self.assertEqual(self.request.outgoingHeaders, {})
