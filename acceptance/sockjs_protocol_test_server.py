from twisted.internet import reactor, task
from twisted.internet.protocol import Factory, Protocol

from txdarn.application import TxDarn

import klein


class EchoProtocol(Protocol):

    def dataReceived(self, data):
        self.transport.write(data)


class CloseProtocol(Protocol):

    def connectionMade(self):
        self.transport.loseConnection()


class AmplifyProtocol(Protocol):

    def _parseNumber(self, length):
        length = int(length)
        return length if 0 < length < 19 else 1

    def dataReceived(self, data):
        response = ["x" * 2 ** self._parseNumber(datum) for datum in data]
        self.transport.write(response)

options = {
    'sockjs-url': 'https://cdnjs.cloudflare.com/ajax/libs/sockjs-client/1.0.3/sockjs.js',
    'websockets': True,
    'timeout': 5.0,
    'maximumBytes': 4096}
darnit = TxDarn(Factory.forProtocol(EchoProtocol), options)
darnitClose = TxDarn(Factory.forProtocol(CloseProtocol), options)

noWS = options.copy()
noWS['websockets'] = False
darnitNoWS = TxDarn(Factory.forProtocol(EchoProtocol), noWS)

darnitAmplify = TxDarn(Factory.forProtocol(AmplifyProtocol), options)

app = klein.Klein()


@app.route('/echo/', strict_slashes=False, branch=True)
def root(request):
    return darnit.app.resource()


@app.route('/close/', strict_slashes=False, branch=True)
def close(request):
    return darnitClose.app.resource()


@app.route('/disabled_websocket_echo/', strict_slashes=False, branch=True)
def noWS(request):
    return darnitNoWS.app.resource()


@app.route('/amplify', strict_slashes=False, branch=True)
def amplify(request):
    return darnitAmplify.app.resource()


@app.route('/streaming.txt', strict_slashes=False, branch=True)
def streaming_txt(request):
    request.setHeader(b'Content-Type', b'text/plain')
    request.setHeader(b'Access-Control-Allow-Origin', b'*')
    request.write(b'a' * 2048 + b'\n')

    def done():
        request.write(b'b\n')

    return task.deferLater(reactor, .25, done)


app.run('localhost', 8081)
