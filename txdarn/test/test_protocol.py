import json

from twisted.trial import unittest
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol, Factory
from twisted.protocols.policies import WrappingFactory
from twisted.test.proto_helpers import StringTransport
from twisted.internet.task import Clock
from twisted.internet.address import IPv4Address
from twisted.internet.error import ConnectionDone
# TODO: don't use twisted's private test APIs
from twisted.web.test.requesthelper import DummyRequest
from .. import protocol as P


class sockJSJSONTestCase(unittest.SynchronousTestCase):

    def test_sockJSJSON(self):
        self.assertEqual(P.sockJSJSON([3000, 'Go away!']),
                         b'[3000,"Go away!"]')


class HeartbeatClockTestCase(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.period = 25.0
        self.heartbeats = 0

        def fakeHeartbeat():
            self.heartbeats += 1

        self.heartbeater = P.HeartbeatClock(fakeHeartbeat,
                                            period=self.period,
                                            clock=self.clock)

    def test_neverScheduled(self):
        '''Heartbeats are not scheduled before their first schedule(), and are
        not scheduled if we immediately stop() the HeartbeatClock.  A
        stopped HeartbeatClock can never schedule any other
        heartbeats.

        '''

        self.assertFalse(self.clock.getDelayedCalls())
        self.heartbeater.stop()
        self.assertFalse(self.clock.getDelayedCalls())

        with self.assertRaises(RuntimeError):
            self.heartbeater.schedule()

        self.assertFalse(self.clock.getDelayedCalls())

    def test_schedule(self):
        '''Heartbeats are scheduled and recur if not interrupted.'''

        self.heartbeater.schedule()
        pendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(), [pendingBeat])

        self.clock.advance(self.period * 2)
        self.assertEqual(self.heartbeats, 1)

        rescheduledPendingBeat = self.heartbeater.pendingHeartbeat
        self.assertIsNot(pendingBeat, rescheduledPendingBeat)
        self.assertEqual(self.clock.getDelayedCalls(),
                         [rescheduledPendingBeat])

    def test_schedule_interrupts(self):
        '''A schedule() call will remove the pending heartbeat and reschedule
        it for later.

        '''
        self.heartbeater.schedule()
        pendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(), [pendingBeat])

        self.heartbeater.schedule()

        self.assertFalse(self.heartbeats)

        rescheduledPendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(),
                         [rescheduledPendingBeat])

    def test_schedule_stop(self):
        '''A stop() call removes any pending heartbeats.'''
        self.heartbeater.schedule()
        pendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(), [pendingBeat])

        self.heartbeater.stop()
        self.assertFalse(self.heartbeats)
        self.assertFalse(self.clock.getDelayedCalls())

        # this does not raise an exception
        self.heartbeater.stop()


class TestProtocol(Protocol):
    connectionMadeCalls = 0

    def connectionMade(self):
        self.connectionMadeCalls += 1
        if self.connectionMadeCalls > 1:
            assert False, "connectionMade must only be called once"


class RecordingProtocol(Protocol):

    def dataReceived(self, data):
        self.factory.receivedData.append(data)


class RecordingProtocolFactory(Factory):
    protocol = RecordingProtocol

    def __init__(self, receivedData):
        self.receivedData = receivedData


class EchoProtocol(TestProtocol):
    DISCONNECT = 'DISCONNECT'

    def dataReceived(self, data):
        if isinstance(data, list):
            self.transport.writeSequence(data)
        else:
            self.transport.write(data)

    def connectionLost(self, reason):
        self.factory.connectionLost.append(reason)


class EchoProtocolFactory(Factory):
    protocol = EchoProtocol

    def __init__(self, connectionLost):
        self.connectionLost = connectionLost


