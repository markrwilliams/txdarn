from automat import MethodicalMachine

import eliot

from twisted.python.constants import Values, ValueConstant
from twisted.internet import reactor, protocol, error
from twisted.python import failure
from twisted.protocols.policies import ProtocolWrapper, WrappingFactory

import six.moves

from txdarn.compat import asJSON, fromJSON


def _collectReturnFalse(outputs):
    '''An Automat collector function that consumes its output genexp and
    returns False.

    '''
    list(outputs)
    return False


def _collectReturnTrue(outputs):
    '''An Automat collector function that consumes its output genexp and
    returns True.

    '''
    list(outputs)
    return True


def sockJSJSON(data, cls=None):
    # no spaces
    return asJSON(data, separators=(',', ':'), cls=cls)


class DISCONNECT(Values):
    GO_AWAY = ValueConstant([3000, "Go away!"])
    STILL_OPEN = ValueConstant([2010, "Another connection still open"])


class HeartbeatClock(object):
    _machine = MethodicalMachine()
    writeHeartbeat = None
    pendingHeartbeat = None

    @staticmethod
    def graphviz(machine=_machine):  # pragma: no cover
        return machine.graphviz()

    def __init__(self, writeHeartbeat=None, period=25.0, clock=reactor):
        self.writeHeartbeat = writeHeartbeat
        self.period = period
        self.clock = clock

    @_machine.state(initial=True)
    def unscheduled(self):
        '''No heartbeat is scheduled.'''

    @_machine.state()
    def scheduled(self):
        '''A heartbeat is pending.'''

    @_machine.state()
    def stopped(self):
        '''The clock is permanently stopped.'''

    @_machine.input()
    def sent(self):
        '''A heartbeat has been sent.'''

    @_machine.input()
    def schedule(self):
        '''Schedule another heartbeat, cancel any pending'''

    @_machine.input()
    def stop(self):
        '''Stop this clock forever'''

    @_machine.output()
    def _reschedule(self):
        self.schedule()

    @_machine.output()
    def _resetTheClock(self):
        '''Drop our pending heartbeat because it's either been completed or
        canceled.

        '''
        self.pendingHeartbeat = None

    @_machine.output()
    def _stopTheClock(self):
        '''Cancel our pending heartbeat.'''
        self.pendingHeartbeat.cancel()

    def _sendHeartbeat(self):
        '''Complete our pending heartbeat.'''
        self.writeHeartbeat()
        self.sent()

    @_machine.output()
    def _startTheClock(self):
        '''Schedule our next heartbeat.'''
        self.pendingHeartbeat = self.clock.callLater(self.period,
                                                     self._sendHeartbeat)

    unscheduled.upon(schedule, enter=scheduled, outputs=[_startTheClock])
    unscheduled.upon(stop, enter=stopped, outputs=[])

    scheduled.upon(schedule, enter=unscheduled, outputs=[_stopTheClock,
                                                         _resetTheClock,
                                                         _reschedule])
    scheduled.upon(sent, enter=unscheduled, outputs=[_resetTheClock,
                                                     _reschedule])

    scheduled.upon(stop, enter=stopped, outputs=[_stopTheClock,
                                                 _resetTheClock])


