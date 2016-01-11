from twisted.internet.protocol import Factory, Protocol

from txdarn.application import TxDarn

import klein


class EchoProtocol(Protocol):

    def dataReceived(self, data):
        self.transport.write(data)


class CloseProtocol(Protocol):

    def connectionMade(self):
        self.transport.loseConnection()

options = {
    'sockjs-url': 'https://cdnjs.cloudflare.com/ajax/libs/sockjs-client/1.0.3/sockjs.js',
    'websockets': True,
    'timeout': 5.0}
darnit = TxDarn(Factory.forProtocol(EchoProtocol), options)
darnitClose = TxDarn(Factory.forProtocol(CloseProtocol), options)

noWS = options.copy()
noWS['websockets'] = False
darnitNoWS = TxDarn(Factory.forProtocol(EchoProtocol), noWS)

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


app.run('localhost', 8081)