class SockJSWireProtocolWrapperTestCase(unittest.TestCase):
    '''Sanity tests for SockJS transport base class.'''

    def setUp(self):
        self.transport = StringTransport()

        self.receivedData = []
        self.wrappedFactory = RecordingProtocolFactory(self.receivedData)
        self.factory = P.SockJSWireProtocolWrappingFactory(
            self.wrappedFactory)

        self.address = IPv4Address('TCP', '127.0.0.1', 80)

        self.protocol = self.factory.buildProtocol(self.address)
        self.protocol.makeConnection(self.transport)

    def test_writeOpen(self):
        '''writeOpen writes a single open frame.'''
        self.protocol.writeOpen()
        self.assertEqual(self.transport.value(), b'o\n')

    def test_writeHeartbeat(self):
        '''writeHeartbeat writes a single heartbeat frame.'''
        self.protocol.writeHeartbeat()
        self.assertEqual(self.transport.value(), b'h\n')

    def test_writeClose(self):
        '''writeClose writes a close frame containing the provided reason.'''
        self.protocol.writeClose(P.DISCONNECT.GO_AWAY)
        self.assertEqual(self.transport.value(), b'c[3000,"Go away!"]\n')

    def test_writeData(self):
        '''writeData writes the provided data to the transport.'''
        self.protocol.writeData(["letter", 2])
        self.assertEqual(self.transport.value(), b'a["letter",2]\n')

    def test_dataReceived(self):
        '''The wrapped protocol receives deserialized JSON data.'''
        self.protocol.dataReceived(b'["letter",2]')
        self.protocol.dataReceived(b'["another",null]')
        self.assertEqual(self.receivedData, [["letter", 2],
                                             ["another", None]])

    def test_closeFrame(self):
        '''closeFrame returns a serialized close frame for use by the
        caller.

        '''
        frame = self.protocol.closeFrame(P.DISCONNECT.GO_AWAY)
        self.assertEqual(frame, b'c[3000,"Go away!"]\n')
        self.assertFalse(self.transport.value())

    def test_emptyDataReceived(self):
        '''The wrapped protocol does not receive empty strings and the sender
        receives an error message.

        '''
        with self.assertRaises(P.InvalidData) as excContext:
            self.protocol.dataReceived(b'')

        self.assertEqual(excContext.exception.reason,
                         P.INVALID_DATA.NO_PAYLOAD.value)
        self.assertFalse(self.receivedData)

    def test_badJSONReceived(self):
        '''The wrapped protocol does not receive malform JSON and the sender
        receives an error message.

        '''
        with self.assertRaises(P.InvalidData) as excContext:
            self.protocol.dataReceived(b'!!!')

        self.assertEqual(excContext.exception.reason,
                         P.INVALID_DATA.BAD_JSON.value)
        self.assertFalse(self.receivedData)

    def test_jsonEncoder(self):
        '''SockJSWireProtocolWrapper can use a json.JSONEncoder subclass for
        writes.

        '''
        class ComplexEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, complex):
                    return [obj.real, obj.imag]
                # Let the base class default method raise the TypeError
                return json.JSONEncoder.default(self, obj)

        factory = P.SockJSWireProtocolWrappingFactory(
            self.wrappedFactory,
            jsonEncoder=ComplexEncoder)

        encodingProtocol = factory.buildProtocol(self.address)
        encodingProtocol.makeConnection(self.transport)

        encodingProtocol.writeData([2 + 1j])

        self.assertEqual(self.transport.value(), b'a[[2.0,1.0]]\n')

    def test_jsonDecoder(self):
        '''SockJSWireProtocolWrapper can use a json.JSONDecoder subclass for
        receives.

        '''
        class SetDecoder(json.JSONDecoder):
            def __init__(self, *args, **kwargs):
                kwargs['object_hook'] = self.set_object_hook
                super(SetDecoder, self).__init__(*args, **kwargs)

            def set_object_hook(self, obj):
                if isinstance(obj, dict) and obj.get('!set'):
                    return set(obj['!set'])
                return obj

        factory = P.SockJSWireProtocolWrappingFactory(
            self.wrappedFactory,
            jsonDecoder=SetDecoder)

        encodingProtocol = factory.buildProtocol(self.address)
        encodingProtocol.makeConnection(self.transport)

        encodingProtocol.dataReceived(b'{"!set": [1, 2, 3]}')
        self.assertEqual(self.receivedData, [{1, 2, 3}])