class SockJSProtocolMachine(object):
    _machine = MethodicalMachine()
    transport = None

    @staticmethod
    def graphviz(machine=_machine):  # pragma: no cover
        return machine.graphviz()

    def __init__(self, heartbeater, jsonEncoder=None, jsonDecoder=None):
        self.heartbeater = heartbeater
        self.jsonEncoder = jsonEncoder
        self.jsonDecoder = jsonDecoder

    @classmethod
    def withHeartbeater(cls, heartbeater, jsonEncoder=None, jsonDecoder=None):
        """Connect a SockJSProtocolMachine to its heartbeater."""
        instance = cls(heartbeater, jsonEncoder, jsonDecoder)
        heartbeater.writeHeartbeat = instance.heartbeat
        return instance

    @_machine.state(initial=True)
    def notYetConnected(self):
        '''A connection has not yet been made.'''

    @_machine.state()
    def connected(self):
        '''We have a connection!'''

    @_machine.state()
    def disconnected(self):
        '''We have been disconnected.'''

    @_machine.input()
    def connect(self, transport):
        '''Establish a connection on the transport.'''

    @_machine.output()
    def _connectionEstablished(self, transport):
        '''Store a reference to our transport and write an open frame.'''
        self.transport = transport
        self.transport.write(b'o')
        self.heartbeater.schedule()

    @_machine.input()
    def write(self, data):
        '''We should write an array-like thing to the transport.'''

    @_machine.output()
    def _writeToTransport(self, data):
        '''Frame the array-like thing and write it.'''
        self.transport.write(b'a' + sockJSJSON(data, cls=self.jsonEncoder))
        self.heartbeater.schedule()

    @_machine.input()
    def receive(self, data):
        '''Data has arrived!'''

    @_machine.output()
    def _received(self, data):
        return fromJSON(data, cls=self.jsonDecoder)

    @_machine.input()
    def heartbeat(self):
        '''Time to send a heartbeat.'''

    @_machine.output()
    def _writeHeartbeatToTransport(self):
        '''Write a heartbeat frame'''
        self.transport.write(b'h')

    @_machine.input()
    def disconnect(self, reason=DISCONNECT.GO_AWAY):
        '''We're closing the connection because of reason.'''

    @staticmethod
    def closeFrame(reason):
        return b'c' + sockJSJSON(reason.value)

    @_machine.output()
    def _writeCloseFrame(self, reason=DISCONNECT.GO_AWAY):
        '''Write a close frame with the given reason and schedule this
        connection close.

        '''
        self.heartbeater.stop()
        self.transport.write(self.closeFrame(reason))
        self.transport.loseConnection()
        self.transport = None

    notYetConnected.upon(connect,
                         enter=connected,
                         outputs=[_connectionEstablished])
    notYetConnected.upon(disconnect,
                         enter=disconnected,
                         outputs=[])

    connected.upon(write,
                   enter=connected,
                   outputs=[_writeToTransport])
    connected.upon(receive,
                   enter=connected,
                   outputs=[_received],
                   collector=next)
    connected.upon(heartbeat,
                   enter=connected,
                   outputs=[_writeHeartbeatToTransport])
    connected.upon(disconnect,
                   enter=disconnected,
                   outputs=[_writeCloseFrame])


loggingRepr = six.moves.reprlib.Repr().repr


class SockJSProtocol(ProtocolWrapper):

    def __init__(self, factory, wrappedProtocol, sockJSMachine):
        ProtocolWrapper.__init__(self, factory, wrappedProtocol)
        self.sockJSMachine = sockJSMachine

    def connectionMade(self):
        action_type = '{}.dataReceived'.format(self.__class__.__name__)
        try:
            with eliot.start_action(action_type=action_type):
                self.sockJSMachine.connect(self.transport)
        except ValueError:
            pass
        else:
            self.wrappedProtocol.connectionMade()

    def dataReceived(self, data):
        action_type = '{}.dataReceived'.format(self.__class__.__name__)
        try:
            with eliot.start_action(action_type=action_type):
                eliot.Message.log(data=loggingRepr(data))

                decoded = self.sockJSMachine.receive(data)

        except Exception:
            pass
        else:
            self.wrappedProtocol.dataReceived(decoded)

    def write(self, data):
        try:
            action_type = '{}.write'.format(self.__class__.__name__)
            with eliot.start_action(action_type=action_type):
                eliot.Message.log(data=loggingRepr(data))

                self.sockJSMachine.write([data])

        except Exception:
            pass

    def writeSequence(self, data):
        try:
            action_type = '{}.writeSequence'.format(self.__class__.__name__)
            with eliot.start_action(action_type=action_type):
                eliot.Message.log(data=loggingRepr(data))

                self.sockJSMachine.write(data)

        except Exception:
            pass

    def loseConnection(self):
        try:
            action_type = '{}.loseConnection'.format(self.__class__.__name__)
            with eliot.start_action(action_type=action_type):

                self.sockJSMachine.disconnect()

        except Exception:
            pass


class SockJSProtocolFactory(WrappingFactory):
    """Factory that wraps another factory to provide the SockJS protocol.

    """

    protocol = SockJSProtocol

    def __init__(self, wrappedProtocol,
                 jsonEncoder=None, jsonDecoder=None,
                 heartbeatPeriod=25.0, clock=reactor):
        WrappingFactory.__init__(self, wrappedProtocol)

        heartbeater = HeartbeatClock(period=heartbeatPeriod, clock=clock)
        self.sockJSMachine = SockJSProtocolMachine.withHeartbeater(
            heartbeater, jsonEncoder, jsonDecoder)

    def buildProtocol(self, addr):
        return self.protocol(self, self.wrappedFactory.buildProtocol(addr),
                             self.sockJSMachine)


