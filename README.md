# txdarn

### SockJS for modern Twisted

#### What is it?
`txdarn` is a [SockJS](http://sockjs.org) [0.3.4](https://github.com/sockjs/sockjs-client) library for Twisted.  It runs on Python 2 and 3 and aims for a simple API.  **This is a work in progress!  Stay tuned for more features and documentation.**

#### How do I use it?

Create a `Protocol` and `Factory`, determine your options and create a `TxDarn` instance:

````python
from twisted.internet import protocol, endpoints, reactor
from twisted.web import server
from txdarn.resources import TxDarn


class SimpleProtocol(protocol.Protocol):

    def dataReceived(self, data):
        self.transport.write(["Here's what you said: {}'".format(data)])


SOCKJS_URL = ('https://cdnjs.cloudflare.com'
              '/ajax/libs/sockjs-client/1.0.3/sockjs.js')

txdarnResource = TxDarn(protocol.Factory.forPortocol(SimpleProtocol),
                        sockJSURL=SOCKJS_URL)
site = server.Site(txdarnResource)
endpoints.serverFromString(reactor, 'tcp:8080').listen(site)
reactor.run()
````
