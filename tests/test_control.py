import json
import math
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

from omnihand_usb.control import MotionController,MotionPlan,MAX_STEP,MAX_RATE
from omnihand_usb.device import DemoHand
from omnihand_usb import motion_protocol as codec
from omnihand_usb.damiao import _NativeDamiaoHand


class FakeHand(DemoHand):
    def __init__(self):
        super().__init__(seconds_per_range=0);self.calls=[];self.fail_write=False;self.fault=False
    def positions(self):self.calls.append(('read',threading.get_ident()));return super().positions()
    def diagnostics(self):self.calls.append(('diagnostics',threading.get_ident()));return super().diagnostics()
    def get_power_state(self):self.calls.append(('power_read',threading.get_ident()));return super().get_power_state()
    def set_positions(self,values):
        self.calls.append(('write',threading.get_ident(),list(values)))
        if self.fail_write:raise TimeoutError('position ACK missing')
        reply=super().set_positions(values)
        if self.fault:reply['faults'][0]=1
        return reply
    def set_enabled(self,value):self.calls.append(('power_write',threading.get_ident(),value));return super().set_enabled(value)


class MotionCodecTests(unittest.TestCase):
    def test_position_frame_has_ten_little_endian_targets_and_legal_dlc(self):
        values=[0,1,256,4096,100,200,300,400,500,600];payload=codec.position_payload(values)
        self.assertEqual(len(payload),24);self.assertEqual(payload[:1],b'\x08')
        self.assertEqual(struct.unpack('<10H',payload[1:21]),tuple(values));self.assertEqual(payload[21:],bytes(3))

    def test_invalid_targets_and_calibration_cannot_be_encoded(self):
        for values in (None,[0]*9,[True]+[0]*9,[1.0]*10,[-1]*10,[4097]*10):
            with self.subTest(values=values),self.assertRaises(ValueError):codec.position_payload(values)
        for state in (0,1,2,None):
            with self.assertRaises(ValueError):codec.power_payload(state)
        self.assertEqual(codec.power_payload(True),b'\x01\x01');self.assertEqual(codec.power_payload(False),b'\x01\x00')

    def test_feedback_is_decoded_without_clamping_and_bad_ack_is_rejected(self):
        payload=b'\x08'+struct.pack('<10H',*([4116]*10))+struct.pack('<10h',*([-2]*10))+bytes(range(10))+bytes([1]+[0]*9)+bytes(3)
        reply=codec.position_reply(payload)
        self.assertEqual(reply['positions'],[4116]*10);self.assertEqual(reply['velocities_raw'],[-2]*10);self.assertEqual(reply['ack_status_raw'][0],1)
        self.assertNotIn('faults',reply)
        for bad in (payload[:60],b'\x09'+payload[1:]):
            with self.assertRaises(ValueError):codec.position_reply(bad)
        with self.assertRaises(RuntimeError):codec.power_reply(b'\x01\x00')
        with self.assertRaises(ValueError):codec.power_state(b'\x02\xff')

    def test_plan_limits_rate_and_single_step_even_after_a_scheduler_stall(self):
        plan=MotionPlan([0]*10,[4096]*10,.5,0,'full')
        self.assertGreaterEqual(plan.duration,4096*1.5/MAX_RATE)
        for now in (0,.1,.2,100,100.1):
            target=plan.waypoint(now)
            self.assertLessEqual(max(abs(a-b) for a,b in zip(target,plan.last_sent)),MAX_STEP)
            plan.last_sent=target

    def test_native_backend_only_returns_enable_success_after_state_readback(self):
        hand=_NativeDamiaoHand('.')
        hand._exchange=Mock(side_effect=[b'\x01\x01',b'\x02\x01'])
        self.assertEqual(hand.set_enabled(True)['power_state'],1)
        self.assertEqual([c.args[0] for c in hand._exchange.call_args_list],[b'\x01\x01',b'\x02'])
        hand._exchange=Mock(side_effect=[b'\x01\x01',b'\x02\x00'])
        with self.assertRaises(RuntimeError):hand.set_enabled(True)

    def test_real_firmware_status_pattern_is_cross_checked_not_a_false_axis_fault(self):
        hand=_NativeDamiaoHand('.')
        hand._exchange=Mock(return_value=b'\x08'+struct.pack('<10H',*([2000]*10))+bytes(30)+bytes([1,0,0,0,1,0,0,0,1,0])+bytes(3))
        hand._query=Mock(return_value=b'\x0d\x00\x00')
        result=hand.set_positions([2000]*10)
        self.assertEqual(result['hand_error_code'],0);self.assertIsNone(result['faults'])
        self.assertEqual(result['ack_status_raw'],[1,0,0,0,1,0,0,0,1,0]);hand._query.assert_called_once_with(0x0D)
        hand._query.return_value=b'\x0d\x01\x00'
        self.assertEqual(hand.set_positions([2000]*10)['hand_error_code'],1)
        hand._query.side_effect=TimeoutError('no diagnostic reply')
        with self.assertRaisesRegex(RuntimeError,'故障复核失败'):hand.set_positions([2000]*10)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.controller=MotionController(Path(self.temp.name));self.hand=FakeHand()
        self.controller.bind(self.hand,'demo','right');self.session=self.controller.snapshot()['session_id']
    def tearDown(self):self.controller.unbind();self.temp.cleanup()

    def queued(self,action,**body):
        output={}
        def request():
            try:output['result']=self.controller.request(action,{'session_id':self.session,**body},timeout=2)
            except Exception as exc:output['error']=exc
        thread=threading.Thread(target=request);thread.start()
        deadline=time.monotonic()+1
        while self.controller._queue.empty() and thread.is_alive() and time.monotonic()<deadline:time.sleep(.001)
        return thread,output

    def call(self,action,**body):
        thread,output=self.queued(action,**body);self.controller.tick(self.hand);thread.join(2)
        self.assertFalse(thread.is_alive())
        if 'error' in output:raise output['error']
        return output['result']

    def writes(self):return [c for c in self.hand.calls if c[0] in ('write','power_write')]
    def ready(self):self.call('arm');self.call('enable');self.hand.calls.clear()

    def test_connection_preview_save_and_arm_never_write_hardware(self):
        self.assertFalse(self.controller.snapshot()['armed']);self.assertEqual(self.hand.calls,[])
        with patch('omnihand_usb.kinematics.PoseSolver') as solver:
            solver.return_value.solve.return_value={'joints_rad':{}}
            result=self.controller.request('preview',{'preset_id':'open'})
        self.assertFalse(result['hardware_write']);self.assertEqual(self.hand.calls,[])
        self.controller.request('save',{'name':'test','positions':[100]*10})
        self.call('arm');self.assertEqual(self.writes(),[])
        self.call('lock');self.assertEqual(self.writes(),[])

    def test_move_is_rejected_when_locked_or_motor_disabled(self):
        with self.assertRaisesRegex(RuntimeError,'锁定'):self.call('move',preset_id='open')
        self.call('arm')
        with self.assertRaisesRegex(RuntimeError,'尚未使能'):self.call('move',preset_id='open')
        self.assertEqual(self.writes(),[])

    def test_all_writes_run_on_owner_and_stop_preempts_waypoints(self):
        self.ready();result=self.call('move',positions=[0]*10,execution_mode='smooth',duration=.5)
        self.assertTrue(result['running'])
        self.controller._plan.began-=3;self.controller._plan.last_tick=-math.inf
        self.controller.tick(self.hand)
        before=len(self.writes());self.assertGreater(before,0)
        self.call('stop');self.assertFalse(self.controller.snapshot()['running'])
        after=len(self.writes());self.assertEqual(after,before+1)
        for _ in range(3):self.controller.tick(self.hand)
        self.assertEqual(len(self.writes()),after)
        self.assertEqual({c[1] for c in self.hand.calls},{threading.get_ident()})

    def test_stop_cancels_an_older_queued_move_instead_of_restarting_it(self):
        self.ready();move_thread,move=self.queued('move',preset_id='open')
        stop_thread,stop=self.queued('stop');self.controller.tick(self.hand)
        move_thread.join(2);stop_thread.join(2)
        self.assertIn('error',move);self.assertIn('取消',str(move['error']));self.assertNotIn('error',stop)
        self.assertFalse(self.controller.snapshot()['running']);self.assertEqual(len(self.writes()),1)

    def test_a_missing_ack_locks_control_and_is_not_retried(self):
        self.ready();self.call('move',positions=[0]*10,execution_mode='smooth',duration=1)
        self.hand.fail_write=True;self.controller._plan.began-=3;self.controller._plan.last_tick=-math.inf
        self.controller.tick(self.hand);state=self.controller.snapshot()
        self.assertFalse(state['armed']);self.assertFalse(state['running']);self.assertIn('ACK',state['error'])
        for _ in range(5):self.controller.tick(self.hand)
        self.assertEqual(len(self.writes()),1)
        # Manual stop remains possible after a failed motion has re-locked control.
        self.hand.fail_write=False;self.call('stop')
        self.assertEqual(len(self.writes()),2);self.assertFalse(self.controller.snapshot()['armed'])
        self.assertEqual(self.controller.snapshot()['status'],'stopped')

    def test_fault_feedback_prevents_next_waypoint(self):
        self.ready();self.call('move',positions=[0]*10,execution_mode='smooth',duration=1);self.hand.fault=True
        self.controller._plan.began-=3;self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertFalse(self.controller.snapshot()['armed']);self.assertIn('故障',self.controller.snapshot()['error'])

    def test_verified_device_error_still_stops_even_when_ack_tail_is_zero(self):
        self.ready();self.call('move',positions=[0]*10,execution_mode='smooth',duration=1)
        self.hand.set_positions=Mock(return_value={'positions':[100]*10,'faults':None,'ack_status_raw':[0]*10,'hand_error_code':21})
        self.controller._plan.began-=3;self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertFalse(self.controller.snapshot()['armed']);self.assertIn('0x0015',self.controller.snapshot()['error'])
        for _ in range(3):self.controller.tick(self.hand)
        self.hand.set_positions.assert_called_once()

    def test_stale_session_is_rejected_and_left_hand_does_not_get_right_presets(self):
        with self.assertRaisesRegex(RuntimeError,'会话'):self.controller.request('arm',{'session_id':'old'})
        self.controller.bind(self.hand,'demo','left');self.assertEqual(self.controller.catalog()['presets'],[])
        with self.assertRaises(ValueError):self.controller.preview({'preset_id':'open'})
        self.assertEqual(self.writes(),[])

    def test_custom_actions_are_persisted_and_cannot_silently_overwrite(self):
        saved=self.controller.save({'name':'my grasp','positions':[2048]*10})
        again=MotionController(Path(self.temp.name));preset=next(p for p in again.catalog()['presets'] if p['id']==saved['preset']['id'])
        self.assertEqual(preset['positions'],[2048]*10)
        with self.assertRaises(ValueError):self.controller.save({'name':'my grasp','positions':[0]*10})
        self.assertEqual(json.loads((Path(self.temp.name)/'data/actions.local.json').read_text(encoding='utf-8'))['presets'][0]['positions'],[2048]*10)

    def test_unsupported_usb_power_operation_sends_no_write(self):
        self.hand.control_capabilities=('position','hold');self.controller.bind(self.hand,'usb','right');self.session=self.controller.snapshot()['session_id'];self.call('arm')
        with self.assertRaisesRegex(RuntimeError,'未提供'):self.call('enable')
        self.assertEqual(self.writes(),[])

    def test_disable_releases_power_and_locks_control(self):
        self.ready();self.call('disable');state=self.controller.snapshot()
        self.assertEqual(state['power_state'],0);self.assertFalse(state['armed']);self.assertFalse(state['running'])
        self.assertEqual(self.writes(),[('power_write',threading.get_ident(),False)])

    def test_completed_requires_feedback_and_not_just_an_elapsed_timer(self):
        self.ready();self.call('move',positions=self.hand.positions(),execution_mode='smooth',duration=.5)
        self.controller._plan.began-=1;self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertEqual(self.controller.snapshot()['status'],'settling')
        for _ in range(2):
            self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand,self.hand.positions())
        self.assertEqual(self.controller.snapshot()['status'],'completed')
        self.assertEqual(self.controller.snapshot()['progress'],1)

    def test_default_direct_move_sends_full_target_once_and_waits_for_real_feedback(self):
        self.ready();current=self.hand.positions();target=[0,4096]*5
        self.hand.set_positions=Mock(return_value={'positions':current,'faults':[0]*10})
        result=self.call('move',positions=target,duration=0)  # no interpolation duration in direct mode
        self.assertEqual(result['execution_mode'],'direct');self.assertEqual(result['effective_duration'],0)
        self.assertEqual(self.controller.snapshot()['status'],'settling')
        for _ in range(4):
            self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.hand.set_positions.assert_called_once_with(target)
        self.assertTrue(self.controller.snapshot()['running'])  # ACK is not arrival
        self.hand._positions=list(target)
        self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertTrue(self.controller.snapshot()['running'])
        self.controller._plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertEqual(self.controller.snapshot()['status'],'completed')
        self.hand.set_positions.assert_called_once_with(target)

    def test_direct_missing_ack_locks_control_without_resending(self):
        self.ready();self.hand.fail_write=True
        self.call('move',positions=[0]*10)
        state=self.controller.snapshot()
        self.assertFalse(state['armed']);self.assertFalse(state['running']);self.assertIn('ACK',state['error'])
        for _ in range(4):self.controller.tick(self.hand)
        self.assertEqual(len(self.writes()),1);self.assertEqual(self.writes()[0][2],[0]*10)

    def test_direct_verified_fault_still_locks_after_one_target(self):
        self.ready()
        self.hand.set_positions=Mock(return_value={'positions':[0]*10,'hand_error_code':21})
        self.call('move',positions=[0]*10)
        self.assertFalse(self.controller.snapshot()['armed']);self.assertIn('0x0015',self.controller.snapshot()['error'])
        for _ in range(4):self.controller.tick(self.hand)
        self.hand.set_positions.assert_called_once_with([0]*10)

    def test_stop_direct_move_sends_current_hold_without_replaying_target(self):
        self.ready();current=self.hand.positions();target=[0]*10
        self.hand.set_positions=Mock(return_value={'positions':current,'faults':[0]*10})
        self.call('move',positions=target);self.call('stop')
        self.assertEqual([c.args[0] for c in self.hand.set_positions.call_args_list],[target,current])
        self.assertEqual(self.controller.snapshot()['status'],'stopped')
        for _ in range(4):self.controller.tick(self.hand)
        self.assertEqual(self.hand.set_positions.call_count,2)

    def test_direct_arrival_timeout_holds_current_position_without_retrying_target(self):
        self.ready();current=self.hand.positions();target=[0]*10
        self.hand.set_positions=Mock(return_value={'positions':current,'faults':[0]*10})
        self.call('move',positions=target)
        plan=self.controller._plan;plan.settle_at-=4;plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertTrue(self.controller.snapshot()['running'])  # allow device movement after a single send
        plan.settle_at-=7;plan.last_tick=-math.inf;self.controller.tick(self.hand)
        self.assertEqual(self.controller.snapshot()['status'],'not_reached')
        self.assertEqual([c.args[0] for c in self.hand.set_positions.call_args_list],[target,current])
        for _ in range(4):self.controller.tick(self.hand)
        self.assertEqual(self.hand.set_positions.call_count,2)

    def test_canfd_finishes_at_measured_51_count_error_on_two_new_reads(self):
        self.controller.bind(self.hand,'canfd','right')
        self.session=self.controller.snapshot()['session_id']
        self.ready()
        target=self.hand.positions()
        actual=list(target)
        actual[6]+=51
        self.hand.set_positions=Mock(return_value={'positions':target,'faults':[0]*10})
        self.call('move',positions=target)
        self.controller.tick(self.hand,poll_missing_feedback=False)
        self.assertTrue(self.controller.snapshot()['running'])
        self.controller.tick(self.hand,actual,poll_missing_feedback=False)
        self.assertTrue(self.controller.snapshot()['running'])
        self.controller.tick(self.hand,actual,poll_missing_feedback=False)
        self.assertEqual(self.controller.snapshot()['status'],'completed')
        self.assertEqual(self.controller.snapshot()['position_error'],51)
        self.assertEqual(self.controller.snapshot()['arrival_tolerance'],64)
        self.hand.set_positions.assert_called_once_with(target)

    def test_stationary_out_of_range_ends_before_arrival_deadline(self):
        self.ready()
        actual=self.hand.positions()
        self.hand.set_positions=Mock(return_value={'positions':actual,'faults':[0]*10})
        target=[0]*10
        self.call('move',positions=target)
        self.controller.tick(self.hand,actual)
        self.controller._plan.stationary_since-=1.2
        self.controller.tick(self.hand,actual)
        state=self.controller.snapshot()
        self.assertEqual(state['status'],'not_reached')
        self.assertIn('停滞',state['error'])
        self.assertEqual([call.args[0] for call in self.hand.set_positions.call_args_list],[target,actual])

    def test_invalid_mode_or_smooth_duration_is_rejected_without_hardware_access(self):
        for body in ({'execution_mode':'unknown'},{'execution_mode':False},{'execution_mode':'smooth','duration':0}):
            with self.subTest(body=body),self.assertRaises(ValueError):self.call('move',positions=[0]*10,**body)
        self.assertEqual(self.hand.calls,[])

    def test_staged_move_checks_feedback_before_each_clearance_step(self):
        presets = {p['id']: p['positions'] for p in self.controller.catalog()['presets']}
        self.hand._positions = list(presets['fist2'])
        self.ready()
        # The ACK may echo the commanded target even though the hand is still moving.
        self.hand.set_positions = Mock(side_effect=lambda target: {'positions': target, 'faults': [0]*10})
        result = self.call('move', preset_id='ok', execution_mode='staged')
        self.assertEqual(result['stage']['total'], 5)
        thumb_open = list(presets['fist2'])
        thumb_open[2] = presets['open'][2]
        self.hand.set_positions.assert_called_once_with(thumb_open)

        def tick():
            self.controller._plan.last_tick = -math.inf
            self.controller.tick(self.hand)

        tick()
        tick()
        self.hand.set_positions.assert_called_once_with(thumb_open)
        self.assertEqual(self.controller.snapshot()['stage']['index'], 1)

        for step in range(1, 6):
            target = list(self.hand.set_positions.call_args.args[0])
            self.hand._positions = target
            tick()
            self.assertEqual(self.controller.snapshot()['stage']['index'], step)
            tick()
            self.assertEqual(self.hand.set_positions.call_count, step)
            if step < 5:
                tick()
                self.assertEqual(self.hand.set_positions.call_count, step+1)

        sent = [c.args[0] for c in self.hand.set_positions.call_args_list]
        for axis in (4, 5, 7, 9):
            self.assertEqual(sent[1][axis], presets['open'][axis])
            self.assertEqual(sent[2][axis], presets['open'][axis])
        self.assertEqual(sent[3][2], thumb_open[2])
        self.assertEqual(sent[-1], presets['ok'])
        self.assertEqual(self.controller.snapshot()['status'], 'completed')

    def test_staged_timeout_holds_the_current_position_and_cancels_remaining_steps(self):
        self.ready()
        before = self.hand.positions()
        self.hand.set_positions = Mock(return_value={'positions': before, 'faults': [0]*10})
        result = self.call('move', preset_id='fist2', execution_mode='staged')
        self.assertGreater(result['stage']['total'], 1)
        plan = self.controller._plan
        plan.settle_at -= plan.arrival_timeout + 1
        plan.last_tick = -math.inf
        self.controller.tick(self.hand)
        self.assertEqual(self.controller.snapshot()['status'], 'not_reached')
        self.assertEqual(self.hand.set_positions.call_args.args[0], before)
        self.controller.tick(self.hand)
        self.assertEqual(self.hand.set_positions.call_count, 2)

    def test_stop_between_clearance_steps_never_sends_the_next_stage(self):
        self.ready()
        result = self.call('move', preset_id='ok', execution_mode='staged')
        self.assertGreater(result['stage']['total'], 1)
        for _ in range(2):
            self.controller._plan.last_tick = -math.inf
            self.controller.tick(self.hand)
        first = self.writes()[0][2]
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.controller.snapshot()['stage']['index'], 2)
        self.call('stop')
        self.controller.tick(self.hand)
        self.assertEqual([write[2] for write in self.writes()], [first, first])
        self.assertEqual(self.controller.snapshot()['status'], 'stopped')

    def test_staged_move_at_target_does_not_open_and_close_again(self):
        current = next(p['positions'] for p in self.controller.catalog()['presets'] if p['id']=='fist2')
        self.hand._positions = list(current)
        self.ready()
        result = self.call('move', preset_id='fist2', execution_mode='staged')
        self.assertEqual(result['stage']['total'], 1)
        self.assertEqual([write[2] for write in self.writes()], [current])

    def test_geometry_fitted_preset_only_applies_to_simulation(self):
        demo = next(p for p in self.controller.catalog()['presets'] if p['id'] == 'fist2')
        self.assertTrue(demo['demo_adjusted'])
        self.controller.bind(self.hand, 'canfd', 'right')
        hardware = next(p for p in self.controller.catalog()['presets'] if p['id'] == 'fist2')
        self.assertEqual(hardware['positions'], [2209, 2545, 811, 0, 46, 46, 0, 46, 4096, 46])
        self.assertNotEqual(demo['positions'], hardware['positions'])
        self.assertEqual(demo['positions'][3:], hardware['positions'][3:])

    def test_timed_out_queued_request_is_not_executed_later(self):
        with self.assertRaises(TimeoutError):self.controller.request('arm',{'session_id':self.session},timeout=.01)
        self.controller.tick(self.hand);self.assertFalse(self.controller.snapshot()['armed']);self.assertEqual(self.hand.calls,[])

    def test_live_joint_changes_only_requested_axis_and_lock_holds_it(self):
        self.ready();before=self.hand.positions();self.hand.set_joint_position=Mock(wraps=self.hand.set_joint_position)
        result=self.call('joint',index=4,position=1234)
        self.hand.set_joint_position.assert_called_once_with(4,1234)
        expected=list(before);expected[3]=1234;self.assertEqual(self.hand.positions(),expected)
        self.assertFalse(result['running']);self.assertTrue(result['jog_active']);self.assertIsNone(self.controller._plan)
        self.call('lock');self.assertEqual(self.writes()[-1][2],expected)
        self.assertFalse(self.controller.snapshot()['jog_active'])

    def test_canfd_slider_reuses_recent_health_without_displaying_ack_as_actual(self):
        self.controller.bind(self.hand,'canfd','right')
        self.session=self.controller.snapshot()['session_id']
        self.ready()
        actual=self.controller.feedback()[0]
        self.hand.submit_joint_position=Mock(return_value={'ack_joint_index':4,'ack_joint_position':1234,
            'hand_error_code':0,'fault_check':'private_0x0d'})
        self.call('joint',index=4,position=1234)
        self.hand.submit_joint_position.assert_called_once_with(4,1234)
        self.assertEqual(self.hand.calls,[])
        self.assertEqual(self.controller.feedback()[0],actual)

    def test_demo_single_joint_keeps_moving_when_retargeted_rapidly(self):
        now=0.0
        hand=DemoHand(clock=lambda:now)
        hand.set_enabled(True)
        initial=hand.positions()[0]
        for target in (2500,2700,2900,3100,3300):
            hand.set_joint_position(1,target)
            now+=.045
        self.assertGreater(hand.positions()[0]-initial,900)

    def test_stop_cancels_unsent_live_joint_requests(self):
        self.ready();self.hand.set_joint_position=Mock(wraps=self.hand.set_joint_position)
        thread,pending=self.queued('joint',index=5,position=1234)
        stop_thread,stop=self.queued('stop');self.controller.tick(self.hand);thread.join(2);stop_thread.join(2)
        self.assertIn('error',pending);self.assertNotIn('error',stop);self.hand.set_joint_position.assert_not_called()

    def test_live_joint_fault_locks_and_is_not_retried(self):
        self.ready();self.hand.set_joint_position=Mock(return_value={'positions':[100]*10,'hand_error_code':21})
        with self.assertRaisesRegex(RuntimeError,'0x0015'):self.call('joint',index=2,position=1234)
        for _ in range(4):self.controller.tick(self.hand)
        self.assertFalse(self.controller.snapshot()['armed']);self.hand.set_joint_position.assert_called_once()

    def test_live_mixed_parameters_reach_the_backend_without_changing_other_axes(self):
        self.ready();before=self.hand.positions()
        self.call('joint',index=3,position=1234,control_mode='pt',currents_ma=[100]*10)
        expected=list(before);expected[2]=1234;self.assertEqual(self.hand.positions(),expected)
        self.assertEqual(self.hand._profile,{'control_mode':'pt','currents_ma':[100]*10})
        self.assertEqual(self.controller.snapshot()['control_mode'],'pt')

    def test_mixed_control_rejects_host_interpolation_without_accessing_hardware(self):
        with self.assertRaises(ValueError):self.call('move',positions=[0]*10,execution_mode='smooth',control_mode='pt',currents_ma=[100]*10)
        self.assertEqual(self.hand.calls,[])


if __name__=='__main__':unittest.main()