class SessionTimeout(Exception):
    """A session has timed out before all its data has been written."""


class RequestSessionMachine(object):
    _machine = MethodicalMachine()
    request = None

    @staticmethod
    def graphviz(machine=_machine):  # pragma: no cover
        return machine.graphviz()

    def __init__(self, serverProtocol):
        self.buffer = []
        self.serverProtocol = serverProtocol

    @_machine.state(initial=True)
    def neverConnected(self):
        '''We've never been connected to any request.'''

    @_machine.state()
    def connectedHaveTransport(self):
        '''We're attached to a transport.'''

    @_machine.state()
    def connectedNoTransportEmptyBuffer(self):
        '''We've been detached from our request and have no pending data to
        write.

        '''

    @_machine.state()
    def connectedNoTransportPending(self):
        '''We've been detached from our request but there's pending data.'''

    @_machine.state()
    def loseConnectionEmptyBuffer(self):
        '''We were told to lose the connection, and we have a request and thus
    an empty buffer.

        '''

    @_machine.state()
    def loseConnectionPending(self):
        '''We were told to lose the connection, but we have data still in our
        buffer.

        '''

    @_machine.state()
    def disconnected(self):
        '''The session bound to this protocol's lifetime has disappeared.'''

    @_machine.input()
    def attach(self, request):
        '''Attach to the request, performing setup if necessary'''

    @_machine.input()
    def detach(self):
        '''Detatch the current request'''

    @_machine.input()
    def write(self, data):
        '''The protocol wants to write to the transport.'''

    @_machine.input()
    def dataReceived(self, data):
        '''The client has written some data.'''

    @_machine.input()
    def loseConnection(self, reason=protocol.connectionDone):
        '''Lose the request, if applicable'''

    @_machine.input()
    def connectionLost(self, reason=protocol.connectionDone):
        '''The connection has been lost; clean up any request and clean up the
        protocol'''

    @_machine.output()
    def _openRequest(self, request):
        assert self.request is None
        self.request = request

    @_machine.output()
    def _receive(self, data):
        '''Pass data through to the wrapped protocol'''
        self.serverProtocol.dataReceived(data)

    @_machine.output()
    def _bufferWrite(self, data):
        '''Without a request, we have to buffer our writes'''
        self.buffer.append(data)

    @_machine.output()
    def _flushBuffer(self, request):
        '''Flush any pending data from the buffer to the request before we '''
        assert request is self.request
        for item in self.buffer:
            request.write(item)
        self.buffer = []

    @_machine.output()
    def _directWrite(self, data):
        '''Skip our buffer'''
        self.request.write(data)

    @_machine.output()
    def _closeRequest(self):
        self.request.finish()
        self.request = None

    @_machine.output()
    def _closeDuplicateRequest(self, request):
        if request is not self.request:
            message = SockJSProtocolMachine.closeFrame(DISCONNECT.STILL_OPEN)
            request.write(message)
            request.finish()

    @_machine.output()
    def _closeProtocol(self, reason=protocol.connectionDone):
        self.serverProtocol.connectionLost(reason=protocol.connectionDone)
        self.serverProtocol = None

    @_machine.output()
    def _timedOut(self, reason=protocol.connectionDone):
        if isinstance(reason.value, (error.ConnectionDone,
                                     error.ConnectionLost)):
            reason = failure.Failure(SessionTimeout())
        self.serverProtocol.connectionLost(reason=reason)
        self.serverProtocol = None

    neverConnected.upon(write,
                        enter=connectedNoTransportPending,
                        outputs=[_bufferWrite])
    neverConnected.upon(dataReceived,
                        enter=connectedNoTransportEmptyBuffer,
                        outputs=[_receive])
    neverConnected.upon(attach,
                        enter=connectedHaveTransport,
                        outputs=[_openRequest],
                        collector=_collectReturnTrue)

    connectedHaveTransport.upon(write,
                                enter=connectedHaveTransport,
                                outputs=[_directWrite])
    connectedHaveTransport.upon(dataReceived,
                                enter=connectedHaveTransport,
                                outputs=[_receive])
    connectedHaveTransport.upon(detach,
                                enter=connectedNoTransportEmptyBuffer,
                                outputs=[_closeRequest])
    connectedHaveTransport.upon(attach,
                                enter=connectedHaveTransport,
                                outputs=[_closeDuplicateRequest],
                                collector=_collectReturnFalse)

    connectedNoTransportEmptyBuffer.upon(write,
                                         enter=connectedNoTransportPending,
                                         outputs=[_bufferWrite])
    connectedNoTransportEmptyBuffer.upon(dataReceived,
                                         enter=connectedNoTransportEmptyBuffer,
                                         outputs=[_receive])
    connectedNoTransportEmptyBuffer.upon(attach,
                                         enter=connectedHaveTransport,
                                         outputs=[_openRequest],
                                         collector=_collectReturnTrue)
    connectedNoTransportEmptyBuffer.upon(detach,
                                         enter=connectedNoTransportEmptyBuffer,
                                         outputs=[])
    connectedNoTransportEmptyBuffer.upon(loseConnection,
                                         enter=loseConnectionEmptyBuffer,
                                         outputs=[])
    connectedNoTransportEmptyBuffer.upon(connectionLost,
                                         enter=disconnected,
                                         outputs=[_closeProtocol])
    # this is a separate state so we can't attach a request after
    # we've called loseConnection
    loseConnectionEmptyBuffer.upon(connectionLost,
                                   enter=disconnected,
                                   outputs=[_closeProtocol])
    loseConnectionEmptyBuffer.upon(detach,
                                   enter=loseConnectionEmptyBuffer,
                                   outputs=[])

    connectedNoTransportPending.upon(write,
                                     enter=connectedNoTransportPending,
                                     outputs=[_bufferWrite])
    connectedNoTransportPending.upon(dataReceived,
                                     enter=connectedNoTransportPending,
                                     outputs=[_receive])
    connectedNoTransportPending.upon(attach,
                                     enter=connectedHaveTransport,
                                     outputs=[_openRequest,
                                              _flushBuffer],
                                     collector=_collectReturnTrue)
    connectedNoTransportPending.upon(detach,
                                     enter=connectedNoTransportPending,
                                     outputs=[])
    connectedNoTransportPending.upon(loseConnection,
                                     enter=loseConnectionPending,
                                     outputs=[])
    connectedNoTransportPending.upon(connectionLost,
                                     enter=disconnected,
                                     outputs=[_timedOut])
    # this is a separate state so we can't attach a request after
    # we've called loseConnection
    loseConnectionPending.upon(connectionLost,
                               enter=disconnected,
                               outputs=[_timedOut])

    loseConnectionPending.upon(detach,
                               enter=loseConnectionPending,
                               outputs=[])


