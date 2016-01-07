from automat import MethodicalMachine

import eliot

from twisted.internet import defer
from twisted.python.constants import Values, ValueConstant
from twisted.internet import reactor, protocol, error
from twisted.python import failure
from twisted.protocols.policies import ProtocolWrapper, WrappingFactory

from txdarn.compat import asJSON, fromJSON


def _makeCollectAndReturn(value):
    '''Make an Automat collector function that consumes its output genexp and
    returns value.

    '''

    def _collectReturnValue(outputs):
        list(outputs)
        return value

    return _collectReturnValue


def sockJSJSON(data, cls=None):
    # no spaces
    return asJSON(data, separators=(',', ':'), cls=cls)


class INVALID_DATA(Values):
    NO_PAYLOAD = ValueConstant(b'Payload expected.')
    BAD_JSON = ValueConstant(b'Broken JSON encoding. ')


class DISCONNECT(Values):
    GO_AWAY = ValueConstant([3000, "Go away!"])
    STILL_OPEN = ValueConstant([2010, "Another connection still open"])


class HeartbeatClock(object):
    '''Schedules an recurring heartbeat frame, but only if no data has
    been written recently.

    '''

    writeHeartbeat = None
    pendingHeartbeat = None
    stopped = False

    def __init__(self, writeHeartbeat=None, period=25.0, clock=reactor):
        self.writeHeartbeat = writeHeartbeat
        self.period = period
        self.clock = clock

    def _createHeartbeatCall(self):
        self.pendingHeartbeat = self.clock.callLater(self.period,
                                                     self._sendHeartbeat)

    def _sendHeartbeat(self):
        self.writeHeartbeat()
        self._createHeartbeatCall()

    def schedule(self):
        """Schedule or reschedule the next heartbeat."""
        if self.stopped:
            raise RuntimeError("Can't schedule stopped heartbeat")

        if self.pendingHeartbeat is None:
            self._createHeartbeatCall()
        else:
            self.pendingHeartbeat.reset(self.period)

    def stop(self):
        """Permanently stop sending heartbeats."""
        if not self.stopped:
            self.stopped = True
            if self.pendingHeartbeat is not None:
                self.pendingHeartbeat.cancel()
                self.pendingHeartbeat = None


class SockJSProtocolMachine(object):
    _machine = MethodicalMachine()
    transport = None

    def __init__(self, heartbeater):
        self.heartbeater = heartbeater

    @classmethod
    def withHeartbeater(cls, heartbeater):
        """Connect a SockJSProtocolMachine to its heartbeater."""
        instance = cls(heartbeater)
        heartbeater.writeHeartbeat = instance.heartbeat
        return instance

    @_machine.state(initial=True)
    def notYetConnected(self):
        '''A connection has not yet been made.'''

    @_machine.state()
    def connected(self):
        '''We have a connection!'''

    @_machine.state()
    def disconnecting(self):
        '''We've asked to be disconnected.'''

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
        self.transport.writeOpen()
        self.heartbeater.schedule()

    @_machine.input()
    def write(self, data):
        '''We should write an array-like thing to the transport.'''

    @_machine.output()
    def _writeToTransport(self, data):
        '''Frame the array-like thing and write it.'''
        self.transport.writeData(data)
        self.heartbeater.schedule()

    @_machine.input()
    def receive(self, data):
        '''Data has arrived!'''

    @_machine.output()
    def _received(self, data):
        """Receive data -- just for completeness' sake"""
        return data

    @_machine.input()
    def heartbeat(self):
        '''Time to send a heartbeat.'''

    @_machine.output()
    def _writeHeartbeatToTransport(self):
        '''Write a heartbeat frame'''
        self.transport.writeHeartbeat()

    @_machine.input()
    def disconnect(self, reason=DISCONNECT.GO_AWAY):
        '''We're closing the connection because of reason.'''

    @_machine.input()
    def close(self):
        '''Our connection has been closed'''

    @_machine.output()
    def _writeCloseFrame(self, reason=DISCONNECT.GO_AWAY):
        '''Write a close frame with the given reason and schedule this
        connection close.

        '''
        self.transport.writeClose(reason)
        self.transport.loseConnection()
        self.transport = None

    @_machine.output()
    def _stopHeartbeat(self):
        '''We lost our connection - stop our heartbeat.'''
        self.heartbeater.stop()
        self.heartbeater = None

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
                   enter=disconnecting,
                   outputs=[_writeCloseFrame])

    connected.upon(close,
                   enter=disconnected,
                   outputs=[_stopHeartbeat])

    disconnecting.upon(close,
                       enter=disconnected,
                       outputs=[_stopHeartbeat])