class RecordsWireProtocolActions(object):

    def __init__(self):
        self.lostConnection = 0
        self.wroteOpen = 0
        self.wroteHeartbeat = 0
        self.wroteData = []
        self.wroteClose = []

    def empty(self):
        return not any([self.lostConnection,
                        self.wroteOpen,
                        self.wroteHeartbeat,
                        self.wroteData,
                        self.wroteClose])


class FakeSockJSWireProtocol(object):

    def __init__(self, recorder):
        self._recorder = recorder

    def loseConnection(self):
        self._recorder.lostConnection += 1

    def writeOpen(self):
        self._recorder.wroteOpen += 1

    def writeHeartbeat(self):
        self._recorder.wroteHeartbeat += 1

    def writeData(self, data):
        self._recorder.wroteData.append(data)

    def writeClose(self, reason):
        self._recorder.wroteClose.append(reason)


class RecordsHeartbeat(object):

    def __init__(self):
        self.scheduleCalls = 0
        self.stopCalls = 0

    def scheduleCalled(self):
        self.scheduleCalls += 1

    def stopCalled(self):
        self.stopCalls += 1


class FakeHeartbeatClock(object):

    def __init__(self, recorder):
        self.writeHeartbeat = None
        self._recorder = recorder

    def schedule(self):
        self._recorder.scheduleCalled()

    def stop(self):
        self._recorder.stopCalled()


class SockJSProtocolMachineTestCase(unittest.TestCase):

    def setUp(self):
        self.heartbeatRecorder = RecordsHeartbeat()
        self.heartbeater = FakeHeartbeatClock(self.heartbeatRecorder)
        self.sockJSMachine = P.SockJSProtocolMachine(self.heartbeater)
        self.protocolRecorder = RecordsWireProtocolActions()
        self.sockJSWireProtocol = FakeSockJSWireProtocol(self.protocolRecorder)

    def test_disconnectBeforeConnect(self):
        '''Disconnecting before connecting permanently disconnects
        a SockJSProtocolMachine.

        '''
        self.sockJSMachine.disconnect()
        self.assertTrue(self.protocolRecorder.empty())

        with self.assertRaises(KeyError):
            self.sockJSMachine.connect(self.sockJSWireProtocol)

    def test_connect(self):
        '''SockJSProtocolMachine.connect writes an opening frame and schedules
        a heartbeat.

        '''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
        self.assertEqual(self.heartbeatRecorder.scheduleCalls, 1)

    def test_write(self):
        '''SockJSProtocolMachine.write writes the requested data and
        (re)schedules a heartbeat.

        '''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        self.sockJSMachine.write([1, 'something'])

        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
        self.assertEqual(self.protocolRecorder.wroteData, [[1, 'something']])
        self.assertEqual(self.heartbeatRecorder.scheduleCalls, 2)

    def test_heartbeat(self):
        '''SockJSProtocolMachine.heartbeat writes a heartbeat!'''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        self.sockJSMachine.heartbeat()

        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
        self.assertEqual(self.protocolRecorder.wroteHeartbeat, 1)
        self.assertEqual(self.heartbeatRecorder.scheduleCalls, 1)

    def test_withHeartBeater(self):
        '''SockJSProtocolMachine.withHeartbeater should associate a new
        instance's heartbeat method with the heartbeater.

        '''
        instance = P.SockJSProtocolMachine.withHeartbeater(
            self.heartbeater)
        instance.connect(self.sockJSWireProtocol)
        self.heartbeater.writeHeartbeat()

        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
        self.assertEqual(self.protocolRecorder.wroteHeartbeat, 1)
        self.assertEqual(self.heartbeatRecorder.scheduleCalls, 1)

    def test_receive(self):
        '''SockJSProtocolMachine.receive passes decoded data through.'''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        data = [1, 'something']
        self.assertEqual(self.sockJSMachine.receive(data), data)

    def test_disconnect(self):
        '''SockJSProtocolMachine.disconnect writes a close frame, disconnects
        the transport and cancels any pending heartbeats.

        '''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        self.sockJSMachine.disconnect(reason=P.DISCONNECT.GO_AWAY)

        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
        self.assertEqual(self.protocolRecorder.wroteClose,
                         [P.DISCONNECT.GO_AWAY])
        self.assertEqual(self.protocolRecorder.lostConnection, 1)

        self.assertEqual(self.heartbeatRecorder.stopCalls, 1)