class RequestWrapperProtocol(ProtocolWrapper):
    """A protocol wrapper that uses an http.Request object as its
    transport.

    The protocol cannot be started without a request, but it may outlive that
    and many subsequent requests.

    """
    request = None

    def __init__(self, *args, **kwargs):
        ProtocolWrapper.__init__(self, *args, **kwargs)
        self.sessionMachine = RequestSessionMachine(self.wrappedProtocol)

    def makeConnection(self, transport):
        name = self.__class__.__name__
        raise RuntimeError(
            "Do not use {name}.makeConnection;"
            " instead use {name}.makeConnectionFromRequest".format(name=name))

    def makeConnectionFromRequest(self, request):
        if self.sessionMachine.attach(request):
            ProtocolWrapper.makeConnection(self, request.transport)

    def detachFromRequest(self):
        self.sessionMachine.detach()

    def write(self, data):
        self.sessionMachine.write(data)

    def writeSequence(self, data):
        for datum in data:
            self.sessionMachine.write(datum)

    def dataReceived(self, data):
        self.sessionMachine.dataReceived(data)

    def loseConnection(self):
        self.disconnecting = 1
        self.sessionMachine.detach()
        self.sessionMachine.loseConnection()

    def registerProducer(self, producer, streaming):
        # TODO: implement this!
        raise NotImplementedError

    def unregisterProducer(self):
        # TODO: implement this!
        raise NotImplementedError

    def connectionLost(self, reason=protocol.connectionDone):
        self.factory.unregisterProtocol(self)
        self.sessionMachine.detach()
        self.sessionMachine.connectionLost(reason)
        self.sessionMachine = None


class RequestWrapperFactory(WrappingFactory):
    """A factory that provides a protocol that proxies through to a
    request object.

    """
    protocol = RequestWrapperProtocol
