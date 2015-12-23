import json

from twisted.trial import unittest
from twisted.internet.protocol import Protocol, Factory
from twisted.test.proto_helpers import StringTransport
from twisted.internet.task import Clock
from twisted.internet.address import IPv4Address
from .. import protocol as P


class sockJSJSONTestCase(unittest.SynchronousTestCase):

    def test_sockJSJSON(self):
        self.assertEqual(P.sockJSJSON([3000, 'Go away!']),
                         b'[3000,"Go away!"]')


class HeartbeatClock(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.period = 25.0
        self.heartbeats = []

        def fakeHeartbeat():
            self.heartbeats.append(1)

        self.heartbeater = P.HeartbeatClock(fakeHeartbeat,
                                            period=self.period,
                                            clock=self.clock)

    def test_neverScheduled(self):
        '''Heartbeats are not scheduled before the first schedule(), and are
        not scheduled if we immediately stop() the HeartbeatClock.  A
        stopped HeartbeatClock can never schedule any other heartbeats.
        '''

        self.assertFalse(self.clock.getDelayedCalls())
        self.heartbeater.stop()
        self.assertFalse(self.clock.getDelayedCalls())

        with self.assertRaises(KeyError):
            self.heartbeater.schedule()

        self.assertFalse(self.clock.getDelayedCalls())

    def test_schedule(self):
        '''Heartbeats are scheduled and recur if not interrupted.'''

        self.heartbeater.schedule()
        pendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(), [pendingBeat])

        self.clock.advance(self.period * 2)
        self.assertEqual(len(self.heartbeats), 1)
        self.assertFalse(pendingBeat.active())

        rescheduledPendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(),
                         [rescheduledPendingBeat])

    def test_schedule_interrupts(self):
        '''A schedule() call will remove the pending heartbeat and reschedule
        it for later.

        '''
        self.heartbeater.schedule()
        pendingBeat = self.heartbeater.pendingHeartbeat

        self.heartbeater.schedule()
        self.assertFalse(self.heartbeats)
        self.assertFalse(pendingBeat.active())

        rescheduledPendingBeat = self.heartbeater.pendingHeartbeat
        self.assertEqual(self.clock.getDelayedCalls(),
                         [rescheduledPendingBeat])

    def test_schedule_stop(self):
        '''A stop() call removes any pending heartbeats.'''
        self.heartbeater.schedule()
        pendingBeat = self.heartbeater.pendingHeartbeat

        self.heartbeater.stop()
        self.assertFalse(self.heartbeats)
        self.assertFalse(pendingBeat.active())
        self.assertFalse(self.clock.getDelayedCalls())


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