class SockJSWireProtocolWrapper(ProtocolWrapper):
    '''Serialize and deserialize SockJS protocol elements.  Used as a
    base class for various transports.

    '''

    def __init__(self, factory, wrappedProtocol):
        ProtocolWrapper.__init__(self, factory, wrappedProtocol)
        self.jsonDecoder = self.factory.jsonDecoder
        self.jsonEncoder = self.factory.jsonEncoder

    def _jsonReceived(self, data):
        self.wrappedProtocol.dataReceived(data)

    def dataReceived(self, data):
        if not data:
            self.transport.write(INVALID_DATA.NO_PAYLOAD.value)
        else:
            try:
                self._jsonReceived(fromJSON(data, cls=self.jsonDecoder))
            except ValueError:
                self.transport.write(INVALID_DATA.BAD_JSON.value)

    def writeOpen(self):
        '''Write an open frame.'''
        self.write(b'o\n')

    def writeHeartbeat(self):
        self.write(b'h\n')

    def writeClose(self, reason):
        frameValue = [b'c',
                      sockJSJSON(reason.value, cls=self.jsonEncoder),
                      '\n']
        frame = b''.join(frameValue)
        self.write(frame)

    def writeData(self, data):
        frameValue = [b'a', sockJSJSON(data, cls=self.jsonEncoder), '\n']
        frame = b''.join(frameValue)
        self.write(frame)


class SockJSWireProtocolWrappingFactory(WrappingFactory):
    '''Factory that wraps a transport with SockJSWireProtocolWrapper.
    Used by transport factories (e.g., HTTP request, websocket
    connection).

    '''
    protocol = SockJSWireProtocolWrapper

    def __init__(self, wrappedFactory, jsonEncoder=None, jsonDecoder=None):
        WrappingFactory.__init__(self, wrappedFactory)
        self.jsonEncoder = jsonEncoder
        self.jsonDecoder = jsonDecoder


class SockJSProtocol(ProtocolWrapper):
    '''Wrap a user-supplied protocol for use with SockJS.'''

    def __init__(self, factory, wrappedProtocol, heartbeatPeriod,
                 clock=reactor):
        ProtocolWrapper.__init__(self, factory, wrappedProtocol)
        heartbeater = HeartbeatClock(period=heartbeatPeriod, clock=clock)
        self.sockJSMachine = SockJSProtocolMachine.withHeartbeater(heartbeater)

    def connectionMade(self):
        # don't catch any exception here - we want to stop
        # ProtocolWrapper.makeConnection from calling
        # self.wrappedProtocol.makeConnection
        self.sockJSMachine.connect(self.transport)

    def dataReceived(self, data):
        data = self.sockJSMachine.receive(data)
        self.wrappedProtocol.dataReceived(data)

    def write(self, data):
        self.sockJSMachine.write([data])

    def writeSequence(self, data):
        self.sockJSMachine.write(data)

    def loseConnection(self):
        self.sockJSMachine.disconnect()

    def connectionLost(self, reason=protocol.connectionDone):
        self.sockJSMachine.close()
        self.sockJSMachine = None
        self.wrappedProtocol.connectionLost(reason)


class SockJSProtocolFactory(WrappingFactory):
    """Factory that wraps another factory to provide the SockJS protocol.

    Wrap your protocol's factory with this for use with TxDarnApp.

    """

    protocol = SockJSProtocol

    def __init__(self, wrappedFactory, heartbeatPeriod=25.0, clock=reactor):
        WrappingFactory.__init__(self, wrappedFactory)
        self.heartbeatPeriod = heartbeatPeriod
        self.clock = clock

    def buildProtocol(self, addr):
        return self.protocol(self,
                             self.wrappedFactory.buildProtocol(addr),
                             self.heartbeatPeriod,
                             self.clock)


class SessionTimeout(Exception):
    """A session has timed out before all its data has been written."""


class RequestSessionMachine(object):
    _machine = MethodicalMachine()
    request = None

    def __init__(self, serverProtocol):
        self.buffer = []
        self.serverProtocol = serverProtocol
        self.connectionCompleted = defer.Deferred()

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
        '''Attach to the request, performing setup if necessary.  The
        user-visible return value of this input should be True if the
        request attached and False if not, because an existing request
        was already attached.

        '''

    @_machine.input()
    def detach(self):
        '''Detach the current request'''

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
    def _completeConnection(self, request):
        self.connectionCompleted.callback(request)

    @_machine.output()
    def _receive(self, data):
        '''Pass data through to the wrapped protocol'''
        self.serverProtocol.dataReceived(data)

    @_machine.output()
    def _bufferWrite(self, data):
        '''Without a request, we have to buffer our writes'''
        self.buffer.extend(data)

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
    def _closeRequestForDeadSession(self, request):
        assert self.request is None
        # TODO: get access to SockJSWireProtocolWrapper, too
        message = SockJSProtocolMachine.closeFrame(DISCONNECT.GO_AWAY)
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
                        outputs=[_openRequest,
                                 _completeConnection],
                        # TODO - everything but XHRSession wants this
                        # to be True
                        collector=_makeCollectAndReturn(False))

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
                                collector=_makeCollectAndReturn(False))

    connectedNoTransportEmptyBuffer.upon(write,
                                         enter=connectedNoTransportPending,
                                         outputs=[_bufferWrite])
    connectedNoTransportEmptyBuffer.upon(dataReceived,
                                         enter=connectedNoTransportEmptyBuffer,
                                         outputs=[_receive])
    connectedNoTransportEmptyBuffer.upon(attach,
                                         enter=connectedHaveTransport,
                                         outputs=[_openRequest],
                                         collector=_makeCollectAndReturn(True))
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
                                     collector=_makeCollectAndReturn(False))
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

    disconnected.upon(attach,
                      enter=disconnected,
                      outputs=[_closeRequestForDeadSession],
                      collector=_makeCollectAndReturn(False))


