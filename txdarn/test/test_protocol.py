import json

from twisted.trial import unittest
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol, Factory
from twisted.test.proto_helpers import StringTransport
from twisted.internet.task import Clock
from twisted.internet.address import IPv4Address
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
        self.assertEqual(self.transport.value(), b'o')

    def test_writeHeartbeat(self):
        '''writeHeartbeat writes a single heartbeat frame.'''
        self.protocol.writeHeartbeat()
        self.assertEqual(self.transport.value(), b'h')

    def test_writeClose(self):
        '''writeClose writes a close frame containing the provided reason.'''
        self.protocol.writeClose(P.DISCONNECT.GO_AWAY)
        self.assertEqual(self.transport.value(), b'c[3000,"Go away!"]')

    def test_writeData(self):
        '''writeData writes the provided data to the transport.'''
        self.protocol.writeData(["letter", 2])
        self.assertEqual(self.transport.value(), b'a["letter",2]')

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
        self.assertEqual(frame, b'c[3000,"Go away!"]')
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

        self.assertEqual(self.transport.value(), b'a[[2.0,1.0]]')

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
        '''SockJSProtocolMachine.disconnect implements an active close: it
        writes a close frame, disconnects the transport, and cancels
        any pending heartbeats.

        '''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        self.sockJSMachine.disconnect(reason=P.DISCONNECT.GO_AWAY)

        self.assertIs(None, self.sockJSMachine.transport)

        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
        self.assertEqual(self.protocolRecorder.wroteClose,
                         [P.DISCONNECT.GO_AWAY])
        self.assertEqual(self.protocolRecorder.lostConnection, 1)

        self.assertEqual(self.heartbeatRecorder.stopCalls, 1)

    def test_close(self):
        '''SockJSProtocolMachine.close implements a passive close: it drops
        the transport and cancels any pending heartbeats.

        '''
        self.sockJSMachine.connect(self.sockJSWireProtocol)
        self.sockJSMachine.close()

        self.assertIs(None, self.sockJSMachine.transport)
        self.assertEqual(self.protocolRecorder.wroteOpen, 1)
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
        self.otherRequestsClosed = []
        self.dataWritten = []
        self.heartbeatsCompleted = 0
        self.currentRequestsFinished = 0
        self.connectionsLostCompletely = 0
        self.connectionsCompletelyLost = []


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

    def closeOtherRequest(self, request, reason):
        self.recorder.otherRequestsClosed.append((request, reason))

    def writeData(self, data):
        self.recorder.dataWritten.append(data)

    def completeWrite(self, data):
        self.recorder.completelyWritten.append(data)

    def completeHeartbeat(self):
        self.recorder.heartbeatsCompleted += 1

    def finishCurrentRequest(self):
        self.recorder.currentRequestsFinished += 1
        self.request = None

    def closeFrame(self, reason):
        return reason

    def completeLoseConnection(self):
        self.recorder.connectionsLostCompletely += 1

    def completeConnectionLost(self, reason):
        self.recorder.connectionsCompletelyLost.append(reason)


