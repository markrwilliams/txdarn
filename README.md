# txdarn

### SockJS for modern Twisted

#### What is it?
`txdarn` is a [SockJS](http://sockjs.org) [3.3](https://sockjs.github.io/sockjs-protocol/sockjs-protocol-0.3.3.html) library for Twisted.  It runs on Python 2 and 3 and aims for a simple API.  **This is a work in progress!  Stay tuned for more features and documentation.**

#### How do I use it?

Create a `Protocol` and `Factory`, determine your options and create a `TxDarn` instance:

````python
from twisted.internet import protocol
from txdarn.application import TxDarn


class SimpleProtocol(protocol.Protocol):

    def dataReceived(self, data):
        self.transport.write(["Here's what you said: {}'".format(data)])



SOCKJS_URL = 'https://cdnjs.cloudflare.com/ajax/libs/sockjs-client/1.0.3/sockjs.js',

options = {
    'sockjs-url': SOCKJS_URL,
    'websockets': True,
    'timeout': 5.0}

if __name__ == "__main__":
    txdarnServer = TxDarn(protocol.Factory.forProtocol(SimpleProtocol), options)
    kleinApp = txdarnServer.app
    kleinApp.run('localhost', 8081)
````

...where `TxDarn.app` is just a [Klein application](http://klein.readthedocs.org/en/latest/).