class RecordsProtocolMachineActions(object):

    def __init__(self):
        self.connect = []
        self.received = []
        self.written = []
        self.disconnected = 0
        self.closed = 0


class FakeSockJSProtocolMachine(object):

    def __init__(self, recorder):
        self._recorder = recorder

    def connect(self, transport):
        self._recorder.connect.append(transport)

    def receive(self, data):
        self._recorder.received.append(data)
        return data

    def write(self, data):
        self._recorder.written.append(data)

    def disconnect(self):
        self._recorder.disconnected += 1

    def close(self):
        self._recorder.closed += 1


class SockJSProtocolTestCase(unittest.TestCase):

    def setUp(self):
        self.connectionLost = []
        self.stateMachineRecorder = RecordsProtocolMachineActions()

        wrappedFactory = EchoProtocolFactory(self.connectionLost)
        self.factory = P.SockJSProtocolFactory(wrappedFactory)

        def fakeStateMachineFactory():
            return FakeSockJSProtocolMachine(self.stateMachineRecorder)

        self.factory.stateMachineFactory = fakeStateMachineFactory

        self.address = IPv4Address('TCP', '127.0.0.1', 80)
        self.transport = StringTransport()

        self.protocol = self.factory.buildProtocol(self.address)
        self.protocol.makeConnection(self.transport)

    def test_makeConnection(self):
        '''makeConnection connects the state machine to the transport.'''
        self.assertEqual(self.stateMachineRecorder.connect, [self.transport])

    def test_dataReceived_write(self):
        '''dataReceived passes the data to the state machine's receive method
        and the wrapped protocol.  With our echo protocol, we also
        test that write() calls the machine's write method.

        '''
        self.protocol.dataReceived(b'"something"')
        self.assertEqual(self.stateMachineRecorder.received,
                         [b'"something"'])
        self.assertEqual(self.stateMachineRecorder.written,
                         [b'"something"'])

    def test_dataReceived_writeSequence(self):
        '''dataReceived passes the data to the state machine's receive method
        and the wrapped protocol.  With our echo protocol, we also
        test that writeSequence() calls the machine's write method.

        '''
        self.protocol.dataReceived([b'"x"', b'"y"'])
        self.assertEqual(self.stateMachineRecorder.received,
                         [[b'"x"', b'"y"']])
        # multiple write calls
        self.assertEqual(self.stateMachineRecorder.written,
                         [b'"x"', b'"y"'])

    def test_loseConnection(self):
        '''loseConnection calls the state machine's disconnect method.'''
        self.protocol.loseConnection()
        self.assertEqual(self.stateMachineRecorder.disconnected, 1)

    def test_connectionLost(self):
        '''connectionLost calls the state machine's close method and the
        wrapped protocol's connectionLost method.

        '''
        reason = "This isn't a real reason"
        self.protocol.connectionLost(reason)
        self.assertEqual(self.stateMachineRecorder.closed, 1)
        self.assertEqual(self.connectionLost, [reason])


class RecordsRequestSessionActions(object):

    def __init__(self):
        self.request = None
        self.connectionsEstablished = []
        self.connectionsCompleted = []
        self.requestsBegun = 0
        self.dataReceived = []
        self.completelyWritten = []
        self.heartbeatsCompleted = 0
        self.currentRequestsFinished = 0
        self.connectionsCompletelyLost = 0


class FakeRequestSessionMachine(object):

    def __init__(self, recorder):
        self.recorder = recorder

    @property
    def request(self):
        return self.recorder.request

    @request.setter
    def request(self, request):
        self.recorder.request = request

    def establishConnection(self, request):
        self.recorder.connectionsEstablished.append(request)

    def completeConnection(self, request):
        self.recorder.connectionsCompleted.append(request)

    def beginRequest(self):
        self.recorder.requestsBegun += 1

    def completeDataReceived(self, data):
        self.recorder.dataReceived.append(data)

    def completeWrite(self, data):
        self.recorder.completelyWritten.append(data)

    def completeHeartbeat(self):
        self.recorder.heartbeatsCompleted += 1

    def finishCurrentRequest(self):
        self.recorder.currentRequestsFinished += 1

    def closeFrame(self, reason):
        return reason

    def completeLoseConnection(self):
        self.recorder.connectionsCompletelyLost += 1