class DummyRequestAllowsNonBytes(DummyRequest):
    '''A DummyRequest subclass that does not assert write has been called
    with bytes.  Use me when you want to inspect something that an
    intermediary would have serialized before writing it to the
    request.

    '''

    def write(self, data):
        self.written.append(data)


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

    def test_connectedHaveTransportWrite(self):
        '''With an attached request, write calls completeWrite and does not
        buffer.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.write(b"abc")
        self.assertEqual(self.recorder.completelyWritten, [b"abc"])

    def test_connectedHaveTransportReceive(self):
        '''With an attached request, received data passes on to the wrapped
        protocol.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.receive(b"abc")
        self.assertEqual(self.recorder.dataReceived, [b"abc"])

    def test_connectedHaveTransportHeartbeat(self):
        '''With an attached request, heartbeats are immediately sent.'''
        self.test_firstAttach()
        self.requestSessionMachine.heartbeat()
        self.assertEqual(self.recorder.heartbeatsCompleted, 1)

    def test_connectedHaveTransportDetach(self):
        '''Detaching from a request finishes that request.'''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.assertEqual(self.recorder.currentRequestsFinished, 1)

    def assertDuplicateRequestClosedWith(self, reason):
        '''Assert that a second request is finished with the given reason.'''

        duplicateRequest = 'not really a request'
        self.requestSessionMachine.attach(duplicateRequest)
        self.assertEqual(self.recorder.otherRequestsClosed,
                         [(duplicateRequest, reason)])

    def test_connectedHaveTransportDuplicateAttach(self):
        '''Attempting to attach a request to a RequestSessionMachine that's
        already attached to a request closes the attached request.

        '''
        self.test_firstAttach()
        self.assertDuplicateRequestClosedWith(P.DISCONNECT.STILL_OPEN)
        duplicateRequest = 'not really a request'
        self.requestSessionMachine.attach(duplicateRequest)

    def test_connectedHaveTransportWriteCloseAndLoseConnection(self):
        '''Writing a close frame to a RequestSessionMachine stores it on the
        machine so it will be written upon loseConnection.


        '''
        self.test_firstAttach()
        self.requestSessionMachine.writeClose(P.DISCONNECT.GO_AWAY)
        self.requestSessionMachine.loseConnection()

        self.assertDuplicateRequestClosedWith(P.DISCONNECT.GO_AWAY)
        self.assertEqual(self.recorder.connectionsLostCompletely, 1)

    def test_connectedHaveTransportLoseConnection(self):
        '''Losing the connection closes the connection and closes the
        wrapped protocol.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.loseConnection()
        self.assertEqual(self.recorder.connectionsLostCompletely, 1)

    def test_connectedHaveTransportConnectionLost(self):
        '''connectionLost unsets the RequestSession's request (but does *not*
        call its finish() a second time) and calls the wrapped
        protocol's connectionLost.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.connectionLost(reason="Some Reason")
        self.assertIsNone(self.recorder.request)
        self.assertEqual(self.recorder.connectionsCompletelyLost,
                         ["Some Reason"])
        self.assertFalse(self.request.finished)

    def test_connectedNoTransportEmptyBufferReceive(self):
        '''The wrapped protocol receives data even when there's no attached
        outgoing request.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()

        deserializedMessage = ["I wasn't serialized'"]

        self.requestSessionMachine.receive(deserializedMessage)

    def test_connectedNoTransportEmptyBufferHeartbeat(self):
        '''Heartbeats are not sent when there's no attached request and the
        write buffer is empty.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()

        self.requestSessionMachine.heartbeat()
        self.assertEqual(self.recorder.heartbeatsCompleted, 0)

    def test_connectedNoTransportEmptyBufferDetach(self):
        '''Detaching a RequestSessionMachine that's already detached is a safe
        noop, so wrappers can always call detach() safel.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.requestSessionMachine.detach()

    def test_connectedNoTransportEmptyBufferWriteCloseAndLoseConnection(self):
        '''Writing a close frame to a RequestSessionMachine stores it on the
        machine so it will be written upon loseConnection.


        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.requestSessionMachine.writeClose(P.DISCONNECT.GO_AWAY)
        self.requestSessionMachine.loseConnection()

        self.assertDuplicateRequestClosedWith(P.DISCONNECT.GO_AWAY)
        self.assertEqual(self.recorder.connectionsLostCompletely, 1)

    def test_connectedNoTransportEmptyBufferConnectionLost(self):
        '''A RequestSessionMachine with no attached request and an empty
        buffer simply closes the protocol upon connectionLost.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()

        someReason = 'not a real reason'

        self.requestSessionMachine.connectionLost(someReason)
        self.assertEqual(self.recorder.connectionsCompletelyLost, [someReason])

    def test_noTransportWriteThenAttach(self):
        '''Writes are buffered when there's no attached request.  Attaching a
        request flushes the buffer.
        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()

        unserializedMessage = ["I wasn't serialized"]

        self.requestSessionMachine.write(unserializedMessage)
        self.requestSessionMachine.write(unserializedMessage)
        self.assertEqual(self.requestSessionMachine.buffer,
                         unserializedMessage * 2)

        newRequest = DummyRequestAllowsNonBytes([b'newRequest'])

        self.requestSessionMachine.attach(newRequest)
        self.assertEqual(self.requestSessionMachine.buffer, [])

        # the two lists have been concatenated into one, and were
        # flushed with a single call to requestSession.writeData
        self.assertEqual(self.recorder.dataWritten,
                         [unserializedMessage * 2])

    def test_connectedNoTransportPendingReceive(self):
        '''Received data passes immediately to the wrapped protocol, even when
        there's pending data.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.requestSessionMachine.write(b'abc')
        self.requestSessionMachine.receive(b'xyz')
        self.assertEqual(self.recorder.dataReceived, [b'xyz'])

    def test_connectedNoTransportPendingHeartbeat(self):
        '''Heartbeats are not sent when there's no attached request and
        pending data.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.requestSessionMachine.write(b'abc')
        self.requestSessionMachine.heartbeat()
        self.assertEqual(self.recorder.heartbeatsCompleted, 0)

    def test_connectedNoTransportPendingWriteCloseAndLoseConnection(self):
        '''Writing a close frame to a RequestSessionMachine stores it on the
        machine so it will be written upon loseConnection.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.requestSessionMachine.write(b'abc')
        self.requestSessionMachine.writeClose(P.DISCONNECT.GO_AWAY)
        self.requestSessionMachine.loseConnection()

        self.assertDuplicateRequestClosedWith(P.DISCONNECT.GO_AWAY)
        self.assertEqual(self.recorder.connectionsLostCompletely, 1)

    def test_connectedNoTransportPendingConnectionLost(self):
        '''If the session times out before all data can be written,
        connectionLost provides calls the wrapped protocol's ConnectionLost
        with a SessionTimeout failure.

        '''
        self.test_firstAttach()
        self.requestSessionMachine.detach()
        self.requestSessionMachine.write('xyz')
        self.requestSessionMachine.connectionLost()
        self.assertEqual(len(self.recorder.connectionsCompletelyLost), 1)
        failure = self.recorder.connectionsCompletelyLost[0]
        failure.trap(P.SessionTimeout)


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