class SockJSProtocolStateMachineTestCase(unittest.TestCase):

    def setUp(self):
        self.heartbeatRecorder = RecordsHeartbeat()
        self.heartbeater = FakeHeartbeatClock(self.heartbeatRecorder)
        self.sockJSMachine = P.SockJSProtocolMachine(self.heartbeater)
        self.transport = StringTransport()

    def test_disconnectBeforeConnect(self):
        '''Disconnecting before connecting permanently disconnects
        a SockJSProtocolMachine.

        '''
        self.sockJSMachine.disconnect()
        self.assertFalse(self.transport.value())

        with self.assertRaises(KeyError):
            self.sockJSMachine.connect(self.transport)

    def test_connect(self):
        '''SockJSProtocolMachine.connect writes an opening frame and schedules
        a heartbeat.

        '''
        self.sockJSMachine.connect(self.transport)
        self.assertEqual(self.transport.value(), b'o')
        self.assertTrue(self.heartbeatRecorder.scheduleCalled)

    def test_write(self):
        '''SockJSProtocolMachine.write writes the requested data and
        (re)schedules a heartbeat.

        '''
        self.sockJSMachine.connect(self.transport)
        self.sockJSMachine.write([1, 'something'])

        expectedProtocolTrace = b''.join([b'o', b'a[1,"something"]'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)

        self.assertEqual(self.heartbeatRecorder.scheduleCalls, 2)

    def test_heartbeat(self):
        '''SockJSProtocolMachine.heartbeat writes a heartbeat!'''
        self.sockJSMachine.connect(self.transport)
        self.sockJSMachine.heartbeat()

        expectedProtocolTrace = b''.join([b'o', b'h'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)

        self.assertTrue(self.heartbeatRecorder.scheduleCalls)

    def test_withHeartBeater(self):
        '''SockJSProtocolMachine.withHeartbeater should associate a new
        instance's heartbeat method with the heartbeater.

        '''
        instance = P.SockJSProtocolMachine.withHeartbeater(
            self.heartbeater)
        instance.connect(self.transport)
        self.heartbeater.writeHeartbeat()

        expectedProtocolTrace = b''.join([b'o', b'h'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)

    def test_receive(self):
        '''SockJSProtocolMachine.receive decodes JSON.'''
        self.sockJSMachine.connect(self.transport)
        self.assertEqual(self.sockJSMachine.receive(b'[1,"something"]'),
                         [1, "something"])

    def test_disconnect(self):
        '''SockJSProtocolMachine.disconnect writes a close frame, disconnects
        the transport and cancels any pending heartbeats.

        '''
        self.sockJSMachine.connect(self.transport)
        self.sockJSMachine.disconnect(reason=P.DISCONNECT.GO_AWAY)

        expectedProtocolTrace = b''.join([b'o', b'c[3000,"Go away!"]'])
        self.assertTrue(self.transport.value(), expectedProtocolTrace)
        self.assertTrue(self.heartbeatRecorder.stopCalls)

    def test_jsonEncoder(self):
        '''SockJSProtocolMachine can use a json.JSONEncoder subclass for
        writes.

        '''
        class ComplexEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, complex):
                    return [obj.real, obj.imag]
                # Let the base class default method raise the TypeError
                return json.JSONEncoder.default(self, obj)

        encodingSockJSMachine = P.SockJSProtocolMachine(
            self.heartbeater,
            jsonEncoder=ComplexEncoder)

        encodingSockJSMachine.connect(self.transport)
        encodingSockJSMachine.write([2 + 1j])

        expectedProtocolTrace = b''.join([b'o', b'a[[2.0,1.0]]'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)

    def test_jsonDecoder(self):
        '''SockJSProtocolMachine can use a json.JSONDecoder subclass for
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

        decodingSockJSMachine = P.SockJSProtocolMachine(
            self.heartbeater,
            jsonDecoder=SetDecoder)

        decodingSockJSMachine.connect(self.transport)
        result = decodingSockJSMachine.receive(b'{"!set": [1, 2, 3]}')
        self.assertEqual(result, {1, 2, 3})


class EchoProtocol(Protocol):

    def dataReceived(self, data):
        if isinstance(data, list):
            self.transport.writeSequence(data)
        else:
            self.transport.write(data)


class SockJSProtocolTestCase(unittest.TestCase):

    def setUp(self):
        self.transport = StringTransport()
        self.clock = Clock()

        wrappedFactory = Factory.forProtocol(EchoProtocol)
        self.factory = P.SockJSProtocolFactory(wrappedFactory,
                                               clock=self.clock)
        self.address = IPv4Address('TCP', '127.0.0.1', 80)
        self.protocol = self.factory.buildProtocol(self.address)
        self.protocol.makeConnection(self.transport)

    def test_dataReceived_write(self):
        self.protocol.dataReceived(b'"something"')
        expectedProtocolTrace = b''.join([b'o', b'a["something"]'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)

    def test_dataReceived_writeSequence(self):
        self.protocol.dataReceived(b'["something", "else"]')
        expectedProtocolTrace = b''.join([b'o', b'a["something","else"]'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)

    def test_loseConnection(self):
        self.protocol.loseConnection()
        expectedProtocolTrace = b''.join([b'o', b'c[3000,"Go away!"]'])
        self.assertEqual(self.transport.value(), expectedProtocolTrace)