class RequestSessionMachineTestCase(unittest.TestCase):

    def setUp(self):
        self.recorder = RecordsRequestSessionActions()
        self.fakeRequestSession = FakeRequestSessionMachine(self.recorder)
        self.requestSessionMachine = P.RequestSessionMachine(
            self.fakeRequestSession)

        self.request = DummyRequest([b'ignored'])

    def test_firstAttach(self):
        '''Attaching the first request to a RequestSessionMachine sets up the
        the protocol wrapper, begins the request, then attaches the
        protocol wrapper to the wrapped protocol as its transport.

        '''
        self.requestSessionMachine.attach(self.request)
        self.assertIs(self.recorder.request, self.request)
        self.assertEqual(self.recorder.connectionsEstablished, [self.request])
        self.assertEqual(self.recorder.requestsBegun, 1)
        self.assertEqual(self.recorder.connectionsCompleted, [self.request])

    def test_connectedWrite(self):
        '''With an attached request, write calls completeWrite and does not
        buffer.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.write(b"abc")
        self.assertEqual(self.recorder.completelyWritten, [b"abc"])


class RequestSessionProtocolTestCase(unittest.TestCase):

    def setUp(self):
        self.transport = StringTransport()
        self.request = DummyRequest([b'ignored'])
        self.request.transport = self.transport

        wrappedFactory = Factory.forProtocol(EchoProtocol)
        self.factory = P.RequestSessionWrappingFactory(wrappedFactory)

        self.protocol = self.factory.buildProtocol(self.request.getHost())
        self.protocol.makeConnectionFromRequest(self.request)

    def test_makeConnection_forbidden(self):
        '''
        RequestSessionProtocol.makeConnection raises a RuntimeError.
        '''
        protocol = self.factory.buildProtocol(self.request.getHost())

        with self.assertRaises(RuntimeError):
            protocol.makeConnection(self.transport)

    def test_write(self):
        ''' RequestSessionProtocol.write writes data to the request, not the
        transport.

        '''
        self.protocol.dataReceived(b'something')
        self.assertEqual(self.request.written, [b'something'])
        self.assertFalse(self.transport.value())

    def test_writeSequence(self):
        '''RequestSessionProtocol.writeSequence also writes data to the
        request, not the transport.

        '''
        self.protocol.dataReceived([b'something', b'else'])
        self.assertEqual(self.request.written, [b'something', b'else'])
        self.assertFalse(self.transport.value())

    def test_loseConnection(self):
        '''RequestSessionProtocol.loseConnection finishes the request.'''

        finishedDeferred = self.request.notifyFinish()

        def assertFinished(ignored):
            self.assertEqual(self.request.finished, 1)
            # DummyRequest doesn't call its transport's loseConnection
            self.assertFalse(self.transport.disconnecting)

        finishedDeferred.addCallback(assertFinished)

        self.protocol.dataReceived([b'about to close!'])
        self.protocol.loseConnection()

        self.assertEqual(self.request.written, [b'about to close!'])

        return finishedDeferred

    def test_connectionLost(self):
        '''RequestSessionProtocol.connectionLost calls through to the wrapped
        protocol with Connection Done when there's no pending data.

        '''

        ensureCalled = Deferred()

        def trapConnectionDone(failure):
            failure.trap(ConnectionDone)

        ensureCalled.addErrback(trapConnectionDone)

        def connectionLost(reason):
            ensureCalled.errback(reason)

        self.protocol.wrappedProtocol.connectionLost = connectionLost
        self.protocol.connectionLost()

        return ensureCalled

    def test_detachAndReattach(self):
        '''RequestSessionProtocol.detachFromRequest buffers subsequent writes,
        which are flushed as soon as makeConnectionFromRequest is
        called again.

        '''
        self.protocol.detachFromRequest()
        self.protocol.dataReceived(b'pending')
        self.protocol.dataReceived(b'more pending')
        self.assertFalse(self.request.written)

        self.protocol.makeConnectionFromRequest(self.request)
        self.assertEqual(self.request.written, [b'pending', b'more pending'])

        self.protocol.dataReceived(b'written!')
        self.assertEqual(self.request.written, [b'pending', b'more pending',
                                                b'written!'])

    def test_doubleMakeConnectionFromRequestClosesDuplicate(self):
        '''A RequestSessionProtocol that's attached to a request will close a
        second request with an error message.

        '''
        secondRequest = DummyRequest([b'another'])
        secondTransport = StringTransport()
        secondRequest.transport = secondTransport

        self.protocol.makeConnectionFromRequest(secondRequest)
        self.assertEqual(secondRequest.written,
                         [b'c[2010,"Another connection still open"]\n'])
        # but we can still write to our first request
        self.protocol.dataReceived(b'hello')
        self.assertEqual(self.request.written, [b'hello'])

    def test_loseConnection_whenDetached(self):
        '''A RequestSessionProtocol with pending data has loseConnection
        called, its connectionLost method receives a SessionTimeout
        failure.
        '''
        self.protocol.detachFromRequest()
        self.protocol.dataReceived(b'pending')
        self.protocol.loseConnection()

        ensureCalled = Deferred()

        def trapSessionTimeout(failure):
            failure.trap(P.SessionTimeout)

        ensureCalled.addErrback(trapSessionTimeout)

        def connectionLost(reason):
            ensureCalled.errback(reason)

        self.protocol.wrappedProtocol.connectionLost = connectionLost
        self.protocol.connectionLost()

        return ensureCalled

    def test_connectionLost_whenDetached(self):
        '''A RequestSessionProtocol with pending data has loseConnection
        called, its connectionLost method receives a SessionTimeout
        failure.
        '''
        self.protocol.detachFromRequest()
        self.protocol.dataReceived(b'pending')

        ensureCalled = Deferred()

        def trapSessionTimeout(failure):
            failure.trap(P.SessionTimeout)

        ensureCalled.addErrback(trapSessionTimeout)

        def connectionLost(reason):
            ensureCalled.errback(reason)

        self.protocol.wrappedProtocol.connectionLost = connectionLost
        self.protocol.connectionLost()

        return ensureCalled


class TimeoutClockTestCase(unittest.TestCase):

    def setUp(self):
        self.timeoutDeferred = Deferred()
        self.clock = Clock()
        self.length = 5.0

        self.timeoutClock = P.TimeoutClock(self.timeoutDeferred,
                                           length=self.length,
                                           clock=self.clock)

    def test_start(self):
        '''A timeout expires a connection if not interrupted and then
        stops.

        '''

        self.timeoutClock.start()

        pendingExpiration = self.timeoutClock.timeoutCall
        self.assertEqual(self.clock.getDelayedCalls(), [pendingExpiration])

        self.clock.advance(self.length * 2)

        def assertTimedOutAndCannotRestart(_):
            self.assertFalse(self.clock.getDelayedCalls())

            self.assertIsNone(self.timeoutClock.timeoutCall)

            with self.assertRaises(RuntimeError):
                self.timeoutClock.start()

            with self.assertRaises(RuntimeError):
                self.timeoutClock.reset()

        self.timeoutDeferred.addCallback(assertTimedOutAndCannotRestart)
        return self.timeoutDeferred

    def test_reset_interrupts(self):
        '''A reset() call will remove the pending timeout, so that a
        subsequent start() call reschedule it.

        '''
        self.timeoutClock.start()

        pendingExpiration = self.timeoutClock.timeoutCall
        self.assertEqual(self.clock.getDelayedCalls(), [pendingExpiration])

        self.timeoutClock.reset()

        resetPendingExecution = self.timeoutClock.timeoutCall
        self.assertIsNot(pendingExpiration, resetPendingExecution)
        self.assertEqual(self.clock.getDelayedCalls(), [])
