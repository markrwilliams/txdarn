import functools
from .exceptions import TxDarnException

# sockjs seems to mandate utf-8
ENCODING = 'UTF-8'
ENCODING_BYTES = ENCODING.encode('ascii')

EMPTY = b''


class MalformedContentType(TxDarnException):
    '''
    Raised when UnicodeResource.setContentType encounters a malformed
    content type
    '''


class _contentTypeDecorator(object):
    '''
    Decorates a render_* method (or any other callable that takes a
    twisted.web.http Request as its first argument), setting the
    content type to formattedContentType

    '''

    def __init__(self, callableObject, formattedContentType):
        self.callableObject = callableObject
        self.formattedContentType = formattedContentType

    def __get__(self, obj, type_=None):
        return self.__class__(self.callableObject.__get__(obj, type_),
                              self.formattedContentType)

    def __call__(self, request, *args, **kwargs):
        result = self.callableObject(request, *args, **kwargs)
        if result or result is EMPTY:
            request.setHeader(b'Content-Type', self.formattedContentType)
        return result


def contentType(contentType, params=()):
    if contentType.count(b';'):
        raise MalformedContentType(
            'contentType must not contain parameters.')

    parameterPairs = [b'='.join([k, v]) for k, v in params]
    parameterPairs.append(b'charset=' + ENCODING_BYTES)

    formattedParameters = b' '.join(parameterPairs)
    contentType = b'; '.join([contentType, formattedParameters])

    return functools.partial(_contentTypeDecorator,
                             formattedContentType=contentType)