class TimeoutClock(object):
    '''
    Expire
    '''
    expired = False
    timeoutCall = None

    def __init__(self, length=5.0, clock=reactor):
        self.length = length
        self.clock = clock
        self.terminated = defer.Deferred()
        self.terminated.addCallback(self._cbExpire)

    def _cbExpire(self, ignored):
        self.expired = True
        self.timeoutCall = None

    def reset(self):
        if self.expired:
            raise RuntimeError("Cannot restart expired timeout.")

        if self.timeoutCall is not None:
            self.timeoutCall.cancel()
            self.timeoutCall = None

    def start(self):
        if self.expired:
            raise RuntimeError("Cannot start expired timeout.")

        if not self.expired and self.timeoutCall is None:
            self.timeoutCall = self.clock.callLater(self.length,
                                                    self.terminated.callback,
                                                    None)


class RequestSessionProtocolWrapper(SockJSWireProtocolWrapper):
    """A protocol wrapper that uses an http.Request object as its
    transport.

    The protocol cannot be started without a request, but it may outlive that
    and many subsequent requests.

    This is the base class for polling SockJS transports

    """
    request = None
    attached = False

    def __init__(self, timeoutClock, *args, **kwargs):
        SockJSWireProtocolWrapper.__init__(self, *args, **kwargs)
        self.timeoutClock = timeoutClock
        self.sessionMachine = RequestSessionMachine(
            self.wrappedProtocol)

        self.connectionCompleted = self.sessionMachine.connectionCompleted
        self.connectionCompleted.addCallback(self._completeConnectionWrapping)

    def makeConnection(self, transport):
        name = self.__class__.__name__
        raise RuntimeError(
            "Do not use {name}.makeConnection;"
            " instead use {name}.makeConnectionFromRequest".format(name=name))

    def _completeConnectionWrapping(self, request):
        ProtocolWrapper.makeConnection(self, request.transport)

    def makeConnectionFromRequest(self, request):
        if self.sessionMachine.attach(request):
            self.attached = True
            self.timeoutClock.reset()

    def detachFromRequest(self):
        self.sessionMachine.detach()
        self.attached = False
        self.timeoutClock.start()

    def write(self, data):
        self.sessionMachine.write(data)

    def writeSequence(self, data):
        for datum in data:
            self.sessionMachine.write(datum)

    def _jsonReceived(self, data):
        self.sessionMachine.dataReceived(data)

    def loseConnection(self):
        self.disconnecting = 1
        self.sessionMachine.detach()
        self.sessionMachine.loseConnection()
        self.timeoutClock.start()

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


class RequestSessionWrappingFactory(SockJSWireProtocolWrappingFactory):
    protocol = RequestSessionProtocolWrapper

    def buildProtocol(self, timeoutClock, addr):
        return self.protocol(timeoutClock,
                             self,
                             self.wrappedFactory.buildProtocol(addr))


class SessionHouse(object):

    def __init__(self, factory, timeout=5.0, timeoutFactory=TimeoutClock):
        self.factory = factory
        self.timeout = 5.0
        self.timeoutFactory = timeoutFactory
        self.sessions = {}

    def makeSession(self, sessionID, request):
        timeoutClock = self.timeoutFactory(self.timeout)
        protocol = self.factory.buildProtocol(timeoutClock,
                                              request.transport.getHost())
        timeoutDeferred = timeoutClock.terminated

        timeoutDeferred.addCallback(self._sessionTimedOut, sessionID)
        timeoutDeferred.addErrback(eliot.writeFailure)

        return protocol

    def _sessionTimedOut(self, _, sessionID):
        protocol = self.sessions.pop(sessionID)
        assert not protocol.attached
        protocol.connectionLost()

    def attachToSession(self, sessionID, request):
        session = self.sessions.get(sessionID)
        if not session:
            session = self.sessions[sessionID] = self.makeSession(sessionID,
                                                                  request)
        session.makeConnectionFromRequest(request)

    def writeToSession(self, sessionID, data):
        try:
            session = self.sessions[sessionID]
        except KeyError:
            return False
        else:
            session.dataReceived(data)
            return True


class XHRSession(RequestSessionProtocolWrapper):

    def writeOpen(self):
        RequestSessionProtocolWrapper.writeOpen(self)
        self.detachFromRequest()

    def writeData(self, data):
        RequestSessionProtocolWrapper.writeData(self, data)
        self.detachFromRequest()


class XHRSessionFactory(RequestSessionWrappingFactory):
    protocol = XHRSession
