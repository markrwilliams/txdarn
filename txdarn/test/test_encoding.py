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
                pass

        self.assertFalse(self.request.outgoingHeaders)

    def test_withNoParams(self):
        '''
        A valid content type without params sets the header
        '''

        @E.contentType(b'text/html')
        def assertHeaderSet(request):
            self.assertEqual(request.outgoingHeaders,
                             {b'content-type': b'text/html; charset=utf-8'})

        assertHeaderSet(self.request)

    def test_withParams(self):
        '''
        A valid content type with params sets the header
        '''

        @E.contentType(b'text/html', params=[(b'q', b'1')])
        def assertHeaderSet(request):
            self.assertEqual(request.outgoingHeaders,
                             {b'content-type':
                              b'text/html; q=1 charset=utf-8'})

        assertHeaderSet(self.request)

    def test_decoratedMethod(self):
        assertEqual = self.assertEqual

        class FakeResource(object):

            @E.contentType(b'text/html', params=[(b'q', b'1')])
            def assertHeaderSet(self, request):
                assertEqual(request.outgoingHeaders,
                            {b'content-type':
                             b'text/html; q=1 charset=utf-8'})

        FakeResource().assertHeaderSet(self.request)
